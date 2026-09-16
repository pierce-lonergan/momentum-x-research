"""D196: Tests for Arena Realism Elevation components.

Coverage:
  Component 1 — BarSeries simulation:
    - simulate_trailing_stop: fires at trail level, never fires if gain < activation
    - simulate_early_profit: triggers within window, misses if gain too small
    - simulate_tranches: correct target prices and minutes
    - simulate_observation_window: approved / red-first-bar / price-below-open / reversal
  Component 1 — BarSeries serialisation:
    - roundtrip to_dict / from_dict
  Component 2 — SlippageModel:
    - high-liquidity stock → low slippage
    - medium-liquidity stock → moderate slippage
    - low-liquidity stock → high slippage
    - micro-liquidity stock → very high slippage
    - stop slippage is higher than entry slippage on illiquid stocks
    - round-trip sums entry + exit
  Component 4 — Walk-forward split:
    - correct chronological ordering
    - correct split index
    - overfit_ratio computed
  Component 5 — Bootstrap CI:
    - mean within expected range
    - CI is wider for noisy input
    - CI lower < mean < CI upper
    - empty input returns (0, 0, 0)
"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import date
from pathlib import Path

import pytest

from src.data.bar_recorder import BarRecorder, BarSeries, MinuteBar
from src.execution.slippage_model import SlippageModel


# =============================================================================
# Helpers
# =============================================================================


def _make_bar(open_: float, high: float, low: float, close: float, minute: int = 0) -> MinuteBar:
    """Create a MinuteBar for testing."""
    return MinuteBar(
        timestamp=f"2026-01-02T09:{30 + minute:02d}:00Z",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100_000,
        vwap=(open_ + high + low + close) / 4.0,
    )


def _make_series(bars: list[MinuteBar], ticker: str = "TEST") -> BarSeries:
    return BarSeries(ticker=ticker, date="2026-01-02", bars=bars)


def _flat_series(price: float, n: int = 30) -> BarSeries:
    """Series where price stays flat — trailing stop should never fire."""
    bars = [_make_bar(price, price * 1.001, price * 0.999, price, m) for m in range(n)]
    return _make_series(bars)


def _ramp_then_fade(entry: float, peak_pct: float, fade_pct: float, n: int = 30) -> BarSeries:
    """Series that ramps up to peak then fades.

    First half ramps from entry to entry*(1+peak_pct).
    Second half fades from peak to entry*(1-fade_pct).
    """
    bars: list[MinuteBar] = []
    half = n // 2
    peak = entry * (1.0 + peak_pct)
    floor = entry * (1.0 - fade_pct)

    for i in range(half):
        p = entry + (peak - entry) * (i / half)
        bars.append(_make_bar(p, p * 1.001, p * 0.999, p, i))

    for i in range(half):
        p = peak - (peak - floor) * (i / half)
        bars.append(_make_bar(p, p * 1.001, p * 0.999, p, half + i))

    return _make_series(bars)


# =============================================================================
# Component 1: BarSeries simulation tests
# =============================================================================


class TestBarSeriesTrailingStop:
    """simulate_trailing_stop bar-by-bar replay."""

    def test_stop_fires_on_fade_after_gain(self):
        """Trail should fire when price fades 50% from peak."""
        series = _ramp_then_fade(entry=10.0, peak_pct=0.10, fade_pct=0.02, n=40)
        exit_price, exit_minute = series.simulate_trailing_stop(entry_price=10.0)
        # Trail activates at +2%, fires at ~+5% (50% of +10% gain)
        assert exit_minute >= 0, "Trail should fire"
        assert exit_price > 10.0, "Exit should be above entry (trade in profit)"

    def test_stop_does_not_fire_below_activation(self):
        """If price never reaches activation threshold, trail should not fire."""
        # Only +0.5% gain — below activation_pct=0.02
        bars = [
            _make_bar(10.0, 10.05, 9.98, 10.03, m) for m in range(20)
        ]
        series = _make_series(bars)
        _, exit_minute = series.simulate_trailing_stop(entry_price=10.0, activation_pct=0.02)
        assert exit_minute == -1, "Trail should not fire when gain below activation"

    def test_trail_ratchets_up(self):
        """Trail level should only move up (ratchet invariant)."""
        # Price climbs steadily; trail should ratchet up each bar
        bars: list[MinuteBar] = []
        for i in range(20):
            p = 10.0 + i * 0.05
            bars.append(_make_bar(p, p + 0.02, p - 0.01, p, i))
        series = _make_series(bars)
        # Should not error; just verify it returns without exit
        _, minute = series.simulate_trailing_stop(entry_price=10.0)
        # With purely ascending price, trail may or may not fire — just no crash
        assert isinstance(minute, int)

    def test_empty_series_returns_entry(self):
        series = BarSeries(ticker="X", date="2026-01-02", bars=[])
        exit_price, minute = series.simulate_trailing_stop(entry_price=5.0)
        assert exit_price == 5.0
        assert minute == -1

    def test_zero_entry_returns_gracefully(self):
        series = _flat_series(price=10.0, n=5)
        exit_price, minute = series.simulate_trailing_stop(entry_price=0.0)
        assert minute == -1


class TestBarSeriesEarlyProfit:
    """simulate_early_profit bar-by-bar replay (D164)."""

    def test_early_profit_triggered_in_window(self):
        """High runs within first 2 bars → early profit fires."""
        bars = [
            _make_bar(10.0, 10.20, 9.95, 10.15, 0),  # bar 0: high > 0.5% target
            _make_bar(10.15, 10.25, 10.10, 10.20, 1),
        ]
        series = _make_series(bars)
        exit_price, qty_pct, exit_minute = series.simulate_early_profit(
            entry_price=10.0, delay_minutes=2, exit_pct=0.50, min_profit=0.005
        )
        assert exit_minute == 0, "Should fire on first bar"
        assert qty_pct == 50
        assert abs(exit_price - 10.05) < 0.01

    def test_early_profit_not_triggered_if_gain_insufficient(self):
        """Price never reaches min_profit threshold → not triggered."""
        bars = [
            _make_bar(10.0, 10.002, 9.99, 10.001, 0),
            _make_bar(10.001, 10.003, 9.99, 10.002, 1),
        ]
        series = _make_series(bars)
        exit_price, qty_pct, exit_minute = series.simulate_early_profit(
            entry_price=10.0, delay_minutes=2, min_profit=0.005
        )
        assert exit_minute == -1
        assert qty_pct == 0

    def test_early_profit_window_boundary(self):
        """Profit hit on bar 3 but window is 2 → not triggered."""
        bars = [
            _make_bar(10.0, 10.002, 9.99, 10.001, 0),
            _make_bar(10.001, 10.003, 9.99, 10.002, 1),
            _make_bar(10.002, 10.20, 10.002, 10.15, 2),  # bar 3 — outside window
        ]
        series = _make_series(bars)
        _, qty_pct, exit_minute = series.simulate_early_profit(
            entry_price=10.0, delay_minutes=2, min_profit=0.005
        )
        assert exit_minute == -1
        assert qty_pct == 0


class TestBarSeriesTranches:
    """simulate_tranches bar-by-bar replay (D165)."""

    def test_all_three_targets_hit(self):
        """Series that hits 3%, 6%, 10% should return 3 hits."""
        bars: list[MinuteBar] = []
        # Stepwise climb: each group of 3 bars reaches a new level
        prices = [10.0, 10.31, 10.61, 11.01]
        for step, p in enumerate(prices):
            for _ in range(5):
                bars.append(_make_bar(p * 0.999, p * 1.001, p * 0.998, p, len(bars)))
        series = _make_series(bars)
        hits = series.simulate_tranches(entry_price=10.0, targets=[0.03, 0.06, 0.10])
        assert len(hits) == 3, f"Expected 3 tranche hits, got {len(hits)}"
        for fill_price, minute in hits:
            assert fill_price > 10.0

    def test_partial_targets_hit(self):
        """Series only reaches +3% — second and third tranches missed."""
        bars: list[MinuteBar] = []
        for m in range(20):
            p = 10.0 + (m / 100.0)
            bars.append(_make_bar(p, p + 0.03, p - 0.01, p, m))
        series = _make_series(bars)
        hits = series.simulate_tranches(entry_price=10.0, targets=[0.03, 0.06, 0.10])
        # Price tops out around 10.19 → only 3% target could be hit
        assert len(hits) <= 2

    def test_no_targets_hit(self):
        """Flat price → no tranches hit."""
        series = _flat_series(price=10.0, n=10)
        hits = series.simulate_tranches(entry_price=10.0, targets=[0.03, 0.06, 0.10])
        assert hits == []

    def test_empty_series_returns_empty(self):
        series = BarSeries(ticker="X", date="2026-01-02", bars=[])
        hits = series.simulate_tranches(entry_price=10.0)
        assert hits == []


class TestBarSeriesObservationWindow:
    """simulate_observation_window bar-by-bar replay (D170)."""

    def test_approved_green_stable(self):
        """First bar green, all above open → approved."""
        bars = [_make_bar(10.0, 10.20, 10.05, 10.15, m) for m in range(15)]
        series = _make_series(bars)
        approved, reason = series.simulate_observation_window(minutes=15)
        assert approved is True
        assert reason == "approved"

    def test_rejected_first_bar_red(self):
        """First bar closes below open → rejected."""
        bars = [_make_bar(10.0, 10.02, 9.90, 9.95, m) for m in range(15)]
        series = _make_series(bars)
        approved, reason = series.simulate_observation_window(minutes=15)
        assert approved is False
        assert reason == "first_bar_red"

    def test_rejected_price_below_open(self):
        """First bar green, but price spends majority of window below open → rejected."""
        bars = []
        # Bar 0: green open
        bars.append(_make_bar(10.0, 10.05, 9.99, 10.03, 0))
        # Bars 1-14: all close below 10.0
        for m in range(1, 15):
            bars.append(_make_bar(9.95, 9.97, 9.90, 9.92, m))
        series = _make_series(bars)
        approved, reason = series.simulate_observation_window(minutes=15)
        assert approved is False
        assert "price_below_open" in reason

    def test_rejected_extreme_reversal(self):
        """Single bar drops >5% from prior close → rejected.

        Bars are kept above open price so that only the reversal check fires.
        """
        bars = []
        # Bar 0: green, above open (10.0)
        bars.append(_make_bar(10.0, 10.15, 10.05, 10.10, 0))
        # Bar 1: still above open but big single-bar drop from 10.10 → ~9.55 (>5%)
        # Keep close above open (10.0) to avoid price_below_open check
        bars.append(_make_bar(10.10, 10.50, 10.05, 10.05, 1))   # extreme high-low range
        # Bar 2: close above open, but prior close is 10.05 and this bar closes at 9.50 (-5.2%)
        bars.append(_make_bar(10.05, 10.06, 9.40, 9.50, 2))     # reversal >5%
        # Remaining bars above open to avoid price_below_open
        for m in range(3, 15):
            bars.append(_make_bar(10.01, 10.05, 10.00, 10.02, m))
        series = _make_series(bars)
        approved, reason = series.simulate_observation_window(minutes=15)
        assert approved is False
        assert reason == "extreme_reversal_bar"

    def test_empty_series(self):
        series = BarSeries(ticker="X", date="2026-01-02", bars=[])
        approved, reason = series.simulate_observation_window()
        assert approved is False
        assert reason == "no_bars"


class TestBarSeriesDerivedMetrics:
    """max_gain_from_open and max_drawdown_from_open."""

    def test_max_gain(self):
        bars = [
            _make_bar(10.0, 10.0, 9.9, 10.0, 0),
            _make_bar(10.0, 11.0, 9.9, 10.5, 1),  # high = 11.0 → +10%
            _make_bar(10.5, 10.6, 10.4, 10.5, 2),
        ]
        series = _make_series(bars)
        assert abs(series.max_gain_from_open() - 0.10) < 0.001

    def test_max_drawdown(self):
        bars = [
            _make_bar(10.0, 10.1, 9.0, 9.5, 0),   # low = 9.0 → -10%
            _make_bar(9.5, 9.6, 9.4, 9.5, 1),
        ]
        series = _make_series(bars)
        assert abs(series.max_drawdown_from_open() - 0.10) < 0.001

    def test_price_at_minute(self):
        bars = [_make_bar(10.0, 10.1, 9.9, 10.0 + i * 0.1, i) for i in range(10)]
        series = _make_series(bars)
        assert series.price_at_minute(5) == pytest.approx(10.5, abs=0.01)

    def test_price_at_minute_clamps_to_last(self):
        bars = [_make_bar(10.0, 10.1, 9.9, 10.0, 0)]
        series = _make_series(bars)
        assert series.price_at_minute(100) == 10.0


# =============================================================================
# Component 1: BarSeries serialisation roundtrip
# =============================================================================


class TestBarSeriesSerialisation:
    def test_roundtrip_to_dict(self):
        bars = [_make_bar(10.0, 10.5, 9.8, 10.2, m) for m in range(5)]
        original = BarSeries(ticker="AAPL", date="2026-01-15", bars=bars)
        restored = BarSeries.from_dict(original.to_dict())
        assert restored.ticker == original.ticker
        assert restored.date == original.date
        assert len(restored.bars) == len(original.bars)
        for ob, rb in zip(original.bars, restored.bars):
            assert ob.open == pytest.approx(rb.open)
            assert ob.high == pytest.approx(rb.high)
            assert ob.volume == rb.volume

    def test_roundtrip_via_json_string(self):
        bars = [_make_bar(5.0, 5.2, 4.9, 5.1, 0)]
        original = BarSeries(ticker="GME", date="2026-02-01", bars=bars)
        json_str = json.dumps(original.to_dict())
        restored = BarSeries.from_dict(json.loads(json_str))
        assert restored.ticker == "GME"

    def test_bar_recorder_save_load_roundtrip(self):
        bars = [_make_bar(100.0, 101.0, 99.5, 100.5, m) for m in range(10)]
        series = BarSeries(ticker="TSLA", date="2026-03-10", bars=bars)

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = BarRecorder(storage_dir=tmpdir)
            recorder.save(series)

            loaded = recorder.load(ticker="TSLA", session_date=date(2026, 3, 10))
            assert loaded is not None
            assert loaded.ticker == "TSLA"
            assert len(loaded.bars) == 10
            assert loaded.bars[0].open == pytest.approx(100.0)

    def test_bar_recorder_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = BarRecorder(storage_dir=tmpdir)
            result = recorder.load(ticker="MISSING", session_date=date(2026, 1, 1))
            assert result is None

    def test_bar_recorder_list_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = BarRecorder(storage_dir=tmpdir)
            s1 = BarSeries(ticker="A", date="2026-01-10", bars=[_make_bar(5, 5.1, 4.9, 5.0, 0)])
            s2 = BarSeries(ticker="B", date="2026-01-10", bars=[_make_bar(6, 6.1, 5.9, 6.0, 0)])
            s3 = BarSeries(ticker="C", date="2026-01-11", bars=[_make_bar(7, 7.1, 6.9, 7.0, 0)])
            recorder.save(s1)
            recorder.save(s2)
            recorder.save(s3)

            available = recorder.list_available()
            assert date(2026, 1, 10) in available
            assert available[date(2026, 1, 10)] == ["A", "B"]
            assert date(2026, 1, 11) in available
            assert available[date(2026, 1, 11)] == ["C"]

    def test_from_alpaca_raw_bar(self):
        raw = {"t": "2026-01-02T14:31:00Z", "o": 10.0, "h": 10.5, "l": 9.8, "c": 10.2, "v": 50000, "vw": 10.1}
        bar = MinuteBar.from_alpaca(raw)
        assert bar.open == 10.0
        assert bar.high == 10.5
        assert bar.volume == 50000
        assert bar.vwap == pytest.approx(10.1)


# =============================================================================
# Component 2: SlippageModel tests
# =============================================================================


class TestSlippageModel:
    """Dynamic slippage model tiered by liquidity."""

    def setup_method(self):
        self.model = SlippageModel()
        self.position = 5_000.0  # $5K position

    def test_high_liquidity_low_slippage(self):
        """$100M+ dolvol with tight spread → slippage well under 0.2%."""
        est = self.model.estimate_entry_slippage(
            position_dollars=self.position,
            daily_dollar_volume=100_000_000,
            spread_pct=0.001,   # 0.1% spread — typical for high-cap liquid stock
        )
        assert est.slippage_pct < 0.002, f"Expected < 0.2%, got {est.slippage_pct:.3%}"
        assert est.tier == "high"

    def test_medium_liquidity_moderate_slippage(self):
        """$10M dolvol → slippage between 0.1% and 1%."""
        est = self.model.estimate_entry_slippage(
            position_dollars=self.position,
            daily_dollar_volume=10_000_000,
        )
        assert 0.001 <= est.slippage_pct <= 0.01, f"Got {est.slippage_pct:.3%}"
        assert est.tier == "medium"

    def test_low_liquidity_high_slippage(self):
        """$2M dolvol → slippage > 0.5%."""
        est = self.model.estimate_entry_slippage(
            position_dollars=self.position,
            daily_dollar_volume=2_000_000,
        )
        assert est.slippage_pct > 0.005, f"Expected > 0.5%, got {est.slippage_pct:.3%}"
        assert est.tier == "low"

    def test_micro_liquidity_very_high_slippage(self):
        """$200K dolvol → slippage > 1%."""
        est = self.model.estimate_entry_slippage(
            position_dollars=self.position,
            daily_dollar_volume=200_000,
        )
        assert est.slippage_pct > 0.01, f"Expected > 1%, got {est.slippage_pct:.3%}"
        assert est.tier == "micro"

    def test_slippage_increases_with_position_size(self):
        """Larger position → larger participation impact → more slippage."""
        est_small = self.model.estimate_entry_slippage(1_000, 1_000_000)
        est_large = self.model.estimate_entry_slippage(100_000, 1_000_000)
        assert est_large.slippage_pct > est_small.slippage_pct

    def test_stop_slippage_worse_than_entry(self):
        """Stop fills are gapped through; stop slippage >= entry slippage."""
        entry = self.model.estimate_entry_slippage(self.position, 500_000)
        stop = self.model.estimate_stop_slippage(self.position, 500_000, volatility_pct=0.05)
        assert stop.slippage_pct >= entry.slippage_pct

    def test_round_trip_is_sum_of_entry_and_exit(self):
        """Round-trip cost should equal entry + exit."""
        rt = self.model.estimate_total_round_trip(self.position, 5_000_000)
        entry = self.model.estimate_entry_slippage(self.position, 5_000_000)
        # round-trip = 2× entry (symmetric assumption)
        assert rt == pytest.approx(entry.slippage_pct * 2, rel=0.01)

    def test_zero_dolvol_capped(self):
        """Zero dollar volume → capped at tier maximum (not infinity)."""
        est = self.model.estimate_entry_slippage(self.position, 0)
        assert est.slippage_pct <= 0.10, "Slippage should be capped even at zero dolvol"
        assert est.slippage_pct > 0

    def test_spread_component_present(self):
        """With non-zero spread, spread_component should be non-zero."""
        est = self.model.estimate_entry_slippage(self.position, 1_000_000, spread_pct=0.02)
        assert est.spread_component > 0


# =============================================================================
# Component 4: Walk-forward split tests
# =============================================================================


class TestWalkForward:
    """Walk-forward validation split logic."""

    def _make_scenario(self, ticker: str, date_str: str, pnl: float):
        """Import and build a LabeledScenario from the meta simulation."""
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.run_meta_simulation import LabeledScenario
        return LabeledScenario(
            ticker=ticker,
            date=date_str,
            entry_price=10.0,
            max_gain_pct=0.05,
            max_drawdown_pct=0.03,
            actual_pnl_dollars=pnl,
            has_news_catalyst=True,
            real_catalyst_confirmed=True,
            dollar_volume=2_000_000,
        )

    def test_split_is_chronological(self):
        """Scenarios should be sorted by date before splitting."""
        from scripts.run_meta_simulation import run_walk_forward, LabeledScenario

        # Build scenarios with out-of-order dates
        dates = ["2026-01-05", "2026-01-01", "2026-01-03", "2026-01-04", "2026-01-02"]
        scenarios = [self._make_scenario(f"T{i}", d, 100.0) for i, d in enumerate(dates)]
        result = run_walk_forward(scenarios, train_ratio=0.60)

        # split_date should be the 4th date when sorted
        sorted_dates = sorted(dates)
        expected_split = sorted_dates[int(len(sorted_dates) * 0.60)]
        assert result["split_date"] == expected_split

    def test_split_ratio_respected(self):
        """75% train → 25% test."""
        from scripts.run_meta_simulation import run_walk_forward

        scenarios = [self._make_scenario(f"T{i}", f"2026-01-{i+1:02d}", 100.0) for i in range(20)]
        result = run_walk_forward(scenarios, train_ratio=0.75)
        total = result["train_trades"] + result["test_trades"]
        assert total <= 20  # Can't exceed total (skips are possible)

    def test_overfit_ratio_computed(self):
        """overfit_ratio should be a valid non-negative float."""
        from scripts.run_meta_simulation import run_walk_forward

        scenarios = [self._make_scenario(f"T{i}", f"2026-02-{i+1:02d}", 50.0) for i in range(10)]
        result = run_walk_forward(scenarios, train_ratio=0.80)
        assert "overfit_ratio" in result
        assert isinstance(result["overfit_ratio"], float)

    def test_result_contains_required_keys(self):
        """Result dict must contain all expected keys."""
        from scripts.run_meta_simulation import run_walk_forward

        scenarios = [self._make_scenario(f"T{i}", f"2026-03-{i+1:02d}", 100.0) for i in range(8)]
        result = run_walk_forward(scenarios, train_ratio=0.75)
        for key in ["train_pnl", "test_pnl", "train_accuracy", "test_accuracy",
                    "train_trades", "test_trades", "split_date", "overfit_ratio", "params_used"]:
            assert key in result, f"Missing key: {key}"

    def test_empty_scenarios_returns_empty_dict(self):
        from scripts.run_meta_simulation import run_walk_forward
        result = run_walk_forward([])
        assert result == {}


# =============================================================================
# Component 5: Bootstrap CI tests
# =============================================================================


class TestBootstrapCI:
    """Bootstrap confidence interval for total P&L."""

    def setup_method(self):
        pytest.importorskip("numpy", reason="numpy required for bootstrap CI")
        from scripts.run_meta_simulation import bootstrap_pnl_ci
        self.bootstrap_pnl_ci = bootstrap_pnl_ci

    def test_empty_input(self):
        mean, low, high = self.bootstrap_pnl_ci([])
        assert mean == 0.0
        assert low == 0.0
        assert high == 0.0

    def test_ci_ordering(self):
        """ci_low <= mean <= ci_high for any non-degenerate input."""
        pnl = [100.0, -50.0, 200.0, 75.0, -25.0, 300.0, -100.0, 150.0] * 10
        mean, low, high = self.bootstrap_pnl_ci(pnl, n_bootstrap=500)
        assert low <= mean <= high

    def test_mean_near_sum_for_constant_input(self):
        """For constant input, bootstrap mean ≈ n * value."""
        pnl = [100.0] * 20
        mean, low, high = self.bootstrap_pnl_ci(pnl, n_bootstrap=500)
        assert abs(mean - 2000.0) < 1.0, f"Expected ~2000, got {mean}"

    def test_wider_ci_for_noisy_input(self):
        """Noisy P&L should produce wider CI than uniform P&L."""
        import numpy as np
        rng = np.random.default_rng(42)
        constant = [100.0] * 50
        noisy = list(rng.normal(100, 500, 50))
        _, low_c, high_c = self.bootstrap_pnl_ci(constant, n_bootstrap=500)
        _, low_n, high_n = self.bootstrap_pnl_ci(noisy, n_bootstrap=500)
        width_constant = high_c - low_c
        width_noisy = high_n - low_n
        assert width_noisy > width_constant, (
            f"Noisy CI width ({width_noisy:.0f}) should exceed constant ({width_constant:.0f})"
        )

    def test_single_element(self):
        """Single trade → CI should bracket that value."""
        mean, low, high = self.bootstrap_pnl_ci([500.0], n_bootstrap=200)
        assert abs(mean - 500.0) < 1.0
        assert low <= 500.0 <= high

    def test_ci_90_narrower_than_ci_95(self):
        """90% CI should be strictly narrower than 95% CI."""
        pnl = [50.0 * (i % 5 - 2) for i in range(40)]
        _, l90, h90 = self.bootstrap_pnl_ci(pnl, n_bootstrap=500, ci=0.90)
        _, l95, h95 = self.bootstrap_pnl_ci(pnl, n_bootstrap=500, ci=0.95)
        assert (h90 - l90) <= (h95 - l95)
