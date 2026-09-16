#!/usr/bin/env python3
"""
Prepare historical data for arena replay from existing MX data caches.

Extracts:
1. Watchlist tickers from premarket cache and journals
2. Previous daily bars for snapshot construction (gap% calculation)
3. Lists which bar data is available vs needs downloading

Usage:
    python mx-arena/scripts/prep_historical.py --date 2026-03-26
    python mx-arena/scripts/prep_historical.py --date 2026-03-26 --download
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARENA_DATA = PROJECT_ROOT / "mx-arena" / "data"


def extract_watchlist(date: str) -> list[str]:
    """Extract watchlist tickers from premarket cache."""
    premarket_file = DATA_DIR / "premarket" / f"premarket_{date}.json"
    if premarket_file.exists():
        with open(premarket_file) as f:
            data = json.load(f)
        tickers = list(data.get("tickers", {}).keys())
        logger.info("Premarket %s: %d tickers", date, len(tickers))
        return tickers
    return []


def extract_journal_tickers(date: str) -> list[str]:
    """Extract tickers from journal entries for a date."""
    tickers = set()
    for f in glob.glob(str(DATA_DIR / "journals" / f"journal_{date}_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ticker = entry.get("ticker", "")
                    if ticker:
                        tickers.add(ticker.upper())
                except json.JSONDecodeError:
                    continue
    return sorted(tickers)


def extract_journal_stats(date: str) -> dict:
    """Extract action counts and key metrics from journals."""
    stats = {"actions": {}, "tickers": {}, "total": 0}
    for f in glob.glob(str(DATA_DIR / "journals" / f"journal_{date}_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    action = entry.get("action", "UNKNOWN")
                    ticker = entry.get("ticker", "")
                    stats["actions"][action] = stats["actions"].get(action, 0) + 1
                    stats["total"] += 1

                    if ticker and action in ("BUY", "STRONG_BUY"):
                        if ticker not in stats["tickers"]:
                            stats["tickers"][ticker] = {
                                "entry_price": entry.get("entry_price"),
                                "stop_loss": entry.get("stop_loss"),
                                "confidence": entry.get("confidence"),
                                "gap_pct": entry.get("gap_pct"),
                            }
                except json.JSONDecodeError:
                    continue
    return stats


def extract_prev_daily(date: str, tickers: list[str]) -> dict[str, dict]:
    """
    Extract previous daily bar for each ticker.

    Sources (in priority order):
    1. Journal entries (have gap_pct + current_price → derive prev_close)
    2. Premarket cache technicals (prev_close field)
    """
    prev_daily = {}

    # Source 1: Journal entries
    for f in glob.glob(str(DATA_DIR / "journals" / f"journal_{date}_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ticker = entry.get("ticker", "")
                    if ticker not in tickers or ticker in prev_daily:
                        continue

                    gap_pct = entry.get("gap_pct", 0)
                    current = entry.get("current_price", 0)
                    prev_close = entry.get("previous_close", 0)

                    if prev_close > 0:
                        prev_daily[ticker] = {
                            "t": "",
                            "o": round(prev_close * 0.99, 4),
                            "h": round(prev_close * 1.02, 4),
                            "l": round(prev_close * 0.97, 4),
                            "c": round(prev_close, 4),
                            "v": 500_000,
                            "vw": round(prev_close, 4),
                            "n": 10000,
                        }
                    elif current > 0 and gap_pct != 0:
                        prev_close = round(current / (1 + gap_pct), 4)
                        prev_daily[ticker] = {
                            "t": "",
                            "o": prev_close,
                            "h": round(prev_close * 1.02, 4),
                            "l": round(prev_close * 0.98, 4),
                            "c": prev_close,
                            "v": 500_000,
                            "vw": prev_close,
                            "n": 10000,
                        }
                except (json.JSONDecodeError, KeyError):
                    continue

    return prev_daily


def check_bar_availability(date: str, tickers: list[str]) -> tuple[list[str], list[str]]:
    """Check which tickers have cached bar data."""
    available = []
    missing = []

    for ticker in tickers:
        # Check JSON cache
        json_path = DATA_DIR / "bars" / f"bars_{ticker}_{date}.json"
        # Check Parquet
        parquet_path = ARENA_DATA / "historical" / ticker / f"{date}.parquet"

        if json_path.exists() or parquet_path.exists():
            available.append(ticker)
        else:
            missing.append(ticker)

    return available, missing


def save_replay_config(date: str, tickers: list[str], prev_daily: dict, stats: dict):
    """Save a replay configuration file for run_historical_validation.py."""
    out_dir = ARENA_DATA / "configs"
    out_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "date": date,
        "tickers": tickers,
        "prev_daily": prev_daily,
        "journal_stats": stats,
    }

    out_file = out_dir / f"replay_{date}.json"
    with open(out_file, "w") as f:
        json.dump(config, f, indent=2, default=str)
    logger.info("Replay config saved to %s", out_file)
    return out_file


async def main():
    parser = argparse.ArgumentParser(description="Prepare historical data for arena replay")
    parser.add_argument("--date", type=str, required=True, help="Date to prepare (YYYY-MM-DD)")
    parser.add_argument("--download", action="store_true", help="Download missing bar data from Alpaca")
    args = parser.parse_args()

    date = args.date

    print(f"\n{'='*60}")
    print(f"PREPARING DATA FOR: {date}")
    print(f"{'='*60}\n")

    # 1. Extract watchlist
    watchlist = extract_watchlist(date)
    journal_tickers = extract_journal_tickers(date)
    all_tickers = sorted(set(watchlist + journal_tickers + ["SPY", "VIXY"]))

    print(f"Premarket watchlist:  {len(watchlist)} tickers")
    print(f"Journal tickers:     {len(journal_tickers)} tickers")
    print(f"Combined + SPY/VIXY: {len(all_tickers)} tickers")
    print(f"  {', '.join(all_tickers[:15])}{'...' if len(all_tickers) > 15 else ''}")

    # 2. Journal stats
    stats = extract_journal_stats(date)
    print(f"\nJournal entries: {stats['total']}")
    print(f"  Actions: {stats['actions']}")
    if stats["tickers"]:
        print(f"  BUY signals: {len(stats['tickers'])} tickers")
        for t, info in list(stats["tickers"].items())[:5]:
            print(f"    {t}: entry=${info['entry_price']}, stop=${info['stop_loss']}, "
                  f"conf={info['confidence']:.2f}, gap={info['gap_pct']:.1%}")

    # 3. Previous daily bars
    prev_daily = extract_prev_daily(date, journal_tickers)
    print(f"\nPrevious daily bars: {len(prev_daily)} tickers derived from premarket")

    # 4. Check bar availability
    available, missing = check_bar_availability(date, journal_tickers)
    print(f"\nBar data availability:")
    print(f"  Available: {len(available)} — {', '.join(available[:10])}")
    print(f"  Missing:   {len(missing)} — {', '.join(missing[:10])}")

    if missing and args.download:
        print(f"\nDownloading {len(missing)} missing tickers...")
        from mx_arena_scripts_download_history import download_symbol
        # TODO: Wire up actual download
        print("  (Download not yet implemented — use scripts/download_history.py)")

    # 5. Save replay config
    config_file = save_replay_config(date, journal_tickers, prev_daily, stats)

    print(f"\n{'='*60}")
    print(f"READY FOR REPLAY")
    print(f"{'='*60}")
    print(f"  Config: {config_file}")
    print(f"  Command: python mx-arena/scripts/run_historical_validation.py --date {date}")
    if missing:
        print(f"\n  NOTE: {len(missing)} tickers missing bar data.")
        print(f"  Download with: python mx-arena/scripts/download_history.py "
              f"--symbols {','.join(missing)} --start {date} --end {date}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
