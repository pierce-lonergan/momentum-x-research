"""URGENT: attach trailing-stop SELL orders to all open lottery positions.

For each open position in the Alpaca paper account:
  1. Check if a trailing-stop SELL already exists for it (avoid duplicates)
  2. If not, submit a trail_percent=15 GTC sell order

This protects naked positions left over from today's bug.

USAGE:
    python scripts/lottery_attach_trailing_stops.py [--trail-pct 15.0] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


async def main_async(trail_pct: float, dry_run: bool) -> int:
    if not ALPACA_API_KEY:
        print("ERROR: ALPACA_API_KEY not set", file=sys.stderr)
        return 1

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # 1. List open positions
        r = await client.get(f"{ALPACA_BASE_URL}/v2/positions")
        r.raise_for_status()
        positions = r.json()
        print(f"[{datetime.now(ET).isoformat()}] Found {len(positions)} open position(s)")

        if not positions:
            print("No open positions — nothing to do.")
            return 0

        # 2. List existing open orders so we don't duplicate trailing stops
        r = await client.get(f"{ALPACA_BASE_URL}/v2/orders",
                              params={"status": "open", "nested": "true"})
        r.raise_for_status()
        open_orders = r.json()
        # Map symbol -> set of (side, type) for quick check
        existing_protective = set()
        for o in open_orders:
            if (o.get("side") == "sell"
                    and o.get("type") in ("trailing_stop", "stop", "stop_limit")):
                existing_protective.add(o["symbol"])
                print(f"  EXISTING protective sell on {o['symbol']}: type={o['type']} id={o['id']}")

        # 3. For each position lacking protection, submit trailing stop
        n_submitted = 0
        n_skipped = 0
        for p in positions:
            symbol = p["symbol"]
            qty = int(float(p["qty"]))
            avg_entry = float(p["avg_entry_price"])
            curr = float(p["current_price"])
            unrealized_pct = float(p["unrealized_plpc"]) * 100
            if symbol in existing_protective:
                print(f"  {symbol:6s} qty={qty:>4d} curr=${curr:.2f} ({unrealized_pct:+.1f}%) "
                      f"-> SKIP (protective sell already exists)")
                n_skipped += 1
                continue
            print(f"  {symbol:6s} qty={qty:>4d} curr=${curr:.2f} ({unrealized_pct:+.1f}%) "
                  f"-> SUBMIT trail_percent={trail_pct} GTC")
            if dry_run:
                print(f"    [DRY-RUN] would submit")
                continue
            payload = {
                "symbol": symbol, "qty": str(qty), "side": "sell",
                "type": "trailing_stop", "time_in_force": "gtc",
                "trail_percent": str(trail_pct),
            }
            try:
                resp = await client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
                resp.raise_for_status()
                body = resp.json()
                print(f"    OK id={body.get('id')} status={body.get('status')}")
                n_submitted += 1
            except httpx.HTTPStatusError as e:
                print(f"    ERROR {e.response.status_code}: {e.response.text[:200]}",
                      file=sys.stderr)
        print()
        print(f"Submitted {n_submitted} trailing stop(s); skipped {n_skipped} (already protected).")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trail-pct", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.trail_pct, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
