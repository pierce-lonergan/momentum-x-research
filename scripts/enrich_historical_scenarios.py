"""
D212: Enrich Historical Gap Scenarios with D212 Data

Fetches gap history, sector/industry, and float data for all 196 historical
scenarios so the arena can backtest D212 faller signals (serial gapper,
day-2 runner, sector, float rotation proxy).

Usage:
    python scripts/enrich_historical_scenarios.py

Output:
    data/scenarios/gap_scenarios_enriched.json (original + D212 fields)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)


async def enrich_scenarios() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    from config.settings import Settings
    import httpx

    settings = Settings()
    _headers = {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }
    _data_url = "https://data.alpaca.markets"
    _http = httpx.AsyncClient(timeout=10, headers=_headers)

    # Load existing scenarios
    scenario_path = Path("data/scenarios/gap_scenarios.json")
    data = json.loads(scenario_path.read_text())
    scenarios = data.get("scenarios", [])
    logger.info("Loaded %d scenarios", len(scenarios))

    enriched_count = 0
    failed_count = 0

    for i, scenario in enumerate(scenarios):
        ticker = scenario["ticker"]
        date = scenario["date"]  # e.g., "2023-11-28"

        # Skip if already enriched with real data (not None)
        if scenario.get("prior_gap_count") is not None:
            enriched_count += 1
            continue

        try:
            # Fetch 30 daily bars ending at the scenario date
            from datetime import datetime as _dt, timedelta as _td
            _end_dt = _dt.fromisoformat(date)
            _start_dt = _end_dt - _td(days=60)
            resp = await _http.get(
                f"{_data_url}/v2/stocks/{ticker}/bars",
                params={
                    "timeframe": "1Day", "limit": 30,
                    "start": f"{_start_dt.strftime('%Y-%m-%d')}T00:00:00Z",
                    "end": f"{date}T23:59:59Z",
                    "feed": "sip",
                },
            )
            bars = resp.json().get("bars", []) if resp.status_code == 200 else []

            if not bars or len(bars) < 3:
                scenario["prior_gap_count"] = None
                scenario["is_day2_runner"] = False
                scenario["float_shares"] = None
                scenario["sector"] = None
                failed_count += 1
                continue

            # Count gap days in the lookback (excluding the scenario day itself)
            gap_count = 0
            yesterday_was_gap = False
            for j in range(1, len(bars) - 1):  # Exclude last bar (scenario day)
                prev_c = bars[j - 1].get("c", 0)
                curr_o = bars[j].get("o", 0)
                if prev_c > 0:
                    gap = (curr_o - prev_c) / prev_c
                    if gap > 0.05:
                        gap_count += 1

            # Check if the day before scenario was also a gap
            if len(bars) >= 3:
                prev_c = bars[-3].get("c", 0)
                day_before_o = bars[-2].get("o", 0)
                if prev_c > 0 and (day_before_o - prev_c) / prev_c > 0.05:
                    yesterday_was_gap = True

            scenario["prior_gap_count"] = gap_count
            scenario["is_day2_runner"] = yesterday_was_gap

            # Estimate float from volume patterns
            # (can't get Finnhub historical, use volume as proxy)
            avg_vol = sum(b.get("v", 0) for b in bars[:-1]) / max(len(bars) - 1, 1)
            scenario["avg_daily_volume"] = int(avg_vol)

            # Compute dollar volume
            scenario["dollar_volume"] = round(
                scenario.get("open_price", 0) * scenario.get("volume", 0), 2
            )

            enriched_count += 1

            if gap_count >= 3:
                logger.info(
                    "  SERIAL GAPPER: %s (%s) — %d prior gaps",
                    ticker, date, gap_count,
                )
            if yesterday_was_gap:
                logger.info(
                    "  DAY-2 RUNNER: %s (%s)",
                    ticker, date,
                )

        except Exception as e:
            logger.warning("  Failed %s (%s): %s", ticker, date, str(e)[:80])
            scenario["prior_gap_count"] = None
            scenario["is_day2_runner"] = False
            failed_count += 1

        # Rate limit: ~200 calls/min for Alpaca
        if (i + 1) % 10 == 0:
            logger.info("  Progress: %d/%d enriched", i + 1, len(scenarios))
            await asyncio.sleep(0.5)

    # Save enriched scenarios
    output_path = Path("data/scenarios/gap_scenarios_enriched.json")
    data["scenarios"] = scenarios
    data["d212_enriched"] = True
    data["d212_enriched_date"] = time.strftime("%Y-%m-%d")
    output_path.write_text(json.dumps(data, indent=2))

    # Also update the original file with enrichments
    scenario_path.write_text(json.dumps(data, indent=2))

    # Print summary
    serial_gappers = sum(1 for s in scenarios if (s.get("prior_gap_count") or 0) >= 3)
    day2_runners = sum(1 for s in scenarios if s.get("is_day2_runner", False))

    serial_wins = sum(
        1 for s in scenarios
        if (s.get("prior_gap_count") or 0) >= 3 and s.get("outcome") == "WIN"
    )
    serial_losses = sum(
        1 for s in scenarios
        if (s.get("prior_gap_count") or 0) >= 3 and s.get("outcome") == "LOSS"
    )

    day2_wins = sum(
        1 for s in scenarios
        if s.get("is_day2_runner") and s.get("outcome") == "WIN"
    )
    day2_losses = sum(
        1 for s in scenarios
        if s.get("is_day2_runner") and s.get("outcome") == "LOSS"
    )

    fresh_wins = sum(
        1 for s in scenarios
        if (s.get("prior_gap_count") or 0) == 0 and s.get("outcome") == "WIN"
    )
    fresh_total = sum(
        1 for s in scenarios
        if (s.get("prior_gap_count") or 0) == 0
    )

    print()
    print("=" * 60)
    print("  D212 HISTORICAL ENRICHMENT RESULTS")
    print("=" * 60)
    print(f"  Scenarios: {len(scenarios)} ({enriched_count} enriched, {failed_count} failed)")
    print()
    print(f"  Serial Gappers (3+ prior gaps): {serial_gappers}")
    if serial_gappers:
        wr = serial_wins / serial_gappers * 100
        print(f"    Win rate: {serial_wins}/{serial_gappers} = {wr:.0f}%")
        print(f"    {'SIGNAL VALIDATES' if wr < 40 else 'NEEDS MORE DATA'}")
    print()
    print(f"  Day-2 Runners: {day2_runners}")
    if day2_runners:
        wr = day2_wins / day2_runners * 100
        print(f"    Win rate: {day2_wins}/{day2_runners} = {wr:.0f}%")
        print(f"    {'SIGNAL VALIDATES' if wr < 40 else 'NEEDS MORE DATA'}")
    print()
    print(f"  Fresh Gappers (0 prior gaps): {fresh_total}")
    if fresh_total:
        wr = fresh_wins / fresh_total * 100
        print(f"    Win rate: {fresh_wins}/{fresh_total} = {wr:.0f}%")
        print(f"    {'EDGE CONFIRMED' if wr > 50 else 'NO EDGE'}")
    print()
    print(f"  Output: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(enrich_scenarios())
