"""
MOMENTUM-X Tests: Restart Recovery

Node ID: tests.unit.test_recovery
Graph Link: tested_by → execution.position_manager, execution.session_state

Tests D64 enhanced restart recovery:
- sync_from_state_and_orders merges session state with Alpaca data
- Fallback to D56 when no state file
- Orphaned state positions ignored
- Tranche/stop order matching
"""

from __future__ import annotations

import pytest
from datetime import date

from config.settings import ExecutionConfig
from src.execution.position_manager import PositionManager
from src.execution.session_state import PositionState, SessionState


@pytest.fixture
def exec_config() -> ExecutionConfig:
    return ExecutionConfig()


@pytest.fixture
def position_manager(exec_config: ExecutionConfig) -> PositionManager:
    return PositionManager(config=exec_config, starting_equity=100_000.0)


def _make_session_state(**positions_kwargs) -> SessionState:
    """Helper to create a SessionState with positions."""
    positions = {}
    for ticker, kwargs in positions_kwargs.items():
        positions[ticker] = PositionState(ticker=ticker, **kwargs)
    return SessionState(
        session_date=date.today().isoformat(),
        daily_realized_pnl=0.0,
        positions=positions,
    )


class TestSyncFromStateAndOrders:
    """Test the enhanced D64 recovery method."""

    def test_enhanced_recovery_with_state(self, position_manager: PositionManager):
        """State + positions + orders → full reconstruction."""
        state = _make_session_state(
            BOOM={
                "qty": 500,
                "entry_price": 8.50,
                "signal_price": 8.45,
                "stop_loss": 7.90,
                "target_prices": [9.35, 10.20, 11.05],
                "tranches_filled": 1,
                "remaining_qty": 334,
                "realized_pnl": 140.0,
                "stop_order_id": "stop-001",
                "tranche_order_ids": ["t1", "t2", "t3"],
            },
        )
        alpaca_positions = [
            {
                "symbol": "BOOM",
                "side": "long",
                "qty": "334",
                "avg_entry_price": "8.50",
                "current_price": "9.10",
            },
        ]
        alpaca_orders = [
            {"id": "stop-001", "symbol": "BOOM", "side": "sell", "type": "stop", "stop_price": "7.90", "qty": "334"},
            {"id": "t2", "symbol": "BOOM", "side": "sell", "type": "limit", "limit_price": "10.20", "qty": "167"},
            {"id": "t3", "symbol": "BOOM", "side": "sell", "type": "limit", "limit_price": "11.05", "qty": "167"},
        ]

        synced = position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=alpaca_positions,
            alpaca_orders=alpaca_orders,
        )

        assert synced == 1
        assert position_manager.has_position("BOOM")
        pos = position_manager._positions["BOOM"]
        assert pos.tranches_filled == 1
        assert pos.remaining_qty == 334
        assert pos.stop_loss == 7.90
        assert pos.signal_price == 8.45
        assert pos.target_prices == [9.35, 10.20, 11.05]
        assert pos.stop_order_id == "stop-001"

    def test_fallback_to_d56_when_no_state(self, position_manager: PositionManager):
        """Missing state entry → D56 estimation from config."""
        state = _make_session_state()  # Empty positions

        alpaca_positions = [
            {
                "symbol": "AAPL",
                "side": "long",
                "qty": "100",
                "avg_entry_price": "150.00",
            },
        ]

        synced = position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=alpaca_positions,
            alpaca_orders=[],
        )

        assert synced == 1
        pos = position_manager._positions["AAPL"]
        # D56 fallback: targets estimated from config
        assert pos.tranches_filled == 0
        assert pos.signal_price == 150.0  # Uses entry as proxy
        assert len(pos.target_prices) == 3
        assert pos.stop_order_id == ""

    def test_orphaned_state_position_ignored(self, position_manager: PositionManager):
        """State has position that Alpaca doesn't → skipped."""
        state = _make_session_state(
            GHOST={
                "qty": 500,
                "entry_price": 10.0,
                "stop_order_id": "stop-ghost",
            },
        )

        # Alpaca has no positions
        synced = position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=[],
            alpaca_orders=[],
        )

        assert synced == 0
        assert not position_manager.has_position("GHOST")

    def test_daily_pnl_restored(self, position_manager: PositionManager):
        """Daily P&L from state file → circuit breaker continuity."""
        state = SessionState(
            session_date=date.today().isoformat(),
            daily_realized_pnl=-8500.0,
        )

        synced = position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=[],
            alpaca_orders=[],
        )

        assert synced == 0
        assert position_manager._daily_realized_pnl == -8500.0

    def test_circuit_breaker_active_after_recovery(self, position_manager: PositionManager):
        """If daily P&L exceeded threshold, circuit breaker should be active."""
        state = SessionState(
            session_date=date.today().isoformat(),
            daily_realized_pnl=-11000.0,  # > -10% of $100k
        )

        position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=[],
            alpaca_orders=[],
        )

        assert position_manager.is_circuit_breaker_active

    def test_multiple_positions_recovered(self, position_manager: PositionManager):
        """Multiple positions all recovered correctly."""
        state = _make_session_state(
            BOOM={"qty": 500, "entry_price": 8.50, "tranches_filled": 0},
            RITR={"qty": 300, "entry_price": 1.06, "tranches_filled": 2},
        )
        alpaca_positions = [
            {"symbol": "BOOM", "side": "long", "qty": "500", "avg_entry_price": "8.50"},
            {"symbol": "RITR", "side": "long", "qty": "100", "avg_entry_price": "1.06"},
        ]

        synced = position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=alpaca_positions,
            alpaca_orders=[],
        )

        assert synced == 2
        assert position_manager.has_position("BOOM")
        assert position_manager.has_position("RITR")
        assert position_manager._positions["BOOM"].tranches_filled == 0
        assert position_manager._positions["RITR"].tranches_filled == 2

    def test_short_positions_skipped(self, position_manager: PositionManager):
        """Short positions from Alpaca are ignored (we only trade long)."""
        state = _make_session_state()
        alpaca_positions = [
            {"symbol": "SHORT", "side": "short", "qty": "100", "avg_entry_price": "50.00"},
        ]

        synced = position_manager.sync_from_state_and_orders(
            session_state=state,
            alpaca_positions=alpaca_positions,
            alpaca_orders=[],
        )

        assert synced == 0

    def test_none_session_state_falls_back(self, position_manager: PositionManager):
        """None session_state → D56 estimation for all positions."""
        alpaca_positions = [
            {"symbol": "AAPL", "side": "long", "qty": "50", "avg_entry_price": "200.00"},
        ]

        synced = position_manager.sync_from_state_and_orders(
            session_state=None,
            alpaca_positions=alpaca_positions,
            alpaca_orders=[],
        )

        # Should still recover (with D56 fallback)
        assert synced == 1
        pos = position_manager._positions["AAPL"]
        assert pos.tranches_filled == 0
        assert pos.signal_price == 200.0


class TestGetOrders:
    """Test the get_orders() API method exists and has correct signature."""

    def test_get_orders_method_exists(self):
        from src.data.alpaca_client import AlpacaDataClient
        assert hasattr(AlpacaDataClient, "get_orders")

    def test_get_orders_is_async(self):
        import asyncio
        from src.data.alpaca_client import AlpacaDataClient
        method = getattr(AlpacaDataClient, "get_orders")
        assert asyncio.iscoroutinefunction(method)


class TestOrderResultStopOrderId:
    """Test that OrderResult exposes stop_order_id."""

    def test_stop_order_id_field_exists(self):
        from src.execution.alpaca_executor import OrderResult
        result = OrderResult(
            order_id="entry-001",
            status="accepted",
            ticker="BOOM",
            qty=500,
            signal_price=8.50,
            submitted_price=8.50,
            stop_order_id="stop-001",
        )
        assert result.stop_order_id == "stop-001"

    def test_stop_order_id_defaults_empty(self):
        from src.execution.alpaca_executor import OrderResult
        result = OrderResult(
            order_id="entry-001",
            status="accepted",
            ticker="BOOM",
            qty=500,
            signal_price=8.50,
            submitted_price=8.50,
        )
        assert result.stop_order_id == ""
