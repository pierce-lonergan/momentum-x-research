"""Backfill 1-min bars from Alpaca for missing-coverage trades.

Block B.1 of the Block 4.4 unblock work. See plan doc 58 §7 and
docs/research-log/61_bar_coverage_gap.md.

Production records bars to data/bar_recordings/{date}/{TICKER}.json
via the WebSocket bar stream. The bar recorder subscribes from the
session-start watchlist; D121 BUG-P9 dynamically-added tickers do
not propagate to the recorder. Result: 5 of 12 recent trades have
no bars (AGPU 4/22, MAAS 4/22, SCNI 4/24, ONMD 4/24, SEGG 4/28).

This script fills the gap retroactively by calling Alpaca's
GET /v2/stocks/{symbol}/bars endpoint and writing JSON in the
recorder's exact schema (so the existing converter can pick it up).

Usage:
    # Backfill the 5 known missing-coverage trades
    python scripts/backfill_historical_bars.py --known-gaps

    # Backfill one ticker for one date
    python scripts/backfill_historical_bars.py --ticker AGPU --date 2026-04-22

    # Dry run (count + URL preview, no writes)
    python scripts/backfill_historical_bars.py --known-gaps --dry-run

Output: data/bar_recordings/{date}/{TICKER}.json with the same shape
the live recorder writes. Existing files are NOT overwritten unless
--force is passed.

Discipline: backfilled files are tagged in their JSON metadata as
`source: backfill_alpaca_v0.1` so future audits can distinguish
recorder-captured bars from backfilled bars.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("backfill_bars")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bar_recordings"

# Make src importable
sys.path.insert(0, str(REPO_ROOT))


# Known coverage gaps from docs/research-log/61_bar_coverage_gap.md
KNOWN_GAPS: list[tuple[str, str]] = [
    ("2026-04-22", "AGPU"),
    ("2026-04-22", "MAAS"),
    ("2026-04-24", "SCNI"),
    ("2026-04-24", "ONMD"),
    ("2026-04-28", "SEGG"),
]


@dataclass
class BackfillResult:
    ticker: str
    date: str
    status: str           # ok | skipped_existing | empty_response | error
    bars_count: int
    output_path: Path | None
    notes: str = ""


# ── Bar fetching (raw HTTP — no live AlpacaDataClient overhead) ────


async def fetch_bars_for_session(
    *, ticker: str, date: str, feed: str = "sip",
) -> list[dict[str, Any]]:
    """Pull 1-min bars for a US-equity session (09:30-16:00 ET).

    Uses raw httpx to avoid spinning up the full AlpacaDataClient
    machinery. Auth from .env via pydantic-settings (same source as
    production). Pulls full session window in a single call (Alpaca
    caps at 10000 bars; one session = 390 bars).
    """
    import httpx
    from zoneinfo import ZoneInfo
    from config.settings import Settings

    et = ZoneInfo("America/New_York")
    session_open = datetime.fromisoformat(f"{date}T09:30:00").replace(tzinfo=et)
    session_close = datetime.fromisoformat(f"{date}T16:00:00").replace(tzinfo=et)
    start_iso = session_open.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_iso = session_close.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    settings = Settings()
    api_key = settings.alpaca.api_key
    secret = settings.alpaca.secret_key
    if not api_key or not secret:
        raise SystemExit(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env "
            "or env vars to call the bars endpoint. (Read-only — no "
            "live trading impact.)"
        )
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret,
    }
    url = f"{settings.alpaca.data_url}/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Min",
        "start": start_iso,
        "end": end_iso,
        "limit": 10000,
        "feed": feed,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data.get("bars") or []


# ── Convert Alpaca shape (short keys) → recorder shape (long keys) ─


def alpaca_bar_to_recorder_shape(bar: dict[str, Any]) -> dict[str, Any]:
    """Alpaca returns {t, o, h, l, c, v, vw, n}. Recorder writes
    {timestamp, open, high, low, close, volume, vwap}."""
    return {
        "timestamp": bar.get("t", ""),
        "open": float(bar.get("o", 0)),
        "high": float(bar.get("h", 0)),
        "low": float(bar.get("l", 0)),
        "close": float(bar.get("c", 0)),
        "volume": int(bar.get("v", 0)),
        "vwap": float(bar.get("vw", 0)),
    }


def write_recording(
    *, ticker: str, date: str, bars: list[dict[str, Any]],
    output_dir: Path, force: bool,
) -> tuple[Path, str]:
    """Write {output_dir}/{date}/{TICKER}.json. Returns (path, status).

    status: 'ok' | 'skipped_existing'
    """
    day_dir = output_dir / date
    day_dir.mkdir(parents=True, exist_ok=True)
    target = day_dir / f"{ticker.upper()}.json"
    if target.exists() and not force:
        return target, "skipped_existing"
    payload = {
        "ticker": ticker.upper(),
        "date": date,
        "source": "backfill_alpaca_v0.1",
        "bars": [alpaca_bar_to_recorder_shape(b) for b in bars],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target, "ok"


# ── Per-ticker workflow ────────────────────────────────────────────


async def backfill_one(
    *, ticker: str, date: str, output_dir: Path, dry_run: bool, force: bool,
) -> BackfillResult:
    try:
        bars = await fetch_bars_for_session(ticker=ticker, date=date)
    except Exception as e:
        return BackfillResult(
            ticker=ticker, date=date, status="error",
            bars_count=0, output_path=None,
            notes=f"fetch failed: {type(e).__name__}: {e}",
        )

    if not bars:
        return BackfillResult(
            ticker=ticker, date=date, status="empty_response",
            bars_count=0, output_path=None,
            notes="Alpaca returned 0 bars (possible halted ticker, holiday, or invalid date)",
        )

    if dry_run:
        return BackfillResult(
            ticker=ticker, date=date, status="ok",
            bars_count=len(bars),
            output_path=output_dir / date / f"{ticker}.json",
            notes="dry-run: would write",
        )

    target, write_status = write_recording(
        ticker=ticker, date=date, bars=bars,
        output_dir=output_dir, force=force,
    )
    return BackfillResult(
        ticker=ticker, date=date, status=write_status,
        bars_count=len(bars), output_path=target,
    )


# ── Main ──────────────────────────────────────────────────────────


async def amain(args) -> int:
    if args.known_gaps:
        targets = list(KNOWN_GAPS)
    elif args.ticker and args.date:
        targets = [(args.date, args.ticker.upper())]
    else:
        logger.error("specify either --known-gaps OR --ticker AND --date")
        return 1

    results: list[BackfillResult] = []
    for date, ticker in targets:
        result = await backfill_one(
            ticker=ticker, date=date,
            output_dir=args.output_dir, dry_run=args.dry_run, force=args.force,
        )
        results.append(result)
        if result.status == "ok":
            logger.info(
                "%s/%s: %s (n=%d bars)%s",
                date, ticker, result.status, result.bars_count,
                f" → {result.output_path}" if not args.dry_run else "",
            )
        elif result.status == "skipped_existing":
            logger.info("%s/%s: skipped (file exists; pass --force to overwrite)", date, ticker)
        elif result.status == "empty_response":
            logger.warning("%s/%s: %s", date, ticker, result.notes)
        else:
            logger.error("%s/%s: %s — %s", date, ticker, result.status, result.notes)

    n_ok = sum(1 for r in results if r.status == "ok")
    n_skipped = sum(1 for r in results if r.status == "skipped_existing")
    n_empty = sum(1 for r in results if r.status == "empty_response")
    n_err = sum(1 for r in results if r.status == "error")
    logger.info(
        "DONE: %d total | %d ok | %d skipped | %d empty | %d error",
        len(results), n_ok, n_skipped, n_empty, n_err,
    )
    return 0 if n_err == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--known-gaps", action="store_true",
                   help="Backfill the 5 known missing-coverage trades from doc 61")
    p.add_argument("--ticker", help="Single-ticker mode")
    p.add_argument("--date", help="Single-date mode (YYYY-MM-DD)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing recording files")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
