"""Optuna hyperparameter tuning for v2/v3 stacked ensemble.

Tunes XGBoost + LightGBM hyperparameters across walk-forward folds.
Search space: n_estimators, max_depth, learning_rate, subsample,
colsample_bytree, min_child_weight (XGB), num_leaves (LGBM).

Objective: maximize per-trade T+5 average return at P>=0.30, weighted
by n_picks per fold.

CLI flags:
  --n-folds N             # default 6 (most recent); -1 = all 16 folds
  --n-trials N            # default 30
  --include-paths         # use v3 features (path-derived + LLM survivor)
  --out NAME              # default v2_optuna_best_params.json

Examples:
  # Original v2 / 6-fold (session 107)
  python scripts/ml_optuna_tune_v2.py

  # Full 16-fold v3 sweep (session 111)
  python scripts/ml_optuna_tune_v2.py --n-folds -1 --include-paths --n-trials 30 --out v3_optuna_full16fold.json

Outputs:
  data/models/<out>
"""
from __future__ import annotations
import argparse, json, pickle, sys, time
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import optuna

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

sys.path.insert(0, str(REPO / "scripts"))
from ml_continuer_v2_ensemble import (  # noqa: E402
    load_data, engineer_features, build_targets, walk_forward_cv,
    fit_logreg, fit_rf, fit_meta,
    conformal_calibrate, conformal_width,
)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def fit_xgb_tuned(X, y, params):
    import xgboost as xgb
    return xgb.XGBClassifier(**params, random_state=42, verbosity=0,
                              n_jobs=-1, eval_metric="logloss").fit(X, y)


def fit_lgbm_tuned(X, y, params):
    import lightgbm as lgb
    return lgb.LGBMClassifier(**params, random_state=42, verbosity=-1, n_jobs=-1).fit(X, y)


def build_tuned_ensemble(X_train, y_train, X_test, y_reg_test, xgb_params, lgbm_params, rng_seed=42):
    """Build ensemble with given hyperparameters; return per-trade stats at P>=0.30."""
    n = len(X_train)
    rng = np.random.RandomState(rng_seed)
    perm = rng.permutation(n)
    n_meta = int(n * 0.20)
    n_calib = int(n * 0.20)
    meta_idx = perm[:n_meta]
    calib_idx = perm[n_meta:n_meta + n_calib]
    base_idx = perm[n_meta + n_calib:]

    Xb, yb = X_train.iloc[base_idx], y_train.iloc[base_idx]
    learners = {
        "xgb":    fit_xgb_tuned(Xb, yb, xgb_params),
        "lgbm":   fit_lgbm_tuned(Xb, yb, lgbm_params),
        "logreg": fit_logreg(Xb, yb),
        "rf":     fit_rf(Xb, yb),
    }
    P_meta = np.column_stack([m.predict_proba(X_train.iloc[meta_idx])[:, 1] for m in learners.values()])
    meta = fit_meta(P_meta, y_train.iloc[meta_idx].values)

    P_test = np.column_stack([m.predict_proba(X_test)[:, 1] for m in learners.values()])
    probs_test = meta.predict_proba(P_test)[:, 1]

    selected = probs_test >= 0.30
    if selected.sum() < 5:
        return None
    return {
        "n": int(selected.sum()),
        "avg_t5": float(y_reg_test[selected].mean()),
        "win": float((y_reg_test[selected] > 0).mean()),
    }


def objective(trial, X, y_cls, y_reg, dates, n_folds: int = 6):
    """Optuna objective: weighted_avg per-trade T+5 across folds.

    n_folds: -1 = use all WF folds; otherwise use last N (more recent = harder).
    """
    xgb_params = {
        "n_estimators": trial.suggest_int("xgb_n_estimators", 200, 600, step=100),
        "max_depth": trial.suggest_int("xgb_max_depth", 3, 7),
        "learning_rate": trial.suggest_float("xgb_lr", 0.02, 0.10, log=True),
        "subsample": trial.suggest_float("xgb_subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("xgb_colsample", 0.6, 1.0),
        "min_child_weight": trial.suggest_int("xgb_min_child", 1, 10),
    }
    lgbm_params = {
        "n_estimators": trial.suggest_int("lgbm_n_estimators", 200, 600, step=100),
        "max_depth": trial.suggest_int("lgbm_max_depth", 4, 10),
        "learning_rate": trial.suggest_float("lgbm_lr", 0.02, 0.10, log=True),
        "num_leaves": trial.suggest_int("lgbm_num_leaves", 15, 63),
        "subsample": trial.suggest_float("lgbm_subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("lgbm_colsample", 0.6, 1.0),
    }
    folds = list(walk_forward_cv(dates, train_window_days=365, test_window_days=30))
    if n_folds > 0:
        folds = folds[-n_folds:]   # last N folds (most recent regime)
    fold_results = []
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        Xtr, Xte = X[tr_mask], X[te_mask]
        ytr, _ = y_cls[tr_mask], y_cls[te_mask]
        y_reg_te = y_reg[te_mask]
        if len(Xte) < 30: continue
        try:
            res = build_tuned_ensemble(Xtr, ytr, Xte, y_reg_te, xgb_params, lgbm_params, rng_seed=42 + fold_i)
        except Exception as e:
            return -1.0  # fail trial
        if res is None: continue
        fold_results.append(res)
    if not fold_results:
        return -1.0
    # Weighted avg by n
    total_n = sum(r["n"] for r in fold_results)
    if total_n == 0: return -1.0
    weighted_avg = sum(r["n"] * r["avg_t5"] for r in fold_results) / total_n
    return weighted_avg  # maximize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-folds", type=int, default=6,
                        help="Number of recent WF folds to evaluate; -1 = all 16")
    parser.add_argument("--n-trials", type=int, default=30)
    parser.add_argument("--include-paths", action="store_true",
                        help="Use v3 features (intraday paths + LLM survivor)")
    parser.add_argument("--out", type=str, default="v2_optuna_best_params.json",
                        help="Output JSON filename under data/models/")
    args = parser.parse_args()

    section("STEP 1 - Load + features")
    df = load_data(include_paths=args.include_paths)
    X = engineer_features(df)
    X = X.replace([np.inf, -np.inf], 0).fillna(0)
    y_cls, y_reg = build_targets(df)
    dates = df["d0"]
    print(f"  {len(X):,} rows, {len(X.columns)} features")

    fold_label = "all 16" if args.n_folds == -1 else f"{args.n_folds} most recent"
    section(f"STEP 2 - Optuna search ({args.n_trials} trials, {fold_label} folds)")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize",
                                  sampler=optuna.samplers.TPESampler(seed=42))
    t0 = time.time()
    for trial_i in range(args.n_trials):
        trial = study.ask()
        try:
            value = objective(trial, X, y_cls, y_reg, dates, n_folds=args.n_folds)
        except Exception as e:
            print(f"  trial {trial_i}: ERROR {str(e)[:60]}")
            value = -1.0
        study.tell(trial, value)
        elapsed = time.time() - t0
        eta = elapsed / (trial_i + 1) * (args.n_trials - trial_i - 1)
        print(f"  trial {trial_i+1:2d}/{args.n_trials}  value={value*100:>+6.2f}%  "
              f"best={study.best_value*100:>+6.2f}%  elapsed={elapsed:.0f}s  eta={eta:.0f}s")

    section("STEP 3 - Best hyperparameters")
    print(f"  Best value: {study.best_value*100:+.3f}%/trade T+5")
    print(f"  Best params:")
    for k, v in study.best_params.items():
        print(f"    {k:25s} {v}")

    out = MODELS / args.out
    out.write_text(json.dumps({
        "best_value_avg_t5": study.best_value,
        "best_params": study.best_params,
        "n_trials": len(study.trials),
        "n_folds": args.n_folds,
        "include_paths": args.include_paths,
        "n_features": len(X.columns),
        "elapsed_s": time.time() - t0,
    }, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
