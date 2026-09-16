#!/usr/bin/env python
"""D201-E9: Stop Loss Autopsy.

Analyzes whether stop losses are helping or hurting. If stocks that get stopped
out subsequently recover and run, the stops are destroying value.

Uses minute-bar data to measure:
1. How often does a stopped-out stock later reach the profit target?
2. What's the optimal stop distance (wider vs tighter)?
3. Are stops firing on normal pullbacks vs genuine reversals?

Usage:
    python scripts/experiment_stop_analysis.py
"""

from __future__ import annotations

import json
import glob
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def load_bar_files() -> dict[str, list[dict]]:
    """Load all bar recording files."""
    bars: dict[str, list[dict]] = {}
    for bf in glob.glob(str(_DATA / "bars" / "bars_*.json")):
        fname = Path(bf).stem
        parts = fname.split("_")
        if len(parts) >= 2:
            ticker = parts[1]
            try:
                with open(bf) as f:
                    bar_data = json.load(f)
                if isinstance(bar_data, list) and len(bar_data) > 10:
                    bars[ticker] = bar_data
            except (json.JSONDecodeError, IOError):
                continue
    return bars


def simulate_stop_levels(bars: list[dict], stop_pcts: list[float],
                         target_pct: float = 0.10) -> dict:
    """Simulate different stop levels on the same price path.

    Returns: {stop_pct: {result: "stop"|"target"|"eod", pnl_pct, stop_minute, recovered}}
    """
    if not bars:
        return {}

    entry_price = bars[0].get("close", bars[0].get("c", 0))
    if entry_price <= 0:
        return {}

    target_price = entry_price * (1 + target_pct)
    results = {}

    for stop_pct in stop_pcts:
        stop_price = entry_price * (1 - stop_pct)
        stopped_out = False
        stop_minute = 0
        exit_price = entry_price
        exit_reason = "eod"

        for i in range(1, len(bars)):
            bar = bars[i]
            low = bar.get("low", bar.get("l", entry_price))
            high = bar.get("high", bar.get("h", entry_price))

            if not stopped_out:
                if low <= stop_price:
                    stopped_out = True
                    stop_minute = i
                    exit_price = stop_price
                    exit_reason = "stop"
                    # Don't break — keep tracking to see if it recovers
                elif high >= target_price:
                    exit_price = target_price
                    exit_reason = "target"
                    break

        # If stopped out, did price later reach the target?
        recovered = False
        max_after_stop = 0.0
        if stopped_out:
            for i in range(stop_minute + 1, len(bars)):
                bar = bars[i]
                high = bar.get("high", bar.get("h", 0))
                if high > max_after_stop:
                    max_after_stop = high
                if high >= target_price:
                    recovered = True
                    break

        if exit_reason == "eod":
            exit_price = bars[-1].get("close", bars[-1].get("c", entry_price))

        pnl_pct = (exit_price - entry_price) / entry_price

        results[stop_pct] = {
            "exit_reason": exit_reason,
            "pnl_pct": pnl_pct,
            "stop_minute": stop_minute if stopped_out else None,
            "recovered": recovered,
            "max_after_stop_pct": (max_after_stop - entry_price) / entry_price if stopped_out else None,
        }

    return results


def main() -> None:
    print("=" * 70)
    print("D201-E9: STOP LOSS AUTOPSY")
    print("=" * 70)

    bar_data = load_bar_files()
    print(f"Loaded bar data for {len(bar_data)} tickers")

    if not bar_data:
        print("ERROR: No bar data found")
        sys.exit(1)

    stop_levels = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.35]
    target_pct = 0.10

    # Aggregate results
    stats: dict[float, dict] = {
        sp: {"stops": 0, "targets": 0, "eods": 0, "recoveries": 0,
             "total_pnl": 0.0, "total": 0}
        for sp in stop_levels
    }

    for ticker, bars in bar_data.items():
        results = simulate_stop_levels(bars, stop_levels, target_pct=target_pct)
        for sp, result in results.items():
            s = stats[sp]
            s["total"] += 1
            s["total_pnl"] += result["pnl_pct"]
            if result["exit_reason"] == "stop":
                s["stops"] += 1
                if result["recovered"]:
                    s["recoveries"] += 1
            elif result["exit_reason"] == "target":
                s["targets"] += 1
            else:
                s["eods"] += 1

    # Print results
    print(f"\nTarget: +{target_pct:.0%} profit target | {len(bar_data)} tickers")
    print()
    print(f"  {'Stop%':>6s} | {'Stops':>6s} | {'Targets':>8s} | {'EOD':>5s} | {'Recovered':>10s} | {'Avg P&L':>8s} | {'Win%':>6s}")
    print("  " + "-" * 75)

    for sp in stop_levels:
        s = stats[sp]
        n = s["total"]
        if n == 0:
            continue
        win_rate = (s["targets"] + sum(1 for _ in [] if True)) / n  # targets = wins
        # Wins = targets + positive EOD exits
        # For simplicity, count targets as wins
        wr = s["targets"] / n
        avg_pnl = s["total_pnl"] / n
        recovery_pct = s["recoveries"] / s["stops"] if s["stops"] > 0 else 0

        tag = ""
        if recovery_pct > 0.50:
            tag = " *** STOP DESTROYING VALUE"
        elif recovery_pct > 0.30:
            tag = " * stops too tight"

        print(
            f"  {sp:5.0%} | {s['stops']:6d} | {s['targets']:8d} | {s['eods']:5d} | "
            f"{s['recoveries']}/{s['stops']} ({recovery_pct:.0%}) | "
            f"{avg_pnl:+7.2%} | {wr:5.0%}{tag}"
        )

    # Find optimal stop
    best_sp = max(stop_levels, key=lambda sp: stats[sp]["total_pnl"] / max(stats[sp]["total"], 1))
    best_pnl = stats[best_sp]["total_pnl"] / max(stats[best_sp]["total"], 1)

    print(f"\n  Best stop distance: {best_sp:.0%} (avg P&L = {best_pnl:+.2%})")

    # No-stop comparison
    no_stop_results = simulate_stop_levels(
        list(bar_data.values())[0] if bar_data else [],
        [1.0],  # 100% = effectively no stop
        target_pct=target_pct,
    )

    print()
    print("--- INTERPRETATION ---")
    print(f"  If 'Recovered' rate > 50%, stops are destroying winning trades")
    print(f"  If wider stops have higher avg P&L, current stops are too tight")
    print(f"  Current production stop: 35% (D100 ATR-based, gap-day widening)")
    print("=" * 70)


if __name__ == "__main__":
    main()
