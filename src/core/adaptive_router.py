"""
D112: Adaptive Compute Router — Three-Tier Evaluation Depth Routing.

Inspired by Gemini 3.1's Deep Think adaptive compute allocation: not all
inputs deserve the same computational budget. Routes candidates to the
appropriate evaluation tier based on structural characteristics:

    Tier 1 — INSTANT_REJECT (<1ms):  Hard structural violations
    Tier 2 — DETERMINISTIC_ONLY (<100ms): High-confidence deterministic cases
    Tier 3 — FULL_PIPELINE (15-30s): Genuinely ambiguous candidates

Impact: If 40% Tier 1 + 30% Tier 2, effective per-cycle time drops from
2-8 min to 1-3 min. On market open, the system processes obvious candidates
in milliseconds and reserves LLM calls for genuinely ambiguous ones.

Freeze compliance: This changes HOW FAST candidates are evaluated, not WHAT
gets traded. MFCS threshold, stops, sizing — all unchanged. Analogous to the
GEX hard filter that already runs pre-agent dispatch.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config.settings import RouterConfig
    from src.core.models import CandidateStock

logger = logging.getLogger(__name__)


# ─── Data Types ─────────────────────────────────────────────────────────

class EvalTier(str, Enum):
    """Evaluation depth tiers for candidate routing."""
    INSTANT_REJECT = "instant_reject"
    DETERMINISTIC_ONLY = "deterministic_only"
    FULL_PIPELINE = "full_pipeline"


# Estimated latency savings per tier (for logging/metrics only).
# Based on observed 5-agent LLM pipeline @ 15-30s per candidate.
_LATENCY_SAVED_MS = {
    EvalTier.INSTANT_REJECT: 25_000,
    EvalTier.DETERMINISTIC_ONLY: 24_900,  # Deterministic path < 100ms
    EvalTier.FULL_PIPELINE: 0,
}


@dataclass(frozen=True)
class RouterDecision:
    """Result of routing a candidate to an evaluation tier."""
    tier: EvalTier
    reason: str
    deterministic_mfcs: float | None = None
    latency_saved_estimate_ms: int = 0
    classification_time_ms: float = 0.0


# Strong catalyst keywords for pump-pattern override.
# If Phase 0 pre-market cache has news containing any of these,
# a sub-$3 stock gapping >30% might be legitimate (not a pump).
_STRONG_CATALYST_KEYWORDS = frozenset({
    "fda", "approval", "approved", "clearance",
    "earnings", "revenue", "eps", "beat",
    "contract", "partnership", "acquisition", "merger",
    "patent", "granted", "breakthrough",
})


class AdaptiveComputeRouter:
    """
    Three-tier evaluation depth router.

    Classifies candidates BEFORE agent dispatch to skip expensive LLM
    evaluation for obvious rejects and high-confidence deterministic cases.

    Args:
        config: RouterConfig from settings
        scanner_thresholds: ScannerThresholds for reference (not duplicated)
        max_entry_spread_pct: From ExecutionConfig — spread rejection threshold
    """

    def __init__(
        self,
        config: RouterConfig,
        max_entry_spread_pct: float = 0.01,
    ) -> None:
        self._config = config
        self._max_entry_spread_pct = max_entry_spread_pct
        # Counters for session-level metrics
        self._tier_counts: dict[EvalTier, int] = {t: 0 for t in EvalTier}

    def classify(
        self,
        candidate: CandidateStock,
        sec_filings: dict[str, Any] | None = None,
        news_items: list[Any] | None = None,
        deterministic_mfcs: float | None = None,
    ) -> RouterDecision:
        """
        Route a candidate to the appropriate evaluation tier.

        Args:
            candidate: CandidateStock from scanner
            sec_filings: Pre-fetched SEC filing summary (may contain has_424b5_same_day)
            news_items: Pre-fetched news items from Phase 0 cache
            deterministic_mfcs: Pre-computed quick deterministic MFCS score.
                If None, Tier 2 deterministic thresholds are skipped (candidate
                goes to FULL_PIPELINE if it passes Tier 1).

        Returns:
            RouterDecision with tier, reason, and metadata
        """
        if not self._config.enabled:
            return RouterDecision(
                tier=EvalTier.FULL_PIPELINE,
                reason="router disabled",
                latency_saved_estimate_ms=0,
            )

        t0 = time.monotonic()

        # ── TIER 1: INSTANT REJECT (deterministic, <1ms) ──
        # Hard structural violations — no LLM can save these.

        # 1. RVOL too low
        if candidate.rvol < self._config.instant_reject_min_rvol:
            return self._decide(
                EvalTier.INSTANT_REJECT,
                f"RVOL {candidate.rvol:.1f}x < {self._config.instant_reject_min_rvol}x minimum",
                t0,
            )

        # 2. Price too low (absolute floor)
        if candidate.current_price < self._config.instant_reject_min_price:
            return self._decide(
                EvalTier.INSTANT_REJECT,
                f"price ${candidate.current_price:.2f} < ${self._config.instant_reject_min_price:.2f} floor",
                t0,
            )

        # 3. Float too large (wrong universe) — UNLESS deterministic MFCS is strong.
        # D220 escape hatch: if the cheap deterministic signals (RVOL + technical +
        # risk) already produce a strong score (>= deterministic_strong_pass, default
        # 0.40), bypass the structural float check. The intent of this gate is to skip
        # LLM evaluation on hopeless candidates; a high deterministic MFCS is the
        # strongest possible signal that the candidate is not hopeless. Today's
        # IMMP/VSA/QBTS at MFCS=0.821 are exactly this case.
        if (
            candidate.float_shares is not None
            and candidate.float_shares > self._config.instant_reject_max_float
            and (
                deterministic_mfcs is None
                or deterministic_mfcs < self._config.deterministic_strong_pass
            )
        ):
            return self._decide(
                EvalTier.INSTANT_REJECT,
                f"float {candidate.float_shares:,} > {self._config.instant_reject_max_float:,} max",
                t0,
            )

        # 4. Gap too small
        if candidate.gap_pct < self._config.instant_reject_min_gap_pct:
            return self._decide(
                EvalTier.INSTANT_REJECT,
                f"gap {candidate.gap_pct*100:.1f}% < {self._config.instant_reject_min_gap_pct*100:.0f}% minimum",
                t0,
            )

        # 5. Same-day 424B5 filing (dilution trap)
        if sec_filings and sec_filings.get("has_424b5_same_day"):
            return self._decide(
                EvalTier.INSTANT_REJECT,
                "same-day 424B5 dilution filing",
                t0,
            )

        # 6. Corporate action detected by scanner
        if candidate.corporate_action_flag:
            return self._decide(
                EvalTier.INSTANT_REJECT,
                f"corporate action: {candidate.corporate_action_flag}",
                t0,
            )

        # 7. Pump pattern: >30% gap on sub-$3 stock
        # D116: Route ALL pump patterns to full pipeline instead of instant-reject.
        # The manipulation_classifier and risk_agent will evaluate whether the
        # move is legitimate. Sub-$3 explosive movers (BIAF +39%, LNAI +189%)
        # are exactly our edge — let the agents decide, don't blanket-ban.
        if (
            candidate.gap_pct > self._config.pump_gap_threshold
            and candidate.current_price < self._config.pump_price_threshold
        ):
            _catalyst_note = ""
            if self._config.pump_catalyst_override and self._has_strong_catalyst(news_items):
                _catalyst_note = " (strong catalyst found)"
            return self._decide(
                EvalTier.FULL_PIPELINE,
                f"pump pattern routed to full eval{_catalyst_note}: "
                f"{candidate.gap_pct*100:.0f}% gap on ${candidate.current_price:.2f} stock",
                t0,
            )

        # ── TIER 2: DETERMINISTIC ONLY (<100ms) ──
        # High-confidence cases where LLM adds negligible marginal info.
        # Uses pre-computed deterministic MFCS (RVOL + Technical + Risk).
        if deterministic_mfcs is not None:
            if deterministic_mfcs > self._config.deterministic_strong_pass:
                return self._decide(
                    EvalTier.DETERMINISTIC_ONLY,
                    f"strong deterministic signal: MFCS={deterministic_mfcs:.3f} "
                    f"> {self._config.deterministic_strong_pass}",
                    t0,
                    deterministic_mfcs=deterministic_mfcs,
                )
            if deterministic_mfcs < self._config.deterministic_clear_reject:
                return self._decide(
                    EvalTier.DETERMINISTIC_ONLY,
                    f"weak deterministic signal: MFCS={deterministic_mfcs:.3f} "
                    f"< {self._config.deterministic_clear_reject}",
                    t0,
                    deterministic_mfcs=deterministic_mfcs,
                )

        # ── TIER 3: FULL PIPELINE ──
        return self._decide(
            EvalTier.FULL_PIPELINE,
            "borderline candidate — LLM analysis needed",
            t0,
        )

    @staticmethod
    def _has_strong_catalyst(news_items: list[Any] | None) -> bool:
        """
        Check if news items contain evidence of a strong catalyst.

        Scans headlines/summaries for keywords that indicate a legitimate
        catalyst (FDA approval, earnings beat, contract, etc.) that could
        explain a large gap on a sub-$3 stock.
        """
        if not news_items:
            return False

        for item in news_items:
            # news_items can be CachedNewsItem objects or dicts
            text = ""
            if isinstance(item, dict):
                text = (
                    item.get("headline", "") + " " + item.get("summary", "")
                ).lower()
            elif hasattr(item, "headline"):
                headline = getattr(item, "headline", "") or ""
                summary = getattr(item, "summary", "") or ""
                text = (headline + " " + summary).lower()

            if any(kw in text for kw in _STRONG_CATALYST_KEYWORDS):
                return True

        return False

    def _decide(
        self,
        tier: EvalTier,
        reason: str,
        t0: float,
        deterministic_mfcs: float | None = None,
    ) -> RouterDecision:
        """Build a RouterDecision and update counters."""
        elapsed_ms = (time.monotonic() - t0) * 1000
        self._tier_counts[tier] += 1

        decision = RouterDecision(
            tier=tier,
            reason=reason,
            deterministic_mfcs=deterministic_mfcs,
            latency_saved_estimate_ms=_LATENCY_SAVED_MS[tier],
            classification_time_ms=round(elapsed_ms, 3),
        )

        log_fn = logger.info if tier != EvalTier.FULL_PIPELINE else logger.debug
        log_fn(
            "D112 ROUTER: %s → %s (%s) [%.1fms]",
            "candidate",  # ticker logged by caller
            tier.value,
            reason,
            elapsed_ms,
        )

        return decision

    def get_session_stats(self) -> dict[str, int]:
        """Return tier distribution for session-level reporting."""
        total = sum(self._tier_counts.values())
        return {
            "total_routed": total,
            "instant_reject": self._tier_counts[EvalTier.INSTANT_REJECT],
            "deterministic_only": self._tier_counts[EvalTier.DETERMINISTIC_ONLY],
            "full_pipeline": self._tier_counts[EvalTier.FULL_PIPELINE],
            "pct_saved": round(
                (
                    self._tier_counts[EvalTier.INSTANT_REJECT]
                    + self._tier_counts[EvalTier.DETERMINISTIC_ONLY]
                )
                / max(total, 1)
                * 100,
                1,
            ),
        }

    def to_log_dict(self, decision: RouterDecision, ticker: str) -> dict[str, Any]:
        """Build a journal-ready log entry for this routing decision."""
        return {
            "router_decision": {
                "ticker": ticker,
                "tier": decision.tier.value,
                "reason": decision.reason,
                "deterministic_mfcs": decision.deterministic_mfcs,
                "latency_saved_estimate_ms": decision.latency_saved_estimate_ms,
                "classification_time_ms": decision.classification_time_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
