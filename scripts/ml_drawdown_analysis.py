"""Drawdown + risk decomposition of meta-scorer WF bankroll P&L.

The session 109/111/112 headline numbers (+54.76% / +65.43% / +122.83% bankroll)
are CUMULATIVE over a 16-month walk-forward. They tell us nothing about:
  - Maximum drawdown (worst peak-to-trough loss)
  - Calmar ratio (annual return / max DD)
  - Daily P&L distribution (mean, std, skew, kurtosis)
  - Single-pick worst-case (left-tail of the per-pick distribution)
  - Tier-conditional drawdowns (which tiers caused the worst sequences)

This matters BEFORE Monday paper deployment because aggressive Kelly
(50/35/20/10 caps) with single-pick worst-case ELITE = -50% of bankroll
implies that one bad ELITE pick on a -100% return wipes a paper-month.
The user needs to see the distribution, not just the mean.

Inputs:
  data/polygon_warehouse/derived/meta_scores_walkforward*.parquet

Outputs:
  data/polygon_warehouse/derived/drawdown_analysis.json
  Console: drawdown table + per-tier risk decomposition + percentile P&Ls
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def compute_drawdown(equity: pd.Series) -> dict:
    """Compute max drawdown + recovery time on an equity curve."""
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    max_dd = float(drawdown.min())
    max_dd_end = drawdown.idxmin()
    # Recovery: first index after max_dd_end where equity >= cummax at that point
    peak_value = cummax.loc[max_dd_end]
    after = equity.loc[max_dd_end:]
    recovered = after[after >= peak_value]
    recovery_idx = recovered.index[0] if len(recovered) > 0 else None

    return {
        "max_drawdown_pct": float(max_dd * 100),
        "max_dd_end": str(max_dd_end),
        "peak_at_max_dd": float(peak_value),
        "trough_at_max_dd": float(equity.loc[max_dd_end]),
        "recovered_at": str(recovery_idx) if recovery_idx else "not yet",
        "recovery_days": int((recovery_idx - max_dd_end).days) if recovery_idx else None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--meta-scores", type=str,
                   default="data/polygon_warehouse/derived/meta_scores_walkforward_16fold_aggressive.parquet",
                   help="Per-row meta_scores parquet path")
    p.add_argument("--bankroll", type=float, default=10_000.0)
    args = p.parse_args()

    section(f"Drawdown analysis: {Path(args.meta_scores).name}  bankroll=${args.bankroll:,.0f}")
    df = pd.read_parquet(args.meta_scores)
    df["d0"] = pd.to_datetime(df["d0"])
    df = df.sort_values(["d0", "ticker"]).reset_index(drop=True)
    print(f"  loaded {len(df):,} rows")
    print(f"  date range: {df['d0'].min()} -> {df['d0'].max()}")
    n_picks = (df["kelly_frac"] > 0).sum()
    print(f"  picks (kelly > 0): {n_picks:,}")

    # ── Per-pick P&L distribution ──
    section("Per-pick P&L distribution (kelly>0 picks only)")
    picks = df[df["kelly_frac"] > 0].copy()
    pnl = picks["pnl_dollars"]
    print(f"  {'metric':<25} {'$':>12}  {'% bankroll':>12}")
    for label, val in [
        ("mean", pnl.mean()),
        ("median", pnl.median()),
        ("std", pnl.std()),
        ("min (worst pick)", pnl.min()),
        ("p1 (1st percentile)", pnl.quantile(0.01)),
        ("p5", pnl.quantile(0.05)),
        ("p25", pnl.quantile(0.25)),
        ("p75", pnl.quantile(0.75)),
        ("p95", pnl.quantile(0.95)),
        ("p99", pnl.quantile(0.99)),
        ("max (best pick)", pnl.max()),
    ]:
        print(f"  {label:<25} ${val:>+11,.2f}  {val/args.bankroll*100:>+11.2f}%")
    print(f"  skew:                     {pnl.skew():>+.3f}")
    print(f"  kurtosis:                 {pnl.kurtosis():>+.3f}")
    print(f"  P&L Sharpe (per pick):    {pnl.mean() / pnl.std() if pnl.std() > 0 else 0:>+.3f}")

    # ── Per-tier risk decomposition ──
    section("Per-tier risk decomposition")
    print(f"  {'tier':<8} {'n':>4} {'mean_$':>9} {'std_$':>9} "
          f"{'min_$':>9} {'max_$':>9} {'% loss':>8}")
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        g = picks[picks["meta_tier"] == tier]
        if len(g) == 0: continue
        loss_rate = (g["pnl_dollars"] < 0).mean() * 100
        print(f"  {tier:<8} {len(g):>4} ${g['pnl_dollars'].mean():>+8.2f} "
              f"${g['pnl_dollars'].std():>+8.2f} ${g['pnl_dollars'].min():>+8.2f} "
              f"${g['pnl_dollars'].max():>+8.2f} {loss_rate:>7.1f}%")

    # ── Daily P&L (group picks by date) ──
    section("Daily P&L distribution")
    daily = picks.groupby("d0")["pnl_dollars"].sum()
    print(f"  trading days: {len(daily)}")
    print(f"  {'metric':<25} {'$':>12}  {'% bankroll':>12}")
    for label, val in [
        ("mean daily $", daily.mean()),
        ("median daily $", daily.median()),
        ("std daily $", daily.std()),
        ("worst day $", daily.min()),
        ("best day $", daily.max()),
        ("p5 daily $", daily.quantile(0.05)),
        ("p95 daily $", daily.quantile(0.95)),
    ]:
        print(f"  {label:<25} ${val:>+11,.2f}  {val/args.bankroll*100:>+11.2f}%")
    n_neg = (daily < 0).sum()
    n_pos = (daily > 0).sum()
    print(f"  negative days: {n_neg}/{len(daily)} ({n_neg/len(daily)*100:.1f}%)")
    print(f"  positive days: {n_pos}/{len(daily)} ({n_pos/len(daily)*100:.1f}%)")

    # ── Equity curve + drawdown ──
    section("Equity curve drawdown")
    # Cumulative bankroll: start = bankroll, add daily $pnl
    daily_full = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(),
                                              freq="D"), fill_value=0)
    equity = args.bankroll + daily_full.cumsum()
    dd = compute_drawdown(equity)
    print(f"  starting bankroll:     ${args.bankroll:,.2f}")
    print(f"  ending bankroll:       ${equity.iloc[-1]:,.2f}")
    print(f"  net P&L:               ${equity.iloc[-1] - args.bankroll:+,.2f} "
          f"({(equity.iloc[-1] - args.bankroll)/args.bankroll*100:+.2f}%)")
    print(f"  peak bankroll:         ${equity.cummax().iloc[-1]:,.2f}")
    print(f"  MAX DRAWDOWN:          {dd['max_drawdown_pct']:.2f}%  "
          f"(peak ${dd['peak_at_max_dd']:,.0f} -> trough ${dd['trough_at_max_dd']:,.0f})")
    print(f"  max DD reached on:     {dd['max_dd_end']}")
    if dd["recovery_days"]:
        print(f"  recovered after:       {dd['recovery_days']} days "
              f"(at {dd['recovered_at']})")
    else:
        print(f"  recovered after:       NOT YET — still under peak as of {daily.index.max()}")

    # ── Calmar + return-to-vol metrics ──
    section("Risk-adjusted return metrics (annualized)")
    n_days = len(daily_full)
    years = n_days / 365.25
    total_return = (equity.iloc[-1] / args.bankroll) - 1
    cagr = (equity.iloc[-1] / args.bankroll) ** (1 / years) - 1 if years > 0 else 0
    daily_returns = daily_full / args.bankroll
    sharpe_daily = daily_returns.mean() / daily_returns.std() if daily_returns.std() > 0 else 0
    sharpe_annual = sharpe_daily * np.sqrt(252)
    calmar = abs(cagr / (dd["max_drawdown_pct"] / 100)) if dd["max_drawdown_pct"] != 0 else 0
    print(f"  total return:          {total_return*100:+.2f}%  over {years:.2f} years")
    print(f"  CAGR:                  {cagr*100:+.2f}%  (annualized compound)")
    print(f"  daily Sharpe:          {sharpe_daily:+.3f}")
    print(f"  ANNUALIZED SHARPE:     {sharpe_annual:+.3f}")
    print(f"  CALMAR RATIO:          {calmar:.2f}  (CAGR / |max_DD|; >1 = excellent)")
    if calmar > 3.0:
        print(f"  *** Calmar > 3 = institutional-grade risk-adjusted returns")
    elif calmar > 1.0:
        print(f"  + Calmar > 1 = healthy risk-adjusted")
    else:
        print(f"  - Calmar < 1 = drawdown exceeds annual gain - RISKY")

    # ── Worst-case scenarios ──
    section("Worst-case scenarios (red-team for Monday)")
    worst_pick = picks.nsmallest(5, "pnl_dollars")[["d0", "ticker", "meta_tier",
                                                       "kelly_frac", "y_reg",
                                                       "pnl_dollars"]]
    print(f"  Worst 5 individual picks:")
    print(worst_pick.to_string(index=False))
    print()
    worst_day = daily.nsmallest(5)
    print(f"  Worst 5 trading days (sum across picks that day):")
    for d, p in worst_day.items():
        print(f"    {d.date()}  ${p:+,.2f}  ({p/args.bankroll*100:+.2f}% of bankroll)")

    # ── Persist ──
    out = {
        "meta_scores_path": args.meta_scores,
        "bankroll_start": args.bankroll,
        "bankroll_end": float(equity.iloc[-1]),
        "total_return_pct": float(total_return * 100),
        "cagr_pct": float(cagr * 100),
        "annualized_sharpe": float(sharpe_annual),
        "calmar_ratio": float(calmar),
        "max_drawdown_pct": float(dd["max_drawdown_pct"]),
        "n_picks": int(n_picks),
        "n_trading_days": int(len(daily)),
        "negative_days_pct": float(n_neg / len(daily) * 100),
        "per_pick_p1_dollars": float(pnl.quantile(0.01)),
        "per_pick_p99_dollars": float(pnl.quantile(0.99)),
        "worst_day_dollars": float(daily.min()),
        "best_day_dollars": float(daily.max()),
        "drawdown_recovery": dd,
    }
    out_path = MODELS / f"drawdown_{Path(args.meta_scores).stem}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
