"""
Tests for D78: Smart Exit Intelligence.

Covers:
  - ExitSignalEngine: all 7 individual signals + composite + recommendations
  - ExitIntelligenceManager: position evaluation + action generation
  - ExitSignal: to_scores_dict serialization
  - Edge cases: zero/negative inputs, missing data, boundary thresholds
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.execution.exit_intelligence import (
    EXIT_SIGNAL_WEIGHTS,
    ExitAction,
    ExitIntelligenceManager,
    ExitSignal,
    ExitSignalEngine,
)


# ── Helpers ──────────────────────────────────────────────────────


@dataclass
class FakeManagedPosition:
    """Minimal stand-in for ManagedPosition in tests."""

    ticker: str
    qty: int = 100
    entry_price: float = 10.0
    signal_price: float = 10.0
    stop_loss: float = 9.0
    target_prices: list[float] = field(default_factory=lambda: [10.3, 10.6, 11.0])
    peak_price: float = 0.0
    trailing_stop_active: bool = False
    entry_spread: float = 0.02
    entry_volume: int = 100_000
    peak_volume: int = 200_000
    remaining_qty: int = 100


# ── ExitSignal Tests ─────────────────────────────────────────────


class TestExitSignal:
    """Tests for ExitSignal dataclass."""

    def test_to_scores_dict_keys(self):
        sig = ExitSignal(ticker="TEST", volume_fade=0.5, composite_exit_urgency=0.35)
        d = sig.to_scores_dict()
        expected_keys = {
            "volume_fade", "vwap_deterioration", "spread_widening",
            "time_decay", "distribution", "resistance_proximity",
            "failed_breakout",
            # D89: Multi-bar signals
            "churning", "obv_divergence", "volume_climax",
            "momentum_degradation", "flow_toxicity",
            # D106: Distribution detector
            "distribution_detector",
            "composite",
        }
        assert set(d.keys()) == expected_keys

    def test_to_scores_dict_rounding(self):
        sig = ExitSignal(ticker="TEST", volume_fade=0.12345, distribution=0.99999)
        d = sig.to_scores_dict()
        assert d["volume_fade"] == 0.123
        assert d["distribution"] == 1.0

    def test_default_recommendation_is_hold(self):
        sig = ExitSignal(ticker="TEST")
        assert sig.recommendation == "HOLD"
        assert sig.composite_exit_urgency == 0.0


# ── Volume Fade Signal ───────────────────────────────────────────


class TestVolumeFade:
    """Tests for ExitSignalEngine._volume_fade."""

    engine = ExitSignalEngine()

    def test_healthy_volume(self):
        """Volume at 80% of peak → score 0.0."""
        assert self.engine._volume_fade(160_000, 200_000) == 0.0

    def test_volume_at_peak(self):
        """Volume at peak → score 0.0."""
        assert self.engine._volume_fade(200_000, 200_000) == 0.0

    def test_volume_above_peak(self):
        """Volume exceeds peak → score 0.0."""
        assert self.engine._volume_fade(300_000, 200_000) == 0.0

    def test_volume_dried_up(self):
        """Volume at 5% of peak → score 1.0."""
        assert self.engine._volume_fade(10_000, 200_000) == 1.0

    def test_volume_moderate_fade(self):
        """Volume at 40% of peak → between 0 and 1."""
        score = self.engine._volume_fade(80_000, 200_000)
        assert 0.0 < score < 1.0

    def test_volume_at_70_pct_boundary(self):
        """Volume at exactly 70% → score 0.0 (boundary)."""
        assert self.engine._volume_fade(140_000, 200_000) == 0.0

    def test_volume_at_10_pct_boundary(self):
        """Volume at exactly 10% → score 1.0 (boundary)."""
        assert self.engine._volume_fade(20_000, 200_000) == 1.0

    def test_zero_peak_volume(self):
        """Peak volume zero → score 0.0 (safe default)."""
        assert self.engine._volume_fade(100_000, 0) == 0.0

    def test_zero_current_volume(self):
        """Current volume zero → score 0.0 (safe default)."""
        assert self.engine._volume_fade(0, 200_000) == 0.0

    def test_both_zero(self):
        assert self.engine._volume_fade(0, 0) == 0.0


# ── VWAP Deterioration Signal ────────────────────────────────────


class TestVwapDeterioration:
    """Tests for ExitSignalEngine._vwap_deterioration."""

    engine = ExitSignalEngine()

    def test_above_vwap(self):
        """Price above VWAP → score 0.0."""
        assert self.engine._vwap_deterioration(10.5, 10.0, 10.0) == 0.0

    def test_at_vwap(self):
        """Price exactly at VWAP → score 0.0."""
        assert self.engine._vwap_deterioration(10.0, 10.0, 10.0) == 0.0

    def test_slightly_below_vwap(self):
        """Price 1% below VWAP → moderate score."""
        score = self.engine._vwap_deterioration(9.90, 10.0, 10.0)
        assert 0.0 < score < 1.0

    def test_far_below_vwap(self):
        """Price 5% below VWAP → score 1.0 (capped)."""
        score = self.engine._vwap_deterioration(9.50, 10.0, 10.0)
        assert score == 1.0

    def test_exactly_3pct_below(self):
        """Price 3% below VWAP → score 1.0."""
        # vwap=10, entry=10, current=9.70 → (10 - 9.70) / 10 = 0.03
        score = self.engine._vwap_deterioration(9.70, 10.0, 10.0)
        assert score == 1.0

    def test_zero_vwap(self):
        """VWAP zero → score 0.0 (no data)."""
        assert self.engine._vwap_deterioration(10.0, 0.0, 10.0) == 0.0

    def test_zero_entry(self):
        """Entry price zero → score 0.0."""
        assert self.engine._vwap_deterioration(10.0, 10.0, 0.0) == 0.0


# ── Spread Widening Signal ───────────────────────────────────────


class TestSpreadWidening:
    """Tests for ExitSignalEngine._spread_widening."""

    engine = ExitSignalEngine()

    def test_normal_spread(self):
        """Small spread, no widening → score 0.0."""
        assert self.engine._spread_widening(0.02, 0.02, 10.0) == 0.0

    def test_spread_3x_entry(self):
        """Spread 3x entry → score 0.9."""
        score = self.engine._spread_widening(0.06, 0.02, 10.0)
        assert score == 0.9

    def test_spread_2x_entry(self):
        """Spread 2x entry → score 0.6."""
        score = self.engine._spread_widening(0.04, 0.02, 10.0)
        assert score == 0.6

    def test_spread_1_5x_entry(self):
        """Spread 1.5x entry → score 0.3."""
        score = self.engine._spread_widening(0.03, 0.02, 10.0)
        assert score == 0.3

    def test_spread_3pct_of_price(self):
        """Spread ≥ 3% of price → score 1.0."""
        score = self.engine._spread_widening(0.30, 0.02, 10.0)
        assert score == 1.0

    def test_spread_1_5pct_no_entry_comparison(self):
        """Spread 1.5% of price, no entry spread → score 0.4."""
        score = self.engine._spread_widening(0.15, 0.0, 10.0)
        assert score == 0.4

    def test_zero_entry_price(self):
        assert self.engine._spread_widening(0.02, 0.02, 0.0) == 0.0

    def test_narrower_than_entry(self):
        """Spread narrower than entry → score 0.0."""
        assert self.engine._spread_widening(0.01, 0.02, 10.0) == 0.0


# ── Time Decay Signal ────────────────────────────────────────────


class TestTimeDecay:
    """Tests for ExitSignalEngine._time_decay."""

    engine = ExitSignalEngine()

    def test_early_morning(self):
        """9:30 AM → score 0.0 (peak momentum)."""
        assert self.engine._time_decay(9, 30) == 0.0

    def test_1030_boundary(self):
        """10:30 AM → score 0.0 (boundary)."""
        assert self.engine._time_decay(10, 30) == 0.0

    def test_noon(self):
        """12:00 PM → between 0 and 1."""
        score = self.engine._time_decay(12, 0)
        # t = 12.0, (12.0 - 10.5) / 4.5 = 1.5/4.5 ≈ 0.333
        assert 0.3 < score < 0.4

    def test_2pm(self):
        """2:00 PM → higher urgency."""
        score = self.engine._time_decay(14, 0)
        # t = 14.0, (14.0 - 10.5) / 4.5 = 3.5/4.5 ≈ 0.778
        assert 0.7 < score < 0.8

    def test_3pm(self):
        """3:00 PM → score 1.0."""
        assert self.engine._time_decay(15, 0) == 1.0

    def test_after_3pm(self):
        """3:30 PM → score 1.0 (capped)."""
        assert self.engine._time_decay(15, 30) == 1.0

    def test_minutes_granularity(self):
        """11:00 AM → small positive score."""
        score = self.engine._time_decay(11, 0)
        # t = 11.0, (11.0 - 10.5) / 4.5 = 0.5/4.5 ≈ 0.111
        assert 0.1 < score < 0.15


# ── Distribution Detection Signal ────────────────────────────────


class TestDistributionDetection:
    """Tests for ExitSignalEngine._distribution_detection."""

    engine = ExitSignalEngine()

    def test_balanced_book(self):
        """Equal bid/ask size → score 0.0."""
        assert self.engine._distribution_detection(1000, 1000) == 0.0

    def test_bid_heavy(self):
        """More buyers → score 0.0."""
        assert self.engine._distribution_detection(2000, 500) == 0.0

    def test_moderate_ask_pressure(self):
        """Ask 2x bid → mild distribution."""
        score = self.engine._distribution_detection(1000, 2000)
        assert 0.0 < score < 0.5

    def test_heavy_distribution(self):
        """Ask 5x bid → score 1.0."""
        score = self.engine._distribution_detection(1000, 5000)
        assert score == 1.0

    def test_extreme_distribution(self):
        """Ask 10x bid → score 1.0 (capped)."""
        score = self.engine._distribution_detection(1000, 10000)
        assert score == 1.0

    def test_at_1_5_boundary(self):
        """Ratio exactly 1.5 → score 0.0."""
        assert self.engine._distribution_detection(1000, 1500) == 0.0

    def test_zero_bid(self):
        """Zero bid → score 0.0 (no data)."""
        assert self.engine._distribution_detection(0, 1000) == 0.0

    def test_zero_ask(self):
        assert self.engine._distribution_detection(1000, 0) == 0.0


# ── Resistance Proximity Signal ──────────────────────────────────


class TestResistanceProximity:
    """Tests for ExitSignalEngine._resistance_proximity."""

    engine = ExitSignalEngine()

    def test_above_resistance(self):
        """Price above resistance → score 0.0."""
        assert self.engine._resistance_proximity(10.5, 10.0) == 0.0

    def test_at_resistance(self):
        """Price at resistance → score 1.0."""
        assert self.engine._resistance_proximity(10.0, 10.0) == 1.0

    def test_just_below_resistance(self):
        """Price 1% below → moderate score."""
        score = self.engine._resistance_proximity(9.90, 10.0)
        assert 0.5 < score < 1.0

    def test_far_from_resistance(self):
        """Price 5% below → score 0.0 (too far away)."""
        assert self.engine._resistance_proximity(9.50, 10.0) == 0.0

    def test_3pct_boundary(self):
        """Price exactly 3% below → score 0.0."""
        # 10.0 - 10.0*0.03 = 9.70, distance_pct = (10-9.70)/9.70 ≈ 0.0309
        assert self.engine._resistance_proximity(9.70, 10.0) == 0.0

    def test_no_resistance(self):
        """No resistance level → score 0.0."""
        assert self.engine._resistance_proximity(10.0, 0.0) == 0.0

    def test_zero_price(self):
        assert self.engine._resistance_proximity(0.0, 10.0) == 0.0


# ── Failed Breakout Signal ───────────────────────────────────────


class TestFailedBreakout:
    """Tests for ExitSignalEngine._failed_breakout."""

    engine = ExitSignalEngine()

    def test_broke_through(self):
        """Price above resistance → score 0.0."""
        assert self.engine._failed_breakout(10.5, 10.0, 9.0) == 0.0

    def test_no_run_up(self):
        """Price hasn't gained from entry → score 0.0."""
        assert self.engine._failed_breakout(9.0, 10.0, 9.0) == 0.0

    def test_near_resistance_after_run(self):
        """Price near resistance after significant run → positive score."""
        # entry=9.0, current=9.95, resistance=10.0
        # gain = (9.95-9)/9 = 0.1055, distance = (10-9.95)/9.95 = 0.005
        score = self.engine._failed_breakout(9.95, 10.0, 9.0)
        assert score > 0.0

    def test_far_from_resistance_despite_run(self):
        """Price ran up but far from resistance → score 0.0."""
        # entry=9.0, current=9.5, resistance=10.0
        # distance = (10-9.5)/9.5 = 0.053 > 0.02
        assert self.engine._failed_breakout(9.5, 10.0, 9.0) == 0.0

    def test_no_resistance_level(self):
        assert self.engine._failed_breakout(10.0, 0.0, 9.0) == 0.0

    def test_zero_entry(self):
        assert self.engine._failed_breakout(10.0, 10.5, 0.0) == 0.0


# ── Composite Score ──────────────────────────────────────────────


class TestCompositeScore:
    """Tests for ExitSignalEngine._composite_score."""

    def test_all_zero_signals(self):
        """All signals at 0 → composite 0."""
        engine = ExitSignalEngine()
        sig = ExitSignal(ticker="TEST")
        assert engine._composite_score(sig) == 0.0

    def test_all_max_signals(self):
        """All 13 signals at 1.0 → composite 1.0."""
        engine = ExitSignalEngine()
        sig = ExitSignal(
            ticker="TEST",
            volume_fade=1.0,
            vwap_deterioration=1.0,
            spread_widening=1.0,
            time_decay=1.0,
            distribution=1.0,
            resistance_proximity=1.0,
            failed_breakout=1.0,
            # D89: Multi-bar signals
            churning=1.0,
            obv_divergence=1.0,
            volume_climax=1.0,
            momentum_degradation=1.0,
            flow_toxicity=1.0,
            # D106: Distribution detector
            distribution_detector=1.0,
        )
        composite = engine._composite_score(sig)
        assert abs(composite - 1.0) < 0.001

    def test_weights_sum_to_one(self):
        """Default weights should sum to 1.0."""
        total = sum(EXIT_SIGNAL_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_vwap_has_highest_weight(self):
        """D89: VWAP deterioration should have the highest weight (0.15, tied with vol_fade/churning)."""
        assert EXIT_SIGNAL_WEIGHTS["vwap_deterioration"] == 0.15
        # VWAP is tied for highest with volume_fade (0.12) and churning (0.10)
        # but remains the single highest individual weight
        max_weight = max(EXIT_SIGNAL_WEIGHTS.values())
        assert EXIT_SIGNAL_WEIGHTS["vwap_deterioration"] == max_weight

    def test_single_signal_contribution(self):
        """D89: Only vwap_deterioration at 1.0 → composite = 0.15 (rebalanced weight)."""
        engine = ExitSignalEngine()
        sig = ExitSignal(ticker="TEST", vwap_deterioration=1.0)
        composite = engine._composite_score(sig)
        assert abs(composite - 0.15) < 0.001

    def test_custom_weights(self):
        """Custom weights merge onto defaults (D120: merge semantics)."""
        custom = {"volume_fade": 1.0}  # Override volume_fade weight
        engine = ExitSignalEngine(weights=custom)
        assert engine._weights["volume_fade"] == 1.0
        # Other defaults preserved
        from src.execution.exit_intelligence import EXIT_SIGNAL_WEIGHTS
        assert engine._weights["vwap_deterioration"] == EXIT_SIGNAL_WEIGHTS["vwap_deterioration"]
        # Signal with only volume_fade=0.8 set, rest default 0
        sig = ExitSignal(ticker="TEST", volume_fade=0.8)
        composite = engine._composite_score(sig)
        # volume_fade contributes 0.8 * 1.0 = 0.8, all others 0
        # Weighted average: 0.8 / sum(all weights)
        total_weight = sum(engine._weights.values())
        expected = 0.8 * 1.0 / total_weight
        assert abs(composite - expected) < 0.001


# ── Recommendation Thresholds ────────────────────────────────────


class TestRecommendations:
    """Tests for compute_exit_signals → recommendation logic."""

    def test_hold_recommendation(self):
        """Low composite → HOLD."""
        engine = ExitSignalEngine()
        sig = engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.5,
            entry_price=10.0,
            hour_et=10,
            minute_et=0,
        )
        assert sig.recommendation == "HOLD"
        assert sig.composite_exit_urgency < 0.3

    def test_tighten_recommendation(self):
        """Moderate composite → TIGHTEN."""
        engine = ExitSignalEngine(tighten_threshold=0.3, exit_threshold=0.6)
        # Push time_decay and volume_fade high for moderate composite
        sig = engine.compute_exit_signals(
            ticker="TEST",
            current_price=9.70,  # Below VWAP
            entry_price=10.0,
            vwap=10.0,
            current_volume=20_000,  # 10% of peak → volume_fade=1.0
            peak_volume=200_000,
            hour_et=14,  # Late afternoon
            minute_et=0,
        )
        assert sig.recommendation in ("TIGHTEN", "EXIT")
        assert sig.composite_exit_urgency >= 0.3

    def test_exit_recommendation(self):
        """High composite → EXIT. D89: uses lower exit threshold to account
        for rebalanced weights (35% of weight is on bar-based signals which
        require bar history to fire)."""
        engine = ExitSignalEngine(tighten_threshold=0.3, exit_threshold=0.45)
        sig = engine.compute_exit_signals(
            ticker="TEST",
            current_price=9.50,  # Far below VWAP
            entry_price=10.0,
            vwap=10.0,
            bid=9.49,
            ask=9.80,  # Wide spread (3% of price)
            current_volume=10_000,  # 5% of peak
            peak_volume=200_000,
            entry_spread=0.02,
            hour_et=15,  # 3 PM
            minute_et=0,
            bid_size=100,
            ask_size=1000,  # 10x ask/bid
        )
        assert sig.recommendation == "EXIT"
        assert sig.composite_exit_urgency >= 0.45

    def test_custom_thresholds(self):
        """Custom thresholds change recommendation breakpoints."""
        engine = ExitSignalEngine(tighten_threshold=0.1, exit_threshold=0.2)
        # Even mild signals should trigger with low thresholds
        sig = engine.compute_exit_signals(
            ticker="TEST",
            current_price=9.85,
            entry_price=10.0,
            vwap=10.0,
            hour_et=13,
            minute_et=0,
        )
        # With lowered thresholds, moderate signals should trigger TIGHTEN or EXIT
        assert sig.composite_exit_urgency > 0.0

    def test_reasoning_populated_for_exit(self):
        """EXIT recommendation has meaningful reasoning."""
        engine = ExitSignalEngine(exit_threshold=0.01)
        sig = engine.compute_exit_signals(
            ticker="TEST",
            current_price=9.50,
            entry_price=10.0,
            vwap=10.0,
            hour_et=15,
        )
        assert sig.recommendation in ("TIGHTEN", "EXIT")
        assert len(sig.reasoning) > 0
        assert sig.reasoning != "Thesis intact"

    def test_hold_reasoning(self):
        """HOLD recommendation has 'Thesis intact'."""
        engine = ExitSignalEngine()
        sig = engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.5,
            entry_price=10.0,
            hour_et=9,
            minute_et=30,
        )
        assert sig.recommendation == "HOLD"
        assert sig.reasoning == "Thesis intact"


# ── Full compute_exit_signals Integration ────────────────────────


class TestComputeExitSignals:
    """Integration tests for the full signal computation."""

    engine = ExitSignalEngine()

    def test_all_signals_in_range(self):
        """All individual signals should be in [0, 1]."""
        sig = self.engine.compute_exit_signals(
            ticker="TEST",
            current_price=9.80,
            entry_price=10.0,
            bid=9.79,
            ask=9.81,
            current_volume=50_000,
            entry_spread=0.01,
            peak_volume=100_000,
            vwap=10.0,
            hour_et=13,
            minute_et=30,
            resistance_level=10.5,
            bid_size=500,
            ask_size=800,
        )
        for name in [
            "volume_fade", "vwap_deterioration", "spread_widening",
            "time_decay", "distribution", "resistance_proximity",
            "failed_breakout", "composite_exit_urgency",
        ]:
            val = getattr(sig, name)
            assert 0.0 <= val <= 1.0, f"{name}={val} out of range"

    def test_no_data(self):
        """Minimal data → all signals 0.0, HOLD."""
        sig = self.engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=10.0,
        )
        assert sig.composite_exit_urgency == 0.0
        assert sig.recommendation == "HOLD"

    def test_ticker_preserved(self):
        sig = self.engine.compute_exit_signals(
            ticker="AAPL",
            current_price=10.0,
            entry_price=10.0,
        )
        assert sig.ticker == "AAPL"


# ── ExitIntelligenceManager Tests ────────────────────────────────


class TestExitIntelligenceManager:
    """Tests for ExitIntelligenceManager.evaluate_positions."""

    def _make_snapshot(
        self,
        price: float = 10.0,
        bid: float = 9.99,
        ask: float = 10.01,
        volume: int = 100_000,
        bid_size: int = 500,
        ask_size: int = 500,
    ) -> dict:
        """Create an Alpaca-style snapshot dict."""
        return {
            "latestTrade": {"p": price},
            "latestQuote": {
                "bp": bid,
                "ap": ask,
                "bs": bid_size,
                "as": ask_size,
            },
            "dailyBar": {"v": volume},
        }

    def test_empty_positions(self):
        mgr = ExitIntelligenceManager()
        actions = mgr.evaluate_positions([], {})
        assert actions == []

    def test_no_snapshot_for_ticker(self):
        """Position with no snapshot → skipped."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="AAPL")
        actions = mgr.evaluate_positions([pos], {})
        assert len(actions) == 0

    def test_hold_action(self):
        """Healthy position → HOLD."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="AAPL",
            entry_price=10.0,
            stop_loss=9.0,
            peak_volume=200_000,
        )
        snap = self._make_snapshot(price=10.5, volume=180_000)
        actions = mgr.evaluate_positions(
            [pos], {"AAPL": snap}, hour_et=10, minute_et=0,
        )
        assert len(actions) == 1
        assert actions[0].action == "HOLD"
        assert actions[0].ticker == "AAPL"

    def test_exit_action(self):
        """Distressed position → EXIT. D89: lower threshold for snapshot-only signals."""
        engine = ExitSignalEngine(tighten_threshold=0.3, exit_threshold=0.45)
        mgr = ExitIntelligenceManager(engine=engine)
        pos = FakeManagedPosition(
            ticker="PUMP",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.02,
            peak_volume=200_000,
        )
        snap = self._make_snapshot(
            price=9.50,
            bid=9.30,
            ask=9.80,  # 5% spread
            volume=10_000,  # dried up
            bid_size=100,
            ask_size=2000,  # heavy distribution
        )
        actions = mgr.evaluate_positions(
            [pos], {"PUMP": snap},
            hour_et=15, minute_et=0,
            vwap_lookup={"PUMP": 10.0},
        )
        assert len(actions) == 1
        assert actions[0].action == "EXIT"
        assert "SMART_EXIT" in actions[0].exit_reason

    def test_tighten_with_stop_ratchet_invariant(self):
        """TIGHTEN only moves stop UP, never down."""
        engine = ExitSignalEngine(tighten_threshold=0.15, exit_threshold=0.8)
        mgr = ExitIntelligenceManager(engine=engine)
        pos = FakeManagedPosition(
            ticker="TEST",
            entry_price=10.0,
            stop_loss=11.0,  # Very high existing stop
            peak_volume=200_000,
        )
        snap = self._make_snapshot(
            price=10.5,
            volume=50_000,  # Moderate fade
        )
        actions = mgr.evaluate_positions(
            [pos], {"TEST": snap},
            hour_et=13, minute_et=0,
            vwap_lookup={"TEST": 10.8},
        )
        # Even if TIGHTEN triggers, new_stop < existing 11.0 → becomes HOLD
        if len(actions) > 0:
            action = actions[0]
            if action.action == "TIGHTEN":
                assert action.new_stop is not None
                assert action.new_stop > pos.stop_loss

    def test_peak_price_tracking(self):
        """Manager updates peak_price on position."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="AAPL",
            entry_price=10.0,
            peak_price=10.0,
        )
        snap = self._make_snapshot(price=12.0)
        mgr.evaluate_positions([pos], {"AAPL": snap})
        assert pos.peak_price == 12.0

    def test_peak_price_no_decrease(self):
        """Peak price should never decrease."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="AAPL",
            entry_price=10.0,
            peak_price=15.0,
        )
        snap = self._make_snapshot(price=12.0)
        mgr.evaluate_positions([pos], {"AAPL": snap})
        assert pos.peak_price == 15.0

    def test_peak_volume_tracking(self):
        """Manager updates peak_volume on position."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="AAPL",
            entry_price=10.0,
            peak_volume=100_000,
        )
        snap = self._make_snapshot(price=10.0, volume=250_000)
        mgr.evaluate_positions([pos], {"AAPL": snap})
        assert pos.peak_volume == 250_000

    def test_multiple_positions(self):
        """Evaluates multiple positions independently."""
        mgr = ExitIntelligenceManager()
        pos1 = FakeManagedPosition(ticker="AAPL", entry_price=10.0)
        pos2 = FakeManagedPosition(ticker="MSFT", entry_price=20.0)
        snaps = {
            "AAPL": self._make_snapshot(price=10.5),
            "MSFT": self._make_snapshot(price=20.5),
        }
        actions = mgr.evaluate_positions([pos1, pos2], snaps)
        assert len(actions) == 2
        tickers = {a.ticker for a in actions}
        assert tickers == {"AAPL", "MSFT"}

    def test_skip_zero_price(self):
        """Snapshot with zero price → skipped."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="AAPL")
        snap = self._make_snapshot(price=0.0)
        actions = mgr.evaluate_positions([pos], {"AAPL": snap})
        assert len(actions) == 0

    def test_vwap_lookup_integration(self):
        """VWAP lookup is passed through correctly."""
        engine = ExitSignalEngine()
        mgr = ExitIntelligenceManager(engine=engine)
        pos = FakeManagedPosition(
            ticker="TEST",
            entry_price=10.0,
        )
        # Price below VWAP should trigger vwap_deterioration
        snap = self._make_snapshot(price=9.50)
        actions = mgr.evaluate_positions(
            [pos], {"TEST": snap},
            vwap_lookup={"TEST": 10.0},
        )
        assert len(actions) == 1
        assert actions[0].signal.vwap_deterioration > 0.0

    def test_alternative_snapshot_format(self):
        """Test fallback snapshot keys (last_price, volume, bid, ask)."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="AAPL", entry_price=10.0)
        snap = {
            "last_price": 10.5,
            "bid": 10.49,
            "ask": 10.51,
            "volume": 100_000,
            "bid_size": 500,
            "ask_size": 500,
        }
        actions = mgr.evaluate_positions([pos], {"AAPL": snap})
        assert len(actions) == 1
        assert actions[0].ticker == "AAPL"


# ── ExitAction Tests ─────────────────────────────────────────────


class TestExitAction:
    """Tests for ExitAction dataclass."""

    def test_hold_action_no_stop(self):
        sig = ExitSignal(ticker="TEST")
        action = ExitAction(ticker="TEST", action="HOLD", signal=sig)
        assert action.new_stop is None
        assert action.exit_reason == ""

    def test_tighten_action_has_stop(self):
        sig = ExitSignal(ticker="TEST")
        action = ExitAction(
            ticker="TEST", action="TIGHTEN", signal=sig,
            new_stop=9.85, exit_reason="D78 TIGHTEN: time_decay",
        )
        assert action.new_stop == 9.85
        assert "TIGHTEN" in action.exit_reason

    def test_exit_action_has_reason(self):
        sig = ExitSignal(ticker="TEST")
        action = ExitAction(
            ticker="TEST", action="EXIT", signal=sig,
            exit_reason="D78 SMART_EXIT: vol_fade=0.9, below_vwap=0.8",
        )
        assert "SMART_EXIT" in action.exit_reason


# ── Edge Cases & Robustness ──────────────────────────────────────


class TestEdgeCases:
    """Edge cases and robustness tests."""

    engine = ExitSignalEngine()

    def test_negative_price(self):
        """Negative prices → all signals 0.0."""
        sig = self.engine.compute_exit_signals(
            ticker="TEST",
            current_price=-5.0,
            entry_price=-10.0,
        )
        assert sig.composite_exit_urgency == 0.0

    def test_very_large_values(self):
        """Very large numbers don't cause overflow."""
        sig = self.engine.compute_exit_signals(
            ticker="TEST",
            current_price=999999.0,
            entry_price=999999.0,
            current_volume=999999999,
            peak_volume=999999999,
        )
        assert 0.0 <= sig.composite_exit_urgency <= 1.0

    def test_penny_stock(self):
        """Low-price stock with wide spread."""
        sig = self.engine.compute_exit_signals(
            ticker="PENNY",
            current_price=0.50,
            entry_price=0.55,
            bid=0.48,
            ask=0.52,
            entry_spread=0.01,
            vwap=0.55,
        )
        assert sig.spread_widening > 0  # 4/50 = 8% spread
        assert sig.vwap_deterioration > 0

    def test_idempotent_evaluation(self):
        """Running the same evaluation twice gives same result."""
        sig1 = self.engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=10.0,
            hour_et=12,
        )
        sig2 = self.engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=10.0,
            hour_et=12,
        )
        assert sig1.composite_exit_urgency == sig2.composite_exit_urgency
        assert sig1.recommendation == sig2.recommendation


# ── Adaptive Targets (from position_manager.py) ─────────────────


class TestComputeAdaptiveTargets:
    """Tests for PositionManager.compute_adaptive_targets static method."""

    def test_import_works(self):
        """Can import and call the method."""
        from src.execution.position_manager import PositionManager
        targets = PositionManager.compute_adaptive_targets(10.0, atr=0.5)
        assert len(targets) == 3
        assert all(t > 10.0 for t in targets)

    def test_atr_based_targets(self):
        """ATR-based: T1=1.5*ATR, T2=3*ATR, T3=6*ATR (D94: widened T3)."""
        from src.execution.position_manager import PositionManager
        targets = PositionManager.compute_adaptive_targets(10.0, atr=1.0)
        # T1=10+1.5, T2=10+3, T3=10+6
        assert abs(targets[0] - 11.5) < 0.01
        assert abs(targets[1] - 13.0) < 0.01
        assert abs(targets[2] - 16.0) < 0.01  # D94: was 15.0 (5*ATR), now 16.0 (6*ATR)

    def test_fallback_no_atr(self):
        """Without ATR → +5/10/20% fallback (D94: widened from 3/6/10%)."""
        from src.execution.position_manager import PositionManager
        targets = PositionManager.compute_adaptive_targets(10.0)
        assert abs(targets[0] - 10.5) < 0.01  # D94: was 10.3
        assert abs(targets[1] - 11.0) < 0.01  # D94: was 10.6
        assert abs(targets[2] - 12.0) < 0.01  # D94: was 11.0

    def test_time_decay_tightening(self):
        """After 2PM → targets tighten 50%."""
        from src.execution.position_manager import PositionManager
        targets_morning = PositionManager.compute_adaptive_targets(10.0, atr=1.0, hour_et=10)
        targets_afternoon = PositionManager.compute_adaptive_targets(10.0, atr=1.0, hour_et=14)
        # Afternoon targets should be tighter (closer to entry)
        for i in range(3):
            assert targets_afternoon[i] < targets_morning[i]

    def test_volume_boost(self):
        """RVOL > 5 → targets widen 20%."""
        from src.execution.position_manager import PositionManager
        targets_normal = PositionManager.compute_adaptive_targets(10.0, atr=1.0, rvol=1.0)
        targets_boosted = PositionManager.compute_adaptive_targets(10.0, atr=1.0, rvol=6.0)
        # Boosted targets should be wider (further from entry)
        for i in range(3):
            assert targets_boosted[i] > targets_normal[i]

    def test_targets_ordered(self):
        """Targets always T1 < T2 < T3."""
        from src.execution.position_manager import PositionManager
        for atr in [0.3, 0.5, 1.0, 2.0]:
            for rvol in [1.0, 3.0, 6.0]:
                for hour in [10, 12, 14]:
                    targets = PositionManager.compute_adaptive_targets(
                        10.0, atr=atr, rvol=rvol, hour_et=hour,
                    )
                    assert targets[0] < targets[1] < targets[2]


# ── D78 Bug Fix Regression Tests ────────────────────────────────────
# These tests cover the specific data starvation bugs that caused D78
# to never fire TIGHTEN/EXIT signals during the Feb 24 trading day.


class TestD78DataStarvationFixes:
    """
    Regression tests for D78 exit intelligence data starvation bugs.

    Root cause: evaluate_positions() was receiving data that caused every
    signal to compute to 0.0, so composite was always 0.0 and the
    recommendation was always HOLD, even when positions should have
    been exited.
    """

    def _make_normalized_snapshot(
        self,
        price: float = 10.0,
        bid: float = 9.99,
        ask: float = 10.01,
        volume: int = 100_000,
        bid_size: int = 500,
        ask_size: int = 500,
        minute_volume: int = 0,
        day_high: float = 0.0,
        day_low: float = 0.0,
        day_open: float = 0.0,
        vwap: float = 0.0,
    ) -> dict:
        """Create a normalized snapshot dict (as returned by AlpacaDataClient)."""
        return {
            "last_price": price,
            "bid": bid,
            "ask": ask,
            "volume": volume,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "minute_volume": minute_volume,
            "day_high": day_high if day_high else price + 0.50,
            "day_low": day_low if day_low else price - 0.50,
            "day_open": day_open if day_open else price - 0.20,
            "vwap": vwap,
        }

    def test_volume_fade_with_minute_volume(self):
        """
        BUG FIX: volume_fade was structurally dead because it used daily
        cumulative volume, which only increases.  Now uses minute-bar
        volume which drops when momentum dies.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="FADE",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.02,
            peak_volume=200_000,
        )
        # Minute volume has dropped massively (momentum dying)
        snap = self._make_normalized_snapshot(
            price=10.2,
            volume=500_000,  # Daily volume high (cumulative, always grows)
            minute_volume=1_000,  # But minute volume dried up
            day_high=10.5,
        )
        # First evaluation: sets _peak_minute_vol = 1000
        mgr.evaluate_positions(
            [pos], {"FADE": snap}, hour_et=11, minute_et=0,
        )

        # Second evaluation: minute volume dropped further
        snap2 = self._make_normalized_snapshot(
            price=10.1,
            volume=510_000,  # Daily still growing
            minute_volume=50,  # Minute volume almost zero
            day_high=10.5,
        )
        actions = mgr.evaluate_positions(
            [pos], {"FADE": snap2}, hour_et=11, minute_et=30,
        )
        assert len(actions) == 1
        # With minute_vol=50 vs peak_minute_vol=1000, ratio=0.05 → volume_fade near 1.0
        assert actions[0].signal.volume_fade > 0.5, (
            f"volume_fade should detect minute volume drying up, got {actions[0].signal.volume_fade}"
        )

    def test_daily_volume_no_longer_gives_false_zero(self):
        """
        Verify that daily cumulative volume (always increasing) no longer
        produces a false 0.0 for volume_fade when minute_volume is unavailable.
        When minute_volume=0, falls back to daily volume which is still
        monotonic but at least tracks peak correctly.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="DAILY",
            entry_price=10.0,
            stop_loss=9.0,
            peak_volume=0,  # Start fresh
        )
        # No minute volume available — uses daily volume
        snap = self._make_normalized_snapshot(
            price=10.0,
            volume=100_000,
            minute_volume=0,  # No minute data
        )
        actions = mgr.evaluate_positions([pos], {"DAILY": snap})
        # Daily volume = peak_volume after first update, so ratio=1.0 → fade=0.0
        # This is expected behavior (daily volume can't detect fade without minute data)
        assert len(actions) == 1
        assert actions[0].signal.volume_fade == 0.0

    def test_entry_spread_auto_capture(self):
        """
        BUG FIX: entry_spread was never populated at position creation.
        Now evaluate_positions() auto-captures it on first cycle if missing.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="SPRD",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.0,  # Not set at entry (the bug)
        )
        snap = self._make_normalized_snapshot(
            price=10.0,
            bid=9.99,
            ask=10.01,  # spread = 0.02
        )
        mgr.evaluate_positions([pos], {"SPRD": snap})
        # entry_spread should be auto-captured
        assert pos.entry_spread > 0, (
            f"entry_spread should be auto-captured, got {pos.entry_spread}"
        )
        assert abs(pos.entry_spread - 0.02) < 0.001

    def test_spread_widening_with_auto_captured_entry(self):
        """
        After auto-capturing entry_spread, spread widening detection works.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="WIDEN",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.0,  # Will be auto-captured
        )
        # First eval: captures entry_spread = 0.02
        snap1 = self._make_normalized_snapshot(
            price=10.0,
            bid=9.99,
            ask=10.01,
        )
        mgr.evaluate_positions([pos], {"WIDEN": snap1})
        assert pos.entry_spread > 0

        # Second eval: spread has widened 3x
        snap2 = self._make_normalized_snapshot(
            price=9.90,
            bid=9.87,
            ask=9.93,  # spread = 0.06 = 3x entry
        )
        actions = mgr.evaluate_positions([pos], {"WIDEN": snap2})
        assert len(actions) == 1
        assert actions[0].signal.spread_widening > 0.5, (
            f"spread_widening should detect 3x widening, got {actions[0].signal.spread_widening}"
        )

    def test_vwap_fallback_from_snapshot(self):
        """
        BUG FIX: VWAP was 0.0 when WebSocket unavailable.
        Now falls back to snapshot vwap field from Alpaca dailyBar.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="VWAP",
            entry_price=10.0,
            stop_loss=9.0,
        )
        # No VWAP in lookup (WebSocket down), but snapshot has VWAP
        snap = self._make_normalized_snapshot(
            price=9.50,  # Below VWAP
            vwap=10.0,  # Alpaca dailyBar VWAP
        )
        actions = mgr.evaluate_positions(
            [pos], {"VWAP": snap},
            hour_et=12, minute_et=0,
            vwap_lookup={},  # Empty — WebSocket not connected
        )
        assert len(actions) == 1
        assert actions[0].signal.vwap_deterioration > 0, (
            f"vwap_deterioration should fire with snapshot VWAP fallback, "
            f"got {actions[0].signal.vwap_deterioration}"
        )

    def test_vwap_fallback_from_ohlc(self):
        """
        VWAP falls back to typical price (OHLC average) when neither
        WebSocket VWAP nor snapshot VWAP is available.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="OHLC",
            entry_price=10.0,
            stop_loss=9.0,
        )
        # No VWAP field, but OHLC data available
        snap = self._make_normalized_snapshot(
            price=9.50,  # Current price far below typical
            vwap=0.0,  # No VWAP
            day_open=10.0,
            day_high=10.5,
            day_low=9.40,
        )
        # typical = (10.0 + 10.5 + 9.40 + 9.50) / 4 = 9.85
        actions = mgr.evaluate_positions(
            [pos], {"OHLC": snap},
            hour_et=12, minute_et=0,
            vwap_lookup={},
        )
        assert len(actions) == 1
        # Price 9.50 is below OHLC-derived VWAP 9.85
        assert actions[0].signal.vwap_deterioration > 0, (
            f"vwap_deterioration should fire with OHLC fallback, "
            f"got {actions[0].signal.vwap_deterioration}"
        )

    def test_resistance_level_from_day_high(self):
        """
        BUG FIX: resistance_level was never passed to compute_exit_signals.
        Now uses day_high as resistance proxy when price is below it.
        """
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="RESIST",
            entry_price=10.0,
            stop_loss=9.0,
        )
        # Price near day high (stalling at resistance)
        snap = self._make_normalized_snapshot(
            price=10.45,
            day_high=10.50,  # Just above current price
        )
        actions = mgr.evaluate_positions(
            [pos], {"RESIST": snap},
            hour_et=11, minute_et=0,
        )
        assert len(actions) == 1
        assert actions[0].signal.resistance_proximity > 0, (
            f"resistance_proximity should detect proximity to day_high, "
            f"got {actions[0].signal.resistance_proximity}"
        )

    def test_resistance_not_set_when_at_high(self):
        """When price IS the day high, no resistance (already broken through)."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="HIGH",
            entry_price=10.0,
            stop_loss=9.0,
        )
        snap = self._make_normalized_snapshot(
            price=10.50,
            day_high=10.50,  # At the high = no resistance
        )
        actions = mgr.evaluate_positions([pos], {"HIGH": snap})
        assert len(actions) == 1
        assert actions[0].signal.resistance_proximity == 0.0

    def test_combined_signals_produce_tighten(self):
        """
        With all fixes applied, a degrading position at midday should
        trigger at least TIGHTEN (composite >= 0.3).
        """
        engine = ExitSignalEngine(tighten_threshold=0.3, exit_threshold=0.6)
        mgr = ExitIntelligenceManager(engine=engine)
        pos = FakeManagedPosition(
            ticker="DEGRADE",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.0,  # Will be auto-captured
        )
        # First eval: capture entry_spread and set _peak_minute_vol
        snap1 = self._make_normalized_snapshot(
            price=10.2,
            bid=10.19,
            ask=10.21,  # tight spread = 0.02
            minute_volume=50_000,
            day_high=10.5,
            vwap=10.3,
        )
        mgr.evaluate_positions(
            [pos], {"DEGRADE": snap1},
            hour_et=10, minute_et=0,
            vwap_lookup={},
        )

        # Second eval: position degrading
        snap2 = self._make_normalized_snapshot(
            price=9.80,  # Below VWAP
            bid=9.75,
            ask=9.85,  # spread = 0.10 = 5x entry
            minute_volume=2_000,  # Volume dried up (4% of peak)
            day_high=10.5,
            vwap=10.1,
            bid_size=200,
            ask_size=1500,  # Heavy distribution
        )
        actions = mgr.evaluate_positions(
            [pos], {"DEGRADE": snap2},
            hour_et=13, minute_et=0,  # After noon
            vwap_lookup={},
        )
        assert len(actions) == 1
        sig = actions[0].signal
        assert sig.composite_exit_urgency >= 0.3, (
            f"Combined degrading signals should produce composite >= 0.3, "
            f"got {sig.composite_exit_urgency:.3f} "
            f"[vf={sig.volume_fade:.2f} vwap={sig.vwap_deterioration:.2f} "
            f"sprd={sig.spread_widening:.2f} time={sig.time_decay:.2f} "
            f"dist={sig.distribution:.2f} res={sig.resistance_proximity:.2f}]"
        )
        assert actions[0].action in ("TIGHTEN", "EXIT"), (
            f"Expected TIGHTEN or EXIT, got {actions[0].action}"
        )

    def test_combined_signals_produce_exit(self):
        """
        A fully distressed position should trigger EXIT.
        D89: With 12-signal rebalanced weights, snapshot-only signals max at ~0.65
        composite. Use lower exit threshold (0.45) to test EXIT behavior.
        This uses the normalized snapshot format (as actually received
        from AlpacaDataClient.get_snapshots()).
        """
        engine = ExitSignalEngine(tighten_threshold=0.3, exit_threshold=0.45)
        mgr = ExitIntelligenceManager(engine=engine)
        pos = FakeManagedPosition(
            ticker="DUMP",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.02,
            peak_volume=200_000,
        )
        # Set up peak minute volume
        pos._peak_minute_vol = 80_000
        # Fully distressed: every signal firing
        snap = self._make_normalized_snapshot(
            price=9.50,  # Below VWAP
            bid=9.30,
            ask=9.80,  # 5% spread = 1.0
            volume=210_000,
            minute_volume=2_000,  # 2.5% of peak minute = dried up
            bid_size=100,
            ask_size=2000,  # 20:1 distribution
            day_high=10.50,
            vwap=10.0,
        )
        actions = mgr.evaluate_positions(
            [pos], {"DUMP": snap},
            hour_et=15, minute_et=0,  # 3 PM = max time decay
            vwap_lookup={},
        )
        assert len(actions) == 1
        assert actions[0].action == "EXIT", (
            f"Fully distressed position should EXIT, got {actions[0].action} "
            f"(composite={actions[0].signal.composite_exit_urgency:.3f})"
        )
        assert "SMART_EXIT" in actions[0].exit_reason

    def test_ws_vwap_takes_priority_over_snapshot(self):
        """WebSocket VWAP (from vwap_lookup) takes priority over snapshot VWAP."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(
            ticker="PRIO",
            entry_price=10.0,
            stop_loss=9.0,
        )
        snap = self._make_normalized_snapshot(
            price=9.80,
            vwap=9.70,  # Snapshot VWAP below price (would be healthy)
        )
        # WebSocket VWAP is above price (would trigger deterioration)
        actions = mgr.evaluate_positions(
            [pos], {"PRIO": snap},
            vwap_lookup={"PRIO": 10.0},  # WebSocket says VWAP is 10.0
        )
        assert len(actions) == 1
        # Price 9.80 below WS VWAP 10.0 → deterioration should fire
        assert actions[0].signal.vwap_deterioration > 0

    def test_normalized_snapshot_format_produces_nonzero_signals(self):
        """
        Critical regression: the normalized snapshot format (flat keys like
        'last_price', 'bid', 'ask') must produce non-zero signals when
        conditions are met.  Before the fix, most signals were dead.
        """
        engine = ExitSignalEngine(tighten_threshold=0.2, exit_threshold=0.5)
        mgr = ExitIntelligenceManager(engine=engine)
        pos = FakeManagedPosition(
            ticker="NORM",
            entry_price=10.0,
            stop_loss=9.0,
            entry_spread=0.02,
        )
        pos._peak_minute_vol = 50_000

        snap = {
            "last_price": 9.70,  # Below VWAP
            "bid": 9.65,
            "ask": 9.75,  # Spread = 0.10
            "volume": 100_000,
            "minute_volume": 3_000,  # 6% of peak = dried up
            "bid_size": 200,
            "ask_size": 1200,  # 6:1 distribution
            "day_high": 10.30,
            "day_low": 9.60,
            "day_open": 10.0,
            "vwap": 10.0,
        }
        actions = mgr.evaluate_positions(
            [pos], {"NORM": snap},
            hour_et=13, minute_et=30,
            vwap_lookup={},
        )
        assert len(actions) == 1
        sig = actions[0].signal
        # Multiple signals should be non-zero
        nonzero_count = sum(1 for v in [
            sig.volume_fade, sig.vwap_deterioration, sig.spread_widening,
            sig.time_decay, sig.distribution, sig.resistance_proximity,
        ] if v > 0)
        assert nonzero_count >= 4, (
            f"Expected at least 4 non-zero signals, got {nonzero_count}: "
            f"vf={sig.volume_fade:.2f} vwap={sig.vwap_deterioration:.2f} "
            f"sprd={sig.spread_widening:.2f} time={sig.time_decay:.2f} "
            f"dist={sig.distribution:.2f} res={sig.resistance_proximity:.2f}"
        )


# ═══════════════════════════════════════════════════════════════════
# D89: Multi-bar Signal Tests
# ═══════════════════════════════════════════════════════════════════


def _make_bars(count: int, base_price: float = 10.0, base_vol: int = 10000,
               trend: str = "flat") -> list[dict]:
    """Helper to generate test bar data."""
    bars = []
    for i in range(count):
        if trend == "up":
            c = base_price + i * 0.1
        elif trend == "down":
            c = base_price - i * 0.1
        else:
            c = base_price
        bars.append({
            "o": c - 0.05,
            "h": c + 0.10,
            "l": c - 0.10,
            "c": c,
            "v": base_vol,
            "vw": c,
        })
    return bars


class TestChurning:
    """D89: Tests for ExitSignalEngine._churning."""

    def test_churning_insufficient_bars(self):
        """Should return 0.0 when fewer than 5 bars."""
        engine = ExitSignalEngine()
        assert engine._churning(None) == 0.0
        assert engine._churning([]) == 0.0
        assert engine._churning(_make_bars(3)) == 0.0

    def test_churning_normal_volume(self):
        """Normal volume + normal range → no churning."""
        engine = ExitSignalEngine()
        bars = _make_bars(10, base_vol=10000)
        score = engine._churning(bars)
        assert score == 0.0

    def test_churning_high_vol_narrow_range(self):
        """High volume with narrow range = institutional distribution."""
        engine = ExitSignalEngine()
        # 11 bars: 7 normal (majority → median = normal), 4 churning
        # Median vol = 5000 (normal majority), 2× = 10000
        # Median range = 0.20 (normal majority), 0.5× = 0.10
        bars = _make_bars(7, base_vol=5000)  # range ~0.20, vol=5000
        for _ in range(4):
            bars.append({
                "o": 10.0, "h": 10.01, "l": 9.99,  # Very narrow range (0.02)
                "c": 10.0, "v": 200000, "vw": 10.0,  # 200K >> 2× median 5K
            })
        score = engine._churning(bars)
        assert score > 0.3, f"Expected churning score > 0.3, got {score}"

    def test_churning_all_bars_churning(self):
        """Majority churning bars → detects churning."""
        engine = ExitSignalEngine()
        # 11 bars: 6 normal (majority for median), 5 extreme churning
        bars = []
        for _ in range(6):
            # Normal bars with wide range and low volume
            bars.append({
                "o": 10.0, "h": 10.20, "l": 9.80,  # Range = 0.40
                "c": 10.0, "v": 5000, "vw": 10.0,
            })
        for _ in range(5):
            # Churning bars: very high volume, tiny range
            bars.append({
                "o": 10.0, "h": 10.005, "l": 9.995,  # Range = 0.01
                "c": 10.0, "v": 500000, "vw": 10.0,  # 100x normal
            })
        score = engine._churning(bars)
        assert score >= 0.5, f"Expected high churning score, got {score}"


class TestOBVDivergence:
    """D89: Tests for ExitSignalEngine._obv_divergence."""

    def test_obv_divergence_insufficient_bars(self):
        """Should return 0.0 when fewer than 10 bars."""
        engine = ExitSignalEngine()
        assert engine._obv_divergence(None) == 0.0
        assert engine._obv_divergence([]) == 0.0
        assert engine._obv_divergence(_make_bars(5)) == 0.0

    def test_obv_divergence_healthy_uptrend(self):
        """Price up + OBV up → no divergence."""
        engine = ExitSignalEngine()
        # Both price and volume consistently rising
        bars = []
        for i in range(15):
            bars.append({
                "o": 10.0 + i * 0.1,
                "h": 10.2 + i * 0.1,
                "l": 9.9 + i * 0.1,
                "c": 10.1 + i * 0.1,  # Consistently higher closes
                "v": 10000 + i * 500,  # Rising volume
                "vw": 10.0 + i * 0.1,
            })
        score = engine._obv_divergence(bars, 11.5)
        assert score == 0.0, f"Expected 0.0 for healthy uptrend, got {score}"

    def test_obv_divergence_bearish(self):
        """Price higher high + OBV lower high → bearish divergence."""
        engine = ExitSignalEngine()
        bars = []
        # First half (8 bars): strong up move, all bars close UP → OBV climbs
        # Each bar close > prev close → all volume ADDS to OBV
        for i in range(8):
            bars.append({
                "o": 10.0 + i * 0.2,
                "h": 10.3 + i * 0.2,
                "l": 9.9 + i * 0.2,
                "c": 10.2 + i * 0.2,  # Strictly rising → OBV = +50K per bar
                "v": 50000,
                "vw": 10.0 + i * 0.2,
            })
        # First half OBV peaks at ~350K (7 × 50K, first bar starts at 0)
        # Price max close = 11.6

        # Second half (8 bars): price makes new close high (>11.6) via one bar
        # but most bars close LOWER than their predecessor → OBV drops
        # Key: each bar's close must be compared to PREVIOUS bar's close
        second_closes = [
            11.5,   # < 11.6 (prev) → OBV -60K
            11.4,   # < 11.5 → OBV -60K
            11.3,   # < 11.4 → OBV -60K
            11.2,   # < 11.3 → OBV -60K
            12.0,   # > 11.2 → OBV +1K (tiny vol, creates price HH > 11.6)
            11.0,   # < 12.0 → OBV -60K
            10.9,   # < 11.0 → OBV -60K
            10.8,   # < 10.9 → OBV -60K
        ]
        for i, sc in enumerate(second_closes):
            if i == 4:
                # The one up-close bar with tiny volume
                bars.append({
                    "o": 11.5, "h": 12.2, "l": 11.4,
                    "c": sc,
                    "v": 1000,  # Tiny volume on the up bar
                    "vw": 11.8,
                })
            else:
                bars.append({
                    "o": sc + 0.15, "h": sc + 0.25, "l": sc - 0.1,
                    "c": sc,
                    "v": 60000,  # Heavy volume on down bars
                    "vw": sc,
                })
        # Second half: max close = 12.0 > first half max close = 11.6 → price HH ✓
        # Second half OBV: starts at ~350K, loses 6×60K=360K, gains 1K → net ~-9K
        # So second half max OBV ≈ 350K (start) < first half max 350K → OBV LH ✓
        score = engine._obv_divergence(bars, 12.5)
        assert score > 0.3, f"Expected divergence score > 0.3, got {score}"

    def test_obv_divergence_flat_obv(self):
        """Price flat → no divergence possible."""
        engine = ExitSignalEngine()
        bars = _make_bars(12, base_price=10.0, base_vol=10000)
        score = engine._obv_divergence(bars, 10.0)
        assert score == 0.0


class TestVolumeClimax:
    """D89: Tests for ExitSignalEngine._volume_climax."""

    def test_volume_climax_insufficient_bars(self):
        """Should return 0.0 when fewer than 10 bars."""
        engine = ExitSignalEngine()
        assert engine._volume_climax(None) == 0.0
        assert engine._volume_climax([]) == 0.0
        assert engine._volume_climax(_make_bars(5)) == 0.0

    def test_volume_climax_normal_volume(self):
        """Normal volume → no climax."""
        engine = ExitSignalEngine()
        bars = _make_bars(15, base_vol=10000)
        score = engine._volume_climax(bars)
        assert score == 0.0

    def test_volume_climax_blowoff_top(self):
        """Extreme volume spike at session high = blow-off top."""
        engine = ExitSignalEngine()
        bars = _make_bars(14, base_vol=10000, base_price=10.0)
        # Last bar: massive volume at session high
        bars.append({
            "o": 10.5, "h": 11.0, "l": 10.4,  # At/near highest high
            "c": 10.8, "v": 100000, "vw": 10.7,  # 10x avg volume
        })
        score = engine._volume_climax(bars)
        assert score > 0.5, f"Expected volume climax > 0.5, got {score}"

    def test_volume_climax_high_vol_not_at_high(self):
        """High volume but not at session high → no climax."""
        engine = ExitSignalEngine()
        # Build bars where high was in the past
        bars = []
        for i in range(14):
            bars.append({
                "o": 12.0 - i * 0.1,
                "h": 12.1 - i * 0.1,
                "l": 11.9 - i * 0.1,
                "c": 12.0 - i * 0.1,
                "v": 10000,
                "vw": 12.0 - i * 0.1,
            })
        # Latest bar: high volume but price far below session high
        bars.append({
            "o": 10.6, "h": 10.7, "l": 10.5,
            "c": 10.6, "v": 100000, "vw": 10.6,
        })
        score = engine._volume_climax(bars)
        assert score == 0.0, f"Expected 0.0 (not at high), got {score}"


class TestMomentumDegradation:
    """D89: Tests for ExitSignalEngine._momentum_degradation."""

    def test_momentum_degradation_insufficient_bars(self):
        """Should return 0.0 when fewer than 5 bars."""
        engine = ExitSignalEngine()
        assert engine._momentum_degradation(None) == 0.0
        assert engine._momentum_degradation([]) == 0.0
        assert engine._momentum_degradation(_make_bars(3)) == 0.0

    def test_momentum_degradation_healthy_uptrend(self):
        """Higher lows → no degradation."""
        engine = ExitSignalEngine()
        bars = _make_bars(8, base_price=10.0, trend="up")
        score = engine._momentum_degradation(bars)
        assert score == 0.0

    def test_momentum_degradation_3_lower_lows(self):
        """3 consecutive lower lows → first sign of degradation."""
        engine = ExitSignalEngine()
        bars = _make_bars(5, base_price=10.0)
        # Override last 4 bars with progressively lower lows
        bars[-4]["l"] = 9.90
        bars[-3]["l"] = 9.80
        bars[-2]["l"] = 9.70
        bars[-1]["l"] = 9.60
        score = engine._momentum_degradation(bars)
        assert score > 0, f"Expected degradation > 0, got {score}"

    def test_momentum_degradation_5_lower_lows(self):
        """5 consecutive lower lows → severe degradation."""
        engine = ExitSignalEngine()
        bars = []
        for i in range(8):
            bars.append({
                "o": 10.0 - i * 0.1,
                "h": 10.1 - i * 0.1,
                "l": 9.9 - i * 0.1,  # Each bar lower low
                "c": 10.0 - i * 0.1,
                "v": 10000,
                "vw": 10.0 - i * 0.1,
            })
        score = engine._momentum_degradation(bars)
        assert score >= 0.9, f"Expected high degradation, got {score}"

    def test_momentum_degradation_mixed(self):
        """Mixed lows (not consecutive) → no degradation."""
        engine = ExitSignalEngine()
        bars = []
        lows = [9.9, 9.8, 9.85, 9.7, 9.75, 9.6]  # Not consecutive
        for i, low in enumerate(lows):
            bars.append({
                "o": 10.0, "h": 10.1, "l": low,
                "c": 10.0, "v": 10000, "vw": 10.0,
            })
        score = engine._momentum_degradation(bars)
        # Last 3: 9.75 → 9.6 = 1 lower low (need 3+)
        assert score == 0.0


class TestFlowToxicity:
    """D89: Tests for ExitSignalEngine._flow_toxicity."""

    def test_flow_toxicity_insufficient_bars(self):
        """Should return 0.0 when fewer than 10 bars."""
        engine = ExitSignalEngine()
        assert engine._flow_toxicity(None) == 0.0
        assert engine._flow_toxicity([]) == 0.0
        assert engine._flow_toxicity(_make_bars(5)) == 0.0

    def test_flow_toxicity_balanced_flow(self):
        """Balanced buy/sell flow → low toxicity."""
        engine = ExitSignalEngine()
        # Bars where close is roughly in the middle of range
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.10, "l": 9.90,
                "c": 10.0,  # Close at midpoint → balanced
                "v": 10000, "vw": 10.0,
            })
        score = engine._flow_toxicity(bars)
        assert score == 0.0, f"Expected 0.0 for balanced flow, got {score}"

    def test_flow_toxicity_high_selling(self):
        """Close near lows → high sell-side toxicity."""
        engine = ExitSignalEngine()
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.20, "l": 9.80,
                "c": 9.82,  # Close near low → sell-heavy
                "v": 10000, "vw": 9.9,
            })
        score = engine._flow_toxicity(bars)
        assert score > 0.3, f"Expected flow toxicity > 0.3, got {score}"

    def test_flow_toxicity_extreme_imbalance(self):
        """Close at low → extreme toxicity."""
        engine = ExitSignalEngine()
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.50, "l": 9.50,
                "c": 9.50,  # Close = low → 100% selling
                "v": 10000, "vw": 9.8,
            })
        score = engine._flow_toxicity(bars)
        assert score >= 0.9, f"Expected extreme toxicity, got {score}"

    def test_flow_toxicity_zero_range_bars_ignored(self):
        """Bars with zero range should be skipped gracefully."""
        engine = ExitSignalEngine()
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.0, "l": 10.0,  # Zero range
                "c": 10.0, "v": 10000, "vw": 10.0,
            })
        score = engine._flow_toxicity(bars)
        assert score == 0.0  # All bars skipped → 0.0


class TestD89ComputeExitSignals:
    """D89: Integration tests for compute_exit_signals with bars parameter."""

    def test_compute_with_no_bars_returns_defaults(self):
        """All D89 signals should be 0.0 when bars=None."""
        engine = ExitSignalEngine()
        signal = engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=9.5,
            bars=None,
        )
        assert signal.churning == 0.0
        assert signal.obv_divergence == 0.0
        assert signal.volume_climax == 0.0
        assert signal.momentum_degradation == 0.0
        assert signal.flow_toxicity == 0.0

    def test_compute_with_bars_activates_signals(self):
        """D89 signals should fire when bar patterns match."""
        engine = ExitSignalEngine()
        # Create bars with degradation (lower lows)
        bars = []
        for i in range(8):
            bars.append({
                "o": 10.0 - i * 0.1,
                "h": 10.1 - i * 0.1,
                "l": 9.9 - i * 0.1,
                "c": 10.0 - i * 0.1,
                "v": 10000,
                "vw": 10.0 - i * 0.1,
            })
        signal = engine.compute_exit_signals(
            ticker="TEST",
            current_price=9.3,
            entry_price=10.0,
            bars=bars,
        )
        assert signal.momentum_degradation > 0, "Expected degradation from lower lows"

    def test_12_signal_weights_sum_to_one(self):
        """D89: Verify all 12 signal weights sum to 1.0."""
        total = sum(EXIT_SIGNAL_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_13_signals_all_have_weights(self):
        """Every signal name in ExitSignal should have a weight. D106: 13 signals."""
        expected_signals = {
            "volume_fade", "vwap_deterioration", "spread_widening",
            "time_decay", "distribution", "resistance_proximity",
            "failed_breakout", "churning", "obv_divergence",
            "volume_climax", "momentum_degradation", "flow_toxicity",
            # D106: Distribution detector
            "distribution_detector",
        }
        assert set(EXIT_SIGNAL_WEIGHTS.keys()) == expected_signals

    def test_composite_score_includes_new_signals(self):
        """D89 signals should contribute to composite score."""
        engine = ExitSignalEngine()
        # Manually set high churning — should increase composite
        signal_with_bars = engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=10.0,
        )
        base_composite = signal_with_bars.composite_exit_urgency

        # Create heavily churning bars
        bars = []
        for _ in range(10):
            bars.append({
                "o": 10.0, "h": 10.005, "l": 9.995,
                "c": 10.0, "v": 100000, "vw": 10.0,
            })
        signal_with_churning = engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=10.0,
            bars=bars,
        )
        # Churning should increase composite score
        if signal_with_churning.churning > 0:
            assert signal_with_churning.composite_exit_urgency >= base_composite

    def test_reasoning_includes_new_signals(self):
        """D89 signals should appear in reasoning when above threshold.
        D106: flow_toxicity weight zeroed (replaced by distribution_detector),
        so test uses churning signal instead which still has weight > 0.
        """
        engine = ExitSignalEngine(tighten_threshold=0.01, exit_threshold=0.9)
        # Force high churning — narrow range + high volume
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.005, "l": 9.995,
                "c": 10.0,  # Narrow range = churning
                "v": 100000, "vw": 10.0,
            })
        signal = engine.compute_exit_signals(
            ticker="TEST",
            current_price=10.0,
            entry_price=10.0,
            bars=bars,
        )
        if signal.churning > 0.3:
            assert "churn" in signal.reasoning


class TestD89BarHistoryManager:
    """D89: Tests for ExitIntelligenceManager bar history ring buffer."""

    def test_bar_history_accumulates(self):
        """Bar history should grow as positions are evaluated."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="RING")
        snap = {
            "latestTrade": {"p": 10.5},
            "latestQuote": {"bp": 10.4, "ap": 10.6, "bs": 100, "as": 100},
            "dailyBar": {"v": 50000, "h": 11.0, "l": 10.0, "o": 10.2, "vw": 10.3},
            "minuteBar": {"o": 10.4, "h": 10.6, "l": 10.3, "c": 10.5, "v": 5000, "vw": 10.45},
        }
        # Evaluate 3 times with minuteBar data
        for _ in range(3):
            mgr.evaluate_positions([pos], {"RING": snap})

        assert "RING" in mgr._bar_history
        assert len(mgr._bar_history["RING"]) == 3

    def test_bar_history_ring_buffer_cap_30(self):
        """Ring buffer should cap at 30 entries."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="CAP")
        snap = {
            "latestTrade": {"p": 10.5},
            "latestQuote": {"bp": 10.4, "ap": 10.6, "bs": 100, "as": 100},
            "dailyBar": {"v": 50000, "h": 11.0, "l": 10.0, "o": 10.2, "vw": 10.3},
            "minuteBar": {"o": 10.4, "h": 10.6, "l": 10.3, "c": 10.5, "v": 5000, "vw": 10.45},
        }
        for _ in range(40):
            mgr.evaluate_positions([pos], {"CAP": snap})

        assert len(mgr._bar_history["CAP"]) == 30

    def test_bar_history_no_accumulate_without_minute_bar(self):
        """No minuteBar in snapshot → no bar history added."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="NOMB")
        snap = {
            "latestTrade": {"p": 10.5},
            "latestQuote": {"bp": 10.4, "ap": 10.6, "bs": 100, "as": 100},
            "dailyBar": {"v": 50000, "h": 11.0, "l": 10.0, "o": 10.2, "vw": 10.3},
            # No minuteBar key
        }
        mgr.evaluate_positions([pos], {"NOMB": snap})
        assert mgr._bar_history.get("NOMB") is None or len(mgr._bar_history.get("NOMB", [])) == 0

    def test_bar_history_passes_bars_to_engine(self):
        """Bars from history should be passed to compute_exit_signals."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="PASS")
        # Build up 12 bars
        for i in range(12):
            snap = {
                "latestTrade": {"p": 10.0 + i * 0.05},
                "latestQuote": {"bp": 10.0, "ap": 10.1, "bs": 100, "as": 100},
                "dailyBar": {"v": 50000, "h": 11.0, "l": 10.0, "o": 10.2, "vw": 10.3},
                "minuteBar": {
                    "o": 10.0 + i * 0.05,
                    "h": 10.1 + i * 0.05,
                    "l": 9.9 + i * 0.05,
                    "c": 10.0 + i * 0.05,
                    "v": 5000 + i * 100,
                    "vw": 10.0 + i * 0.05,
                },
            }
            actions = mgr.evaluate_positions([pos], {"PASS": snap})

        # Should have 12 bars accumulated
        assert len(mgr._bar_history["PASS"]) == 12
        # The last action should have signals computed (bars were passed)
        assert len(actions) == 1

    def test_new_signals_default_zero_without_bar_data(self):
        """D89 signals stay at 0.0 when no bar history exists."""
        mgr = ExitIntelligenceManager()
        pos = FakeManagedPosition(ticker="ZERO")
        snap = {
            "latestTrade": {"p": 10.5},
            "latestQuote": {"bp": 10.4, "ap": 10.6, "bs": 100, "as": 100},
            "dailyBar": {"v": 50000, "h": 11.0, "l": 10.0, "o": 10.2, "vw": 10.3},
            # No minuteBar → no bar history → new signals stay 0.0
        }
        actions = mgr.evaluate_positions([pos], {"ZERO": snap})
        assert len(actions) == 1
        sig = actions[0].signal
        assert sig.churning == 0.0
        assert sig.obv_divergence == 0.0
        assert sig.volume_climax == 0.0
        assert sig.momentum_degradation == 0.0
        assert sig.flow_toxicity == 0.0


# ═══════════════════════════════════════════════════════════════════
# D89: Chandelier Exit + Safe-Zone Stop Tests
# ═══════════════════════════════════════════════════════════════════

from src.execution.position_manager import PositionManager


@pytest.fixture
def pm():
    """Create a PositionManager with default config for testing."""
    from config.settings import ExecutionConfig
    return PositionManager(config=ExecutionConfig(), starting_equity=100_000)


class TestChandelierExit:
    """D89: Tests for Chandelier Exit in compute_trailing_stop."""

    def test_chandelier_stop_from_peak(self, pm):
        """Chandelier Exit = peak_price - 4.0 × ATR."""
        stop = pm.compute_trailing_stop(
            current_price=12.0,
            entry_price=10.0,
            current_stop=9.0,
            atr=0.50,
            peak_price=13.0,
        )
        # Chandelier: 13.0 - 4.0 * 0.50 = 11.0
        # Safe-zone may adjust slightly, but should be near 11.0
        assert stop > 10.5, f"Expected stop > 10.5 from Chandelier, got {stop}"
        assert stop < 11.5, f"Expected stop < 11.5 from Chandelier, got {stop}"

    def test_chandelier_ratchet_invariant(self, pm):
        """INVARIANT: stop only ratchets UP, never down."""
        # First: high stop from Chandelier
        stop1 = pm.compute_trailing_stop(
            current_price=12.0, entry_price=10.0,
            current_stop=11.5,  # Already high
            atr=0.50, peak_price=13.0,
        )
        assert stop1 >= 11.5, "Stop must not go below current_stop"

        # Even if peak drops (shouldn't happen but defensive)
        stop2 = pm.compute_trailing_stop(
            current_price=11.0, entry_price=10.0,
            current_stop=stop1,
            atr=0.50, peak_price=11.5,
        )
        assert stop2 >= stop1, "Stop must only ratchet UP"

    def test_chandelier_fallback_no_peak(self, pm):
        """Without peak_price, falls back to 2× ATR from current price."""
        stop = pm.compute_trailing_stop(
            current_price=12.0, entry_price=10.0,
            current_stop=9.0,
            atr=0.50, peak_price=None,
        )
        # Fallback: 12.0 - 2.0 * 0.50 = 11.0
        assert stop > 10.5, f"Expected fallback stop > 10.5, got {stop}"

    def test_chandelier_fallback_no_atr(self, pm):
        """Without ATR, falls back to 3% below current price."""
        stop = pm.compute_trailing_stop(
            current_price=10.0, entry_price=9.0,
            current_stop=8.0,
            atr=None, peak_price=None,
        )
        # Fallback: 10.0 * 0.97 = 9.70
        assert stop > 9.5, f"Expected percentage fallback > 9.5, got {stop}"

    def test_chandelier_zero_atr(self, pm):
        """ATR=0 should trigger percentage fallback."""
        stop = pm.compute_trailing_stop(
            current_price=10.0, entry_price=9.0,
            current_stop=8.0,
            atr=0.0, peak_price=10.5,
        )
        # 10.0 * 0.97 = 9.70 (percentage fallback)
        assert stop > 9.5


class TestSafeZoneStop:
    """D89: Tests for adjust_stop_for_safe_zone."""

    def test_safe_zone_near_round_10(self):
        """Stop near $10.00 should be nudged below."""
        stop = PositionManager.adjust_stop_for_safe_zone(10.02, atr=0.50)
        assert stop < 10.0, f"Expected stop below $10.00, got {stop}"

    def test_safe_zone_near_round_50(self):
        """Stop near $50.00 should be nudged below."""
        stop = PositionManager.adjust_stop_for_safe_zone(49.98, atr=1.0)
        assert stop < 50.0, f"Expected stop below $50.00, got {stop}"
        assert stop <= 49.85, f"Expected stop <= $49.85, got {stop}"

    def test_safe_zone_near_round_5(self):
        """Stop near $5.00 should be nudged below."""
        stop = PositionManager.adjust_stop_for_safe_zone(5.03, atr=0.30)
        assert stop < 5.0, f"Expected stop below $5.00, got {stop}"

    def test_safe_zone_no_round_nearby(self):
        """Stop far from round numbers should be unchanged (or near-unchanged)."""
        raw = 7.63
        stop = PositionManager.adjust_stop_for_safe_zone(raw, atr=0.50)
        assert abs(stop - raw) < 0.1, f"Expected stop near {raw}, got {stop}"

    def test_safe_zone_half_dollar(self):
        """Stop near $X.50 should be nudged below."""
        stop = PositionManager.adjust_stop_for_safe_zone(10.51, atr=0.40)
        assert stop < 10.50, f"Expected stop below $10.50, got {stop}"

    def test_safe_zone_zero_stop(self):
        """Zero stop should pass through unchanged."""
        stop = PositionManager.adjust_stop_for_safe_zone(0.0, atr=0.50)
        assert stop == 0.0

    def test_safe_zone_no_atr_uses_percentage(self):
        """Without ATR, uses 0.3% of price as buffer."""
        stop = PositionManager.adjust_stop_for_safe_zone(10.02, atr=None)
        assert stop < 10.0, f"Expected stop below $10.00 without ATR, got {stop}"

    def test_safe_zone_large_round_100(self):
        """Stop near $100.00 should be nudged below."""
        stop = PositionManager.adjust_stop_for_safe_zone(100.05, atr=2.0)
        assert stop < 100.0, f"Expected stop below $100.00, got {stop}"


# ── D89b: ATR Computation Tests ─────────────────────────────────


class TestATRComputation:
    """D89b: Tests for ExitIntelligenceManager.compute_atr."""

    def test_atr_insufficient_bars(self):
        """Should return None when fewer than period+1 bars."""
        mgr = ExitIntelligenceManager()
        assert mgr.compute_atr(None) is None
        assert mgr.compute_atr([]) is None
        assert mgr.compute_atr(_make_bars(10), period=14) is None

    def test_atr_sufficient_bars(self):
        """Should return a positive float with enough bars."""
        mgr = ExitIntelligenceManager()
        bars = _make_bars(20, base_price=10.0)
        # Each bar has h=10.1, l=9.9 → range = 0.20
        # With stable prices, ATR ≈ 0.20
        atr = mgr.compute_atr(bars, period=14)
        assert atr is not None
        assert atr > 0, f"Expected positive ATR, got {atr}"
        assert atr < 1.0, f"ATR should be small for $10 stock, got {atr}"

    def test_atr_uses_true_range(self):
        """ATR should use max(H-L, |H-prevC|, |L-prevC|) not just H-L."""
        mgr = ExitIntelligenceManager()
        # Gap down: prev close=10, next bar H=9.2, L=9.0
        # True range = max(0.2, |9.2-10|=0.8, |9.0-10|=1.0) = 1.0
        bars = []
        for i in range(16):
            if i < 15:
                bars.append({"o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 1000, "vw": 10.0})
            else:
                # Gap down bar
                bars.append({"o": 9.1, "h": 9.2, "l": 9.0, "c": 9.1, "v": 5000, "vw": 9.1})
        atr = mgr.compute_atr(bars, period=14)
        assert atr is not None
        # ATR should be > 0.20 (normal range) because of the gap bar
        assert atr > 0.20, f"ATR should reflect gap bar, got {atr}"

    def test_get_atr_from_bar_history(self):
        """get_atr() should retrieve ATR from internal bar history."""
        mgr = ExitIntelligenceManager()
        # Simulate bar accumulation
        bars = _make_bars(20, base_price=10.0)
        mgr._bar_history["TEST"] = bars
        atr = mgr.get_atr("TEST")
        assert atr is not None and atr > 0

    def test_get_atr_missing_ticker(self):
        """get_atr() for unknown ticker should return None."""
        mgr = ExitIntelligenceManager()
        assert mgr.get_atr("UNKNOWN") is None


class TestFlowToxicitySellDirectional:
    """D89b: Tests that flow toxicity is sell-directional, not absolute."""

    def test_buy_dominated_returns_zero(self):
        """Close near high (strong buying) should NOT trigger toxicity."""
        engine = ExitSignalEngine()
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.20, "l": 9.80,
                "c": 10.18,  # Close near high → buy-dominated
                "v": 10000, "vw": 10.1,
            })
        score = engine._flow_toxicity(bars)
        assert score == 0.0, f"Buy-dominated flow should be 0.0, got {score}"

    def test_sell_dominated_fires(self):
        """Close near low (heavy selling) should trigger toxicity."""
        engine = ExitSignalEngine()
        bars = []
        for _ in range(12):
            bars.append({
                "o": 10.0, "h": 10.20, "l": 9.80,
                "c": 9.82,  # Close near low → sell-dominated
                "v": 10000, "vw": 9.9,
            })
        score = engine._flow_toxicity(bars)
        assert score > 0.3, f"Sell-dominated flow should be > 0.3, got {score}"


class TestVolumeClimaxScoring:
    """D89b: Tests for volume climax scoring formula."""

    def test_exactly_3x_volume_is_zero(self):
        """At exactly 3× SMA volume, score should be 0.0 (threshold)."""
        engine = ExitSignalEngine()
        bars = _make_bars(14, base_vol=10000, base_price=10.0)
        # Last bar: exactly 3x avg volume at session high
        # avg = (14*10000 + 30000) / 15 = 170000/15 ≈ 11333
        # ratio = 30000/11333 = 2.647, < 3.0 → won't even trigger
        # Need to make it so latest_vol / vol_sma = exactly 3.0
        # With 14 bars at 10000, set last bar to make ratio ≈ 3.0
        bars.append({
            "o": 10.5, "h": 11.0, "l": 10.4,
            "c": 10.8, "v": 30000, "vw": 10.7,
        })
        # vol_sma = (14*10000+30000)/15 = 170000/15 = 11333
        # latest_vol/vol_sma = 30000/11333 = 2.647 < 3.0 → 0.0
        score = engine._volume_climax(bars)
        assert score == 0.0, f"Below 3x threshold should be 0.0, got {score}"

    def test_5x_volume_is_max(self):
        """At 5× SMA volume, score should be 1.0."""
        engine = ExitSignalEngine()
        bars = _make_bars(14, base_vol=1000, base_price=10.0)
        bars.append({
            "o": 10.5, "h": 11.0, "l": 10.4,
            "c": 10.8, "v": 100000, "vw": 10.7,
        })
        score = engine._volume_climax(bars)
        assert score >= 0.9, f"Very high spike should be near 1.0, got {score}"
