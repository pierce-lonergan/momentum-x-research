#!/usr/bin/env python3
"""
Download historical data from Alpaca for arena simulations.

Downloads 1-minute bars and daily bars for all symbols that appeared
on the MOMENTUM-X watchlist (extracted from trade journals).

Usage:
    python mx-arena/scripts/download_history.py --days 90
    python mx-arena/scripts/download_history.py --symbols AAPL,TSLA --start 2026-01-01
    python mx-arena/scripts/download_history.py --symbols-from data/journals/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    print("Warning: pandas not installed. Install with: pip install pandas pyarrow")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ALPACA_DATA_URL = "https://data.alpaca.markets"


def extract_symbols_from_journals(journals_dir: str) -> list[str]:
    """Extract unique ticker symbols from trade journal files."""
    symbols = set()
    journal_path = Path(journals_dir)

    for f in journal_path.glob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ticker = entry.get("ticker", "")
                    if ticker:
                        symbols.add(ticker.upper())
                except json.JSONDecodeError:
                    continue

    logger.info("Found %d unique symbols in journals", len(symbols))
    return sorted(symbols)


async def download_bars(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    headers: dict,
) -> list[dict]:
    """Download bars from Alpaca data API with pagination."""
    all_bars = []
    page_token = None

    while True:
        params: dict = {
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "limit": "10000",
            "adjustment": "split",
            "feed": "sip",
        }
        if page_token:
            params["page_token"] = page_token

        url = f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars"
        resp = await client.get(url, params=params, headers=headers)

        if resp.status_code == 429:
            logger.warning("Rate limited, waiting 60s...")
            await asyncio.sleep(60)
            continue

        if resp.status_code != 200:
            logger.warning(
                "Failed to download %s %s: %d %s",
                symbol, timeframe, resp.status_code, resp.text[:100],
            )
            break

        data = resp.json()
        bars = data.get("bars", [])
        if not bars:
            break

        all_bars.extend(bars)
        page_token = data.get("next_page_token")
        if not page_token:
            break

    return all_bars


async def download_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    start_date: str,
    end_date: str,
    output_dir: Path,
    headers: dict,
    timeframe: str = "1Min",
) -> int:
    """Download and save bars for one symbol."""
    bars = await download_bars(
        client, symbol, timeframe, start_date, end_date, headers,
    )
    if not bars:
        return 0

    if not HAS_PANDAS:
        # Save as JSON fallback
        out_path = output_dir / symbol / f"{start_date}_{end_date}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(bars, f)
        return len(bars)

    df = pd.DataFrame(bars)
    # Rename Alpaca columns to standard names
    col_map = {"t": "timestamp", "o": "o", "h": "h", "l": "l", "c": "c", "v": "v", "vw": "vw", "n": "n"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    if timeframe in ("1Day", "1D"):
        # Single parquet per symbol
        out_path = output_dir / f"{symbol}.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path, index=False)
    else:
        # Group by date and save per-day files
        if "timestamp" in df.columns:
            df["date"] = pd.to_datetime(df["timestamp"]).dt.date.astype(str)
        elif "t" in df.columns:
            df["date"] = pd.to_datetime(df["t"]).dt.date.astype(str)
        else:
            df["date"] = start_date

        for date_str, group in df.groupby("date"):
            out_path = output_dir / symbol / f"{date_str}.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            group.drop(columns=["date"]).to_parquet(out_path, index=False)

    return len(bars)


async def main():
    parser = argparse.ArgumentParser(description="Download Alpaca historical data")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols")
    parser.add_argument("--symbols-from", type=str, help="Extract symbols from journal dir")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--timeframe", type=str, default="1Min", help="1Min or 1Day")
    parser.add_argument(
        "--output", type=str, default="mx-arena/data/historical",
        help="Output directory",
    )
    args = parser.parse_args()

    # Get API credentials
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("Error: Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables")
        sys.exit(1)

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }

    # Determine symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    elif args.symbols_from:
        symbols = extract_symbols_from_journals(args.symbols_from)
    else:
        print("Error: Provide --symbols or --symbols-from")
        sys.exit(1)

    # Determine date range
    end_date = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.start:
        start_date = args.start
    else:
        start_dt = datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=args.days)
        start_date = start_dt.strftime("%Y-%m-%d")

    output_dir = Path(args.output)
    if args.timeframe in ("1Day", "1D"):
        output_dir = output_dir.parent / "daily"

    logger.info(
        "Downloading %d symbols, %s to %s, timeframe=%s",
        len(symbols), start_date, end_date, args.timeframe,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        total_bars = 0
        for i, symbol in enumerate(symbols):
            count = await download_symbol(
                client, symbol, start_date, end_date,
                output_dir, headers, args.timeframe,
            )
            total_bars += count
            if count > 0:
                logger.info("[%d/%d] %s: %d bars", i + 1, len(symbols), symbol, count)
            else:
                logger.debug("[%d/%d] %s: no data", i + 1, len(symbols), symbol)

            # Rate limit: ~3 req/sec
            await asyncio.sleep(0.35)

    logger.info("Done. Total: %d bars saved to %s", total_bars, output_dir)


if __name__ == "__main__":
    asyncio.run(main())
