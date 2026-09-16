"""D294 (2026-05-13) — Alpaca stop-price floor clamp regression.

Pin the QUCY production failure mode from 2026-05-13 paper-trading run:
QUCY at $0.55-0.58 hit `D73 UNPROCESSABLE: 'stop_loss.stop_price must be
<= base_price - 0.01'` on every OTO submission, triggering BRIDGE_CANCEL
churn that wasted ~5 attempts across the day.

Pre-D294: stop math (entry × (1 - 0.02)) at sub-$1 prices could land
within $0.01 of base after rounding. Post-D294: a helper clamps the
stop to satisfy Alpaca's strict `stop ≤ base - 0.01` (long) and
`stop ≥ base + 0.01` (short).
"""
from __future__ import annotations

import pytest

from src.data.alpaca_client import _clamp_stop_below_base


# ── Headline production-failure case (QUCY 2026-05-13) ────────────────


def test_qucy_failing_case_is_clamped():
    """The exact failure: base $0.5573, stop $0.55 (only $0.0073 below)."""
    out = _clamp_stop_below_base(0.55, 0.5573, side="long")
    # Must satisfy Alpaca's strict inequality: stop <= base - 0.01
    assert out <= 0.5573 - 0.01 + 1e-9, (
        f"clamped stop {out} violates Alpaca's stop <= base - 0.01"
    )
    # Should be widened to exactly the legal ceiling
    assert out == pytest.approx(0.5473, abs=1e-9)


def test_qucy_already_legal_stop_is_untouched():
    """A stop already comfortably below base must not be moved."""
    out = _clamp_stop_below_base(0.40, 0.5573, side="long")
    assert out == pytest.approx(0.4000)


# ── Sub-$1 boundary cases (4-decimal tick) ────────────────────────────


@pytest.mark.parametrize(
    "stop_in, base, expected",
    [
        (0.5500, 0.5573, 0.5473),  # exactly at the violation threshold
        (0.5573, 0.5573, 0.5473),  # stop = base (worst case)
        (0.5573 + 0.005, 0.5573, 0.5473),  # stop above base (data bug)
        (0.5400, 0.5573, 0.5400),  # already legal (≥ 0.0173 below)
        (0.0099, 0.0199, 0.0099),  # extreme penny stock
        (0.0150, 0.0199, 0.0099),  # extreme penny stock, too tight
    ],
)
def test_long_clamp_sub_dollar(stop_in, base, expected):
    out = _clamp_stop_below_base(stop_in, base, side="long")
    assert out == pytest.approx(expected, abs=1e-9), (
        f"clamp({stop_in}, {base}) = {out}, expected {expected}"
    )
    assert out <= base - 0.01 + 1e-9


# ── Above-$1 boundary cases (2-decimal tick) ──────────────────────────


@pytest.mark.parametrize(
    "stop_in, base, expected",
    [
        (199.99, 200.00, 199.99),  # legal — exactly $0.01 below
        (199.995, 200.00, 199.99),  # too close after rounding
        (200.00, 200.00, 199.99),  # equal to base
        (50.00, 100.00, 50.00),    # comfortable distance untouched
        (1.00, 1.005, 0.99),       # straddling the $1 boundary on base
    ],
)
def test_long_clamp_above_dollar(stop_in, base, expected):
    out = _clamp_stop_below_base(stop_in, base, side="long")
    assert out == pytest.approx(expected, abs=1e-9), (
        f"clamp({stop_in}, {base}) = {out}, expected {expected}"
    )
    assert out <= base - 0.01 + 1e-9


# ── Short-side mirror ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stop_in, base, expected",
    [
        (0.56, 0.5573, 0.5673),     # too close above, must widen up
        (0.60, 0.5573, 0.6000),     # already comfortably above
        (200.005, 200.00, 200.01),  # round-up to legal floor on $200
    ],
)
def test_short_clamp(stop_in, base, expected):
    out = _clamp_stop_below_base(stop_in, base, side="short")
    assert out == pytest.approx(expected, abs=1e-9)
    assert out >= base + 0.01 - 1e-9


# ── Invalid side rejected ─────────────────────────────────────────────


def test_invalid_side_raises():
    with pytest.raises(ValueError, match="side must be"):
        _clamp_stop_below_base(0.5, 0.6, side="diagonal")


# ── Behavior preserved: clamp never tightens an already-legal stop ────


def test_long_clamp_never_tightens():
    """The clamp WIDENS toward safety; it must NEVER move a legal stop
    closer to base (which would be a tightening = bad direction)."""
    base = 5.00
    for stop_pct in [0.01, 0.05, 0.10, 0.20, 0.50]:
        stop_in = base * (1 - stop_pct)
        out = _clamp_stop_below_base(stop_in, base, side="long")
        # Distance must be >= original distance (we only widen)
        assert (base - out) >= (base - stop_in) - 1e-9, (
            f"clamp({stop_in:.4f}, {base}) = {out:.4f} TIGHTENED the stop"
        )


# ── Integration: production OTO payload would not have failed ─────────


def test_qucy_payload_passes_alpaca_inequality():
    """End-to-end: an OTO payload built with the clamped stop satisfies
    Alpaca's documented constraint string-encoded as it would be sent."""
    base_price = 0.5573
    raw_stop = base_price * (1 - 0.005)  # arbitrarily tight
    clamped = _clamp_stop_below_base(raw_stop, base_price, side="long")
    # This is the exact comparison Alpaca's server-side validator does:
    #   parsed_stop <= parsed_base - 0.01
    assert float(str(clamped)) <= float(str(round(base_price, 4))) - 0.01 + 1e-9
