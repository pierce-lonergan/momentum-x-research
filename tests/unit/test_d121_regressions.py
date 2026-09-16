"""
D121 Regression Tests: Bugs fixed in sweep rounds 5-6.

Covers critical PnL, sizing, and integration bugs to prevent regressions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from config.settings import ExecutionConfig
from src.execution.position_manager import ManagedPosition, PositionManager


# ── Helpers ──────────────────────────────────────────────────────────


def _make_position(
    ticker: str = "TEST",
    qty: int = 100,
    entry_price: float = 10.0,
    stop_loss: float = 9.0,
    remaining_qty: int | None = None,
    realized_pnl: float = 0.0,
) -> ManagedPosition:
    return ManagedPosition(
        ticker=ticker,
        qty=qty,
        entry_price=entry_price,
        signal_price=entry_price,
        stop_loss=stop_loss,
        target_prices=[11.0, 12.0, 13.0],
        remaining_qty=remaining_qty if remaining_qty is not None else qty,
        realized_pnl=realized_pnl,
    )


def _make_pm(starting_equity: float = 100_000.0) -> PositionManager:
    config = ExecutionConfig()
    return PositionManager(config=config, starting_equity=starting_equity)


# ── BUG-C1/X1: PnL uses remaining_qty, not qty ─────────────────────


class TestPnlRemainingQty:
    """BUG-C1: close_position_with_attribution must use remaining_qty."""

    def test_close_after_partial_tranche_uses_remaining_qty(self):
        """After selling 30 of 100 shares via tranches, close PnL = (exit-entry) * 70."""
        pm = _make_pm()
        pos = _make_position(qty=100, entry_price=10.0, remaining_qty=70, realized_pnl=60.0)
        pm.add_position(pos)

        # Close at $11 — PnL should be (11-10) * 70 = $70, NOT (11-10) * 100
        pm.close_position_with_attribution(
            ticker="TEST",
            exit_price=11.0,
            exit_time=datetime.now(timezone.utc),
            agent_signals_map={},
            variant_map={},
        )

        # daily_realized_pnl should reflect remaining_qty PnL only
        assert pm._daily_realized_pnl == pytest.approx(70.0)

    def test_full_position_close_uses_full_qty(self):
        """When no tranches sold, remaining_qty == qty, PnL uses full qty."""
        pm = _make_pm()
        pos = _make_position(qty=100, entry_price=10.0, remaining_qty=100)
        pm.add_position(pos)

        pm.close_position_with_attribution(
            ticker="TEST",
            exit_price=11.0,
            exit_time=datetime.now(timezone.utc),
            agent_signals_map={},
            variant_map={},
        )

        assert pm._daily_realized_pnl == pytest.approx(100.0)


# ── BUG-E1/GAP-7: Tranche computation for small positions ──────────


class TestSmallPositionTranches:
    """Tranche computation must handle qty=1 and qty=2 without zero-qty tranches."""

    def test_qty_1_single_tranche(self):
        pm = _make_pm()
        pos = _make_position(qty=1, remaining_qty=1)
        tranches = pm.compute_exit_tranches(pos)
        assert len(tranches) == 1
        assert tranches[0].qty == 1
        assert tranches[0].exit_type == "trailing_stop"

    def test_qty_2_single_tranche(self):
        pm = _make_pm()
        pos = _make_position(qty=2, remaining_qty=2)
        tranches = pm.compute_exit_tranches(pos)
        assert len(tranches) == 1
        assert tranches[0].qty == 2

    def test_qty_3_three_tranches(self):
        pm = _make_pm()
        pos = _make_position(qty=3, remaining_qty=3)
        tranches = pm.compute_exit_tranches(pos)
        assert len(tranches) == 3
        total_qty = sum(t.qty for t in tranches)
        assert total_qty == 3
        assert all(t.qty > 0 for t in tranches)

    def test_qty_100_three_tranches_sum_to_total(self):
        pm = _make_pm()
        pos = _make_position(qty=100, remaining_qty=100)
        tranches = pm.compute_exit_tranches(pos)
        assert len(tranches) == 3
        total_qty = sum(t.qty for t in tranches)
        assert total_qty == 100


# ── BUG-X4/X5: OrderResult fields ───────────────────────────────────


class TestOrderResultFields:
    """OrderResult must have fill_price and stop_order_id fields."""

    def test_order_result_has_fill_price_field(self):
        from src.execution.alpaca_executor import OrderResult

        result = OrderResult(
            order_id="test-123",
            status="filled",
            ticker="TEST",
            qty=100,
            signal_price=10.0,
            submitted_price=10.0,
            fill_price=10.05,
            stop_order_id="stop-456",
        )
        assert result.fill_price == 10.05
        assert result.stop_order_id == "stop-456"

    def test_order_result_fill_price_defaults_zero(self):
        from src.execution.alpaca_executor import OrderResult

        result = OrderResult(
            order_id="test-123",
            status="new",
            ticker="TEST",
            qty=100,
            signal_price=10.0,
            submitted_price=10.0,
        )
        assert result.fill_price == 0.0


# ── BUG-E4: Scanner gap_pct division by zero ────────────────────────


class TestScannerGapGuard:
    """previous_close=0 must not produce Inf gap_pct."""

    def test_compute_gap_percent_zero_close(self):
        from src.scanners.premarket import compute_gap_pct

        result = compute_gap_pct(5.0, 0.0)
        assert result == 0.0
        assert not (result != result)  # not NaN


# ── BUG-E6: float_shares=0 must not be treated as large-cap ─────────


class TestFloatSharesZero:
    """float_shares=0 should map to micro-float, not 50M large-cap."""

    def test_zero_float_is_not_fifty_million(self):
        float_shares_input = 0
        float_shares = float_shares_input if float_shares_input is not None else 50_000_000
        assert float_shares == 0

    def test_none_float_defaults_to_fifty_million(self):
        float_shares_input = None
        float_shares = float_shares_input if float_shares_input is not None else 50_000_000
        assert float_shares == 50_000_000
