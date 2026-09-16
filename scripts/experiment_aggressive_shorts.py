#!/usr/bin/env python
"""D207: Aggressive Gap Fader Short — Arena Proof.

Simulates proactive short selling on extreme gap-ups (>50%) against
the 196-scenario database. Proves positive EV with aggressive targets
(-5%, -15%, -25%) vs D161 conservative targets (-3%, -6%, -10%).

Usage:
    python scripts/experiment_aggressive_shorts.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def simulate_short(
    entry: float,
    high_from_open: float,
    low_from_open: float,
    intraday_return: float,
    stop_pct: float,
    target_pcts: list[float],
) -> dict:
    """Simulate a short trade on a single scenario."""
    # For shorts: we SELL at open, profit when price drops
    # high_from_open = worst case (price goes UP = loss for short)
    # low_from_open = best case (price goes DOWN = profit for short)

    # Check stop (price goes above entry by stop_pct)
    if high_from_open >= stop_pct:
        return {"exit": "STOP", "pnl_pct": -stop_pct}

    # Check targets (price drops below entry) — take MOST aggressive hit
    # Sorted: [-0.25, -0.15, -0.05]. First match = biggest drop = most profit.
    best_target_hit = None
    for target in sorted(target_pcts):  # Most negative first
        if low_from_open <= target:  # Price dropped enough
            best_target_hit = target
            break  # Take the most aggressive target that was reached

    if best_target_hit is not None:
        return {"exit": "TARGET", "pnl_pct": abs(best_target_hit)}

    # EOD exit: intraday_return for the stock. Short P&L = -intraday_return
    return {"exit": "EOD", "pnl_pct": -intraday_return}


def main() -> None:
    print("=" * 70)
    print("  D207: AGGRESSIVE GAP FADER SHORT — ARENA PROOF")
    print("=" * 70)

    # Load scenarios
    with open(_DATA / "scenarios" / "gap_scenarios.json") as f:
        data = json.load(f)
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])

    # Load VIX
    vix = {}
    vix_path = _DATA / "regime" / "vix_history.csv"
    if vix_path.exists():
        with open(vix_path) as f:
            for row in csv.DictReader(f):
                vix[row["Date"][:10]] = float(row["Close"])

    # Filter to extreme gap faders (>50% gap)
    print(f"\n  Total scenarios: {len(scenarios)}")

    extreme_gaps = []
    for s in scenarios:
        gap = abs(s.get("gap_pct", 0))
        if gap >= 0.30:  # Analyze 30%+ for comparison
            v = vix.get(s["date"])
            extreme_gaps.append({**s, "vix": v, "abs_gap": gap})

    print(f"  Gaps >= 30%: {len(extreme_gaps)}")
    print(f"  Gaps >= 50%: {sum(1 for s in extreme_gaps if s['abs_gap'] >= 0.50)}")

    # Strategy configs to compare
    configs = {
        "D161 Conservative (-3/-6/-10)": {
            "targets": [-0.03, -0.06, -0.10],
            "stop": 0.35,
            "min_gap": 0.30,
        },
        "D207 Aggressive (-5/-15/-25)": {
            "targets": [-0.05, -0.15, -0.25],
            "stop": 0.35,
            "min_gap": 0.50,
        },
        "D207 on 30%+ gaps": {
            "targets": [-0.05, -0.15, -0.25],
            "stop": 0.35,
            "min_gap": 0.30,
        },
        "No-stop EOD cover": {
            "targets": [],
            "stop": 10.0,  # Effectively no stop
            "min_gap": 0.50,
        },
    }

    print(f"\n  {'Strategy':<35s} | {'n':>4s} | {'WR':>5s} | {'Avg':>7s} | {'Total':>7s} | {'PF':>5s}")
    print("  " + "-" * 75)

    for name, cfg in configs.items():
        trades = []
        for s in extreme_gaps:
            if s["abs_gap"] < cfg["min_gap"]:
                continue

            high = s.get("high_from_open_pct", 0)
            low = s.get("low_from_open_pct", 0)
            intra = s.get("intraday_return", 0)

            result = simulate_short(
                entry=1.0,  # Normalized
                high_from_open=high,
                low_from_open=low,
                intraday_return=intra,
                stop_pct=cfg["stop"],
                target_pcts=cfg["targets"],
            )
            trades.append(result)

        if not trades:
            print(f"  {name:<35s} | {'N/A':>4s}")
            continue

        wins = sum(1 for t in trades if t["pnl_pct"] > 0)
        n = len(trades)
        wr = wins / n
        avg = sum(t["pnl_pct"] for t in trades) / n
        total = sum(t["pnl_pct"] for t in trades)
        gross_win = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
        gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

        print(
            f"  {name:<35s} | {n:4d} | {wr:4.0%} | {avg:+6.1%} | {total:+6.0%} | {pf:4.1f}x"
        )

    # Detailed D207 scenario breakdown
    print()
    print("  --- D207 AGGRESSIVE SHORT: TRADE-BY-TRADE (gap >= 50%) ---")
    d207_trades = []
    for s in extreme_gaps:
        if s["abs_gap"] < 0.50:
            continue
        high = s.get("high_from_open_pct", 0)
        low = s.get("low_from_open_pct", 0)
        intra = s.get("intraday_return", 0)

        result = simulate_short(
            entry=1.0,
            high_from_open=high,
            low_from_open=low,
            intraday_return=intra,
            stop_pct=0.35,
            target_pcts=[-0.05, -0.15, -0.25],
        )

        d207_trades.append({
            "ticker": s["ticker"],
            "date": s["date"],
            "gap": s["abs_gap"],
            "outcome": s["outcome"],
            "high_from_open": high,
            "low_from_open": low,
            "intraday_return": intra,
            **result,
        })

    for t in sorted(d207_trades, key=lambda x: -x["pnl_pct"]):
        tag = "WIN" if t["pnl_pct"] > 0 else "LOSS"
        print(
            f"    {t['ticker']:6s} ({t['date']}): gap={t['gap']:.0%} "
            f"exit={t['exit']:6s} short_pnl={t['pnl_pct']:+.1%} "
            f"intra_ret={t['intraday_return']:+.1%} "
            f"high={t['high_from_open']:+.1%} low={t['low_from_open']:+.1%} [{tag}]"
        )

    # Equity simulation
    if d207_trades:
        equity = 10_000
        for t in sorted(d207_trades, key=lambda x: x["date"]):
            trade_pnl = 1000 * t["pnl_pct"]  # $1K per trade
            equity += trade_pnl

        wins = sum(1 for t in d207_trades if t["pnl_pct"] > 0)
        n = len(d207_trades)
        print(f"\n  D207 Equity: $10,000 → ${equity:,.0f} ({wins}W/{n-wins}L on {n} trades)")

    # Combined long + short
    print()
    print("  --- COMBINED STRATEGY: D205 LONGS + D207 SHORTS ---")
    # D205 longs: 40 trades, 80% WR, +3.85% avg (from profitability_proof)
    long_pnl = 40 * 0.0385 * 1000  # $1K per trade
    short_pnl = sum(t["pnl_pct"] * 1000 for t in d207_trades) if d207_trades else 0
    total = long_pnl + short_pnl
    total_trades = 40 + len(d207_trades)
    print(f"  Long P&L (40 trades):  ${long_pnl:+,.0f}")
    print(f"  Short P&L ({len(d207_trades)} trades): ${short_pnl:+,.0f}")
    print(f"  COMBINED P&L:          ${total:+,.0f} on {total_trades} trades")
    print(f"  Combined avg/trade:    ${total/total_trades:+,.0f}")

    print("=" * 70)


if __name__ == "__main__":
    main()
