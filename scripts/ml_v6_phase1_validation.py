"""v6 Phase 1 validation: does the microstructure pack improve over v3 features?

Per the Phase 0 hygiene rule (docs/research-log/133 § 7), every v6 feature
addition must pass three gates BEFORE shipping to production:

  1. Numerai-style 100% feature-neutralized Spearman improves over v3 baseline
  2. Deflated Sharpe Ratio with explicit N_trials >= 0.5
  3. CPCV stability: frac of fold-subsets with positive Sharpe >= 90%

This script runs the controlled experiment:

  CONTROL    = v3-features-only XGBoost regression on ret_t5, 16-fold WF
  TREATMENT  = (v3 features + v6 microstructure pack) XGBoost regression

Both models use IDENTICAL hyperparameters (XGBoost defaults — NO grid
search to avoid inducing multiple-testing inflation). The only thing
that differs is the feature matrix.

We restrict the comparison to (ticker, d0) keys where v6_pack has
coverage (~3K rows from 2025-08 onward) — this is the *fair* apples-
to-apples test, not v3-on-full-history vs v6-on-recent-window.

OUTPUT:
  data/models/v6_phase1_validation.json
  data/polygon_warehouse/derived/ml_v6_phase1_control_preds.parquet
  data/polygon_warehouse/derived/ml_v6_phase1_treatment_preds.parquet

USAGE:
  python scripts/ml_v6_phase1_validation.py
  python scripts/ml_v6_phase1_validation.py --n-folds 8     # quicker dry run
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# Data
# ──────────────────────────────────────────────────────────────────────


V6_FEATURES = [
    "vpin_d0",
    "ofi_first30_d0",
    "kyle_lambda_d0",
    "hawkes_fano_d0",
    "amihud_illiq_d0",
    "iso_sweep_count_d0",
]


def load_v3_plus_v6(v6_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """Load v3 features (paths included), join v6 microstructure pack.

    Returns:
      X_v3        — v3 feature matrix
      X_v3_v6     — v3 ⊕ v6 feature matrix (same row order)
      y           — ret_t5 regression target
      d0          — date column (for fold splits)
    """
    from ml_continuer_v2_ensemble import load_data, engineer_features  # type: ignore

    df = load_data(include_paths=True)
    print(f"  v3+paths loaded: {len(df):,} rows")

    v6 = pd.read_parquet(v6_path)
    print(f"  v6 pack loaded:  {len(v6):,} rows")

    # Normalize d0 dtypes for the join
    df["d0"] = pd.to_datetime(df["d0"])
    v6["d0"] = pd.to_datetime(v6["d0"])

    merged = df.merge(v6[["ticker", "d0", *V6_FEATURES]],
                      on=["ticker", "d0"], how="inner")
    print(f"  merged: {len(merged):,} rows after inner join "
          f"(restricted to v6-covered keys)")

    if len(merged) < 500:
        raise RuntimeError(
            f"only {len(merged)} rows after v6 join — backfill incomplete")

    X_v3 = engineer_features(merged)
    X_v3 = X_v3.replace([np.inf, -np.inf], 0).fillna(0)

    # v3+v6 = v3 columns + v6 features (cleaned)
    v6_block = merged[V6_FEATURES].copy()
    v6_block = v6_block.replace([np.inf, -np.inf], np.nan)
    # Log-transform fat-tailed features for tabular model stability
    v6_block["log_kyle_lambda_d0"] = np.log1p(v6_block["kyle_lambda_d0"].clip(lower=0))
    v6_block["log_hawkes_fano_d0"] = np.log1p(v6_block["hawkes_fano_d0"].clip(lower=0))
    v6_block["log_amihud_illiq_d0"] = np.log1p(v6_block["amihud_illiq_d0"].clip(lower=0))
    v6_block["log_iso_sweep_count_d0"] = np.log1p(v6_block["iso_sweep_count_d0"].clip(lower=0))
    v6_block = v6_block.fillna(0)
    v6_block.index = X_v3.index

    X_v3_v6 = pd.concat([X_v3, v6_block], axis=1)
    y = merged["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(merged["d0"]).reset_index(drop=True)
    X_v3 = X_v3.reset_index(drop=True)
    X_v3_v6 = X_v3_v6.reset_index(drop=True)
    return X_v3, X_v3_v6, y, d0


# ──────────────────────────────────────────────────────────────────────
# Walk-forward training (single XGBoost config — NO grid search)
# ──────────────────────────────────────────────────────────────────────


def walk_forward_xgb(X: pd.DataFrame, y: pd.Series, d0: pd.Series,
                     n_folds: int = 16,
                     train_window_days: int = 365,
                     test_window_days: int = 30) -> pd.DataFrame:
    """Rolling walk-forward XGBoost regression. Returns OOS predictions
    keyed on (d0, original_index).

    Single hyperparameter config — NO Optuna, NO cross-validation grid,
    NO feature selection. This is critical: any grid search would
    inflate N_trials in the downstream DSR computation.
    """
    import xgboost as xgb

    XGB_PARAMS = {
        "objective": "reg:squarederror",
        "n_estimators": 600,
        "max_depth": 5,
        "learning_rate": 0.03,
        "subsample": 0.8,
        "colsample_bytree": 0.6,
        "min_child_weight": 5,
        "reg_alpha": 0.5,
        "reg_lambda": 1.0,
        "tree_method": "hist",
        "device": "cuda",
        "n_jobs": -1,
        "random_state": 42,
    }

    df = pd.DataFrame({"y": y, "d0": d0, "_idx": np.arange(len(y))})
    d_min = d0.min()
    d_max = d0.max()
    total_days = (d_max - d_min).days
    # Spread n_folds test windows evenly across the data
    fold_starts = pd.date_range(
        d_min + pd.Timedelta(days=train_window_days),
        d_max - pd.Timedelta(days=test_window_days),
        periods=n_folds,
    )

    all_preds = []
    for fold_i, fstart in enumerate(fold_starts):
        train_lo = fstart - pd.Timedelta(days=train_window_days)
        train_hi = fstart
        test_lo = fstart
        test_hi = fstart + pd.Timedelta(days=test_window_days)

        train_mask = (d0 >= train_lo) & (d0 < train_hi)
        test_mask = (d0 >= test_lo) & (d0 < test_hi)
        if train_mask.sum() < 100 or test_mask.sum() < 5:
            continue

        Xtr = X.loc[train_mask]
        ytr = y.loc[train_mask]
        Xte = X.loc[test_mask]
        yte = y.loc[test_mask]
        d0_te = d0.loc[test_mask]

        model = xgb.XGBRegressor(**XGB_PARAMS)
        model.fit(Xtr, ytr, verbose=False)
        pred = model.predict(Xte)

        all_preds.append(pd.DataFrame({
            "fold": fold_i,
            "d0": d0_te.values,
            "y_true": yte.values,
            "y_pred": pred,
            "_idx": df.loc[test_mask, "_idx"].values,
        }))
        print(f"  fold {fold_i:>2}: train={train_mask.sum():>5,} test={test_mask.sum():>4,} "
              f"  Spearman={spearmanr(yte, pred).statistic:+.3f}")

    return pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────
# Validation: neutralized rho + CPCV stability + DSR
# ──────────────────────────────────────────────────────────────────────


def neutralized_rho(preds: pd.DataFrame, exposures: pd.DataFrame,
                    proportion: float = 1.0) -> float:
    """Project predictions onto residual space orthogonal to exposures
    (Numerai-style), then compute Spearman with y_true.

    pred_neut = pred - proportion · F · pinv(F) · pred
    """
    F = exposures.values.astype(float)
    F = F - F.mean(axis=0)
    F_pinv = np.linalg.pinv(F)
    p = preds["y_pred"].values.astype(float)
    p_proj = F @ (F_pinv @ p)
    p_neut = p - proportion * p_proj
    if p_neut.std() > 0:
        p_neut = p_neut / p_neut.std()
    rho = spearmanr(preds["y_true"].values, p_neut).statistic
    return float(rho)


def cpcv_stability(preds: pd.DataFrame, n_subsets: int = 200,
                   k_per_subset: int = 4, seed: int = 7) -> dict:
    """Sample n_subsets random k_per_subset folds from the available folds.
    For each subset, compute per-day mean predicted-rank vs realized-rank
    Sharpe. Report distribution.
    """
    rng = np.random.default_rng(seed)
    folds = preds["fold"].unique()
    # Guard against degenerate sampling: if folds <= k_per_subset, every
    # "random subset" is forced to be the same set, which makes the
    # 200-sample distribution one number repeated 200 times. Doc 135's
    # Run 1 hit this case (4 folds, k=4) and reported a fake distribution.
    if len(folds) <= k_per_subset + 1:
        return {"error": f"need >={k_per_subset+2} folds for non-degenerate "
                         f"sampling; got {len(folds)} (Run 1 of doc 135 had this bug)"}
    sharpes = []
    for _ in range(n_subsets):
        sample = rng.choice(folds, size=k_per_subset, replace=False)
        sub = preds[preds["fold"].isin(sample)].copy()
        # P&L proxy: long top-quintile by prediction each day
        per_day = sub.groupby("d0")
        daily = []
        for d, g in per_day:
            if len(g) < 3:
                continue
            top = g.nlargest(max(1, len(g) // 5), "y_pred")
            daily.append(top["y_true"].mean())
        if len(daily) < 5:
            continue
        arr = np.array(daily, dtype=float)
        if arr.std() > 0:
            sharpes.append(arr.mean() / arr.std() * np.sqrt(252))
    if not sharpes:
        return {"error": "no valid subsets"}
    sharpes = np.array(sharpes)
    return {
        "mean": float(sharpes.mean()),
        "std": float(sharpes.std()),
        "p05": float(np.percentile(sharpes, 5)),
        "p50": float(np.percentile(sharpes, 50)),
        "p95": float(np.percentile(sharpes, 95)),
        "frac_positive": float((sharpes > 0).mean()),
        "n_valid_subsets": int(len(sharpes)),
    }


def deflated_sharpe(preds: pd.DataFrame, n_trials: int = 1) -> dict:
    """DSR per Bailey-LdP JPM 2014. n_trials=1 for the single-config control."""
    from scipy.stats import norm

    per_day = preds.groupby("d0").apply(
        lambda g: g.nlargest(max(1, len(g) // 5), "y_pred")["y_true"].mean(),
        include_groups=False,
    )
    pnl = per_day.dropna().values
    if len(pnl) < 15:
        return {"error": f"only {len(pnl)} days (need >= 15)"}
    mean = pnl.mean()
    std = pnl.std(ddof=1)
    if std <= 0:
        return {"error": "zero std"}
    sr = mean / std
    sr_ann = sr * np.sqrt(252)
    skew = float(((pnl - mean) ** 3).mean() / std ** 3)
    kurt_ex = float(((pnl - mean) ** 4).mean() / std ** 4 - 3)
    T = len(pnl)
    sigma_sr = np.sqrt((1 - skew * sr + (kurt_ex / 4) * sr ** 2) / (T - 1))
    if n_trials <= 1:
        sr_max = 0.0
    else:
        gamma = 0.5772156649  # Euler-Mascheroni
        sr_max = ((1 - gamma) * norm.ppf(1 - 1 / n_trials) +
                  gamma * norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr - sr_max) / sigma_sr
    dsr = float(norm.cdf(z))
    return {
        "T_days": T,
        "sr_per_period": float(sr),
        "sr_annualized": float(sr_ann),
        "skew": skew,
        "excess_kurtosis": kurt_ex,
        "sigma_sr": float(sigma_sr),
        "n_trials": n_trials,
        "sr_max_under_H0": float(sr_max),
        "z_score": float(z),
        "dsr": dsr,
    }


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v6-path", default=str(DERIVED / "microstructure_v6_pack.parquet"))
    ap.add_argument("--n-folds", type=int, default=12)
    ap.add_argument("--train-days", type=int, default=120,
                    help="train window (default 120 — short to fit the 9-month v6 window)")
    ap.add_argument("--test-days", type=int, default=15)
    args = ap.parse_args()
    v6_path = Path(args.v6_path)
    if not v6_path.exists():
        print(f"v6 pack not found: {v6_path}")
        return 2

    section("STEP 1 — load v3 + v6 features (inner-join on v6 coverage)")
    X_v3, X_v3_v6, y, d0 = load_v3_plus_v6(v6_path)
    print(f"  X_v3:    {X_v3.shape}")
    print(f"  X_v3_v6: {X_v3_v6.shape}  (+{X_v3_v6.shape[1] - X_v3.shape[1]} v6 cols)")

    section("STEP 2 — train CONTROL (v3-only, default XGB, 16-fold WF)")
    t0 = time.time()
    preds_ctrl = walk_forward_xgb(X_v3, y, d0, n_folds=args.n_folds,
                                   train_window_days=args.train_days,
                                   test_window_days=args.test_days)
    print(f"  control finished in {time.time()-t0:.1f}s, {len(preds_ctrl):,} OOS rows")

    section("STEP 3 — train TREATMENT (v3+v6, default XGB, 16-fold WF)")
    t0 = time.time()
    preds_trt = walk_forward_xgb(X_v3_v6, y, d0, n_folds=args.n_folds,
                                   train_window_days=args.train_days,
                                   test_window_days=args.test_days)
    print(f"  treatment finished in {time.time()-t0:.1f}s, {len(preds_trt):,} OOS rows")

    section("STEP 4 — build neutralization exposures (sector+cap+intraday)")
    # Re-load v3 base data to get the exposure columns
    from ml_continuer_v2_ensemble import load_data  # type: ignore
    df_full = load_data(include_paths=True)
    df_full["d0"] = pd.to_datetime(df_full["d0"])
    v6 = pd.read_parquet(v6_path)
    v6["d0"] = pd.to_datetime(v6["d0"])
    merged = df_full.merge(v6[["ticker", "d0"]], on=["ticker", "d0"], how="inner")
    exposures = pd.DataFrame({
        "log_market_cap": np.log1p(merged.get("market_cap", pd.Series(np.zeros(len(merged)))).fillna(0)),
        "log_dvol_d0":    np.log1p(merged.get("dvol_d0", pd.Series(np.zeros(len(merged)))).fillna(0)),
        "prior_avg_t5":   merged.get("prior_avg_t5", pd.Series(np.zeros(len(merged)))).fillna(0),
        "intraday_pct":   merged.get("intraday_pct", pd.Series(np.zeros(len(merged)))).fillna(0),
    })
    print(f"  exposures: {exposures.shape}")

    # Map exposures by _idx into preds (preds carry _idx that points into merged)
    def neut_for_preds(preds: pd.DataFrame) -> float:
        idx = preds["_idx"].values
        exp_aligned = exposures.iloc[idx].reset_index(drop=True)
        return neutralized_rho(preds.reset_index(drop=True), exp_aligned, 1.0)

    section("STEP 5 — compute all metrics")
    raw_ctrl = float(spearmanr(preds_ctrl["y_true"], preds_ctrl["y_pred"]).statistic)
    raw_trt  = float(spearmanr(preds_trt["y_true"],  preds_trt["y_pred"]).statistic)
    neut_ctrl = neut_for_preds(preds_ctrl)
    neut_trt  = neut_for_preds(preds_trt)
    cpcv_ctrl = cpcv_stability(preds_ctrl)
    cpcv_trt  = cpcv_stability(preds_trt)
    dsr_ctrl  = deflated_sharpe(preds_ctrl, n_trials=1)
    dsr_trt   = deflated_sharpe(preds_trt,  n_trials=1)

    print()
    print("                          control (v3)   treatment (v3+v6)   delta")
    print(f"  raw Spearman          {raw_ctrl:>+12.4f}      {raw_trt:>+12.4f}    {raw_trt-raw_ctrl:>+8.4f}")
    print(f"  100%-neutralized rho  {neut_ctrl:>+12.4f}      {neut_trt:>+12.4f}    {neut_trt-neut_ctrl:>+8.4f}")
    print(f"  DSR                   {dsr_ctrl.get('dsr',float('nan')):>12.4f}      {dsr_trt.get('dsr',float('nan')):>12.4f}")
    print(f"  CPCV frac>0           {cpcv_ctrl.get('frac_positive',float('nan')):>12.2%}      {cpcv_trt.get('frac_positive',float('nan')):>12.2%}")
    print(f"  CPCV mean Sharpe      {cpcv_ctrl.get('mean',float('nan')):>+12.3f}      {cpcv_trt.get('mean',float('nan')):>+12.3f}")

    section("STEP 6 — persist")
    out_json = MODELS / "v6_phase1_validation.json"
    out_json.write_text(json.dumps({
        "n_rows": int(len(preds_ctrl)),
        "n_features_v3": int(X_v3.shape[1]),
        "n_features_v3_v6": int(X_v3_v6.shape[1]),
        "raw_spearman_control": raw_ctrl,
        "raw_spearman_treatment": raw_trt,
        "neutralized_rho_control": neut_ctrl,
        "neutralized_rho_treatment": neut_trt,
        "delta_neutralized_rho": neut_trt - neut_ctrl,
        "cpcv_control": cpcv_ctrl,
        "cpcv_treatment": cpcv_trt,
        "dsr_control": dsr_ctrl,
        "dsr_treatment": dsr_trt,
    }, indent=2, default=str))
    preds_ctrl.to_parquet(DERIVED / "ml_v6_phase1_control_preds.parquet", compression="zstd")
    preds_trt.to_parquet(DERIVED / "ml_v6_phase1_treatment_preds.parquet", compression="zstd")
    print(f"  -> {out_json}")
    print(f"  -> ml_v6_phase1_control_preds.parquet")
    print(f"  -> ml_v6_phase1_treatment_preds.parquet")

    section("DECISION")
    delta = neut_trt - neut_ctrl
    cpcv_ok = cpcv_trt.get("frac_positive", 0) >= 0.90
    dsr_ok = dsr_trt.get("dsr", 0) >= 0.5
    rho_ok = delta >= 0.01
    if rho_ok and cpcv_ok and dsr_ok:
        print(f"  PASS — v6 microstructure pack improves neutralized rho by "
              f"{delta:+.4f} with stable CPCV ({cpcv_trt['frac_positive']:.0%}) "
              f"and DSR={dsr_trt['dsr']:.3f}. Recommend wiring into v3 retraining loop.")
    else:
        why = []
        if not rho_ok:  why.append(f"neutralized rho delta {delta:+.4f} < 0.01")
        if not cpcv_ok: why.append(f"CPCV frac>0 {cpcv_trt.get('frac_positive',0):.0%} < 90%")
        if not dsr_ok:  why.append(f"DSR {dsr_trt.get('dsr',0):.3f} < 0.5")
        print(f"  FAIL — {'; '.join(why)}. v6 pack stays out of production.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
