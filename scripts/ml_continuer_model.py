"""ML continuer model — XGBoost + walk-forward + conformal prediction.

Per doc 102 §4.1: the top-5 ship-this-session list is
  A1 (XGBoost), A2 (conformal), I1 (walk-forward), I7 (sizer), I3 (versioning).

This script implements A1+A2+I1+I3 (sizer is separate file).

Pipeline:
  1. Load aftermath_strat.parquet (20K clean catalog rows)
  2. Build features at d0 time (no future leak):
       - intraday_pct, ret_open_close_d0, dvol_d0, open_price
       - WALK-FORWARD per-ticker prior_n, prior_cont, prior_fade
       - prior_7d_count, day_of_week, month
       - rolling per-ticker volume/vol_ratio_t1
  3. Target: stratum_t5 == 'continuer' (binary), ret_t5 (regression)
  4. Walk-forward CV with rolling 90-day train, 30-day test
  5. XGBoost classifier + regressor
  6. Conformal calibration (split conformal)
  7. Compare to per-ticker prior baseline (V3 chronic-fader gate)
  8. Persist model artifacts + manifest

Outputs:
  data/models/continuer_v1.pkl
  data/models/continuer_v1_manifest.json
  data/polygon_warehouse/derived/ml_walkforward_results.parquet
"""
from __future__ import annotations
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
MODELS.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_data() -> pd.DataFrame:
    """Load aftermath_strat with walk-forward per-ticker features.

    Critical: prior_n/prior_cont/prior_fade are computed using ONLY rows
    strictly BEFORE d0 — no future leak.
    """
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    df = con.sql(f"""
        WITH base AS (
            SELECT * FROM read_parquet('{aftermath}')
            WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
              AND open BETWEEN 0.5 AND 50 AND volume > 100000
        ),
        enriched AS (
            SELECT *,
                   COUNT(*) OVER (PARTITION BY ticker ORDER BY d0
                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                       AND 1 PRECEDING) AS prior_n,
                   SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) OVER (
                       PARTITION BY ticker ORDER BY d0
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_cont,
                   SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) OVER (
                       PARTITION BY ticker ORDER BY d0
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_fade,
                   COUNT(*) OVER (PARTITION BY ticker
                                   ORDER BY d0
                                   RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                           AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count,
                   AVG(ret_t5) OVER (PARTITION BY ticker ORDER BY d0
                                     ROWS BETWEEN UNBOUNDED PRECEDING
                                         AND 1 PRECEDING) AS prior_avg_t5
            FROM base
        )
        SELECT * FROM enriched
        ORDER BY d0
    """).df()
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the feature matrix (only walk-forward-safe features)."""
    f = pd.DataFrame()
    # Direct features
    f["log_open"] = np.log(df["open"].clip(lower=0.01))
    f["log_dvol_d0"] = np.log(df["dvol_d0"].clip(lower=1))
    f["intraday_pct"] = df["intraday_pct"]
    f["intraday_pct_log"] = np.log1p(df["intraday_pct"].clip(lower=0))
    f["ret_open_close_d0"] = df["ret_open_close_d0"]
    f["close_strength"] = df["ret_open_close_d0"] / df["intraday_pct"].clip(lower=0.01)
    # Walk-forward priors (smoothed, beta-binomial)
    f["prior_n"] = df["prior_n"].fillna(0)
    f["prior_n_log"] = np.log1p(df["prior_n"].fillna(0))
    f["prior_cont_rate"] = (20.78 + df["prior_cont"].fillna(0) * 100) / (100 + df["prior_n"].fillna(0))
    f["prior_fade_rate"] = (43.36 + df["prior_fade"].fillna(0) * 100) / (100 + df["prior_n"].fillna(0))
    f["prior_avg_t5"] = df["prior_avg_t5"].fillna(0)
    f["prior_7d_count"] = df["prior_7d_count"].fillna(0)
    # Time features
    d0 = pd.to_datetime(df["d0"])
    f["dow"] = d0.dt.dayofweek
    f["month"] = d0.dt.month
    f["year"] = d0.dt.year
    f["day_of_year"] = d0.dt.dayofyear
    return f


def build_targets(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Two targets: binary continuer + regression on ret_t5."""
    y_cls = (df["ret_t5"] >= 0.10).astype(int)
    y_reg = df["ret_t5"].clip(-0.50, 1.00)  # cap for stability
    return y_cls, y_reg


def walk_forward_cv(
    X: pd.DataFrame,
    y_cls: pd.Series,
    y_reg: pd.Series,
    dates: pd.Series,
    train_window_days: int = 365,
    test_window_days: int = 30,
    min_train_size: int = 1000,
):
    """Generator yielding (train_idx, test_idx) for walk-forward CV.

    Rolling window: train on N consecutive days, test on next M, slide.
    """
    dates_dt = pd.to_datetime(dates)
    start = dates_dt.min() + timedelta(days=train_window_days)
    end = dates_dt.max()
    cur = start
    while cur < end:
        train_end = cur
        train_start = cur - timedelta(days=train_window_days)
        test_end = cur + timedelta(days=test_window_days)
        train_mask = (dates_dt >= train_start) & (dates_dt < train_end)
        test_mask = (dates_dt >= train_end) & (dates_dt < test_end)
        if train_mask.sum() < min_train_size or test_mask.sum() == 0:
            cur += timedelta(days=test_window_days)
            continue
        yield train_mask.values, test_mask.values
        cur += timedelta(days=test_window_days)


def fit_xgb_clf(X_train, y_train):
    import xgboost as xgb
    return xgb.XGBClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbosity=0, n_jobs=-1,
        eval_metric="logloss",
    ).fit(X_train, y_train)


def fit_xgb_reg(X_train, y_train):
    import xgboost as xgb
    return xgb.XGBRegressor(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbosity=0, n_jobs=-1,
        eval_metric="rmse",
    ).fit(X_train, y_train)


def conformal_calibrate(probs_calib: np.ndarray, y_calib: np.ndarray, alpha: float = 0.1):
    """Split conformal: returns half-width of prediction intervals.

    For binary classification: nonconformity = 1 - p(true class).
    Quantile of nonconformity over the calibration set gives the threshold
    for predicting "covered" sets.

    For our use-case: we use this to compute a CONFIDENCE WIDTH per
    prediction. Wider width = less confidence = smaller position.
    """
    # Nonconformity scores: 1 - prob assigned to true class
    nc = 1 - np.where(y_calib == 1, probs_calib, 1 - probs_calib)
    # The (1-alpha)-quantile is our threshold
    n = len(nc)
    q_level = np.ceil((n + 1) * (1 - alpha)) / n
    threshold = np.quantile(nc, min(q_level, 1.0))
    return threshold


def conformal_width(probs_test: np.ndarray, threshold: float) -> np.ndarray:
    """Return per-prediction confidence width.

    Width = max(distance from each prediction's class probability to
    decision boundary 0.5 that would still be 'covered').
    Higher width = less confidence.
    """
    # For binary, width is essentially 1 - max(p, 1-p), inflated by threshold
    width = (1 - np.abs(probs_test - 0.5) * 2) * (1 + threshold)
    return np.clip(width, 0, 1)


def baseline_chronic_fader_gate(features: pd.DataFrame) -> pd.Series:
    """Baseline V3-WF gate: continuer if (rate >= 5% OR no history)
    AND (prior_7d_count <= 1) AND (dvol_d0 reasonable)."""
    dvol = np.exp(features["log_dvol_d0"])
    rate = features["prior_cont_rate"]
    n = features["prior_n"]
    p7d = features["prior_7d_count"]
    intra = features["intraday_pct"]
    return (
        ((n == 0) | (rate >= 5.0))
        & (p7d <= 1)
        & (dvol >= 1e5)
        & (dvol <= 100e6)
        & (intra >= 0.30)
        & (intra <= 1.00)
    )


def main():
    section("STEP 1 - Load + enrich data (walk-forward priors)")
    t0 = time.perf_counter()
    df = load_data()
    print(f"  loaded {len(df):,} rows in {time.perf_counter()-t0:.1f}s")
    print(f"  date range: {df['d0'].min()} -> {df['d0'].max()}")

    section("STEP 2 - Feature engineering")
    X = engineer_features(df)
    y_cls, y_reg = build_targets(df)
    dates = df["d0"]
    print(f"  features: {list(X.columns)}")
    print(f"  X shape: {X.shape}")
    print(f"  y_cls: {y_cls.sum():,} positive (continuer) of {len(y_cls):,} ({y_cls.mean()*100:.2f}%)")
    print(f"  y_reg: median={y_reg.median()*100:+.2f}%  mean={y_reg.mean()*100:+.2f}%")

    section("STEP 3 - Walk-forward CV")
    folds = list(walk_forward_cv(X, y_cls, y_reg, dates,
                                   train_window_days=365, test_window_days=30))
    print(f"  n folds: {len(folds)}")

    fold_results = []
    all_test_preds = []
    for fold_i, (train_mask, test_mask) in enumerate(folds):
        X_tr, X_te = X[train_mask], X[test_mask]
        y_cls_tr, y_cls_te = y_cls[train_mask], y_cls[test_mask]
        y_reg_tr, y_reg_te = y_reg[train_mask], y_reg[test_mask]
        baseline_te = baseline_chronic_fader_gate(X_te)
        if len(X_te) < 30:
            continue
        # Split train into model + calib (80/20)
        n_tr = len(X_tr)
        rng = np.random.RandomState(42 + fold_i)
        perm = rng.permutation(n_tr)
        n_calib = int(n_tr * 0.20)
        calib_idx, model_idx = perm[:n_calib], perm[n_calib:]
        Xm, ym_cls, ym_reg = X_tr.iloc[model_idx], y_cls_tr.iloc[model_idx], y_reg_tr.iloc[model_idx]
        Xc, yc_cls, _ = X_tr.iloc[calib_idx], y_cls_tr.iloc[calib_idx], y_reg_tr.iloc[calib_idx]

        clf = fit_xgb_clf(Xm, ym_cls)
        reg = fit_xgb_reg(Xm, ym_reg)

        # Conformal threshold from calibration set
        probs_calib = clf.predict_proba(Xc)[:, 1]
        thr = conformal_calibrate(probs_calib, yc_cls.values, alpha=0.10)

        # Test
        probs_test = clf.predict_proba(X_te)[:, 1]
        ret_pred_test = reg.predict(X_te)
        widths = conformal_width(probs_test, thr)

        # Compare strategies
        # Baseline V3-WF: take all where baseline gate passes
        baseline_n = baseline_te.sum()
        baseline_avg = y_reg_te[baseline_te].mean() if baseline_n else 0
        baseline_win = (y_reg_te[baseline_te] > 0).mean() if baseline_n else 0

        # ML threshold-50%: take where probs >= 0.5
        ml_50 = probs_test >= 0.50
        ml_50_n = ml_50.sum()
        ml_50_avg = y_reg_te[ml_50].mean() if ml_50_n else 0
        ml_50_win = (y_reg_te[ml_50] > 0).mean() if ml_50_n else 0

        # ML threshold-30%: take where probs >= 0.30 (catalog baseline is 21% so 30% is meaningful)
        ml_30 = probs_test >= 0.30
        ml_30_n = ml_30.sum()
        ml_30_avg = y_reg_te[ml_30].mean() if ml_30_n else 0
        ml_30_win = (y_reg_te[ml_30] > 0).mean() if ml_30_n else 0

        # ML high-confidence: take where probs >= 0.5 AND conformal width < 0.5
        ml_hc = (probs_test >= 0.50) & (widths < 0.50)
        ml_hc_n = ml_hc.sum()
        ml_hc_avg = y_reg_te[ml_hc].mean() if ml_hc_n else 0
        ml_hc_win = (y_reg_te[ml_hc] > 0).mean() if ml_hc_n else 0

        fold_results.append({
            "fold": fold_i,
            "test_start": str(dates[test_mask].min()),
            "test_end": str(dates[test_mask].max()),
            "n_test": len(X_te),
            "baseline_n": int(baseline_n), "baseline_avg": baseline_avg, "baseline_win": baseline_win,
            "ml_50_n": int(ml_50_n), "ml_50_avg": ml_50_avg, "ml_50_win": ml_50_win,
            "ml_30_n": int(ml_30_n), "ml_30_avg": ml_30_avg, "ml_30_win": ml_30_win,
            "ml_hc_n": int(ml_hc_n), "ml_hc_avg": ml_hc_avg, "ml_hc_win": ml_hc_win,
            "conformal_threshold": thr,
        })

        # Store per-row predictions
        test_preds = pd.DataFrame({
            "fold": fold_i,
            "d0": df.loc[test_mask, "d0"].values,
            "ticker": df.loc[test_mask, "ticker"].values,
            "y_cls": y_cls_te.values,
            "y_reg": y_reg_te.values,
            "prob_continuer": probs_test,
            "ret_pred": ret_pred_test,
            "conformal_width": widths,
            "baseline_pass": baseline_te.values,
        })
        all_test_preds.append(test_preds)

    section("STEP 4 - Aggregate fold results")
    fr = pd.DataFrame(fold_results)
    # Filter folds with too few test samples
    fr = fr[fr["n_test"] >= 30]
    print(f"  n folds with >=30 test rows: {len(fr)}")

    # Aggregate metrics
    def agg(name, n_col, avg_col, win_col):
        # Weight by n in each fold
        total_n = fr[n_col].sum()
        if total_n == 0:
            return f"{name}: no samples"
        weighted_avg = (fr[n_col] * fr[avg_col]).sum() / total_n
        # Weighted win rate
        weighted_win = (fr[n_col] * fr[win_col]).sum() / total_n
        # Sharpe-like: mean / std of per-fold avg
        sharpe = fr[avg_col].mean() / max(fr[avg_col].std(), 1e-6) * (252/30)**0.5
        return (f"{name:<25} n={total_n:>6,}  weighted_avg={weighted_avg*100:>+6.2f}%  "
                f"weighted_win={weighted_win*100:>5.1f}%  sharpe~{sharpe:>+5.2f}")

    print()
    print("  STRATEGY                     SAMPLES   WEIGHTED AVG  WIN%   SHARPE")
    print("  " + "-" * 78)
    print("  " + agg("BASELINE (V3-WF gate)", "baseline_n", "baseline_avg", "baseline_win"))
    print("  " + agg("ML threshold P>=0.30",   "ml_30_n",    "ml_30_avg",    "ml_30_win"))
    print("  " + agg("ML threshold P>=0.50",   "ml_50_n",    "ml_50_avg",    "ml_50_win"))
    print("  " + agg("ML high-confidence",     "ml_hc_n",    "ml_hc_avg",    "ml_hc_win"))

    section("STEP 5 - SHAP feature importance (final-fold model)")
    # Re-train on the full TRAIN portion of the LAST fold, then SHAP-importance
    last_train_mask, last_test_mask = folds[-1]
    Xm = X[last_train_mask]; ym = y_cls[last_train_mask]
    final_clf = fit_xgb_clf(Xm, ym)
    importances = pd.Series(final_clf.feature_importances_, index=X.columns).sort_values(ascending=False)
    print("  Feature importance (gain):")
    for k, v in importances.items():
        print(f"    {k:25s} {v:.4f}")

    section("STEP 6 - Per-fold breakdown")
    print(f"  {'fold':<5} {'test_start':<12} {'n':>4} {'base_n':>6} {'base_avg':>9} "
          f"{'ml30_n':>6} {'ml30_avg':>9} {'ml50_n':>6} {'ml50_avg':>9}")
    for _, r in fr.iterrows():
        print(f"  {r['fold']:<5} {r['test_start']:<12} {r['n_test']:>4,} "
              f"{r['baseline_n']:>6,} {r['baseline_avg']*100:>+8.2f}% "
              f"{r['ml_30_n']:>6,} {r['ml_30_avg']*100:>+8.2f}% "
              f"{r['ml_50_n']:>6,} {r['ml_50_avg']*100:>+8.2f}%")

    section("STEP 7 - Persist model + manifest")
    # Train final model on ALL data through last cutoff (for production use)
    X_final = X[dates < dates.max() - timedelta(days=5)]  # leave last 5d unseen
    y_final = y_cls[dates < dates.max() - timedelta(days=5)]
    final_clf = fit_xgb_clf(X_final, y_final)
    final_reg = fit_xgb_reg(X_final, y_reg[dates < dates.max() - timedelta(days=5)])
    final_calib_idx = np.random.RandomState(42).choice(len(X_final),
                                                        size=int(len(X_final)*0.2), replace=False)
    final_thr = conformal_calibrate(
        final_clf.predict_proba(X_final.iloc[final_calib_idx])[:, 1],
        y_final.iloc[final_calib_idx].values, alpha=0.10,
    )

    artifacts = {
        "classifier": final_clf,
        "regressor": final_reg,
        "conformal_threshold": float(final_thr),
        "feature_columns": list(X.columns),
        "training_rows": len(X_final),
        "training_date_range": [str(dates.min()), str(dates[dates < dates.max() - timedelta(days=5)].max())],
    }
    pkl_path = MODELS / "continuer_v1.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(artifacts, f)
    print(f"  model artifacts -> {pkl_path}")

    manifest = {
        "version": "v1",
        "trained_at": pd.Timestamp.utcnow().isoformat(),
        "data_rows": len(df),
        "training_rows": len(X_final),
        "feature_columns": list(X.columns),
        "conformal_threshold": float(final_thr),
        "walk_forward_results": {
            "n_folds": int(len(fr)),
            "baseline_weighted_avg": float((fr["baseline_n"] * fr["baseline_avg"]).sum() / max(fr["baseline_n"].sum(), 1)),
            "ml_30_weighted_avg":   float((fr["ml_30_n"]    * fr["ml_30_avg"]).sum()    / max(fr["ml_30_n"].sum(), 1)),
            "ml_50_weighted_avg":   float((fr["ml_50_n"]    * fr["ml_50_avg"]).sum()    / max(fr["ml_50_n"].sum(), 1)),
            "ml_hc_weighted_avg":   float((fr["ml_hc_n"]    * fr["ml_hc_avg"]).sum()    / max(fr["ml_hc_n"].sum(), 1)),
        },
        "feature_importance": importances.to_dict(),
    }
    manifest_path = MODELS / "continuer_v1_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    print(f"  manifest -> {manifest_path}")

    # Persist all-fold predictions for analysis
    if all_test_preds:
        preds_df = pd.concat(all_test_preds)
        out_preds = DERIVED / "ml_walkforward_results.parquet"
        preds_df.to_parquet(out_preds, compression="zstd")
        print(f"  predictions -> {out_preds} ({len(preds_df):,} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
