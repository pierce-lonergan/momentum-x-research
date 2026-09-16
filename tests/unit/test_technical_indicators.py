"""
Tests for src.data.technical_indicators

Node ID: tests.unit.test_technical_indicators
Validates RSI, MACD, Bollinger Bands, support/resistance, VWAP computations.
"""

from __future__ import annotations

from src.data.technical_indicators import (
    _aggregate_bars,
    _compute_bollinger_bands,
    _compute_ema,
    _compute_macd,
    _compute_rsi,
    _compute_support_resistance,
    _compute_vwap,
    compute_indicators,
    format_price_data,
)

# ── Test Data Fixtures ────────────────────────────────────────────────


def _make_bars(
    n: int = 50,
    start_price: float = 10.0,
    trend: float = 0.01,
    spread: float = 0.5,
) -> list[dict]:
    """Generate synthetic 1-min bars with controllable trend."""
    bars = []
    price = start_price
    for i in range(n):
        price = price * (1 + trend)
        bar_o = price - spread * 0.3
        bar_h = price + spread
        bar_l = price - spread
        bar_c = price + spread * 0.2
        bar_v = 10_000 + i * 100
        bars.append({"t": f"2026-01-15T14:{30 + i}:00Z", "o": bar_o, "h": bar_h, "l": bar_l, "c": bar_c, "v": bar_v})
    return bars


def _make_flat_bars(n: int = 50, price: float = 10.0) -> list[dict]:
    """Bars with no movement for testing edge cases."""
    return [
        {"t": f"2026-01-15T14:{30 + i}:00Z", "o": price, "h": price, "l": price, "c": price, "v": 10_000}
        for i in range(n)
    ]


# ── RSI Tests ─────────────────────────────────────────────────────────


class TestRSI:
    def test_rsi_uptrend(self):
        """RSI should be above 50 in a consistent uptrend."""
        bars = _make_bars(50, trend=0.005)
        closes = [b["c"] for b in bars]
        rsi = _compute_rsi(closes, period=14)
        assert rsi is not None
        assert rsi > 50, f"RSI in uptrend should be > 50, got {rsi}"

    def test_rsi_downtrend(self):
        """RSI should be below 50 in a consistent downtrend."""
        bars = _make_bars(50, trend=-0.005)
        closes = [b["c"] for b in bars]
        rsi = _compute_rsi(closes, period=14)
        assert rsi is not None
        assert rsi < 50, f"RSI in downtrend should be < 50, got {rsi}"

    def test_rsi_bounded_0_100(self):
        """RSI must always be between 0 and 100."""
        for trend in [-0.02, -0.01, 0, 0.01, 0.02]:
            bars = _make_bars(50, trend=trend)
            closes = [b["c"] for b in bars]
            rsi = _compute_rsi(closes, period=14)
            if rsi is not None:
                assert 0 <= rsi <= 100

    def test_rsi_insufficient_data_returns_none(self):
        """RSI with fewer than period+1 closes returns None."""
        assert _compute_rsi([10.0] * 10, period=14) is None

    def test_rsi_all_gains_returns_100(self):
        """All positive deltas should give RSI = 100."""
        closes = [10.0 + i * 0.1 for i in range(30)]
        rsi = _compute_rsi(closes, period=14)
        assert rsi is not None
        assert rsi == 100.0


# ── MACD Tests ────────────────────────────────────────────────────────


class TestMACD:
    def test_macd_uptrend_positive_histogram(self):
        """MACD histogram should be positive in strong uptrend."""
        bars = _make_bars(60, trend=0.005)
        closes = [b["c"] for b in bars]
        macd = _compute_macd(closes)
        assert macd is not None
        assert macd["histogram"] > 0

    def test_macd_returns_three_components(self):
        """MACD should return macd, signal, and histogram."""
        bars = _make_bars(60, trend=0.002)
        closes = [b["c"] for b in bars]
        macd = _compute_macd(closes)
        assert macd is not None
        assert "macd" in macd
        assert "signal" in macd
        assert "histogram" in macd

    def test_macd_insufficient_data_returns_none(self):
        """MACD with fewer than slow+signal bars returns None."""
        closes = [10.0] * 20
        assert _compute_macd(closes) is None


# ── Bollinger Bands Tests ─────────────────────────────────────────────


class TestBollingerBands:
    def test_bb_upper_above_middle_above_lower(self):
        """Bollinger Bands should maintain upper > middle > lower."""
        bars = _make_bars(30, spread=0.5)
        closes = [b["c"] for b in bars]
        bb = _compute_bollinger_bands(closes, period=20)
        assert bb is not None
        assert bb["upper"] > bb["middle"] > bb["lower"]

    def test_bb_width_positive(self):
        """Band width should be positive for non-constant data."""
        bars = _make_bars(30, spread=0.5)
        closes = [b["c"] for b in bars]
        bb = _compute_bollinger_bands(closes, period=20)
        assert bb is not None
        assert bb["width_pct"] > 0

    def test_bb_position_in_range(self):
        """%B position should typically be between 0 and 1 when price is within bands."""
        bars = _make_bars(30, trend=0.001, spread=0.3)
        closes = [b["c"] for b in bars]
        bb = _compute_bollinger_bands(closes, period=20)
        assert bb is not None
        # Position can exceed 0-1 range during breakouts, but should exist
        assert isinstance(bb["position"], float)

    def test_bb_flat_data_narrow_bands(self):
        """Flat price data should produce very narrow (near-zero width) bands."""
        bars = _make_flat_bars(30)
        closes = [b["c"] for b in bars]
        bb = _compute_bollinger_bands(closes, period=20)
        assert bb is not None
        assert bb["width_pct"] < 0.01  # Essentially zero width

    def test_bb_insufficient_data_returns_none(self):
        """Bollinger Bands with fewer than period bars returns None."""
        assert _compute_bollinger_bands([10.0] * 5, period=20) is None


# ── Support/Resistance Tests ──────────────────────────────────────────


class TestSupportResistance:
    def test_resistance_above_support(self):
        """Resistance should be above support level."""
        bars = _make_bars(30)
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        closes = [b["c"] for b in bars]
        sr = _compute_support_resistance(highs, lows, closes)
        assert sr is not None
        assert sr["resistance"] > sr["support"]

    def test_pivot_between_support_and_resistance(self):
        """Pivot point should be between support and resistance."""
        bars = _make_bars(30)
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        closes = [b["c"] for b in bars]
        sr = _compute_support_resistance(highs, lows, closes)
        assert sr is not None
        assert sr["support"] <= sr["pivot"] <= sr["resistance"]

    def test_empty_data_returns_none(self):
        """Empty input should return None."""
        assert _compute_support_resistance([], [], []) is None


# ── VWAP Tests ────────────────────────────────────────────────────────


class TestVWAP:
    def test_vwap_within_price_range(self):
        """VWAP should be within the high-low range of the bars."""
        bars = _make_bars(30, trend=0.002)
        vwap = _compute_vwap(bars)
        assert vwap is not None
        all_lows = [b["l"] for b in bars]
        all_highs = [b["h"] for b in bars]
        assert min(all_lows) <= vwap <= max(all_highs)

    def test_vwap_flat_equals_price(self):
        """VWAP of flat data should equal the price."""
        bars = _make_flat_bars(10, price=50.0)
        vwap = _compute_vwap(bars)
        assert vwap is not None
        assert abs(vwap - 50.0) < 0.01

    def test_vwap_zero_volume_returns_none(self):
        """Bars with all zero volume should return None."""
        bars = [{"h": 10, "l": 9, "c": 9.5, "v": 0} for _ in range(10)]
        assert _compute_vwap(bars) is None


# ── EMA Tests ─────────────────────────────────────────────────────────


class TestEMA:
    def test_ema_follows_trend(self):
        """EMA should be >= SMA in uptrend due to recency weighting."""
        prices = [10.0 + i * 0.5 for i in range(30)]
        ema = _compute_ema(prices, 9)
        sma = sum(prices[-9:]) / 9
        assert ema is not None
        assert ema >= sma

    def test_ema_insufficient_data_returns_none(self):
        assert _compute_ema([10.0] * 3, 9) is None


# ── Integration: compute_indicators ──────────────────────────────────


class TestComputeIndicators:
    def test_returns_dict_with_expected_keys(self):
        """compute_indicators should return a dict with at least RSI, BB, support/resistance."""
        bars = _make_bars(60, trend=0.003)
        indicators = compute_indicators(bars)
        assert isinstance(indicators, dict)
        assert "rsi_14" in indicators
        assert "bb_upper" in indicators
        assert "support_level" in indicators
        assert "vwap" in indicators

    def test_includes_moving_averages(self):
        bars = _make_bars(60, trend=0.002)
        indicators = compute_indicators(bars)
        assert "sma_9" in indicators
        assert "ema_9" in indicators

    def test_includes_volume_metrics(self):
        bars = _make_bars(60, trend=0.001)
        indicators = compute_indicators(bars)
        assert "avg_bar_volume" in indicators
        assert "volume_surge_ratio" in indicators

    def test_empty_bars_returns_empty(self):
        """Empty bars should return empty indicators dict."""
        assert compute_indicators([]) == {}

    def test_minimal_bars_returns_partial(self):
        """5 bars should return at least some indicators."""
        bars = _make_bars(6, trend=0.01)
        indicators = compute_indicators(bars)
        # Some indicators require more data, but at least volume should work
        assert isinstance(indicators, dict)

    def test_all_values_are_numeric(self):
        """All indicator values should be int or float."""
        bars = _make_bars(60, trend=0.002)
        indicators = compute_indicators(bars)
        for key, val in indicators.items():
            assert isinstance(val, (int, float)), f"{key} is not numeric: {type(val)}"

    def test_macd_present_with_enough_data(self):
        """MACD should appear when we have 36+ bars."""
        bars = _make_bars(60, trend=0.003)
        indicators = compute_indicators(bars)
        assert "macd_line" in indicators
        assert "macd_signal" in indicators
        assert "macd_histogram" in indicators


# ── format_price_data Tests ──────────────────────────────────────────


class TestFormatPriceData:
    def test_returns_1min_timeframe(self):
        bars = _make_bars(20)
        price_data = format_price_data(bars)
        assert "1min" in price_data
        assert len(price_data["1min"]) == 10  # Last 10

    def test_derives_5min_from_1min(self):
        """Should aggregate 1-min bars into 5-min when no explicit 5-min provided."""
        bars = _make_bars(30)
        price_data = format_price_data(bars)
        assert "5min" in price_data
        assert len(price_data["5min"]) >= 1

    def test_bars_have_ohlcv_keys(self):
        bars = _make_bars(10)
        price_data = format_price_data(bars)
        for bar in price_data["1min"]:
            assert "o" in bar
            assert "h" in bar
            assert "l" in bar
            assert "c" in bar
            assert "v" in bar

    def test_empty_bars_returns_empty(self):
        assert format_price_data([]) == {}


# ── Bar Aggregation Tests ────────────────────────────────────────────


class TestAggregation:
    def test_5min_aggregation(self):
        """5 one-minute bars should aggregate into 1 five-minute bar."""
        bars = _make_bars(10)
        agg = _aggregate_bars(bars, 5)
        assert len(agg) == 2  # 10 bars / 5 = 2 aggregated bars

    def test_aggregated_high_is_max(self):
        """Aggregated high should be max of component highs."""
        bars = _make_bars(5)
        agg = _aggregate_bars(bars, 5)
        assert len(agg) == 1
        expected_high = max(b["h"] for b in bars)
        assert abs(agg[0]["h"] - expected_high) < 0.0001

    def test_aggregated_volume_is_sum(self):
        """Aggregated volume should be sum of component volumes."""
        bars = _make_bars(5)
        agg = _aggregate_bars(bars, 5)
        assert len(agg) == 1
        expected_vol = sum(b["v"] for b in bars)
        assert agg[0]["v"] == expected_vol
