"""ITEM 5 — TabPFNv2 on v3 features: Bayes ceiling vs model-class ceiling.

Per user critique on doc 138 (and originally proposed in doc 131 § 5):

  "TabPFNv2 on v3 features only, no distillation. Determines whether
  the 0.14-0.16 Spearman ceiling is Bayes (need new data) or
  model-class (need new architecture). This experiment has been
  sitting on the to-do list across two architecture iterations."

Decision threshold (from user):
  - TabPFNv2 Spearman >= 0.18  -> model-class ceiling, foundation
                                  models could break it. v3 is NOT
                                  the ceiling.
  - TabPFNv2 Spearman ~0.14-0.16 -> Bayes ceiling. v3 IS the ceiling
                                    for this universe at this label
                                    horizon. Pivot to deployment.

This script:
  1. Loads v3 base data (no v6 features)
  2. Same 12-fold WF as Item 1's CPU validation (120d train, 15d test)
  3. For each fold: train TabPFNRegressor on train, predict on test
  4. Aggregate OOS preds, compute overall Spearman + neutralized rho
  5. Compare to v3 XGBoost CPU-deterministic baseline (Item 1)
  6. Verdict based on the 0.18 threshold

DOC 138 RULE: verdict section in the doc stays blank until results land.

USAGE:
  python scripts/ml_v6_item5_tabpfn_ceiling.py
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def neutralized_rho(y_pred: np.ndarray, y_true: np.ndarray,
                    exposures: pd.DataFrame, proportion: float = 1.0) -> float:
    F = exposures.values.astype(float)
    F = F - F.mean(axis=0)
    F_pinv = np.linalg.pinv(F)
    p = y_pred.astype(float)
    p_proj = F @ (F_pinv @ p)
    p_neut = p - proportion * p_proj
    if p_neut.std() > 0:
        p_neut = p_neut / p_neut.std()
    return float(spearmanr(y_true, p_neut).statistic)


def build_exposures(merged: pd.DataFrame, n: int) -> pd.DataFrame:
    exp = pd.DataFrame({
        "log_market_cap": np.log1p(merged.get("market_cap", pd.Series(np.zeros(n))).fillna(0)),
        "log_dvol_d0":    np.log1p(merged.get("dvol_d0", pd.Series(np.zeros(n))).fillna(0)),
        "prior_avg_t5":   merged.get("prior_avg_t5", pd.Series(np.zeros(n))).fillna(0),
        "intraday_pct":   merged.get("intraday_pct", pd.Series(np.zeros(n))).fillna(0),
    })
    if "sic_description" in merged.columns:
        sic = merged["sic_description"].fillna("").str.lower()
        for sec, key in [("pharma", "pharm"), ("bio", "bio"),
                          ("medical", "medic"), ("software", "software"),
                          ("finance", "financ"), ("semi", "semicond"),
                          ("spac", "spac"), ("reit", "reit")]:
            exp[f"sector_{sec}"] = sic.str.contains(key).astype(float)
    return exp


def main() -> int:
    section("STEP 1 — load v3 base data + engineer features (no v6 join)")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  loaded {len(df):,} rows")
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  X: {X.shape}  y: {y.shape}  d0 range: {d0.min().date()} -> {d0.max().date()}")

    section("STEP 2 — 12-fold WF: TabPFNv2 regression vs ret_t5")
    # TabPFN constraints: <=10000 train rows is the sweet spot. Our 120d-train
    # window is typically 2-5K rows after filtering. Fits cleanly.
    from tabpfn import TabPFNRegressor

    n_folds = 12
    train_days = 120
    test_days = 15
    d_min, d_max = d0.min(), d0.max()
    fold_starts = pd.date_range(
        d_min + pd.Timedelta(days=train_days),
        d_max - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  {n_folds} folds, train={train_days}d, test={test_days}d")
    print(f"  fold_starts: {fold_starts[0].date()} ... {fold_starts[-1].date()}")
    print()

    rows = []
    for fold_i, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5:
            print(f"  fold {fold_i:>2}: skip (tr={tr.sum()}, te={te.sum()})")
            continue
        Xtr = X.loc[tr].values
        ytr = y.loc[tr].values
        Xte = X.loc[te].values
        yte = y.loc[te].values

        # TabPFN limit: ~10K samples. Subsample if needed.
        max_train = 10000
        if len(Xtr) > max_train:
            rng = np.random.default_rng(42 + fold_i)
            idx = rng.choice(len(Xtr), max_train, replace=False)
            Xtr_use = Xtr[idx]
            ytr_use = ytr[idx]
        else:
            Xtr_use = Xtr
            ytr_use = ytr

        t0 = time.time()
        try:
            model = TabPFNRegressor(device="cuda", random_state=42)
            model.fit(Xtr_use, ytr_use)
            pred = model.predict(Xte)
        except Exception as e:
            print(f"  fold {fold_i:>2}: TabPFN error: {e}")
            continue
        elapsed = time.time() - t0
        rho_fold = float(spearmanr(yte, pred).statistic)
        rows.append(pd.DataFrame({
            "fold": fold_i, "d0": d0.loc[te].values,
            "y_true": yte, "y_pred": pred,
            "_idx": np.where(te)[0],
        }))
        print(f"  fold {fold_i:>2}: tr={tr.sum():>5}  te={te.sum():>4}  "
              f"Spearman={rho_fold:+.3f}  ({elapsed:.0f}s)", flush=True)

    if not rows:
        print("  no successful folds — abort")
        return 1

    section("STEP 3 — aggregate metrics + compare to v3 XGBoost CPU baseline")
    preds = pd.concat(rows, ignore_index=True)
    rho_overall = float(spearmanr(preds["y_true"], preds["y_pred"]).statistic)
    print(f"  TabPFNv2 OOS rows: {len(preds):,}")
    print(f"  TabPFNv2 overall Spearman: {rho_overall:+.4f}")

    # Build exposures from base df, indexed by _idx
    exp = build_exposures(df, len(df))
    exp_aligned = exp.iloc[preds["_idx"].values].reset_index(drop=True)
    rho_neut = neutralized_rho(preds["y_pred"].values, preds["y_true"].values,
                                exp_aligned, 1.0)
    print(f"  TabPFNv2 100%-neutralized rho: {rho_neut:+.4f}")

    # Compare to v3 XGBoost CPU baseline (run inline for fair comparison
    # on the SAME folds and SAME data slice)
    print()
    print("  v3 XGBoost CPU control on SAME folds (for apples-to-apples comparison):")
    import xgboost as xgb
    XGB_PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1, "random_state": 42,
    }
    xgb_rows = []
    for fold_i, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5: continue
        m = xgb.XGBRegressor(**XGB_PARAMS)
        m.fit(X.loc[tr], y.loc[tr], verbose=False)
        pred = m.predict(X.loc[te])
        xgb_rows.append(pd.DataFrame({
            "fold": fold_i, "d0": d0.loc[te].values,
            "y_true": y.loc[te].values, "y_pred": pred,
            "_idx": np.where(te)[0],
        }))
    xgb_preds = pd.concat(xgb_rows, ignore_index=True)
    xgb_rho = float(spearmanr(xgb_preds["y_true"], xgb_preds["y_pred"]).statistic)
    xgb_exp = exp.iloc[xgb_preds["_idx"].values].reset_index(drop=True)
    xgb_neut = neutralized_rho(xgb_preds["y_pred"].values, xgb_preds["y_true"].values,
                                 xgb_exp, 1.0)
    print(f"  v3 XGBoost overall Spearman:        {xgb_rho:+.4f}")
    print(f"  v3 XGBoost 100%-neutralized rho:    {xgb_neut:+.4f}")
    print()
    print("  HEAD-TO-HEAD:")
    print(f"    TabPFNv2 Spearman:     {rho_overall:+.4f}")
    print(f"    v3 XGBoost Spearman:   {xgb_rho:+.4f}")
    print(f"    Delta:                 {rho_overall - xgb_rho:+.4f}")
    print()
    print(f"    TabPFNv2 neutralized:  {rho_neut:+.4f}")
    print(f"    v3 XGBoost neut:       {xgb_neut:+.4f}")
    print(f"    Delta neutralized:     {rho_neut - xgb_neut:+.4f}")

    # User's decision rule:
    # >= 0.18 -> model-class ceiling, foundation models can break it
    # ~0.14-0.16 -> Bayes ceiling, v3 IS the ceiling
    section("STEP 4 — verdict per user's pre-stated decision rule")
    print(f"  Threshold from user critique: TabPFNv2 >= 0.18 -> model-class ceiling")
    print(f"  TabPFNv2 raw Spearman: {rho_overall:+.4f}")
    print()
    if rho_overall >= 0.18:
        print(f"  -> MODEL-CLASS CEILING (TabPFNv2 = {rho_overall:.4f} >= 0.18)")
        print(f"  -> Foundation-model approach could break the v3 ceiling")
        print(f"  -> Architecture exploration is still warranted")
    elif rho_overall >= 0.16:
        print(f"  -> AMBIGUOUS ({rho_overall:.4f} in [0.16, 0.18))")
        print(f"  -> Slight TabPFN edge; foundation models marginal")
    else:
        print(f"  -> BAYES CEILING (TabPFNv2 = {rho_overall:.4f} < 0.16)")
        print(f"  -> v3 IS the ceiling for this universe")
        print(f"  -> Pivot to deployment quality (capacity, slippage, sizing, drawdown)")

    out = {
        "tabpfn_overall_spearman": rho_overall,
        "tabpfn_neutralized_rho": rho_neut,
        "tabpfn_n_oos_rows": int(len(preds)),
        "v3_xgb_overall_spearman": xgb_rho,
        "v3_xgb_neutralized_rho": xgb_neut,
        "v3_xgb_n_oos_rows": int(len(xgb_preds)),
        "delta_spearman": rho_overall - xgb_rho,
        "delta_neutralized": rho_neut - xgb_neut,
        "decision_threshold_018": rho_overall >= 0.18,
        "decision_threshold_016": rho_overall >= 0.16,
    }
    out_path = MODELS / "v6_item5_tabpfn_ceiling.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")

    # Also save the OOS predictions for follow-up analysis
    preds.to_parquet(DERIVED / "ml_v6_item5_tabpfn_preds.parquet", compression="zstd")
    xgb_preds.to_parquet(DERIVED / "ml_v6_item5_xgb_preds.parquet", compression="zstd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
