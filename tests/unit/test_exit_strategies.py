"""
Tests for D109 Phase 4 + D110: Parallel Exit Strategies.

Covers all 6 independent exit strategy classes, the ContagionNetwork,
the ParallelExitEngine orchestrator, and SignalHistoryLogger extensions.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.execution.exit_strategies import (
    ExitStrategyResult,
    GratitudeExitStrategy,
    ParallelExitEngine,
    PullbackClassifier,
    PullbackState,
    PullbackTrackerState,
    VelocityEngine,
    VelocityPhase,
    VolumeExhaustionStrategy,
)


# ── Helpers ──────────────────────────────────────────────────────


def _bar(c: float, v: int = 1000, h: float | None = None, l: float | None = None) -> dict:
    """Create a minimal bar dict for testing."""
    return {
        "o": c,
        "h": h if h is not None else c + 0.05,
        "l": l if l is not None else c - 0.05,
        "c": c,
        "v": v,
    }


def _bars_rising(start: float, count: int, step: float = 0.10) -> list[dict]:
    """Create a sequence of rising bars."""
    return [_bar(start + i * step, v=1000 + i * 100) for i in range(count)]


def _bars_flat(price: float, count: int) -> list[dict]:
    """Create a sequence of flat bars at same price."""
    return [_bar(price) for _ in range(count)]


def _bars_declining(start: float, count: int, step: float = 0.10) -> list[dict]:
    """Create a sequence of declining bars."""
    return [_bar(start - i * step, v=1000 - i * 50) for i in range(count)]


# ── VelocityEngine Tests ────────────────────────────────────────


class TestVelocityEngine:
    """Test momentum velocity calculation and phase classification."""

    def setup_method(self) -> None:
        self.engine = VelocityEngine()

    def test_no_bars_returns_neutral(self) -> None:
        result = self.engine.evaluate(10.0, 5.0, None)
        assert not result.should_exit
        assert not result.should_tighten
        assert result.confidence == 0.0

    def test_insufficient_bars_returns_neutral(self) -> None:
        result = self.engine.evaluate(10.0, 5.0, [_bar(10.0), _bar(10.05)])
        assert not result.should_exit
        assert not result.should_tighten

    def test_time_based_phase_ignition(self) -> None:
        """Phase is IGNITION for minutes 0-5 regardless of velocity."""
        bars = _bars_declining(10.0, 5, step=0.5)  # Strong decline
        result = self.engine.evaluate(10.0, 2.0, bars)
        assert result.details["phase"] == "IGNITION"
        # During IGNITION, negative velocity only tightens (noise tolerance)
        assert result.should_tighten
        assert not result.should_exit

    def test_time_based_phase_thrust(self) -> None:
        """Phase is THRUST for minutes 5-15."""
        bars = _bars_declining(10.0, 5, step=0.3)
        result = self.engine.evaluate(10.0, 8.0, bars)
        assert result.details["phase"] == "THRUST"

    def test_time_based_phase_cruise(self) -> None:
        """Phase is CRUISE for minutes 15-30."""
        result = self.engine.evaluate(10.0, 20.0, _bars_flat(10.0, 5))
        assert result.details["phase"] == "CRUISE"

    def test_time_based_phase_decay(self) -> None:
        """Phase is DECAY for 30+ minutes."""
        result = self.engine.evaluate(10.0, 35.0, _bars_flat(10.0, 5))
        assert result.details["phase"] == "DECAY"

    def test_cannot_be_decay_at_minute_3(self) -> None:
        """A 3-minute-old position is ALWAYS IGNITION, never DECAY."""
        # Even with zero velocity, phase is time-based
        bars = _bars_flat(10.0, 5)
        result = self.engine.evaluate(10.0, 3.0, bars)
        assert result.details["phase"] == "IGNITION"
        assert result.details["phase"] != "DECAY"

    def test_positive_velocity_above_threshold_no_signal(self) -> None:
        """Strong positive velocity → no tighten or exit."""
        # Bars rising 0.5% per bar on a $10 entry = 0.5%/min
        bars = [_bar(10.0), _bar(10.0), _bar(10.0), _bar(10.05), _bar(10.15)]
        result = self.engine.evaluate(10.0, 8.0, bars)  # THRUST phase
        # velocity = (10.15 - 10.0) / (10.0 * 3) = 0.005/min — above THRUST threshold
        assert not result.should_exit
        assert not result.should_tighten

    def test_negative_velocity_in_thrust_triggers_exit(self) -> None:
        """Negative velocity outside IGNITION → should_exit."""
        bars = [_bar(10.0), _bar(10.0), _bar(10.0), _bar(9.95), _bar(9.85)]
        result = self.engine.evaluate(10.0, 8.0, bars)  # THRUST phase
        assert result.should_exit
        assert result.details["phase"] == "THRUST"

    def test_velocity_calculation_accuracy(self) -> None:
        """Verify velocity math: (bars[-1].c - bars[-3].c) / (entry * 3)."""
        bars = [_bar(10.0), _bar(10.0), _bar(10.10), _bar(10.20), _bar(10.30)]
        result = self.engine.evaluate(10.0, 8.0, bars)
        # velocity = (10.30 - 10.10) / (10.0 * 3) = 0.00667/min
        expected = (10.30 - 10.10) / (10.0 * 3.0)
        assert abs(result.details["velocity_per_min"] - expected) < 1e-6


# ── PullbackClassifier Tests ────────────────────────────────────


class TestPullbackClassifier:
    """Test pullback state machine transitions with relative thresholds."""

    def setup_method(self) -> None:
        self.classifier = PullbackClassifier()

    def _tracker(self, peak: float = 10.0) -> PullbackTrackerState:
        return PullbackTrackerState(peak_since_entry=peak)

    def test_advancing_on_new_highs(self) -> None:
        """Price at peak → ADVANCING, no signal."""
        tracker = self._tracker(10.5)
        result = self.classifier.evaluate(10.0, 10.6, 10.5, tracker)
        assert tracker.current_state == PullbackState.ADVANCING
        assert not result.should_exit
        assert not result.should_tighten

    def test_transition_to_pullback_at_30pct_retracement(self) -> None:
        """Price drops 30% of advance → PULLBACK."""
        # Entry=10, peak=11 → advance=1.0. 30% retracement = $0.30 drop from peak
        tracker = self._tracker(11.0)
        result = self.classifier.evaluate(10.0, 10.65, 11.0, tracker)
        # retracement = (11.0 - 10.65) / (11.0 - 10.0) = 0.35 > 0.30
        assert tracker.current_state == PullbackState.PULLBACK
        assert result.should_tighten

    def test_small_pullback_stays_advancing(self) -> None:
        """Price drops <30% of advance → still ADVANCING."""
        tracker = self._tracker(11.0)
        result = self.classifier.evaluate(10.0, 10.85, 11.0, tracker)
        # retracement = (11.0 - 10.85) / 1.0 = 0.15 < 0.30
        assert tracker.current_state == PullbackState.ADVANCING
        assert not result.should_tighten

    def test_transition_to_recovered_at_90pct_peak(self) -> None:
        """Price recovers to 90% of peak → RECOVERED."""
        tracker = self._tracker(11.0)
        tracker.current_state = PullbackState.PULLBACK
        tracker.advance_size = 1.0
        tracker.cycles_in_pullback = 2

        result = self.classifier.evaluate(10.0, 10.95, 11.0, tracker)
        # 10.95 >= 11.0 * 0.90 = 9.9 → RECOVERED
        assert tracker.current_state == PullbackState.RECOVERED

    def test_transition_to_exhausted_by_depth(self) -> None:
        """Pullback exceeds 50% retracement → EXHAUSTED."""
        tracker = self._tracker(11.0)
        tracker.current_state = PullbackState.PULLBACK
        tracker.advance_size = 1.0
        tracker.cycles_in_pullback = 1

        result = self.classifier.evaluate(10.0, 10.45, 11.0, tracker)
        # retracement = (11.0 - 10.45) / 1.0 = 0.55 > 0.50
        assert tracker.current_state == PullbackState.EXHAUSTED
        assert result.should_exit
        assert result.confidence > 0.8

    def test_transition_to_exhausted_by_cycles(self) -> None:
        """5+ cycles in PULLBACK without recovery → EXHAUSTED."""
        tracker = self._tracker(11.0)
        tracker.current_state = PullbackState.PULLBACK
        tracker.advance_size = 1.0
        tracker.cycles_in_pullback = 5  # Will become 6 on next eval

        # Price still in pullback range but not exhausted by depth
        result = self.classifier.evaluate(10.0, 10.75, 11.0, tracker)
        # retracement = 0.25 < 0.50, but cycles = 6 > 5
        assert tracker.current_state == PullbackState.EXHAUSTED
        assert result.should_exit

    def test_recovered_to_advancing_on_new_high(self) -> None:
        """After recovery, new high → back to ADVANCING."""
        tracker = self._tracker(11.0)
        tracker.current_state = PullbackState.RECOVERED

        result = self.classifier.evaluate(10.0, 11.05, 11.0, tracker)
        assert tracker.current_state == PullbackState.ADVANCING

    def test_exhausted_is_terminal(self) -> None:
        """Once EXHAUSTED, stays EXHAUSTED with exit signal."""
        tracker = self._tracker(11.0)
        tracker.current_state = PullbackState.EXHAUSTED

        result = self.classifier.evaluate(10.0, 11.5, 11.0, tracker)
        # Even with price above peak, state stays EXHAUSTED
        assert tracker.current_state == PullbackState.EXHAUSTED
        assert result.should_exit

    def test_relative_threshold_small_advance(self) -> None:
        """Small advance ($0.10) → small absolute pullback to trigger."""
        # Entry=2.00, peak=2.10 → advance=0.10
        tracker = self._tracker(2.10)
        # 30% of $0.10 = $0.03 pullback → price $2.07
        result = self.classifier.evaluate(2.0, 2.065, 2.10, tracker)
        # retracement = 0.035 / 0.10 = 0.35 > 0.30
        assert tracker.current_state == PullbackState.PULLBACK

    def test_relative_threshold_large_advance(self) -> None:
        """Large advance ($5.00) → proportionally larger pullback needed."""
        # Entry=10, peak=15 → advance=5.0
        tracker = self._tracker(15.0)
        # 30% of $5.0 = $1.50 pullback needed → price $13.50
        result = self.classifier.evaluate(10.0, 14.0, 15.0, tracker)
        # retracement = 1.0 / 5.0 = 0.20 < 0.30 — NOT yet in pullback
        assert tracker.current_state == PullbackState.ADVANCING

    def test_state_persists_across_calls(self) -> None:
        """Same tracker across multiple evaluate calls."""
        # Use larger advance so pullback zone and recovery zone don't overlap
        # Entry=5, peak=10 → advance=5. 90% of peak = 9.0.
        # 30% retracement = 8.5, 50% retracement = 7.5
        # Pullback zone (between 30% and 50%): 7.5 < price < 8.5
        # Recovery = price >= 9.0 (above pullback zone)
        # So a price of 8.0 is in pullback (40% retracement, below 9.0)
        tracker = self._tracker(10.0)

        # Call 1: enter pullback (35% retracement)
        self.classifier.evaluate(5.0, 8.25, 10.0, tracker)
        assert tracker.current_state == PullbackState.PULLBACK

        # Call 2: still in pullback zone — 40% retracement, below 90% of peak (9.0)
        self.classifier.evaluate(5.0, 8.0, 10.0, tracker)
        assert tracker.current_state == PullbackState.PULLBACK
        assert tracker.cycles_in_pullback == 2


# ── VolumeExhaustionStrategy Tests ──────────────────────────────


class TestVolumeExhaustionStrategy:
    """Test volume ratio analysis."""

    def setup_method(self) -> None:
        self.strategy = VolumeExhaustionStrategy()

    def test_no_bars_returns_neutral(self) -> None:
        result = self.strategy.evaluate(1000, None)
        assert not result.should_exit
        assert not result.should_tighten

    def test_healthy_volume_no_signal(self) -> None:
        """Volume ratio >0.30 → no signal."""
        bars = [_bar(10.0, v=800) for _ in range(5)]
        result = self.strategy.evaluate(1000, bars)
        # ratio = 800/1000 = 0.80
        assert not result.should_exit
        assert not result.should_tighten
        assert result.details["volume_ratio"] == pytest.approx(0.8, abs=0.01)

    def test_moderate_fade_triggers_tighten(self) -> None:
        """Volume ratio 0.15-0.30 → tighten."""
        bars = [_bar(10.0, v=250) for _ in range(5)]
        result = self.strategy.evaluate(1000, bars)
        # ratio = 250/1000 = 0.25
        assert result.should_tighten
        assert not result.should_exit

    def test_severe_fade_triggers_exit(self) -> None:
        """Volume ratio <0.15 → exit."""
        bars = [_bar(10.0, v=100) for _ in range(5)]
        result = self.strategy.evaluate(1000, bars)
        # ratio = 100/1000 = 0.10
        assert result.should_exit

    def test_declining_volume_trend(self) -> None:
        """Declining volume trend boosts confidence."""
        # 6 bars: first 3 at 400, last 3 at 200
        bars = [_bar(10.0, v=400)] * 3 + [_bar(10.0, v=200)] * 3
        result = self.strategy.evaluate(1000, bars)
        # 5-bar avg = (400 + 200*4) / 5 = 1200/5 = 240; wait — it takes last 5 bars
        # bars[-5:] = [400, 200, 200, 200, 200] → avg = 240
        # ratio = 240/1000 = 0.24 → tighten
        assert result.should_tighten
        assert result.details["trend_declining"] is True
        # Confidence base from ratio + 0.15 trend boost
        assert result.confidence > 0.0


# ── GratitudeExitStrategy Tests ─────────────────────────────────


class TestGratitudeExitStrategy:
    """Test R-multiple profit capture with time-decaying threshold."""

    def setup_method(self) -> None:
        self.strategy = GratitudeExitStrategy()

    def test_early_position_needs_3r(self) -> None:
        """At minute 0, threshold is 3.0R — high bar."""
        # Entry=10, stop=9.5, 1R=$0.50, need 3R=$1.50, price must be $11.50
        result = self.strategy.evaluate(10.0, 9.5, 11.0, 0.0)
        # unrealized_r = 1.0/0.5 = 2.0 < 3.0 threshold
        assert not result.should_exit
        assert not result.should_tighten

    def test_minute_30_needs_1_5r(self) -> None:
        """At minute 30, threshold = max(0.75, 3.0 - 0.05*30) = 1.5R."""
        result = self.strategy.evaluate(10.0, 9.5, 10.80, 30.0)
        # unrealized_r = 0.80/0.50 = 1.6R ≥ 1.5R threshold → tighten
        assert result.should_tighten
        assert result.details["threshold"] == pytest.approx(1.5, abs=0.01)

    def test_floor_at_075r(self) -> None:
        """After 60+ minutes, threshold floors at 0.75R."""
        result = self.strategy.evaluate(10.0, 9.5, 10.0, 60.0)
        # max(0.75, 3.0 - 0.05*60) = max(0.75, 0.0) = 0.75
        assert result.details["threshold"] == pytest.approx(0.75, abs=0.01)

    def test_zero_risk_unit_returns_neutral(self) -> None:
        """Stop at or above entry → 1R=0 → neutral."""
        result = self.strategy.evaluate(10.0, 10.0, 10.5, 10.0)
        assert not result.should_exit
        assert not result.should_tighten

    def test_r_above_threshold_triggers_tighten(self) -> None:
        """Unrealized R at threshold → tighten."""
        # minute 45: threshold = max(0.75, 3.0 - 2.25) = 0.75R
        result = self.strategy.evaluate(10.0, 9.0, 10.80, 45.0)
        # 1R = $1.0, unrealized_r = 0.80/1.0 = 0.80 ≥ 0.75 → tighten
        assert result.should_tighten
        assert not result.should_exit

    def test_r_well_above_threshold_triggers_exit(self) -> None:
        """Unrealized R at 1.5× threshold → exit."""
        # minute 45: threshold = 0.75R, exit at 0.75 * 1.5 = 1.125R
        result = self.strategy.evaluate(10.0, 9.0, 11.20, 45.0)
        # unrealized_r = 1.20/1.0 = 1.2R ≥ 1.125 → exit
        assert result.should_exit

    def test_negative_pnl_no_signal(self) -> None:
        """Underwater position → no gratitude signal."""
        result = self.strategy.evaluate(10.0, 9.5, 9.8, 15.0)
        assert not result.should_exit
        assert not result.should_tighten


# ── ParallelExitEngine Tests ────────────────────────────────────


class TestParallelExitEngine:
    """Test the orchestrator that runs all 4 strategies."""

    def setup_method(self) -> None:
        self.engine = ParallelExitEngine()

    def test_evaluate_all_returns_six_results(self) -> None:
        """evaluate_all returns exactly 6 ExitStrategyResult (D109: 4 + D110: 2)."""
        bars = _bars_rising(10.0, 5)
        results = self.engine.evaluate_all(
            ticker="TEST", entry_price=10.0, stop_loss=9.5,
            current_price=10.5, peak_price=10.5,
            minutes_held=10.0, entry_volume=1000, bars=bars,
        )
        assert len(results) == 6
        names = {r.strategy_name for r in results}
        assert names == {
            "velocity", "pullback", "volume_exhaustion", "gratitude",
            "catalyst_half_life", "alpha_oracle",
        }

    def test_pullback_state_created_and_persists(self) -> None:
        """PullbackTrackerState is created on first call and persists."""
        bars = _bars_rising(10.0, 5)
        self.engine.evaluate_all(
            ticker="AAPL", entry_price=10.0, stop_loss=9.5,
            current_price=10.5, peak_price=10.5,
            minutes_held=5.0, entry_volume=1000, bars=bars,
        )
        assert "AAPL" in self.engine._pullback_state
        state1 = self.engine._pullback_state["AAPL"]

        # Second call uses same state
        self.engine.evaluate_all(
            ticker="AAPL", entry_price=10.0, stop_loss=9.5,
            current_price=10.3, peak_price=10.5,
            minutes_held=6.0, entry_volume=1000, bars=bars,
        )
        assert self.engine._pullback_state["AAPL"] is state1

    def test_cleanup_position_removes_state(self) -> None:
        """cleanup_position removes pullback state for the ticker."""
        bars = _bars_rising(10.0, 5)
        self.engine.evaluate_all(
            ticker="MSFT", entry_price=10.0, stop_loss=9.5,
            current_price=10.5, peak_price=10.5,
            minutes_held=5.0, entry_volume=1000, bars=bars,
        )
        assert "MSFT" in self.engine._pullback_state
        self.engine.cleanup_position("MSFT")
        assert "MSFT" not in self.engine._pullback_state

    def test_prune_stale_removes_closed_tickers(self) -> None:
        """prune_stale removes tickers not in active set."""
        self.engine._pullback_state["AAPL"] = PullbackTrackerState()
        self.engine._pullback_state["MSFT"] = PullbackTrackerState()
        self.engine._pullback_state["TSLA"] = PullbackTrackerState()

        self.engine.prune_stale({"AAPL"})
        assert "AAPL" in self.engine._pullback_state
        assert "MSFT" not in self.engine._pullback_state
        assert "TSLA" not in self.engine._pullback_state

    def test_to_log_dict_nests_correctly(self) -> None:
        """to_log_dict produces nested strategy blob + flat summary."""
        results = [
            ExitStrategyResult("velocity", should_exit=False, should_tighten=True,
                               confidence=0.7, details={"velocity_per_min": 0.001, "phase": "THRUST"}),
            ExitStrategyResult("pullback", should_exit=False, should_tighten=False,
                               confidence=0.0, details={"state": "ADVANCING", "retracement_pct": 0.12}),
            ExitStrategyResult("volume_exhaustion", should_exit=False, should_tighten=False,
                               confidence=0.0, details={"volume_ratio": 0.65}),
            ExitStrategyResult("gratitude", should_exit=True, should_tighten=False,
                               confidence=0.9, details={"unrealized_r": 2.5, "threshold": 1.5}),
        ]

        log = ParallelExitEngine.to_log_dict(results)

        # Nested blob
        assert "parallel_strategies" in log
        assert "velocity" in log["parallel_strategies"]
        assert log["parallel_strategies"]["velocity"]["tighten"] is True
        assert log["parallel_strategies"]["gratitude"]["exit"] is True

        # Flat summary
        assert log["anyof_would_exit"] is True  # gratitude said exit
        assert log["anyof_would_tighten"] is True  # velocity said tighten, gratitude said exit


# ── SignalHistoryLogger Extension Tests ──────────────────────────


class TestSignalHistoryLoggerExtended:
    """Test extended log_cycle with parallel strategy results."""

    def test_backward_compat_no_parallel(self, tmp_path: Path) -> None:
        """log_cycle without parallel results produces original format."""
        from src.execution.exit_intelligence import SignalHistoryLogger

        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        sig_logger = SignalHistoryLogger(signal_history_dir=str(sig_dir))

        mock_signal = MagicMock()
        mock_signal.composite_exit_urgency = 0.25
        mock_signal.recommendation = "HOLD"
        mock_signal.to_scores_dict.return_value = {
            "composite": 0.25, "volume_fade": 0.1, "vwap_deterioration": 0.2,
        }

        sig_logger.log_cycle(
            ticker="TEST", signal=mock_signal,
            current_price=10.5, entry_price=10.0, pnl_pct=5.0,
        )
        sig_logger.close()

        log_files = list(sig_dir.glob("signal_log_*.jsonl"))
        assert len(log_files) == 1
        with open(log_files[0]) as f:
            entry = json.loads(f.readline())

        assert entry["ticker"] == "TEST"
        assert entry["composite"] == 0.25
        # No parallel fields
        assert "parallel_strategies" not in entry
        assert "anyof_would_exit" not in entry

    def test_with_parallel_results_has_nested_blob(self, tmp_path: Path) -> None:
        """log_cycle with parallel results includes nested strategy blob."""
        from src.execution.exit_intelligence import SignalHistoryLogger

        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        sig_logger = SignalHistoryLogger(signal_history_dir=str(sig_dir))

        mock_signal = MagicMock()
        mock_signal.composite_exit_urgency = 0.15
        mock_signal.recommendation = "HOLD"
        mock_signal.to_scores_dict.return_value = {"volume_fade": 0.05}

        parallel = [
            ExitStrategyResult("velocity", details={"phase": "THRUST"}),
            ExitStrategyResult("pullback", should_tighten=True, details={"state": "PULLBACK"}),
            ExitStrategyResult("volume_exhaustion", details={"volume_ratio": 0.5}),
            ExitStrategyResult("gratitude", details={"unrealized_r": 1.2}),
        ]
        log_extras = ParallelExitEngine.to_log_dict(parallel)

        sig_logger.log_cycle(
            ticker="TEST", signal=mock_signal,
            current_price=10.5, entry_price=10.0, pnl_pct=5.0,
            parallel_log=log_extras,
            intraday_atr=0.15, mfe_pct=0.05, time_held_min=8.5,
        )
        sig_logger.close()

        log_files = list(sig_dir.glob("signal_log_*.jsonl"))
        with open(log_files[0]) as f:
            entry = json.loads(f.readline())

        # Nested blob present
        assert "parallel_strategies" in entry
        assert entry["parallel_strategies"]["pullback"]["tighten"] is True
        assert entry["parallel_strategies"]["velocity"]["phase"] == "THRUST"

        # Flat summary
        assert entry["anyof_would_exit"] is False
        assert entry["anyof_would_tighten"] is True

        # Derived metrics
        assert entry["intraday_atr"] == 0.15
        assert entry["mfe_pct"] == 0.05
        assert entry["time_held_min"] == 8.5

    def test_anyof_would_exit_true_when_strategy_fires(self, tmp_path: Path) -> None:
        """anyof_would_exit is True when any strategy says exit."""
        from src.execution.exit_intelligence import SignalHistoryLogger

        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        sig_logger = SignalHistoryLogger(signal_history_dir=str(sig_dir))

        mock_signal = MagicMock()
        mock_signal.composite_exit_urgency = 0.1
        mock_signal.recommendation = "HOLD"
        mock_signal.to_scores_dict.return_value = {}

        parallel = [
            ExitStrategyResult("velocity"),
            ExitStrategyResult("pullback", should_exit=True),  # EXIT!
            ExitStrategyResult("volume_exhaustion"),
            ExitStrategyResult("gratitude"),
        ]
        log_extras = ParallelExitEngine.to_log_dict(parallel)

        sig_logger.log_cycle(
            ticker="TEST", signal=mock_signal,
            current_price=10.5, entry_price=10.0, pnl_pct=5.0,
            parallel_log=log_extras,
        )
        sig_logger.close()

        log_files = list(sig_dir.glob("signal_log_*.jsonl"))
        with open(log_files[0]) as f:
            entry = json.loads(f.readline())

        assert entry["anyof_would_exit"] is True


# ── D110: CatalystHalfLifeStrategy Tests ─────────────────────────

from src.execution.exit_strategies import (  # noqa: E402
    AlphaDecayOracle,
    AlphaMeasurement,
    CatalystHalfLifeStrategy,
    ContagionNetwork,
    ContagionResult,
)


class TestCatalystHalfLifeStrategy:
    """Test catalyst-type-specific exit timing."""

    def setup_method(self) -> None:
        self.strategy = CatalystHalfLifeStrategy()

    def test_fda_approval_long_half_life(self) -> None:
        """FDA approval has 180-min half-life; no signal at minute 10."""
        result = self.strategy.evaluate(
            minutes_held=10.0, velocity_per_min=0.002,
            catalyst_type="fda_approval", manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert not result.should_exit
        assert not result.should_tighten
        assert result.details["half_life_min"] == 180.0

    def test_social_media_promo_short_half_life(self) -> None:
        """Social media pump has 8-min half-life; tighten by minute 7."""
        result = self.strategy.evaluate(
            minutes_held=7.0, velocity_per_min=0.001,
            catalyst_type="social_media_promo", manipulation_phase="ORGANIC_MOMENTUM",
        )
        # 0.8 × 8 = 6.4 → should tighten at minute 7
        assert result.should_tighten
        assert not result.should_exit

    def test_exit_requires_time_and_negative_velocity(self) -> None:
        """Exit fires ONLY when past deadline AND velocity ≤ 0."""
        # Past exit deadline (1.5 × 8 = 12) but velocity positive → NO exit
        result = self.strategy.evaluate(
            minutes_held=13.0, velocity_per_min=0.001,
            catalyst_type="social_media_promo", manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert not result.should_exit
        # Tighten fires (time-only, no velocity gate)
        assert result.should_tighten

    def test_exit_fires_when_past_deadline_and_negative_velocity(self) -> None:
        """Exit fires when time expired AND velocity ≤ 0."""
        result = self.strategy.evaluate(
            minutes_held=13.0, velocity_per_min=-0.001,
            catalyst_type="social_media_promo", manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert result.should_exit
        assert "shelf life expired" in result.reasoning.lower()

    def test_promotional_early_adjustment(self) -> None:
        """PROMOTIONAL_EARLY multiplies half-life by 0.6."""
        result = self.strategy.evaluate(
            minutes_held=5.0, velocity_per_min=0.001,
            catalyst_type="technical_breakout",  # median=25
            manipulation_phase="PROMOTIONAL_EARLY",  # 0.6× → 15
        )
        # Effective half-life = 25 × 0.6 = 15.0
        assert result.details["half_life_min"] == 15.0

    def test_promotional_late_adjustment(self) -> None:
        """PROMOTIONAL_LATE multiplies half-life by 0.3."""
        result = self.strategy.evaluate(
            minutes_held=5.0, velocity_per_min=0.001,
            catalyst_type="technical_breakout",  # median=25
            manipulation_phase="PROMOTIONAL_LATE",  # 0.3× → 7.5
        )
        assert result.details["half_life_min"] == 7.5

    def test_unknown_catalyst_defaults_to_20_min(self) -> None:
        """Unknown catalyst type uses 20-min median half-life."""
        result = self.strategy.evaluate(
            minutes_held=1.0, velocity_per_min=0.001,
            catalyst_type="unknown", manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert result.details["half_life_min"] == 20.0

    def test_tighten_at_80_pct_of_half_life(self) -> None:
        """Tighten fires at 80% of half-life (time-only, no velocity gate)."""
        # technical_breakout: half_life=25, tighten at 20 min
        result = self.strategy.evaluate(
            minutes_held=21.0, velocity_per_min=0.005,  # still positive
            catalyst_type="technical_breakout", manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert result.should_tighten
        assert not result.should_exit  # velocity positive, no exit

    def test_zero_velocity_triggers_exit_past_deadline(self) -> None:
        """Velocity exactly 0 also triggers exit past deadline."""
        result = self.strategy.evaluate(
            minutes_held=40.0, velocity_per_min=0.0,  # zero = flat
            catalyst_type="technical_breakout",  # exit deadline = 37.5
            manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert result.should_exit


# ── D110: ContagionNetwork Tests ─────────────────────────────────


class TestContagionNetwork:
    """Test cross-position signal propagation."""

    def setup_method(self) -> None:
        self.network = ContagionNetwork(decay_minutes=10.0, threshold=0.3)

    def _record_biotech_signal(self, ticker: str = "BIAF", confidence: float = 0.8) -> None:
        """Helper: record a biotech sector signal."""
        self.network.record_signal(
            ticker=ticker, sector="Biotech", catalyst_type="fda_approval",
            gap_pct=40.0, confidence=confidence, is_exit=False,
            timestamp=datetime.now(timezone.utc),
        )

    def test_same_sector_propagation(self) -> None:
        """Signal propagates to same-sector position."""
        self._record_biotech_signal("BIAF", confidence=0.8)
        result = self.network.propagate(
            target_ticker="ISPC", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=25.0,
        )
        # Same sector (+0.4), same catalyst (+0.2), similar gap (~2x) (+0.1) = 0.7
        # intensity = 0.8 × 0.7 × ~1.0 (time decay) ≈ 0.56
        assert result.intensity > 0.3
        assert result.should_tighten

    def test_different_sector_no_propagation(self) -> None:
        """Signal does NOT propagate to different sector with different catalyst."""
        self._record_biotech_signal("BIAF", confidence=0.8)
        result = self.network.propagate(
            target_ticker="RCAT", target_sector="Technology",
            target_catalyst="technical_breakout", target_gap_pct=12.0,
        )
        # Different sector (0), different catalyst (0), different gap range (0) = 0.0
        assert result.intensity < 0.3
        assert not result.should_tighten

    def test_time_decay_partial(self) -> None:
        """Signal at T, check at T+5 → partial decay."""
        past = datetime.now(timezone.utc)
        self.network.record_signal(
            ticker="BIAF", sector="Biotech", catalyst_type="fda_approval",
            gap_pct=40.0, confidence=0.8, is_exit=False, timestamp=past,
        )
        # Check at T+5 minutes (50% decay)
        from datetime import timedelta
        check_time = past + timedelta(minutes=5)
        result = self.network.propagate(
            target_ticker="ISPC", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=25.0,
            current_time=check_time,
        )
        # Decay at 5 min = 0.5 → intensity roughly halved vs T+0
        assert result.intensity > 0
        assert result.intensity < 0.5  # Should be less than full strength

    def test_time_decay_expired(self) -> None:
        """Signal fully decayed after decay window."""
        from datetime import timedelta
        past = datetime.now(timezone.utc) - timedelta(minutes=11)
        self.network.record_signal(
            ticker="BIAF", sector="Biotech", catalyst_type="fda_approval",
            gap_pct=40.0, confidence=0.8, is_exit=False, timestamp=past,
        )
        result = self.network.propagate(
            target_ticker="ISPC", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=25.0,
        )
        assert result.intensity == 0.0
        assert not result.should_tighten

    def test_threshold_filtering(self) -> None:
        """Low-intensity contagion below threshold doesn't trigger tighten."""
        self.network.record_signal(
            ticker="BIAF", sector="Biotech", catalyst_type="fda_approval",
            gap_pct=40.0, confidence=0.2, is_exit=False,
            timestamp=datetime.now(timezone.utc),
        )
        result = self.network.propagate(
            target_ticker="ISPC", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=25.0,
        )
        # Low confidence (0.2) × correlation → below 0.3 threshold
        assert not result.should_tighten

    def test_max_intensity_across_sources(self) -> None:
        """Multiple sources → take MAX intensity."""
        self._record_biotech_signal("BIAF", confidence=0.3)
        self._record_biotech_signal("PRSO", confidence=0.9)
        result = self.network.propagate(
            target_ticker="ISPC", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=25.0,
        )
        # Should use PRSO's 0.9 confidence, not BIAF's 0.3
        assert result.source_count == 2
        assert result.intensity > 0.5

    def test_no_self_contagion(self) -> None:
        """Source ticker is excluded from its own contagion propagation."""
        self._record_biotech_signal("BIAF", confidence=0.9)
        result = self.network.propagate(
            target_ticker="BIAF", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=40.0,
        )
        assert result.intensity == 0.0
        assert not result.should_tighten

    def test_active_position_count(self) -> None:
        """active_position_count returns correct count of non-expired signals."""
        self._record_biotech_signal("BIAF")
        self._record_biotech_signal("ISPC")
        assert self.network.active_position_count() == 2

    def test_contagion_produces_tighten_only(self) -> None:
        """Contagion result never produces should_exit, only should_tighten."""
        self._record_biotech_signal("BIAF", confidence=1.0)
        result = self.network.propagate(
            target_ticker="ISPC", target_sector="Biotech",
            target_catalyst="fda_approval", target_gap_pct=25.0,
        )
        # ContagionResult has should_tighten but no should_exit field
        assert hasattr(result, "should_tighten")
        assert result.should_tighten  # High intensity → tighten


# ── D110: AlphaDecayOracle Tests ─────────────────────────────────


class TestAlphaDecayOracle:
    """Test real-time edge estimation vs null return curve."""

    def setup_method(self) -> None:
        self.oracle = AlphaDecayOracle()

    def test_positive_alpha_at_t5(self) -> None:
        """Stock outperforming null at T+5 → positive alpha, no signal."""
        result = self.oracle.evaluate("TEST", current_return_pct=5.0, minutes_since_open=5.0)
        # null at T+5 = 3.0%, observed = 5.0%, alpha = 2.0%
        assert result.details["alpha_pct"] == pytest.approx(2.0, abs=0.01)
        assert not result.should_exit
        assert not result.should_tighten

    def test_zero_alpha_triggers_exit(self) -> None:
        """Stock matching null exactly → should_exit."""
        result = self.oracle.evaluate("TEST", current_return_pct=3.0, minutes_since_open=5.0)
        # null at T+5 = 3.0%, observed = 3.0%, alpha = 0.0%
        assert result.details["alpha_pct"] == pytest.approx(0.0, abs=0.01)
        assert result.should_exit

    def test_negative_alpha_triggers_exit(self) -> None:
        """Stock underperforming null → should_exit."""
        result = self.oracle.evaluate("TEST", current_return_pct=1.0, minutes_since_open=5.0)
        # null at T+5 = 3.0%, observed = 1.0%, alpha = -2.0%
        assert result.details["alpha_pct"] == pytest.approx(-2.0, abs=0.01)
        assert result.should_exit

    def test_thin_alpha_triggers_tighten(self) -> None:
        """Alpha between 0 and 0.5% → should_tighten."""
        # null at T+10 = 2.0%, observed = 2.3% → alpha = 0.3%
        result = self.oracle.evaluate("TEST", current_return_pct=2.3, minutes_since_open=10.0)
        assert result.details["alpha_pct"] == pytest.approx(0.3, abs=0.01)
        assert result.should_tighten
        assert not result.should_exit

    def test_decay_rate_computation(self) -> None:
        """Decay rate computed from rolling window of measurements."""
        # Feed 5 measurements with declining alpha
        for i in range(5):
            self.oracle.evaluate("TEST", current_return_pct=5.0 - i * 0.5, minutes_since_open=5.0 + i)
        # 6th measurement — oracle should have decay rate
        result = self.oracle.evaluate("TEST", current_return_pct=2.5, minutes_since_open=10.0)
        assert result.details["decay_rate_per_min"] < 0  # Negative = decaying

    def test_interpolation_between_curve_points(self) -> None:
        """Null curve interpolates correctly between defined points."""
        # T+7.5 should interpolate between T+5 (3.0) and T+10 (2.0) → 2.5
        result = self.oracle.evaluate("TEST", current_return_pct=5.0, minutes_since_open=7.5)
        assert result.details["null_return_pct"] == pytest.approx(2.5, abs=0.01)
        assert result.details["alpha_pct"] == pytest.approx(2.5, abs=0.01)


# ── D110: Extended ParallelExitEngine Tests ──────────────────────


class TestParallelExitEngineD110:
    """Test D110 extensions to ParallelExitEngine."""

    def setup_method(self) -> None:
        self.engine = ParallelExitEngine()

    def test_returns_6_results(self) -> None:
        """evaluate_all returns 6 strategy results (was 4 in D109)."""
        bars = _bars_rising(10.0, 5)
        results = self.engine.evaluate_all(
            ticker="AAPL", entry_price=10.0, stop_loss=9.5,
            current_price=10.5, peak_price=10.5,
            minutes_held=5.0, entry_volume=1000, bars=bars,
            catalyst_type="technical_breakout",
            manipulation_phase="ORGANIC_MOMENTUM",
            minutes_since_open=15.0,
        )
        assert len(results) == 6
        names = [r.strategy_name for r in results]
        assert "velocity" in names
        assert "pullback" in names
        assert "volume_exhaustion" in names
        assert "gratitude" in names
        assert "catalyst_half_life" in names
        assert "alpha_oracle" in names

    def test_contagion_in_log_dict(self) -> None:
        """to_log_dict includes contagion section when provided."""
        results = [
            ExitStrategyResult("velocity"),
            ExitStrategyResult("pullback"),
            ExitStrategyResult("volume_exhaustion"),
            ExitStrategyResult("gratitude"),
            ExitStrategyResult("catalyst_half_life"),
            ExitStrategyResult("alpha_oracle"),
        ]
        contagion = ContagionResult(intensity=0.45, should_tighten=True, source_count=1, sources=["BIAF"])
        log = ParallelExitEngine.to_log_dict(results, contagion=contagion, contagion_active_positions=2)

        assert "contagion" in log
        assert log["contagion"]["intensity"] == 0.45
        assert log["contagion"]["tighten"] is True
        assert log["contagion_active_positions"] == 2
        # Contagion tighten should be reflected in anyof
        assert log["anyof_would_tighten"] is True

    def test_backward_compat_4_results(self) -> None:
        """to_log_dict still works with 4 results (D109 callers)."""
        results = [
            ExitStrategyResult("velocity"),
            ExitStrategyResult("pullback"),
            ExitStrategyResult("volume_exhaustion"),
            ExitStrategyResult("gratitude"),
        ]
        log = ParallelExitEngine.to_log_dict(results)
        assert "parallel_strategies" in log
        assert len(log["parallel_strategies"]) == 4
        assert log["contagion_active_positions"] == 0
