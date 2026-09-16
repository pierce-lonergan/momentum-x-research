"""D210: Fetch historical session data for a specific date.

Pulls minute bars + asset metadata for a list of tickers from Alpaca and
saves a complete SessionDataCollector snapshot to:
  data/historical_collection/YYYY-MM-DD/

Usage:
    python scripts/fetch_historical_day.py --date 2026-04-06 --tickers COCP,FC,SIDU

Requires .env with ALPACA_API_KEY and ALPACA_SECRET_KEY (or env vars).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── .env loading (best-effort) ────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass  # dotenv optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fetch_historical_day")


async def fetch_day(
    session_date: date,
    tickers: list[str],
    feed: str = "iex",
) -> None:
    """Fetch and persist all available data for `tickers` on `session_date`."""
    from config.settings import AlpacaConfig
    from src.data.alpaca_client import AlpacaDataClient
    from src.data.session_data_collector import SessionDataCollector

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in env or .env")
        sys.exit(1)

    alpaca_cfg = AlpacaConfig(
        api_key=api_key,
        secret_key=secret_key,
        base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        data_url=os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets"),
        feed=feed,
    )
    client = AlpacaDataClient(alpaca_cfg)
    collector = SessionDataCollector(session_date=session_date)

    logger.info("Fetching data for %d tickers on %s", len(tickers), session_date.isoformat())

    # ── 1. Snapshots (price / gap / RVOL proxies) ─────────────────────────────
    logger.info("Fetching snapshots...")
    try:
        snapshots = await client.get_snapshots(tickers)
        for ticker, snap in snapshots.items():
            # Synthesise a lightweight candidate-like object for register_candidate
            class _FakeCandidate:
                pass
            c = _FakeCandidate()
            c.ticker = ticker
            c.gap_pct = None
            c.rvol = None
            c.current_price = float(snap.get("last_price") or snap.get("latestTrade", {}).get("p", 0) or 0)
            c.previous_close = float(snap.get("prev_close") or snap.get("prevDailyBar", {}).get("c", 0) or 0)
            c.float_shares = snap.get("float_shares")
            c.market_cap = snap.get("market_cap")
            c.bid = float(snap.get("bid", 0) or 0)
            c.ask = float(snap.get("ask", 0) or 0)
            c.premarket_volume = int(snap.get("volume") or snap.get("minuteBar", {}).get("v", 0) or 0)
            # Compute gap_pct if we have both prices
            if c.current_price > 0 and c.previous_close > 0:
                c.gap_pct = (c.current_price - c.previous_close) / c.previous_close
            collector.register_candidate(c)
            logger.info("  %s  price=%.2f  prev_close=%.2f  gap=%.1f%%",
                        ticker, c.current_price, c.previous_close,
                        (c.gap_pct or 0) * 100)
    except Exception as e:
        logger.warning("Snapshot fetch failed: %s", e)
        # Register tickers with empty data so bars still get fetched
        for ticker in tickers:
            class _EmptyCandidate:
                pass
            c = _EmptyCandidate()
            c.ticker = ticker
            for attr in ("gap_pct", "rvol", "current_price", "previous_close",
                         "float_shares", "market_cap", "bid", "ask", "premarket_volume"):
                setattr(c, attr, None)
            collector.register_candidate(c)

    # ── 2. Asset metadata (float, market cap) ────────────────────────────────
    logger.info("Fetching asset metadata...")
    for ticker in tickers:
        try:
            asset = await client.get_asset(ticker)
            rec = collector.get_record(ticker)
            if rec and asset:
                # Alpaca asset data typically doesn't include float/mcap,
                # but we capture whatever is available.
                rec.notes.append(f"asset_class={asset.get('class','?')} status={asset.get('status','?')}")
        except Exception as e:
            logger.debug("Asset fetch skipped for %s: %s", ticker, e)

    # ── 3. Minute bars ────────────────────────────────────────────────────────
    logger.info("Fetching minute bars...")
    await collector.fetch_and_store_bars(client, tickers=tickers, session_date=session_date)

    # ── 4. Save ───────────────────────────────────────────────────────────────
    collector.save_session()

    # ── 5. Report ─────────────────────────────────────────────────────────────
    out_dir = Path("data/historical_collection") / session_date.isoformat()
    bar_counts = {t: len(collector.get_record(t).minute_bars or []) for t in tickers
                  if collector.get_record(t)}
    logger.info("Done. Files written to %s", out_dir)
    logger.info("Bar counts: %s", json.dumps(bar_counts))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch historical session data for a specific date",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Session date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--tickers",
        required=True,
        help="Comma-separated list of tickers, e.g. COCP,FC,SIDU",
    )
    parser.add_argument(
        "--feed",
        default="iex",
        choices=["iex", "sip", "otc"],
        help="Alpaca data feed (default: iex)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        session_date = date.fromisoformat(args.date)
    except ValueError:
        logger.error("Invalid date: %s — expected YYYY-MM-DD", args.date)
        sys.exit(1)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        logger.error("No tickers provided")
        sys.exit(1)

    asyncio.run(fetch_day(session_date=session_date, tickers=tickers, feed=args.feed))


if __name__ == "__main__":
    main()
