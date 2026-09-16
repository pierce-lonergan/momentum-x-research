"""
MOMENTUM-X Tests: Alpaca Executor

Node ID: tests.unit.test_executor
Graph Link: tested_by → execution.alpaca_executor

TDD: These tests are written BEFORE the implementation.
All tests use mocked Alpaca API calls.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.core.models import TradeVerdict, DebateResult
from config.settings import ExecutionConfig


class TestAlpacaExecutor:
    """Test TradeVerdict → Alpaca order conversion."""

    @pytest.fixture
    def exec_config(self) -> ExecutionConfig:
        # doc 286: pin the marketable-limit knobs explicitly so the
        # worst-fill cap re-anchor (D286) is deterministic regardless of
        # EXEC_MARKETABLE_* env overrides.
        return ExecutionConfig(
            paper_aggressive_mode=False,
            marketable_limit_enabled=True,
            marketable_base_offset_pct=0.004,
            marketable_max_offset_pct=0.015,
            marketable_mfcs_full_at=0.55,
        )

    @pytest.mark.asyncio
    async def test_buy_verdict_submits_oto_order(self, exec_config):
        """D57-fix: A BUY verdict should submit an OTO order (buy limit + stop sell).

        OTO ensures the stop leg only activates after the buy fills,
        preventing Alpaca's 403 "potential wash trade detected" error.
        """
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="BOOM",
            action="STRONG_BUY",
            confidence=0.85,
            mfcs=0.78,
            entry_price=8.50,
            stop_loss=7.90,
            target_prices=[9.35, 10.20, 11.05],
            position_size_pct=0.05,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        mock_client.get_account.return_value = {"equity": "100000.00"}
        mock_client.submit_oto_order.return_value = {
            "id": "oto-001",
            "status": "accepted",
            "symbol": "BOOM",
            "order_class": "oto",
            "legs": [
                {
                    "id": "stop-leg-001",
                    "side": "sell",
                    "type": "stop",
                    "stop_price": "7.90",
                    "status": "held",  # Held until buy fills
                },
            ],
        }

        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        assert result.order_id == "oto-001"
        assert result.status == "accepted"
        assert result.qty > 0
        assert result.order_type == "oto"
        # D64: Verify stop_order_id is extracted from OTO legs
        assert result.stop_order_id == "stop-leg-001"
        # doc 286 (stale-anchor cap): the 5% cap is taken against the WORST
        # legal fill — the D190 marketable limit 8.50*(1+0.015)=8.6275 (mfcs
        # 0.78 >= full_at) — not the frozen eval price. $5000 / 8.6275 = 579
        # shares (pre-doc-286 this was 588 = $5000 / 8.50, which could fill
        # ABOVE the 5% cap when the limit chased).
        assert result.qty == 579
        # Verify OTO order was submitted (not separate limit + stop)
        mock_client.submit_oto_order.assert_called_once()
        mock_client.submit_limit_order.assert_not_called()
        mock_client.submit_stop_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trade_verdict_skipped(self, exec_config):
        """A NO_TRADE verdict should NOT submit any order."""
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="SKIP",
            action="NO_TRADE",
            confidence=0.0,
            mfcs=0.3,
            entry_price=5.0,
            stop_loss=4.5,
            target_prices=[],
            position_size_pct=0.0,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        assert result is None
        mock_client.submit_bracket_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_position_limit_enforced(self, exec_config):
        """Should refuse to execute if max concurrent positions reached."""
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="NOPE",
            action="BUY",
            confidence=0.7,
            mfcs=0.65,
            entry_price=10.0,
            stop_loss=9.3,
            target_prices=[11.0],
            position_size_pct=0.03,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        # Return 8 existing positions (at max — D37 increased from 3 to 8)
        mock_client.get_positions.return_value = [
            {"symbol": f"POS{i}"} for i in range(8)
        ]

        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        assert result is None  # Refused

    @pytest.mark.asyncio
    async def test_zero_size_verdict_skipped(self, exec_config):
        """Zero position_size_pct should not submit order."""
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="ZERO",
            action="BUY",
            confidence=0.5,
            mfcs=0.5,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[11.0],
            position_size_pct=0.0,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        assert result is None

    @pytest.mark.asyncio
    async def test_slippage_tracking_recorded(self, exec_config):
        """OrderResult should track signal_price for slippage analysis."""
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="SLIP",
            action="BUY",
            confidence=0.8,
            mfcs=0.7,
            entry_price=12.0,
            stop_loss=11.0,
            target_prices=[13.2, 14.4],
            position_size_pct=0.03,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        mock_client.get_account.return_value = {"equity": "50000.00"}
        mock_client.submit_oto_order.return_value = {
            "id": "oto-slip",
            "status": "accepted",
            "symbol": "SLIP",
            "order_class": "oto",
            "legs": [
                {"id": "stop-slip", "side": "sell", "type": "stop", "status": "held"},
            ],
        }

        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        assert result.signal_price == 12.0
        assert result.submitted_price == 12.0
        assert result.stop_order_id == "stop-slip"

    @pytest.mark.asyncio
    async def test_stop_order_id_empty_when_no_legs(self, exec_config):
        """D64: If OTO response has no legs, stop_order_id should be empty."""
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="NOLEG",
            action="BUY",
            confidence=0.8,
            mfcs=0.7,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[11.0],
            position_size_pct=0.03,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        mock_client.get_account.return_value = {"equity": "50000.00"}
        mock_client.submit_oto_order.return_value = {
            "id": "oto-noleg",
            "status": "accepted",
            "symbol": "NOLEG",
            "order_class": "oto",
            "legs": [],  # No legs returned
        }

        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        assert result is not None
        assert result.order_id == "oto-noleg"
        assert result.stop_order_id == ""  # No stop leg → empty

    @pytest.mark.asyncio
    async def test_oto_order_rejected_metrics_tracked(self, exec_config):
        """D65: OTO order rejection should increment rejection metrics."""
        from src.execution.alpaca_executor import AlpacaExecutor

        verdict = TradeVerdict(
            ticker="REJ",
            action="BUY",
            confidence=0.8,
            mfcs=0.7,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[11.0],
            position_size_pct=0.03,
            reasoning_summary="test",
        )

        mock_client = AsyncMock()
        mock_client.get_account.return_value = {"equity": "50000.00"}
        mock_client.submit_oto_order.return_value = {
            "id": "oto-rej",
            "status": "rejected",
            "symbol": "REJ",
            "order_class": "oto",
            "legs": [],
        }

        executor = AlpacaExecutor(config=exec_config, client=mock_client)
        result = await executor.execute(verdict)

        # Sweep fix: Rejected orders now return None to prevent phantom positions.
        # Bridge.execute_verdict() checks `if order_result is None` and skips
        # add_position(), preventing ghost entries in PositionManager.
        assert result is None


class TestPositionManager:
    """Test position lifecycle management."""

    @pytest.fixture
    def exec_config(self) -> ExecutionConfig:
        return ExecutionConfig()

    def test_circuit_breaker_triggers(self, exec_config):
        """Daily P&L < -5% should trigger circuit breaker."""
        from src.execution.position_manager import PositionManager

        pm = PositionManager(config=exec_config, starting_equity=100_000.0)
        pm.record_realized_pnl(-8000.0)  # -8%
        assert not pm.is_circuit_breaker_active

        pm.record_realized_pnl(-2500.0)  # Total: -10.5% (D37: threshold is now -10%)
        assert pm.is_circuit_breaker_active

    def test_circuit_breaker_blocks_new_entries(self, exec_config):
        """With circuit breaker active, can_enter_new_position must return False."""
        from src.execution.position_manager import PositionManager

        pm = PositionManager(config=exec_config, starting_equity=100_000.0)
        assert pm.can_enter_new_position()

        pm.record_realized_pnl(-11000.0)  # -11% > -10% threshold (D37)
        assert not pm.can_enter_new_position()

    def test_scaled_exit_targets_generated(self, exec_config):
        """Three-tranche scaled exit should produce 3 exit levels."""
        from src.execution.position_manager import PositionManager, ManagedPosition

        pm = PositionManager(config=exec_config, starting_equity=100_000.0)
        pos = ManagedPosition(
            ticker="BOOM",
            qty=300,
            entry_price=10.0,
            signal_price=10.0,
            stop_loss=9.30,
            target_prices=[11.0, 12.0, 13.0],
            order_id="order-001",
        )
        tranches = pm.compute_exit_tranches(pos)

        assert len(tranches) == 3
        assert tranches[0].qty == 100  # 1/3
        assert tranches[0].target == 11.0
        assert tranches[1].qty == 100
        assert tranches[2].qty == 100

    def test_stop_moves_to_breakeven_after_first_tranche(self, exec_config):
        """After T1 fills, stop should move to entry price (breakeven)."""
        from src.execution.position_manager import PositionManager, ManagedPosition

        pm = PositionManager(config=exec_config, starting_equity=100_000.0)
        pos = ManagedPosition(
            ticker="BOOM",
            qty=300,
            entry_price=10.0,
            signal_price=10.0,
            stop_loss=9.30,
            target_prices=[11.0, 12.0, 13.0],
            order_id="order-001",
        )
        new_stop = pm.compute_stop_after_tranche(pos, tranche_filled=1)
        assert new_stop == 10.0  # Breakeven

    def test_stop_moves_to_t1_after_second_tranche(self, exec_config):
        """After T2 fills, stop should move to T1 target."""
        from src.execution.position_manager import PositionManager, ManagedPosition

        pm = PositionManager(config=exec_config, starting_equity=100_000.0)
        pos = ManagedPosition(
            ticker="BOOM",
            qty=300,
            entry_price=10.0,
            signal_price=10.0,
            stop_loss=9.30,
            target_prices=[11.0, 12.0, 13.0],
            order_id="order-001",
        )
        new_stop = pm.compute_stop_after_tranche(pos, tranche_filled=2)
        assert new_stop == 11.0  # T1 target


# ── D107 WS2: Orphaned Order Reconciliation ─────────────────────


class TestOrphanedOrderReconciliation:
    """D107: Tests for identify_orphaned_orders()."""

    @pytest.fixture
    def pm(self) -> "PositionManager":
        from src.execution.position_manager import PositionManager
        return PositionManager(config=ExecutionConfig(), starting_equity=100_000.0)

    def test_identify_orphans_no_tracked_positions(self, pm):
        """All orders are orphans when no positions are tracked."""
        orders = [
            {"id": "o1", "symbol": "AAPL", "status": "new", "side": "buy", "qty": "100"},
            {"id": "o2", "symbol": "TSLA", "status": "accepted", "side": "sell", "qty": "50"},
        ]
        orphans = pm.identify_orphaned_orders(orders)
        assert len(orphans) == 2

    def test_identify_orphans_all_matching(self, pm):
        """No orphans when all orders match tracked positions."""
        from src.execution.position_manager import ManagedPosition
        pm.add_position(ManagedPosition(
            ticker="AAPL", qty=100, entry_price=150.0,
            signal_price=150.0, stop_loss=145.0,
            target_prices=[155.0, 160.0, 165.0], order_id="ord1",
        ))
        orders = [
            {"id": "o1", "symbol": "AAPL", "status": "new", "side": "sell", "qty": "50"},
        ]
        orphans = pm.identify_orphaned_orders(orders)
        assert len(orphans) == 0

    def test_identify_orphans_mixed(self, pm):
        """Some orphans, some tracked — only untracked returned."""
        from src.execution.position_manager import ManagedPosition
        pm.add_position(ManagedPosition(
            ticker="AAPL", qty=100, entry_price=150.0,
            signal_price=150.0, stop_loss=145.0,
            target_prices=[155.0, 160.0, 165.0], order_id="ord1",
        ))
        orders = [
            {"id": "o1", "symbol": "AAPL", "status": "new", "side": "sell", "qty": "50"},
            {"id": "o2", "symbol": "TSLA", "status": "accepted", "side": "buy", "qty": "100"},
            {"id": "o3", "symbol": "NVDA", "status": "pending_new", "side": "buy", "qty": "75"},
        ]
        orphans = pm.identify_orphaned_orders(orders)
        assert len(orphans) == 2
        orphan_symbols = {o["symbol"] for o in orphans}
        assert orphan_symbols == {"TSLA", "NVDA"}

    def test_identify_orphans_ignores_filled(self, pm):
        """Filled and cancelled orders should be excluded."""
        orders = [
            {"id": "o1", "symbol": "AAPL", "status": "filled", "side": "buy", "qty": "100"},
            {"id": "o2", "symbol": "TSLA", "status": "cancelled", "side": "sell", "qty": "50"},
            {"id": "o3", "symbol": "NVDA", "status": "expired", "side": "buy", "qty": "25"},
        ]
        orphans = pm.identify_orphaned_orders(orders)
        assert len(orphans) == 0

    def test_identify_orphans_empty_list(self, pm):
        """Empty input returns empty output."""
        orphans = pm.identify_orphaned_orders([])
        assert orphans == []
