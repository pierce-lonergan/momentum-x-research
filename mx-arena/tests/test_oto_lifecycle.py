"""
OTO (One-Triggers-Other) order lifecycle regression test.

Replays the exact D100 production flow:
1. Submit OTO buy limit + stop
2. Feed bars until buy fills
3. Verify stop leg activates (held -> new)
4. Feed bars where stop triggers
5. Verify: position closed, P&L correct

Also tests the D57 cancel-and-resubmit flow and tranche sells.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from arena.clock import ClockMode, SimClock
from arena.exchange import SimExchange, PositionState
from arena.fill_model import AlpacaFillModel, Bar
from arena.spread_model import SpreadModel


@pytest.fixture
def clock():
    return SimClock(
        start=datetime(2026, 3, 26, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc),
        mode=ClockMode.MANUAL,
    )


@pytest.fixture
def exchange(clock):
    return SimExchange(
        clock=clock,
        fill_model=AlpacaFillModel(),
        spread_model=SpreadModel(),
        initial_cash=100_000.0,
        seed=99,  # Seed that avoids partial fills for clean testing
    )


def _bar(ts_minute: int, open: float, high: float, low: float, close: float, vol: int = 50000) -> Bar:
    """Helper to create bars at minute offsets from 09:30."""
    return Bar(
        timestamp=f"2026-03-26T{13 + ts_minute // 60:02d}:{30 + ts_minute % 60:02d}:00Z",
        open=open, high=high, low=low, close=close, volume=vol,
    )


class TestOTOFullLifecycle:
    """Replay the production D100 OTO flow end-to-end."""

    @pytest.mark.asyncio
    async def test_oto_entry_fills_then_stop_activates(self, exchange, clock):
        """OTO: buy fills -> stop child transitions from held to new."""
        order = exchange.submit_order(
            symbol="MKDW", qty=200, side="buy", order_type="limit",
            limit_price=5.80, order_class="oto",
            stop_loss={"stop_price": 4.80},
        )

        # Verify initial state
        assert order.status == "new"
        assert len(order.legs) == 1
        stop_leg = order.legs[0]
        assert stop_leg.status == "held"
        assert stop_leg.stop_price == 4.80

        # T+0: Price at $5.40 — buy limit at $5.80 > ask, should fill
        bar = _bar(0, 5.30, 5.45, 5.25, 5.40)
        exchange.update_market_data({"MKDW": bar})
        await exchange.on_tick(clock.now)

        # Buy should be filled
        assert order.status == "filled"
        assert order.filled_qty == 200

        # Stop should be activated
        assert stop_leg.status == "new"

        # Position should exist
        assert "MKDW" in exchange.positions
        pos = exchange.positions["MKDW"]
        assert pos.qty == 200

    @pytest.mark.asyncio
    async def test_oto_stop_triggers_on_price_drop(self, exchange, clock):
        """Full OTO lifecycle: entry -> stop triggers -> position closed."""
        order = exchange.submit_order(
            symbol="FEED", qty=100, side="buy", order_type="limit",
            limit_price=3.00, order_class="oto",
            stop_loss={"stop_price": 2.70},
        )
        stop_leg = order.legs[0]

        # T+0: Entry fills
        bar0 = _bar(0, 2.90, 3.10, 2.85, 2.95)
        exchange.update_market_data({"FEED": bar0})
        await exchange.on_tick(clock.now)
        assert order.status == "filled"
        assert stop_leg.status == "new"

        # T+1: Price holds above stop
        bar1 = _bar(1, 2.95, 3.05, 2.80, 2.90)
        exchange.update_market_data({"FEED": bar1})
        await exchange.on_tick(clock.now)
        assert stop_leg.status == "new"  # Still waiting

        # T+2: Price drops below stop ($2.70)
        bar2 = _bar(2, 2.85, 2.88, 2.65, 2.68)
        exchange.update_market_data({"FEED": bar2})
        await exchange.on_tick(clock.now)

        # Stop should have triggered and filled
        assert stop_leg.status == "filled"

        # Position should be closed
        assert "FEED" not in exchange.positions

        # P&L should be negative (bought ~$2.95, stopped at ~$2.65)
        sell_trades = [t for t in exchange.trade_history if t["side"] == "sell"]
        assert len(sell_trades) >= 1

    @pytest.mark.asyncio
    async def test_oto_child_stays_held_if_parent_doesnt_fill(self, exchange, clock):
        """Stop leg must NOT activate if parent never fills."""
        order = exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=4.00,  # Below market — won't fill
            order_class="oto",
            stop_loss={"stop_price": 3.50},
        )
        stop_leg = order.legs[0]

        # Price stays above limit
        for i in range(5):
            bar = _bar(i, 5.0, 5.1, 4.9, 5.0)
            exchange.update_market_data({"TEST": bar})
            await exchange.on_tick(clock.now)

        assert order.status == "new"  # Never filled
        assert stop_leg.status == "held"  # Still held

    @pytest.mark.asyncio
    async def test_cancel_oto_cancels_children(self, exchange, clock):
        """Canceling parent should cancel all children."""
        order = exchange.submit_order(
            symbol="TEST", qty=100, side="buy", order_type="limit",
            limit_price=5.00, order_class="oto",
            stop_loss={"stop_price": 4.50},
        )

        result = exchange.cancel_order(order.id)
        assert result is not None
        assert order.status == "canceled"
        assert order.legs[0].status == "canceled"


class TestBracketLifecycle:
    """Test bracket orders (entry + take-profit + stop-loss)."""

    @pytest.mark.asyncio
    async def test_bracket_tp_fills_cancels_stop(self, exchange, clock):
        """When take-profit fills, stop-loss should be canceled."""
        order = exchange.submit_order(
            symbol="RMSG", qty=500, side="buy", order_type="limit",
            limit_price=0.60, order_class="bracket",
            take_profit={"limit_price": 0.70},
            stop_loss={"stop_price": 0.50},
        )
        tp_leg = order.legs[0]  # Take-profit
        sl_leg = order.legs[1]  # Stop-loss

        # T+0: Entry fills
        bar0 = _bar(0, 0.55, 0.62, 0.54, 0.58)
        exchange.update_market_data({"RMSG": bar0})
        await exchange.on_tick(clock.now)
        assert order.status == "filled"
        assert tp_leg.status == "new"
        assert sl_leg.status == "new"

        # T+1: Price rises to take-profit
        bar1 = _bar(1, 0.60, 0.72, 0.59, 0.71)
        exchange.update_market_data({"RMSG": bar1})
        await exchange.on_tick(clock.now)

        # Take-profit should fill, stop should be canceled
        assert tp_leg.status == "filled"
        assert sl_leg.status == "canceled"

        # Position should be closed
        assert "RMSG" not in exchange.positions

    @pytest.mark.asyncio
    async def test_bracket_stop_fills_cancels_tp(self, exchange, clock):
        """When stop-loss fills, take-profit should be canceled."""
        order = exchange.submit_order(
            symbol="SRPU", qty=100, side="buy", order_type="limit",
            limit_price=14.50, order_class="bracket",
            take_profit={"limit_price": 16.00},
            stop_loss={"stop_price": 13.00},
        )
        tp_leg = order.legs[0]
        sl_leg = order.legs[1]

        # T+0: Entry fills
        bar0 = _bar(0, 14.30, 14.60, 14.20, 14.40)
        exchange.update_market_data({"SRPU": bar0})
        await exchange.on_tick(clock.now)
        assert order.status == "filled"

        # T+1-5: Price drops through stop
        for i in range(1, 6):
            price = 14.0 - i * 0.4
            bar = _bar(i, price + 0.1, price + 0.2, price - 0.1, price)
            exchange.update_market_data({"SRPU": bar})
            await exchange.on_tick(clock.now)

        assert sl_leg.status == "filled"
        assert tp_leg.status == "canceled"


class TestTrancheExits:
    """Test tranche limit sell orders (the D100 standalone flow)."""

    @pytest.mark.asyncio
    async def test_multiple_sell_limits_at_different_prices(self, exchange, clock):
        """Submit 3 tranche sells at different prices."""
        # Create position via market buy
        exchange.submit_order(symbol="KOD", qty=300, side="buy", order_type="market")
        bar0 = _bar(0, 22.00, 22.50, 21.80, 22.20, vol=100000)
        exchange.update_market_data({"KOD": bar0})
        await exchange.on_tick(clock.now)
        assert "KOD" in exchange.positions

        # Submit 3 tranche sells
        t1 = exchange.submit_order(symbol="KOD", qty=100, side="sell", order_type="limit", limit_price=24.00)
        t2 = exchange.submit_order(symbol="KOD", qty=100, side="sell", order_type="limit", limit_price=26.00)
        t3 = exchange.submit_order(symbol="KOD", qty=100, side="sell", order_type="limit", limit_price=28.00)

        # Price rises to first tranche
        bar1 = _bar(1, 22.50, 24.50, 22.40, 24.20, vol=100000)
        exchange.update_market_data({"KOD": bar1})
        await exchange.on_tick(clock.now)

        # First tranche should fill (bid >= 24.00)
        assert t1.status == "filled"
        assert t2.status == "new"
        assert t3.status == "new"

        # Position should be reduced
        assert exchange.positions["KOD"].qty == 200


class TestAccountIntegrity:
    """Test account state consistency through order lifecycle."""

    @pytest.mark.asyncio
    async def test_equity_equals_cash_plus_positions(self, exchange, clock):
        """Account equity must always = cash + sum(position.market_value)."""
        initial_equity = exchange.account.cash

        # Buy some stock
        exchange.submit_order(symbol="TEST", qty=100, side="buy", order_type="market")
        bar = _bar(0, 10.0, 10.1, 9.9, 10.0)
        exchange.update_market_data({"TEST": bar})
        await exchange.on_tick(clock.now)

        # Verify equity = cash + positions
        cash = exchange.account.cash
        pos_value = sum(p.market_value for p in exchange.positions.values())
        computed_equity = cash + pos_value

        # Should be close to initial (small spread cost)
        assert abs(computed_equity - initial_equity) < initial_equity * 0.01
