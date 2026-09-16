"""Composite-score shadow telemetry — per-candidate, called from orchestrator.

The orchestrator calls `maybe_score_composite()` at the end of every evaluation.
This function:
  1. Checks the SHADOW_SCORING_ENABLED env var (kill switch)
  2. Computes the composite score (full + prescore)
  3. Logs to the shadow JSONL via ShadowLogger
  4. Returns nothing (write-only)

Failure modes are NEVER raised — they're logged at WARNING and swallowed. The
orchestrator's hot path proceeds regardless.

Schema written to data/shadow/shadow_<date>.jsonl:
    {
      "kind": "composite_shadow",
      "session_date": "YYYY-MM-DD",
      "ticker": str,
      "scoring_timestamp": ISO8601 UTC,
      "production_decision": "BUY" | "NO_TRADE",
      "production_gate_rejected": str | None,
      "production_mfcs": float | None,
      "composite_score_prescore": float | None,
      "composite_score_full": float | None,
      "shadow_decision_at_threshold_0_40": "BUY" | "NO_TRADE",
      "agreement": "AGREE_BUY" | "AGREE_NO_TRADE" | "DISAGREE_SHADOW_BUYS"
                   | "DISAGREE_PROD_BUYS",
      "model_version": "v0",
      "sec_data_available": bool | None,  # D221 Phase F (2026-04-19):
        # True  = SEC EDGAR returned data this evaluation,
        # False = SEC fetch failed / outage detected,
        # None  = SEC data status not propagated to shadow yet
        #         (default until docs/engineering_hygiene/sec_degradation_fix.md
        #         ships the producer-side wiring).
        # See docs/research-log/12_session_1_bar_replay.md for why this matters:
        # session 1's 6/6 T+15 winners were 5/6 SEC-confounded, so honest
        # session-2-onward analysis MUST distinguish "SEC clean" from "SEC down."
    }
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from src.shadow.logger import get_shadow_logger

logger = logging.getLogger(__name__)

_SHADOW_THRESHOLD_FOR_DECISION = 0.40  # from Phase 3 IS finding (caveats apply)
_MODEL_VERSION = "v0"


def is_composite_shadow_enabled() -> bool:
    """Kill switch: SHADOW_SCORING_ENABLED env var. Default True.

    Set to 'false' (case-insensitive) to disable in seconds without a code change.
    """
    val = os.environ.get("SHADOW_SCORING_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def maybe_score_composite(
    candidate_features: dict[str, Any],
    ticker: str,
    session_date: str,
    production_decision: str,
    production_gate_rejected: str | None = None,
    production_mfcs: float | None = None,
    sec_data_available: bool | None = None,
) -> None:
    """Compute composite shadow scores and log them. NEVER raises.

    Args:
        candidate_features: dict matching extract_features() input contract.
            At minimum: gap_pct, premarket_volume, dollar_volume, price.
        ticker: stock symbol
        session_date: "YYYY-MM-DD"
        production_decision: "BUY" or "NO_TRADE" — the orchestrator's verdict.
        production_gate_rejected: which gate produced the NO_TRADE (None if BUY).
        production_mfcs: the MFCS the production pipeline computed.
        sec_data_available: True if SEC EDGAR was queried successfully for this
            candidate, False if the fetch failed (5xx / timeout / network),
            None if status is not yet propagated. See module docstring for
            why this matters for honest session-2-onward analysis.

    Returns:
        None — this is a write-only side channel.
    """
    if not is_composite_shadow_enabled():
        return

    try:
        # Lazy import — keeps import graph clean for AST isolation
        from src.composite.score import composite_score_both

        is_buy = (production_decision == "BUY")
        scores = composite_score_both(candidate_features, arena_buy_verdict=is_buy)

        full_score = scores.get("full")
        # Shadow decision at the IS-derived 0.40 threshold (caveats per Phase 4 diags)
        shadow_decision = "NO_TRADE"
        if full_score is not None and full_score >= _SHADOW_THRESHOLD_FOR_DECISION:
            shadow_decision = "BUY"

        # Agreement classification
        if production_decision == "BUY" and shadow_decision == "BUY":
            agreement = "AGREE_BUY"
        elif production_decision == "NO_TRADE" and shadow_decision == "NO_TRADE":
            agreement = "AGREE_NO_TRADE"
        elif shadow_decision == "BUY" and production_decision == "NO_TRADE":
            agreement = "DISAGREE_SHADOW_BUYS"
        else:
            agreement = "DISAGREE_PROD_BUYS"

        entry: dict[str, Any] = {
            "kind": "composite_shadow",
            "session_date": session_date,
            "ticker": ticker,
            "scoring_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "production_decision": production_decision,
            "production_gate_rejected": production_gate_rejected,
            "production_mfcs": production_mfcs,
            "composite_score_prescore": scores.get("prescore"),
            "composite_score_full": full_score,
            "shadow_decision_at_threshold_0_40": shadow_decision,
            "shadow_threshold": _SHADOW_THRESHOLD_FOR_DECISION,
            "agreement": agreement,
            "model_version": _MODEL_VERSION,
            "sec_data_available": sec_data_available,
        }
        get_shadow_logger().log(entry)

        # D221 Phase F: surface DISAGREE_SHADOW_BUYS to Decision Learning
        # channel when composite score is high enough to be interesting.
        # The post function honors its own threshold + trust-the-gate
        # filtering; calling it unconditionally for the agreement type
        # is fine. Write-only side channel (same contract as the JSONL
        # log above): never reads back into production.
        if agreement == "DISAGREE_SHADOW_BUYS" and full_score is not None:
            try:
                from src.monitoring.decision_learning import (
                    post_disagree_shadow_buy, get_distribution_cache,
                )
                import asyncio as _dl_asyncio
                _agent_signals = candidate_features.get("_agent_signals_for_dl")
                _dl_asyncio.ensure_future(post_disagree_shadow_buy(
                    ticker=ticker,
                    composite_score=full_score,
                    production_decision=production_decision,
                    rejection_code=production_gate_rejected,
                    rejection_reason=production_gate_rejected,
                    mfcs=production_mfcs,
                    agent_signals=_agent_signals,
                    distribution_cache=get_distribution_cache(),
                ))
            except Exception as _dl_e:
                logger.debug(
                    "decision_learning DISAGREE post failed for %s: %s",
                    ticker, _dl_e,
                )
    except Exception as e:
        # Hard rule: shadow MUST NEVER raise into production.
        logger.warning(
            "composite_shadow: scoring failed for %s on %s (%s) — production unaffected",
            ticker, session_date, e,
        )
