"""
D219: Minute Bar Downloader for Historical Candidates

Reads candidates from data/backfill/candidates.jsonl and pulls
minute bars for each from Alpaca. Stores in the existing bar_recordings
format for arena compatibility.

Usage:
    python scripts/backfill_bars.py [--max-candidates 500]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("backfill_bars")


async def download_minute_bars(
    ticker: str, date: str, key: str, secret: str,
) -> list[dict] | None:
    """Pull 1-minute bars from 04:00 to 20:00 ET for a specific date."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://data.alpaca.markets/v2/stocks/{ticker}/bars",
                params={
                    "timeframe": "1Min",
                    "start": f"{date}T08:00:00Z",  # 4:00 AM ET
                    "end": f"{date}T20:00:00Z",     # 4:00 PM ET
                    "limit": 10000,
                },
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            )
            if resp.status_code == 429:
                logger.warning("Rate limited on %s %s, sleeping 5s", ticker, date)
                await asyncio.sleep(5)
                return None
            if resp.status_code != 200:
                return None
            data = resp.json()
            bars = data.get("bars", [])
            if not bars:
                return None
            # Normalize to our standard format
            return [
                {
                    "timestamp": b["t"],
                    "open": b["o"],
                    "high": b["h"],
                    "low": b["l"],
                    "close": b["c"],
                    "volume": b["v"],
                    "vwap": b.get("vw", 0),
                }
                for b in bars
            ]
    except Exception as e:
        logger.debug("Failed %s %s: %s", ticker, date, e)
        return None


async def run(max_candidates: int = 500):
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    candidates_file = Path("data/backfill/candidates.jsonl")

    if not candidates_file.exists():
        logger.error("Run backfill_harvester.py first to generate candidates.jsonl")
        return

    with open(candidates_file, encoding="utf-8") as f:
        candidates = [json.loads(line) for line in f if line.strip()]

    # Sort by gap_pct descending — prioritize the biggest movers
    candidates.sort(key=lambda c: -c.get("gap_pct", 0))
    candidates = candidates[:max_candidates]

    logger.info("Downloading minute bars for %d candidates", len(candidates))
    downloaded = 0
    skipped = 0
    failed = 0

    for i, cand in enumerate(candidates):
        ticker = cand["ticker"]
        date = cand["date"]

        # Check if already downloaded
        bar_dir = Path(f"data/bar_recordings/{date}")
        bar_file = bar_dir / f"{ticker}.json"
        if bar_file.exists():
            skipped += 1
            continue

        bars = await download_minute_bars(ticker, date, key, secret)
        if bars and len(bars) > 10:
            bar_dir.mkdir(parents=True, exist_ok=True)
            with open(bar_file, "w", encoding="utf-8") as f:
                json.dump({"ticker": ticker, "date": date, "bars": bars}, f)
            downloaded += 1
        else:
            failed += 1

        # Rate limiting: 4 calls/sec
        await asyncio.sleep(0.25)

        if (i + 1) % 50 == 0:
            logger.info(
                "Progress: %d/%d (downloaded=%d, skipped=%d, failed=%d)",
                i + 1, len(candidates), downloaded, skipped, failed,
            )

    logger.info(
        "COMPLETE: downloaded=%d, skipped=%d, failed=%d, total=%d",
        downloaded, skipped, failed, len(candidates),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-candidates", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(run(max_candidates=args.max_candidates))


if __name__ == "__main__":
    main()
