#!/usr/bin/env python
"""D214: Fetch real minute bars from Alpaca for historical gap scenarios.

Downloads 1Min bars for the first 60 minutes of each scenario's trading day
(9:30-10:30 ET). Scenarios without fetchable bars are logged and excluded
from archetype training. NO synthetic fallback — train on reality only.

Usage:
    python scripts/fetch_scenario_minute_bars.py
    python scripts/fetch_scenario_minute_bars.py --limit 20  # First 20 only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


async def fetch_bars() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    import httpx

    headers = {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }
    data_url = "https://data.alpaca.markets"

    # Load scenarios
    scenario_path = _DATA / "scenarios" / "gap_scenarios.json"
    data = json.loads(scenario_path.read_text())
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.limit > 0:
        scenarios = scenarios[:args.limit]

    # Output dirs
    bar_dir = _DATA / "scenarios" / "minute_bars"
    bar_dir.mkdir(parents=True, exist_ok=True)
    archetype_dir = _DATA / "archetypes"
    archetype_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    dropped = []
    fetched = 0
    cached = 0

    async with httpx.AsyncClient(timeout=15, headers=headers) as http:
        for i, s in enumerate(scenarios):
            ticker = s["ticker"]
            date = s["date"]
            out_file = bar_dir / f"{ticker}_{date}.json"

            # Skip if already fetched
            if out_file.exists():
                existing = json.loads(out_file.read_text())
                if existing.get("bars") and len(existing["bars"]) >= 10:
                    manifest.append({
                        "ticker": ticker, "date": date,
                        "source": "alpaca", "bar_count": len(existing["bars"]),
                    })
                    cached += 1
                    continue

            # Compute time range: 9:30 AM to 10:30 AM ET on the scenario date
            # ET = UTC-5 (EST) or UTC-4 (EDT). Use a safe window.
            start = f"{date}T13:30:00Z"  # 9:30 AM ET = 1:30 PM UTC (EST)
            end = f"{date}T14:30:00Z"    # 10:30 AM ET = 2:30 PM UTC (EST)

            try:
                resp = await http.get(
                    f"{data_url}/v2/stocks/{ticker}/bars",
                    params={
                        "timeframe": "1Min",
                        "start": start,
                        "end": end,
                        "limit": 100,
                        "feed": "sip",
                    },
                )

                if resp.status_code != 200:
                    dropped.append({
                        "ticker": ticker, "date": date,
                        "reason": f"HTTP {resp.status_code}",
                        "detail": resp.text[:200],
                    })
                    continue

                bars_data = resp.json().get("bars", [])

                if not bars_data or len(bars_data) < 10:
                    dropped.append({
                        "ticker": ticker, "date": date,
                        "reason": "insufficient_bars",
                        "bar_count": len(bars_data) if bars_data else 0,
                    })
                    continue

                # Normalize bar format
                normalized = []
                for b in bars_data:
                    normalized.append({
                        "timestamp": b.get("t", ""),
                        "open": b.get("o", 0),
                        "high": b.get("h", 0),
                        "low": b.get("l", 0),
                        "close": b.get("c", 0),
                        "volume": b.get("v", 0),
                        "vwap": b.get("vw", 0),
                    })

                out_file.write_text(json.dumps({
                    "ticker": ticker,
                    "date": date,
                    "source": "alpaca",
                    "bars": normalized,
                }, indent=2))

                manifest.append({
                    "ticker": ticker, "date": date,
                    "source": "alpaca", "bar_count": len(normalized),
                })
                fetched += 1

            except Exception as e:
                dropped.append({
                    "ticker": ticker, "date": date,
                    "reason": "exception",
                    "detail": str(e)[:200],
                })

            # Rate limit
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i+1}/{len(scenarios)} ({fetched} fetched, {cached} cached, {len(dropped)} dropped)")
                await asyncio.sleep(1)

    # Save manifest and dropped list
    manifest_path = _DATA / "scenarios" / "minute_bar_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    dropped_path = archetype_dir / "dropped_scenarios.json"
    dropped_path.write_text(json.dumps(dropped, indent=2))

    print()
    print("=" * 60)
    print("  D214: MINUTE BAR FETCH RESULTS")
    print("=" * 60)
    print(f"  Total scenarios:  {len(scenarios)}")
    print(f"  Fetched (new):    {fetched}")
    print(f"  Cached (existing):{cached}")
    print(f"  Dropped:          {len(dropped)}")
    print(f"  Available for training: {fetched + cached}")
    print(f"  Manifest: {manifest_path}")
    print(f"  Dropped:  {dropped_path}")

    if dropped:
        from collections import Counter
        reasons = Counter(d["reason"] for d in dropped)
        print(f"\n  Drop reasons:")
        for r, c in reasons.most_common():
            print(f"    {r}: {c}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(fetch_bars())
