#!/usr/bin/env python
"""D215 P1: Kelly Position Sizing Audit

Computes optimal half-Kelly from historical trade data and compares to
current tier sizing + per-trade max position cap. Identifies if the
system is under-betting or over-betting.

Usage:
    python scripts/kelly_audit.py
    python scripts/kelly_audit.py --from-scenarios  # Use 196 gap scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Compute Kelly Criterion fraction.

    f* = (p * b - q) / b
    where p = win_rate, q = 1-p, b = avg_win/avg_loss (odds ratio)
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.0
    b = abs(avg_win / avg_loss)
    q = 1 - win_rate
    f = (win_rate * b - q) / b
    return max(0.0, f)


def main():
    parser = argparse.ArgumentParser(description="D215: Kelly Position Sizing Audit")
    parser.add_argument("--from-scenarios", action="store_true", help="Use gap scenarios instead of journals")
    args = parser.parse_args()

    print("=" * 70)
    print("  D215: KELLY POSITION SIZING AUDIT")
    print("=" * 70)

    # Load current settings
    from config.settings import Settings
    s = Settings()

    print("\n  CURRENT TIER CONFIGURATION:")
    kt = s.kelly_tier
    print(f"    Tier 1 (Standard):  risk={kt.tier1_risk_pct*100:.1f}%, max_pos={kt.tier1_max_position_pct*100:.0f}%")
    print(f"    Tier 2 (High Conv): risk={kt.tier2_risk_pct*100:.1f}%, max_pos={kt.tier2_max_position_pct*100:.0f}%")
    print(f"    Tier 3 (Except):    risk={kt.tier3_risk_pct*100:.1f}%, max_pos={kt.tier3_max_position_pct*100:.0f}%")
    print(f"    Tier 4 (Outlier):   risk={kt.tier4_risk_pct*100:.1f}%, max_pos={kt.tier4_max_position_pct*100:.0f}%")
    print(f"    Global max_position_pct: {s.execution.max_position_pct*100:.0f}%")

    # Load trade data
    if args.from_scenarios:
        path = _DATA / "scenarios" / "gap_scenarios.json"
        data = json.loads(path.read_text())
        scenarios = data if isinstance(data, list) else data.get("scenarios", [])
        bar_dir = _DATA / "scenarios" / "minute_bars"

        trades = []
        for sc in scenarios:
            bf = bar_dir / f"{sc['ticker']}_{sc['date']}.json"
            if not bf.exists():
                continue
            bars = json.loads(bf.read_text()).get("bars", [])
            if len(bars) < 2:
                continue
            entry = bars[0]["open"]
            if entry <= 0:
                continue
            # T+1m exit (bar-1)
            exit_price = bars[1]["close"] if len(bars) > 1 else entry
            pnl_pct = (exit_price - entry) / entry
            trades.append(pnl_pct)

        print(f"\n  DATA SOURCE: 196 gap scenarios (bar-1 exit)")
    else:
        # Try to load from journal files
        journal_dir = _DATA / "journals"
        trades = []
        for jf in sorted(journal_dir.glob("journal_*.jsonl")):
            for line in jf.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    if d.get("fill_price") and d.get("exit_price"):
                        pnl = (d["exit_price"] - d["fill_price"]) / d["fill_price"]
                        trades.append(pnl)
                except Exception:
                    continue

        if not trades:
            print("\n  No journal trade data found. Using --from-scenarios instead.")
            return

        print(f"\n  DATA SOURCE: {len(trades)} journal trades")

    if len(trades) < 10:
        print(f"  Too few trades ({len(trades)}) for Kelly computation.")
        return

    # Compute Kelly
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    n = len(trades)
    n_wins = len(wins)
    n_losses = len(losses)
    wr = n_wins / n
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else float("inf")

    full_kelly = kelly_fraction(wr, avg_win, avg_loss)
    half_kelly = full_kelly / 2
    quarter_kelly = full_kelly / 4

    print(f"\n  TRADE STATISTICS:")
    print(f"    Trades:        {n}")
    print(f"    Win Rate:      {wr:.1%} ({n_wins}W / {n_losses}L)")
    print(f"    Avg Winner:    {avg_win*100:+.2f}%")
    print(f"    Avg Loser:     {avg_loss*100:+.2f}%")
    print(f"    Win/Loss Ratio:{abs(avg_win/avg_loss):.2f}x" if avg_loss != 0 else "    Win/Loss: inf")
    print(f"    Profit Factor: {profit_factor:.2f}x")

    print(f"\n  KELLY FRACTIONS:")
    print(f"    Full Kelly:    {full_kelly*100:.1f}% per trade")
    print(f"    Half Kelly:    {half_kelly*100:.1f}% per trade (recommended)")
    print(f"    Quarter Kelly: {quarter_kelly*100:.1f}% per trade (conservative)")

    # Compare to current
    print(f"\n  GAP ANALYSIS:")
    current_tier1_risk = kt.tier1_risk_pct
    print(f"    Current Tier 1 risk: {current_tier1_risk*100:.1f}%")
    print(f"    Optimal half-Kelly:  {half_kelly*100:.1f}%")

    if half_kelly > current_tier1_risk * 1.2:
        gap = (half_kelly - current_tier1_risk) * 100
        print(f"    >>> UNDER-BETTING by {gap:.1f}pp — increase Tier 1 risk to {half_kelly*100:.1f}%")
    elif half_kelly < current_tier1_risk * 0.8:
        gap = (current_tier1_risk - half_kelly) * 100
        print(f"    >>> OVER-BETTING by {gap:.1f}pp — reduce Tier 1 risk to {half_kelly*100:.1f}%")
    else:
        print(f"    >>> SIZING IS APPROPRIATE (within 20% of optimal)")

    # Check max position cap
    max_pos = s.execution.max_position_pct
    print(f"\n    Current max position cap: {max_pos*100:.0f}%")
    if max_pos < half_kelly * 2:
        print(f"    >>> Cap is BINDING — Kelly wants up to {half_kelly*200:.0f}% but cap is {max_pos*100:.0f}%")
    else:
        print(f"    >>> Cap is NOT binding — Kelly stays well below {max_pos*100:.0f}%")

    # Bootstrap confidence interval on Kelly
    rng = np.random.default_rng(42)
    kelly_samples = []
    arr = np.array(trades)
    for _ in range(1000):
        sample = rng.choice(arr, size=len(arr), replace=True)
        w = [s for s in sample if s > 0]
        l = [s for s in sample if s <= 0]
        if w and l:
            kelly_samples.append(kelly_fraction(
                len(w) / len(sample),
                np.mean(w), np.mean(l),
            ))
    if kelly_samples:
        kelly_arr = np.array(kelly_samples)
        print(f"\n  KELLY BOOTSTRAP 95% CI:")
        print(f"    Full Kelly: [{np.percentile(kelly_arr, 2.5)*100:.1f}%, {np.percentile(kelly_arr, 97.5)*100:.1f}%]")
        print(f"    Half Kelly: [{np.percentile(kelly_arr, 2.5)*50:.1f}%, {np.percentile(kelly_arr, 97.5)*50:.1f}%]")

        if np.percentile(kelly_arr, 2.5) <= 0:
            print(f"    WARNING: Kelly CI includes ZERO — the edge may not be real")

    # Recommendation
    print(f"\n  RECOMMENDATION:")
    if full_kelly <= 0:
        print(f"    Kelly fraction is ZERO or NEGATIVE — no edge detected.")
        print(f"    Do NOT increase position sizes. Current sizing is already generous.")
    elif half_kelly > current_tier1_risk * 1.5:
        print(f"    INCREASE Tier 1 risk from {current_tier1_risk*100:.1f}% to {half_kelly*100:.1f}%")
        print(f"    This could increase PnL by ~{(half_kelly/current_tier1_risk - 1)*100:.0f}%")
    else:
        print(f"    Current sizing is within range. No change needed.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
