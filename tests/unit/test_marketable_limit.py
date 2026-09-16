"""doc 189: tests for the marketable, momentum-adaptive entry-limit offset.

WHY: Friday 5/29 the WIDE_STOP path submitted a PASSIVE limit at the stale eval
price -> CMND filled 0/2. A perfect PICK we can't FILL earns $0. The fix crosses
the spread by an offset that scales with conviction (strong continuer -> chase a
bit more to ensure the fill) and is CAPPED (never chase a runner too far). These
tests pin the pure offset math (train==serve, no executor/broker mocking).
"""
from __future__ import annotations

import pytest

from src.execution.alpaca_executor import (
    compute_marketable_limit,
    compute_marketable_offset,
)

# Production defaults (config/settings.py ExecutionConfig).
BASE = 0.004
MAXO = 0.015
FULL = 0.55


def _limit(side, entry, quote, mfcs):
    return compute_marketable_limit(
        side=side, entry_price=entry, quote_price=quote, mfcs=mfcs,
        base_offset=BASE, max_offset=MAXO, mfcs_full_at=FULL,
    )


def _offset(mfcs):
    return compute_marketable_offset(
        mfcs, base_offset=BASE, max_offset=MAXO, mfcs_full_at=FULL
    )


def test_none_mfcs_yields_base_offset():
    """No conviction signal -> stay passive at the base offset (don't chase)."""
    assert _offset(None) == pytest.approx(BASE)


def test_zero_mfcs_yields_base_offset():
    assert _offset(0.0) == pytest.approx(BASE)


def test_negative_mfcs_clamps_to_base():
    """Garbage/negative MFCS must not produce a sub-base (or negative) offset."""
    assert _offset(-1.0) == pytest.approx(BASE)


def test_mfcs_at_full_threshold_yields_max_offset():
    """A strong continuer (MFCS == full_at) chases up to the cap to ensure fill."""
    assert _offset(FULL) == pytest.approx(MAXO)


def test_mfcs_above_full_threshold_clamps_to_max():
    """NEVER chase a runner past the cap, no matter how high MFCS goes."""
    assert _offset(0.95) == pytest.approx(MAXO)
    assert _offset(10.0) == pytest.approx(MAXO)


def test_mfcs_halfway_is_linear_midpoint():
    """Momentum-adaptive: halfway conviction -> halfway between base and cap."""
    mid = _offset(FULL / 2.0)
    assert mid == pytest.approx(BASE + (MAXO - BASE) * 0.5)
    assert BASE < mid < MAXO


def test_offset_is_monotonic_in_mfcs():
    """Higher conviction -> never a smaller offset (chase more, not less)."""
    seq = [_offset(m) for m in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.7)]
    assert seq == sorted(seq)


def test_full_at_zero_is_safe_no_div_by_zero():
    """mfcs_full_at=0 must not divide-by-zero; degrade to the base offset."""
    assert compute_marketable_offset(
        0.6, base_offset=BASE, max_offset=MAXO, mfcs_full_at=0.0
    ) == pytest.approx(BASE)


def test_anchored_limit_crosses_the_spread_and_is_capped():
    """The applied limit = anchor*(1+offset): always above the anchor (marketable),
    never more than the cap above it (bounded worst fill)."""
    anchor = 3.57  # CMND's Friday price
    lo = round(anchor * (1.0 + _offset(0.0)), 4)
    hi = round(anchor * (1.0 + _offset(0.95)), 4)
    assert lo > anchor                      # even the weakest is marketable
    assert hi <= round(anchor * (1.0 + MAXO), 4) + 1e-9  # capped
    assert hi > lo                          # stronger conviction -> more aggressive


# ── doc 190: two-sided compute_marketable_limit (BUY long / SELL short) ──

def test_buy_anchors_to_ask_when_above_entry():
    """A long that ticked up since eval: anchor to the live ask (not stale entry)."""
    # ask 3.60 > entry 3.50 -> anchor 3.60, +1.5% at full MFCS = 3.654
    assert _limit("buy", 3.50, 3.60, FULL) == pytest.approx(3.654)


def test_buy_ignores_ask_below_entry():
    """If the ask is below the eval price, max() keeps entry as the anchor."""
    assert _limit("buy", 3.50, 3.40, 0.0) == pytest.approx(round(3.50 * (1 + BASE), 4))


def test_buy_no_quote_falls_back_to_entry_still_marketable():
    assert _limit("buy", 3.50, None, 0.0) == pytest.approx(round(3.50 * (1 + BASE), 4))


def test_sell_short_anchors_to_bid_and_crosses_down():
    """A short entry crosses DOWN to the bid to fill (sell-to-open)."""
    # bid 3.40 < entry 3.50 -> anchor 3.40, -1.5% at full MFCS = 3.349
    assert _limit("sell", 3.50, 3.40, FULL) == pytest.approx(3.349)


def test_sell_ignores_bid_above_entry():
    """If the bid is above the eval price, min() keeps entry as the anchor."""
    assert _limit("sell", 3.50, 3.60, 0.0) == pytest.approx(round(3.50 * (1 - BASE), 4))


def test_sell_no_quote_falls_back_to_entry_crossing_down():
    assert _limit("sell", 3.50, None, 0.0) == pytest.approx(round(3.50 * (1 - BASE), 4))


def test_buy_always_above_sell_always_below_entry():
    """Direction invariant: a BUY limit is >= entry, a SELL limit is <= entry."""
    for mfcs in (None, 0.0, 0.3, 0.55, 0.9):
        assert _limit("buy", 3.50, 3.55, mfcs) >= 3.50
        assert _limit("sell", 3.50, 3.45, mfcs) <= 3.50


def test_both_sides_bounded_by_cap():
    """Worst fill is bounded: buy <= anchor*(1+cap), sell >= anchor*(1-cap)."""
    buy = _limit("buy", 3.50, 3.60, 5.0)   # huge MFCS -> clamp at cap
    sell = _limit("sell", 3.50, 3.40, 5.0)
    assert buy <= round(3.60 * (1 + MAXO), 4) + 1e-9
    assert sell >= round(3.40 * (1 - MAXO), 4) - 1e-9
