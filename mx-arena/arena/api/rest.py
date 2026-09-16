"""
FastAPI REST endpoints implementing the Alpaca trading + data API.

Every endpoint the MOMENTUM-X bot calls is implemented here.
The bot uses custom httpx clients with configurable base_url/data_url,
so redirection is just ALPACA_BASE_URL=http://localhost:8080.

Endpoints implemented:
  Trading: /v2/account, /v2/orders, /v2/positions, /v2/clock
  Data:    /v2/stocks/{sym}/bars, /v2/stocks/{sym}/snapshot,
           /v2/stocks/snapshots, /v1beta1/screener/*
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response

from ..clock import SimClock
from ..data_engine import DataEngine
from ..exchange import SimExchange
from .models import OrderRequest

logger = logging.getLogger(__name__)


def create_app(
    exchange: SimExchange,
    data_engine: DataEngine,
    clock: SimClock,
) -> FastAPI:
    """Create the FastAPI app wired to arena components."""

    app = FastAPI(title="mx-arena", version="0.1.0")

    # ── Authentication middleware ─────────────────────────────────

    @app.middleware("http")
    async def validate_auth(request: Request, call_next):
        """Accept any non-empty API key (sim mode)."""
        key = request.headers.get("APCA-API-KEY-ID", "")
        if not key and request.url.path not in ("/", "/docs", "/openapi.json"):
            return Response(status_code=401, content="Unauthorized")
        return await call_next(request)

    # ── Account ──────────────────────────────────────────────────

    @app.get("/v2/account")
    async def get_account():
        return exchange.account.to_alpaca_dict(exchange.positions)

    # ── Orders ───────────────────────────────────────────────────

    @app.post("/v2/orders")
    async def create_order(req: OrderRequest):
        try:
            order = exchange.submit_order(
                symbol=req.symbol,
                qty=req.qty or 0,
                side=req.side,
                order_type=req.type,
                time_in_force=req.time_in_force,
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                trail_percent=req.trail_percent,
                order_class=req.order_class,
                take_profit=req.take_profit,
                stop_loss=req.stop_loss,
                client_order_id=req.client_order_id,
            )
            return order.to_alpaca_dict()
        except Exception as e:
            logger.error("Order submission failed: %s", e)
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/v2/orders")
    async def list_orders(
        status: str = "open",
        symbols: Optional[str] = None,
        limit: int = 100,
    ):
        orders = exchange.get_orders(status=status, symbols=symbols, limit=limit)
        return [o.to_alpaca_dict() for o in orders]

    @app.get("/v2/orders/{order_id}")
    async def get_order(order_id: str):
        order = exchange.orders.get(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order.to_alpaca_dict()

    @app.delete("/v2/orders/{order_id}")
    async def cancel_order(order_id: str):
        order = exchange.cancel_order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return Response(status_code=204)

    @app.delete("/v2/orders")
    async def cancel_all_orders():
        canceled = []
        for oid in list(exchange.orders.keys()):
            result = exchange.cancel_order(oid)
            if result:
                canceled.append(result.to_alpaca_dict())
        return canceled

    # ── Positions ────────────────────────────────────────────────

    @app.get("/v2/positions")
    async def list_positions():
        return [p.to_alpaca_dict() for p in exchange.positions.values()]

    @app.get("/v2/positions/{symbol_or_id}")
    async def get_position(symbol_or_id: str):
        pos = exchange.positions.get(symbol_or_id.upper())
        if pos is None:
            raise HTTPException(status_code=404, detail="position does not exist")
        return pos.to_alpaca_dict()

    @app.delete("/v2/positions/{symbol_or_id}")
    async def close_position(symbol_or_id: str, qty: Optional[float] = None):
        order = exchange.close_position(symbol_or_id, qty)
        if order is None:
            raise HTTPException(status_code=404, detail="position does not exist")
        return order.to_alpaca_dict()

    @app.delete("/v2/positions")
    async def close_all_positions():
        closed = []
        for symbol in list(exchange.positions.keys()):
            order = exchange.close_position(symbol)
            if order:
                closed.append(order.to_alpaca_dict())
        return closed

    # ── Clock ────────────────────────────────────────────────────

    @app.get("/v2/clock")
    async def get_clock():
        return {
            "timestamp": clock.now.isoformat(),
            "is_open": clock.is_market_open,
            "next_open": clock.next_open.isoformat(),
            "next_close": clock.next_close.isoformat(),
        }

    @app.get("/v2/calendar")
    async def get_calendar():
        # Simplified: return single trading day
        et = clock.now_et
        return [{
            "date": et.strftime("%Y-%m-%d"),
            "open": "09:30",
            "close": "16:00",
            "session_open": "04:00",
            "session_close": "20:00",
        }]

    # ── Market Data: Bars ────────────────────────────────────────

    @app.get("/v2/stocks/{symbol}/bars")
    async def get_bars(
        symbol: str,
        timeframe: str = "1Min",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 1000,
        adjustment: str = "split",
        feed: str = "sip",
    ):
        bars = data_engine.get_bars(
            symbol=symbol, timeframe=timeframe, limit=limit,
            start=start, end=end,
        )
        return {
            "bars": bars,
            "symbol": symbol.upper(),
            "next_page_token": None,
        }

    @app.get("/v1/bars")
    async def get_bars_multi(
        symbols: str = "",
        timeframe: str = "1Min",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 1000,
    ):
        result = {}
        for sym in symbols.split(","):
            sym = sym.strip()
            if sym:
                result[sym] = data_engine.get_bars(
                    symbol=sym, timeframe=timeframe, limit=limit,
                    start=start, end=end,
                )
        return {"bars": result, "next_page_token": None}

    # ── Market Data: Snapshots ───────────────────────────────────

    @app.get("/v2/stocks/{symbol}/snapshot")
    async def get_snapshot(symbol: str, feed: str = "sip"):
        return data_engine.get_snapshot(symbol)

    @app.get("/v2/stocks/snapshots")
    async def get_multi_snapshot(
        symbols: str = Query(...),
        feed: str = "sip",
    ):
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        return data_engine.get_snapshots(sym_list)

    # ── Screener ─────────────────────────────────────────────────

    @app.get("/v1beta1/screener/stocks/most-actives")
    async def most_actives(by: str = "volume", top: int = 20):
        tickers = data_engine.get_most_active(limit=top)
        return {
            "most_actives": [
                {"symbol": t, "volume": 1_000_000, "trade_count": 50_000}
                for t in tickers
            ],
            "last_updated": clock.now.isoformat(),
        }

    @app.get("/v1beta1/screener/stocks/movers")
    async def top_movers(top: int = 20):
        tickers = data_engine.get_top_movers(limit=top)
        return {
            "gainers": [
                {"symbol": t, "percent_change": 10.0, "change": 1.0, "price": 10.0}
                for t in tickers
            ],
            "losers": [],
            "last_updated": clock.now.isoformat(),
        }

    # ── News (stub) ──────────────────────────────────────────────

    @app.get("/v1beta3/news")
    async def get_news(
        symbols: Optional[str] = None,
        limit: int = 10,
    ):
        return {"news": [], "next_page_token": None}

    # ── Assets (stub) ────────────────────────────────────────────

    @app.get("/v2/assets/{symbol}")
    async def get_asset(symbol: str):
        return {
            "id": "00000000-0000-0000-0000-000000000000",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "status": "active",
            "tradable": True,
            "marginable": True,
            "maintenance_margin_requirement": 25,
            "shortable": False,
            "easy_to_borrow": False,
            "fractionable": True,
        }

    return app
