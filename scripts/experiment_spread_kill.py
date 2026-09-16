#!/usr/bin/env python
"""D201-E10: Spread Impact Analysis.

Measures how much bid-ask spread is eating into P&L. On low-float stocks,
spreads of 3-10% mean you're already losing before the trade starts.

Scans journal entries for spread data and correlates with outcomes.

Usage:
    python scripts/experiment_spread_kill.py
"""

from __future__ import annotations

import json
import glob
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    print("=" * 70)
    print("D201-E10: SPREAD IMPACT ANALYSIS")
    print("=" * 70)

    # Load live trade outcomes for joining
    live_lookup: dict[str, dict] = {}
    tr_path = _DATA / "trade_results.jsonl"
    if tr_path.exists():
        with open(tr_path) as f:
            for line in f:
                tr = json.loads(line)
                live_lookup[(tr["ticker"], tr["session_date"])] = tr

    # Load scenario outcomes
    sc_path = _DATA / "scenarios" / "gap_scenarios.json"
    sc_lookup: dict[str, dict] = {}
    if sc_path.exists():
        with open(sc_path) as f:
            data = json.load(f)
        for s in (data if isinstance(data, list) else data.get("scenarios", [])):
            sc_lookup[(s["ticker"], s["date"])] = s

    # Scan journals for spread data and gap_pct
    spread_buckets: dict[str, dict] = {
        "< 1%": {"total": 0, "wins": 0, "pnl": 0.0},
        "1-3%": {"total": 0, "wins": 0, "pnl": 0.0},
        "3-5%": {"total": 0, "wins": 0, "pnl": 0.0},
        "5-10%": {"total": 0, "wins": 0, "pnl": 0.0},
        "> 10%": {"total": 0, "wins": 0, "pnl": 0.0},
    }

    gap_vs_spread: list[dict] = []
    entries_with_spread = 0
    entries_without_spread = 0

    for jf in sorted(glob.glob(str(_DATA / "journals" / "journal_*.jsonl"))):
        with open(jf) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ticker = entry.get("ticker", "")
                date = entry.get("session_date", "")
                price = entry.get("current_price", 0)

                # Try to extract spread from input_data
                input_data = entry.get("input_data", {})
                bid = None
                ask = None
                if isinstance(input_data, dict):
                    bid = input_data.get("bid")
                    ask = input_data.get("ask")

                if bid and ask and bid > 0 and ask > 0:
                    spread_pct = (ask - bid) / ((ask + bid) / 2)
                    entries_with_spread += 1
                else:
                    entries_without_spread += 1
                    continue

                # Determine outcome
                outcome = live_lookup.get((ticker, date)) or sc_lookup.get((ticker, date))
                if outcome is None:
                    continue

                is_win = outcome.get("is_win", outcome.get("outcome") == "WIN")
                pnl = outcome.get("pnl", 0.0)

                # Bucket
                if spread_pct < 0.01:
                    bucket = spread_buckets["< 1%"]
                elif spread_pct < 0.03:
                    bucket = spread_buckets["1-3%"]
                elif spread_pct < 0.05:
                    bucket = spread_buckets["3-5%"]
                elif spread_pct < 0.10:
                    bucket = spread_buckets["5-10%"]
                else:
                    bucket = spread_buckets["> 10%"]

                bucket["total"] += 1
                if is_win:
                    bucket["wins"] += 1
                if pnl:
                    bucket["pnl"] += pnl

                gap_vs_spread.append({
                    "ticker": ticker,
                    "spread_pct": spread_pct,
                    "gap_pct": abs(entry.get("gap_pct", 0)),
                    "is_win": is_win,
                })

    print(f"Entries with spread data: {entries_with_spread}")
    print(f"Entries without spread data: {entries_without_spread}")
    print()

    print("--- SPREAD BUCKET -> WIN RATE ---")
    print(f"  {'Spread':<10s} | {'n':>5s} | {'Win%':>6s} | {'W':>4s}/{' L':>4s} | {'P&L':>10s}")
    print("  " + "-" * 50)

    for label, bucket in spread_buckets.items():
        n = bucket["total"]
        if n == 0:
            continue
        wr = bucket["wins"] / n
        losses = n - bucket["wins"]
        pnl_str = f"${bucket['pnl']:+,.0f}" if bucket["pnl"] != 0 else "N/A"
        tag = ""
        if wr < 0.10 and n >= 5:
            tag = " *** TOXIC"
        print(f"  {label:<10s} | {n:5d} | {wr:5.0%} | {bucket['wins']:4d}/{losses:4d} | {pnl_str:>10s}{tag}")

    # Spread vs gap correlation
    if gap_vs_spread:
        avg_spread_winners = [g["spread_pct"] for g in gap_vs_spread if g["is_win"]]
        avg_spread_losers = [g["spread_pct"] for g in gap_vs_spread if not g["is_win"]]
        print()
        print("--- SPREAD: WINNERS vs LOSERS ---")
        if avg_spread_winners:
            print(f"  Winners avg spread: {sum(avg_spread_winners)/len(avg_spread_winners):.2%}")
        if avg_spread_losers:
            print(f"  Losers avg spread:  {sum(avg_spread_losers)/len(avg_spread_losers):.2%}")

    print()
    print("--- RECOMMENDATION ---")
    toxic_spreads = [
        label for label, b in spread_buckets.items()
        if b["total"] >= 5 and b["wins"] / b["total"] < 0.10
    ]
    if toxic_spreads:
        print(f"  Spreads in [{', '.join(toxic_spreads)}] are toxic (< 10% WR).")
        print("  Consider tightening the max-entry-spread filter.")
    else:
        print("  No clear spread toxicity detected (or insufficient data).")

    print("=" * 70)


if __name__ == "__main__":
    main()
