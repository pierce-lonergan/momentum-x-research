"""Unified bar source: merge polygon_cache, bar_recordings, alpaca_backfill.

Priority order (most-preferred first):
1. polygon_cache (full SIP, extended hours, bit-perfect OHLC)
2. bar_recordings (legacy IEX-only, regular session only)
3. alpaca_backfill (older Alpaca historical, regular session only)

Returns a list of bars with a uniform schema:
    {ts_ms: int, open: float, high: float, low: float,
     close: float, volume: float, vwap: float|None, source: str}

Usage:
    from data_providers.unified_bar_source import get_bars
    bars = get_bars("AKAN", "2026-04-29")
    for b in bars:
        print(b["ts_ms"], b["close"], b["source"])
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

POLYGON_EQUITY = REPO_ROOT / "data" / "polygon_backfill" / "equity_minute_bars"
POLYGON_ETF = REPO_ROOT / "data" / "polygon_backfill" / "etf_minute_bars"
BAR_RECORDINGS = REPO_ROOT / "data" / "bar_recordings"
ALPACA_BACKFILL = REPO_ROOT / "data" / "backfill_alpaca_v0.1"  # may not exist


def _load_polygon(ticker: str, date_str: str) -> Optional[list[dict]]:
    """Polygon parquet covers a wide date range per file. Filter to one day."""
    for root in (POLYGON_EQUITY, POLYGON_ETF):
        p = root / f"{ticker}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            logger.warning("polygon load %s failed: %s", p, e)
            continue
        # Day window in UTC ms
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        day_start = int(d.timestamp() * 1000)
        day_end = day_start + 86_400_000
        sub = df[(df["ts_ms"] >= day_start) & (df["ts_ms"] < day_end)]
        if sub.empty:
            continue
        return [
            {
                "ts_ms": int(r["ts_ms"]),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r["volume"]),
                "vwap": float(r["vwap"]) if pd.notna(r["vwap"]) else None,
                "source": "polygon",
            }
            for _, r in sub.iterrows()
        ]
    return None


def _load_bar_recordings(ticker: str, date_str: str) -> Optional[list[dict]]:
    p = BAR_RECORDINGS / date_str / f"{ticker}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("bar_recordings load %s failed: %s", p, e)
        return None
    out = []
    for b in obj.get("bars", []):
        ts = b.get("timestamp")
        if not ts:
            continue
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        out.append({
            "ts_ms": int(ts_dt.timestamp() * 1000),
            "open": float(b["open"]), "high": float(b["high"]),
            "low": float(b["low"]), "close": float(b["close"]),
            "volume": float(b["volume"]),
            "vwap": float(b["vwap"]) if b.get("vwap") is not None else None,
            "source": "bar_recordings",
        })
    return out or None


def _load_alpaca_backfill(ticker: str, date_str: str) -> Optional[list[dict]]:
    """Optional: legacy Alpaca backfill. Returns None if absent."""
    if not ALPACA_BACKFILL.exists():
        return None
    # Try parquet per ticker first
    p = ALPACA_BACKFILL / f"{ticker}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_start = int(d.timestamp() * 1000)
    day_end = day_start + 86_400_000
    if "ts_ms" not in df.columns:
        return None
    sub = df[(df["ts_ms"] >= day_start) & (df["ts_ms"] < day_end)]
    if sub.empty:
        return None
    return [
        {
            "ts_ms": int(r["ts_ms"]),
            "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
            "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
            "volume": float(r.get("volume") or 0),
            "vwap": float(r["vwap"]) if "vwap" in df.columns and pd.notna(r["vwap"]) else None,
            "source": "alpaca_backfill",
        }
        for _, r in sub.iterrows()
    ]


def get_bars(ticker: str, date_str: str) -> list[dict]:
    """Return 1-minute bars for (ticker, date) preferring Polygon.

    date_str: 'YYYY-MM-DD'

    Returns empty list if no source has data.
    """
    ticker = ticker.upper()
    for loader in (_load_polygon, _load_bar_recordings, _load_alpaca_backfill):
        bars = loader(ticker, date_str)
        if bars:
            bars.sort(key=lambda b: b["ts_ms"])
            return bars
    return []


def get_source(ticker: str, date_str: str) -> Optional[str]:
    """Tell which source `get_bars` would pick (for diagnostics)."""
    ticker = ticker.upper()
    for loader, name in [
        (_load_polygon, "polygon"),
        (_load_bar_recordings, "bar_recordings"),
        (_load_alpaca_backfill, "alpaca_backfill"),
    ]:
        if loader(ticker, date_str):
            return name
    return None
