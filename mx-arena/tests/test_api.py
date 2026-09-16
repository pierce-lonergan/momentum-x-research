"""Tests for REST API endpoints using FastAPI TestClient."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from arena.api.rest import create_app
from arena.clock import ClockMode, SimClock
from arena.data_engine import DataEngine
from arena.exchange import SimExchange
from arena.fill_model import AlpacaFillModel, Bar
from arena.spread_model import SpreadModel

try:
    from httpx import ASGITransport, AsyncClient
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

pytestmark = pytest.mark.skipif(not HAS_HTTPX, reason="httpx required")

AUTH_HEADERS = {
    "APCA-API-KEY-ID": "test-key",
    "APCA-API-SECRET-KEY": "test-secret",
}


@pytest.fixture
def components():
    clock = SimClock(
        start=datetime(2026, 3, 25, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 3, 25, 20, 0, tzinfo=timezone.utc),
        mode=ClockMode.MANUAL,
    )
    exchange = SimExchange(
        clock=clock,
        fill_model=AlpacaFillModel(),
        spread_model=SpreadModel(),
        initial_cash=100_000.0,
        seed=42,
    )
    data_engine = DataEngine(
        clock=clock,
        historical_dir="nonexistent",
        symbols=[],
    )
    app = create_app(exchange, data_engine, clock)
    return app, exchange, data_engine, clock


@pytest.fixture
async def client(components):
    app, _, _, _ = components
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAccountEndpoint:
    @pytest.mark.asyncio
    async def test_get_account(self, client):
        resp = await client.get("/v2/account", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ACTIVE"
        assert data["cash"] == "100000.0"
        assert data["equity"] == "100000.0"
        assert float(data["buying_power"]) == 400_000.0

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, client):
        resp = await client.get("/v2/account")
        assert resp.status_code == 401


class TestOrderEndpoints:
    @pytest.mark.asyncio
    async def test_create_market_order(self, client):
        resp = await client.post("/v2/orders", headers=AUTH_HEADERS, json={
            "symbol": "AAPL",
            "qty": 100,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert data["qty"] == "100"
        assert data["status"] == "new"
        assert data["id"] is not None

    @pytest.mark.asyncio
    async def test_create_oto_order(self, client):
        resp = await client.post("/v2/orders", headers=AUTH_HEADERS, json={
            "symbol": "MKDW",
            "qty": 200,
            "side": "buy",
            "type": "limit",
            "limit_price": 5.50,
            "time_in_force": "day",
            "order_class": "oto",
            "stop_loss": {"stop_price": 4.80},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["order_class"] == "oto"
        assert data["legs"] is not None
        assert len(data["legs"]) == 1
        assert data["legs"][0]["status"] == "held"

    @pytest.mark.asyncio
    async def test_list_orders(self, client):
        # Submit two orders
        await client.post("/v2/orders", headers=AUTH_HEADERS, json={
            "symbol": "A", "qty": 10, "side": "buy", "type": "market", "time_in_force": "day",
        })
        await client.post("/v2/orders", headers=AUTH_HEADERS, json={
            "symbol": "B", "qty": 20, "side": "buy", "type": "limit",
            "limit_price": 5.0, "time_in_force": "day",
        })

        resp = await client.get("/v2/orders?status=open", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        orders = resp.json()
        assert len(orders) == 2

    @pytest.mark.asyncio
    async def test_cancel_order(self, client):
        # Create
        resp = await client.post("/v2/orders", headers=AUTH_HEADERS, json={
            "symbol": "TEST", "qty": 50, "side": "buy", "type": "limit",
            "limit_price": 5.0, "time_in_force": "day",
        })
        order_id = resp.json()["id"]

        # Cancel
        resp = await client.delete(f"/v2/orders/{order_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 204


class TestPositionEndpoints:
    @pytest.mark.asyncio
    async def test_list_positions_empty(self, client):
        resp = await client.get("/v2/positions", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_get_position_not_found(self, client):
        resp = await client.get("/v2/positions/NOEXIST", headers=AUTH_HEADERS)
        assert resp.status_code == 404


class TestClockEndpoint:
    @pytest.mark.asyncio
    async def test_get_clock(self, client):
        resp = await client.get("/v2/clock", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "is_open" in data
        assert "next_open" in data
        assert "next_close" in data
        assert data["is_open"] is True  # 9:30 AM ET is market hours


class TestMarketDataEndpoints:
    @pytest.mark.asyncio
    async def test_get_snapshot(self, client):
        resp = await client.get("/v2/stocks/AAPL/snapshot", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "latestTrade" in data
        assert "latestQuote" in data
        assert "minuteBar" in data
        assert "dailyBar" in data
        assert "prevDailyBar" in data

    @pytest.mark.asyncio
    async def test_get_multi_snapshot(self, client):
        resp = await client.get(
            "/v2/stocks/snapshots?symbols=AAPL,TSLA",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "AAPL" in data
        assert "TSLA" in data

    @pytest.mark.asyncio
    async def test_get_bars(self, client):
        resp = await client.get(
            "/v2/stocks/AAPL/bars?timeframe=1Min&limit=10",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "bars" in data

    @pytest.mark.asyncio
    async def test_screener_most_actives(self, client):
        resp = await client.get(
            "/v1beta1/screener/stocks/most-actives",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "most_actives" in data

    @pytest.mark.asyncio
    async def test_screener_movers(self, client):
        resp = await client.get(
            "/v1beta1/screener/stocks/movers",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "gainers" in data


class TestNewsEndpoint:
    @pytest.mark.asyncio
    async def test_news_returns_empty(self, client):
        resp = await client.get("/v1beta3/news", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["news"] == []
