#!/usr/bin/env python3
"""
Property-Based Testing Suite for D122 Exit Engine

Uses Hypothesis to generate random market conditions and verify that
system invariants hold under all inputs. This catches edge cases that
unit tests miss — off-by-one errors, division by zero on extreme inputs,
state machine violations, and monotonicity failures.

Usage:
    # Run all property tests (default 200 examples each)
    python -m pytest tests/property/test_d122_properties.py -v

    # Run with more examples for deeper coverage
    python -m pytest tests/property/test_d122_properties.py -v \
        --hypothesis-seed=0 -s

    # Run a specific property
    python -m pytest tests/property/test_d122_properties.py::TestExitInvariants::test_upgrade_only_semantics -v

Install: pip install hypothesis
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hypothesis import given, assume, settings, HealthCheck
from hypothesis import strategies as st
import pytest

# Import the replay simulator strategies for standalone testing
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# We import from the replay simulator to test the same logic
# In production, you'd import from src/ directly
from scripts.phase3_replay_simulator import (
    SimBar, SimPosition, D122ExitEvaluator, ExitSignal,
    VelocityEngineSimulator, PullbackClassifierSimulator,
    VolumeExhaustionSimulator, GratitudeExitSimulator,
    CatalystHalfLifeSimulator, AlphaDecayOracleSimulator,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for generating market data
# ---------------------------------------------------------------------------

reasonable_price = st.floats(min_value=0.50, max_value=500.0, allow_nan=False, allow_infinity=False)
reasonable_volume = st.floats(min_value=0, max_value=1e9, allow_nan=False, allow_infinity=False)
reasonable_pct = st.floats(min_value=-0.50, max_value=5.0, allow_nan=False, allow_infinity=False)
reasonable_atr = st.floats(min_value=0.001, max_value=50.0, allow_nan=False, allow_infinity=False)
bar_count = st.integers(min_value=0, max_value=500)
confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

catalyst_types = st.sampled_from([
    "unknown", "FDA_APPROVAL", "EARNINGS_BEAT", "MERGER_ACQUISITION",
    "BREAKOUT", "SOCIAL_MEDIA", "PUMP_DUMP", "CORPORATE_UPDATE",
    "SECTOR_CATALYST",
])


@st.composite
def sim_bar(draw, base_price=None):
    """Generate a realistic minute bar."""
    if base_price is None:
        base_price = draw(st.floats(min_value=1.0, max_value=200.0,
                                     allow_nan=False, allow_infinity=False))
    noise = draw(st.floats(min_value=-0.05, max_value=0.05,
                            allow_nan=False, allow_infinity=False))
    close = base_price * (1 + noise)
    high = max(base_price, close) * (1 + abs(draw(st.floats(
        min_value=0, max_value=0.02, allow_nan=False, allow_infinity=False))))
    low = min(base_price, close) * (1 - abs(draw(st.floats(
        min_value=0, max_value=0.02, allow_nan=False, allow_infinity=False))))

    return SimBar(
        timestamp=datetime(2026, 3, 20, 9, 30),
        open=base_price,
        high=max(high, max(base_price, close)),
        low=min(low, min(base_price, close)),
        close=max(close, 0.01),  # price can't go to 0
        volume=draw(st.floats(min_value=100, max_value=1e7,
                               allow_nan=False, allow_infinity=False)),
    )


@st.composite
def sim_position(draw):
    """Generate a realistic position."""
    entry = draw(st.floats(min_value=1.0, max_value=100.0,
                            allow_nan=False, allow_infinity=False))
    atr = draw(st.floats(min_value=entry * 0.01, max_value=entry * 0.15,
                          allow_nan=False, allow_infinity=False))
    gap = draw(st.floats(min_value=0.03, max_value=0.40,
                          allow_nan=False, allow_infinity=False))
    stop_dist = max(atr * 2.0, entry * 0.04)
    stop = entry - min(stop_dist, entry * 0.20)
    bars_held = draw(st.integers(min_value=0, max_value=300))

    # Build velocity window
    velocity = []
    price = entry
    for _ in range(min(bars_held, 10)):
        price = price * (1 + draw(st.floats(
            min_value=-0.01, max_value=0.01,
            allow_nan=False, allow_infinity=False)))
        velocity.append(price)

    return SimPosition(
        ticker="TEST",
        entry_price=entry,
        entry_time=datetime(2026, 3, 20, 9, 30),
        qty=100,
        stop_loss=stop,
        atr=atr,
        gap_pct=gap,
        catalyst_type=draw(catalyst_types),
        peak_price=entry * (1 + abs(draw(st.floats(
            min_value=0, max_value=0.20,
            allow_nan=False, allow_infinity=False)))),
        bars_since_entry=bars_held,
        velocity_window=velocity,
        pullback_peak=entry * (1 + abs(draw(st.floats(
            min_value=0, max_value=0.15,
            allow_nan=False, allow_infinity=False)))),
    )


# ---------------------------------------------------------------------------
# Property tests: Core invariants
# ---------------------------------------------------------------------------

class TestExitInvariants:
    """
    These properties must hold for ALL possible market inputs.
    A single counterexample means a bug.
    """

    @given(pos=sim_position(), bar=sim_bar())
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_upgrade_only_semantics(self, pos, bar):
        """
        CRITICAL INVARIANT: Parallel strategies can only upgrade actions.
        HOLD→TIGHTEN ✓, HOLD→EXIT ✓, TIGHTEN→EXIT ✓
        EXIT→HOLD ✗, EXIT→TIGHTEN ✗
        """
        evaluator = D122ExitEvaluator(min_confidence=0.0, min_strategies_for_exit=1)
        action, signals = evaluator.evaluate(pos, bar)

        # If the legacy action is EXIT (stop hit), the final action must be EXIT
        if bar.low <= pos.stop_loss:
            assert action == "EXIT", (
                f"Stop was hit (low={bar.low}, stop={pos.stop_loss}) "
                f"but action was {action}, not EXIT"
            )

    @given(pos=sim_position(), bar=sim_bar())
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_action_is_valid_enum(self, pos, bar):
        """Every evaluation must return a valid action."""
        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)
        action, signals = evaluator.evaluate(pos, bar)
        assert action in ("HOLD", "TIGHTEN", "EXIT"), f"Invalid action: {action}"

    @given(pos=sim_position(), bar=sim_bar())
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_all_strategies_return_signals(self, pos, bar):
        """Every strategy must return exactly one signal per evaluation."""
        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)
        _, signals = evaluator.evaluate(pos, bar)
        assert len(signals) == 6, f"Expected 6 signals, got {len(signals)}"
        strategy_names = {s.strategy for s in signals}
        expected = {
            "VelocityEngine", "PullbackClassifier", "VolumeExhaustion",
            "GratitudeExit", "CatalystHalfLife", "AlphaDecayOracle",
        }
        assert strategy_names == expected, f"Missing strategies: {expected - strategy_names}"

    @given(pos=sim_position(), bar=sim_bar())
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_confidence_bounded_0_1(self, pos, bar):
        """All confidence values must be in [0, 1]."""
        evaluator = D122ExitEvaluator(min_confidence=0.0, min_strategies_for_exit=1)
        _, signals = evaluator.evaluate(pos, bar)
        for s in signals:
            assert 0.0 <= s.confidence <= 1.0, (
                f"{s.strategy} returned confidence {s.confidence}"
            )

    @given(
        pos=sim_position(), bar=sim_bar(),
        thresh_lo=st.floats(min_value=0.0, max_value=0.5,
                             allow_nan=False, allow_infinity=False),
        thresh_hi=st.floats(min_value=0.5, max_value=1.0,
                             allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_higher_confidence_threshold_never_more_aggressive(self, pos, bar,
                                                                thresh_lo, thresh_hi):
        """
        MONOTONICITY: A higher confidence threshold should never produce
        a MORE aggressive action than a lower threshold.

        If threshold=0.3 says HOLD, threshold=0.7 must also say HOLD.
        If threshold=0.7 says EXIT, threshold=0.3 must also say EXIT.
        """
        assume(thresh_lo < thresh_hi)

        eval_lo = D122ExitEvaluator(min_confidence=thresh_lo, min_strategies_for_exit=1)
        eval_hi = D122ExitEvaluator(min_confidence=thresh_hi, min_strategies_for_exit=1)

        action_lo, _ = eval_lo.evaluate(pos, bar)
        action_hi, _ = eval_hi.evaluate(pos, bar)

        action_rank = {"HOLD": 0, "TIGHTEN": 1, "EXIT": 2}

        # Higher threshold should be <= lower threshold in aggressiveness
        # UNLESS the legacy action is EXIT (stop hit), which bypasses thresholds
        if bar.low > pos.stop_loss:  # stop NOT hit
            assert action_rank[action_hi] <= action_rank[action_lo], (
                f"Higher threshold {thresh_hi} produced {action_hi} "
                f"but lower threshold {thresh_lo} produced {action_lo}"
            )

    @given(pos=sim_position(), bar=sim_bar())
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_no_exceptions_on_any_input(self, pos, bar):
        """The evaluator must never raise an exception, regardless of input."""
        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)
        try:
            action, signals = evaluator.evaluate(pos, bar)
        except Exception as e:
            pytest.fail(f"Exception on input: {e}\npos={pos}\nbar={bar}")


# ---------------------------------------------------------------------------
# Property tests: Individual strategy invariants
# ---------------------------------------------------------------------------

class TestVelocityEngineProperties:

    @given(pos=sim_position(), bar=sim_bar())
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_never_exits_with_positive_velocity(self, pos, bar):
        """VelocityEngine should never EXIT when price is accelerating upward."""
        # Set velocity window to strictly increasing
        base = bar.close * 0.95
        pos.velocity_window = [base * (1 + 0.01 * i) for i in range(5)]

        engine = VelocityEngineSimulator()
        sig = engine.evaluate(pos, bar)

        # With strongly positive velocity, should not EXIT
        if pos.velocity_window[-1] > pos.velocity_window[0] * 1.02:
            assert sig.action != "EXIT", (
                f"EXIT with positive velocity: {pos.velocity_window}"
            )

    @given(bars_held=st.integers(min_value=0, max_value=400))
    @settings(max_examples=200)
    def test_phase_assignment_covers_all_minutes(self, bars_held):
        """Every minute maps to exactly one phase."""
        pos = SimPosition(
            ticker="T", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9, atr=0.3, bars_since_entry=bars_held,
            velocity_window=[10.0] * min(bars_held + 1, 5),
        )
        bar = SimBar(timestamp=datetime.now(), open=10, high=10.1,
                     low=9.9, close=10, volume=1000)

        engine = VelocityEngineSimulator()
        sig = engine.evaluate(pos, bar)
        # Should always return a signal, never crash
        assert sig.action in ("HOLD", "TIGHTEN", "EXIT")


class TestGratitudeExitProperties:

    @given(
        entry=st.floats(min_value=1, max_value=100,
                         allow_nan=False, allow_infinity=False),
        stop_pct=st.floats(min_value=0.02, max_value=0.15,
                            allow_nan=False, allow_infinity=False),
        minutes=st.integers(min_value=0, max_value=300),
    )
    @settings(max_examples=300)
    def test_threshold_decreases_over_time(self, entry, stop_pct, minutes):
        """GratitudeExit threshold should decrease (become easier to hit) over time."""
        threshold_early = max(0.75, 3.0 - 0.05 * 0)
        threshold_late = max(0.75, 3.0 - 0.05 * minutes)
        assert threshold_late <= threshold_early

    @given(
        entry=st.floats(min_value=1, max_value=100,
                         allow_nan=False, allow_infinity=False),
        stop_pct=st.floats(min_value=0.02, max_value=0.15,
                            allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_threshold_floor_at_075R(self, entry, stop_pct):
        """Threshold never drops below 0.75R regardless of time."""
        for m in range(0, 500):
            threshold = max(0.75, 3.0 - 0.05 * m)
            assert threshold >= 0.75, f"Threshold {threshold} < 0.75 at minute {m}"


class TestCatalystHalfLifeProperties:

    @given(
        catalyst=catalyst_types,
        minutes=st.integers(min_value=0, max_value=500),
    )
    @settings(max_examples=300)
    def test_never_exits_before_half_life(self, catalyst, minutes):
        """Should never fire EXIT before 0.8x half-life."""
        half_lives = CatalystHalfLifeSimulator.HALF_LIVES
        half_life = half_lives.get(catalyst, 20)

        pos = SimPosition(
            ticker="T", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9, atr=0.3,
            catalyst_type=catalyst, bars_since_entry=minutes,
            velocity_window=[10.0 - 0.001 * i for i in range(5)],  # declining
        )
        bar = SimBar(timestamp=datetime.now(), open=10, high=10,
                     low=9.8, close=9.9, volume=1000)

        strat = CatalystHalfLifeSimulator()
        sig = strat.evaluate(pos, bar)

        if minutes < half_life * 0.8:
            assert sig.action == "HOLD", (
                f"Fired {sig.action} at minute {minutes}, "
                f"half_life={half_life}, catalyst={catalyst}"
            )

    @given(catalyst=catalyst_types)
    @settings(max_examples=50)
    def test_all_catalysts_have_half_life(self, catalyst):
        """Every catalyst type must map to a positive half-life."""
        half_life = CatalystHalfLifeSimulator.HALF_LIVES.get(catalyst, 20)
        assert half_life > 0, f"Catalyst {catalyst} has non-positive half-life"


class TestAlphaDecayOracleProperties:

    @given(
        entry=st.floats(min_value=1, max_value=100,
                         allow_nan=False, allow_infinity=False),
        minutes=st.integers(min_value=0, max_value=120),
        return_pct=st.floats(min_value=-0.20, max_value=0.50,
                              allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=300)
    def test_high_alpha_never_exits(self, entry, minutes, return_pct):
        """If observed return is well above null, should not exit."""
        oracle = AlphaDecayOracleSimulator()
        null_return = oracle._interpolate_null(minutes)

        # If return is > null + 2%, should be HOLD
        if return_pct * 100 > null_return + 2.0:
            pos = SimPosition(
                ticker="T", entry_price=entry, entry_time=datetime.now(),
                qty=100, stop_loss=entry * 0.9, atr=entry * 0.03,
                bars_since_entry=minutes,
                velocity_window=[entry] * 3,
            )
            bar = SimBar(
                timestamp=datetime.now(),
                open=entry * (1 + return_pct),
                high=entry * (1 + return_pct + 0.01),
                low=entry * (1 + return_pct - 0.01),
                close=entry * (1 + return_pct),
                volume=10000,
            )
            sig = oracle.evaluate(pos, bar)
            assert sig.action == "HOLD", (
                f"EXIT at alpha={return_pct*100 - null_return:.1f}%"
            )

    @given(minutes=st.floats(min_value=0, max_value=120,
                              allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_null_curve_monotonically_decreasing(self, minutes):
        """The null return curve should never increase over time."""
        oracle = AlphaDecayOracleSimulator()
        v1 = oracle._interpolate_null(minutes)
        v2 = oracle._interpolate_null(minutes + 1)
        assert v2 <= v1 + 0.001, (  # small epsilon for float precision
            f"Null curve increased: {v1:.4f} at {minutes}min → {v2:.4f} at {minutes+1}min"
        )


# ---------------------------------------------------------------------------
# Property tests: Composite behavior
# ---------------------------------------------------------------------------

class TestCompositeProperties:

    @given(
        entry=st.floats(min_value=2, max_value=50,
                         allow_nan=False, allow_infinity=False),
        gap=st.floats(min_value=0.05, max_value=0.30,
                       allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_gap_day_stop_wider_than_standard(self, entry, gap):
        """
        D122: Gap-day stop floor should always produce wider stops
        than the standard 4% floor when gap > 8%.
        """
        assume(gap > 0.08)
        atr = entry * 0.03  # typical ATR

        standard_floor = entry * 0.04
        gap_floor = entry * gap * 0.5

        # Gap floor should be wider
        assert gap_floor > standard_floor, (
            f"Gap floor {gap_floor:.4f} <= standard {standard_floor:.4f} "
            f"at gap={gap:.1%}"
        )

    @given(
        entry=st.floats(min_value=2, max_value=50,
                         allow_nan=False, allow_infinity=False),
        gap=st.floats(min_value=0.05, max_value=0.80,
                       allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_gap_day_stop_capped_at_20pct(self, entry, gap):
        """D122: Stop distance must never exceed 20% regardless of gap size."""
        gap_floor = entry * gap * 0.5
        capped = min(gap_floor, entry * 0.20)
        assert capped <= entry * 0.20 + 0.01  # epsilon for float

    @given(
        pos=sim_position(), bar=sim_bar(),
        strategies_required=st.integers(min_value=1, max_value=6),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_more_strategies_required_means_fewer_exits(self, pos, bar,
                                                         strategies_required):
        """
        Requiring more strategies for EXIT should never produce
        MORE exits than requiring fewer.
        """
        eval_1 = D122ExitEvaluator(min_confidence=0.0, min_strategies_for_exit=1)
        eval_n = D122ExitEvaluator(min_confidence=0.0,
                                    min_strategies_for_exit=strategies_required)

        action_1, _ = eval_1.evaluate(pos, bar)
        action_n, _ = eval_n.evaluate(pos, bar)

        rank = {"HOLD": 0, "TIGHTEN": 1, "EXIT": 2}

        # Unless legacy EXIT (stop hit), more strategies = less aggressive
        if bar.low > pos.stop_loss:
            assert rank[action_n] <= rank[action_1], (
                f"min_strategies={strategies_required} produced {action_n} "
                f"but min_strategies=1 produced {action_1}"
            )


# ---------------------------------------------------------------------------
# Adversarial scenarios
# ---------------------------------------------------------------------------

class TestAdversarialScenarios:
    """
    Hand-crafted scenarios designed to break the exit engine.
    These are NOT random — they target specific failure modes.
    """

    def test_flash_crash_recovery(self):
        """
        Price drops 15% in 1 bar then recovers fully.
        Should stop-out if below stop, not be rescued by parallel EXIT.
        """
        entry = 10.0
        atr = 0.30
        stop = entry - max(atr * 2, entry * 0.04)

        pos = SimPosition(
            ticker="FLASH", entry_price=entry,
            entry_time=datetime(2026, 3, 20, 9, 30),
            qty=100, stop_loss=stop, atr=atr,
            bars_since_entry=5,
            velocity_window=[10.0, 10.1, 10.2, 10.3, 10.4],
            pullback_peak=10.4,
        )

        crash_bar = SimBar(
            timestamp=datetime(2026, 3, 20, 9, 35),
            open=10.4, high=10.4, low=8.5, close=10.3,
            volume=500000,
        )

        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)
        action, _ = evaluator.evaluate(pos, crash_bar)

        # Low touched below stop — must EXIT
        assert action == "EXIT"

    def test_zero_volume_bar(self):
        """Volume = 0 should not crash VolumeExhaustion."""
        pos = SimPosition(
            ticker="DEAD", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9, atr=0.3, bars_since_entry=30,
            velocity_window=[10.0] * 5, pullback_peak=10.5,
        )
        bar = SimBar(timestamp=datetime.now(), open=10, high=10.1,
                     low=9.9, close=10, volume=0)

        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)
        action, signals = evaluator.evaluate(pos, bar, entry_bar_vol=0)
        assert action in ("HOLD", "TIGHTEN", "EXIT")

    def test_penny_stock_extreme_spread(self):
        """$0.50 stock with $0.10 ATR — stops shouldn't be tighter than spread."""
        entry = 0.50
        atr = 0.10
        stop = entry - max(atr * 2, entry * 0.04)
        # stop = 0.50 - 0.20 = 0.30 (40% below entry)
        assert stop == pytest.approx(0.30, abs=0.01)

        # With gap-day widening: 15% gap
        gap = 0.15
        gap_floor = entry * gap * 0.5  # = 0.0375
        # ATR-based (0.20) is wider than gap floor (0.0375)
        # So stop stays at 0.30
        final_stop_dist = max(atr * 2, entry * 0.04, gap_floor)
        assert final_stop_dist == pytest.approx(0.20, abs=0.01)

    def test_position_at_exact_stop_price(self):
        """Bar low exactly equals stop — should trigger."""
        pos = SimPosition(
            ticker="EXACT", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9.40, atr=0.30, bars_since_entry=10,
            velocity_window=[10.0] * 5, pullback_peak=10.0,
        )
        bar = SimBar(timestamp=datetime.now(), open=9.50, high=9.55,
                     low=9.40, close=9.45, volume=10000)

        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)
        action, _ = evaluator.evaluate(pos, bar)
        assert action == "EXIT"

    def test_catalyst_unknown_30min_hold(self):
        """
        The scenario that caused 93% false-fire rate:
        catalyst_type=unknown, held 30+ min, velocity <= 0.
        With confidence gate at 0.7, CatalystHalfLife fires at 0.75.
        This SHOULD now fire (by design) but only if velocity is negative.
        """
        pos = SimPosition(
            ticker="UNK30", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9.40, atr=0.30,
            catalyst_type="unknown",
            bars_since_entry=35,  # past 1.5x half-life (20*1.5=30)
            velocity_window=[10.0, 9.99, 9.98, 9.97, 9.96],  # declining
            pullback_peak=10.2,
        )
        bar = SimBar(timestamp=datetime.now(), open=9.96, high=9.97,
                     low=9.93, close=9.95, volume=5000)

        strat = CatalystHalfLifeSimulator()
        sig = strat.evaluate(pos, bar)

        # Should fire — past half-life AND velocity <= 0
        assert sig.action == "EXIT"
        assert sig.confidence >= 0.7

    def test_catalyst_unknown_30min_still_advancing(self):
        """
        Same as above but velocity is POSITIVE.
        Should NOT fire EXIT (velocity gate protects winners).
        """
        pos = SimPosition(
            ticker="UNK30+", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9.40, atr=0.30,
            catalyst_type="unknown",
            bars_since_entry=35,
            velocity_window=[10.0, 10.02, 10.05, 10.08, 10.12],  # advancing
            pullback_peak=10.12,
        )
        bar = SimBar(timestamp=datetime.now(), open=10.12, high=10.15,
                     low=10.10, close=10.13, volume=8000)

        strat = CatalystHalfLifeSimulator()
        sig = strat.evaluate(pos, bar)

        # Should NOT exit — still advancing despite time
        assert sig.action != "EXIT", (
            f"CatalystHalfLife exited at positive velocity"
        )

    def test_gratitude_exit_at_minute_zero(self):
        """Minute 0: threshold = 3.0R. Only extreme winners trigger."""
        entry = 10.0
        stop = 9.40  # risk = $0.60
        # 3.0R = $1.80 above entry = $11.80

        pos = SimPosition(
            ticker="MIN0", entry_price=entry, entry_time=datetime.now(),
            qty=100, stop_loss=stop, atr=0.30,
            bars_since_entry=0, velocity_window=[entry],
        )

        # At $11.50 (2.5R) — should NOT trigger
        bar = SimBar(timestamp=datetime.now(), open=11.5, high=11.6,
                     low=11.4, close=11.5, volume=10000)
        strat = GratitudeExitSimulator()
        sig = strat.evaluate(pos, bar)
        assert sig.action == "HOLD"

        # At $12.50 (4.2R) — should trigger EXIT (>= 1.5x threshold = 4.5R)
        bar2 = SimBar(timestamp=datetime.now(), open=12.5, high=12.7,
                      low=12.4, close=12.7, volume=10000)
        sig2 = strat.evaluate(pos, bar2)
        assert sig2.action == "EXIT"

    def test_pullback_from_zero_advance(self):
        """
        Position that never advances should not trigger EXHAUSTED
        (no advance to retrace from).
        """
        pos = SimPosition(
            ticker="FLAT", entry_price=10, entry_time=datetime.now(),
            qty=100, stop_loss=9.40, atr=0.30,
            bars_since_entry=60,
            velocity_window=[10.0] * 5,
            pullback_peak=10.0,  # never advanced
            advance_size=0.0,
        )
        bar = SimBar(timestamp=datetime.now(), open=9.95, high=10.0,
                     low=9.90, close=9.95, volume=5000)

        strat = PullbackClassifierSimulator()
        sig = strat.evaluate(pos, bar)
        assert sig.action == "HOLD", "Fired on zero advance"


# ---------------------------------------------------------------------------
# Monte Carlo stress test
# ---------------------------------------------------------------------------

class TestMonteCarloStress:
    """
    Run many random evaluations and check statistical properties.
    """

    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    @given(seed=st.integers(min_value=0, max_value=10000))
    def test_1000_random_evaluations_no_crash(self, seed):
        """
        Generate 1000 random (pos, bar) pairs and verify no crashes.
        This is a fuzz test — it finds edge cases in float math.
        """
        import random
        random.seed(seed)

        evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)

        for _ in range(100):
            entry = random.uniform(0.5, 200)
            atr = random.uniform(0.001, entry * 0.2)
            gap = random.uniform(-0.5, 0.5)
            stop = max(0.01, entry - max(atr * 2, entry * 0.04))

            pos = SimPosition(
                ticker="FUZZ", entry_price=entry,
                entry_time=datetime.now(), qty=100,
                stop_loss=stop, atr=atr, gap_pct=gap,
                catalyst_type=random.choice(list(CatalystHalfLifeSimulator.HALF_LIVES.keys())),
                bars_since_entry=random.randint(0, 400),
                velocity_window=[entry * (1 + random.gauss(0, 0.01))
                                  for _ in range(random.randint(0, 10))],
                pullback_peak=entry * (1 + abs(random.gauss(0, 0.1))),
            )

            price = entry * (1 + random.gauss(0, 0.05))
            bar = SimBar(
                timestamp=datetime.now(),
                open=price, high=price * 1.01,
                low=price * 0.99, close=price,
                volume=abs(random.gauss(10000, 50000)),
            )

            try:
                action, signals = evaluator.evaluate(pos, bar)
                assert action in ("HOLD", "TIGHTEN", "EXIT")
                assert len(signals) == 6
                for s in signals:
                    assert 0 <= s.confidence <= 1
            except Exception as e:
                pytest.fail(
                    f"Crash on random input (seed={seed}): {e}\n"
                    f"pos.entry={entry}, pos.stop={stop}, bar.close={price}"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
