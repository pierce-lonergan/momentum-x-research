#!/usr/bin/env python
"""D215 Day D: Backfill recoverable features for historical trades.

Fetches daily bars from Alpaca for each of the 34 historical round-trip
trades and recovers gap_pct, approximate rvol, and entry-day price levels.
Outputs a JSON file that can be used for future analysis.

Usage:
    python scripts/backfill_trade_features.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    from dotenv import load_dotenv
    load_dotenv()
    import httpx
    import requests

    h = {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }

    # Get all round-trip trades from Alpaca
    r = requests.get(
        "https://paper-api.alpaca.markets/v2/orders?status=all&limit=500",
        headers=h, timeout=10,
    )
    orders = r.json()

    buys = {}
    round_trips = []
    for o in sorted(orders, key=lambda x: x["created_at"]):
        fp = float(o.get("filled_avg_price") or 0)
        if fp <= 0:
            continue
        ticker = o["symbol"]
        qty = int(o.get("filled_qty") or 0)
        if qty <= 0:
            continue
        if o["side"] == "buy":
            buys[ticker] = {"price": fp, "qty": qty, "time": o["created_at"]}
        elif o["side"] == "sell" and ticker in buys:
            buy = buys.pop(ticker)
            pnl_pct = (fp - buy["price"]) / buy["price"]
            try:
                hold_min = (
                    datetime.fromisoformat(o["created_at"][:19])
                    - datetime.fromisoformat(buy["time"][:19])
                ).total_seconds() / 60
            except Exception:
                hold_min = 0
            round_trips.append({
                "ticker": ticker,
                "date": buy["time"][:10],
                "buy_price": buy["price"],
                "sell_price": fp,
                "pnl_pct": round(pnl_pct, 6),
                "qty": min(buy["qty"], qty),
                "hold_min": round(hold_min, 1),
                "buy_time": buy["time"][:19],
                "sell_time": o["created_at"][:19],
            })

    print(f"Found {len(round_trips)} round-trip trades")

    # Fetch daily bars to recover gap_pct and rvol
    data_url = "https://data.alpaca.markets"
    backfilled = []
    failed = 0

    with httpx.Client(timeout=15, headers=h) as http:
        for i, rt in enumerate(round_trips):
            ticker = rt["ticker"]
            date = rt["date"]

            try:
                # Fetch 5 daily bars ending at trade date
                from datetime import timedelta
                end_dt = datetime.fromisoformat(date)
                start_dt = end_dt - timedelta(days=10)

                resp = http.get(
                    f"{data_url}/v2/stocks/{ticker}/bars",
                    params={
                        "timeframe": "1Day",
                        "start": f"{start_dt.strftime('%Y-%m-%d')}T00:00:00Z",
                        "end": f"{date}T23:59:59Z",
                        "feed": "sip",
                        "limit": 5,
                    },
                )

                if resp.status_code != 200:
                    failed += 1
                    backfilled.append({**rt, "recovered": False, "reason": f"HTTP {resp.status_code}"})
                    continue

                bars = resp.json().get("bars", [])
                if len(bars) < 2:
                    failed += 1
                    backfilled.append({**rt, "recovered": False, "reason": "insufficient bars"})
                    continue

                # Trade day is last bar, previous day is second-to-last
                trade_bar = bars[-1]
                prev_bar = bars[-2]

                prev_close = prev_bar["c"]
                trade_open = trade_bar["o"]
                trade_volume = trade_bar["v"]
                prev_volume = prev_bar["v"]

                gap_pct = (trade_open - prev_close) / prev_close if prev_close > 0 else 0
                rvol = trade_volume / prev_volume if prev_volume > 0 else 0

                backfilled.append({
                    **rt,
                    "recovered": True,
                    "gap_pct": round(gap_pct, 6),
                    "rvol": round(rvol, 2),
                    "prev_close": prev_close,
                    "trade_open": trade_open,
                    "trade_high": trade_bar["h"],
                    "trade_low": trade_bar["l"],
                    "trade_close": trade_bar["c"],
                    "trade_volume": trade_volume,
                    "prev_volume": prev_volume,
                })

            except Exception as e:
                failed += 1
                backfilled.append({**rt, "recovered": False, "reason": str(e)[:80]})

            if (i + 1) % 10 == 0:
                print(f"  Progress: {i+1}/{len(round_trips)}")

    # Save
    output_path = _DATA / "backfill" / "trade_features_backfill.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(backfilled, indent=2))

    # Summary
    recovered = [b for b in backfilled if b.get("recovered")]
    longs = [b for b in recovered if b["pnl_pct"] > -9]  # Rough long filter

    print(f"\n{'='*60}")
    print(f"  D215 DAY D: BACKFILL RESULTS")
    print(f"{'='*60}")
    print(f"  Total trades:   {len(round_trips)}")
    print(f"  Recovered:      {len(recovered)}")
    print(f"  Failed:         {failed}")
    print(f"  Output:         {output_path}")

    if recovered:
        print(f"\n  RECOVERED TRADE FEATURES:")
        print(f"  {'Ticker':6s} {'Date':10s} {'Gap%':>7s} {'RVOL':>6s} {'P&L':>7s} {'Hold':>6s}")
        print(f"  {'-'*50}")
        for b in recovered[:20]:
            print(
                f"  {b['ticker']:6s} {b['date']:10s} "
                f"{b['gap_pct']*100:>+6.1f}% {b['rvol']:>5.1f}x "
                f"{b['pnl_pct']*100:>+6.1f}% {b['hold_min']:>5.0f}m"
            )

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
