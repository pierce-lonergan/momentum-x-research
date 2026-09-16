"""Fetch Finnhub earnings calendar for the OOS corpus window.

One bulk call for the full Dec 2025 → Apr 2026 range, cached to
disk. The harness then queries this cache instead of hitting
Finnhub per-session.

Output: data/calibration/historical_earnings_2025-12_to_2026-04.json
        Schema: {ticker: [{date, epsEstimate, epsActual, hour, quarter}, ...]}

Usage:
    python scripts/fetch_historical_earnings.py
    # or override window:
    python scripts/fetch_historical_earnings.py --start 2025-12-01 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("fetch_earnings")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "calibration" / "historical_earnings_2025-12_to_2026-04.json"


async def fetch_window(*, start: str, end: str) -> list[dict]:
    """Single Finnhub call for the [start, end] window."""
    import os
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        # Manually parse .env if needed
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("FINNHUB_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        raise SystemExit(
            "FINNHUB_API_KEY must be set in env or .env to fetch the "
            "earnings calendar. Without it, this script cannot run."
        )

    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": start, "to": end, "token": api_key},
        )
        r.raise_for_status()
        data = r.json()
    return data.get("earningsCalendar", []) or []


async def amain(args) -> int:
    events = await fetch_window(start=args.start, end=args.end)
    logger.info("fetched %d earnings events for [%s, %s]", len(events), args.start, args.end)

    # Group by ticker
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        sym = e.get("symbol", "")
        if not sym:
            continue
        by_ticker[sym].append({
            "date": e.get("date", ""),
            "epsEstimate": e.get("epsEstimate"),
            "epsActual": e.get("epsActual"),
            "hour": e.get("hour", ""),
            "quarter": e.get("quarter"),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "window": {"start": args.start, "end": args.end},
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "n_events": len(events),
        "n_unique_tickers": len(by_ticker),
        "by_ticker": dict(by_ticker),
    }
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "wrote %s — %d events, %d unique tickers",
        args.output, len(events), len(by_ticker),
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2025-12-01")
    p.add_argument("--end", default="2026-04-30")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    import os
    sys.exit(main())
