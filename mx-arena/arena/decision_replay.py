"""
Decision Replay Engine — re-evaluate candidates with different parameters.

The critical missing capability: instead of replaying journal BUY signals
(which holds entry decisions fixed), this re-runs the deterministic scoring
pipeline with sweep parameters applied to produce DIFFERENT BUY/NO_TRADE
decisions per configuration.

How it works:
1. Load watchlist + journal data for a historical day
2. For each candidate, reconstruct market context from journal
3. Re-compute MFCS with sweep params (new technical signal + journal LLM signals)
4. Apply consensus gate
5. Output: BUY set per parameter configuration
6. Feed into matching engine for P&L simulation

What changes vs journal replay:
- gap_momentum_score_threshold affects which stocks get STRONG_BULL from technical agent
- mfcs_buy_threshold affects which stocks pass the BUY gate
- These determine WHICH trades are taken, not just how they're managed
"""

from __future__ import annotations

import glob
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# MFCS signal mapping (mirrors src/core/scoring.py)
SIGNAL_NUMERIC = {
    "STRONG_BULL": 1.0,
    "BULL": 0.5,
    "NEUTRAL": 0.0,
    "BEAR": -0.5,
    "STRONG_BEAR": -1.0,
}

# Default agent weights (mirrors src/core/scoring.py _default_weights)
DEFAULT_WEIGHTS = {
    "catalyst_news": 0.30,
    "technical": 0.20,
    "volume_rvol": 0.20,
    "float_structure": 0.15,
    "institutional": 0.10,
    "deep_search": 0.05,
}

# Agent ID -> weight category mapping
AGENT_CATEGORY = {
    "news_agent": "catalyst_news",
    "technical_agent": "technical",
    "deterministic_technical": "technical",
    "fundamental_agent": "float_structure",
    "institutional_agent": "institutional",
    "deep_search_agent": "deep_search",
    "manipulation_classifier": None,  # Not in MFCS weights
    "risk_agent": "risk",
}

MIN_CONFIDENCE_FLOOR = 0.20


@dataclass
class CandidateContext:
    """Reconstructed candidate context from journal data."""
    ticker: str
    current_price: float
    previous_close: float
    gap_pct: float
    rvol: float
    premarket_volume: int
    has_news_catalyst: bool
    entry_price: float
    stop_loss: float
    # Journal agent signals (LLM signals we replay as-is)
    journal_signals: list[dict]
    # Pre-computed scores from journal
    journal_mfcs: float
    journal_action: str
    journal_component_scores: dict
    journal_risk_score: float
    # D148: Finnhub enrichment data (for ML outlier prediction)
    float_shares: int | None = None
    shares_outstanding: int | None = None
    market_cap: float | None = None
    industry: str | None = None
    short_interest_pct: float | None = None
    social_velocity: float | None = None
    options_pc_ratio: float | None = None


def load_candidates_from_journals(
    date: str,
    journals_dir: str | Path,
) -> list[CandidateContext]:
    """
    Load all evaluated candidates from journal entries for a date.

    Returns one CandidateContext per unique ticker (first occurrence).
    Includes both BUY and NO_TRADE entries — we re-evaluate ALL candidates.
    """
    journals_dir = Path(journals_dir)
    candidates = []
    seen_tickers = set()

    for f in sorted(glob.glob(str(journals_dir / f"journal_{date}_*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ticker = entry.get("ticker", "")
                if not ticker or ticker in seen_tickers:
                    continue
                seen_tickers.add(ticker)

                candidates.append(CandidateContext(
                    ticker=ticker,
                    current_price=entry.get("current_price", 0),
                    previous_close=entry.get("previous_close", 0),
                    gap_pct=entry.get("gap_pct", 0),
                    rvol=entry.get("rvol", 0),
                    premarket_volume=entry.get("premarket_volume", 0),
                    has_news_catalyst=_has_news_catalyst(entry),
                    entry_price=entry.get("entry_price", 0),
                    stop_loss=entry.get("stop_loss", 0),
                    journal_signals=entry.get("agent_signals", []),
                    journal_mfcs=entry.get("mfcs", 0),
                    journal_action=entry.get("action", "NO_TRADE"),
                    journal_component_scores=entry.get("component_scores", {}),
                    journal_risk_score=entry.get("risk_score", 0),
                ))

    logger.info("Loaded %d unique candidates from %s journals", len(candidates), date)
    return candidates


def _has_news_catalyst(entry: dict) -> bool:
    """Check if the news agent returned a bullish signal."""
    for sig in entry.get("agent_signals", []):
        if sig.get("agent_id") == "news_agent":
            return sig.get("signal") in ("BULL", "STRONG_BULL")
    return False


def replay_decisions(
    candidates: list[CandidateContext],
    param_overrides: dict[str, Any],
) -> list[dict]:
    """
    Re-evaluate all candidates with parameter overrides applied.

    Returns list of BUY decisions (candidates that pass all gates).
    Each result contains the candidate context + recomputed MFCS.

    Sweepable parameters:
    - gap_momentum_score_threshold (default 1.0)
    - gap_momentum_min_gap (default 0.08)
    - gap_momentum_min_rvol (default 2.5)
    - mfcs_buy_threshold (default 0.15)
    - risk_aversion_lambda (default 0.3)
    - stop_loss_pct (default 0.04) — applied at execution, not decision
    """
    gm_threshold = param_overrides.get("gap_momentum_score_threshold", 1.0)
    gm_min_gap = param_overrides.get("gap_momentum_min_gap", 0.08)
    gm_min_rvol = param_overrides.get("gap_momentum_min_rvol", 2.5)
    mfcs_threshold = param_overrides.get("mfcs_buy_threshold", 0.15)
    risk_lambda = param_overrides.get("risk_aversion_lambda", 0.3)
    stop_pct = param_overrides.get("stop_loss_pct", 0.04)

    # Phase 4 innovation: LLM signal masking for dollar value measurement
    # Modes: "all" (default), "deterministic_only", "news_only", "technical_only"
    signal_mask = param_overrides.get("signal_mask", "all")

    buys = []

    for cand in candidates:
        # Step 1: Re-evaluate technical signal with new gap momentum params
        tech_signal, tech_confidence = _evaluate_technical(
            cand, gm_threshold, gm_min_gap, gm_min_rvol,
        )

        # Step 2: Collect all signals (journal LLM + new technical)
        component_scores = {}
        risk_score = cand.journal_risk_score

        for sig in cand.journal_signals:
            agent_id = sig.get("agent_id", "")
            category = AGENT_CATEGORY.get(agent_id)

            if category == "risk":
                # Use journal risk score as-is
                risk_score = sig.get("risk_score", cand.journal_risk_score)
                if risk_score == 0 or risk_score is None:
                    # Fallback: derive from signal direction
                    risk_map = {"STRONG_BEAR": 0.9, "BEAR": 0.7, "NEUTRAL": 0.5,
                                "BULL": 0.3, "STRONG_BULL": 0.1}
                    risk_score = risk_map.get(sig.get("signal", "NEUTRAL"), 0.5)
                continue

            if category == "technical":
                # Use our re-evaluated technical signal instead of journal's
                continue

            if category is None:
                # Skip non-MFCS agents (manipulation_classifier)
                continue

            # Phase 4 innovation: signal masking for LLM value measurement
            if signal_mask == "deterministic_only":
                continue  # Skip ALL LLM signals — only technical + RVOL contribute
            elif signal_mask == "news_only" and category != "catalyst_news":
                continue  # Only news agent contributes
            elif signal_mask == "technical_only" and category != "technical":
                continue  # Only technical (already replaced above)

            # Use journal LLM signal as-is
            signal_dir = sig.get("signal", "NEUTRAL")
            confidence = sig.get("confidence", 0)
            score = _signal_to_score(signal_dir, confidence)

            # Skip empty neutrals
            if signal_dir == "NEUTRAL" and not sig.get("reasoning", "").strip():
                continue

            if category in component_scores:
                if abs(score) > abs(component_scores[category]):
                    component_scores[category] = score
            else:
                component_scores[category] = score

        # Add re-evaluated technical score
        tech_score = _signal_to_score(tech_signal, tech_confidence)
        component_scores["technical"] = tech_score

        # Add RVOL score (deterministic, from candidate data)
        rvol = cand.rvol
        if rvol < 1.0:
            rvol_score = 0.0
        elif rvol < 2.0:
            rvol_score = 0.2 + 0.2 * (rvol - 1.0)
        elif rvol < 4.0:
            rvol_score = 0.4 + 0.15 * (rvol - 2.0)
        else:
            rvol_score = min(1.0, 0.7 + 0.05 * (rvol - 4.0))
        if "volume_rvol" not in component_scores:
            component_scores["volume_rvol"] = rvol_score

        # Step 3: Compute MFCS with weight redistribution
        active_weight = sum(
            DEFAULT_WEIGHTS.get(cat, 0) for cat in component_scores if cat in DEFAULT_WEIGHTS
        )
        weight_boost = (1.0 / active_weight) if active_weight > 0 else 1.0

        weighted_sum = sum(
            DEFAULT_WEIGHTS.get(cat, 0) * weight_boost * score
            for cat, score in component_scores.items()
            if cat in DEFAULT_WEIGHTS
        )

        mfcs = weighted_sum - (risk_lambda * risk_score)

        # Step 4: Consensus gate (bullish must outnumber bearish)
        all_signals = []
        for sig in cand.journal_signals:
            if sig.get("agent_id") == "technical_agent":
                all_signals.append({"signal": tech_signal})
            elif sig.get("agent_id") != "risk_agent":
                all_signals.append(sig)

        bullish = [s for s in all_signals if s.get("signal") in ("BULL", "STRONG_BULL")]
        bearish = [s for s in all_signals if s.get("signal") in ("BEAR", "STRONG_BEAR")]

        passes_consensus = len(bullish) > len(bearish)

        # Step 5: Spread filter (D101) — reject wide-spread entries
        max_spread_pct = param_overrides.get("max_entry_spread_pct", 0.015)
        entry = cand.entry_price if cand.entry_price > 0 else cand.current_price
        if entry > 0:
            # Estimate spread at open from price tier
            from .spread_model import SpreadModel
            _sm = SpreadModel()
            from datetime import datetime as _dt, timezone as _tz
            _open_time = _dt(2026, 3, 26, 9, 30, tzinfo=_tz.utc)  # Placeholder
            half_spread = _sm.get_spread(entry, 50000, _open_time)
            spread_pct = (2 * half_spread) / entry
            passes_spread = spread_pct <= max_spread_pct
        else:
            passes_spread = True

        # Step 6: BUY decision
        is_buy = mfcs >= mfcs_threshold and passes_consensus and passes_spread

        if is_buy:
            # Compute stop based on override
            entry = cand.entry_price if cand.entry_price > 0 else cand.current_price
            stop = entry * (1 - stop_pct) if stop_pct else cand.stop_loss

            buys.append({
                "ticker": cand.ticker,
                "entry_price": entry,
                "stop_loss": round(stop, 4),
                "mfcs": round(mfcs, 4),
                "confidence": tech_confidence,
                "gap_pct": cand.gap_pct,
                "rvol": cand.rvol,
                "tech_signal": tech_signal,
                "journal_action": cand.journal_action,
                "journal_mfcs": cand.journal_mfcs,
                "component_scores": {k: round(v, 4) for k, v in component_scores.items()},
                "consensus": f"{len(bullish)}B/{len(bearish)}Be",
                # Flag if this is a NEW buy (wasn't in journal)
                "is_new_buy": cand.journal_action not in ("BUY", "STRONG_BUY"),
                # D148: Enrichment data flows through to trade results
                "float_shares": cand.float_shares,
                "market_cap": cand.market_cap,
                "industry": cand.industry,
            })

    logger.info(
        "Decision replay: %d/%d candidates -> BUY (params: %s)",
        len(buys), len(candidates),
        ", ".join(f"{k}={v}" for k, v in param_overrides.items()),
    )
    return buys


def _evaluate_technical_full(
    cand: CandidateContext,
    gm_threshold: float,
    gm_min_gap: float,
    gm_min_rvol: float,
    bars_data: dict | None = None,
) -> tuple[str, float]:
    """
    D141 Gap 1: Full deterministic technical agent evaluation.

    Calls the REAL production DeterministicTechnicalAgent with all 50+ signal
    types (RSI, MACD, Bollinger, EMA, ATR, VWAP, patterns, gap momentum).
    Falls back to simplified evaluator if production agent unavailable.
    """
    try:
        import asyncio
        from src.agents.deterministic_technical import DeterministicTechnicalAgent

        agent = DeterministicTechnicalAgent()

        # Build price_data from available bar data
        price_data = {}
        if bars_data:
            price_data["1min"] = [
                {"o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
                for b in (list(bars_data.values())[:20] if isinstance(bars_data, dict) else [])
            ]

        # Call the real agent
        result = asyncio.get_event_loop().run_until_complete(
            agent.analyze(
                ticker=cand.ticker,
                current_price=cand.current_price,
                rvol=cand.rvol,
                gap_pct=cand.gap_pct,
                vwap=cand.current_price * 0.999,  # Approximate VWAP
                price_data=price_data if price_data else None,
            )
        )
        return (result.signal, result.confidence)
    except Exception as e:
        # Fall back to simplified evaluator
        logger.debug("Full tech agent failed (%s), using simplified", e)
        return _evaluate_technical_simple(cand, gm_threshold, gm_min_gap, gm_min_rvol)


def _evaluate_technical(
    cand: CandidateContext,
    gm_threshold: float,
    gm_min_gap: float,
    gm_min_rvol: float,
) -> tuple[str, float]:
    """
    Simplified re-evaluation of the deterministic technical agent.

    Focuses on the gap momentum mode logic (D126) which is the primary
    parameter we want to sweep. Returns (signal_direction, confidence).
    """
    gap_pct = abs(cand.gap_pct)
    rvol = cand.rvol

    # Gap momentum score
    momentum_score = gap_pct * rvol

    # Gap momentum mode activation
    gap_momentum_active = (
        momentum_score > gm_threshold
        and gap_pct > gm_min_gap
        and rvol > gm_min_rvol
    )

    if gap_momentum_active:
        # D126: Gap IS the signal — worth STRONG_BULL
        # Confidence scales with momentum score (capped at 0.95)
        confidence = min(0.95, 0.5 + momentum_score * 0.1)
        return ("STRONG_BULL", confidence)

    # Fallback: basic directional signal from gap + rvol
    if gap_pct > 0.15 and rvol > 3.0:
        return ("BULL", 0.6)
    elif gap_pct > 0.08 and rvol > 2.0:
        return ("BULL", 0.4)
    elif gap_pct > 0.05 and rvol > 1.5:
        return ("NEUTRAL", 0.3)
    else:
        return ("NEUTRAL", 0.2)


# Alias for fallback in _evaluate_technical_full
_evaluate_technical_simple = _evaluate_technical


def _signal_to_score(signal: str, confidence: float) -> float:
    """Convert signal direction + confidence to bipolar score."""
    direction = SIGNAL_NUMERIC.get(signal, 0.0)
    if direction != 0.0 and confidence < MIN_CONFIDENCE_FLOOR:
        confidence = MIN_CONFIDENCE_FLOOR
    return direction * confidence
