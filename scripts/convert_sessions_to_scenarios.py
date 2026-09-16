#!/usr/bin/env python
"""D213: Convert D210 session data to gap scenarios for arena expansion.

Reads session collector data (data/historical_collection/YYYY-MM-DD/),
extracts intraday price extremes from minute bars, and appends new
scenarios to gap_scenarios.json.

Usage:
    python scripts/convert_sessions_to_scenarios.py                  # All available dates
    python scripts/convert_sessions_to_scenarios.py --date 2026-04-08  # Single date
    python scripts/convert_sessions_to_scenarios.py --dry-run          # Preview without writing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def load_session_tickers(date: str) -> list[dict]:
    """Load all ticker JSON files from a session date."""
    session_dir = _DATA / "historical_collection" / date
    if not session_dir.exists():
        return []

    tickers = []
    for f in sorted(session_dir.glob("*.json")):
        if f.name == "session_summary.json":
            continue
        try:
            data = json.loads(f.read_text())
            data["_filename"] = f.name
            tickers.append(data)
        except Exception:
            continue
    return tickers


def ticker_to_scenario(ticker_data: dict, date: str) -> dict | None:
    """Convert a D210 ticker record to gap_scenarios format."""
    bars = ticker_data.get("minute_bars", [])
    if not bars or len(bars) < 10:
        return None

    ticker = ticker_data.get("ticker", "")
    gap_pct = ticker_data.get("gap_pct", 0)
    rvol = ticker_data.get("rvol", 0)
    open_price = ticker_data.get("current_price", 0) or ticker_data.get("entry_price", 0)
    prev_close = ticker_data.get("previous_close", 0)

    if not ticker or open_price <= 0 or prev_close <= 0:
        return None

    # Compute gap_pct from prev_close if not provided
    if not gap_pct and prev_close > 0:
        gap_pct = (open_price - prev_close) / prev_close

    # Only include stocks with meaningful gaps (>3%)
    if abs(gap_pct) < 0.03:
        return None

    # Extract price extremes from minute bars
    highs = [b.get("h", b.get("high", 0)) for b in bars if b.get("h", b.get("high", 0)) > 0]
    lows = [b.get("l", b.get("low", 0)) for b in bars if b.get("l", b.get("low", 0)) > 0]
    closes = [b.get("c", b.get("close", 0)) for b in bars if b.get("c", b.get("close", 0)) > 0]

    if not highs or not lows or not closes:
        return None

    session_high = max(highs)
    session_low = min(lows)
    session_close = closes[-1]

    high_from_open_pct = (session_high - open_price) / open_price
    low_from_open_pct = (session_low - open_price) / open_price
    intraday_return = (session_close - open_price) / open_price
    day_return = (session_close - prev_close) / prev_close

    # Determine outcome (WIN = positive intraday return)
    outcome = "WIN" if intraday_return > 0 else "LOSS"

    # Volume
    volume = sum(b.get("v", b.get("volume", 0)) for b in bars)
    prev_volume = ticker_data.get("avg_daily_volume") or 0

    return {
        "ticker": ticker,
        "date": date,
        "gap_pct": round(gap_pct, 6),
        "rvol": round(rvol, 4),
        "outcome": outcome,
        "open_price": round(open_price, 4),
        "close": round(session_close, 4),
        "high": round(session_high, 4),
        "low": round(session_low, 4),
        "prev_close": round(prev_close, 4),
        "high_from_open_pct": round(high_from_open_pct, 6),
        "low_from_open_pct": round(low_from_open_pct, 6),
        "intraday_return": round(intraday_return, 6),
        "day_return": round(day_return, 6),
        "volume": volume,
        "prev_volume": prev_volume,
        # D212 enrichment (from session collector)
        "prior_gap_count": ticker_data.get("prior_gap_count"),
        "is_day2_runner": ticker_data.get("is_day2_runner", False),
        "dollar_volume": round(open_price * volume, 2) if volume > 0 else None,
        "avg_daily_volume": prev_volume,
        "market_cap": ticker_data.get("market_cap"),
        "float_shares": ticker_data.get("float_shares"),
        "sector": ticker_data.get("sector"),
        "mfcs": ticker_data.get("mfcs"),
        "faller_risk_score": ticker_data.get("faller_risk_score"),
        "was_traded": ticker_data.get("traded", False),
        "trade_pnl": ticker_data.get("pnl_dollars"),
        # Source tracking
        "source": "D210_session_collector",
        "source_date": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="D213: Convert sessions to scenarios")
    parser.add_argument("--date", help="Single date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    # Find available session dates
    collection_dir = _DATA / "historical_collection"
    if args.date:
        dates = [args.date]
    elif collection_dir.exists():
        dates = sorted(d.name for d in collection_dir.iterdir() if d.is_dir() and d.name.startswith("20"))
    else:
        print("No session data found in data/historical_collection/")
        return

    # Load existing scenarios to avoid duplicates
    scenario_path = _DATA / "scenarios" / "gap_scenarios.json"
    if scenario_path.exists():
        with open(scenario_path) as f:
            data = json.load(f)
        existing = data if isinstance(data, list) else data.get("scenarios", [])
        if isinstance(data, dict):
            wrapper = data
        else:
            wrapper = {"scenarios": existing}
    else:
        existing = []
        wrapper = {"scenarios": existing}

    existing_keys = {(s["ticker"], s["date"]) for s in existing}

    print("=" * 60)
    print("  D213: SESSION → SCENARIO CONVERTER")
    print("=" * 60)
    print(f"\n  Existing scenarios: {len(existing)}")
    print(f"  Session dates to process: {len(dates)}")

    new_scenarios = []
    for date in dates:
        tickers = load_session_tickers(date)
        if not tickers:
            continue

        date_new = 0
        for td in tickers:
            ticker = td.get("ticker", "")
            if (ticker, date) in existing_keys:
                continue

            scenario = ticker_to_scenario(td, date)
            if scenario is not None:
                new_scenarios.append(scenario)
                existing_keys.add((ticker, date))
                date_new += 1

        if date_new > 0:
            print(f"    {date}: {date_new} new scenarios from {len(tickers)} tickers")

    print(f"\n  New scenarios found: {len(new_scenarios)}")

    if new_scenarios:
        wins = sum(1 for s in new_scenarios if s["outcome"] == "WIN")
        print(f"  New scenario WR: {wins}/{len(new_scenarios)} = {wins/len(new_scenarios)*100:.0f}%")

        if not args.dry_run:
            wrapper["scenarios"] = existing + new_scenarios
            wrapper["last_expanded"] = datetime.now(timezone.utc).isoformat()
            wrapper["total_scenarios"] = len(wrapper["scenarios"])

            scenario_path.write_text(json.dumps(wrapper, indent=2))
            print(f"\n  Written to: {scenario_path}")
            print(f"  Total scenarios: {len(wrapper['scenarios'])}")
        else:
            print(f"\n  [DRY RUN] Would append {len(new_scenarios)} to {scenario_path}")

        # Show sample
        print(f"\n  Sample new scenarios:")
        for s in new_scenarios[:5]:
            print(f"    {s['ticker']:6s} ({s['date']}): gap={s['gap_pct']*100:.1f}% rvol={s['rvol']:.1f}x {s['outcome']} ret={s['intraday_return']*100:+.1f}%")
    else:
        print("  No new scenarios to add.")

    print("=" * 60)


if __name__ == "__main__":
    main()
