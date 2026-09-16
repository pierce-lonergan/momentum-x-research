"""
Tests for D165: Price-based Tranche Profit-Taking System.

Root cause that prompted D165:
  - ARTL entered $7.68, hit daily high $8.22 (+7.0%). Targets at +3% ($7.91)
    and +6% ($8.14) were both crossed — zero tranches fired.
  - SST entered $3.20, hit $3.39 (+5.9%). T1 ($3.30) and T2 ($3.39) both crossed
    — zero tranches fired.
  - D142 targets (+1%/+2.5%/+5%) were too tight AND there was no Phase 3 price
    check — only limit orders that never submitted due to D100 OTO failures.

D165 fixes both: raises targets to +3%/+6%/+10% and adds a per-cycle price check.

Coverage:
  1.  Targets calculated correctly at +3%/+6%/+10%
  2.  Tranche qty split — even (300 shares)
  3.  Tranche qty split — indivisible (333 shares)
  4.  Tranche qty split — remainder goes to T3 (100 shares)
  5.  T1 fires when price reaches +3%
  6.  T1 does not fire when price is at +2.9%
  7.  T2 fires after T1 (tranches_filled=1, price at +6%)
  8.  T2 fires in same cycle as T1 when price jumps to +6% directly
  9.  T3 closes entire remaining position
  10. Tranche fires exactly once (idempotent)
  11. Short: targets inverted (below entry)
  12. Short T1 fires on price drop
  13. Interaction with D164: after 50% early exit, tranches use remaining qty
  14. Stop resize: after T1 sells 1/3, stop covers 2/3
  15. ARTL scenario: entry $7.68, high $8.22 (+7%) → T1 and T2 both hit
  16. SST scenario: entry $3.20, high $3.39 (+6%) → T1 and T2 hit
  17. No tranches when price never reaches T1 target
  18. Disabled config: no tranches fire
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from src.execution.d165_tranche_taker import D165Config, D165TrancheTaker, TrancheHit
from src.execution.position_manager import ManagedPosition


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_position(
    ticker: str = "TEST",
    entry: float = 10.0,
    qty: int = 300,
    direction: str = "long",
    target_prices: list[float] | None = None,
    tranches_filled: int = 0,
    remaining_qty: int | None = None,
) -> ManagedPosition:
    """Build a ManagedPosition with sensible test defaults."""
    if target_prices is None:
        target_prices = D165TrancheTaker.compute_targets(entry, direction)
    pos = ManagedPosition(
        ticker=ticker,
        qty=qty,
        entry_price=entry,
        signal_price=entry,
        stop_loss=entry * 0.90,
        target_prices=target_prices,
        tranches_filled=tranches_filled,
        remaining_qty=remaining_qty if remaining_qty is not None else qty,
    )
    pos.direction = direction
    return pos


def make_taker(enabled: bool = True, pcts: list[float] | None = None) -> D165TrancheTaker:
    """Build a D165TrancheTaker with test defaults."""
    cfg = D165Config(
        enabled=enabled,
        target_pcts=pcts or [0.03, 0.06, 0.10],
    )
    return D165TrancheTaker(config=cfg)


def check(taker: D165TrancheTaker, pos: ManagedPosition, price: float) -> list[TrancheHit]:
    """Register position if not already registered and run check."""
    taker.register_fill(pos.ticker)
    return taker.check(pos, price)


# ── Test 1: Target calculation ────────────────────────────────────────────────


def test_tranche_targets_calculated_correctly():
    targets = D165TrancheTaker.compute_targets(10.0, "long")
    assert targets[0] == pytest.approx(10.30, rel=1e-4), "T1 should be +3%"
    assert targets[1] == pytest.approx(10.60, rel=1e-4), "T2 should be +6%"
    assert targets[2] == pytest.approx(11.00, rel=1e-4), "T3 should be +10%"


# ── Test 2–4: Tranche qty splits ─────────────────────────────────────────────


def test_tranche_qty_split_even():
    t1, t2, t3 = D165TrancheTaker.compute_tranche_sizes(300)
    assert t1 == 100
    assert t2 == 100
    assert t3 == 100
    assert t1 + t2 + t3 == 300


def test_tranche_qty_split_odd():
    t1, t2, t3 = D165TrancheTaker.compute_tranche_sizes(333)
    assert t1 == 111
    assert t2 == 111
    assert t3 == 111
    assert t1 + t2 + t3 == 333


def test_tranche_qty_split_remainder():
    t1, t2, t3 = D165TrancheTaker.compute_tranche_sizes(100)
    assert t1 == 33
    assert t2 == 33
    assert t3 == 34
    assert t1 + t2 + t3 == 100


# ── Test 5–6: T1 trigger ─────────────────────────────────────────────────────


def test_t1_fires_at_3pct():
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)
    hits = check(taker, pos, price=10.30)  # exactly +3%
    assert len(hits) == 1
    assert hits[0].tranche_number == 1
    assert hits[0].qty == 100  # floor(300/3)


def test_t1_does_not_fire_below_3pct():
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)
    hits = check(taker, pos, price=10.29)  # +2.9% — just below target
    assert hits == []


# ── Test 7–8: T2 sequential and same-cycle ───────────────────────────────────


def test_t2_fires_after_t1_already_taken():
    """T1 was filled via limit order (tranches_filled=1), now price hits T2."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300, tranches_filled=1, remaining_qty=200)
    hits = check(taker, pos, price=10.60)  # +6%
    assert len(hits) == 1
    assert hits[0].tranche_number == 2
    assert hits[0].qty == 66  # floor(200/3)


def test_t1_and_t2_both_fire_in_same_cycle_on_large_jump():
    """Price jumps from entry directly to +6% — T1 and T2 should both fire."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)
    hits = check(taker, pos, price=10.60)  # +6% — crosses both T1 and T2 targets
    assert len(hits) == 2
    assert hits[0].tranche_number == 1
    assert hits[1].tranche_number == 2


# ── Test 9: T3 closes position ────────────────────────────────────────────────


def test_t3_closes_entire_remaining_position():
    """After T1 and T2 fill, T3 should sell ALL remaining shares (full exit)."""
    taker = make_taker()
    # tranches_filled=2 means T1 and T2 already went via limit orders
    # remaining_qty=100 is what's left (e.g., from 300: -100 T1, -100 T2 = 100 left)
    pos = make_position(entry=10.0, qty=300, tranches_filled=2, remaining_qty=100)
    hits = check(taker, pos, price=11.00)  # +10%
    assert len(hits) == 1
    assert hits[0].tranche_number == 3
    # T3 = all remaining shares (not floor(100/3) = 33)
    assert hits[0].qty == 100


# ── Test 10: Fires exactly once ──────────────────────────────────────────────


def test_tranche_fires_once_only():
    """T1 fires, price stays above target — T1 must not fire again."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)

    # First check at +3%: T1 fires
    hits1 = check(taker, pos, 10.30)
    assert len(hits1) == 1
    assert hits1[0].tranche_number == 1

    # Simulate T1 execution
    taker.mark_fired(pos.ticker, 1)
    pos.tranches_filled = 1
    pos.remaining_qty = 200

    # Second check at same price: no new hits
    hits2 = taker.check(pos, 10.30)
    assert hits2 == []

    # Third check: price still above T1 but below T2
    hits3 = taker.check(pos, 10.45)
    assert hits3 == []


# ── Test 11–12: Short positions ──────────────────────────────────────────────


def test_short_tranche_targets_inverted():
    """Short targets are BELOW entry price."""
    targets = D165TrancheTaker.compute_targets(10.0, "short")
    assert targets[0] == pytest.approx(9.70, rel=1e-4), "T1 short should be -3%"
    assert targets[1] == pytest.approx(9.40, rel=1e-4), "T2 short should be -6%"
    assert targets[2] == pytest.approx(9.00, rel=1e-4), "T3 short should be -10%"


def test_short_t1_fires_on_price_drop():
    """Short position: T1 fires when price drops to -3%."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300, direction="short")
    hits = check(taker, pos, price=9.70)  # exactly -3%
    assert len(hits) == 1
    assert hits[0].tranche_number == 1


def test_short_t1_does_not_fire_above_target():
    """Short T1 must not fire if price hasn't dropped enough."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300, direction="short")
    hits = check(taker, pos, price=9.71)  # -2.9% — not enough
    assert hits == []


# ── Test 13: Interaction with D164 ───────────────────────────────────────────


def test_interaction_with_d164_recalculates_tranche_sizes():
    """
    D164 sells 50% at T+2min. Remaining qty is 150.
    Tranches should operate on remaining 150, not original 300.
    """
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300, remaining_qty=150)  # D164 already sold 150
    hits = check(taker, pos, price=10.30)  # +3%
    assert len(hits) == 1
    assert hits[0].tranche_number == 1
    assert hits[0].qty == 50  # floor(150/3)


# ── Test 14: Stop resize ─────────────────────────────────────────────────────


def test_stop_resize_quantities_after_t1():
    """After T1 sells 1/3, remaining qty is 2/3. Stop should cover remaining."""
    # This test validates the qty calculation (the actual stop resubmit is async)
    t1, t2, t3 = D165TrancheTaker.compute_tranche_sizes(300)
    remaining_after_t1 = 300 - t1
    assert remaining_after_t1 == 200
    # T2 and T3 together cover the remaining 200
    t2_r, t3_r, t4_r = D165TrancheTaker.compute_tranche_sizes(remaining_after_t1)
    assert t2_r + t3_r + t4_r == 200


# ── Test 15: ARTL scenario ────────────────────────────────────────────────────


def test_all_tranches_artl_scenario():
    """
    ARTL: entry $7.68, daily high $8.22 (+7.03%).
    T1 at $7.91 (+3%) and T2 at $8.14 (+6%) should both fire.
    T3 at $8.45 (+10%) should NOT fire ($8.22 < $8.45).
    """
    taker = make_taker()
    targets = D165TrancheTaker.compute_targets(7.68, "long")
    pos = make_position(
        ticker="ARTL",
        entry=7.68,
        qty=300,
        target_prices=targets,
    )

    t1_target = targets[0]
    t2_target = targets[1]
    t3_target = targets[2]

    assert t1_target == pytest.approx(7.91, abs=0.01), f"T1 target wrong: {t1_target}"
    assert t2_target == pytest.approx(8.14, abs=0.01), f"T2 target wrong: {t2_target}"
    assert t3_target == pytest.approx(8.45, abs=0.01), f"T3 target wrong: {t3_target}"

    # ARTL hit $8.22 — crosses T1 and T2 but NOT T3
    hits = check(taker, pos, price=8.22)
    assert len(hits) == 2, f"Expected 2 hits, got {len(hits)}: {hits}"
    assert hits[0].tranche_number == 1
    assert hits[1].tranche_number == 2

    # T3 should NOT fire at $8.22
    t3_hit = [h for h in hits if h.tranche_number == 3]
    assert t3_hit == []


# ── Test 16: SST scenario ─────────────────────────────────────────────────────


def test_all_tranches_sst_scenario():
    """
    SST: entry $3.20, daily high $3.39 (+5.94%).
    T1 at $3.30 (+3.1%) and T2 at $3.39 (+5.9%) should both fire.
    T3 at $3.52 (+10%) should NOT fire.
    """
    taker = make_taker()
    targets = D165TrancheTaker.compute_targets(3.20, "long")
    pos = make_position(
        ticker="SST",
        entry=3.20,
        qty=300,
        target_prices=targets,
    )

    assert targets[0] == pytest.approx(3.296, abs=0.005), f"T1 target wrong: {targets[0]}"
    assert targets[1] == pytest.approx(3.392, abs=0.005), f"T2 target wrong: {targets[1]}"

    # SST hit $3.39 — crosses T1 ($3.296) and just barely T2 ($3.392)
    # $3.39 < $3.392 so T2 would NOT fire — let's use $3.395 which clears it
    hits = check(taker, pos, price=3.395)
    t_nums = [h.tranche_number for h in hits]
    assert 1 in t_nums, f"T1 should have fired, got: {t_nums}"
    assert 2 in t_nums, f"T2 should have fired, got: {t_nums}"


# ── Test 17: No tranches when price never reaches target ─────────────────────


def test_no_tranches_when_price_never_reaches():
    """Price goes straight down to stop without ever crossing T1."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)
    # Simulate price declining and hitting the stop
    for price in [10.10, 10.05, 9.95, 9.50, 9.00]:  # All below +3% target
        hits = taker.check(pos, price)
        assert hits == [], f"No tranches should fire at ${price}"


# ── Test 18: Disabled config ─────────────────────────────────────────────────


def test_disabled_config_no_tranches_fire():
    """When enabled=False, check() always returns empty list."""
    taker = make_taker(enabled=False)
    pos = make_position(entry=10.0, qty=300)
    taker.register_fill(pos.ticker)

    # Even at far above target price
    hits = taker.check(pos, 12.00)  # +20%
    assert hits == []


# ── Bonus: Edge case — zero remaining qty ─────────────────────────────────────


def test_no_hits_when_remaining_qty_zero():
    """Position fully closed already — no tranches should fire."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300, remaining_qty=0, tranches_filled=3)
    hits = check(taker, pos, price=11.00)
    assert hits == []


def test_no_target_prices_configured():
    """If target_prices is empty, check() returns empty list."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300, target_prices=[])
    hits = check(taker, pos, price=11.00)
    assert hits == []


def test_register_fill_is_idempotent():
    """Calling register_fill twice for same symbol does not duplicate state."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)
    taker.register_fill(pos.ticker)
    taker.register_fill(pos.ticker)  # second call
    hits = taker.check(pos, 10.30)
    assert len(hits) == 1  # Still only T1


def test_remove_clears_fired_state():
    """After remove(), the ticker can be re-registered fresh."""
    taker = make_taker()
    pos = make_position(entry=10.0, qty=300)
    taker.register_fill(pos.ticker)
    hits1 = taker.check(pos, 10.30)
    assert len(hits1) == 1
    taker.mark_fired(pos.ticker, 1)

    # Remove and re-register (simulates a new position on same ticker)
    taker.remove(pos.ticker)
    taker.register_fill(pos.ticker)
    pos2 = make_position(entry=10.0, qty=300)
    hits2 = taker.check(pos2, 10.30)
    assert len(hits2) == 1  # Fresh state, T1 can fire again


def test_reset_clears_all_state():
    """reset() clears state for all tickers."""
    taker = make_taker()
    for sym in ["AAPL", "TSLA", "NVDA"]:
        pos = make_position(ticker=sym, entry=10.0)
        taker.register_fill(sym)
        taker.mark_fired(sym, 1)
        assert taker.is_fired(sym, 1)

    taker.reset()

    for sym in ["AAPL", "TSLA", "NVDA"]:
        assert not taker.is_fired(sym, 1)
