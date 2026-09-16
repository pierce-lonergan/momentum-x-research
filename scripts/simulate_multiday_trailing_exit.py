"""Simulate a multi-day TRAILING-STOP exit vs the one-day EOD close (doc 230).

Reads data/research/multiday_hold_dataset.parquet — split-adjusted forward 1-5d OHLC stored as
returns vs the SIGNAL-DAY CLOSE (the price we 'failed to sell at'). Question Pierce posed: does
holding past the signal-day close with a TRAILING STOP beat selling at that close (~our current
one-day policy, which = 0% by construction here)?

Look-ahead-safe DAILY trailing: each forward day, check the stop (trail off the peak THROUGH the
PRIOR day, plus gap-at-open) BEFORE updating the peak with today's high. Stop fills at the level,
or at the open if it gapped through overnight (the realistic worse fill). No intraday path within
a day — this is a daily-bar approximation; minute-bar realism is a follow-up (gap #3).

Usage:
  python scripts/simulate_multiday_trailing_exit.py --horizon 5
  python scripts/simulate_multiday_trailing_exit.py --horizon 5 --min-close 1.0   # drop pennies
"""
from __future__ import annotations

import argparse
import duckdb
import pandas as pd

PARQUET = "data/research/multiday_hold_dataset.parquet"
TRAILS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]


def reconstruct(row, H):
    """Rebuild the split-adjusted (open, high, low, close) path in entry-price scale."""
    base = float(row["base_close"])
    days = []
    prev_close = base
    for k in range(1, H + 1):
        c = base * (1 + row[f"d{k}_close_ret"])
        hi = base * (1 + row[f"d{k}_high_ret"])
        lo = base * (1 + row[f"d{k}_low_ret"])
        og = row.get(f"d{k}_open_gap", 0.0) or 0.0
        op = prev_close * (1 + og)
        days.append((op, hi, lo, c))
        prev_close = c
    return base, days


def sim_trail(row, H, trail):
    """Return the realized return of a trailing-stop hold from the signal-day close."""
    base, days = reconstruct(row, H)
    peak = base
    for (op, hi, lo, c) in days:
        stop = peak * (1 - trail)
        if op <= stop:            # gapped through the stop overnight -> exit at the open
            return op / base - 1
        if lo <= stop:            # hit intraday -> assume fill at the stop level
            return stop / base - 1
        peak = max(peak, hi)      # ratchet AFTER the stop check (no intraday look-ahead)
    return days[-1][3] / base - 1  # never stopped -> exit at the dH close


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--min-close", type=float, default=0.0,
                    help="liquidity proxy: drop entries with base_close < this (pennies)")
    a = ap.parse_args()
    H = a.horizon

    df = duckdb.connect().execute(f"SELECT * FROM read_parquet('{PARQUET}')").df()
    df = df[~df.get("suspected_uncaptured_split", False)].copy()
    if a.min_close > 0:
        df = df[df["base_close"] >= a.min_close].copy()
    print(f"n={len(df)} clean entries (min_close=${a.min_close})  horizon={H}d\n")

    hold = df[f"d{H}_close_ret"]
    peak = df[f"run_high_thru_d{H}"]
    print("BASELINES (return vs the signal-day close = what we 'failed to sell at'):")
    print(f"  exit AT d0 close (our ~1-day policy): 0.0% by construction")
    print(f"  hold to d{H} close (naive):  median {hold.median()*100:+.1f}%  "
          f"mean {hold.mean()*100:+.1f}%  win {(hold>0).mean()*100:.0f}%")
    print(f"  peak ceiling (perfect trail): median {peak.median()*100:+.1f}%  "
          f"mean {peak.mean()*100:+.1f}%  (unreachable upper bound)\n")

    print("TRAILING-STOP SWEEP (exit when price falls trail% off the running peak):")
    print(f"  {'trail':>5} {'median':>8} {'mean':>8} {'win%':>6} {'%>+10':>6} {'%<-10':>6} {'beat-d0':>8}")
    best = None
    for trail in TRAILS:
        rets = df.apply(lambda r: sim_trail(r, H, trail), axis=1)
        row = (trail, rets.median(), rets.mean(), (rets > 0).mean(),
               (rets > 0.10).mean(), (rets < -0.10).mean())
        print(f"  {trail*100:>4.0f}% {rets.median()*100:>7.1f}% {rets.mean()*100:>7.1f}% "
              f"{(rets>0).mean()*100:>5.0f}% {(rets>0.10).mean()*100:>5.0f}% "
              f"{(rets<-0.10).mean()*100:>5.0f}% {(rets>0).mean()*100:>7.0f}%")
        if best is None or row[2] > best[2]:
            best = row

    bt = best[0]
    print(f"\nby MFCS bucket (trail={bt*100:.0f}%, the best-mean trail; return vs d0 close):")
    dd = df.dropna(subset=["mfcs"]).copy()
    dd["ret"] = dd.apply(lambda r: sim_trail(r, H, bt), axis=1)
    dd["bucket"] = pd.cut(dd["mfcs"], [0, 0.3, 0.45, 0.55, 1.0])
    for b, g in dd.groupby("bucket", observed=True):
        if len(g) < 5:
            continue
        print(f"  MFCS {str(b):>12}: n={len(g):>4}  median {g['ret'].median()*100:>+6.1f}%  "
              f"mean {g['ret'].mean()*100:>+6.1f}%  win {(g['ret']>0).mean()*100:.0f}%  "
              f"hold-close median {g[f'd{H}_close_ret'].median()*100:+.1f}%")

    print(f"\nby gap classification (trail={bt*100:.0f}%):")
    for b, g in dd.groupby("gap_classification", observed=True):
        if len(g) < 5:
            continue
        print(f"  {str(b):>14}: n={len(g):>4}  median {g['ret'].median()*100:>+6.1f}%  "
              f"mean {g['ret'].mean()*100:>+6.1f}%  win {(g['ret']>0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
