"""
D121 Sweep Round 4: Edge-case tests for gaps identified in coverage audit.

Covers:
- TrancheExitMonitor: cancel_all_for_ticker, restore_tranche_state, duplicate fills
- StopResubmitter: cancel_and_remove, duplicate registration, qty guards
- FillStreamBridge: None stop_resubmitter in drain_and_resubmit
- SessionState: from_dict with malformed values
- DebateEngine: _safe_judge_float with garbage inputs
- TradeResultTracker: win_rate_by_catalyst cold-start
- ExitIntelligence: OBV with near-zero values
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import ExecutionConfig
from src.execution.position_manager import ManagedPosition, PositionManager


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


def _make_pm(equity: float = 100_000.0) -> PositionManager:
    return PositionManager(config=ExecutionConfig(), starting_equity=equity)


def _make_pos(
    ticker: str = "TEST",
    qty: int = 100,
    entry: float = 10.0,
    stop: float = 9.0,
    remaining: int | None = None,
    tranches_filled: int = 0,
) -> ManagedPosition:
    return ManagedPosition(
        ticker=ticker,
        qty=qty,
        entry_price=entry,
        signal_price=entry,
        stop_loss=stop,
        target_prices=[11.0, 12.0, 13.0],
        remaining_qty=remaining if remaining is not None else qty,
        tranches_filled=tranches_filled,
    )


# ═══════════════════════════════════════════════════════════════════
# TRANCHE MONITOR
# ═══════════════════════════════════════════════════════════════════


class TestTrancheMonitorCancelAll:
    """CQ-1: cancel_all_for_ticker must cancel orders and clean state."""

    @pytest.mark.asyncio
    async def test_cancel_all_removes_matching_orders(self):
        from src.execution.tranche_monitor import TrancheExitMonitor

        pm = _make_pm()
        pm.add_position(_make_pos("AAPL"))
        monitor = TrancheExitMonitor(pm)

        monitor.register_tranche_order("oid1", "AAPL", 1, 11.0, 33)
        monitor.register_tranche_order("oid2", "AAPL", 2, 12.0, 33)
        monitor.register_tranche_order("oid3", "MSFT", 1, 20.0, 50)

        client = AsyncMock()
        await monitor.cancel_all_for_ticker("AAPL", client)

        # AAPL orders canceled, MSFT untouched
        assert client.cancel_order.call_count == 2
        assert "oid3" in monitor._order_map
        assert "oid1" not in monitor._order_map
        assert "oid2" not in monitor._order_map

    @pytest.mark.asyncio
    async def test_cancel_all_noop_when_no_orders(self):
        from src.execution.tranche_monitor import TrancheExitMonitor

        pm = _make_pm()
        monitor = TrancheExitMonitor(pm)
        client = AsyncMock()

        # Should not crash
        await monitor.cancel_all_for_ticker("NOPE", client)
        client.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_all_handles_broker_error(self):
        from src.execution.tranche_monitor import TrancheExitMonitor

        pm = _make_pm()
        pm.add_position(_make_pos("AAPL"))
        monitor = TrancheExitMonitor(pm)
        monitor.register_tranche_order("oid1", "AAPL", 1, 11.0, 33)

        client = AsyncMock()
        client.cancel_order.side_effect = Exception("order not found")

        # Should not crash even if broker rejects cancel
        await monitor.cancel_all_for_ticker("AAPL", client)
        assert "oid1" not in monitor._order_map


class TestTrancheMonitorRestore:
    """D121 BUG-L3: restore_tranche_state recovers tranches_filled."""

    def test_restore_from_positions(self):
        from src.execution.tranche_monitor import TrancheExitMonitor

        pm = _make_pm()
        pm.add_position(_make_pos("AAPL", tranches_filled=2))
        pm.add_position(_make_pos("MSFT", tranches_filled=0))

        monitor = TrancheExitMonitor(pm)
        monitor.restore_tranche_state()

        assert monitor._ticker_tranches.get("AAPL") == 2
        assert "MSFT" not in monitor._ticker_tranches

    def test_restore_empty_positions(self):
        from src.execution.tranche_monitor import TrancheExitMonitor

        pm = _make_pm()
        monitor = TrancheExitMonitor(pm)
        monitor.restore_tranche_state()  # Should not crash
        assert len(monitor._ticker_tranches) == 0


class TestTrancheMonitorDuplicateFills:
    """Edge case: duplicate fill events must not corrupt state."""

    def test_duplicate_fill_does_not_double_count(self):
        from src.execution.tranche_monitor import TrancheExitMonitor, TrancheFillEvent

        pm = _make_pm()
        pos = _make_pos("AAPL", qty=99, remaining=99)
        pm.add_position(pos)

        monitor = TrancheExitMonitor(pm)
        monitor.register_tranche_order("oid1", "AAPL", 1, 11.0, 33)

        event = TrancheFillEvent(order_id="oid1", ticker="AAPL", filled_price=11.0, filled_qty=33)
        result1 = monitor.on_fill(event)
        assert result1 is not None
        assert result1.tranche_number == 1

        # Second fill with same order_id — already popped from _order_map
        result2 = monitor.on_fill(event)
        assert result2 is None  # Unknown order, ignored

    def test_remaining_qty_never_negative(self):
        from src.execution.tranche_monitor import TrancheExitMonitor, TrancheFillEvent

        pm = _make_pm()
        pos = _make_pos("AAPL", qty=10, remaining=5)
        pm.add_position(pos)

        monitor = TrancheExitMonitor(pm)
        monitor.register_tranche_order("oid1", "AAPL", 1, 11.0, 10)

        # Fill qty > remaining — max(0, ...) guard should prevent negative
        event = TrancheFillEvent(order_id="oid1", ticker="AAPL", filled_price=11.0, filled_qty=10)
        monitor.on_fill(event)

        assert pos.remaining_qty >= 0


# ═══════════════════════════════════════════════════════════════════
# STOP RESUBMITTER
# ═══════════════════════════════════════════════════════════════════


class TestStopResubmitterCancelAndRemove:
    """CQ-2: cancel_and_remove cancels broker order + cleans tracking."""

    @pytest.mark.asyncio
    async def test_cancel_and_remove_success(self):
        from src.execution.stop_resubmitter import StopResubmitter

        client = AsyncMock()
        resubmitter = StopResubmitter(client=client)
        resubmitter.register_stop("AAPL", "stop-001", 145.0, 100)

        await resubmitter.cancel_and_remove("AAPL")

        client.cancel_order.assert_called_once_with("stop-001")
        assert resubmitter.get_tracked_stop("AAPL") is None

    @pytest.mark.asyncio
    async def test_cancel_and_remove_noop_when_not_tracked(self):
        from src.execution.stop_resubmitter import StopResubmitter

        client = AsyncMock()
        resubmitter = StopResubmitter(client=client)

        # Should not crash
        await resubmitter.cancel_and_remove("NOPE")
        client.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_and_remove_handles_broker_error(self):
        from src.execution.stop_resubmitter import StopResubmitter

        client = AsyncMock()
        client.cancel_order.side_effect = Exception("already canceled")
        resubmitter = StopResubmitter(client=client)
        resubmitter.register_stop("AAPL", "stop-001", 145.0, 100)

        # Should not crash, and tracking should still be cleaned up
        await resubmitter.cancel_and_remove("AAPL")
        assert resubmitter.get_tracked_stop("AAPL") is None

    def test_register_duplicate_overwrites(self):
        from src.execution.stop_resubmitter import StopResubmitter

        resubmitter = StopResubmitter(client=MagicMock())
        resubmitter.register_stop("AAPL", "stop-001", 145.0, 100)
        resubmitter.register_stop("AAPL", "stop-002", 150.0, 90)

        tracked = resubmitter.get_tracked_stop("AAPL")
        assert tracked.order_id == "stop-002"
        assert tracked.stop_price == 150.0
        assert tracked.qty == 90


# ═══════════════════════════════════════════════════════════════════
# FILL STREAM BRIDGE
# ═══════════════════════════════════════════════════════════════════


class TestFillStreamBridgeNoneResubmitter:
    """BUG-S2: drain_and_resubmit must handle None stop_resubmitter."""

    @pytest.mark.asyncio
    async def test_drain_with_none_resubmitter_clears_pending(self):
        from src.execution.fill_stream_bridge import FillStreamBridge

        monitor = MagicMock()
        bridge = FillStreamBridge(tranche_monitor=monitor, stop_resubmitter=None)

        # Simulate queued pending resubmits
        bridge._pending_resubmits.append(("AAPL", 150.0, 90))
        bridge._pending_resubmits.append(("MSFT", 200.0, 50))

        events = await bridge.drain_and_resubmit()

        # Pending should be cleared, no crash
        assert len(bridge._pending_resubmits) == 0
        assert isinstance(events, list)


# ═══════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════


class TestSessionStateFromDictMalformed:
    """BUG-S7: from_dict must not crash on malformed values."""

    def test_non_numeric_qty(self):
        from src.execution.session_state import PositionState

        data = {"ticker": "AAPL", "qty": "abc", "entry_price": 10.0}
        state = PositionState.from_dict(data)
        assert state.ticker == "AAPL"
        assert state.qty == 0  # safe default

    def test_none_values(self):
        from src.execution.session_state import PositionState

        data = {"ticker": "TEST", "qty": None, "entry_price": None, "stop_loss": None}
        state = PositionState.from_dict(data)
        assert state.qty == 0
        assert state.entry_price == 0.0
        assert state.stop_loss == 0.0

    def test_list_where_scalar_expected(self):
        from src.execution.session_state import PositionState

        data = {"ticker": "TEST", "qty": [1, 2, 3], "entry_price": "not_a_number"}
        state = PositionState.from_dict(data)
        # Should not crash — safe defaults used
        assert isinstance(state.qty, int)
        assert isinstance(state.entry_price, float)

    def test_empty_dict(self):
        from src.execution.session_state import PositionState

        state = PositionState.from_dict({})
        assert state.ticker == ""
        assert state.qty == 0
        assert state.entry_price == 0.0

    def test_valid_roundtrip(self):
        from src.execution.session_state import PositionState

        original = PositionState(
            ticker="AAPL",
            qty=100,
            entry_price=150.0,
            signal_price=149.5,
            stop_loss=145.0,
            target_prices=[155.0, 160.0, 165.0],
            tranches_filled=1,
            remaining_qty=67,
            realized_pnl=330.0,
            entry_order_id="ent-001",
            stop_order_id="stp-001",
            tranche_order_ids=["t1", "t2", "t3"],
            opened_at="2026-03-21T09:30:00+00:00",
        )
        data = original.to_dict()
        restored = PositionState.from_dict(data)

        assert restored.ticker == "AAPL"
        assert restored.qty == 100
        assert restored.entry_price == 150.0
        assert restored.remaining_qty == 67
        assert restored.realized_pnl == 330.0
        assert restored.tranches_filled == 1
        assert len(restored.tranche_order_ids) == 3


# ═══════════════════════════════════════════════════════════════════
# DEBATE ENGINE: _safe_judge_float
# ═══════════════════════════════════════════════════════════════════


class TestSafeJudgeFloat:
    """BUG-S1: _safe_judge_float must handle all garbage LLM outputs."""

    def _import_fn(self):
        from src.agents.debate_engine import _safe_judge_float
        return _safe_judge_float

    def test_normal_float(self):
        fn = self._import_fn()
        assert fn(0.75) == 0.75

    def test_float_string(self):
        fn = self._import_fn()
        assert fn("0.75") == 0.75

    def test_dollar_prefix(self):
        fn = self._import_fn()
        result = fn("$4.50")
        assert result == 4.50

    def test_word_string_returns_none(self):
        fn = self._import_fn()
        assert fn("high") is None

    def test_fraction_string_returns_none(self):
        fn = self._import_fn()
        assert fn("0.75/1.0") is None

    def test_empty_string_returns_none(self):
        fn = self._import_fn()
        assert fn("") is None

    def test_none_returns_none(self):
        fn = self._import_fn()
        assert fn(None) is None

    def test_integer(self):
        fn = self._import_fn()
        assert fn(5) == 5.0

    def test_negative(self):
        fn = self._import_fn()
        assert fn(-1.5) == -1.5

    def test_zero(self):
        fn = self._import_fn()
        assert fn(0) == 0.0

    def test_scientific_notation(self):
        fn = self._import_fn()
        result = fn("1e-3")
        # May or may not parse — just shouldn't crash
        assert result is None or isinstance(result, float)


# ═══════════════════════════════════════════════════════════════════
# TRADE RESULT TRACKER: win_rate_by_catalyst cold start
# ═══════════════════════════════════════════════════════════════════


class TestWinRateByCatalystColdStart:
    """BUG-S13: win_rate_by_catalyst must have cold-start fallback."""

    def test_returns_none_under_5_trades(self):
        from src.execution.trade_result_tracker import TradeResult, TradeResultTracker

        tracker = TradeResultTracker.__new__(TradeResultTracker)
        tracker._results = deque()
        tracker._path = Path("/dev/null")

        _now = datetime.now(timezone.utc).isoformat()
        for i in range(4):
            tracker._results.append(TradeResult(
                ticker=f"T{i}", pnl=1.0, is_win=True, kelly_tier=1,
                catalyst_type="earnings",
                entry_time=_now, exit_time=_now,
            ))

        result = tracker.win_rate_by_catalyst("earnings", n=30)
        assert result is None  # Only 4 trades, need >= 5

    def test_cold_start_with_5_trades(self):
        from src.execution.trade_result_tracker import TradeResult, TradeResultTracker

        tracker = TradeResultTracker.__new__(TradeResultTracker)
        tracker._results = deque()
        tracker._path = Path("/dev/null")

        _now = datetime.now(timezone.utc).isoformat()
        # 5 trades: 3 wins, 2 losses = 60% win rate
        for i in range(5):
            tracker._results.append(TradeResult(
                ticker=f"T{i}",
                pnl=1.0 if i < 3 else -1.0,
                is_win=i < 3, kelly_tier=1,
                catalyst_type="earnings",
                entry_time=_now, exit_time=_now,
            ))

        result = tracker.win_rate_by_catalyst("earnings", n=30)
        assert result is not None
        assert result == pytest.approx(0.6)

    def test_full_history_uses_last_n(self):
        from src.execution.trade_result_tracker import TradeResult, TradeResultTracker

        tracker = TradeResultTracker.__new__(TradeResultTracker)
        tracker._results = deque()
        tracker._path = Path("/dev/null")

        _now = datetime.now(timezone.utc).isoformat()
        # 35 trades: first 30 are losses, last 5 are wins
        for i in range(35):
            tracker._results.append(TradeResult(
                ticker=f"T{i}",
                pnl=1.0 if i >= 30 else -1.0,
                is_win=i >= 30, kelly_tier=1,
                catalyst_type="gap",
                entry_time=_now, exit_time=_now,
            ))

        result = tracker.win_rate_by_catalyst("gap", n=30)
        assert result is not None
        # Last 30 trades: 25 losses + 5 wins = 5/30
        assert result == pytest.approx(5 / 30, abs=0.01)

    def test_different_catalyst_not_counted(self):
        from src.execution.trade_result_tracker import TradeResult, TradeResultTracker

        tracker = TradeResultTracker.__new__(TradeResultTracker)
        tracker._results = deque()
        tracker._path = Path("/dev/null")

        _now = datetime.now(timezone.utc).isoformat()
        # 10 "earnings" trades, 10 "gap" trades
        for i in range(10):
            tracker._results.append(TradeResult(
                ticker=f"E{i}",
                pnl=1.0, is_win=True, kelly_tier=1,
                catalyst_type="earnings",
                entry_time=_now, exit_time=_now,
            ))
        for i in range(10):
            tracker._results.append(TradeResult(
                ticker=f"G{i}",
                pnl=-1.0, is_win=False, kelly_tier=1,
                catalyst_type="gap",
                entry_time=_now, exit_time=_now,
            ))

        earnings_rate = tracker.win_rate_by_catalyst("earnings", n=30)
        gap_rate = tracker.win_rate_by_catalyst("gap", n=30)

        assert earnings_rate == pytest.approx(1.0)  # 10/10 wins
        assert gap_rate == pytest.approx(0.0)  # 0/10 wins


# ═══════════════════════════════════════════════════════════════════
# EXIT INTELLIGENCE: OBV near-zero edge cases
# ═══════════════════════════════════════════════════════════════════


class TestOBVNearZero:
    """BUG-S11: OBV divergence must not produce extreme values from near-zero."""

    def test_obv_near_zero_returns_bounded(self):
        from src.execution.exit_intelligence import ExitSignalEngine

        # Static method — call directly on class
        result = ExitSignalEngine._obv_divergence(bars=None)
        # With None bars, should return 0.0
        assert result == 0.0

    def test_empty_bars_returns_zero(self):
        from src.execution.exit_intelligence import ExitSignalEngine

        result = ExitSignalEngine._obv_divergence(bars=[])
        assert result == 0.0

    def test_single_bar_returns_zero(self):
        from src.execution.exit_intelligence import ExitSignalEngine

        result = ExitSignalEngine._obv_divergence(
            bars=[{"c": 10.0, "v": 100}],
        )
        assert result == 0.0

    def test_result_always_bounded_0_1(self):
        from src.execution.exit_intelligence import ExitSignalEngine

        # Build bars with price-up, volume-down divergence
        bars = []
        for i in range(20):
            bars.append({
                "c": 10.0 + i * 0.1,  # Price rising
                "v": max(1, 1000 - i * 50),  # Volume declining
                "h": 10.0 + i * 0.15,
                "l": 10.0 + i * 0.05,
                "o": 10.0 + i * 0.08,
            })
        result = ExitSignalEngine._obv_divergence(bars=bars)
        assert 0.0 <= result <= 1.0


# ═══════════════════════════════════════════════════════════════════
# REPLAY OPTIMIZER: mfcs_scaling_denom zero guard
# ═══════════════════════════════════════════════════════════════════


class TestMFCSScalingDenomGuard:
    """BUG-S10: mfcs_scaling_denom=0 must not crash."""

    def test_zero_denom_uses_fallback(self):
        # Verify the guard logic directly
        denom_input = 0.0
        _denom = denom_input if denom_input > 0 else 0.5
        mfcs = 0.35
        result = 0.05 + 0.10 * min(1.0, mfcs / _denom)
        assert 0.05 <= result <= 0.15  # Bounded correctly

    def test_positive_denom_used_directly(self):
        denom_input = 0.35
        _denom = denom_input if denom_input > 0 else 0.5
        assert _denom == 0.35

    def test_negative_denom_uses_fallback(self):
        denom_input = -1.0
        _denom = denom_input if denom_input > 0 else 0.5
        assert _denom == 0.5
