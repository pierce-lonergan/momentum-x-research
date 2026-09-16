#!/usr/bin/env python
"""D200-E2: Entry Timing Optimization Experiment.

Simulates entries at every minute from 9:30 to 10:30 for scenarios with
minute-bar data. Measures optimal entry window per gap classification.

Usage:
    python scripts/experiment_entry_timing.py
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
    """Load all bar recording files. Returns ticker → list of bars."""
    bars: dict[str, list[dict]] = {}
    for bf in glob.glob(str(_DATA / "bars" / "bars_*.json")):
        fname = Path(bf).stem  # bars_TICKER_DATE
        parts = fname.split("_")
        if len(parts) >= 2:
            ticker = parts[1]
        else:
            continue
        try:
            with open(bf) as f:
                bar_data = json.load(f)
            if isinstance(bar_data, list) and len(bar_data) > 0:
                bars[ticker] = bar_data
        except (json.JSONDecodeError, IOError):
            continue
    return bars


def load_scenarios_by_ticker() -> dict[str, dict]:
    """Load scenarios keyed by ticker for outcome joining."""
    sc_path = _DATA / "scenarios" / "gap_scenarios.json"
    if not sc_path.exists():
        return {}
    with open(sc_path) as f:
        data = json.load(f)
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])
    result: dict[str, dict] = {}
    for s in scenarios:
        result[s["ticker"]] = s
    return result


def simulate_entry_at_minute(bars: list[dict], entry_minute: int,
                            stop_pct: float = 0.08,
                            target_pct: float = 0.10) -> dict:
    """Simulate a trade entered at a specific minute offset from market open.

    Args:
        bars: List of OHLCV minute bars (index 0 = 9:30)
        entry_minute: Minutes after 9:30 to enter (0 = 9:30, 15 = 9:45)
        stop_pct: Stop loss distance below entry
        target_pct: Profit target above entry

    Returns:
        Dict with entry_price, exit_price, pnl_pct, exit_reason, hold_minutes
    """
    if entry_minute >= len(bars):
        return {"pnl_pct": 0.0, "exit_reason": "no_data", "hold_minutes": 0}

    # Entry at the close of the entry_minute bar
    entry_bar = bars[entry_minute]
    entry_price = entry_bar.get("close", entry_bar.get("c", 0))
    if entry_price <= 0:
        return {"pnl_pct": 0.0, "exit_reason": "invalid_price", "hold_minutes": 0}

    stop_price = entry_price * (1 - stop_pct)
    target_price = entry_price * (1 + target_pct)

    # Simulate bar-by-bar from entry+1 to EOD
    for i in range(entry_minute + 1, len(bars)):
        bar = bars[i]
        low = bar.get("low", bar.get("l", entry_price))
        high = bar.get("high", bar.get("h", entry_price))
        close = bar.get("close", bar.get("c", entry_price))

        # Check stop
        if low <= stop_price:
            return {
                "entry_price": entry_price,
                "exit_price": stop_price,
                "pnl_pct": -stop_pct,
                "exit_reason": "stop",
                "hold_minutes": i - entry_minute,
            }

        # Check target
        if high >= target_price:
            return {
                "entry_price": entry_price,
                "exit_price": target_price,
                "pnl_pct": target_pct,
                "exit_reason": "target",
                "hold_minutes": i - entry_minute,
            }

    # EOD exit at last bar close
    last_close = bars[-1].get("close", bars[-1].get("c", entry_price))
    pnl_pct = (last_close - entry_price) / entry_price
    return {
        "entry_price": entry_price,
        "exit_price": last_close,
        "pnl_pct": pnl_pct,
        "exit_reason": "eod",
        "hold_minutes": len(bars) - 1 - entry_minute,
    }


def main() -> None:
    print("=" * 70)
    print("D200-E2: ENTRY TIMING OPTIMIZATION")
    print("=" * 70)

    bar_data = load_bar_files()
    scenarios = load_scenarios_by_ticker()
    print(f"Bar files loaded: {len(bar_data)} tickers")
    print(f"Scenarios loaded: {len(scenarios)}")

    if not bar_data:
        print("ERROR: No bar data found in data/bars/")
        sys.exit(1)

    # Entry minutes to test: 0 (9:30) through 60 (10:30)
    entry_minutes = list(range(0, 61, 5))

    # Results: entry_minute → list of pnl_pct
    results: dict[int, list[float]] = defaultdict(list)
    results_by_gap: dict[str, dict[int, list[float]]] = {
        "small_gap": defaultdict(list),   # < 20%
        "medium_gap": defaultdict(list),  # 20-50%
        "large_gap": defaultdict(list),   # > 50%
    }

    for ticker, bars in bar_data.items():
        scenario = scenarios.get(ticker, {})
        gap_pct = abs(scenario.get("gap_pct", 0.15))

        if gap_pct < 0.20:
            gap_cat = "small_gap"
        elif gap_pct < 0.50:
            gap_cat = "medium_gap"
        else:
            gap_cat = "large_gap"

        for entry_min in entry_minutes:
            sim = simulate_entry_at_minute(bars, entry_min)
            pnl = sim.get("pnl_pct", 0.0)
            results[entry_min].append(pnl)
            results_by_gap[gap_cat][entry_min].append(pnl)

    # ── Print results ───────────────────────────────────────────────
    print()
    print("─── ENTRY TIMING: ALL STOCKS ───")
    print(f"  {'Entry Time':<12s} | {'Avg P&L':>8s} | {'Win%':>6s} | {'n':>4s} | {'Chart':>20s}")
    print("  " + "-" * 60)

    best_minute = 0
    best_pnl = -999.0
    for minute in entry_minutes:
        pnls = results[minute]
        if not pnls:
            continue
        avg_pnl = sum(pnls) / len(pnls)
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
        time_str = f"9:{30 + minute:02d}" if minute < 30 else f"10:{minute - 30:02d}"
        bar = "#" * max(0, int((avg_pnl + 0.05) * 100))
        print(f"  {time_str:<12s} | {avg_pnl:+7.2%} | {win_rate:5.0%} | {len(pnls):4d} | {bar}")
        if avg_pnl > best_pnl:
            best_pnl = avg_pnl
            best_minute = minute

    best_time = f"9:{30 + best_minute:02d}" if best_minute < 30 else f"10:{best_minute - 30:02d}"
    print(f"\n  ★ Best entry time: {best_time} (avg P&L = {best_pnl:+.2%})")

    # By gap category
    for gap_cat, gap_results in results_by_gap.items():
        if not any(gap_results.values()):
            continue
        print(f"\n─── ENTRY TIMING: {gap_cat.upper().replace('_', ' ')} ───")
        cat_best_min = 0
        cat_best_pnl = -999.0
        for minute in entry_minutes:
            pnls = gap_results[minute]
            if not pnls:
                continue
            avg_pnl = sum(pnls) / len(pnls)
            win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
            time_str = f"9:{30 + minute:02d}" if minute < 30 else f"10:{minute - 30:02d}"
            print(f"  {time_str:<12s} | {avg_pnl:+7.2%} | {win_rate:5.0%} | n={len(pnls)}")
            if avg_pnl > cat_best_pnl:
                cat_best_pnl = avg_pnl
                cat_best_min = minute
        ct = f"9:{30 + cat_best_min:02d}" if cat_best_min < 30 else f"10:{cat_best_min - 30:02d}"
        print(f"  ★ Best for {gap_cat}: {ct} ({cat_best_pnl:+.2%})")

    print()
    print("RECOMMENDATION:")
    print(f"  Delay entry to {best_time} ET for best average P&L.")
    print(f"  Current D170 window (15 min) aligns with {'early' if best_minute < 15 else 'recommended'} entry.")
    print("=" * 70)


if __name__ == "__main__":
    main()
