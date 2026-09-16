#!/usr/bin/env python
"""D200-E7: Time-of-Day Exit Curve Experiment.

Plots P&L curve from entry to 16:00 for stocks with minute-bar data.
Finds the optimal time-based exit point (when do gap-ups peak on average?).

Usage:
    python scripts/experiment_exit_timing.py
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
        else:
            continue
        try:
            with open(bf) as f:
                bar_data = json.load(f)
            if isinstance(bar_data, list) and len(bar_data) > 10:
                bars[ticker] = bar_data
        except (json.JSONDecodeError, IOError):
            continue
    return bars


def compute_pnl_curve(bars: list[dict]) -> list[float]:
    """Compute cumulative P&L curve from open price at each minute.

    Returns list of pnl_pct values, one per bar (index 0 = 9:30).
    Entry assumed at bar 0 close (9:30 candle close = 9:31 effectively).
    """
    if not bars:
        return []

    entry_price = bars[0].get("close", bars[0].get("c", 0))
    if entry_price <= 0:
        return []

    curve = []
    for bar in bars:
        close = bar.get("close", bar.get("c", entry_price))
        pnl_pct = (close - entry_price) / entry_price
        curve.append(pnl_pct)
    return curve


def main() -> None:
    print("=" * 70)
    print("D200-E7: TIME-OF-DAY EXIT CURVE")
    print("=" * 70)

    bar_data = load_bar_files()
    print(f"Loaded bar data for {len(bar_data)} tickers")

    if not bar_data:
        print("ERROR: No bar data found in data/bars/")
        sys.exit(1)

    # Compute P&L curves
    all_curves: list[list[float]] = []
    peak_minutes: list[int] = []

    for ticker, bars in bar_data.items():
        curve = compute_pnl_curve(bars)
        if len(curve) < 30:
            continue
        all_curves.append(curve)

        # Find peak minute
        peak_pnl = max(curve)
        peak_min = curve.index(peak_pnl)
        peak_minutes.append(peak_min)

    if not all_curves:
        print("ERROR: No valid P&L curves computed")
        sys.exit(1)

    # Compute average P&L at each minute across all tickers
    max_len = max(len(c) for c in all_curves)
    avg_curve: list[float] = []
    n_at_minute: list[int] = []

    for minute in range(min(max_len, 390)):  # max 390 min in trading day
        values = [c[minute] for c in all_curves if minute < len(c)]
        if values:
            avg_curve.append(sum(values) / len(values))
            n_at_minute.append(len(values))
        else:
            avg_curve.append(0.0)
            n_at_minute.append(0)

    # Find average peak
    avg_peak_pnl = max(avg_curve) if avg_curve else 0.0
    avg_peak_minute = avg_curve.index(avg_peak_pnl) if avg_curve else 0

    # ── Results: P&L curve at key times ─────────────────────────────
    print(f"\nAnalyzed {len(all_curves)} tickers with bar data")
    print()
    print("─── AVERAGE P&L CURVE (SELECTED TIMESTAMPS) ───")
    print(f"  {'Time ET':<10s} | {'Min#':>4s} | {'Avg P&L':>8s} | {'n':>4s} | {'Chart':>20s}")
    print("  " + "-" * 55)

    checkpoints = [0, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 360]
    for minute in checkpoints:
        if minute >= len(avg_curve):
            break
        hour = 9 + (30 + minute) // 60
        min_part = (30 + minute) % 60
        time_str = f"{hour}:{min_part:02d}"
        pnl = avg_curve[minute]
        n = n_at_minute[minute]
        bar_len = max(0, int((pnl + 0.05) * 100))
        bar = "#" * min(bar_len, 20)
        marker = " ★" if minute == avg_peak_minute else ""
        print(f"  {time_str:<10s} | {minute:4d} | {pnl:+7.2%} | {n:4d} | {bar}{marker}")

    peak_hour = 9 + (30 + avg_peak_minute) // 60
    peak_min_part = (30 + avg_peak_minute) % 60
    peak_time = f"{peak_hour}:{peak_min_part:02d}"

    print(f"\n  ★ Average peak: {peak_time} ET (minute #{avg_peak_minute}, P&L = {avg_peak_pnl:+.2%})")

    # ── Peak minute distribution ────────────────────────────────────
    print()
    print("─── PEAK MINUTE DISTRIBUTION ───")
    print(f"  When does each stock reach its daily high?")

    peak_buckets = defaultdict(int)
    for pm in peak_minutes:
        if pm < 15:
            peak_buckets["9:30-9:44"] += 1
        elif pm < 30:
            peak_buckets["9:45-9:59"] += 1
        elif pm < 60:
            peak_buckets["10:00-10:29"] += 1
        elif pm < 120:
            peak_buckets["10:30-11:29"] += 1
        elif pm < 210:
            peak_buckets["11:30-1:00"] += 1
        else:
            peak_buckets["1:00-4:00"] += 1

    for window, count in sorted(peak_buckets.items()):
        pct = count / len(peak_minutes)
        bar = "#" * int(pct * 30)
        print(f"  {window:<15s} | {count:3d} ({pct:4.0%}) {bar}")

    # ── Compare exit strategies ─────────────────────────────────────
    print()
    print("─── EXIT STRATEGY COMPARISON ───")

    strategies = {
        "EOD (current)": len(avg_curve) - 1 if avg_curve else 0,
        f"Peak time ({peak_time})": avg_peak_minute,
        "30 min hold": 30,
        "60 min hold": 60,
        "90 min hold": 90,
        "120 min hold": 120,
    }

    for name, exit_min in strategies.items():
        if exit_min >= len(avg_curve):
            exit_min = len(avg_curve) - 1
        pnl = avg_curve[exit_min] if exit_min < len(avg_curve) else 0
        marker = " ★ BEST" if exit_min == avg_peak_minute else ""
        print(f"  {name:<25s} | avg P&L = {pnl:+.2%}{marker}")

    print()
    print("RECOMMENDATION:")
    if avg_peak_minute < 60:
        print(f"  Gap-ups peak early ({peak_time} ET). Consider selling 50% at {peak_time}.")
    elif avg_peak_minute < 180:
        print(f"  Gap-ups peak mid-day ({peak_time} ET). Current D170+trailing strategy is reasonable.")
    else:
        print(f"  Gap-ups peak late ({peak_time} ET). Holding to EOD is justified.")

    eod_pnl = avg_curve[-1] if avg_curve else 0
    if avg_peak_pnl > eod_pnl * 1.5:
        print(f"  ⚠ Peak P&L ({avg_peak_pnl:+.2%}) is {avg_peak_pnl/eod_pnl:.1f}x the EOD P&L ({eod_pnl:+.2%})")
        print(f"    → Time-based exit at {peak_time} would significantly improve returns")

    print("=" * 70)


if __name__ == "__main__":
    main()
