"""Realism pass (doc 233): a DEPLOYABLE conviction-sizing + hold-to-EOD portfolio backtest.

doc 232 found P(continue) is a strong CONVICTION signal (high-early-p names returned +14.7% mean
hold-to-EOD vs +4.7% low; beats MFCS) — but frictionless. This adds the realism that turns an
idealized number into a deployable one, and answers: does P(continue)-selection beat MFCS-selection
NET of costs and capital limits?

REALISM LAYERS:
  - SLIPPAGE/SPREAD: price-bucketed round-trip cost (pennies bleed; large caps cheap), default model
    + a flat-cost SWEEP for sensitivity. Small-caps are expensive to round-trip — this is the haircut.
  - CAPITAL CAP: each session pick top-N by the ranking signal, max EXEC_MAX_POSITIONS=8, each <=15%
    (EXEC_MAX_POSITION_PCT), conviction-weighted, undeployed remainder = cash.
  - LIQUIDITY proxy: price floor (drop the sub-$1 names you can't fill at size); swept.
  - NO LOOK-AHEAD: early_p uses the first 3 decision points (9:35-9:45); you therefore ENTER AFTER
    that window (decision point #3, ~9:50) — hold_ret is measured entry#3 -> EOD, never from 9:35.
  - NO LEAKAGE: P(continue) is an out-of-fold prediction (purged K-fold by day-group).

Usage: python scripts/backtest_conviction_portfolio.py --maxpos 8 --maxw 0.15 --floor 1.0
"""
from __future__ import annotations

import argparse
import duckdb
import numpy as np
import pandas as pd

LABELS = "data/research/exit_labels.parquet"
MULTIDAY = "data/research/multiday_hold_dataset.parquet"   # for base_close (price level -> cost bucket)
FEATS = ["minute_idx", "ret_session", "ret_5m", "ret_15m", "ret_30m", "vwap_dist", "high_dist",
         "low_dist", "range_pos", "rvol_cum", "vol_accel_5m", "realized_vol_15m",
         "up_min_frac_15m", "gap_pct", "rvol_entry", "mfcs"]
ENTRY_IDX = 3   # enter at the 3rd decision point (~9:50), AFTER the early_p window (no look-ahead)


def round_trip_cost(px):
    """Realistic round-trip (spread + slippage) for small-caps, by price bucket."""
    if px < 1:   return 0.040
    if px < 3:   return 0.025
    if px < 10:  return 0.015
    if px < 30:  return 0.008
    return 0.004


def build_daytable():
    from sklearn.ensemble import HistGradientBoostingClassifier as GBM
    df = duckdb.connect().execute(f"SELECT * FROM read_parquet('{LABELS}')").df()
    df = df.dropna(subset=["continued"]).copy()
    for c in FEATS:
        df[c] = df[c].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)
    # out-of-fold P(continue)
    days = sorted(df["session_date"].unique())
    grp = {d: gi for gi, g in enumerate(np.array_split(days, 6)) for d in g}
    df["_g"] = df["session_date"].map(grp); df["p"] = np.nan
    for gi in range(6):
        tr = df[df["_g"] != gi]; te = df.index[df["_g"] == gi]
        if len(te) and tr["continued"].nunique() > 1:
            m = GBM(max_iter=250, learning_rate=0.05, max_depth=4, l2_regularization=1.0)
            m.fit(tr[FEATS], tr["continued"])
            df.loc[te, "p"] = m.predict_proba(df.loc[te, FEATS])[:, 1]
    df = df.dropna(subset=["p"])
    # per ticker-day: early_p (first 3 pts), entry AFTER that, hold-to-EOD from the entry bar
    rows = []
    for (tk, d), g in df.groupby(["ticker", "session_date"]):
        g = g.sort_values("minute_idx")
        rel = 1.0 + g["ret_session"].to_numpy()
        p = g["p"].to_numpy()
        if len(rel) <= ENTRY_IDX + 1 or rel[ENTRY_IDX] <= 0:
            continue
        early_p = float(p[:3].mean())
        hold_ret = rel[-1] / rel[ENTRY_IDX] - 1.0   # enter#3 -> EOD (no look-ahead)
        rows.append({"ticker": tk, "session_date": d, "early_p": early_p,
                     "hold_ret": hold_ret, "mfcs": float(g["mfcs"].iloc[0] or 0)})
    dt = pd.DataFrame(rows)
    # price level for the cost bucket
    md = duckdb.connect().execute(
        f"SELECT ticker, session_date, base_close FROM read_parquet('{MULTIDAY}')").df()
    dt = dt.merge(md, on=["ticker", "session_date"], how="left")
    dt["base_close"] = dt["base_close"].fillna(5.0)   # neutral mid-bucket if unmatched
    return dt


def run(dt, rank_col, maxpos, maxw, floor, flat_cost=None, min_p=None):
    daily = []
    for d, g in dt.groupby("session_date"):
        g = g[g["base_close"] >= floor]
        if min_p is not None:
            g = g[g["early_p"] >= min_p]
        if len(g) == 0:
            daily.append(0.0); continue
        g = g.sort_values(rank_col, ascending=False).head(maxpos)
        w = g[rank_col].clip(lower=1e-6).to_numpy()
        w = w / w.sum()
        w = np.minimum(w, maxw)              # per-position cap; remainder stays cash
        cost = (np.full(len(g), flat_cost) if flat_cost is not None
                else g["base_close"].map(round_trip_cost).to_numpy())
        net = g["hold_ret"].to_numpy() - cost
        daily.append(float((w * net).sum()))
    return np.array(daily)


def report(name, daily):
    if len(daily) == 0:
        print(f"  {name}: no days"); return
    curve = np.cumprod(1 + daily); peak = np.maximum.accumulate(curve)
    dd = (curve / peak - 1).min()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    print(f"  {name:<34} totalRet {(*[curve[-1]-1],)[0]*100:>+7.1f}%  meanDay {daily.mean()*100:>+5.2f}%  "
          f"Sharpe {sharpe:>5.2f}  winDay {(daily>0).mean()*100:>3.0f}%  maxDD {dd*100:>5.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maxpos", type=int, default=8)
    ap.add_argument("--maxw", type=float, default=0.15)
    ap.add_argument("--floor", type=float, default=1.0)
    a = ap.parse_args()

    dt = build_daytable()
    print(f"ticker-days: {len(dt)} | sessions: {dt['session_date'].nunique()} | "
          f"maxpos={a.maxpos} maxw={a.maxw:.0%} price-floor=${a.floor}\n")

    print("=== SELECTION SIGNAL: P(continue) vs MFCS vs all (bucketed realistic cost) ===")
    report("P(continue)-ranked top-8", run(dt, "early_p", a.maxpos, a.maxw, a.floor))
    report("MFCS-ranked top-8 (current)", run(dt, "mfcs", a.maxpos, a.maxw, a.floor))
    # 'all' = equal-conviction over a random-ish top-8 (use a constant rank -> first 8 alphabetical/day)
    dt["_one"] = 1.0
    report("no-signal top-8 (baseline)", run(dt, "_one", a.maxpos, a.maxw, a.floor))

    print("\n=== COST SENSITIVITY: P(continue)-ranked top-8, flat round-trip cost ===")
    for c in [0.005, 0.010, 0.020, 0.030]:
        report(f"flat cost {c*100:.1f}%", run(dt, "early_p", a.maxpos, a.maxw, a.floor, flat_cost=c))

    print("\n=== LIQUIDITY (price floor) SENSITIVITY: P(continue)-ranked, bucketed cost ===")
    for fl in [0.0, 1.0, 2.0, 5.0]:
        report(f"price floor ${fl:.0f}", run(dt, "early_p", a.maxpos, a.maxw, fl))

    print("\n=== ABSTENTION: only enter when early_p >= threshold (top-8, bucketed cost, floor $1) ===")
    for mp in [None, 0.20, 0.25, 0.30]:
        report(f"min early_p {mp}", run(dt, "early_p", a.maxpos, a.maxw, 1.0, min_p=mp))


if __name__ == "__main__":
    main()
