"""v6 Phase 1.5 — combined d0 + d-1 + DELTA microstructure pack.

Hypothesis: pre-news positioning (d-1) and news-day flow (d0) carry
COMPLEMENTARY information. The DELTA features (d0 minus d-1) encode
the REGIME CHANGE between the two days, which may be the strongest
single signal.

Bold play (per user feedback "be epic, be bold"):
  - d-1 features alone: HURT global Spearman, but HELP per-tier P@30
    in BROAD (+0.042) and VETOED (+0.050). Lookahead-safe.
  - d0 features alone: HELP global Spearman (+0.022, p=0.000 noise floor),
    but HURT ELITE precision and lift in middle decile. Lookahead-blocked.
  - Combined d0 + d-1 + (d0 - d-1) — does it stack?

This is a pure feature-engineering test on top of the existing pipeline.
No new training-time tricks. The same 12-fold WF + per-tier diagnostics.

If the combined pack:
  (a) Beats d-1's per-tier lift, AND
  (b) Doesn't hurt ELITE,
  -> recommend production wiring of D-1 + DELTA features (lookahead-safe
     subset; drop d0 columns to keep production safe).

If not, conclude: microstructure has reached its local maximum at d-1
alone, and the next iteration should pivot to news/options/sympathy
features per M.md §1C-§1F.

USAGE:
  python scripts/ml_v6_phase1_combined_d0_dminus1.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))

V6_FEATURES = [
    "vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
    "hawkes_fano_d0", "amihud_illiq_d0", "iso_sweep_count_d0",
]


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def engineer_v6_block(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Engineer one microstructure block (d0 or d_minus_1).
    Adds log-transforms of fat-tailed features."""
    b = df[V6_FEATURES].copy()
    b = b.replace([np.inf, -np.inf], np.nan)
    b["log_kyle_lambda"] = np.log1p(b["kyle_lambda_d0"].clip(lower=0))
    b["log_hawkes_fano"] = np.log1p(b["hawkes_fano_d0"].clip(lower=0))
    b["log_amihud_illiq"] = np.log1p(b["amihud_illiq_d0"].clip(lower=0))
    b["log_iso_sweep_count"] = np.log1p(b["iso_sweep_count_d0"].clip(lower=0))
    b = b.fillna(0)
    return b.add_suffix(f"_{suffix}")


def load_combined() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """Load v3 features + d0 v6 + d-1 v6, build delta features.

    Returns: X_v3, v6_d0_block, v6_dminus1_block, delta_block, y, d0_dates"""
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])

    v6_d0 = pd.read_parquet(DERIVED / "microstructure_v6_pack.parquet")
    v6_d0["d0"] = pd.to_datetime(v6_d0["d0"])
    v6_dm1 = pd.read_parquet(DERIVED / "microstructure_v6_pack_dminus1.parquet")
    v6_dm1["d0"] = pd.to_datetime(v6_dm1["d0"])

    # Inner-join: only keys with BOTH d0 and d-1 features
    merged = (df.merge(v6_d0[["ticker", "d0", *V6_FEATURES]],
                       on=["ticker", "d0"], how="inner",
                       suffixes=("", "_dup_d0_drop"))
                .merge(v6_dm1[["ticker", "d0", *V6_FEATURES]],
                       on=["ticker", "d0"], how="inner",
                       suffixes=("_d0", "_dm1")))
    print(f"  inner-join: {len(merged):,} rows with both d0 + d-1 v6 features")

    X_v3 = engineer_features(merged)
    X_v3 = X_v3.replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)

    # Build d0 block (rename _d0-suffixed cols back to v6_d0 names)
    d0_cols = {f"{c}_d0": c for c in V6_FEATURES}
    d0_df = merged.rename(columns=d0_cols)[V6_FEATURES]
    v6_d0_block = engineer_v6_block(d0_df, "d0")
    # d-1 block
    dm1_cols = {f"{c}_dm1": c for c in V6_FEATURES}
    dm1_df = merged.rename(columns=dm1_cols)[V6_FEATURES]
    v6_dm1_block = engineer_v6_block(dm1_df, "dm1")

    # Delta block: d0 - d-1 for each engineered feature
    delta_block = pd.DataFrame(index=v6_d0_block.index)
    for col in v6_d0_block.columns:
        col_name = col.removesuffix("_d0")
        d0_val = v6_d0_block[col].values
        dm1_val = v6_dm1_block[col_name + "_dm1"].values
        delta_block[col_name + "_delta"] = d0_val - dm1_val
    delta_block = delta_block.fillna(0).reset_index(drop=True)

    y = merged["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(merged["d0"]).reset_index(drop=True)
    return X_v3, v6_d0_block.reset_index(drop=True), v6_dm1_block.reset_index(drop=True), delta_block, y, d0


def walk_forward(X: pd.DataFrame, y: pd.Series, d0: pd.Series,
                 n_folds: int = 12, train_days: int = 120, test_days: int = 15) -> pd.DataFrame:
    import xgboost as xgb
    XGB_PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cuda", "n_jobs": -1, "random_state": 42,
    }
    d_min, d_max = d0.min(), d0.max()
    fold_starts = pd.date_range(
        d_min + pd.Timedelta(days=train_days),
        d_max - pd.Timedelta(days=test_days), periods=n_folds,
    )
    rows = []
    for f, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5: continue
        m = xgb.XGBRegressor(**XGB_PARAMS)
        m.fit(X.loc[tr], y.loc[tr], verbose=False)
        rows.append(pd.DataFrame({
            "fold": f, "d0": d0.loc[te].values,
            "y_true": y.loc[te].values, "y_pred": m.predict(X.loc[te]),
            "_idx": np.where(te)[0],
        }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def per_tier_p30(X: pd.DataFrame, y_reg: pd.Series, d0: pd.Series) -> dict:
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score
    PARAMS = {
        "objective": "binary:logistic", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cuda", "n_jobs": -1,
        "random_state": 42, "eval_metric": "logloss",
    }
    TIERS = {"BROAD": 0.10, "VETOED": 0.15, "HIGH": 0.25, "ELITE": 0.40}
    out = {}
    d_min, d_max = d0.min(), d0.max()
    fs_list = pd.date_range(d_min + pd.Timedelta(days=120),
                             d_max - pd.Timedelta(days=15), periods=12)
    for tier, thr in TIERS.items():
        y_bin = (y_reg >= thr).astype(int)
        aucs, p30s = [], []
        for fs in fs_list:
            tr = (d0 >= fs - pd.Timedelta(days=120)) & (d0 < fs)
            te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=15))
            if tr.sum() < 100 or te.sum() < 30 or y_bin.loc[tr].sum() < 5: continue
            m = xgb.XGBClassifier(**PARAMS)
            m.fit(X.loc[tr], y_bin.loc[tr], verbose=False)
            p = m.predict_proba(X.loc[te])[:, 1]
            if y_bin.loc[te].sum() > 0 and y_bin.loc[te].sum() < te.sum():
                aucs.append(roc_auc_score(y_bin.loc[te], p))
            top_idx = np.argsort(p)[-min(30, te.sum()):]
            p30s.append(y_bin.loc[te].values[top_idx].mean())
        out[tier] = {"thr": thr, "auc": float(np.mean(aucs)), "p30": float(np.mean(p30s))}
    return out


def main() -> int:
    section("LOAD")
    X_v3, b_d0, b_dm1, b_delta, y, d0 = load_combined()
    print(f"  X_v3: {X_v3.shape}")
    print(f"  v6 d0:    {b_d0.shape}")
    print(f"  v6 d-1:   {b_dm1.shape}")
    print(f"  v6 delta: {b_delta.shape}")
    print(f"  y: {y.shape}")

    section("4 FEATURE-SET VARIANTS — compare global Spearman + per-tier P@30")
    variants = {
        "v3_only":               X_v3,
        "v3 + d-1":              pd.concat([X_v3, b_dm1], axis=1),
        "v3 + d-1 + delta":      pd.concat([X_v3, b_dm1, b_delta], axis=1),
        "v3 + d0 + d-1 + delta": pd.concat([X_v3, b_d0, b_dm1, b_delta], axis=1),
    }
    rows = []
    for name, X in variants.items():
        print(f"\n  -> {name}: {X.shape[1]} features")
        preds = walk_forward(X, y, d0)
        rho = float(spearmanr(preds["y_true"], preds["y_pred"]).statistic)
        tier = per_tier_p30(X, y, d0)
        rows.append({
            "variant": name, "n_features": X.shape[1], "spearman": rho,
            "BROAD_p30": tier["BROAD"]["p30"],
            "VETOED_p30": tier["VETOED"]["p30"],
            "HIGH_p30": tier["HIGH"]["p30"],
            "ELITE_p30": tier["ELITE"]["p30"],
            "BROAD_auc": tier["BROAD"]["auc"],
            "VETOED_auc": tier["VETOED"]["auc"],
            "HIGH_auc": tier["HIGH"]["auc"],
            "ELITE_auc": tier["ELITE"]["auc"],
        })

    # Summary table
    section("SUMMARY")
    df_out = pd.DataFrame(rows)
    print()
    print("  variant                  n_feat   Spearman   BROAD_p30  VETOED_p30  HIGH_p30  ELITE_p30")
    print("  " + "-" * 92)
    for _, r in df_out.iterrows():
        print(f"  {r['variant']:<24} {r['n_features']:>6}   {r['spearman']:>+8.4f}    "
              f"{r['BROAD_p30']:>+7.3f}     {r['VETOED_p30']:>+7.3f}    "
              f"{r['HIGH_p30']:>+7.3f}    {r['ELITE_p30']:>+7.3f}")
    print()
    # Deltas vs v3-only baseline
    base = df_out.iloc[0]
    print("  DELTAS vs v3_only:")
    print("  variant                  d_Spear   d_BROAD   d_VETOED   d_HIGH   d_ELITE")
    print("  " + "-" * 80)
    for _, r in df_out.iloc[1:].iterrows():
        print(f"  {r['variant']:<24} {r['spearman']-base['spearman']:>+7.4f}  "
              f"{r['BROAD_p30']-base['BROAD_p30']:>+7.3f}   "
              f"{r['VETOED_p30']-base['VETOED_p30']:>+7.3f}    "
              f"{r['HIGH_p30']-base['HIGH_p30']:>+7.3f}  "
              f"{r['ELITE_p30']-base['ELITE_p30']:>+7.3f}")

    out_json = MODELS / "v6_phase1_combined_d0_dminus1.json"
    out_json.write_text(json.dumps(rows, indent=2, default=str))
    print(f"\n  wrote {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
