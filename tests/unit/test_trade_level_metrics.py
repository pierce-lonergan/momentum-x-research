"""
Tests for TradeLevelMetrics and compute_trade_level_metrics() (D68).

S038 WS5: Backtest trade-level performance metrics.
"""

from __future__ import annotations

import pytest

from src.core.backtest_metrics import (
    TradeLevelMetrics,
    compute_trade_level_metrics,
    compute_metrics_from_journal,
)


class TestComputeTradeLevelMetrics:
    """Tests for compute_trade_level_metrics()."""

    def test_empty_list_returns_zeros(self):
        m = compute_trade_level_metrics([])
        assert m.total_trades == 0
        assert m.win_rate_pct == 0.0
        assert m.profit_factor == 0.0
        assert m.max_drawdown_pct == 0.0
        assert m.max_consecutive_losses == 0
        assert m.expectancy == 0.0

    def test_all_winners(self):
        m = compute_trade_level_metrics([100.0, 200.0, 50.0])
        assert m.total_trades == 3
        assert m.win_rate_pct == 100.0
        assert m.max_consecutive_losses == 0
        assert m.largest_winner == 200.0
        assert m.largest_loser == 50.0  # min of all positive
        assert m.max_drawdown_pct == 0.0  # No drawdown if all wins

    def test_all_losers(self):
        m = compute_trade_level_metrics([-100.0, -50.0, -200.0])
        assert m.total_trades == 3
        assert m.win_rate_pct == 0.0
        assert m.max_consecutive_losses == 3
        assert m.largest_loser == -200.0
        assert m.profit_factor == 0.0  # 0 / loss

    def test_mixed_trades(self):
        pnls = [100.0, -50.0, 200.0, -30.0, 150.0]
        m = compute_trade_level_metrics(pnls)

        assert m.total_trades == 5
        assert m.win_rate_pct == 60.0  # 3/5
        assert m.largest_winner == 200.0
        assert m.largest_loser == -50.0

        # Profit factor: 450 / 80 = 5.625, rounded to 5.62
        assert m.profit_factor == 5.62

        # Max consecutive losses: 1 (losses alternate with wins)
        assert m.max_consecutive_losses == 1

    def test_consecutive_losses_streak(self):
        pnls = [100.0, -10.0, -20.0, -30.0, 50.0, -5.0, -15.0]
        m = compute_trade_level_metrics(pnls)

        assert m.max_consecutive_losses == 3  # -10, -20, -30

    def test_max_drawdown(self):
        # equity: 100, 50, 250, 220, 370
        # peak:   100, 100, 250, 250, 370
        # drawdown at step 2: (100-50)/100 = 50%
        # drawdown at step 4: (250-220)/250 = 12%
        pnls = [100.0, -50.0, 200.0, -30.0, 150.0]
        m = compute_trade_level_metrics(pnls)

        assert m.max_drawdown_pct == 50.0

    def test_expectancy(self):
        pnls = [100.0, -50.0, 200.0, -50.0]
        m = compute_trade_level_metrics(pnls)

        # win_rate = 0.5, avg_winner = 150, avg_loser = 50
        # expectancy = 150 * 0.5 - 50 * 0.5 = 50
        assert m.expectancy == 50.0

    def test_with_durations(self):
        pnls = [100.0, -50.0]
        durations = [30.0, 15.0]
        m = compute_trade_level_metrics(pnls, trade_durations_min=durations)

        assert m.avg_trade_duration_min == 22.5

    def test_without_durations(self):
        pnls = [100.0, -50.0]
        m = compute_trade_level_metrics(pnls)
        assert m.avg_trade_duration_min == 0.0

    def test_single_trade_win(self):
        m = compute_trade_level_metrics([500.0])
        assert m.total_trades == 1
        assert m.win_rate_pct == 100.0
        assert m.max_consecutive_losses == 0
        assert m.largest_winner == 500.0

    def test_single_trade_loss(self):
        m = compute_trade_level_metrics([-200.0])
        assert m.total_trades == 1
        assert m.win_rate_pct == 0.0
        assert m.max_consecutive_losses == 1
        assert m.largest_loser == -200.0

    def test_zero_pnl_counts_as_loss(self):
        m = compute_trade_level_metrics([0.0])
        assert m.win_rate_pct == 0.0
        assert m.max_consecutive_losses == 1


class TestComputeMetricsFromJournal:
    """Tests for compute_metrics_from_journal()."""

    def test_empty_entries(self):
        m = compute_metrics_from_journal([])
        assert m.total_trades == 0

    def test_with_mock_entries(self):
        class MockEntry:
            def __init__(self, action, realized_pnl, hold_duration_minutes=None):
                self.action = action
                self.realized_pnl = realized_pnl
                self.hold_duration_minutes = hold_duration_minutes

        entries = [
            MockEntry("BUY", 100.0, 30.0),
            MockEntry("BUY", -50.0, 15.0),
            MockEntry("BUY", None, None),  # Open position, no close
            MockEntry("NO_TRADE", None, None),  # Skipped
        ]

        m = compute_metrics_from_journal(entries)
        assert m.total_trades == 2  # Only closed BUY entries
        assert m.win_rate_pct == 50.0
        assert m.avg_trade_duration_min == 22.5

    def test_no_closed_trades(self):
        class MockEntry:
            def __init__(self):
                self.action = "BUY"
                self.realized_pnl = None
                self.hold_duration_minutes = None

        entries = [MockEntry(), MockEntry()]
        m = compute_metrics_from_journal(entries)
        assert m.total_trades == 0
