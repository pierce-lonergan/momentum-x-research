#!/usr/bin/env python
"""D215 Day E: Journal-Alpaca Reconciliation

Compares Alpaca order history against journal entries to verify 1:1
coverage. Every Alpaca fill should have a corresponding journal entry
with non-None features.

Usage:
    python scripts/reconcile_journal.py
    python scripts/reconcile_journal.py --date 2026-04-09
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    from dotenv import load_dotenv
    load_dotenv()
    import requests

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date to reconcile (YYYY-MM-DD)")
    args = parser.parse_args()

    h = {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }

    date_filter = args.date or ""

    # 1. Get Alpaca filled buy orders
    params = {"status": "all", "limit": 100, "side": "buy"}
    if date_filter:
        params["after"] = f"{date_filter}T00:00:00Z"
        params["until"] = f"{date_filter}T23:59:59Z"

    r = requests.get(
        "https://paper-api.alpaca.markets/v2/orders",
        headers=h, params=params, timeout=10,
    )
    orders = r.json()

    alpaca_buys = []
    for o in orders:
        fp = float(o.get("filled_avg_price") or 0)
        if fp > 0 and o["side"] == "buy":
            alpaca_buys.append({
                "ticker": o["symbol"],
                "order_id": o["id"],
                "fill_price": fp,
                "qty": int(o.get("filled_qty") or 0),
                "time": o["created_at"][:19],
                "date": o["created_at"][:10],
                "type": o.get("type", "?"),
            })

    # 2. Get journal entries
    journal_dir = _DATA / "journals"
    journal_fills = {}  # order_id -> entry
    journal_features = {}  # ticker -> {gap_pct, rvol, mfcs, ...}

    for jf in sorted(journal_dir.glob("journal_*.jsonl")):
        if date_filter and date_filter not in jf.name:
            continue
        for line in jf.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                oid = d.get("order_id", "")
                ticker = d.get("ticker", "")
                if oid:
                    journal_fills[oid] = d
                if ticker and d.get("action") in ("BUY", "STRONG_BUY"):
                    journal_features[ticker] = {
                        "gap_pct": d.get("gap_pct"),
                        "rvol": d.get("rvol"),
                        "mfcs": d.get("mfcs"),
                        "fill_price": d.get("fill_price"),
                        "slippage_bps": d.get("slippage_bps"),
                        "phase": d.get("phase", "?"),
                    }
            except Exception:
                pass

    # 3. Reconcile
    print("=" * 60)
    print(f"  D215 DAY E: JOURNAL-ALPACA RECONCILIATION")
    if date_filter:
        print(f"  Date: {date_filter}")
    print("=" * 60)

    print(f"\n  Alpaca filled buys: {len(alpaca_buys)}")
    print(f"  Journal entries with order_id: {len(journal_fills)}")

    matched = 0
    unmatched_alpaca = []
    feature_gaps = []

    for ab in alpaca_buys:
        oid = ab["order_id"]
        if oid in journal_fills:
            matched += 1
            # Check feature completeness
            jf = journal_fills[oid]
            missing = []
            if not jf.get("gap_pct") and jf.get("gap_pct") != 0:
                missing.append("gap_pct")
            if not jf.get("rvol") and jf.get("rvol") != 0:
                missing.append("rvol")
            if jf.get("fill_price") is None:
                missing.append("fill_price")
            if missing:
                feature_gaps.append({"ticker": ab["ticker"], "order_id": oid[:8], "missing": missing})
        else:
            unmatched_alpaca.append(ab)

    print(f"\n  RECONCILIATION:")
    print(f"    Matched:       {matched}/{len(alpaca_buys)}")
    print(f"    Unmatched:     {len(unmatched_alpaca)}")

    if unmatched_alpaca:
        print(f"\n  UNMATCHED ALPACA ORDERS (no journal entry):")
        for ua in unmatched_alpaca:
            print(f"    {ua['ticker']:6s} {ua['time']} qty={ua['qty']} fill=${ua['fill_price']:.2f} id={ua['order_id'][:8]}")

    if feature_gaps:
        print(f"\n  FEATURE GAPS (matched but incomplete):")
        for fg in feature_gaps:
            print(f"    {fg['ticker']:6s} order={fg['order_id']} missing: {fg['missing']}")

    # Verdict
    print(f"\n  VERDICT:")
    if len(unmatched_alpaca) == 0 and len(feature_gaps) == 0:
        print(f"    PASS: 1:1 reconciliation with complete features")
    elif len(unmatched_alpaca) == 0:
        print(f"    PARTIAL: All orders matched but {len(feature_gaps)} have incomplete features")
    else:
        coverage = matched / len(alpaca_buys) * 100 if alpaca_buys else 100
        print(f"    FAIL: {len(unmatched_alpaca)} orders missing from journal ({coverage:.0f}% coverage)")
        if coverage < 90:
            print(f"    CRITICAL: Coverage below 90% — execution recorder may not be wired correctly")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
