"""Pluggable rule library for decision replay.

Each rule is a pure function: takes a captured DecisionRow + override
parameters, returns the new verdict action (BUY/HOLD/NO_TRADE) under
the alternate rule.

The rules MIRROR the production logic in src/core/orchestrator.py and
src/core/scoring.py — they are NOT reimplementations. When production
logic changes, the rule mirror must update in lockstep (the
property test in tests/unit/test_decision_replay.py pins this contract:
replaying with no overrides must match the captured verdict).
"""
from __future__ import annotations

from typing import Literal

VerdictAction = Literal["BUY", "STRONG_BUY", "HOLD", "NO_TRADE"]


# ── D124 consensus alignment rules ────────────────────────────────────


def d124_v1_pre_bug_ak(
    *,
    bullish_count: int,
    bullish_conf: float,
    bearish_count: int,
    bearish_conf: float,
) -> bool:
    """Pre-Bug-AK (D219-era) D124 logic.

    Returns True if the candidate would be REJECTED by D124. Source:
    `src/core/orchestrator.py:1068-1086` (pre-2026-04-27 commit `efe13f8`).

    The latent defect: when bullish_conf=0, the second branch becomes
    `bearish_conf > 0` — any bearish confidence > 0 trips. Today's log
    showed 47% of D124 rejections firing on this degenerate case.
    """
    return (
        bearish_count >= bullish_count + 2
        or (bearish_count > bullish_count and bearish_conf > bullish_conf * 1.5)
    )


def d124_v2_bug_ak(
    *,
    bullish_count: int,
    bullish_conf: float,
    bearish_count: int,
    bearish_conf: float,
    max_bearish_conf: float,
) -> tuple[bool, str | None]:
    """Bug AK three-tier D124 logic. Returns (rejected, tier_name)."""
    tier_a = bearish_count >= bullish_count + 2
    tier_b = bearish_count >= 2 and bearish_conf > max(bullish_conf * 1.5, 1.0)
    tier_c = bearish_count >= 1 and max_bearish_conf >= 0.85
    if tier_a:
        return True, "A:numeric"
    if tier_b:
        return True, "B:aggregate"
    if tier_c:
        return True, "C:single-veto"
    return False, None


# ── MFCS-threshold rules ──────────────────────────────────────────────


def mfcs_threshold_static(*, mfcs: float, threshold: float = 0.25) -> bool:
    """Returns True if MFCS clears the static threshold.

    Production uses `>` not `>=` per orchestrator.py:2858, so we match."""
    return mfcs > threshold


# ── Replay one decision under an alternate rule set ───────────────────


def replay_decision_d124(
    captured: dict,
    *,
    rule_version: str = "v2_bug_ak",
) -> tuple[VerdictAction, dict]:
    """Replay a captured decision through alternate D124 logic.

    Args:
        captured: DecisionRow as a dict.
        rule_version: 'v1_pre_bug_ak' or 'v2_bug_ak'.

    Returns:
        (replayed_verdict, replay_metadata).

    Replay semantics (HONEST about uncertainty):
        - We can DEFINITIVELY answer: would D124 reject under the
          alternate rule?
        - We CANNOT definitively answer: if D124 changes its mind,
          what would the downstream MFCS-threshold gate decide? That
          requires re-running the agents with fresh data.

        Therefore the replay decision tree:
          1. If captured was BUY/HOLD (D124 didn't reject in prod):
             - If alternate rule REJECTS → replayed=NO_TRADE
               (flip direction: BUY→NO_TRADE or HOLD→NO_TRADE)
             - Otherwise → replayed = captured (no change)
          2. If captured was NO_TRADE due to D124 reject:
             - If alternate rule also REJECTS → replayed=NO_TRADE
               (no change)
             - If alternate rule PASSES → replayed=PASSED_TO_MFCS
               (we don't know what MFCS would say; mark as
               "POTENTIAL_BUY" — counterfactual treats as opportunity)
          3. If captured was NO_TRADE for non-D124 reasons (e.g.
             D112 router, MFCS threshold):
             - The D124 rule change doesn't affect the outcome.
             - replayed = NO_TRADE (no change)
    """
    bull_n = int(captured.get("d124_bullish_count", 0))
    bull_c = float(captured.get("d124_bullish_conf", 0.0))
    bear_n = int(captured.get("d124_bearish_count", 0))
    bear_c = float(captured.get("d124_bearish_conf", 0.0))
    max_bear = float(captured.get("d124_max_bearish_conf", bear_c))
    captured_action = captured.get("verdict_action", "NO_TRADE")
    captured_d124_rejected = bool(captured.get("d124_was_rejected", False))

    # Apply chosen D124 rule
    if rule_version == "v1_pre_bug_ak":
        rejected = d124_v1_pre_bug_ak(
            bullish_count=bull_n, bullish_conf=bull_c,
            bearish_count=bear_n, bearish_conf=bear_c,
        )
        rejection_tier = "v1" if rejected else None
    elif rule_version == "v2_bug_ak":
        rejected, rejection_tier = d124_v2_bug_ak(
            bullish_count=bull_n, bullish_conf=bull_c,
            bearish_count=bear_n, bearish_conf=bear_c,
            max_bearish_conf=max_bear,
        )
    else:
        raise ValueError(f"unknown rule_version: {rule_version}")

    # Three cases per the docstring above:
    if captured_d124_rejected:
        # Captured was a D124 reject under the OLD rule.
        if rejected:
            # New rule also rejects → no change
            replayed: VerdictAction = "NO_TRADE"
            flip = "no_flip"
        else:
            # New rule passes → we DON'T KNOW what MFCS would say.
            # Treat as a POTENTIAL_BUY for the counterfactual (the
            # operationally-interesting case: the system would have
            # had a CHANCE to evaluate it under the new rule).
            replayed = "BUY"  # synthetic: we mark it as a potential trade
            flip = "captured_NO_TRADE_d124_to_replayed_potential_BUY"
    elif captured_action in ("BUY", "STRONG_BUY"):
        # Captured was a BUY (D124 passed under old rule, MFCS cleared).
        if rejected:
            # New rule rejects → flip BUY → NO_TRADE
            replayed = "NO_TRADE"
            flip = "captured_BUY_to_replayed_NO_TRADE_blocked_by_new_d124"
        else:
            # New rule also passes → captured stands
            replayed = captured_action
            flip = "no_flip"
    elif captured_action == "HOLD":
        # Captured was HOLD (D124 passed but MFCS didn't clear).
        # D124 rule change doesn't affect the MFCS gate, so HOLD stands
        # unless the new D124 rule blocks (in which case it's still
        # NO_TRADE-ish but for a different reason).
        replayed = "NO_TRADE" if rejected else "HOLD"
        flip = (
            "captured_HOLD_to_replayed_NO_TRADE_now_blocked_by_d124"
            if rejected else "no_flip"
        )
    else:
        # captured was NO_TRADE for non-D124 reason
        replayed = "NO_TRADE"
        flip = "no_flip"

    metadata = {
        "rule_version": rule_version,
        "d124_rejected_under_replay_rule": rejected,
        "d124_rejection_tier_replay": rejection_tier,
        "captured_d124_rejected": captured_d124_rejected,
        "captured_action": captured_action,
        "replayed_action": replayed,
        "verdict_changed": replayed != captured_action,
        "flip_direction": flip,
    }
    return replayed, metadata
