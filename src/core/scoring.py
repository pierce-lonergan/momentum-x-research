"""
MOMENTUM-X Multi-Factor Composite Scoring Engine

### ARCHITECTURAL CONTEXT
Implements MFCS from MOMENTUM_LOGIC.md §5. This is a pure-math module with
NO LLM calls. It receives AgentSignals and computes a weighted composite score.

Ref: MOMENTUM_LOGIC.md §5 (MFCS formula)
Ref: ADR-001 (Pipeline position: between agent dispatch and debate engine)

### DESIGN DECISIONS
- Pure functions over stateful classes for testability
- Polars not needed here — simple arithmetic on small collections
- Signal direction mapped to numeric scale for weighted averaging
- Risk score is subtracted (penalizes, not rewards)
"""

from __future__ import annotations

import logging

from src.core.models import (
    AgentSignal,
    CandidateStock,
    NewsSignal,
    RiskSignal,
    ScoredCandidate,
    SignalDirection,
    TechnicalSignal,
)

# ─── Signal → Numeric Mapping ────────────────────────────────────────

SIGNAL_NUMERIC: dict[SignalDirection, float] = {
    "STRONG_BULL": 1.0,
    "BULL": 0.5,           # D101: was 0.7 — too close to STRONG_BULL, compressing scale
    "NEUTRAL": 0.0,        # D100: was 0.4 — phantom positive score from no-data agents
    "BEAR": -0.5,          # D101: was 0.1 — bearish signals MUST subtract from MFCS
    "STRONG_BEAR": -1.0,   # D101: was 0.0 — strong bearish now has real veto power
}


def signal_to_score(signal: AgentSignal) -> float:
    """
    Convert an AgentSignal to a bipolar [-1, +1] score.

    D101: Full bipolar mapping — bearish signals actively subtract from MFCS.
    The score blends the direction (mapped to numeric) with the agent's
    stated confidence. A BEAR signal with high confidence now produces a
    strongly negative contribution, giving bearish agents real veto power.

    Formula: score = direction_numeric × max(confidence, MIN_CONFIDENCE_FLOOR)
    Range: [-1.0, +1.0] (was [0.0, 1.0] pre-D101)
    Ref: MOMENTUM_LOGIC.md §5 (σ_k normalization)
    """
    direction_score = SIGNAL_NUMERIC.get(signal.signal, 0.0)  # D100: default 0.0 (was 0.4)

    # D116: Minimum confidence floor for non-NEUTRAL signals.
    # Bug: MOBX Mar 20 — technical_agent returned STRONG_BEAR with conf=0.0,
    # making the bearish signal invisible (score = -1.0 × 0.0 = 0.0).
    # A directional signal with zero confidence is nonsensical — if you have
    # no confidence, the signal should be NEUTRAL. Enforce a floor so
    # directional signals always contribute meaningfully to MFCS.
    confidence = signal.confidence
    if direction_score != 0.0 and confidence < 0.20:
        confidence = 0.20  # Floor: any directional signal contributes at least 20%

    return direction_score * confidence


def compute_mfcs(
    candidate: CandidateStock,
    signals: list[AgentSignal],
    weights: dict[str, float] | None = None,
    risk_aversion_lambda: float = 0.3,
    debate_threshold: float = 0.6,
) -> ScoredCandidate:
    """
    Compute Multi-Factor Composite Score per MOMENTUM_LOGIC.md §5:

        MFCS(S, t) = Σ w_k · σ_k(S, t) - λ · RISK(S, t)

    Args:
        candidate: The stock being evaluated
        signals: List of AgentSignals from all analytical agents
        weights: Agent weight overrides (defaults from MOMENTUM_LOGIC.md §5)
        risk_aversion_lambda: λ parameter (default 0.3)
        debate_threshold: MFCS threshold to trigger debate engine

    Returns:
        ScoredCandidate with MFCS and component breakdown

    Ref: MOMENTUM_LOGIC.md §5
    Ref: ADR-001 (scoring position in pipeline)
    """
    if weights is None:
        weights = _default_weights()

    # ── Categorize signals by agent type ──
    component_scores: dict[str, float] = {}
    risk_score = 0.0

    for signal in signals:
        agent_type = _classify_agent(signal)

        if agent_type == "risk":
            # Risk agent contributes to penalty, not reward.
            if isinstance(signal, RiskSignal):
                risk_score = signal.risk_score
            else:
                # Fallback for base AgentSignal (error handler, etc.)
                # Map signal direction to a risk score that aligns with
                # RiskSignal.risk_score semantics (0=safe, 1=dangerous):
                #   STRONG_BEAR → high risk (0.9)
                #   BEAR        → elevated risk (0.7)
                #   NEUTRAL     → moderate/unknown risk (0.5, matches RiskSignal default)
                #   BULL        → low risk (0.3)
                #   STRONG_BULL → minimal risk (0.1)
                # This avoids the old formula (1.0 - direction * confidence)
                # which gave NEUTRAL/0.35 → risk_score=0.86 (near maximum).
                _RISK_DIRECTION_MAP: dict[str, float] = {
                    "STRONG_BEAR": 0.9,
                    "BEAR": 0.7,
                    "NEUTRAL": 0.5,
                    "BULL": 0.3,
                    "STRONG_BULL": 0.1,
                }
                risk_score = _RISK_DIRECTION_MAP.get(signal.signal, 0.5)
        elif agent_type in weights:
            # D26: Skip default fallback signals — agents that received no data
            # return NEUTRAL with empty reasoning. These should not contribute
            # phantom scores that dilute real signals from agents with actual data.
            if (
                signal.signal == "NEUTRAL"
                and not signal.reasoning.strip()
            ):
                continue

            score = signal_to_score(signal)
            # D101: Take strongest-magnitude score if multiple signals for same category
            # (e.g., multiple prompt variants — only most extreme contributes).
            # With bipolar scores, use the value with the largest absolute magnitude.
            existing = component_scores.get(agent_type)
            if existing is None or abs(score) > abs(existing):
                component_scores[agent_type] = score

    # ── Deterministic RVOL score ──
    # RVOL is a pure quantitative signal from the scanner — no LLM needed.
    # Maps RVOL to a [0, 1] score using empirical thresholds:
    #   RVOL < 1.0  → 0.0 (below average volume)
    #   RVOL 1-2    → 0.2-0.4 (mildly elevated)
    #   RVOL 2-4    → 0.4-0.7 (strong demand signal)
    #   RVOL > 4    → 0.7-1.0 (explosive demand)
    # Ref: MOMENTUM_LOGIC.md §5 (volume_rvol component)
    if "volume_rvol" in weights and "volume_rvol" not in component_scores:
        rvol = candidate.rvol
        if rvol < 1.0:
            rvol_score = 0.0
        elif rvol < 2.0:
            rvol_score = 0.2 + 0.2 * (rvol - 1.0)  # 1.0→0.2, 2.0→0.4
        elif rvol < 4.0:
            rvol_score = 0.4 + 0.15 * (rvol - 2.0)  # 2.0→0.4, 4.0→0.7
        else:
            rvol_score = min(1.0, 0.7 + 0.05 * (rvol - 4.0))  # 4.0→0.7, 10.0→1.0
        component_scores["volume_rvol"] = rvol_score

    # ── Compute weighted sum with D26 weight redistribution ──
    # When agents return empty defaults (excluded above), their weight capacity
    # is wasted. Redistribute absent agents' weight proportionally to agents
    # that DID provide real signals. This prevents 4 empty agents (60% weight)
    # from diluting 2 real agents down to only 40% scoring capacity.
    active_weight = sum(
        weights.get(cat, 0.0) for cat in component_scores if cat in weights
    )
    # Boost factor: if only 0.40 of weight is active, boost = 1.0/0.40 = 2.5
    if active_weight == 0:
        logging.getLogger(__name__).debug(
            "D96 MFCS %s: all agents returned empty/NEUTRAL — "
            "active_weight=0, MFCS will be 0.0",
            candidate.ticker,
        )
    weight_boost = (1.0 / active_weight) if active_weight > 0 else 1.0

    weighted_sum = sum(
        weights.get(agent_type, 0.0) * weight_boost * score
        for agent_type, score in component_scores.items()
    )

    # ── D96: Log weight redistribution details for EOD audit ──
    _logger = logging.getLogger(__name__)
    _absent_agents = [k for k in weights if k not in component_scores]
    _contrib_details = " | ".join(
        f"{at}={sc:.3f}×{weights.get(at, 0):.2f}×{weight_boost:.2f}={weights.get(at, 0) * weight_boost * sc:.3f}"
        for at, sc in component_scores.items()
    )
    _logger.info(
        "D96 MFCS %s: active=%d/%d boost=%.2fx risk=%.2f×λ%.2f=%.3f | %s%s",
        candidate.ticker,
        len(component_scores), len(weights),
        weight_boost,
        risk_score, risk_aversion_lambda, risk_aversion_lambda * risk_score,
        _contrib_details,
        f" | absent: {','.join(_absent_agents)}" if _absent_agents else "",
    )

    # ── Apply risk penalty ──
    mfcs = weighted_sum - (risk_aversion_lambda * risk_score)

    # ── D101: Clamp to [-1, 1] — allow negative MFCS ──
    # Pre-D101: clamped to [0, 1] which masked bearish consensus.
    # A negative MFCS means bears dominate — must NEVER pass buy threshold.
    mfcs = max(-1.0, min(1.0, mfcs))

    return ScoredCandidate(
        candidate=candidate,
        mfcs=mfcs,
        agent_signals=signals,
        component_scores=component_scores,
        risk_score=risk_score,
        qualifies_for_debate=mfcs >= debate_threshold,
    )


def _default_weights() -> dict[str, float]:
    """
    Default agent weights from MOMENTUM_LOGIC.md §5.

    | Agent         | Weight | Justification                     |
    |---------------|--------|-----------------------------------|
    | catalyst_news | 0.30   | Primary driver of +20% moves      |
    | technical     | 0.20   | Breakout confirmation              |
    | volume_rvol   | 0.20   | Demand-supply imbalance            |
    | float_struct  | 0.15   | Low-float amplification            |
    | institutional | 0.10   | UOA, block trade confirmation      |
    | deep_search   | 0.05   | Supplementary, low-confidence      |
    """
    return {
        "catalyst_news": 0.30,
        "technical": 0.20,
        "volume_rvol": 0.20,
        "float_structure": 0.15,
        "institutional": 0.10,
        "deep_search": 0.05,
    }


def _classify_agent(signal: AgentSignal) -> str:
    """Map agent_id to weight category."""
    agent_id = signal.agent_id.lower()
    if "news" in agent_id or "catalyst" in agent_id:
        return "catalyst_news"
    elif "tech" in agent_id:
        return "technical"
    elif "volume" in agent_id or "rvol" in agent_id or "scanner" in agent_id:
        return "volume_rvol"
    elif "float" in agent_id or "fund" in agent_id or "fundamental" in agent_id:
        return "float_structure"
    elif "inst" in agent_id or "option" in agent_id:
        return "institutional"
    elif "deep" in agent_id or "search" in agent_id:
        return "deep_search"
    elif "risk" in agent_id:
        return "risk"
    else:
        return "unknown"
