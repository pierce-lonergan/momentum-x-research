"""Tests for D194 Order Flow Analysis."""

from __future__ import annotations

import math
import pytest

from src.data.order_flow import (
    ACCUMULATION_BLOCK_MIN,
    ACCUMULATION_ISO_MIN,
    ACCUMULATION_NET_FLOW_MIN,
    DISTRIBUTION_BLOCK_MIN,
    DISTRIBUTION_NET_FLOW_MAX,
    FALLER_ADJ_ACCUMULATION,
    FALLER_ADJ_DISTRIBUTION,
    FALLER_ADJ_RETAIL,
    RETAIL_BLOCK_MAX,
    FlowSignal,
    OrderFlowAnalyzer,
    OrderFlowResult,
    TradeDirection,
    TradeEvent,
    trades_from_alpaca,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_trade(
    price: float,
    size: int,
    conditions: list[str] | None = None,
    ts: float = 1_700_000_000.0,
) -> TradeEvent:
    return TradeEvent(
        timestamp=ts,
        price=price,
        size=size,
        conditions=conditions or [],
    )


def _analyzer() -> OrderFlowAnalyzer:
    return OrderFlowAnalyzer()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Trade direction classification
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeDirectionClassification:

    def test_at_ask_is_buy(self):
        a = _analyzer()
        t = _make_trade(price=10.05, size=100)
        assert a._classify_trade_direction(t, bid=9.95, ask=10.05) == TradeDirection.BUY

    def test_above_ask_is_buy(self):
        a = _analyzer()
        t = _make_trade(price=10.07, size=100)
        assert a._classify_trade_direction(t, bid=9.95, ask=10.05) == TradeDirection.BUY

    def test_at_bid_is_sell(self):
        a = _analyzer()
        t = _make_trade(price=9.95, size=200)
        assert a._classify_trade_direction(t, bid=9.95, ask=10.05) == TradeDirection.SELL

    def test_below_bid_is_sell(self):
        a = _analyzer()
        t = _make_trade(price=9.90, size=200)
        assert a._classify_trade_direction(t, bid=9.95, ask=10.05) == TradeDirection.SELL

    def test_above_midpoint_inside_spread_is_buy(self):
        a = _analyzer()
        # midpoint = 10.00; 10.03 > 10.00
        t = _make_trade(price=10.03, size=50)
        assert a._classify_trade_direction(t, bid=9.95, ask=10.05) == TradeDirection.BUY

    def test_below_midpoint_inside_spread_is_sell(self):
        a = _analyzer()
        # midpoint = 10.00; 9.97 < 10.00
        t = _make_trade(price=9.97, size=50)
        assert a._classify_trade_direction(t, bid=9.95, ask=10.05) == TradeDirection.SELL

    def test_zero_spread_returns_unknown(self):
        a = _analyzer()
        t = _make_trade(price=10.00, size=100)
        assert a._classify_trade_direction(t, bid=10.00, ask=10.00) == TradeDirection.UNKNOWN

    def test_invalid_bid_ask_returns_unknown(self):
        a = _analyzer()
        t = _make_trade(price=10.00, size=100)
        assert a._classify_trade_direction(t, bid=0.0, ask=10.00) == TradeDirection.UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# 2. Autocorrelation
# ─────────────────────────────────────────────────────────────────────────────

class TestAutocorrelation:

    def test_mostly_buys_nonnegative_autocorr(self):
        # 18 buys then 2 sells → high positive autocorr (persistent buy flow)
        a = _analyzer()
        dirs = [TradeDirection.BUY] * 18 + [TradeDirection.SELL] * 2
        corr = a._compute_autocorrelation(dirs)
        # Persistent buy flow should yield non-negative autocorrelation
        assert corr >= 0.0, f"Expected non-negative autocorr for buy-dominated flow, got {corr}"

    def test_constant_direction_returns_zero(self):
        # All-same direction: zero variance → correlation undefined, returns 0
        a = _analyzer()
        dirs = [TradeDirection.BUY] * 20
        corr = a._compute_autocorrelation(dirs)
        assert corr == 0.0

    def test_alternating_negative_autocorr(self):
        a = _analyzer()
        dirs = [TradeDirection.BUY, TradeDirection.SELL] * 10
        corr = a._compute_autocorrelation(dirs)
        assert corr < -0.5, f"Expected negative autocorr for alternating, got {corr}"

    def test_too_few_trades_returns_zero(self):
        a = _analyzer()
        dirs = [TradeDirection.BUY, TradeDirection.SELL]
        corr = a._compute_autocorrelation(dirs)
        assert corr == 0.0

    def test_all_unknown_returns_zero(self):
        a = _analyzer()
        dirs = [TradeDirection.UNKNOWN] * 10
        corr = a._compute_autocorrelation(dirs)
        assert corr == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Signal classification
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalClassification:

    def _result_with(
        self,
        net_flow_ratio: float,
        block_ratio: float,
        iso_ratio: float = 0.0,
        total_trades: int = 20,
    ) -> OrderFlowResult:
        r = OrderFlowResult(ticker="TEST")
        r.total_trades = total_trades
        r.net_flow_ratio = net_flow_ratio
        r.block_ratio = block_ratio
        r.iso_ratio = iso_ratio
        return r

    def test_insufficient_data_below_min_trades(self):
        a = _analyzer()
        r = self._result_with(0.5, 0.6, total_trades=3)
        assert a._classify_signal(r) == FlowSignal.INSUFFICIENT_DATA

    def test_accumulation_high_block_positive_flow(self):
        a = _analyzer()
        r = self._result_with(
            net_flow_ratio=ACCUMULATION_NET_FLOW_MIN + 0.05,
            block_ratio=ACCUMULATION_BLOCK_MIN + 0.05,
        )
        assert a._classify_signal(r) == FlowSignal.INSTITUTIONAL_ACCUMULATION

    def test_accumulation_via_iso_without_block(self):
        a = _analyzer()
        r = self._result_with(
            net_flow_ratio=ACCUMULATION_NET_FLOW_MIN + 0.05,
            block_ratio=0.10,  # below block threshold
            iso_ratio=ACCUMULATION_ISO_MIN + 0.05,
        )
        assert a._classify_signal(r) == FlowSignal.INSTITUTIONAL_ACCUMULATION

    def test_distribution_negative_flow_high_block(self):
        a = _analyzer()
        r = self._result_with(
            net_flow_ratio=DISTRIBUTION_NET_FLOW_MAX - 0.05,
            block_ratio=DISTRIBUTION_BLOCK_MIN + 0.05,
        )
        assert a._classify_signal(r) == FlowSignal.INSTITUTIONAL_DISTRIBUTION

    def test_retail_low_block_mixed_direction(self):
        a = _analyzer()
        r = self._result_with(net_flow_ratio=0.05, block_ratio=0.08)
        assert a._classify_signal(r) == FlowSignal.RETAIL_DOMINATED

    def test_mixed_for_ambiguous_metrics(self):
        a = _analyzer()
        # High block ratio but insufficient net flow for accumulation
        r = self._result_with(net_flow_ratio=0.10, block_ratio=0.50)
        assert a._classify_signal(r) == FlowSignal.MIXED

    def test_boundary_accumulation_net_flow_just_below(self):
        a = _analyzer()
        r = self._result_with(
            net_flow_ratio=ACCUMULATION_NET_FLOW_MIN - 0.01,
            block_ratio=ACCUMULATION_BLOCK_MIN + 0.10,
        )
        # Just below threshold → not accumulation
        assert a._classify_signal(r) != FlowSignal.INSTITUTIONAL_ACCUMULATION


# ─────────────────────────────────────────────────────────────────────────────
# 4. Faller adjustment
# ─────────────────────────────────────────────────────────────────────────────

class TestFallerAdjustment:

    def test_accumulation_gives_negative_adjustment(self):
        a = _analyzer()
        r = OrderFlowResult(ticker="X", signal=FlowSignal.INSTITUTIONAL_ACCUMULATION)
        assert a.get_faller_adjustment(r) == FALLER_ADJ_ACCUMULATION
        assert a.get_faller_adjustment(r) < 0

    def test_distribution_gives_positive_adjustment(self):
        a = _analyzer()
        r = OrderFlowResult(ticker="X", signal=FlowSignal.INSTITUTIONAL_DISTRIBUTION)
        assert a.get_faller_adjustment(r) == FALLER_ADJ_DISTRIBUTION
        assert a.get_faller_adjustment(r) > 0

    def test_retail_gives_positive_adjustment(self):
        a = _analyzer()
        r = OrderFlowResult(ticker="X", signal=FlowSignal.RETAIL_DOMINATED)
        assert a.get_faller_adjustment(r) == FALLER_ADJ_RETAIL
        assert a.get_faller_adjustment(r) > 0

    def test_mixed_gives_zero_adjustment(self):
        a = _analyzer()
        r = OrderFlowResult(ticker="X", signal=FlowSignal.MIXED)
        assert a.get_faller_adjustment(r) == 0.0

    def test_insufficient_data_gives_zero_adjustment(self):
        a = _analyzer()
        r = OrderFlowResult(ticker="X", signal=FlowSignal.INSUFFICIENT_DATA)
        assert a.get_faller_adjustment(r) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Full analyze_trades integration
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyzeTrades:

    def _block_buy_trades(self, n: int = 10) -> list[TradeEvent]:
        """Generate n block buy trades at ask."""
        return [_make_trade(price=10.05, size=2_000, ts=float(i)) for i in range(n)]

    def test_empty_trades_returns_insufficient(self):
        a = _analyzer()
        result = a.analyze_trades("AAPL", [], bid=9.95, ask=10.05)
        assert result.signal == FlowSignal.INSUFFICIENT_DATA
        assert result.total_trades == 0

    def test_block_buy_sweep_classified_as_accumulation(self):
        a = _analyzer()
        trades = self._block_buy_trades(10)
        result = a.analyze_trades("AAPL", trades, bid=9.95, ask=10.05)
        assert result.signal == FlowSignal.INSTITUTIONAL_ACCUMULATION
        assert result.net_flow_ratio > 0
        assert result.block_ratio > 0

    def test_small_retail_trades_classified_as_retail(self):
        a = _analyzer()
        trades = [
            _make_trade(price=10.00, size=50, ts=float(i))  # inside spread, small
            for i in range(10)
        ]
        result = a.analyze_trades("AAPL", trades, bid=9.95, ask=10.05)
        # All size-50 trades are retail; mixed direction (at midpoint = UNKNOWN → could be mixed)
        assert result.retail_ratio > 0
        assert result.block_ratio == 0.0

    def test_iso_trades_counted_correctly(self):
        a = _analyzer()
        trades = [
            _make_trade(price=10.05, size=500, conditions=["F"], ts=float(i))
            for i in range(10)
        ]
        result = a.analyze_trades("TEST", trades, bid=9.95, ask=10.05)
        assert result.iso_trades == 10
        assert result.iso_ratio > 0

    def test_block_threshold_by_dollar_value(self):
        a = _analyzer()
        # 200 shares at $60 = $12K > $10K threshold → block
        trades = [_make_trade(price=60.00, size=200, ts=float(i)) for i in range(10)]
        result = a.analyze_trades("BIGPRICE", trades, bid=59.90, ask=60.10)
        assert result.block_trades == 10
        assert result.block_ratio == 1.0

    def test_result_to_dict_has_required_keys(self):
        a = _analyzer()
        trades = self._block_buy_trades(6)
        result = a.analyze_trades("AAPL", trades, bid=9.95, ask=10.05)
        d = result.to_dict()
        for key in ["ticker", "signal", "faller_adjustment", "net_flow_ratio",
                    "block_ratio", "iso_ratio", "direction_autocorrelation"]:
            assert key in d, f"Missing key: {key}"

    def test_distribution_large_sell_blocks(self):
        a = _analyzer()
        trades = [
            _make_trade(price=9.95, size=2_000, ts=float(i))  # at bid = SELL
            for i in range(10)
        ]
        result = a.analyze_trades("DIST", trades, bid=9.95, ask=10.05)
        assert result.net_flow_ratio < 0
        assert result.signal == FlowSignal.INSTITUTIONAL_DISTRIBUTION

    def test_total_volume_aggregation(self):
        a = _analyzer()
        trades = [_make_trade(price=10.05, size=500, ts=float(i)) for i in range(5)]
        result = a.analyze_trades("VOL", trades, bid=9.95, ask=10.05)
        assert result.total_volume == 2_500
        assert result.total_trades == 5

    def test_faller_adjustment_set_on_result(self):
        a = _analyzer()
        trades = self._block_buy_trades(10)
        result = a.analyze_trades("ADJ", trades, bid=9.95, ask=10.05)
        assert result.faller_adjustment == FALLER_ADJ_ACCUMULATION


# ─────────────────────────────────────────────────────────────────────────────
# 6. Alpaca raw dict conversion
# ─────────────────────────────────────────────────────────────────────────────

class TestTradesFromAlpaca:

    def test_basic_conversion(self):
        raw = [
            {"t": "2026-04-04T09:30:00Z", "p": 10.05, "s": 500, "c": ["F"]},
            {"t": "2026-04-04T09:30:01Z", "p": 9.95,  "s": 200, "c": []},
        ]
        trades = trades_from_alpaca(raw)
        assert len(trades) == 2
        assert trades[0].price == 10.05
        assert trades[0].size == 500
        assert "F" in trades[0].conditions
        assert trades[1].price == 9.95

    def test_missing_conditions_defaults_to_empty(self):
        raw = [{"t": 1_700_000_000, "p": 5.0, "s": 100}]
        trades = trades_from_alpaca(raw)
        assert trades[0].conditions == []

    def test_epoch_timestamp_passthrough(self):
        raw = [{"t": 1_700_000_000.5, "p": 5.0, "s": 100}]
        trades = trades_from_alpaca(raw)
        assert trades[0].timestamp == pytest.approx(1_700_000_000.5)

    def test_empty_list_returns_empty(self):
        assert trades_from_alpaca([]) == []
