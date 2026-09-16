#!/usr/bin/env python
"""D208: Aggressive Kelly Sizing Arena Test.

Tests what happens when we size positions aggressively based on signal quality.
With D199-D207 gates producing 80% WR on longs, Kelly says we should bet big.

Kelly formula: F* = W - (1-W)/R
  W = 0.80 (win rate from D205)
  R = avg_win/avg_loss = 6.21%/5.58% = 1.11
  F* = 0.80 - 0.20/1.11 = 0.62 → Kelly says bet 62% of bankroll!

Even half-Kelly = 31% per trade. This experiment simulates aggressive sizing
on the D205 passing scenarios to find the optimal risk level.

Usage:
    python scripts/experiment_aggressive_kelly.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def simulate_equity_curve(
    trades: list[dict],
    position_pct: float,
    starting_equity: float = 100_000,
) -> dict:
    """Simulate equity curve with fixed position sizing."""
    equity = starting_equity
    peak = equity
    max_dd = 0
    max_dd_pct = 0
    trade_results = []

    for t in trades:
        position_size = equity * position_pct
        pnl_dollars = position_size * t["pnl_pct"]
        equity += pnl_dollars
        trade_results.append(pnl_dollars)

        if equity > peak:
            peak = equity
        dd = peak - equity
        dd_pct = dd / peak if peak > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd = dd

    total_pnl = equity - starting_equity
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    n = len(trades)

    return {
        "final_equity": equity,
        "total_pnl": total_pnl,
        "total_return_pct": total_pnl / starting_equity,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_dollars": max_dd,
        "win_rate": wins / n if n > 0 else 0,
        "n_trades": n,
        "avg_trade_pnl": sum(trade_results) / n if n > 0 else 0,
        "best_trade": max(trade_results) if trade_results else 0,
        "worst_trade": min(trade_results) if trade_results else 0,
    }


def main() -> None:
    print("=" * 70)
    print("  D208: AGGRESSIVE KELLY SIZING ARENA TEST")
    print("=" * 70)

    # Load scenarios
    with open(_DATA / "scenarios" / "gap_scenarios.json") as f:
        data = json.load(f)
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])

    # Load VIX
    vix = {}
    with open(_DATA / "regime" / "vix_history.csv") as f:
        for row in csv.DictReader(f):
            vix[row["Date"][:10]] = float(row["Close"])

    # D205 gate stack — same as profitability proof
    VIX_BLOCK = 20.0
    VIX_REDUCE = 15.0
    ALLOWED_DAYS = {0, 3}
    GAP_MIN = 0.05
    GAP_MAX = 0.50
    RVOL_MIN = 2.0
    STOP_PCT = 0.35
    TARGET_PCT = 0.10

    # Build passing long trades
    long_trades = []
    for s in scenarios:
        v = vix.get(s["date"])
        try:
            dow = datetime.strptime(s["date"], "%Y-%m-%d").weekday()
        except ValueError:
            continue
        gap = abs(s.get("gap_pct", 0))
        rvol = s.get("rvol", 0)

        if not (v is not None and v < VIX_BLOCK and dow in ALLOWED_DAYS
                and GAP_MIN <= gap <= GAP_MAX and rvol >= RVOL_MIN):
            continue

        high = s.get("high_from_open_pct", 0)
        low = s.get("low_from_open_pct", 0)
        intra = s.get("intraday_return", 0)

        if low <= -STOP_PCT:
            pnl_pct = -STOP_PCT
        elif high >= TARGET_PCT:
            pnl_pct = TARGET_PCT
        else:
            pnl_pct = intra

        # VIX position scaling
        size_mult = 0.5 if v >= VIX_REDUCE else 1.0
        long_trades.append({
            "ticker": s["ticker"],
            "date": s["date"],
            "pnl_pct": pnl_pct * size_mult,
            "gap": gap,
            "rvol": rvol,
            "outcome": s["outcome"],
        })

    # Build passing short trades (D207)
    short_trades = []
    for s in scenarios:
        gap = abs(s.get("gap_pct", 0))
        if gap < 0.30:
            continue
        high = s.get("high_from_open_pct", 0)
        low = s.get("low_from_open_pct", 0)
        intra = s.get("intraday_return", 0)

        # Short simulation
        if high >= 0.35:  # Stop hit
            pnl_pct = -0.35
        elif low <= -0.25:  # T3 hit
            pnl_pct = 0.25
        elif low <= -0.15:  # T2 hit
            pnl_pct = 0.15
        elif low <= -0.05:  # T1 hit
            pnl_pct = 0.05
        else:
            pnl_pct = -intra  # EOD cover

        short_trades.append({
            "ticker": s["ticker"],
            "date": s["date"],
            "pnl_pct": pnl_pct,
            "gap": gap,
            "type": "short",
        })

    # Sort all trades by date for chronological simulation
    all_trades = sorted(long_trades + short_trades, key=lambda t: t["date"])

    # Kelly calculation
    long_wins = sum(1 for t in long_trades if t["pnl_pct"] > 0)
    long_n = len(long_trades)
    long_wr = long_wins / long_n if long_n > 0 else 0
    avg_win = sum(t["pnl_pct"] for t in long_trades if t["pnl_pct"] > 0) / max(long_wins, 1)
    avg_loss = abs(sum(t["pnl_pct"] for t in long_trades if t["pnl_pct"] <= 0) / max(long_n - long_wins, 1))
    R = avg_win / avg_loss if avg_loss > 0 else 1
    kelly_f = long_wr - (1 - long_wr) / R if R > 0 else 0

    print(f"\n  Long trades: {long_n} (WR={long_wr:.0%})")
    print(f"  Short trades: {len(short_trades)} (WR={sum(1 for t in short_trades if t['pnl_pct'] > 0)/len(short_trades):.0%})")
    print(f"  Total trades: {len(all_trades)}")
    print(f"\n  Kelly calculation (longs only):")
    print(f"    W = {long_wr:.2f}, R = {R:.2f}")
    print(f"    Full Kelly: F* = {kelly_f:.2f} ({kelly_f:.0%} of bankroll)")
    print(f"    Half Kelly: {kelly_f/2:.0%}")
    print(f"    Quarter Kelly: {kelly_f/4:.0%}")

    # Simulate different sizing strategies
    sizing_configs = {
        "Conservative (2%)": 0.02,
        "Standard (5%)": 0.05,
        "Moderate (10%)": 0.10,
        "Aggressive (15%)": 0.15,
        "Quarter Kelly (~15%)": kelly_f / 4,
        "Half Kelly (~31%)": kelly_f / 2,
        "Full Kelly (~62%)": kelly_f,
        "YOLO (50%)": 0.50,
    }

    print(f"\n  {'Strategy':<25s} | {'Final $':>10s} | {'Return':>8s} | {'MaxDD':>7s} | {'Avg/Trade':>10s} | {'Worst':>10s}")
    print("  " + "-" * 85)

    for name, size_pct in sizing_configs.items():
        if size_pct <= 0:
            continue
        result = simulate_equity_curve(all_trades, min(size_pct, 0.99), starting_equity=100_000)
        print(
            f"  {name:<25s} | ${result['final_equity']:>9,.0f} | "
            f"{result['total_return_pct']:>+7.1%} | {result['max_drawdown_pct']:>6.1%} | "
            f"${result['avg_trade_pnl']:>+9,.0f} | ${result['worst_trade']:>+9,.0f}"
        )

    # Risk-adjusted comparison
    print(f"\n  --- RISK-ADJUSTED RANKING (Return / MaxDD) ---")
    ranked = []
    for name, size_pct in sizing_configs.items():
        if size_pct <= 0:
            continue
        result = simulate_equity_curve(all_trades, min(size_pct, 0.99), starting_equity=100_000)
        ratio = result["total_return_pct"] / max(result["max_drawdown_pct"], 0.001)
        ranked.append((name, ratio, result))

    for name, ratio, result in sorted(ranked, key=lambda x: -x[1]):
        print(
            f"  {name:<25s}: {ratio:>5.2f} "
            f"(ret={result['total_return_pct']:+.1%}, dd={result['max_drawdown_pct']:.1%})"
        )

    # Recommendation
    best = max(ranked, key=lambda x: x[1])
    print(f"\n  RECOMMENDATION: {best[0]}")
    print(f"  Risk-adjusted ratio: {best[1]:.2f}")
    print(f"  Return: {best[2]['total_return_pct']:+.1%}, MaxDD: {best[2]['max_drawdown_pct']:.1%}")
    print(f"  Final equity: ${best[2]['final_equity']:,.0f} (from $100K)")

    # Tiered sizing: different sizes for longs vs shorts
    print(f"\n  --- TIERED SIZING (different for longs vs shorts) ---")

    for long_size, short_size, label in [
        (0.15, 0.05, "15% long / 5% short"),
        (0.25, 0.05, "25% long / 5% short"),
        (0.30, 0.10, "30% long / 10% short"),
        (0.50, 0.05, "50% long / 5% short"),
    ]:
        equity = 100_000
        peak = equity
        max_dd_pct = 0
        for t in all_trades:
            size = long_size if t.get("type") != "short" else short_size
            pnl = equity * size * t["pnl_pct"]
            equity += pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            if dd > max_dd_pct:
                max_dd_pct = dd

        ret = (equity - 100_000) / 100_000
        ratio = ret / max(max_dd_pct, 0.001)
        print(f"  {label:<25s}: ${equity:>9,.0f} ({ret:+.1%}) DD={max_dd_pct:.1%} ratio={ratio:.2f}")

    print("=" * 70)


if __name__ == "__main__":
    main()
