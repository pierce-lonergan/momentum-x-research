"""Validate the Bessembinder-Lottery finding from R1.

R1 result: 10% trailing stop, equal-weight all tickers, daily compound = +15.5%.

Critical questions:
  V1: Walk-forward (train cutoff doesn't matter here — strategy has no params).
      Just compare first-half P&L to second-half P&L. If second half is also
      positive, we have a real edge.
  V2: Day-by-day P&L distribution. Worst day, max consecutive losing streak,
      max drawdown.
  V3: Sensitivity to trail width (already in R1 — 20%/30%/50% lose money).
      What about 5%, 7%, 12%, 15%? Find the sharpest cliff.
  V4: Freshness tilt — if we ONLY trade first-appearance tickers, does the
      strategy improve? Per R2, first-appearance has higher >=100% rate.
  V5: Realistic transaction cost model. At 1c/share commission + 5bp slippage,
      what's the break-even per-trade size?
  V6: Per-ticker contribution — is the +15.5% driven by 1-2 monster trades,
      or is it broadly distributed?
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest_5_hypotheses import load_all_dates, OUT_DIR  # type: ignore


def trail_strategy(rth_bars: list, trail_pct: float, entry_px: float | None = None) -> dict:
    """Buy at RTH open (or `entry_px`), trail at trail_pct from running high.
    Returns: {entry_px, exit_px, exit_reason, pnl_pct, running_high, hold_min}."""
    if not rth_bars: return None
    if entry_px is None:
        entry_px = float(rth_bars[0][1]["open"])
    if entry_px <= 0: return None
    running_high = entry_px
    exit_px = entry_px
    exit_reason = "eod"
    last_close = entry_px
    open_ts = rth_bars[0][0]
    last_ts = open_ts
    for ts, b in rth_bars:
        hi = float(b["high"]); lo = float(b["low"]); cl = float(b["close"])
        running_high = max(running_high, hi)
        trail_stop = running_high * (1 - trail_pct)
        if lo <= trail_stop:
            exit_px = trail_stop
            exit_reason = "trail"
            last_ts = ts
            break
        last_close = cl
        last_ts = ts
        exit_px = cl
    hold_min = int((last_ts - open_ts).total_seconds() // 60)
    return {"entry_px": entry_px, "exit_px": exit_px, "exit_reason": exit_reason,
            "pnl_pct": (exit_px - entry_px) / entry_px, "running_high": running_high,
            "hold_min": hold_min}


def run_lottery(by_date: dict, trail_pct: float, first_appearance_only: bool = False) -> pd.DataFrame:
    seen: set = set()
    rows = []
    for date_str in sorted(by_date):
        for t in by_date[date_str]:
            tk = t["ticker"]
            is_first = tk not in seen
            seen.add(tk)
            if first_appearance_only and not is_first: continue
            res = trail_strategy(t["rth"], trail_pct)
            if res is None: continue
            rows.append({
                "date": date_str, "ticker": tk,
                "is_first": is_first, **res,
            })
    return pd.DataFrame(rows)


def daily_returns(df: pd.DataFrame) -> pd.Series:
    """Equal-weight daily portfolio: per-date mean of trade returns."""
    return df.groupby("date")["pnl_pct"].mean().sort_index()


def compound(s: pd.Series) -> float:
    return float((1 + s).prod() - 1)


def max_drawdown(s: pd.Series) -> float:
    """Max peak-to-trough drawdown of the equity curve."""
    if s.empty: return 0.0
    eq = (1 + s).cumprod()
    rolling_peak = eq.cummax()
    dd = eq / rolling_peak - 1
    return float(dd.min())


def realistic_costs(notional_per_trade: float, commission_per_share: float = 0.005,
                     min_per_trade: float = 0.35, slippage_bps: float = 5) -> float:
    """Cost as fraction of notional. Assume avg ~$5/share micro-cap.
    notional = shares × price → shares = notional / price. We assume $5 avg price."""
    avg_price = 5.0
    shares = max(1, int(notional_per_trade / avg_price))
    commission = max(min_per_trade, commission_per_share * shares)
    slippage = notional_per_trade * (slippage_bps / 10000)
    return (commission + slippage) / notional_per_trade


def main():
    by_date = load_all_dates()

    # ─── V1: walk-forward / time-split ───────────────────────────
    print("=== V1: walk-forward + sensitivity to trail width ===")
    print(f"{'trail':>6s}  {'n_full':>6s}  {'avg_full':>9s}  {'cmp_full':>9s}  "
          f"{'cmp_h1':>8s}  {'cmp_h2':>8s}  {'mdd_full':>9s}  {'sharpe':>7s}")
    sweep_results = {}
    for trail in [0.05, 0.07, 0.10, 0.12, 0.15, 0.20]:
        df = run_lottery(by_date, trail)
        dr = daily_returns(df)
        # halves
        cutoff_idx = len(dr) // 2
        h1 = dr.iloc[:cutoff_idx]
        h2 = dr.iloc[cutoff_idx:]
        c_full = compound(dr); c_h1 = compound(h1); c_h2 = compound(h2)
        mdd = max_drawdown(dr)
        sharpe = float(dr.mean() / dr.std() * (252 ** 0.5)) if dr.std() > 0 else 0
        print(f"  {trail*100:>4.0f}%  {len(df):>6d}  "
              f"{df['pnl_pct'].mean()*100:>+7.2f}%  {c_full*100:>+7.2f}%  "
              f"{c_h1*100:>+6.2f}%  {c_h2*100:>+6.2f}%  "
              f"{mdd*100:>+7.2f}%  {sharpe:>6.2f}")
        sweep_results[f"trail_{int(trail*100)}"] = {
            "n_trades": len(df),
            "avg_per_trade": float(df["pnl_pct"].mean()),
            "compound_full": c_full, "compound_h1": c_h1, "compound_h2": c_h2,
            "max_drawdown": mdd, "sharpe_annualized": sharpe,
            "win_rate": float((df["pnl_pct"] > 0).mean()),
            "n_winners_gte_50pct": int((df["pnl_pct"] >= 0.50).sum()),
            "n_winners_gte_100pct": int((df["pnl_pct"] >= 1.00).sum()),
        }

    # ─── V4: freshness tilt — first-appearance only ─────────────
    print("\n=== V4: freshness tilt (first-appearance tickers only) ===")
    print(f"{'trail':>6s}  {'n_first':>7s}  {'avg':>7s}  {'cmp_full':>9s}  "
          f"{'cmp_h1':>8s}  {'cmp_h2':>8s}  {'mdd':>7s}  {'sharpe':>7s}")
    fresh_results = {}
    for trail in [0.07, 0.10, 0.15]:
        df = run_lottery(by_date, trail, first_appearance_only=True)
        dr = daily_returns(df)
        cutoff_idx = len(dr) // 2
        h1 = dr.iloc[:cutoff_idx]; h2 = dr.iloc[cutoff_idx:]
        c_full = compound(dr); c_h1 = compound(h1); c_h2 = compound(h2)
        mdd = max_drawdown(dr)
        sharpe = float(dr.mean() / dr.std() * (252 ** 0.5)) if dr.std() > 0 else 0
        print(f"  {trail*100:>4.0f}%  {len(df):>7d}  {df['pnl_pct'].mean()*100:>+5.2f}%  "
              f"{c_full*100:>+7.2f}%  {c_h1*100:>+6.2f}%  {c_h2*100:>+6.2f}%  "
              f"{mdd*100:>+5.2f}%  {sharpe:>6.2f}")
        fresh_results[f"trail_{int(trail*100)}"] = {
            "n_trades": len(df),
            "compound_full": c_full, "compound_h1": c_h1, "compound_h2": c_h2,
            "max_drawdown": mdd, "sharpe_annualized": sharpe,
            "win_rate": float((df["pnl_pct"] > 0).mean()),
        }

    # ─── V2: day-by-day analysis on best config ─────────────────
    best_cfg = max(sweep_results.items(), key=lambda kv: kv[1]["compound_full"])
    print(f"\n=== V2: day-by-day on best config ({best_cfg[0]}) ===")
    best_trail = int(best_cfg[0].split("_")[1]) / 100
    df_best = run_lottery(by_date, best_trail)
    dr_best = daily_returns(df_best)
    print(f"  Best day:  {dr_best.idxmax()} = {dr_best.max()*100:+.2f}%")
    print(f"  Worst day: {dr_best.idxmin()} = {dr_best.min()*100:+.2f}%")
    losing_streaks = []
    cur = 0
    for d, v in dr_best.items():
        if v < 0: cur += 1
        else:
            if cur > 0: losing_streaks.append(cur)
            cur = 0
    if cur > 0: losing_streaks.append(cur)
    print(f"  Max losing streak: {max(losing_streaks) if losing_streaks else 0} days")
    print(f"  Win days: {(dr_best > 0).sum()} of {len(dr_best)} ({(dr_best > 0).mean()*100:.1f}%)")
    print(f"  Avg daily: {dr_best.mean()*100:+.3f}%  std: {dr_best.std()*100:.3f}%")

    # ─── V5: realistic transaction cost model ───────────────────
    print(f"\n=== V5: transaction cost sensitivity (best config = {best_cfg[0]}) ===")
    print(f"  per-trade gross avg = {df_best['pnl_pct'].mean()*100:+.3f}%")
    for notional in [100, 500, 1000, 5000, 10000]:
        cost_pct = realistic_costs(notional)
        net = df_best["pnl_pct"].mean() - cost_pct
        # net daily compound
        df_best_net = df_best.copy()
        df_best_net["pnl_pct_net"] = df_best_net["pnl_pct"] - cost_pct
        dr_net = df_best_net.groupby("date")["pnl_pct_net"].mean().sort_index()
        c_net = compound(dr_net)
        print(f"  $/trade={notional:>5d}: cost={cost_pct*100:.3f}%  "
              f"net_avg={net*100:+.3f}%  compound_net={c_net*100:+.2f}%")

    # ─── V6: per-ticker contribution ────────────────────────────
    print(f"\n=== V6: per-ticker contribution (top 10 winners, top 10 losers) ===")
    by_ticker = df_best.groupby("ticker")["pnl_pct"].sum().sort_values(ascending=False)
    print("\n  TOP 10 contributors (sum of trade-pnl_pct, NOT dollars):")
    print(by_ticker.head(10).to_string())
    print("\n  BOTTOM 10:")
    print(by_ticker.tail(10).to_string())
    # Concentration: what share of total comes from top-N?
    total = by_ticker.sum()
    print(f"\n  Total summed pnl_pct (across all trades): {total*100:+.2f}%")
    for n in [5, 10, 20, 50]:
        top_n_share = by_ticker.head(n).sum() / total if total > 0 else None
        print(f"    Top {n:2d} tickers contribute {by_ticker.head(n).sum()*100:+.2f}% "
              f"of total {top_n_share*100 if top_n_share else 0:.1f}%")

    # Persist
    out = {
        "trail_sweep": sweep_results,
        "first_appearance_sweep": fresh_results,
        "best_config": best_cfg[0],
        "best_config_stats": best_cfg[1],
    }
    (OUT_DIR / "lottery_validation_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {OUT_DIR / 'lottery_validation_summary.json'}")


if __name__ == "__main__":
    main()
