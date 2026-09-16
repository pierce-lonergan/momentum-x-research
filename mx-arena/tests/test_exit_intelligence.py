"""Tests for Sprint 3: exit intelligence strategies."""

from __future__ import annotations

import pytest

from arena.exit_intelligence import (
    velocity_exit,
    volume_exhaustion,
    gratitude_exit,
    alpha_decay_oracle,
    evaluate_exit,
    _interpolate_null,
)
from arena.fill_model import Bar


def _bars(closes: list[float], volumes: list[int] | None = None) -> list[Bar]:
    """Create bars from close prices."""
    if volumes is None:
        volumes = [50000] * len(closes)
    return [
        Bar(timestamp=f"2026-03-26T09:{30+i:02d}:00-04:00",
            open=c*0.999, high=c*1.002, low=c*0.998, close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


class TestVelocityExit:
    def test_positive_velocity_no_exit(self):
        bars = _bars([10.0, 10.1, 10.2, 10.3, 10.4])
        result = velocity_exit(bars, entry_price=10.0, minutes_held=3)
        assert not result.should_exit

    def test_negative_velocity_in_thrust_exits(self):
        bars = _bars([10.0, 10.1, 10.0, 9.8, 9.6])
        result = velocity_exit(bars, entry_price=10.0, minutes_held=10)
        assert result.should_exit or result.should_tighten

    def test_negative_velocity_in_ignition_only_tightens(self):
        bars = _bars([10.0, 9.9, 9.8])
        result = velocity_exit(bars, entry_price=10.0, minutes_held=3)
        # In IGNITION phase, negative velocity should tighten not exit
        assert result.should_tighten
        assert not result.should_exit

    def test_too_few_bars_no_signal(self):
        bars = _bars([10.0, 10.1])
        result = velocity_exit(bars, entry_price=10.0, minutes_held=2)
        assert not result.should_exit and not result.should_tighten


class TestVolumeExhaustion:
    def test_strong_volume_no_exit(self):
        bars = _bars([10.0]*5, [50000]*5)
        result = volume_exhaustion(bars, entry_volume=50000)
        assert not result.should_exit

    def test_volume_fade_triggers_exit(self):
        bars = _bars([10.0]*5, [5000]*5)  # 10% of entry volume
        result = volume_exhaustion(bars, entry_volume=50000)
        assert result.should_exit

    def test_moderate_fade_tightens(self):
        bars = _bars([10.0]*5, [12000]*5)  # 24% of entry volume
        result = volume_exhaustion(bars, entry_volume=50000)
        assert result.should_tighten

    def test_declining_trend_boosts_confidence(self):
        # 6 bars: first 3 moderate volume, last 3 sharply declining
        bars = _bars([10.0]*6, [15000, 14000, 13000, 5000, 4000, 3000])
        result = volume_exhaustion(bars, entry_volume=50000)
        # 5-bar avg ~7800 / 50000 = 0.156 -> below TIGHTEN_RATIO
        assert result.should_tighten or result.should_exit
        assert result.confidence > 0


class TestGratitudeExit:
    def test_profitable_below_threshold_no_exit(self):
        # 1R profit at 5 min, threshold = 3R - 0.05*5 = 2.75R
        result = gratitude_exit(
            entry_price=10.0, current_price=10.50, stop_loss=9.50,
            minutes_held=5,
        )
        # 1R < 2.75R: not at threshold yet
        assert not result.should_exit

    def test_high_r_multiple_triggers_exit(self):
        # Entry 10, stop 9.50, price 12.00: unrealized_r = 2/0.5 = 4R
        # At 30 min: threshold = max(0.75, 3.0 - 0.05*30) = 1.5R
        # 4R >= 1.5 * 1.5 = 2.25R -> EXIT
        result = gratitude_exit(
            entry_price=10.0, current_price=12.0, stop_loss=9.50,
            minutes_held=30,
        )
        assert result.should_exit

    def test_threshold_decays_over_time(self):
        # Same R-multiple, but much later -> lower threshold
        result_early = gratitude_exit(10.0, 11.0, 9.50, minutes_held=5)
        result_late = gratitude_exit(10.0, 11.0, 9.50, minutes_held=40)
        # Late should be more likely to trigger
        assert result_late.confidence >= result_early.confidence


class TestAlphaDecayOracle:
    def test_outperforming_null_no_exit(self):
        # At 10 min, null expects +2.0%. Actual +5% -> alpha = +3%
        result = alpha_decay_oracle(
            entry_price=10.0, current_price=10.5,
            minutes_since_open=10,
        )
        assert not result.should_exit

    def test_underperforming_null_exits(self):
        # At 10 min, null expects +2.0%. Actual -1% -> alpha = -3%
        result = alpha_decay_oracle(
            entry_price=10.0, current_price=9.9,
            minutes_since_open=10,
        )
        assert result.should_exit

    def test_too_early_no_signal(self):
        result = alpha_decay_oracle(10.0, 9.9, minutes_since_open=3)
        assert not result.should_exit

    def test_null_curve_interpolation(self):
        # At minute 7.5, should interpolate between (5, 3.0) and (10, 2.0)
        val = _interpolate_null(7.5)
        assert 2.0 < val < 3.0

    def test_durability_shift(self):
        # Positive shift makes oracle more lenient (expects returns longer)
        result_no_shift = alpha_decay_oracle(10.0, 10.1, 30)
        result_shifted = alpha_decay_oracle(10.0, 10.1, 30, durability_shift=15)
        # Shifted should have higher alpha (null curve read at 45 min: lower expected)
        assert result_shifted.details["alpha"] >= result_no_shift.details["alpha"]


class TestEvaluateExitAggregation:
    def test_hold_when_no_signals(self):
        bars = _bars([10.0, 10.05, 10.1, 10.15, 10.2])
        action, conf, results = evaluate_exit(
            bars, entry_price=10.0, current_price=10.2,
            stop_loss=9.5, entry_volume=50000,
            minutes_held=5, minutes_since_open=5,
        )
        # With positive momentum and volume, should hold
        assert action == "HOLD" or action == "TIGHTEN"

    def test_exit_on_multiple_strategy_agreement(self):
        # Declining price, declining volume, past alpha decay
        bars = _bars(
            [10.0, 9.9, 9.8, 9.7, 9.6, 9.5],
            [50000, 40000, 20000, 5000, 3000, 2000],
        )
        action, conf, results = evaluate_exit(
            bars, entry_price=10.0, current_price=9.5,
            stop_loss=9.0, entry_volume=50000,
            minutes_held=30, minutes_since_open=30,
        )
        # Multiple strategies should fire
        exits = [r for r in results if r.should_exit]
        assert len(exits) >= 1  # At least velocity or volume


class TestPortfolioConstraints:
    """Test max_positions and evaluation delay in sweep."""

    def test_max_positions_limits_trades(self):
        """With max_positions=2, only first 2 candidates should trade."""
        from arena.runner import _simulate_journal_trades
        from arena.harness import ArenaConfig, ArenaInstance
        from arena.fill_model import Bar as FillBar

        config = ArenaConfig(date="2026-03-26", symbols=["A", "B", "C"], data_dir="")
        instance = ArenaInstance(config)

        # Inject simple bars for 3 symbols
        for sym in ["A", "B", "C"]:
            instance.data_engine._minute_bars[sym] = {
                i: FillBar(f"2026-03-26T09:{30+i:02d}:00-04:00", 5.0, 5.1, 4.9, 5.0, 50000)
                for i in range(60)
            }

        buys = [
            {"ticker": "A", "entry_price": 5.10, "stop_loss": 4.50,
             "target_prices": [], "gap_pct": 0.20, "rvol": 5.0},
            {"ticker": "B", "entry_price": 5.10, "stop_loss": 4.50,
             "target_prices": [], "gap_pct": 0.15, "rvol": 4.0},
            {"ticker": "C", "entry_price": 5.10, "stop_loss": 4.50,
             "target_prices": [], "gap_pct": 0.10, "rvol": 3.0},
        ]

        trades = _simulate_journal_trades(instance, buys, {"max_positions": 2})
        assert len(trades) <= 2
