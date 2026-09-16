"""
D115: Tests for TradeResultTracker.
"""

import json
import pytest
from datetime import date
from pathlib import Path

from src.execution.trade_result_tracker import TradeResult, TradeResultTracker


@pytest.fixture
def tmp_jsonl(tmp_path):
    return tmp_path / "trade_results.jsonl"


class TestTradeResultTracker:
    def test_win_rate_calculation(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl, max_history=200)
        for i in range(30):
            tracker.record(TradeResult(
                ticker="TEST", catalyst_type="FDA_APPROVAL",
                entry_time="2026-03-16T09:30:00", exit_time="2026-03-16T10:00:00",
                pnl=100.0 if i < 12 else -50.0,  # 12 wins / 30 = 40%
                is_win=i < 12,
                session_date="2026-03-16",
            ))
        assert tracker.win_rate(n=30) == pytest.approx(0.40, abs=0.01)

    def test_insufficient_data_returns_none(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl)
        tracker.record(TradeResult(
            ticker="A", catalyst_type="NONE",
            entry_time="", exit_time="", pnl=10, is_win=True,
            session_date="2026-03-16",
        ))
        assert tracker.win_rate(n=30) is None

    def test_catalyst_win_rate(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl)
        # 30 FDA trades: 20 wins
        for i in range(30):
            tracker.record(TradeResult(
                ticker="FDA", catalyst_type="FDA_APPROVAL",
                entry_time="", exit_time="",
                pnl=100.0 if i < 20 else -50.0,
                is_win=i < 20,
                session_date="2026-03-16",
            ))
        # 5 earnings trades (< n=30 but >= 5 cold-start threshold)
        for i in range(5):
            tracker.record(TradeResult(
                ticker="EARN", catalyst_type="EARNINGS_BEAT",
                entry_time="", exit_time="", pnl=50.0, is_win=True,
                session_date="2026-03-16",
            ))
        assert tracker.win_rate_by_catalyst("FDA_APPROVAL", n=30) == pytest.approx(20 / 30)
        # D121 BUG-S13: Cold-start fallback returns 5/5 = 1.0 (was None)
        assert tracker.win_rate_by_catalyst("EARNINGS_BEAT", n=30) == pytest.approx(1.0)
        # Under 5 trades still returns None
        assert tracker.win_rate_by_catalyst("UNKNOWN_CATALYST", n=30) is None

    def test_persistence_across_sessions(self, tmp_jsonl):
        """Write results, create new tracker, verify loaded."""
        t1 = TradeResultTracker(history_file=tmp_jsonl)
        for i in range(5):
            t1.record(TradeResult(
                ticker=f"T{i}", catalyst_type="NONE",
                entry_time="", exit_time="",
                pnl=10.0, is_win=True, session_date="2026-03-16",
            ))
        # New tracker reads from same file
        t2 = TradeResultTracker(history_file=tmp_jsonl)
        assert t2.total_trades == 5

    def test_tier3_count_today(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl)
        today = date.today().isoformat()
        tracker.record(TradeResult(
            ticker="A", catalyst_type="FDA_APPROVAL",
            entry_time="", exit_time="",
            pnl=500.0, is_win=True, kelly_tier=3,
            session_date=today,
        ))
        tracker.record(TradeResult(
            ticker="B", catalyst_type="EARNINGS_BEAT",
            entry_time="", exit_time="",
            pnl=200.0, is_win=True, kelly_tier=2,
            session_date=today,
        ))
        assert tracker.tier3_plus_count_today() == 1

    def test_had_tier3_stop_today(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl)
        today = date.today().isoformat()
        tracker.record(TradeResult(
            ticker="A", catalyst_type="FDA_APPROVAL",
            entry_time="", exit_time="",
            pnl=-500.0, is_win=False, kelly_tier=3,
            session_date=today,
        ))
        assert tracker.had_tier3_stop_today() is True

    def test_had_tier3_stop_today_false(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl)
        today = date.today().isoformat()
        tracker.record(TradeResult(
            ticker="A", catalyst_type="FDA_APPROVAL",
            entry_time="", exit_time="",
            pnl=500.0, is_win=True, kelly_tier=3,
            session_date=today,
        ))
        assert tracker.had_tier3_stop_today() is False

    def test_max_history_deque(self, tmp_jsonl):
        tracker = TradeResultTracker(history_file=tmp_jsonl, max_history=10)
        for i in range(20):
            tracker.record(TradeResult(
                ticker=f"T{i}", catalyst_type="NONE",
                entry_time="", exit_time="",
                pnl=10.0, is_win=True, session_date="2026-03-16",
            ))
        # Deque should only hold last 10
        assert tracker.total_trades == 10
