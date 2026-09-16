"""Experiment 1 of doc 162 — cascade alpha validation on freshest data.

Pre-commits locked in doc 162 §1 (E1):
  PASS:     XGB Spearman >= +0.18 AND mean ret_t5 across all picks >= -1%
  MARGINAL: in [+0.10, +0.18) OR mean ret_t5 in [-3%, -1%)
  FAIL:     XGB Spearman < +0.10 OR mean ret_t5 < -3%
            -> URGENT re-enable D277 HALT before market open

Compares two windows on the now-fresh aftermath_strat:
  - last 30 days (freshest, ~600 rows after ret_t5 filter)
  - last 60 days (~1200 rows)
vs doc 147's NEWER baseline (Oct 2025 - Apr 2026, n=1797, XGB +0.230).
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def main() -> int:
    section("STEP 1 -- load v3 panel + features (fresh through 2026-05-11)")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  loaded {len(df):,} rows, max d0 = {d0.max().date()}, X cols = {X.shape[1]}")

    section("STEP 2 -- run v3 XGBoost CPU baseline on rolling WF")
    import xgboost as xgb
    PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1, "random_state": 42,
    }

    # 12-fold WF same as doc 147/152
    n_folds = 12
    train_days = 120
    test_days = 15
    fold_starts = pd.date_range(
        d0.min() + pd.Timedelta(days=train_days),
        d0.max() - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  WF: {n_folds} folds, train={train_days}d, test={test_days}d")
    print(f"  fold starts: {[fs.date() for fs in fold_starts[-4:]]} (last 4 = 'recent')")

    rows = []
    for fi, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5:
            continue
        m = xgb.XGBRegressor(**PARAMS)
        m.fit(X.loc[tr], y.loc[tr], verbose=False)
        pred = m.predict(X.loc[te])
        rows.append(pd.DataFrame({
            "fold": fi, "d0": d0.loc[te].values,
            "y_true": y.loc[te].values, "y_pred": pred,
        }))
    preds = pd.concat(rows, ignore_index=True)
    preds["d0"] = pd.to_datetime(preds["d0"])
    print(f"  total OOS preds: {len(preds):,}")

    section("STEP 3 -- recent-subset metrics (last 30/60 days)")
    today = d0.max()
    last_30 = preds[preds["d0"] >= today - pd.Timedelta(days=30)]
    last_60 = preds[preds["d0"] >= today - pd.Timedelta(days=60)]
    last_120 = preds[preds["d0"] >= today - pd.Timedelta(days=120)]

    def report(window_name: str, sub: pd.DataFrame) -> dict:
        if len(sub) < 20:
            print(f"  {window_name}: too few rows ({len(sub)}), skipping")
            return {}
        rho = float(spearmanr(sub["y_true"], sub["y_pred"]).statistic)
        mean_ret = float(sub["y_true"].mean())
        median_ret = float(sub["y_true"].median())
        # Top-decile mean ret (bot trades the top of distribution)
        top_decile = sub.nlargest(max(1, len(sub) // 10), "y_pred")["y_true"]
        top_mean = float(top_decile.mean()) if len(top_decile) else 0
        print(
            f"  {window_name}: n={len(sub):>4} | "
            f"Spearman={rho:+.4f} | "
            f"mean_ret={mean_ret*100:+.2f}% | "
            f"median_ret={median_ret*100:+.2f}% | "
            f"top-decile_mean={top_mean*100:+.2f}%"
        )
        return {
            "n": int(len(sub)),
            "spearman": rho,
            "mean_ret": mean_ret,
            "median_ret": median_ret,
            "top_decile_mean_ret": top_mean,
        }

    out_30 = report("Last 30 days", last_30)
    out_60 = report("Last 60 days", last_60)
    out_120 = report("Last 120 days", last_120)

    section("STEP 4 -- per-fold breakdown (last 4 folds = production-relevant)")
    for fi in sorted(preds["fold"].unique())[-4:]:
        sub = preds[preds["fold"] == fi]
        rho = float(spearmanr(sub["y_true"], sub["y_pred"]).statistic)
        mean_ret = float(sub["y_true"].mean())
        start_d = sub["d0"].min().date()
        print(
            f"  fold {fi:>2} (start {start_d}): n={len(sub):>4}  "
            f"Spearman={rho:+.4f}  mean_ret={mean_ret*100:+.2f}%"
        )

    section("STEP 5 -- VERDICT against doc 162 §1 E1 pre-commits")
    eval_window = out_30  # Primary gate
    spearman_30 = eval_window.get("spearman", 0)
    mean_ret_30 = eval_window.get("mean_ret", 0)

    if spearman_30 >= 0.18 and mean_ret_30 >= -0.01:
        verdict = "PASS -- bot trades tomorrow with current sizing; alpha confirmed"
        gate = "PASS"
    elif spearman_30 >= 0.10 and mean_ret_30 >= -0.03:
        verdict = "MARGINAL -- bot trades tomorrow; flag for post-EOD review"
        gate = "MARGINAL"
    else:
        verdict = "FAIL -- URGENT: re-enable D277 HALT before 04:30 ET; alpha collapsed"
        gate = "FAIL"

    print(f"  Last-30-day Spearman: {spearman_30:+.4f} (gate >= +0.18 for PASS)")
    print(f"  Last-30-day mean_ret: {mean_ret_30*100:+.2f}% (gate >= -1% for PASS)")
    print(f"  Doc 147 baseline (Oct-Apr): XGB Spearman +0.230, n=1,797")
    print(f"  VERDICT: {gate}")
    print(f"  ACTION: {verdict}")

    out = {
        "max_d0_in_lake": str(d0.max().date()),
        "n_total_oos": int(len(preds)),
        "doc_147_baseline_spearman": 0.230,
        "doc_147_baseline_n": 1797,
        "last_30_days": out_30,
        "last_60_days": out_60,
        "last_120_days": out_120,
        "gate": gate,
        "verdict": verdict,
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    out_path = MODELS / "v6_e1_cascade_validation_2026_05_12.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
