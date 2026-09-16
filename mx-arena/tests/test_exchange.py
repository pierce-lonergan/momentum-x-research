"""Tests for SimExchange matching engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from arena.clock import ClockMode, SimClock
from arena.exchange import SimExchange, OrderState
from arena.fill_model import AlpacaFillModel, Bar
from arena.spread_model import SpreadModel


@pytest.fixture
def clock():
    return SimClock(
        start=datetime(2026, 3, 25, 13, 30, tzinfo=timezone.utc),  # 9:30 ET
        end=datetime(2026, 3, 25, 20, 0, tzinfo=timezone.utc),
        mode=ClockMode.MANUAL,
    )


@pytest.fixture
def exchange(clock):
    return SimExchange(
        clock=clock,
        fill_model=AlpacaFillModel(),
        spread_model=SpreadModel(),
        initial_cash=100_000.0,
        seed=42,
    )


class TestOrderSubmission:
    """Test order creation and state tracking."""

    def test_submit_market_order(self, exchange):
        order = exchange.submit_order(
            symbol="AAPL", qty=100, side="buy", order_type="market",
        )
        assert order.id is not None
        assert order.symbol == "AAPL"
        assert order.qty == 100
        assert order.side == "buy"
        assert order.type == "market"
        assert order.status == "new"

    def test_submit_limit_order(self, exchange):
        order = exchange.submit_order(
            symbol="TSLA", qty=50, side="buy", order_type="limit",
            limit_price=150.0,
        )
        assert order.limit_price == 150.0
        assert order.type == "limit"

    def test_submit_oto_order(self, exchange):
        """OTO (One-Triggers-Other): entry + stop-loss child."""
        order = exchange.submit_order(
            symbol="MKDW", qty=200, side="buy", order_type="limit",
            limit_price=5.50, order_class="oto",
            stop_loss={"stop_price": 4.80},
        )
        assert order.order_class == "oto"
        assert len(order.legs) == 1
        assert order.legs[0].status == "held"
        assert order.legs[0].type == "stop"
        assert order.legs[0].stop_price == 4.80

    def test_submit_bracket_order(self, exchange):
        order = exchange.submit_order(
            symbol="FEED", qty=100, side="buy", order_type="limit",
            limit_price=3.00, order_class="bracket",
            take_profit={"limit_price": 3.50},
            stop_loss={"stop_price": 2.70},
        )
        assert order.order_class == "bracket"
        assert len(order.legs) == 2
        # Take-profit leg
        assert order.legs[0].type == "limit"
        assert order.legs[0].limit_price == 3.50
        assert order.legs[0].status == "held"
        # Stop-loss leg
        assert order.legs[1].type == "stop"
        assert order.legs[1].stop_price == 2.70


class TestMarketOrderFills:
    """Test market order execution against NBBO."""

    @pytest.mark.asyncio
    async def test_market_buy_fills_at_ask(self, exchange, clock):
        exchange.submit_order(symbol="TEST", qty=100, side="buy", order_type="market")

        # Provide market data
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        # Should be filled
        order = list(exchange.orders.values())[0]
        assert order.status == "filled"
        assert order.filled_qty == 100
        # Fill price should be at ask (close + half_spread)
        assert order.filled_avg_price > 10.0

    @pytest.mark.asyncio
    async def test_market_sell_fills_at_bid(self, exchange, clock):
        # First buy to create position
        exchange.submit_order(symbol="TEST", qty=100, side="buy", order_type="market")
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        # Verify we have a position
        assert "TEST" in exchange.positions

        # Now sell — tick multiple times to handle partial fills
        exchange.submit_order(symbol="TEST", qty=100, side="sell", order_type="market")
        for i in range(5):
            bar_n = Bar(timestamp=f"2026-03-25T09:3{i+1}:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
            exchange.update_market_data({"TEST": bar_n})
            await exchange.on_tick(clock.now)

        sell_orders = [o for o in exchange.orders.values() if o.side == "sell" and o.status == "filled"]
        assert len(sell_orders) == 1
        assert sell_orders[0].filled_avg_price < 10.0  # Filled at bid


class TestLimitOrderFills:
    """Test limit order marketability checks."""

    @pytest.mark.asyncio
    async def test_limit_buy_fills_when_marketable(self, exchange, clock):
        exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=10.50,  # Above ask
        )
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        order = list(exchange.orders.values())[0]
        assert order.status == "filled"

    @pytest.mark.asyncio
    async def test_limit_buy_doesnt_fill_when_too_low(self, exchange, clock):
        exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=9.50,  # Below bid
        )
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        order = list(exchange.orders.values())[0]
        assert order.status == "new"  # Not filled


class TestStopOrders:
    """Test stop order trigger logic."""

    @pytest.mark.asyncio
    async def test_stop_sell_triggers_on_low(self, exchange, clock):
        # Create position first
        exchange.positions["TEST"] = __import__("arena.exchange", fromlist=["PositionState"]).PositionState(
            symbol="TEST", qty=100, avg_entry_price=10.0, current_price=10.0,
        )

        exchange.submit_order(
            symbol="TEST", qty=100, side="sell", order_type="stop",
            stop_price=9.50,
        )

        # Bar low dips below stop
        bar = Bar(timestamp="2026-03-25T10:00:00Z", open=10.0, high=10.0, low=9.40, close=9.45, volume=30000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        stop_order = [o for o in exchange.orders.values() if o.type == "stop"][0]
        assert stop_order.status == "filled"

    @pytest.mark.asyncio
    async def test_stop_doesnt_trigger_above_price(self, exchange, clock):
        exchange.positions["TEST"] = __import__("arena.exchange", fromlist=["PositionState"]).PositionState(
            symbol="TEST", qty=100, avg_entry_price=10.0, current_price=10.0,
        )

        exchange.submit_order(
            symbol="TEST", qty=100, side="sell", order_type="stop",
            stop_price=9.50,
        )

        # Bar stays above stop
        bar = Bar(timestamp="2026-03-25T10:00:00Z", open=10.0, high=10.2, low=9.80, close=10.1, volume=30000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        stop_order = [o for o in exchange.orders.values() if o.type == "stop"][0]
        assert stop_order.status == "new"


class TestOTOLifecycle:
    """Test One-Triggers-Other order lifecycle (what the bot actually uses)."""

    @pytest.mark.asyncio
    async def test_oto_child_activates_on_parent_fill(self, exchange, clock):
        order = exchange.submit_order(
            symbol="MKDW", qty=200, side="buy", order_type="limit",
            limit_price=6.00, order_class="oto",
            stop_loss={"stop_price": 5.00},
        )

        # Verify child is held
        child = order.legs[0]
        assert child.status == "held"

        # Fill parent
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=5.80, high=6.10, low=5.70, close=5.90, volume=100000)
        exchange.update_market_data({"MKDW": bar})
        await exchange.on_tick(clock.now)

        # Parent should be filled
        assert order.status == "filled"
        # Child should be activated
        assert child.status == "new"

    @pytest.mark.asyncio
    async def test_oto_child_stays_held_until_parent_fills(self, exchange, clock):
        order = exchange.submit_order(
            symbol="MKDW", qty=200, side="buy", order_type="limit",
            limit_price=5.00,  # Below market — won't fill
            order_class="oto",
            stop_loss={"stop_price": 4.50},
        )

        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=5.80, high=6.10, low=5.70, close=5.90, volume=100000)
        exchange.update_market_data({"MKDW": bar})
        await exchange.on_tick(clock.now)

        # Parent not filled (limit below ask)
        assert order.status == "new"
        # Child still held
        assert order.legs[0].status == "held"


class TestPositionTracking:
    """Test position creation, averaging, and closing."""

    @pytest.mark.asyncio
    async def test_buy_creates_position(self, exchange, clock):
        exchange.submit_order(symbol="TEST", qty=100, side="buy", order_type="market")
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        assert "TEST" in exchange.positions
        pos = exchange.positions["TEST"]
        assert pos.qty == 100
        assert pos.avg_entry_price > 0

    @pytest.mark.asyncio
    async def test_sell_removes_position(self, exchange, clock):
        # Buy
        exchange.submit_order(symbol="TEST", qty=100, side="buy", order_type="market")
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        # Sell — tick multiple times to handle partial fills
        exchange.submit_order(symbol="TEST", qty=100, side="sell", order_type="market")
        for i in range(10):
            exchange.update_market_data({"TEST": bar})
            await exchange.on_tick(clock.now)

        assert "TEST" not in exchange.positions

    @pytest.mark.asyncio
    async def test_cash_updates_on_fill(self, exchange, clock):
        initial_cash = exchange.account.cash
        exchange.submit_order(symbol="TEST", qty=100, side="buy", order_type="market")
        bar = Bar(timestamp="2026-03-25T09:30:00Z", open=10.0, high=10.1, low=9.9, close=10.0, volume=50000)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        # Cash should decrease by fill_price × qty
        assert exchange.account.cash < initial_cash


class TestCancelOrders:
    """Test order cancellation."""

    def test_cancel_open_order(self, exchange):
        order = exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=5.00,
        )
        result = exchange.cancel_order(order.id)
        assert result is not None
        assert result.status == "canceled"

    def test_cancel_filled_order_fails(self, exchange):
        order = exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="market",
        )
        order.status = "filled"
        result = exchange.cancel_order(order.id)
        assert result is None

    def test_cancel_oto_cancels_children(self, exchange):
        order = exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=5.00, order_class="oto",
            stop_loss={"stop_price": 4.50},
        )
        exchange.cancel_order(order.id)
        assert order.status == "canceled"
        assert order.legs[0].status == "canceled"


class TestAccountState:
    """Test account serialization."""

    def test_account_to_alpaca_dict(self, exchange):
        d = exchange.account.to_alpaca_dict(exchange.positions)
        assert d["status"] == "ACTIVE"
        assert d["cash"] == "100000.0"
        assert d["equity"] == "100000.0"
        assert float(d["buying_power"]) == 400_000.0  # 4x margin

    def test_get_orders_filters(self, exchange):
        exchange.submit_order(symbol="A", qty=10, side="buy", order_type="market")
        exchange.submit_order(symbol="B", qty=10, side="buy", order_type="limit", limit_price=5.0)

        open_orders = exchange.get_orders(status="open")
        assert len(open_orders) == 2

        # Mark one as filled
        list(exchange.orders.values())[0].status = "filled"
        open_orders = exchange.get_orders(status="open")
        assert len(open_orders) == 1


class TestAlpacaDictSerialization:
    """Test that responses match Alpaca API format."""

    def test_order_dict_has_required_fields(self, exchange):
        order = exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=10.0,
        )
        d = order.to_alpaca_dict()

        required = [
            "id", "client_order_id", "symbol", "qty", "filled_qty",
            "side", "type", "time_in_force", "limit_price", "status",
            "created_at", "order_class",
        ]
        for field in required:
            assert field in d, f"Missing field: {field}"

        # Values should be strings (Alpaca convention)
        assert isinstance(d["qty"], str)
        assert isinstance(d["limit_price"], str)

    def test_position_dict_has_required_fields(self, exchange):
        from arena.exchange import PositionState
        pos = PositionState(
            symbol="TEST", qty=100, avg_entry_price=10.0, current_price=11.0,
        )
        d = pos.to_alpaca_dict()

        required = [
            "symbol", "qty", "avg_entry_price", "current_price",
            "market_value", "unrealized_pl", "side",
        ]
        for field in required:
            assert field in d, f"Missing field: {field}"

        assert d["market_value"] == "1100.0"
        assert d["unrealized_pl"] == "100.0"
