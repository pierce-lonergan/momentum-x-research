"""Tests for fill models."""

from __future__ import annotations

from random import Random

import pytest

from arena.exchange import OrderState
from arena.fill_model import AlpacaFillModel, Bar, RealisticFillModel


def _make_order(**kwargs) -> OrderState:
    defaults = dict(
        id="test-id", client_order_id="test-coid",
        symbol="TEST", side="buy", type="market",
        time_in_force="day", qty=100, filled_qty=0,
    )
    defaults.update(kwargs)
    return OrderState(**defaults)


@pytest.fixture
def model():
    return AlpacaFillModel()


@pytest.fixture
def bar():
    return Bar(
        timestamp="2026-03-25T09:30:00Z",
        open=10.0, high=10.2, low=9.8, close=10.0,
        volume=50000, vwap=10.0,
    )


class TestAlpacaFillModel:
    def test_market_buy_fills_at_ask(self, model, bar):
        order = _make_order(side="buy", type="market")
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is not None
        assert fill.price == 10.01

    def test_market_sell_fills_at_bid(self, model, bar):
        order = _make_order(side="sell", type="market")
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is not None
        assert fill.price == 9.99

    def test_limit_buy_fills_when_marketable(self, model, bar):
        order = _make_order(side="buy", type="limit", limit_price=10.05)
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is not None
        assert fill.price <= 10.05  # Price improvement possible

    def test_limit_buy_no_fill_when_too_low(self, model, bar):
        order = _make_order(side="buy", type="limit", limit_price=9.50)
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is None

    def test_limit_sell_fills_when_marketable(self, model, bar):
        order = _make_order(side="sell", type="limit", limit_price=9.95)
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is not None

    def test_limit_sell_no_fill_when_too_high(self, model, bar):
        order = _make_order(side="sell", type="limit", limit_price=10.50)
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is None

    def test_stop_sell_triggers_on_low(self, model, bar):
        order = _make_order(side="sell", type="stop", stop_price=9.85)
        # Bar low is 9.8, which is below stop at 9.85
        fill = model.try_fill(order, bar, bid=9.79, ask=9.81, rng=Random(42))
        assert fill is not None

    def test_stop_sell_no_trigger_above(self, model):
        bar = Bar(timestamp="t", open=10.0, high=10.2, low=9.90, close=10.0, volume=50000)
        order = _make_order(side="sell", type="stop", stop_price=9.85)
        fill = model.try_fill(order, bar, bid=9.89, ask=9.91, rng=Random(42))
        assert fill is None

    def test_partial_fill_deterministic(self, model, bar):
        """With seed=0, verify partial fill behavior is deterministic."""
        order = _make_order(side="buy", type="market", qty=100)
        rng = Random(0)

        fills = []
        for _ in range(100):
            order_copy = _make_order(side="buy", type="market", qty=100)
            fill = model.try_fill(order_copy, bar, bid=9.99, ask=10.01, rng=Random(0))
            fills.append(fill.qty)

        # All should be the same (deterministic)
        assert len(set(fills)) == 1

    def test_partial_fill_probability(self, model, bar):
        """Over many trials, ~10% should be partial fills."""
        partial_count = 0
        total = 1000

        for seed in range(total):
            order = _make_order(side="buy", type="market", qty=100)
            fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(seed))
            if fill and fill.qty < 100:
                partial_count += 1

        # Should be roughly 10% ± 3%
        ratio = partial_count / total
        assert 0.05 < ratio < 0.18, f"Partial fill ratio {ratio:.2%} outside expected range"

    def test_already_filled_returns_none(self, model, bar):
        order = _make_order(side="buy", type="market", qty=100, filled_qty=100)
        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is None


class TestRealisticFillModel:
    def test_volume_limits_fill(self):
        model = RealisticFillModel()
        bar = Bar(timestamp="t", open=10.0, high=10.1, low=9.9, close=10.0, volume=100)
        order = _make_order(side="buy", type="market", qty=50)

        fill = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))
        assert fill is not None
        # 5% of 100 volume = 5 max
        assert fill.qty <= 5

    def test_market_impact_increases_buy_price(self):
        model = RealisticFillModel()
        bar = Bar(timestamp="t", open=10.0, high=10.1, low=9.9, close=10.0, volume=1000)
        order = _make_order(side="buy", type="market", qty=50)

        fill_realistic = model.try_fill(order, bar, bid=9.99, ask=10.01, rng=Random(42))

        base_model = AlpacaFillModel()
        order2 = _make_order(side="buy", type="market", qty=50)
        fill_base = base_model.try_fill(order2, bar, bid=9.99, ask=10.01, rng=Random(42))

        # Realistic fill should have higher price due to impact
        assert fill_realistic.price >= fill_base.price
