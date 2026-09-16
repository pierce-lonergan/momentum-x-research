"""ITEM 5b — TabICL on v3 features: open-weights substitute for the
blocked TabPFNv2 ceiling test (Item 5).

TabPFN ships under priorlabs.ai license requiring OAuth handshake; can't
complete from a background script. TabICL (arXiv 2602.11139, Mar 2026)
is in the same tabular-foundation-model family (in-context learning,
attention over training set, pretrained on synthetic tabular tasks).
Open-weights from HuggingFace `jingang/TabICL`. No OAuth.

Same decision rule as user proposed for TabPFN:
  - Spearman >= 0.18  -> MODEL-CLASS CEILING (foundation model can break v3)
  - Spearman ~0.14-0.16 -> BAYES CEILING (v3 is the universe maximum)

Caveat: TabICL is not TabPFNv2. If TabICL underperforms, that doesn't
*conclusively* close the model-class question — there may still be a
TabPFNv2-specific advantage. But it's the closest open-weights proxy
and removes the OAuth blocker. If the user later runs TabPFNv2 itself,
that result stays primary.

Same WF setup as Item 1's CPU control: 12 folds, 120d train, 15d test.

USAGE:
  python scripts/ml_v6_item5b_tabicl_ceiling.py
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


def build_exposures(merged: pd.DataFrame) -> pd.DataFrame:
    n = len(merged)
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
    section("STEP 1 — load v3 base data + engineer features")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  loaded {len(df):,} rows")
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  X: {X.shape}  y: {y.shape}  d0 range: {d0.min().date()} -> {d0.max().date()}")

    section("STEP 2 — 12-fold WF: TabICL regression vs ret_t5")
    from tabicl import TabICLRegressor

    n_folds = 12
    train_days = 120
    test_days = 15
    d_min, d_max = d0.min(), d0.max()
    fold_starts = pd.date_range(
        d_min + pd.Timedelta(days=train_days),
        d_max - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  {n_folds} folds, train={train_days}d, test={test_days}d")
    print()

    rows = []
    max_train = 8000  # TabICL handles up to ~10K context; 8K leaves headroom
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
        if len(Xtr) > max_train:
            rng = np.random.default_rng(42 + fold_i)
            idx = rng.choice(len(Xtr), max_train, replace=False)
            Xtr = Xtr[idx]
            ytr = ytr[idx]

        t0 = time.time()
        try:
            model = TabICLRegressor(device="cuda")
            model.fit(Xtr, ytr)
            pred = model.predict(Xte)
        except Exception as e:
            print(f"  fold {fold_i:>2}: TabICL error: {e}")
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

    section("STEP 3 — aggregate metrics")
    preds = pd.concat(rows, ignore_index=True)
    rho_overall = float(spearmanr(preds["y_true"], preds["y_pred"]).statistic)
    print(f"  TabICL OOS rows: {len(preds):,}")
    print(f"  TabICL overall Spearman: {rho_overall:+.4f}")

    exp = build_exposures(df)
    exp_aligned = exp.iloc[preds["_idx"].values].reset_index(drop=True)
    rho_neut = neutralized_rho(preds["y_pred"].values, preds["y_true"].values,
                                exp_aligned, 1.0)
    print(f"  TabICL 100%-neutralized rho: {rho_neut:+.4f}")

    section("STEP 4 — v3 XGBoost CPU baseline on SAME folds (apples-to-apples)")
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
    print(f"    TabICL Spearman:       {rho_overall:+.4f}")
    print(f"    v3 XGBoost Spearman:   {xgb_rho:+.4f}")
    print(f"    Delta:                 {rho_overall - xgb_rho:+.4f}")
    print(f"    TabICL neutralized:    {rho_neut:+.4f}")
    print(f"    v3 XGBoost neut:       {xgb_neut:+.4f}")
    print(f"    Delta neutralized:     {rho_neut - xgb_neut:+.4f}")

    section("STEP 5 — verdict per user's TabPFN decision rule (TabICL substitute)")
    print(f"  Threshold: foundation-model Spearman >= 0.18 -> model-class ceiling")
    print(f"  TabICL raw Spearman: {rho_overall:+.4f}")
    print()
    if rho_overall >= 0.18:
        print(f"  -> MODEL-CLASS CEILING ({rho_overall:.4f} >= 0.18)")
        print(f"  -> Foundation models can break v3. Architecture exploration valid.")
        print(f"  -> Caveat: TabICL is not TabPFNv2. User should still run TabPFNv2 if available.")
    elif rho_overall >= 0.16:
        print(f"  -> AMBIGUOUS ({rho_overall:.4f} in [0.16, 0.18))")
        print(f"  -> TabICL marginal; TabPFNv2 might be different.")
    else:
        print(f"  -> SUPPORTS BAYES CEILING ({rho_overall:.4f} < 0.16)")
        print(f"  -> v3 is at or near the universe ceiling for this data scale.")
        print(f"  -> Pivot to deployment quality (capacity / slippage / sizing / drawdown).")
        print(f"  -> CAVEAT: TabPFNv2 might still beat this. Open question pending license.")

    out = {
        "model": "TabICL (open-weights substitute for TabPFN)",
        "tabicl_overall_spearman": rho_overall,
        "tabicl_neutralized_rho": rho_neut,
        "tabicl_n_oos_rows": int(len(preds)),
        "v3_xgb_overall_spearman": xgb_rho,
        "v3_xgb_neutralized_rho": xgb_neut,
        "v3_xgb_n_oos_rows": int(len(xgb_preds)),
        "delta_spearman": rho_overall - xgb_rho,
        "delta_neutralized": rho_neut - xgb_neut,
        "supports_modelclass_ceiling": rho_overall >= 0.18,
        "supports_bayes_ceiling": rho_overall < 0.16,
    }
    out_path = MODELS / "v6_item5b_tabicl_ceiling.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    preds.to_parquet(DERIVED / "ml_v6_item5b_tabicl_preds.parquet", compression="zstd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
