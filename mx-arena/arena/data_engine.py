"""
Data Engine — Historical data replay + snapshot construction.

Loads 1-minute bars from Parquet or JSON files and constructs the
snapshot/bar responses that the bot expects from Alpaca's data API.

Supported layouts:
    Parquet: data/historical/{symbol}/{date}.parquet
    JSON:    data/bars/bars_{symbol}_{date}.json (existing MX cache format)
    Daily:   data/daily/{symbol}.parquet or data/daily/{symbol}_daily.json

Each file has Alpaca bar fields: t, o, h, l, c, v, vw, n
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .clock import SimClock
from .fill_model import Bar

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


class DataEngine:
    """
    Loads historical data and serves it through the simulated clock.

    Provides:
    - get_bars(): Historical OHLCV bars (used by orchestrator for ATR, SPY, VIX)
    - get_snapshot(): Point-in-time snapshot (used by scanner for gap%, RVOL)
    - get_current_bar(): Current tick's bar for matching engine
    - screener data: Most active tickers / top movers from historical watchlists
    """

    def __init__(
        self,
        clock: SimClock,
        historical_dir: str | Path,
        daily_dir: str | Path | None = None,
        json_bars_dir: str | Path | None = None,
        symbols: list[str] | None = None,
    ):
        self.clock = clock
        self.historical_dir = Path(historical_dir)
        self.daily_dir = Path(daily_dir) if daily_dir else self.historical_dir.parent / "daily"
        # D130: Existing MX bar cache (data/bars/bars_{SYMBOL}_{DATE}.json)
        self.json_bars_dir = Path(json_bars_dir) if json_bars_dir else None
        self.symbols = [s.upper() for s in (symbols or [])]

        # Loaded data: {symbol: {minute_index: Bar}}
        self._minute_bars: dict[str, dict[int, Bar]] = {}
        # Daily bars: {symbol: list[dict]}
        self._daily_bars: dict[str, list[dict]] = {}
        # Previous daily bars for snapshot: {symbol: dict}
        self._prev_daily: dict[str, dict] = {}
        # Today's aggregate bar (built up tick by tick): {symbol: Bar}
        self._today_agg: dict[str, dict] = {}

        # Market data stream listeners
        self._market_listeners: list[asyncio.Queue] = []

        # Screener data
        self._watchlist: list[str] = []

    def load_day(self, date_str: str, symbols: list[str] | None = None) -> None:
        """Load minute-bar data for one day from Parquet or JSON files."""
        target_symbols = [s.upper() for s in (symbols or self.symbols)]
        loaded = 0

        for symbol in target_symbols:
            # Try Parquet first
            bars = self._load_parquet_bars(symbol, date_str)

            # D130: Fall back to JSON bar cache (data/bars/bars_{SYMBOL}_{DATE}.json)
            if bars is None:
                bars = self._load_json_bars(symbol, date_str)

            if bars is not None:
                self._minute_bars[symbol] = bars
                loaded += 1

        # Set watchlist to loaded symbols
        self._watchlist = [s for s in target_symbols if s in self._minute_bars]
        logger.info("Loaded %d/%d symbols for %s", loaded, len(target_symbols), date_str)

    def _load_parquet_bars(self, symbol: str, date_str: str) -> dict[int, Bar] | None:
        """Load bars from Parquet file."""
        if not HAS_PANDAS:
            return None

        for path in [
            self.historical_dir / symbol / f"{date_str}.parquet",
            self.historical_dir / f"{symbol}_{date_str}.parquet",
        ]:
            if path.exists():
                df = pd.read_parquet(path)
                # P2 fix: Sort by timestamp to ensure chronological order
                ts_col = "timestamp" if "timestamp" in df.columns else "t"
                if ts_col in df.columns:
                    df = df.sort_values(ts_col).reset_index(drop=True)
                bars = {}
                for i, row in df.iterrows():
                    ts = str(row.get("timestamp", row.get("t", "")))
                    bar = Bar(
                        timestamp=ts,
                        open=float(row.get("o", row.get("open", 0))),
                        high=float(row.get("h", row.get("high", 0))),
                        low=float(row.get("l", row.get("low", 0))),
                        close=float(row.get("c", row.get("close", 0))),
                        volume=int(row.get("v", row.get("volume", 0))),
                        vwap=float(row.get("vw", row.get("vwap", 0))),
                        trade_count=int(row.get("n", row.get("trade_count", 0))),
                    )
                    bars[len(bars)] = bar
                return bars
        return None

    def _load_json_bars(self, symbol: str, date_str: str) -> dict[int, Bar] | None:
        """D130: Load bars from JSON cache (data/bars/bars_{SYMBOL}_{DATE}.json)."""
        search_dirs = [self.historical_dir]
        if self.json_bars_dir:
            search_dirs.insert(0, self.json_bars_dir)

        for base in search_dirs:
            for path in [
                base / f"bars_{symbol}_{date_str}.json",
                base / symbol / f"{date_str}.json",
                base / f"{symbol}_{date_str}.json",
            ]:
                if path.exists():
                    with open(path) as f:
                        raw_bars = json.load(f)
                    # P2 fix: Sort by timestamp to ensure chronological order
                    raw_bars.sort(key=lambda b: b.get("t", ""))
                    bars = {}
                    for entry in raw_bars:
                        bar = Bar(
                            timestamp=str(entry.get("t", "")),
                            open=float(entry.get("o", 0)),
                            high=float(entry.get("h", 0)),
                            low=float(entry.get("l", 0)),
                            close=float(entry.get("c", 0)),
                            volume=int(entry.get("v", 0)),
                            vwap=float(entry.get("vw", 0)),
                            trade_count=int(entry.get("n", 0)),
                        )
                        bars[len(bars)] = bar
                    logger.debug("Loaded %d bars from JSON: %s", len(bars), path)
                    return bars
        return None

    def load_daily(self, symbol: str) -> list[dict]:
        """Load daily bars for one symbol (for ATR computation, SPY/VIX)."""
        if symbol in self._daily_bars:
            return self._daily_bars[symbol]

        path = self.daily_dir / f"{symbol}.parquet"
        if not path.exists():
            path = self.daily_dir / symbol / "daily.parquet"
        if not path.exists():
            return []

        if HAS_PANDAS:
            df = pd.read_parquet(path)
            bars = []
            for _, row in df.iterrows():
                bars.append({
                    "t": str(row.get("timestamp", row.get("t", ""))),
                    "o": float(row.get("o", row.get("open", 0))),
                    "h": float(row.get("h", row.get("high", 0))),
                    "l": float(row.get("l", row.get("low", 0))),
                    "c": float(row.get("c", row.get("close", 0))),
                    "v": int(row.get("v", row.get("volume", 0))),
                    "vw": float(row.get("vw", row.get("vwap", 0))),
                    "n": int(row.get("n", row.get("trade_count", 0))),
                })
            self._daily_bars[symbol] = bars
            return bars
        return []

    def set_prev_daily(self, symbol: str, bar: dict) -> None:
        """Manually set previous daily bar for snapshot construction."""
        self._prev_daily[symbol.upper()] = bar

    def get_bars_at_tick(self, tick_index: int) -> dict[str, Bar]:
        """Get all bars for a given tick (minute) index."""
        result = {}
        for symbol, bars in self._minute_bars.items():
            if tick_index in bars:
                result[symbol] = bars[tick_index]
                # Update today's aggregate
                self._update_daily_aggregate(symbol, bars[tick_index])
        return result

    def _update_daily_aggregate(self, symbol: str, bar: Bar) -> None:
        """Accumulate today's daily bar from minute bars."""
        if symbol not in self._today_agg:
            self._today_agg[symbol] = {
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
                "vw": bar.vwap,
                "n": bar.trade_count,
            }
        else:
            agg = self._today_agg[symbol]
            agg["h"] = max(agg["h"], bar.high)
            agg["l"] = min(agg["l"], bar.low)
            agg["c"] = bar.close
            agg["v"] += bar.volume
            agg["n"] += bar.trade_count

    # ── API Methods (called by REST endpoints) ───────────────────────

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        """
        Construct Alpaca-format snapshot for a symbol.

        Used by the bot's scanner for gap%, RVOL, and current price.
        """
        symbol = symbol.upper()
        bars = self._minute_bars.get(symbol, {})
        agg = self._today_agg.get(symbol, {})
        prev = self._prev_daily.get(symbol, {})

        # Find current bar (latest loaded)
        current_bar = None
        if bars:
            max_idx = max(bars.keys())
            current_bar = bars[max_idx]

        current_price = current_bar.close if current_bar else 0.0
        prev_close = prev.get("c", 0.0)

        return {
            "latestTrade": {
                "t": self.clock.now.isoformat(),
                "p": current_price,
                "s": 100,
                "x": "V",
                "c": ["@"],
            },
            "latestQuote": {
                "t": self.clock.now.isoformat(),
                "bp": round(current_price * 0.999, 2),
                "ap": round(current_price * 1.001, 2),
                "bs": 100,
                "as": 100,
                "bx": "V",
                "ax": "V",
            },
            "minuteBar": {
                "t": current_bar.timestamp if current_bar else "",
                "o": current_bar.open if current_bar else 0,
                "h": current_bar.high if current_bar else 0,
                "l": current_bar.low if current_bar else 0,
                "c": current_bar.close if current_bar else 0,
                "v": current_bar.volume if current_bar else 0,
                "vw": current_bar.vwap if current_bar else 0,
                "n": current_bar.trade_count if current_bar else 0,
            },
            "dailyBar": {
                "t": self.clock.now.strftime("%Y-%m-%dT00:00:00Z"),
                "o": agg.get("o", current_price),
                "h": agg.get("h", current_price),
                "l": agg.get("l", current_price),
                "c": agg.get("c", current_price),
                "v": agg.get("v", 0),
                "vw": agg.get("vw", 0),
                "n": agg.get("n", 0),
            },
            "prevDailyBar": {
                "t": prev.get("t", ""),
                "o": prev.get("o", 0),
                "h": prev.get("h", 0),
                "l": prev.get("l", 0),
                "c": prev_close,
                "v": prev.get("v", 0),
                "vw": prev.get("vw", 0),
                "n": prev.get("n", 0),
            },
        }

    def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """Get snapshots for multiple symbols."""
        return {s: self.get_snapshot(s) for s in symbols}

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        limit: int = 200,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return historical bars in Alpaca format.

        For 1Min: return from loaded minute bars.
        For 1Day: return from loaded daily bars.
        """
        symbol = symbol.upper()

        if timeframe in ("1Day", "1D"):
            daily = self.load_daily(symbol)
            # Filter by date range if provided
            if start:
                daily = [b for b in daily if b["t"] >= start]
            if end:
                daily = [b for b in daily if b["t"] <= end]
            return daily[-limit:]

        # Minute bars
        bars = self._minute_bars.get(symbol, {})
        result = []
        for idx in sorted(bars.keys()):
            bar = bars[idx]
            result.append({
                "t": bar.timestamp,
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
                "vw": bar.vwap,
                "n": bar.trade_count,
            })
        return result[-limit:]

    def get_most_active(self, limit: int = 20) -> list[str]:
        """Return watchlist symbols ranked by volume (Gap 5: dynamic screener)."""
        if not self._minute_bars:
            return self._watchlist[:limit]
        # Rank by total volume (most active = highest volume)
        vol_ranked = sorted(
            self._minute_bars.keys(),
            key=lambda s: sum(b.volume for b in self._minute_bars[s].values()),
            reverse=True,
        )
        return vol_ranked[:limit]

    def get_top_movers(self, limit: int = 20) -> list[str]:
        """Return symbols with largest gap% (simulated movers screener)."""
        movers = []
        for symbol in self._watchlist:
            prev = self._prev_daily.get(symbol, {})
            prev_close = prev.get("c", 0)
            bars = self._minute_bars.get(symbol, {})
            if bars and prev_close > 0:
                first_bar = bars.get(0)
                if first_bar:
                    gap_pct = (first_bar.open - prev_close) / prev_close
                    movers.append((symbol, gap_pct))

        movers.sort(key=lambda x: abs(x[1]), reverse=True)
        return [s for s, _ in movers[:limit]]

    # ── Market data streaming ────────────────────────────────────────

    def register_market_listener(self, queue: asyncio.Queue) -> None:
        self._market_listeners.append(queue)

    def unregister_market_listener(self, queue: asyncio.Queue) -> None:
        if queue in self._market_listeners:
            self._market_listeners.remove(queue)

    async def emit_market_data(self, bars: dict[str, Bar]) -> None:
        """Push bar/quote data to WebSocket listeners."""
        for q in self._market_listeners:
            for symbol, bar in bars.items():
                try:
                    q.put_nowait({
                        "type": "bar",
                        "symbol": symbol,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "timestamp": bar.timestamp,
                        "trade_count": bar.trade_count,
                        "vwap": bar.vwap,
                    })
                except asyncio.QueueFull:
                    pass
