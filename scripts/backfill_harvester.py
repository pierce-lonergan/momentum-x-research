"""
D219: Historical Candidate Harvester

Builds the dataset MOMENTUM-X would have produced if it had been running
for the last 90 trading days. Uses Alpaca's historical data API.

Strategy:
1. Get all active US equities from Alpaca (~12K tickers)
2. For each trading day, pull daily bars in batches of 200 tickers
3. Compute gap% from prior close to current open
4. Filter for EMC criteria: gap>=5%, price $2-$100, dolvol>=$2M
5. Output: data/backfill/candidates.jsonl

Usage:
    python scripts/backfill_harvester.py [--days 90] [--batch-size 200]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("backfill")


async def get_tradable_tickers(key: str, secret: str) -> list[str]:
    """Get all exchange-listed tradable US equities."""
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://paper-api.alpaca.markets/v2/assets",
            params={"status": "active", "asset_class": "us_equity"},
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        resp.raise_for_status()
        assets = resp.json()

    # Filter: exchange-listed, tradable, not OTC
    tickers = [
        a["symbol"] for a in assets
        if a.get("tradable")
        and a.get("exchange") in ("NYSE", "NASDAQ", "ARCA", "AMEX", "BATS")
        and not a.get("symbol", "").endswith(".WS")
        and "." not in a.get("symbol", "X")  # Skip derivative symbols
    ]
    logger.info("Found %d exchange-listed tradable tickers", len(tickers))
    return tickers


async def get_daily_bars_batch(
    tickers: list[str], date_str: str, key: str, secret: str,
) -> dict[str, dict]:
    """Fetch daily bars for a batch of tickers on a specific date."""
    import httpx
    symbols = ",".join(tickers)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://data.alpaca.markets/v2/stocks/bars",
            params={
                "timeframe": "1Day",
                "start": f"{date_str}T00:00:00Z",
                "end": f"{date_str}T23:59:59Z",
                "symbols": symbols,
                "limit": 10000,
            },
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        if resp.status_code == 429:
            logger.warning("Rate limited, sleeping 5s...")
            await asyncio.sleep(5)
            return {}
        if resp.status_code != 200:
            return {}
        data = resp.json()
        result = {}
        bars_dict = data.get("bars", {})
        for ticker, bars in bars_dict.items():
            if bars and len(bars) > 0:
                result[ticker] = bars[0]  # Daily bar
        return result


def get_trading_days(num_days: int) -> list[str]:
    """Get the last N trading days (weekdays, skip known holidays)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    today = datetime.now(et).date()
    days = []
    d = today - timedelta(days=1)  # Start from yesterday
    while len(days) < num_days:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return list(reversed(days))


async def harvest(num_days: int = 90, batch_size: int = 200):
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        return

    output_dir = Path("data/backfill")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "candidates.jsonl"

    # Get universe
    tickers = await get_tradable_tickers(key, secret)
    trading_days = get_trading_days(num_days)
    logger.info("Scanning %d days × %d tickers (in batches of %d)",
                len(trading_days), len(tickers), batch_size)

    total_candidates = 0
    with open(output_file, "w", encoding="utf-8") as f:
        for day_idx, date in enumerate(trading_days):
            # Get prior trading day for gap calculation
            prior_date = trading_days[day_idx - 1] if day_idx > 0 else None
            if prior_date is None:
                continue

            day_candidates = 0
            day_start = time.monotonic()

            # Fetch bars in batches
            all_today = {}
            all_prior = {}

            for i in range(0, len(tickers), batch_size):
                batch = tickers[i:i + batch_size]

                today_bars = await get_daily_bars_batch(batch, date, key, secret)
                prior_bars = await get_daily_bars_batch(batch, prior_date, key, secret)

                all_today.update(today_bars)
                all_prior.update(prior_bars)

                # Rate limiting: ~4 calls/sec (2 calls per batch = 2 batches/sec)
                await asyncio.sleep(0.5)

            # Scan for gap-up candidates
            for ticker, today in all_today.items():
                prior = all_prior.get(ticker)
                if not prior:
                    continue

                prior_close = prior.get("c", 0)
                today_open = today.get("o", 0)
                today_high = today.get("h", 0)
                today_low = today.get("l", 0)
                today_close = today.get("c", 0)
                today_volume = today.get("v", 0)
                today_vwap = today.get("vw", 0)

                if prior_close <= 0 or today_open <= 0:
                    continue

                gap_pct = (today_open - prior_close) / prior_close
                dollar_volume = today_volume * today_vwap if today_vwap > 0 else today_volume * today_open

                # EMC filter (matching D219 thresholds)
                if (
                    gap_pct >= 0.05
                    and today_open >= 2.00
                    and today_open <= 100.00
                    and dollar_volume >= 2_000_000
                ):
                    candidate = {
                        "date": date,
                        "ticker": ticker,
                        "prior_close": round(prior_close, 4),
                        "open": round(today_open, 4),
                        "high": round(today_high, 4),
                        "low": round(today_low, 4),
                        "close": round(today_close, 4),
                        "volume": today_volume,
                        "vwap": round(today_vwap, 4),
                        "gap_pct": round(gap_pct, 4),
                        "dollar_volume": round(dollar_volume, 0),
                    }
                    f.write(json.dumps(candidate) + "\n")
                    day_candidates += 1
                    total_candidates += 1

            elapsed = time.monotonic() - day_start
            logger.info(
                "Day %d/%d: %s — %d candidates (%.1fs, %d tickers scanned)",
                day_idx + 1, len(trading_days), date, day_candidates,
                elapsed, len(all_today),
            )

    logger.info("COMPLETE: %d total candidates across %d days → %s",
                total_candidates, len(trading_days), output_file)


def main():
    parser = argparse.ArgumentParser(description="Historical candidate harvester")
    parser.add_argument("--days", type=int, default=90, help="Number of trading days to scan")
    parser.add_argument("--batch-size", type=int, default=200, help="Tickers per API call")
    args = parser.parse_args()

    asyncio.run(harvest(num_days=args.days, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
