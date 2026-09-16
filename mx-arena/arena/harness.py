"""
ArenaInstance — Single simulation harness.

Encapsulates one complete simulation: clock + exchange + data + API + bot.
This is the unit of parallelization — the runner spawns many of these.

Two modes:
- HTTP mode: Full FastAPI server, bot connects via HTTP/WS (~5s/day)
- Direct mode: Patch AlpacaDataClient directly, skip HTTP (~0.5s/day)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import WebSocket

from .api.rest import create_app
from .api.ws_trading import trading_stream_handler
from .api.ws_market import market_data_stream_handler
from .clock import ClockMode, SimClock
from .data_engine import DataEngine
from .exchange import SimExchange
from .fill_model import AlpacaFillModel, RealisticFillModel
from .spread_model import SpreadModel

logger = logging.getLogger(__name__)

ET_OFFSET = timedelta(hours=-4)  # EDT (simplified)


@dataclass
class ArenaConfig:
    """Configuration for one simulation instance."""
    date: str                                   # "2026-03-25"
    symbols: list[str]                          # Watchlist tickers
    data_dir: str | Path = "mx-arena/data/historical"
    daily_dir: str | Path | None = None
    initial_cash: float = 100_000.0
    seed: int = 42
    clock_mode: ClockMode = ClockMode.REPLAY
    fill_model: str = "alpaca"                  # "alpaca" or "realistic"
    rest_port: int = 8080
    ws_trade_port: int = 8081
    ws_market_port: int = 8082
    param_overrides: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    # Previous daily bars for snapshot (prev_close for gap calculation)
    prev_daily_bars: dict[str, dict] = field(default_factory=dict)

    @property
    def session_start(self) -> datetime:
        """4:00 AM ET on the simulation date (pre-market)."""
        return datetime.strptime(
            f"{self.date} 04:00:00", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone(ET_OFFSET))

    @property
    def session_end(self) -> datetime:
        """8:00 PM ET on the simulation date (after-hours)."""
        return datetime.strptime(
            f"{self.date} 20:00:00", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone(ET_OFFSET))


@dataclass
class RunResult:
    """Results from a single simulation run."""
    config_id: str
    date: str
    params: dict[str, Any]
    trades: list[dict[str, Any]]
    pnl: float
    positions_eod: list[dict]
    orders_total: int
    signal_history: list[dict]

    @property
    def win_rate(self) -> float:
        wins = [t for t in self.trades if t.get("pnl", 0) > 0]
        total = [t for t in self.trades if "pnl" in t]
        return len(wins) / max(len(total), 1)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t["pnl"] for t in self.trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.trades if t.get("pnl", 0) < 0))
        return gross_profit / max(gross_loss, 0.01)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "date": self.date,
            "params": self.params,
            "pnl": self.pnl,
            "trade_count": len(self.trades),
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "orders_total": self.orders_total,
        }


class ArenaInstance:
    """
    One complete simulation: clock + exchange + data + API + bot.
    """

    def __init__(self, config: ArenaConfig):
        self.config = config

        # Core components
        self.clock = SimClock(
            start=config.session_start,
            end=config.session_end,
            mode=config.clock_mode,
        )

        fill_model = (
            RealisticFillModel() if config.fill_model == "realistic"
            else AlpacaFillModel()
        )

        self.spread_model = SpreadModel()

        self.exchange = SimExchange(
            clock=self.clock,
            fill_model=fill_model,
            spread_model=self.spread_model,
            initial_cash=config.initial_cash,
            seed=config.seed,
        )

        self.data_engine = DataEngine(
            clock=self.clock,
            historical_dir=config.data_dir,
            daily_dir=config.daily_dir,
            symbols=config.symbols,
        )

        # Wire components
        self._tick_index = 0

    def load_data(self) -> None:
        """Load historical data for the simulation date."""
        self.data_engine.load_day(self.config.date, self.config.symbols)

        # Set previous daily bars for snapshot construction
        for symbol, bar in self.config.prev_daily_bars.items():
            self.data_engine.set_prev_daily(symbol, bar)

    async def _on_tick(self, timestamp: datetime) -> None:
        """Clock tick handler: feed data → exchange."""
        bars = self.data_engine.get_bars_at_tick(self._tick_index)
        if bars:
            self.exchange.update_market_data(bars)
            await self.exchange.on_tick(timestamp)
            await self.data_engine.emit_market_data(bars)
        self._tick_index += 1

    async def run_http_mode(self) -> RunResult:
        """
        Full HTTP simulation — highest fidelity.

        Starts FastAPI server, sets environment variables to redirect
        the bot's httpx clients, then runs bot + clock concurrently.
        """
        # Create FastAPI app with WebSocket routes
        app = create_app(self.exchange, self.data_engine, self.clock)

        # Add WebSocket routes
        @app.websocket("/stream")
        async def ws_trading(ws: WebSocket):
            await trading_stream_handler(ws, self.exchange)

        @app.websocket("/v2/{feed}")
        async def ws_market(ws: WebSocket, feed: str):
            await market_data_stream_handler(ws, self.data_engine, feed)

        # Load data
        self.load_data()

        # Wire tick handler
        self.clock.subscribe_async(self._on_tick)

        # Set environment for bot
        os.environ["ALPACA_BASE_URL"] = f"http://localhost:{self.config.rest_port}"
        os.environ["ALPACA_DATA_URL"] = f"http://localhost:{self.config.rest_port}"
        os.environ["ALPACA_API_KEY"] = "SIM_KEY"
        os.environ["ALPACA_SECRET_KEY"] = "SIM_SECRET"

        # Start server
        server_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.config.rest_port,
            log_level="warning",
        )
        server = uvicorn.Server(server_config)

        logger.info(
            "Arena HTTP mode: %s, %d symbols, port %d",
            self.config.date, len(self.config.symbols), self.config.rest_port,
        )

        # Run server + clock concurrently
        server_task = asyncio.create_task(server.serve())

        # Wait for server to be ready
        await asyncio.sleep(0.5)

        # Drive the clock
        await self.clock.run()

        # Shutdown server
        server.should_exit = True
        await server_task

        return self._collect_results()

    async def run_standalone(self) -> RunResult:
        """
        Run simulation without bot — just drive clock + exchange.

        Useful for testing the arena components independently or
        for direct-mode parameter sweeps.
        """
        self.load_data()
        self.clock.subscribe_async(self._on_tick)
        await self.clock.run()
        return self._collect_results()

    def _collect_results(self) -> RunResult:
        return RunResult(
            config_id=self.config.id or self.config.date,
            date=self.config.date,
            params=self.config.param_overrides,
            trades=self.exchange.trade_history,
            pnl=self.exchange.realized_pnl,
            positions_eod=[
                p.to_alpaca_dict() for p in self.exchange.positions.values()
            ],
            orders_total=len(self.exchange.all_orders),
            signal_history=self.exchange.signal_log,
        )
