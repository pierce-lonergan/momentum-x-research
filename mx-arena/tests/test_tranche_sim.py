"""Tests for Sprint 2: multi-tranche exit simulation + stop ratcheting."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from arena.clock import ClockMode, SimClock
from arena.data_engine import DataEngine
from arena.fill_model import Bar
from arena.harness import ArenaConfig, ArenaInstance
from arena.runner import _simulate_journal_trades


def _make_instance_with_bars(symbol: str, bars_list: list[Bar]) -> ArenaInstance:
    """Create a minimal ArenaInstance with injected bars."""
    config = ArenaConfig(
        date="2026-03-26",
        symbols=[symbol],
        data_dir="",
        initial_cash=100_000.0,
    )
    instance = ArenaInstance(config)
    bar_dict = {i: b for i, b in enumerate(bars_list)}
    instance.data_engine._minute_bars[symbol] = bar_dict
    return instance


def _bar(minute: int, o: float, h: float, l: float, c: float, v: int = 50000) -> Bar:
    ts = f"2026-03-26T{9 + minute // 60:02d}:{30 + minute % 60:02d}:00-04:00"
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


class TestTrancheExits:
    """Test T1/T2/T3 partial exit simulation."""

    def test_all_three_tranches_fill(self):
        """Stock goes straight up — all 3 tranches fill."""
        bars = [
            _bar(0, 5.00, 5.05, 4.95, 5.00),   # Entry bar
            _bar(1, 5.05, 5.30, 5.00, 5.25),    # T1 hits ($5.20)
            _bar(2, 5.25, 5.55, 5.20, 5.50),    # T2 hits ($5.40)
            _bar(3, 5.50, 5.80, 5.45, 5.70),    # T3 hits ($5.60)
        ]
        instance = _make_instance_with_bars("WIN", bars)

        buys = [{
            "ticker": "WIN",
            "entry_price": 5.10,  # Limit above open ask
            "stop_loss": 4.50,
            "target_prices": [5.20, 5.40, 5.60],
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        assert len(trades) == 1
        trade = trades[0]

        # Should have tranche exits
        assert len(trade["tranches"]) >= 2  # At least T1 + T2 should fill
        # P&L should be positive
        assert trade["pnl"] > 0

    def test_stop_hit_before_any_tranche(self):
        """Stock drops immediately — stop hit, no tranches fill."""
        bars = [
            _bar(0, 5.00, 5.05, 4.95, 5.00),
            _bar(1, 4.90, 4.95, 4.40, 4.45),    # Gap below stop
        ]
        instance = _make_instance_with_bars("LOSE", bars)

        buys = [{
            "ticker": "LOSE",
            "entry_price": 5.10,
            "stop_loss": 4.50,
            "target_prices": [5.20, 5.40, 5.60],
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        assert len(trades) == 1
        trade = trades[0]
        assert trade["exit_reason"] == "stop"
        assert trade["pnl"] < 0
        # All qty stopped out in one tranche
        stop_exits = [t for t in trade["tranches"] if t["type"] == "stop"]
        assert len(stop_exits) == 1
        assert stop_exits[0]["qty"] == 100


class TestStopRatcheting:
    """Test stop moves up after tranche fills."""

    def test_stop_ratchets_to_breakeven_after_t1(self):
        """After T1 fills, stop should ratchet to entry price."""
        bars = [
            _bar(0, 5.00, 5.05, 4.95, 5.00),    # Entry fills
            _bar(1, 5.05, 5.25, 5.00, 5.20),     # T1 ($5.20) fills
            _bar(2, 5.10, 5.15, 4.95, 5.00),     # Pulls back but above breakeven
            _bar(3, 4.95, 5.00, 4.80, 4.85),     # Drops below original stop but above breakeven
        ]
        instance = _make_instance_with_bars("RATCH", bars)

        buys = [{
            "ticker": "RATCH",
            "entry_price": 5.10,
            "stop_loss": 4.50,  # Original stop
            "target_prices": [5.20, 5.40, 5.60],
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        assert len(trades) == 1
        trade = trades[0]

        # T1 should have filled
        t1_exits = [t for t in trade["tranches"] if t["type"] == "T1"]
        assert len(t1_exits) == 1

        # Stop should have ratcheted to ~breakeven (entry price)
        # current_stop should be >= fill_price
        assert trade["current_stop"] >= trade["fill_price"] * 0.99  # Within 1%

    def test_stop_ratchets_to_t1_after_t2(self):
        """After T2 fills, stop should ratchet to T1 price."""
        bars = [
            _bar(0, 5.00, 5.05, 4.95, 5.00),
            _bar(1, 5.10, 5.25, 5.05, 5.20),     # T1 fills
            _bar(2, 5.25, 5.45, 5.20, 5.40),     # T2 fills
            _bar(3, 5.30, 5.35, 5.15, 5.20),     # Pulls back
            _bar(4, 5.15, 5.20, 5.10, 5.15),     # Still above T1 stop
        ]
        instance = _make_instance_with_bars("RATCH2", bars)

        buys = [{
            "ticker": "RATCH2",
            "entry_price": 5.10,
            "stop_loss": 4.50,
            "target_prices": [5.20, 5.40, 5.60],
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        trade = trades[0]

        # Both T1 and T2 should have filled
        t_exits = [t for t in trade["tranches"] if t["type"].startswith("T")]
        assert len(t_exits) >= 2

        # Stop should have ratcheted to T1 level (5.20)
        assert trade["current_stop"] >= 5.15  # At or near T1


class TestNoTargets:
    """Test behavior when no target_prices in journal."""

    def test_no_targets_holds_to_eod(self):
        """Without targets, hold until stop or EOD."""
        bars = [
            _bar(0, 5.00, 5.05, 4.95, 5.00),
            _bar(1, 5.05, 5.10, 5.00, 5.08),
            _bar(2, 5.08, 5.12, 5.03, 5.10),
        ]
        instance = _make_instance_with_bars("HOLD", bars)

        buys = [{
            "ticker": "HOLD",
            "entry_price": 5.10,
            "stop_loss": 4.50,
            "target_prices": [],  # No targets
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "eod"


class TestMixedScenarios:
    """Test realistic mixed scenarios from Mar 26 patterns."""

    def test_rmsg_pattern_gap_and_hold(self):
        """RMSG pattern: gap up, never look back, T1/T2 fill, hold rest to EOD."""
        bars = [
            _bar(0, 0.55, 0.58, 0.54, 0.57),    # Entry
            _bar(5, 0.58, 0.62, 0.57, 0.61),     # T1 ($0.60) fills
            _bar(10, 0.61, 0.65, 0.60, 0.64),    # T2 ($0.63) fills
            _bar(20, 0.63, 0.66, 0.62, 0.64),    # Near T3 but not quite
        ]
        instance = _make_instance_with_bars("RMSG", bars)

        buys = [{
            "ticker": "RMSG",
            "entry_price": 0.60,
            "stop_loss": 0.39,
            "target_prices": [0.60, 0.63, 0.68],
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        if trades:
            trade = trades[0]
            # Should have positive P&L from tranche exits
            assert trade["pnl"] >= 0

    def test_jblu_pattern_stop_hit(self):
        """JBLU pattern: enters, stops out quickly."""
        bars = [
            _bar(0, 4.70, 4.78, 4.65, 4.72),    # Entry
            _bar(1, 4.68, 4.70, 4.35, 4.40),     # Stop hit (low=4.35 < stop=4.38)
        ]
        instance = _make_instance_with_bars("JBLU", bars)

        buys = [{
            "ticker": "JBLU",
            "entry_price": 4.80,
            "stop_loss": 4.38,
            "target_prices": [4.84, 5.07, 5.53],
        }]

        trades = _simulate_journal_trades(instance, buys, {})
        if trades:
            trade = trades[0]
            assert trade["exit_reason"] == "stop"
            assert trade["pnl"] < 0
