"""Final candidate strategy v1: regime-filtered H3 + walk-forward validation.

Best regime filter from regime_overlay_summary.json:
  F5_avg_dvol_top5 in BOTTOM tercile → H3 (short top-1 of first-30-min ≥20%
  with target -30% / stop +15%) produces +7.02%/trade, 67% win, +224% compound.

This script:
  A) Replicates that finding precisely
  B) Walk-forward validation: train tercile cutoff on first 50% of dates,
     test on second 50%. If still positive, the signal is robust.
  C) Trade-by-trade audit of the bottom-tercile bucket
  D) Bootstrap CI on the filtered strategy
  E) Tests OPPOSITE regime: F5 in TOP tercile, do we go LONG (since shorts fail)?
"""
from __future__ import annotations

import json
import random
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest_5_hypotheses import (  # type: ignore
    load_all_dates, first_30_max_return, simulate_short_trade,
    simulate_long_trade, OUT_DIR,
)
from backtest_regime_overlay import daily_features, run_h3_with_filter, summary  # type: ignore


def run_h1_with_filter(by_date: dict, eligible_dates: set,
                        entry=(13, 0), k=3) -> list[dict]:
    out = []
    for date_str, tickers in by_date.items():
        if date_str not in eligible_dates: continue
        scored = []
        for t in tickers:
            f30, _, _ = first_30_max_return(t["rth"])
            if f30 is None: continue
            scored.append((t, f30))
        scored.sort(key=lambda x: -x[1])
        for t, f30 in scored[:k]:
            rth = t["rth"]
            target = rth[0][0].replace(hour=entry[0], minute=entry[1], second=0)
            entry_bar = next((b for ts, b in rth if ts >= target), None)
            if entry_bar is None: continue
            ats = next(ts for ts, b in rth if ts >= target)
            entry_px = float(entry_bar["open"])
            sim = simulate_long_trade(rth, ats, entry_px, 0.20, -0.10)
            out.append({"date": date_str, "ticker": t["ticker"],
                        "first_30": f30,
                        "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"]})
    return out


def bootstrap(pnls: list[float], n_iter: int = 5000) -> dict:
    n = len(pnls)
    if n == 0: return {}
    rng = random.Random(42)
    means = []
    for _ in range(n_iter):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return {
        "n": n,
        "mean_observed": sum(pnls) / n,
        "p05": means[int(n_iter * 0.05)],
        "p50": means[int(n_iter * 0.50)],
        "p95": means[int(n_iter * 0.95)],
        "share_positive": sum(1 for m in means if m > 0) / n_iter,
    }


def main():
    by_date = load_all_dates()
    feat = daily_features(by_date).sort_values("date").reset_index(drop=True)

    # ─── A) Replicate finding ───
    print("=== A) Replicate F5 lo-tercile H3 ===")
    feat["F5_bin"] = pd.qcut(feat["F5_avg_dvol_top5"], q=3,
                              labels=["lo", "mid", "hi"], duplicates="drop")
    lo_dates = set(feat.loc[feat["F5_bin"] == "lo", "date"])
    hi_dates = set(feat.loc[feat["F5_bin"] == "hi", "date"])
    h3_lo = run_h3_with_filter(by_date, lo_dates)
    h3_hi = run_h3_with_filter(by_date, hi_dates)
    print(f"  H3 on F5=lo:  {summary(h3_lo, 'H3_F5_lo')}")
    print(f"  H3 on F5=hi:  {summary(h3_hi, 'H3_F5_hi')}")

    # ─── B) Walk-forward ───
    print("\n=== B) Walk-forward: train cutoff on first 50%, test on second 50% ===")
    dates_sorted = sorted(feat["date"].tolist())
    n_dates = len(dates_sorted)
    train_dates = dates_sorted[:n_dates // 2]
    test_dates = dates_sorted[n_dates // 2:]
    feat_train = feat[feat["date"].isin(train_dates)]
    feat_test = feat[feat["date"].isin(test_dates)]
    # Compute bottom-tercile threshold from train, apply to test
    train_tercile_cutoff = feat_train["F5_avg_dvol_top5"].quantile(1/3)
    test_lo_dates = set(feat_test.loc[feat_test["F5_avg_dvol_top5"] <= train_tercile_cutoff, "date"])
    train_lo_dates = set(feat_train.loc[feat_train["F5_avg_dvol_top5"] <= train_tercile_cutoff, "date"])
    print(f"  Train period: {train_dates[0]} to {train_dates[-1]} ({len(train_dates)} dates)")
    print(f"  Test  period: {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} dates)")
    print(f"  Train-tercile cutoff: ${train_tercile_cutoff/1e6:.1f}M dollar volume")
    print(f"  Test dates passing filter: {len(test_lo_dates)} of {len(test_dates)}")
    train_trades = run_h3_with_filter(by_date, train_lo_dates)
    test_trades = run_h3_with_filter(by_date, test_lo_dates)
    print(f"\n  IN-SAMPLE  (train + filter applied): {summary(train_trades, 'train_lo')}")
    print(f"  OUT-OF-SAMPLE (test, train-cutoff):   {summary(test_trades, 'test_lo')}")

    # ─── C) Trade-by-trade audit ───
    print("\n=== C) F5=lo trade-by-trade ===")
    df_lo = pd.DataFrame(h3_lo)
    if not df_lo.empty:
        df_lo = df_lo.sort_values("date")
        print(df_lo.to_string(index=False))

    # ─── D) Bootstrap CI ───
    print("\n=== D) Bootstrap CI on F5=lo ===")
    if h3_lo:
        b = bootstrap([t["pnl_pct"] for t in h3_lo])
        print(f"  n={b['n']}  mean_obs={b['mean_observed']*100:+.2f}%  "
              f"95% CI [{b['p05']*100:+.2f}%, {b['p95']*100:+.2f}%]  "
              f"share_positive={b['share_positive']*100:.1f}%")

    # ─── E) Opposite regime: F5=hi -> go LONG instead? ───
    print("\n=== E) F5=hi -> run H1 long ===")
    h1_lo = run_h1_with_filter(by_date, lo_dates)
    h1_hi = run_h1_with_filter(by_date, hi_dates)
    print(f"  H1 on F5=lo:  {summary(h1_lo, 'H1_F5_lo')}")
    print(f"  H1 on F5=hi:  {summary(h1_hi, 'H1_F5_hi')}")

    # ─── F) Combined: short on F5=lo, long on F5=hi (regime-switched) ───
    print("\n=== F) Regime-switched: short on F5=lo + long on F5=hi ===")
    daily: dict[str, float] = {}
    for tr in h3_lo:
        daily[tr["date"]] = daily.get(tr["date"], 0.0) + tr["pnl_pct"]
    for tr in h1_hi:
        daily[tr["date"]] = daily.get(tr["date"], 0.0) + tr["pnl_pct"] / 3  # 3 picks per day
    # also collect mid-tercile separately (no trade)
    daily_pnls = list(daily.values())
    if daily_pnls:
        s = pd.Series(daily_pnls)
        cum = (1 + s).prod() - 1
        sharpe = (s.mean() / s.std()) * (252 ** 0.5) if s.std() > 0 else None
        print(f"  n_trade_days={len(daily_pnls)}  daily_mean={s.mean()*100:+.3f}%  "
              f"daily_std={s.std()*100:.3f}%  sharpe_annual={sharpe:.2f}  "
              f"compound={cum*100:+.2f}%")

    # ─── Persist ───
    out = {
        "h3_F5_lo": summary(h3_lo, "H3_F5_lo"),
        "h3_F5_hi": summary(h3_hi, "H3_F5_hi"),
        "walk_forward": {
            "train_cutoff_dollar_vol": float(train_tercile_cutoff),
            "train_period": [train_dates[0], train_dates[-1]],
            "test_period": [test_dates[0], test_dates[-1]],
            "in_sample_train_lo": summary(train_trades, "train_lo"),
            "out_of_sample_test_lo": summary(test_trades, "test_lo"),
        },
        "f5_lo_trades": h3_lo,
        "bootstrap_f5_lo": bootstrap([t["pnl_pct"] for t in h3_lo]) if h3_lo else None,
        "h1_F5_lo": summary(h1_lo, "H1_F5_lo"),
        "h1_F5_hi": summary(h1_hi, "H1_F5_hi"),
    }
    (OUT_DIR / "final_strategy_v1_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {OUT_DIR / 'final_strategy_v1_summary.json'}")


if __name__ == "__main__":
    main()
