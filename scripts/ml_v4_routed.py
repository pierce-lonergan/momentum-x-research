"""Path B: train v4 ONLY on rows with microstructure data; route at inference.

Per doc 118 ablation: v3 underperforms by -7.80pp on rows with microstructure
data. The s117 v4 retrain failed because training on ALL rows (mostly
without microstructure) diluted the recent-regime signal.

This script:
  1. Loads aftermath_strat + microstructure features
  2. Filters training data to rows where has_microstructure=1 (the recent
     regime where v3 underperforms)
  3. Trains v4 stacked ensemble on this restricted set
  4. Walk-forward evaluates v4 ON THE SAME RESTRICTED SET
  5. Compares v4 vs v3 on those rows directly

If v4 beats v3 on the routed slice, ship the inference router:
  if has_microstructure: use v4
  else:                  use v3

USAGE:
  python scripts/ml_v4_routed.py [--n-trials 30] [--out-suffix _v4_routed]
"""
from __future__ import annotations
import argparse
import json
import pickle
import sys
import time
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

sys.path.insert(0, str(REPO / "scripts"))
from ml_continuer_v2_ensemble import (  # noqa: E402
    load_data, engineer_features, build_targets, walk_forward_cv,
    fit_xgb, fit_lgbm, fit_logreg, fit_rf, fit_meta,
    conformal_calibrate, conformal_width,
)


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--optuna-params", type=str,
                        default=str(MODELS / "v3_optuna_full16fold.json"))
    parser.add_argument("--out-suffix", type=str, default="_v4_routed")
    args = parser.parse_args()

    optuna_params = None
    if args.optuna_params and Path(args.optuna_params).exists():
        op = json.loads(Path(args.optuna_params).read_text())
        optuna_params = op.get("best_params", op)
        print(f"  Loaded Optuna params from {args.optuna_params}")

    section("STEP 1 - Load + filter to has_microstructure=1 rows")
    df = load_data(include_paths=True, include_microstructure=True,
                    include_news=True,
                    news_path=str(DERIVED / "news_features_polygon_180d.parquet"))
    micro_mask = df["sweep_burst_rate_first30"].notna()
    df_routed = df[micro_mask].reset_index(drop=True)
    print(f"  Total rows: {len(df):,}")
    print(f"  Rows with microstructure: {len(df_routed):,} ({len(df_routed)/len(df)*100:.1f}%)")

    if len(df_routed) < 200:
        print(f"  ERROR: only {len(df_routed)} rows with microstructure; need >=200")
        return 1

    section("STEP 2 - Feature engineering on routed subset")
    X = engineer_features(df_routed)
    X = X.replace([np.inf, -np.inf], 0).fillna(0)
    y_cls, y_reg = build_targets(df_routed)
    dates = df_routed["d0"]
    print(f"  features: {len(X.columns)}, X shape: {X.shape}")
    print(f"  positive rate: {y_cls.mean()*100:.2f}%")

    section("STEP 3 - Walk-forward stacked ensemble (v4 routed)")
    # micro=1 data covers ~89 days (Jan 26 - Apr 24). Use 30d train / 7d test
    # for ~7-9 folds. Smaller windows force the model to generalize within
    # the recent regime rather than memorize long-horizon patterns.
    # min_train_size=100 (vs default 1000) because routed dataset is small.
    folds = list(walk_forward_cv(dates, train_window_days=30, test_window_days=7,
                                    min_train_size=100))
    print(f"  n folds: {len(folds)}")

    fold_results = []
    all_test_predictions = []
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        Xtr, Xte = X[tr_mask], X[te_mask]
        ytr, yte = y_cls[tr_mask], y_cls[te_mask]
        if len(Xte) < 5 or len(Xtr) < 30: continue

        # Inside-fold split: 60% base / 20% meta / 20% calib
        n = len(Xtr)
        rng = np.random.RandomState(42 + fold_i)
        perm = rng.permutation(n)
        n_meta = max(int(n * 0.20), 5)
        n_calib = max(int(n * 0.20), 5)
        meta_idx = perm[:n_meta]
        calib_idx = perm[n_meta:n_meta + n_calib]
        base_idx = perm[n_meta + n_calib:]
        Xb, yb = Xtr.iloc[base_idx], ytr.iloc[base_idx]

        # Train base learners
        try:
            learners = {
                "xgb": fit_xgb(Xb, yb, optuna_params),
                "lgbm": fit_lgbm(Xb, yb, optuna_params),
                "logreg": fit_logreg(Xb, yb),
                "rf": fit_rf(Xb, yb),
            }
        except Exception as e:
            print(f"  fold {fold_i}: train error {e}")
            continue
        P_meta = np.column_stack([m.predict_proba(Xtr.iloc[meta_idx])[:, 1]
                                    for m in learners.values()])
        meta = fit_meta(P_meta, ytr.iloc[meta_idx].values)

        # Test predictions
        P_test = np.column_stack([m.predict_proba(Xte)[:, 1] for m in learners.values()])
        probs_test = meta.predict_proba(P_test)[:, 1]

        # Conformal threshold
        P_calib = np.column_stack([m.predict_proba(Xtr.iloc[calib_idx])[:, 1]
                                     for m in learners.values()])
        probs_calib = meta.predict_proba(P_calib)[:, 1]
        thr = conformal_calibrate(probs_calib, ytr.iloc[calib_idx].values, alpha=0.10)
        widths = conformal_width(probs_test, thr)

        y_reg_te = df_routed.loc[te_mask, "ret_t5"].clip(-0.50, 1.00)

        n_30 = int((probs_test >= 0.30).sum())
        n_50 = int((probs_test >= 0.50).sum())
        avg_30 = float(y_reg_te[probs_test >= 0.30].mean()) if n_30 else 0.0
        avg_50 = float(y_reg_te[probs_test >= 0.50].mean()) if n_50 else 0.0

        fold_results.append({
            "fold": fold_i, "n_test": len(Xte),
            "n_30": n_30, "avg_30": avg_30,
            "n_50": n_50, "avg_50": avg_50,
        })
        per_row = pd.DataFrame({
            "fold": fold_i,
            "d0": df_routed.loc[te_mask, "d0"].values,
            "ticker": df_routed.loc[te_mask, "ticker"].values,
            "y_cls": yte.values,
            "y_reg": y_reg_te.values,
            "prob_continuer": probs_test,
            "conformal_width": widths,
        })
        all_test_predictions.append(per_row)

    section("STEP 4 - Aggregate v4-routed WF stats")
    if not fold_results:
        print("  No folds completed!")
        return 1
    fr = pd.DataFrame(fold_results)
    total_n_30 = fr["n_30"].sum()
    weighted_avg_30 = (fr["n_30"] * fr["avg_30"]).sum() / max(total_n_30, 1) * 100
    total_n_50 = fr["n_50"].sum()
    weighted_avg_50 = (fr["n_50"] * fr["avg_50"]).sum() / max(total_n_50, 1) * 100

    print(f"  v4-routed P>=0.30:  n={total_n_30:>4,}  avg={weighted_avg_30:>+6.2f}%")
    print(f"  v4-routed P>=0.50:  n={total_n_50:>4,}  avg={weighted_avg_50:>+6.2f}%")

    section("STEP 5 - Compare v4-routed vs v3 on the SAME rows")
    v3_p = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    v3 = pd.read_parquet(v3_p)
    v3["d0"] = pd.to_datetime(v3["d0"])
    v4 = pd.concat(all_test_predictions, ignore_index=True)
    v4["d0"] = pd.to_datetime(v4["d0"])
    v3_routed = v3.merge(v4[["d0", "ticker"]], on=["d0", "ticker"], how="inner")

    v3_n_30 = (v3_routed["prob_continuer"] >= 0.30).sum()
    v3_avg_30 = (v3_routed.loc[v3_routed["prob_continuer"] >= 0.30, "y_reg"]
                  .clip(-0.5, 1.0).mean() * 100) if v3_n_30 else 0
    print(f"  v3 (same rows) P>=0.30: n={v3_n_30:>4,}  avg={v3_avg_30:>+6.2f}%")
    print(f"  v4-routed      P>=0.30: n={total_n_30:>4,}  avg={weighted_avg_30:>+6.2f}%")
    delta = weighted_avg_30 - v3_avg_30
    print(f"\n  DELTA: {delta:+.2f}pp")
    if delta > 1.0:
        print(f"  *** v4-routed BEATS v3 on micro=1 rows by +{delta:.2f}pp")
        print("      SHIP Path B inference router")
    elif delta < -1.0:
        print(f"  --- v4-routed LOSES to v3 by {delta:.2f}pp")
        print("      HOLD v3 (single model)")
    else:
        print(f"  -   v4-routed within +/-1pp of v3 ({delta:+.2f}pp)")
        print("      Not enough lift to justify routing complexity")

    section("STEP 6 - Persist")
    if all_test_predictions:
        out_preds = DERIVED / f"ml_v4_routed_walkforward_predictions{args.out_suffix}.parquet"
        v4.to_parquet(out_preds, compression="zstd")
        print(f"  Wrote {out_preds}")

    summary = {
        "n_routed_rows": len(df_routed),
        "n_folds_completed": len(fr),
        "v4_routed_p30_n": int(total_n_30),
        "v4_routed_p30_avg_pct": float(weighted_avg_30),
        "v4_routed_p50_n": int(total_n_50),
        "v4_routed_p50_avg_pct": float(weighted_avg_50),
        "v3_p30_n": int(v3_n_30),
        "v3_p30_avg_pct": float(v3_avg_30),
        "delta_pp": float(delta),
        "verdict": ("SHIP Path B" if delta > 1.0
                     else "HOLD v3 single model" if delta < -1.0
                     else "marginal"),
    }
    out_path = MODELS / f"v4_routed_summary{args.out_suffix}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
