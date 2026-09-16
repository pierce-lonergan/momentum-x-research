"""
Track C Phase 1 — SimpleBroker oracle unit tests.

Per `31_simple_broker_spec.md` §5 Phase 1: 100% unit test coverage of
SimpleBroker BEFORE Phase 2 wires it to a Hypothesis state machine.

Scope: lifecycle correctness of the broker model itself. If these
tests fail, the oracle is buggy and any invariant check using it is
checking against fiction.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.property.simple_broker import (
    BrokerError,
    OrderRecord,
    PositionRecord,
    SimpleBroker,
    TERMINAL_STATES,
    TERMINAL_SUCCESS,
)


# ── Constants ──────────────────────────────────────────────────────


class TestConstants:

    def test_terminal_states_complete(self):
        assert "filled" in TERMINAL_STATES
        assert "canceled" in TERMINAL_STATES
        assert "expired" in TERMINAL_STATES
        assert "rejected" in TERMINAL_STATES
        assert "replaced" in TERMINAL_STATES

    def test_terminal_success_is_subset(self):
        assert TERMINAL_SUCCESS.issubset(TERMINAL_STATES)


# ── Production-shape methods ───────────────────────────────────────


class TestSubmitOrder:

    @pytest.mark.asyncio
    async def test_submit_simple_market_order(self):
        b = SimpleBroker()
        result = await b.submit_order({
            "symbol": "AAPL", "side": "buy", "qty": "100",
            "type": "market", "time_in_force": "day",
        })
        assert result["status"] == "accepted"
        assert result["symbol"] == "AAPL"
        assert int(result["qty"]) == 100
        assert result["legs"] == []

    @pytest.mark.asyncio
    async def test_submit_oto_creates_held_stop_leg(self):
        b = SimpleBroker()
        result = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
            "limit_price": "100", "time_in_force": "day",
            "order_class": "oto",
            "stop_loss": {"stop_price": "95"},
        })
        assert len(result["legs"]) == 1
        leg = result["legs"][0]
        assert leg["side"] == "sell"
        assert leg["type"] == "stop"
        assert float(leg["stop_price"]) == 95.0
        assert leg["status"] == "held"  # OTO stop is held until parent fills

    @pytest.mark.asyncio
    async def test_submit_to_halted_ticker_rejected(self):
        b = SimpleBroker()
        b.halt("HALTED")
        with pytest.raises(BrokerError) as exc:
            await b.submit_order({
                "symbol": "HALTED", "side": "buy", "qty": "1",
                "type": "market", "time_in_force": "day",
            })
        assert exc.value.status_code == 403


class TestCancelOrder:

    @pytest.mark.asyncio
    async def test_cancel_active_order(self):
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "1",
            "type": "limit", "limit_price": "1", "time_in_force": "day",
        })
        await b.cancel_order(r["id"])
        assert b.orders[r["id"]].status == "canceled"

    @pytest.mark.asyncio
    async def test_cancel_oto_cascades_to_children(self):
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
            "limit_price": "100", "time_in_force": "day",
            "order_class": "oto",
            "stop_loss": {"stop_price": "95"},
        })
        await b.cancel_order(r["id"])
        # Both parent and child should be canceled
        parent = b.orders[r["id"]]
        assert parent.status == "canceled"
        assert len(parent.child_order_ids) == 1
        child = b.orders[parent.child_order_ids[0]]
        assert child.status == "canceled"

    @pytest.mark.asyncio
    async def test_cancel_idempotent(self):
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "1",
            "type": "limit", "limit_price": "1", "time_in_force": "day",
        })
        await b.cancel_order(r["id"])
        await b.cancel_order(r["id"])  # second call must not raise
        assert b.orders[r["id"]].status == "canceled"


class TestGetOrders:

    @pytest.mark.asyncio
    async def test_filter_by_status_open(self):
        b = SimpleBroker()
        r1 = await b.submit_order({"symbol": "X", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        r2 = await b.submit_order({"symbol": "Y", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        await b.cancel_order(r1["id"])
        open_orders = await b.get_orders(status="open")
        assert len(open_orders) == 1
        assert open_orders[0]["id"] == r2["id"]

    @pytest.mark.asyncio
    async def test_filter_by_symbol(self):
        b = SimpleBroker()
        await b.submit_order({"symbol": "X", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        await b.submit_order({"symbol": "Y", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        x_orders = await b.get_orders(status="all", symbols="X")
        assert len(x_orders) == 1
        assert x_orders[0]["symbol"] == "X"


class TestGetPositions:

    @pytest.mark.asyncio
    async def test_empty_when_no_fills(self):
        b = SimpleBroker()
        assert await b.get_positions() == []

    @pytest.mark.asyncio
    async def test_position_synthesised_from_fill(self):
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "100",
            "type": "market", "time_in_force": "day",
        })
        b.terminal_fill(r["id"], price=10.00)
        positions = await b.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "X"
        assert int(positions[0]["qty"]) == 100
        assert positions[0]["side"] == "long"


class TestClosePosition:

    @pytest.mark.asyncio
    async def test_close_position_submits_market_sell(self):
        b = SimpleBroker()
        r = await b.submit_order({"symbol": "X", "side": "buy", "qty": "100", "type": "market", "time_in_force": "day"})
        b.terminal_fill(r["id"], 10.00)
        result = await b.close_position("X")
        assert result["side"] == "sell"
        assert int(result["qty"]) == 100
        assert result["type"] == "market"

    @pytest.mark.asyncio
    async def test_close_position_no_position_404(self):
        b = SimpleBroker()
        with pytest.raises(BrokerError) as exc:
            await b.close_position("NONEXISTENT")
        assert exc.value.status_code == 404


# ── Test-rule state-injection methods ──────────────────────────────


class TestPartialFill:

    @pytest.mark.asyncio
    async def test_partial_fill_updates_filled_qty_and_vwap(self):
        b = SimpleBroker()
        r = await b.submit_order({"symbol": "X", "side": "buy", "qty": "100", "type": "market", "time_in_force": "day"})
        b.partial_fill(r["id"], 30, 10.00)
        order = b.orders[r["id"]]
        assert order.filled_qty == 30
        assert order.filled_avg_price == pytest.approx(10.00)
        assert order.status == "partially_filled"

        b.partial_fill(r["id"], 70, 11.00)  # final
        order = b.orders[r["id"]]
        assert order.filled_qty == 100
        # VWAP = (30*10 + 70*11) / 100 = (300+770)/100 = 10.70
        assert order.filled_avg_price == pytest.approx(10.70, abs=0.01)
        assert order.status == "filled"

    @pytest.mark.asyncio
    async def test_oto_stop_activates_on_first_fill(self):
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
            "limit_price": "100", "time_in_force": "day",
            "order_class": "oto", "stop_loss": {"stop_price": "95"},
        })
        parent = b.orders[r["id"]]
        stop_id = parent.child_order_ids[0]
        assert b.orders[stop_id].status == "held"
        # First partial fill activates the stop
        b.partial_fill(r["id"], 5, 100.00)
        assert b.orders[stop_id].status == "new"


class TestTerminalFill:

    @pytest.mark.asyncio
    async def test_terminal_fill_completes_residual(self):
        b = SimpleBroker()
        r = await b.submit_order({"symbol": "X", "side": "buy", "qty": "100", "type": "market", "time_in_force": "day"})
        b.partial_fill(r["id"], 40, 10.00)
        b.terminal_fill(r["id"], 10.20)
        order = b.orders[r["id"]]
        assert order.filled_qty == 100
        assert order.status == "filled"


class TestReject:

    @pytest.mark.asyncio
    async def test_reject_marks_terminal(self):
        b = SimpleBroker()
        r = await b.submit_order({"symbol": "X", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        b.reject(r["id"])
        assert b.orders[r["id"]].status == "rejected"


class TestExpireAtEod:

    @pytest.mark.asyncio
    async def test_expire_marks_day_orders_expired(self):
        b = SimpleBroker()
        r1 = await b.submit_order({"symbol": "X", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        r2 = await b.submit_order({"symbol": "Y", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "gtc"})
        n = b.expire_at_eod()
        assert n == 1
        assert b.orders[r1["id"]].status == "expired"
        assert b.orders[r2["id"]].status == "accepted"  # GTC unaffected


class TestHaltResume:

    @pytest.mark.asyncio
    async def test_halt_rejects_pending_orders(self):
        b = SimpleBroker()
        r = await b.submit_order({"symbol": "X", "side": "buy", "qty": "1", "type": "limit", "limit_price": "1", "time_in_force": "day"})
        b.halt("X")
        assert b.orders[r["id"]].status == "rejected"

    @pytest.mark.asyncio
    async def test_resume_clears_halt(self):
        b = SimpleBroker()
        b.halt("X")
        assert "X" in b.halted_tickers
        b.resume("X")
        assert "X" not in b.halted_tickers


class TestTriggerStop:

    @pytest.mark.asyncio
    async def test_trigger_stop_fires_market_sell(self):
        b = SimpleBroker()
        # Set up a long position with active stop
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
            "limit_price": "100", "time_in_force": "day",
            "order_class": "oto", "stop_loss": {"stop_price": "95"},
        })
        b.terminal_fill(r["id"], 100.00)  # parent fills, stop activates
        stop_id = b.orders[r["id"]].child_order_ids[0]
        assert b.orders[stop_id].status == "new"
        # Trigger the stop
        b.trigger_stop(stop_id, fill_price=94.50)
        assert b.orders[stop_id].status == "filled"
        assert b.orders[stop_id].filled_avg_price == pytest.approx(94.50)
        # Position should be closed (long covered by sell)
        positions = await b.get_positions()
        assert len(positions) == 0


# ── Integration: full lifecycle scenarios ──────────────────────────


class TestLifecycleScenarios:

    @pytest.mark.asyncio
    async def test_buy_then_close_full_cycle(self):
        """Submit OTO buy, fill, close, position drained."""
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "100", "type": "limit",
            "limit_price": "10.00", "time_in_force": "day",
            "order_class": "oto", "stop_loss": {"stop_price": "9.00"},
        })
        b.terminal_fill(r["id"], 10.00)
        positions = await b.get_positions()
        assert len(positions) == 1

        # Bug AD fix: close_position now auto-fills. Pass fill_price=11.00
        # to simulate slippage from cost basis ($10) to exit ($11).
        close_response = await b.close_position("X", fill_price=11.00)
        positions = await b.get_positions()
        assert len(positions) == 0
        # P&L: bought 100 @ $10, sold 100 @ $11 = +$100
        # equity = starting + realized
        assert b.equity == pytest.approx(100_000.00 + 100, abs=0.01)

    @pytest.mark.asyncio
    async def test_partial_fill_oto_stop_activates_correctly(self):
        """OTO stop must activate even on partial fills (not just terminal)."""
        b = SimpleBroker()
        r = await b.submit_order({
            "symbol": "X", "side": "buy", "qty": "100", "type": "limit",
            "limit_price": "10.00", "time_in_force": "day",
            "order_class": "oto", "stop_loss": {"stop_price": "9.00"},
        })
        parent = b.orders[r["id"]]
        stop_id = parent.child_order_ids[0]
        assert b.orders[stop_id].status == "held"
        b.partial_fill(r["id"], 30, 10.00)
        assert b.orders[stop_id].status == "new"  # active even on partial
