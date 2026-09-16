"""Diagnose the time-split degradation in H1 (13:00 long) and H3 (short ripper).

Hypotheses:
  R1: One outlier date (CRCA) drives the first-half outperformance — drop it
      and the edge disappears.
  R2: The watchlist size changed mid-sample (the bot's filter tightened).
      Edge correlates with universe size.
  R3: True regime change — market microstructure adapted; the strategy is
      a-temporally negative now.
  R4: Look at monthly P&L profile — when did the edge die?

Outputs:
  data/audits/winners_robustness_summary.json
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest_5_hypotheses import (  # type: ignore
    load_all_dates, first_30_max_return, simulate_long_trade, simulate_short_trade,
    OUT_DIR,
)


def run_h1_trades(by_date: dict, entry=(13, 0), k=3) -> list[dict]:
    out = []
    for date_str, tickers in by_date.items():
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
                        "entry_px": entry_px, "first_30": f30,
                        "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"]})
    return out


def run_h3_trades(by_date: dict, k=1, min_pct=0.20, tgt=0.30, stp=0.15) -> list[dict]:
    out = []
    for date_str, tickers in by_date.items():
        scored = []
        for t in tickers:
            f30, end_ts, _ = first_30_max_return(t["rth"])
            if f30 is None or f30 < min_pct: continue
            scored.append((t, f30, end_ts))
        scored.sort(key=lambda x: -x[1])
        for t, f30, end_ts in scored[:k]:
            rth = t["rth"]
            entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
            if entry_bar is None: continue
            ats = next(ts for ts, b in rth if ts >= end_ts)
            entry_px = float(entry_bar["open"])
            sim = simulate_short_trade(rth, ats, entry_px, tgt, stp)
            out.append({"date": date_str, "ticker": t["ticker"],
                        "entry_px": entry_px, "first_30": f30,
                        "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"]})
    return out


def monthly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    g = df.groupby("month").agg(
        n=("pnl_pct", "size"),
        mean=("pnl_pct", "mean"),
        median=("pnl_pct", "median"),
        win_rate=("pnl_pct", lambda s: (s > 0).mean()),
        compound=("pnl_pct", lambda s: (1 + s).prod() - 1),
    ).reset_index()
    return g


def main():
    by_date = load_all_dates()

    print("\n=== R1: drop CRCA from H1 + H3 ===")
    h1 = pd.DataFrame(run_h1_trades(by_date))
    h3 = pd.DataFrame(run_h3_trades(by_date))
    h1_no_crca = h1[h1["ticker"] != "CRCA"]
    h3_no_crca = h3[h3["ticker"] != "CRCA"]
    print(f"  H1: full={h1['pnl_pct'].mean()*100:+.3f}%  no_CRCA={h1_no_crca['pnl_pct'].mean()*100:+.3f}%  "
          f"(CRCA appearances: {(h1['ticker']=='CRCA').sum()})")
    print(f"  H3: full={h3['pnl_pct'].mean()*100:+.3f}%  no_CRCA={h3_no_crca['pnl_pct'].mean()*100:+.3f}%  "
          f"(CRCA appearances: {(h3['ticker']=='CRCA').sum()})")

    print("\n=== R2: watchlist size effect ===")
    sizes = pd.DataFrame([{"date": d, "n_tickers": len(ts)} for d, ts in by_date.items()])
    h1m = h1.merge(sizes, on="date")
    h3m = h3.merge(sizes, on="date")
    # bin by quartile of universe size
    for h_name, hdf in [("H1", h1m), ("H3", h3m)]:
        if len(hdf) == 0: continue
        hdf["univ_bin"] = pd.qcut(hdf["n_tickers"], q=4, duplicates="drop", labels=False)
        g = hdf.groupby("univ_bin").agg(
            n_trades=("pnl_pct", "size"),
            avg_n_tickers=("n_tickers", "mean"),
            avg_pnl=("pnl_pct", "mean"),
            win_rate=("pnl_pct", lambda s: (s > 0).mean()),
        )
        print(f"\n  {h_name} by universe-size quartile:")
        print(g.to_string())

    print("\n=== R4: monthly P&L profile ===")
    print("\n  H1 (long 13:00, top-3) monthly:")
    print(monthly(h1).to_string(index=False))
    print("\n  H3 (short top-1, min20, tgt30/stp15) monthly:")
    print(monthly(h3).to_string(index=False))

    # H1 and H3 by date - what dates contributed most?
    print("\n=== Most profitable trade-days in H3 (short) ===")
    by_day_h3 = h3.groupby("date").agg(
        n=("pnl_pct", "size"),
        sum_pnl=("pnl_pct", "sum"),
        tickers=("ticker", lambda s: ",".join(s)),
    ).reset_index().sort_values("sum_pnl", ascending=False).head(10)
    print(by_day_h3.to_string(index=False))

    print("\n=== Worst trade-days in H3 (short) ===")
    print(h3.groupby("date").agg(
        n=("pnl_pct", "size"),
        sum_pnl=("pnl_pct", "sum"),
        tickers=("ticker", lambda s: ",".join(s)),
    ).reset_index().sort_values("sum_pnl", ascending=True).head(10).to_string(index=False))

    print("\n=== Most profitable trade-days in H1 (long late) ===")
    by_day_h1 = h1.groupby("date").agg(
        n=("pnl_pct", "size"),
        sum_pnl=("pnl_pct", "sum"),
        tickers=("ticker", lambda s: ",".join(s)),
    ).reset_index().sort_values("sum_pnl", ascending=False).head(10)
    print(by_day_h1.to_string(index=False))

    print("\n=== Worst trade-days in H1 (long late) ===")
    print(h1.groupby("date").agg(
        n=("pnl_pct", "size"),
        sum_pnl=("pnl_pct", "sum"),
        tickers=("ticker", lambda s: ",".join(s)),
    ).reset_index().sort_values("sum_pnl", ascending=True).head(10).to_string(index=False))

    out = {
        "h1_no_crca": {"n": int(len(h1_no_crca)),
                       "avg": float(h1_no_crca["pnl_pct"].mean()),
                       "compound": float((1 + h1_no_crca["pnl_pct"]).prod() - 1)},
        "h3_no_crca": {"n": int(len(h3_no_crca)),
                       "avg": float(h3_no_crca["pnl_pct"].mean()),
                       "compound": float((1 + h3_no_crca["pnl_pct"]).prod() - 1)},
        "h1_monthly": monthly(h1).to_dict(orient="records"),
        "h3_monthly": monthly(h3).to_dict(orient="records"),
    }
    (OUT_DIR / "winners_robustness_summary.json").write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
