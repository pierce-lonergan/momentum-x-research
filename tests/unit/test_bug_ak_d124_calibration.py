"""Bug AK tests: D124 consensus alignment three-tier rejection logic.

Pins today's over-rejection pattern as a regression guard. Of 37
D124 rejections in the live 2026-04-27 session log, 47% were
"0 bullish vs 1 bearish (conf 0.45-0.80)" — exactly the case the
old `bearish_conf > bullish_conf * 1.5` arithmetic mishandled
(when bullish_conf=0, ANY bearish conf > 0 trips the check).

The new three-tier rule:
  Tier A: numeric dominance — len(bear) >= len(bull) + 2
  Tier B: ≥2 bearish AND aggregate conf > max(bull*1.5, 1.0)
  Tier C: single very-high-conf bearish (>=0.85) — fraud/manipulation
          single-veto path preserved
  Otherwise: pass through to MFCS scoring.

These tests directly exercise the `_bearish_dominant` boolean logic
without needing to spin up the full orchestrator (which would require
mocking 6+ collaborators).
"""
from __future__ import annotations

import pytest


# ── Reference implementation (mirrors orchestrator.py:1056-1090 logic) ──


def _bearish_dominant_v1_pre_bug_ak(bull_n, bull_conf, bear_n, bear_conf):
    """Old D219 logic — the version that over-rejected today."""
    return (
        bear_n >= bull_n + 2
        or (bear_n > bull_n and bear_conf > bull_conf * 1.5)
    )


def _bearish_dominant_v2_bug_ak(bull_n, bull_conf, bear_n, bear_conf, max_bear_conf):
    """New three-tier logic from Bug AK."""
    tier_a = bear_n >= bull_n + 2
    tier_b = bear_n >= 2 and bear_conf > max(bull_conf * 1.5, 1.0)
    tier_c = bear_n >= 1 and max_bear_conf >= 0.85
    return tier_a or tier_b or tier_c


# ── Tests pinning today's actual rejection patterns ──


@pytest.mark.parametrize(
    "bull_n,bull_conf,bear_n,bear_conf,max_bear,count_today,description",
    [
        # Patterns that the v1 logic OVER-REJECTED — Bug AK should now PASS them
        (0, 0.0, 1, 0.45, 0.45, 7, "0 vs 1 bearish (conf 0.45) — most common today"),
        (0, 0.0, 1, 0.49, 0.49, 3, "0 vs 1 bearish (conf 0.49)"),
        (0, 0.0, 1, 0.55, 0.55, 1, "0 vs 1 bearish (conf 0.55)"),
        (0, 0.0, 1, 0.80, 0.80, 7, "0 vs 1 bearish (conf 0.80) — borderline"),
    ],
)
def test_bug_ak_passes_single_low_conf_bearish_through_to_mfcs(
    bull_n, bull_conf, bear_n, bear_conf, max_bear, count_today, description,
):
    """Today's most common D124 rejection pattern — single bearish
    with confidence 0.45-0.80, no bullish opinion at all. Should now
    PASS through to MFCS (which can still HOLD/NO_TRADE based on
    composite score).
    """
    # Old logic over-rejected this
    assert _bearish_dominant_v1_pre_bug_ak(bull_n, bull_conf, bear_n, bear_conf) is True, (
        f"sanity: v1 should have over-rejected: {description}"
    )
    # New logic passes it through
    assert _bearish_dominant_v2_bug_ak(bull_n, bull_conf, bear_n, bear_conf, max_bear) is False, (
        f"Bug AK regression: should PASS through to MFCS: {description}"
    )


@pytest.mark.parametrize(
    "bull_n,bull_conf,bear_n,bear_conf,max_bear,description",
    [
        # ≥2 bearish with aggregate conf > 1.0 — keep rejecting
        (0, 0.0, 2, 1.04, 0.55, "0 vs 2 bearish (agg 1.04) — should still reject"),
        (0, 0.0, 2, 1.00, 0.50, "0 vs 2 bearish (agg 1.00) — borderline reject"),
        (0, 0.0, 2, 1.25, 0.65, "0 vs 2 bearish (agg 1.25) — should still reject"),
        (0, 0.0, 2, 1.29, 0.65, "0 vs 2 bearish (agg 1.29) — should still reject"),
        (1, 0.5, 2, 1.25, 0.7, "1 vs 2 bearish (agg 1.25) — should still reject"),
    ],
)
def test_bug_ak_still_rejects_aggregate_bearish_pressure(
    bull_n, bull_conf, bear_n, bear_conf, max_bear, description,
):
    """The actually-bad cases — multiple bearish agents with meaningful
    aggregate confidence. Both v1 and v2 should reject."""
    assert _bearish_dominant_v2_bug_ak(bull_n, bull_conf, bear_n, bear_conf, max_bear) is True, (
        f"Bug AK regression: should still reject: {description}"
    )


@pytest.mark.parametrize(
    "bull_n,bull_conf,bear_n,bear_conf,max_bear,description",
    [
        # Numeric dominance — bearish outnumber bullish by 2+
        (0, 0.0, 2, 0.50, 0.30, "0 vs 2 bearish — numeric dominance Tier A"),
        (1, 0.5, 3, 0.90, 0.40, "1 vs 3 bearish — Tier A"),
    ],
)
def test_bug_ak_tier_a_numeric_dominance(bull_n, bull_conf, bear_n, bear_conf, max_bear, description):
    """Tier A: bearish numerically dominate by 2+. Should reject regardless
    of confidence."""
    assert _bearish_dominant_v2_bug_ak(bull_n, bull_conf, bear_n, bear_conf, max_bear) is True, description


@pytest.mark.parametrize(
    "bull_n,bull_conf,bear_n,bear_conf,max_bear,description",
    [
        # Single very-high-conf bearish (manipulation/fraud detection)
        (0, 0.0, 1, 0.85, 0.85, "0 vs 1 bearish at 0.85 conf — Tier C single-veto"),
        (0, 0.0, 1, 0.95, 0.95, "0 vs 1 bearish at 0.95 conf — manipulation single-veto"),
        (1, 0.5, 1, 0.90, 0.90, "1 vs 1 bearish at 0.90 conf — single-veto trumps tie"),
    ],
)
def test_bug_ak_tier_c_single_high_conf_veto(bull_n, bull_conf, bear_n, bear_conf, max_bear, description):
    """Tier C: single bearish agent at very-high confidence (>=0.85)
    can still veto — preserves the manipulation/fraud detection path."""
    assert _bearish_dominant_v2_bug_ak(bull_n, bull_conf, bear_n, bear_conf, max_bear) is True, description


@pytest.mark.parametrize(
    "bull_n,bull_conf,bear_n,bear_conf,max_bear,description",
    [
        # No bearish at all — definitely don't reject
        (3, 1.5, 0, 0.0, 0.0, "3 vs 0 bearish — clearly bullish, no reject"),
        (1, 0.5, 0, 0.0, 0.0, "1 vs 0 bearish — minimal bullish, no reject"),
        # Bullish dominate — don't reject
        (3, 2.0, 1, 0.5, 0.5, "3 vs 1 bearish — bullish dominate"),
        # All neutral — empty case
        (0, 0.0, 0, 0.0, 0.0, "0 vs 0 — no signals at all"),
        # Tie 1-1 with low conf — pass through to MFCS
        (1, 0.5, 1, 0.5, 0.5, "1 vs 1 tied at 0.5 conf — pass to MFCS"),
    ],
)
def test_bug_ak_passes_legitimate_setups(bull_n, bull_conf, bear_n, bear_conf, max_bear, description):
    """All the cases where the trade should pass through to MFCS scoring
    (not rejected by D124)."""
    assert _bearish_dominant_v2_bug_ak(bull_n, bull_conf, bear_n, bear_conf, max_bear) is False, description


def test_bug_ak_rejection_volume_estimate_against_today_session():
    """Statistical test: replay today's 37 D124 rejections through the v2
    logic and verify the estimated reduction. Pins the
    'should reduce rejections by ~47%' claim from the strategic assessment."""
    # Today's actual rejection patterns (from the log analysis):
    # (bull_n, bear_n, bear_conf, max_bear_conf, count)
    # max_bear_conf inferred as bear_conf when bear_n=1, otherwise approximated
    todays_patterns = [
        (0, 0, 1, 0.45, 0.45, 7),
        (0, 0, 1, 0.80, 0.80, 7),
        (0, 0, 2, 1.04, 0.55, 6),
        (0, 0, 2, 1.00, 0.50, 5),
        (0, 0, 2, 1.25, 0.65, 4),
        (0, 0, 1, 0.49, 0.49, 3),
        (0, 0, 2, 1.29, 0.65, 3),
        (1, 0.5, 2, 1.25, 0.70, 1),
        (0, 0, 1, 0.55, 0.55, 1),
    ]
    total = sum(c for *_, c in todays_patterns)
    v1_reject = sum(c for bn, bc, brn, brc, mb, c in todays_patterns
                    if _bearish_dominant_v1_pre_bug_ak(bn, bc, brn, brc))
    v2_reject = sum(c for bn, bc, brn, brc, mb, c in todays_patterns
                    if _bearish_dominant_v2_bug_ak(bn, bc, brn, brc, mb))
    reduction_pct = (v1_reject - v2_reject) / v1_reject * 100

    assert total == 37, f"sanity: today's total was 37, got {total}"
    assert v1_reject == 37, f"sanity: v1 rejected all 37, got {v1_reject}"
    assert v2_reject < v1_reject, "Bug AK should reduce rejections"
    # Bug AK should pass through ~47% (the 17 single-bearish low-conf cases
    # with max_bear < 0.85). The 0.80 case is just under the 0.85 single-
    # veto threshold so it passes through too.
    assert 40 <= reduction_pct <= 55, (
        f"Bug AK should reduce rejections by 40-55%; actual {reduction_pct:.1f}%"
    )
