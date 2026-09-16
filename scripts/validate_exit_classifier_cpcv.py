"""Combinatorial Purged Cross-Validation (CPCV) of the exit-continuation classifier (doc 231).

WHY: the doc-231 deep research found the single most transferable lesson in the deep-RL/quant
literature is Bailey/Lopez de Prado overfitting detection — "a single validation set can easily
result in model overfitting" (Gort et al., AAAI 2023: single-split walk-forward scored an agent
p_WF=17.5% REJECTED while purged K-fold scored p_KCV=7.9% ACCEPTED). My earlier 0.76 walk-forward
AUC used essentially ONE split arrangement -> it could be a false positive. CPCV re-tests the edge
across MANY train/test arrangements with purging, giving an honest distribution, not one lucky number.

DESIGN (day-grouped, leakage-safe):
  - Each session_date -> one of N contiguous time GROUPS (a whole ticker-day is never split across
    train/test, and the 30-min intraday label horizon never crosses a day -> no horizon leakage).
  - Combinatorial: test on EVERY combination of k groups; train on the rest, with a 1-day EMBARGO
    at test boundaries (Lopez de Prado). C(N,k) paths -> C(6,2)=15 honest OOS AUC estimates.
  - A robust edge = high MEAN OOS AUC with LOW variance and a floor well above 0.5 across all paths.

Usage: python scripts/validate_exit_classifier_cpcv.py --groups 6 --k 2
"""
from __future__ import annotations

import argparse
import itertools

import duckdb
import numpy as np

PARQUET = "data/research/exit_labels.parquet"
FEATS = ["minute_idx", "ret_session", "ret_5m", "ret_15m", "ret_30m", "vwap_dist", "high_dist",
         "low_dist", "range_pos", "rvol_cum", "vol_accel_5m", "realized_vol_15m",
         "up_min_frac_15m", "gap_pct", "rvol_entry", "mfcs"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", type=int, default=6)
    ap.add_argument("--k", type=int, default=2, help="test groups per combination")
    a = ap.parse_args()

    from sklearn.ensemble import HistGradientBoostingClassifier as GBM
    from sklearn.metrics import roc_auc_score

    df = duckdb.connect().execute(f"SELECT * FROM read_parquet('{PARQUET}')").df()
    df = df.dropna(subset=["continued"]).copy()
    for c in FEATS:
        df[c] = df[c].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)

    days = sorted(df["session_date"].unique())
    day_idx = {d: i for i, d in enumerate(days)}
    groups = [list(g) for g in np.array_split(days, a.groups)]
    grp_of_day = {d: gi for gi, g in enumerate(groups) for d in g}
    print(f"{len(df):,} rows | {len(days)} sessions -> {a.groups} groups | "
          f"C({a.groups},{a.k})={len(list(itertools.combinations(range(a.groups), a.k)))} CPCV paths")

    aucs, lifts, mins_floor = [], [], []
    for combo in itertools.combinations(range(a.groups), a.k):
        test_days = set(d for gi in combo for d in groups[gi])
        test_pos = sorted(day_idx[d] for d in test_days)
        # 1-day embargo around each contiguous test block
        embargo = set()
        for p in test_pos:
            embargo.add(p - 1); embargo.add(p + 1)
        train_days = [d for d in days if d not in test_days and day_idx[d] not in embargo]
        tr = df[df["session_date"].isin(train_days)]
        te = df[df["session_date"].isin(test_days)]
        if len(te) < 300 or te["continued"].nunique() < 2 or len(tr) < 1000:
            continue
        m = GBM(max_iter=250, learning_rate=0.05, max_depth=4, l2_regularization=1.0)
        m.fit(tr[FEATS], tr["continued"])
        p = m.predict_proba(te[FEATS])[:, 1]
        auc = roc_auc_score(te["continued"], p)
        y = te["continued"].to_numpy()
        top = y[p >= np.quantile(p, 0.9)]
        lift = (top.mean() / y.mean()) if y.mean() > 0 and len(top) else float("nan")
        aucs.append(auc); lifts.append(lift); mins_floor.append(auc)

    aucs = np.array(aucs)
    print(f"\n=== CPCV RESULT ({len(aucs)} purged combinatorial OOS paths) ===")
    print(f"  AUC  mean {aucs.mean():.3f}  std {aucs.std():.3f}  min {aucs.min():.3f}  max {aucs.max():.3f}")
    print(f"  paths with AUC>0.60: {(aucs>0.60).mean()*100:.0f}%   >0.65: {(aucs>0.65).mean()*100:.0f}%")
    lifts = np.array([x for x in lifts if x == x])
    if len(lifts):
        print(f"  top-decile P(continue) lift  mean {lifts.mean():.2f}x  min {lifts.min():.2f}x")
    verdict = ("ROBUST — edge survives combinatorial purged CV (not a single-split artifact)"
               if aucs.mean() > 0.65 and aucs.min() > 0.58
               else "FRAGILE — wide variance / low floor; treat the single-split 0.76 as optimistic")
    print(f"\n  VERDICT: {verdict}")


if __name__ == "__main__":
    main()
