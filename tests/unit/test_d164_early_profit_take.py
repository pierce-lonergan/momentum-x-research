"""
Tests for D164: Time-based partial profit take at T+2 minutes.

Coverage:
  - Timing: WAIT before T+2min, TAKE_PROFIT at T+2min, SKIP past deadline
  - Profitability: TAKE_PROFIT only when >= min_profit_pct
  - Quantity calculation: floor(original_qty * exit_pct)
  - Short positions: profitable when price < entry
  - State lifecycle: register → check → mark_executed → remove
  - Edge cases: duplicate registration, remove cleans state
  - Backtest scenarios: ARTL and SST from Mar 30
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from src.execution.early_profit_take import (
    EarlyProfitAction,
    EarlyProfitTakeConfig,
    EarlyProfitTaker,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_mgr(**kwargs) -> EarlyProfitTaker:
    """Build an EarlyProfitTaker with test-friendly defaults."""
    defaults = dict(
        enabled=True,
        delay_seconds=120.0,
        exit_pct=0.50,
        min_profit_pct=0.005,  # +0.5%
        max_delay_seconds=300.0,
        apply_to_shorts=True,
    )
    defaults.update(kwargs)
    cfg = EarlyProfitTakeConfig(**defaults)
    return EarlyProfitTaker(config=cfg)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ts(base: datetime, offset_seconds: float) -> datetime:
    """Return base + offset_seconds."""
    return base + timedelta(seconds=offset_seconds)


def register_long(
    mgr: EarlyProfitTaker,
    symbol: str = "AAAA",
    entry: float = 10.0,
    qty: int = 1000,
    fill_time: datetime | None = None,
) -> datetime:
    """Register a long position and return the fill_time used."""
    fill_time = fill_time or utcnow()
    mgr.register_fill(
        symbol=symbol,
        fill_time=fill_time,
        entry_price=entry,
        qty=qty,
        direction="long",
    )
    return fill_time


def register_short(
    mgr: EarlyProfitTaker,
    symbol: str = "AAAA",
    entry: float = 10.0,
    qty: int = 1000,
    fill_time: datetime | None = None,
) -> datetime:
    """Register a short position and return the fill_time used."""
    fill_time = fill_time or utcnow()
    mgr.register_fill(
        symbol=symbol,
        fill_time=fill_time,
        entry_price=entry,
        qty=qty,
        direction="short",
    )
    return fill_time


# ── Timing ────────────────────────────────────────────────────────────────────


class TestTiming:

    def test_not_triggered_before_delay(self):
        """At T+1min (< 2min), should return WAIT."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        # Price is +1% profitable, but we're only at T+60s
        action = mgr.check("AAAA", 10.10, ts(fill, 60))
        assert action == EarlyProfitAction.WAIT, f"Expected WAIT, got {action}"

    def test_triggered_at_delay_when_profitable(self):
        """At T+2min with +1% profit → TAKE_PROFIT."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 10.10, ts(fill, 120))
        assert action == EarlyProfitAction.TAKE_PROFIT, f"Expected TAKE_PROFIT, got {action}"

    def test_triggered_slightly_past_delay(self):
        """At T+2min+5s, check still fires if within deadline."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 10.10, ts(fill, 125))
        assert action == EarlyProfitAction.TAKE_PROFIT

    def test_skip_after_deadline(self):
        """At T+6min (past 5min deadline) → SKIP regardless of profitability."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        # Very profitable, but past deadline
        action = mgr.check("AAAA", 15.0, ts(fill, 360))
        assert action == EarlyProfitAction.SKIP, f"Expected SKIP past deadline, got {action}"

    def test_skip_after_deadline_persists(self):
        """Once SKIP returned (deadline), subsequent checks also return SKIP."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        mgr.check("AAAA", 15.0, ts(fill, 360))   # sets skipped=True
        action = mgr.check("AAAA", 20.0, ts(fill, 400))
        assert action == EarlyProfitAction.SKIP

    def test_wait_boundary_one_second_before(self):
        """At T+119s (1s before trigger), should still WAIT."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 10.10, ts(fill, 119))
        assert action == EarlyProfitAction.WAIT


# ── Profitability ─────────────────────────────────────────────────────────────


class TestProfitability:

    def test_skip_when_not_profitable(self):
        """At T+2min with -1% loss → SKIP."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 9.90, ts(fill, 120))
        assert action == EarlyProfitAction.SKIP, f"Expected SKIP on loss, got {action}"

    def test_skip_when_exactly_at_entry(self):
        """At T+2min with 0% (price == entry) → SKIP (must exceed min_profit_pct)."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 10.0, ts(fill, 120))
        assert action == EarlyProfitAction.SKIP

    def test_skip_when_below_min_profit(self):
        """At T+2min with +0.3% (below 0.5% min) → SKIP."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        # +0.3%: 10.0 * 1.003 = 10.03
        action = mgr.check("AAAA", 10.03, ts(fill, 120))
        assert action == EarlyProfitAction.SKIP, f"Expected SKIP below min_profit, got {action}"

    def test_take_profit_at_min_threshold(self):
        """At T+2min with exactly +0.5% → TAKE_PROFIT."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        # exactly +0.5%: 10.0 * 1.005 = 10.05
        action = mgr.check("AAAA", 10.05, ts(fill, 120))
        assert action == EarlyProfitAction.TAKE_PROFIT, (
            f"Expected TAKE_PROFIT at min threshold, got {action}"
        )

    def test_skip_persists_after_unprofitable_check(self):
        """Once SKIP returned at T+2min, subsequent checks also return SKIP."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        mgr.check("AAAA", 9.90, ts(fill, 120))   # SKIP — not profitable
        # Even if price later becomes profitable, we already skipped
        action = mgr.check("AAAA", 12.0, ts(fill, 150))
        assert action == EarlyProfitAction.SKIP


# ── Already taken ─────────────────────────────────────────────────────────────


class TestAlreadyTaken:

    def test_already_taken_returns_already_taken(self):
        """After mark_executed(), subsequent check() → ALREADY_TAKEN."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        # Simulate execution
        state = mgr.get_state("AAAA")
        if state:
            state.skipped = False  # ensure not skipped
        mgr.mark_executed("AAAA", exit_price=10.10, qty_sold=500)
        action = mgr.check("AAAA", 10.20, ts(fill, 180))
        assert action == EarlyProfitAction.ALREADY_TAKEN

    def test_mark_executed_records_details(self):
        """After mark_executed, state has correct exit_price and qty_sold."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, entry=10.0, fill_time=fill)
        mgr.mark_executed("AAAA", exit_price=10.25, qty_sold=500)
        state = mgr.get_state("AAAA")
        assert state is not None
        assert state.executed is True
        assert abs(state.exit_price - 10.25) < 0.0001
        assert state.qty_sold == 500


# ── Quantity calculation ──────────────────────────────────────────────────────


class TestQuantityCalculation:

    def test_correct_qty_round_number(self):
        """1000 shares × 50% = 500 to sell."""
        mgr = make_mgr()
        register_long(mgr, qty=1000)
        assert mgr.qty_to_sell("AAAA") == 500

    def test_correct_qty_odd_number(self):
        """333 shares × 50% = floor(166.5) = 166 to sell."""
        mgr = make_mgr()
        register_long(mgr, qty=333)
        assert mgr.qty_to_sell("AAAA") == 166

    def test_correct_qty_small_position(self):
        """3 shares × 50% = floor(1.5) = 1 to sell."""
        mgr = make_mgr()
        register_long(mgr, qty=3)
        assert mgr.qty_to_sell("AAAA") == 1

    def test_correct_qty_one_share(self):
        """1 share × 50% = floor(0.5) = 0 to sell (too small)."""
        mgr = make_mgr()
        register_long(mgr, qty=1)
        assert mgr.qty_to_sell("AAAA") == 0

    def test_qty_unregistered_returns_zero(self):
        """qty_to_sell for unknown symbol returns 0."""
        mgr = make_mgr()
        assert mgr.qty_to_sell("GHOST") == 0


# ── Short positions ───────────────────────────────────────────────────────────


class TestShortPositions:

    def test_short_profitable_means_price_below(self):
        """Short at $10, price $9.50 at T+2min (+5% gain) → TAKE_PROFIT."""
        mgr = make_mgr()
        fill = utcnow()
        register_short(mgr, entry=10.0, fill_time=fill)
        # Short profit: (10.0 - 9.50) / 10.0 = 5%
        action = mgr.check("AAAA", 9.50, ts(fill, 120))
        assert action == EarlyProfitAction.TAKE_PROFIT, f"Expected TAKE_PROFIT for short, got {action}"

    def test_short_not_profitable_price_above(self):
        """Short at $10, price $10.50 at T+2min (-5% loss) → SKIP."""
        mgr = make_mgr()
        fill = utcnow()
        register_short(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 10.50, ts(fill, 120))
        assert action == EarlyProfitAction.SKIP, f"Expected SKIP for losing short, got {action}"

    def test_short_at_entry_price(self):
        """Short at $10, price $10.00 at T+2min (0% gain) → SKIP."""
        mgr = make_mgr()
        fill = utcnow()
        register_short(mgr, entry=10.0, fill_time=fill)
        action = mgr.check("AAAA", 10.0, ts(fill, 120))
        assert action == EarlyProfitAction.SKIP

    def test_apply_to_shorts_false_not_registered(self):
        """With apply_to_shorts=False, register_fill for short is a no-op."""
        mgr = make_mgr(apply_to_shorts=False)
        fill = utcnow()
        register_short(mgr, fill_time=fill)
        # Should not be tracked
        action = mgr.check("AAAA", 9.0, ts(fill, 120))
        assert action == EarlyProfitAction.NOT_TRACKED


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:

    def test_remove_cleans_state(self):
        """After remove(), check returns NOT_TRACKED."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, fill_time=fill)
        mgr.remove("AAAA")
        action = mgr.check("AAAA", 15.0, ts(fill, 120))
        assert action == EarlyProfitAction.NOT_TRACKED

    def test_remove_nonexistent_is_safe(self):
        """Removing an unknown symbol does not raise."""
        mgr = make_mgr()
        mgr.remove("DOESNOTEXIST")  # should not raise

    def test_not_tracked_for_unknown_symbol(self):
        """check() for a symbol that was never registered → NOT_TRACKED."""
        mgr = make_mgr()
        action = mgr.check("GHOST", 50.0, utcnow())
        assert action == EarlyProfitAction.NOT_TRACKED

    def test_register_duplicate_is_idempotent(self):
        """Registering same symbol twice preserves original state."""
        mgr = make_mgr()
        fill = utcnow()
        register_long(mgr, symbol="AAAA", entry=10.0, qty=1000, fill_time=fill)
        # Second register with different entry — should be ignored
        mgr.register_fill("AAAA", fill_time=fill, entry_price=20.0, qty=500, direction="long")
        state = mgr.get_state("AAAA")
        assert state is not None
        assert state.entry_price == 10.0, "Second register should not overwrite state"
        assert state.original_qty == 1000

    def test_disabled_config_not_tracked(self):
        """With enabled=False, register is a no-op and check returns NOT_TRACKED."""
        mgr = make_mgr(enabled=False)
        fill = utcnow()
        register_long(mgr, fill_time=fill)
        action = mgr.check("AAAA", 15.0, ts(fill, 120))
        assert action == EarlyProfitAction.NOT_TRACKED


# ── Multiple positions ────────────────────────────────────────────────────────


class TestMultiplePositions:

    def test_multiple_positions_independent(self):
        """Two positions tracked independently."""
        mgr = make_mgr()
        fill_a = utcnow()
        fill_b = utcnow()
        mgr.register_fill("AAAA", fill_time=fill_a, entry_price=10.0, qty=500, direction="long")
        mgr.register_fill("BBBB", fill_time=fill_b, entry_price=20.0, qty=200, direction="long")

        # AAAA: T+2min, +2% → TAKE_PROFIT
        action_a = mgr.check("AAAA", 10.20, ts(fill_a, 120))
        # BBBB: T+1min → WAIT
        action_b = mgr.check("BBBB", 20.40, ts(fill_b, 60))

        assert action_a == EarlyProfitAction.TAKE_PROFIT
        assert action_b == EarlyProfitAction.WAIT

    def test_executing_one_does_not_affect_other(self):
        """mark_executed on AAAA does not affect BBBB."""
        mgr = make_mgr()
        fill_a = utcnow()
        fill_b = utcnow()
        mgr.register_fill("AAAA", fill_time=fill_a, entry_price=10.0, qty=500, direction="long")
        mgr.register_fill("BBBB", fill_time=fill_b, entry_price=20.0, qty=200, direction="long")
        mgr.mark_executed("AAAA", exit_price=10.20, qty_sold=250)
        action_b = mgr.check("BBBB", 20.50, ts(fill_b, 120))
        assert action_b == EarlyProfitAction.TAKE_PROFIT


# ── Backtest validation ───────────────────────────────────────────────────────


class TestBacktestScenarios:

    def test_backtest_artl_scenario(self):
        """
        ARTL (Mar 30): Entry $7.68, 333 shares.
        At T+2min, price ~$8.00 (+4.2% gain).
        Expected: TAKE_PROFIT, sell 166 of 333, lock in partial profit.

        Partial PnL = (8.00 - 7.68) * 166 = $53.12
        """
        mgr = make_mgr()
        fill = utcnow()
        mgr.register_fill(
            symbol="ARTL",
            fill_time=fill,
            entry_price=7.68,
            qty=333,
            direction="long",
        )
        action = mgr.check("ARTL", 8.00, ts(fill, 120))
        assert action == EarlyProfitAction.TAKE_PROFIT, (
            f"ARTL at +4.2% should TAKE_PROFIT, got {action}"
        )
        qty_to_sell = mgr.qty_to_sell("ARTL")
        assert qty_to_sell == 166, f"ARTL: expected 166 (floor(333*0.5)), got {qty_to_sell}"
        # Verify partial PnL math
        partial_pnl = (8.00 - 7.68) * qty_to_sell
        assert abs(partial_pnl - 53.12) < 0.01, f"ARTL partial PnL: expected ~$53.12, got ${partial_pnl:.2f}"

    def test_backtest_sst_scenario(self):
        """
        SST (Mar 30): Entry $3.20, 1249 shares.
        At T+2min, price ~$3.30 (+3.1% gain).
        Expected: TAKE_PROFIT, sell 624 of 1249, lock in partial profit.

        Partial PnL = (3.30 - 3.20) * 624 = $62.40
        """
        mgr = make_mgr()
        fill = utcnow()
        mgr.register_fill(
            symbol="SST",
            fill_time=fill,
            entry_price=3.20,
            qty=1249,
            direction="long",
        )
        action = mgr.check("SST", 3.30, ts(fill, 120))
        assert action == EarlyProfitAction.TAKE_PROFIT, (
            f"SST at +3.1% should TAKE_PROFIT, got {action}"
        )
        qty_to_sell = mgr.qty_to_sell("SST")
        assert qty_to_sell == 624, f"SST: expected 624 (floor(1249*0.5)), got {qty_to_sell}"
        partial_pnl = (3.30 - 3.20) * qty_to_sell
        assert abs(partial_pnl - 62.40) < 0.01, f"SST partial PnL: expected ~$62.40, got ${partial_pnl:.2f}"

    def test_artl_not_profitable_at_entry(self):
        """
        ARTL: If price is still at entry at T+2min (no movement), should SKIP.
        """
        mgr = make_mgr()
        fill = utcnow()
        mgr.register_fill(
            symbol="ARTL",
            fill_time=fill,
            entry_price=7.68,
            qty=333,
            direction="long",
        )
        action = mgr.check("ARTL", 7.68, ts(fill, 120))
        assert action == EarlyProfitAction.SKIP

    def test_sst_loss_at_trigger(self):
        """
        SST: If down -2% at T+2min, should SKIP.
        """
        mgr = make_mgr()
        fill = utcnow()
        mgr.register_fill(
            symbol="SST",
            fill_time=fill,
            entry_price=3.20,
            qty=1249,
            direction="long",
        )
        action = mgr.check("SST", 3.136, ts(fill, 120))  # -2%
        assert action == EarlyProfitAction.SKIP
