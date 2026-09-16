"""DISCRIMINATOR REVERSE-ENGINEERING — what features does TabPFN see that v3 misses?

Per doc 144 § Exp #3 pre-commit (now strengthened by ticker-alignment fix):

  Defensive overlay: v3 trades filtered by TabPFN-agreement get +5.70 pp
  per pick (mean ret_t5 +9.40% vs +3.70% baseline).

  "TabPFN trades, v3 SKIPS" picks: mean ret_t5 +2.52% (vs all-skip baseline
  -6.88%). 617 OOS examples where TabPFN's prediction was high enough for
  top-quintile but v3's binary classifier said skip.

The user's framing:
  "If we can engineer the discriminating feature into v3's input space,
  we get the alpha without deploying TabPFN at all (no license, no
  inference cost)."

EXPERIMENT:
  1. Take the merged set: 3,577 rows where we have both TabPFN pred and
     v3 BROAD specialist prob, on the same (ticker, d0).
  2. Compute residual = TabPFN's relative-rank-per-day - v3's relative-rank-per-day.
     (Normalize both to [0, 1] within each day so they're comparable.)
  3. Train XGBoost on v3's 54 engineered features to predict the residual.
     The features that explain the residual ARE the discriminator.
  4. SHAP values on this XGBoost reveal:
     - Which v3 features matter most for explaining TabPFN's distinct view
     - Compare to v3's own feature importance: which features are
       UNDER-weighted by v3 relative to how TabPFN uses them?
  5. For each top discriminator feature, check standalone Spearman vs
     ret_t5 — is it a real signal v3 isn't capturing well?

DECISION RULES:
  - If XGBoost on residual achieves R² > 0.3: TabPFN's edge is largely
    explainable from v3 features. Engineer those features (interactions,
    transforms) into v3's input space.
  - If R² < 0.1: TabPFN is using something fundamental to in-context
    learning that XGBoost can't capture. Must deploy TabPFN itself.
  - If 0.1 ≤ R² ≤ 0.3: partial — some discriminator features identifiable,
    but TabPFN's architecture also matters.

USAGE:
  python scripts/ml_v6_tabpfn_discriminator.py
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


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def per_day_rank_pct(values: pd.Series, dates: pd.Series) -> pd.Series:
    """Per-day percentile rank (0 to 1, where 1 = highest)."""
    df = pd.DataFrame({"v": values.values, "d0": dates.values}, index=values.index)
    return df.groupby("d0")["v"].rank(pct=True, method="average")


def main() -> int:
    section("STEP 1 — load clean (recovered) TabPFN preds + v3 + base features")
    tabpfn = pd.read_parquet(DERIVED / "ml_v6_item5_tabpfn_preds_RECOVERED.parquet")
    v3 = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    tabpfn["d0"] = pd.to_datetime(tabpfn["d0"])
    v3["d0"] = pd.to_datetime(v3["d0"])

    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])

    # Inner-join clean TabPFN + v3 on (ticker, d0)
    m = tabpfn.merge(v3[["ticker", "d0", "y_reg", "prob_specialist_BROAD"]],
                     on=["ticker", "d0"])
    print(f"  TabPFN ⋈ v3 on (ticker, d0): {len(m):,} rows")

    # Now join base features. Use (ticker, d0) join to base data + engineer features.
    # We need v3 features for THIS subset only.
    base_join = m[["ticker", "d0"]].merge(df, on=["ticker", "d0"])
    print(f"  ⋈ base data: {len(base_join):,} rows")
    X = engineer_features(base_join).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    print(f"  v3 features: {X.shape}")

    # Reset all to common index order; trust the join order
    m = m.reset_index(drop=True)

    section("STEP 2 — compute discriminator target: TabPFN-rank-per-day minus v3-rank-per-day")
    # Both predictions normalized to per-day percentile rank for comparability
    m["tabpfn_rank"] = per_day_rank_pct(m["y_pred"], m["d0"])
    m["v3_rank"] = per_day_rank_pct(m["prob_specialist_BROAD"], m["d0"])
    m["residual"] = m["tabpfn_rank"] - m["v3_rank"]
    print(f"  residual stats: mean={m['residual'].mean():+.4f}  std={m['residual'].std():.4f}")
    print(f"  range: [{m['residual'].min():+.3f}, {m['residual'].max():+.3f}]")
    print(f"  rows where TabPFN ranks STRICTLY HIGHER than v3 (residual > 0.2): "
          f"{(m['residual'] > 0.2).sum()}")
    print(f"  rows where v3 ranks STRICTLY HIGHER than TabPFN (residual < -0.2): "
          f"{(m['residual'] < -0.2).sum()}")

    section("STEP 3 — train XGBoost to predict the residual from v3 features")
    import xgboost as xgb
    from sklearn.model_selection import KFold

    PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1, "random_state": 42,
    }
    # 5-fold CV to avoid overfitting; we want OOS R² as the "explainability" score
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    y_resid = m["residual"].values
    oos_pred = np.zeros(len(m))
    for fold_i, (train_idx, test_idx) in enumerate(kf.split(X)):
        model = xgb.XGBRegressor(**PARAMS)
        model.fit(X.iloc[train_idx], y_resid[train_idx], verbose=False)
        oos_pred[test_idx] = model.predict(X.iloc[test_idx])
    # Compute R² on OOS predictions
    ss_res = float(((y_resid - oos_pred) ** 2).sum())
    ss_tot = float(((y_resid - y_resid.mean()) ** 2).sum())
    oos_r2 = 1 - ss_res / ss_tot
    oos_corr = float(spearmanr(y_resid, oos_pred).statistic)
    print(f"  OOS R² (XGBoost predicting TabPFN-v3 residual from v3 features): {oos_r2:.4f}")
    print(f"  OOS Spearman: {oos_corr:.4f}")

    if oos_r2 > 0.30:
        explainability = "HIGH — TabPFN's edge is largely explainable from v3 features"
    elif oos_r2 > 0.10:
        explainability = "PARTIAL — some explainable, but TabPFN's architecture also matters"
    else:
        explainability = "LOW — TabPFN uses something v3 features can't represent"
    print(f"  -> {explainability}")

    section("STEP 4 — feature importance: which v3 features explain the residual?")
    # Refit on full data for stable feature importance
    full_model = xgb.XGBRegressor(**PARAMS)
    full_model.fit(X, y_resid, verbose=False)
    importances = pd.DataFrame({
        "feature": X.columns,
        "importance": full_model.feature_importances_,
    }).sort_values("importance", ascending=False)
    print("  Top 15 features by importance for predicting (TabPFN_rank - v3_rank):")
    print(importances.head(15).to_string(index=False))

    # Compute SHAP values for top features (sample for speed)
    print()
    print("  Computing SHAP for top discriminator features (sample of 1000 rows)...")
    import shap
    rng = np.random.default_rng(42)
    sample_idx = rng.choice(len(X), min(1000, len(X)), replace=False)
    explainer = shap.TreeExplainer(full_model)
    shap_values = explainer.shap_values(X.iloc[sample_idx])
    shap_mean_abs = pd.DataFrame({
        "feature": X.columns,
        "mean_abs_shap": np.abs(shap_values).mean(axis=0),
    }).sort_values("mean_abs_shap", ascending=False)
    print("  Top 10 features by mean(|SHAP|) on residual:")
    print(shap_mean_abs.head(10).to_string(index=False))

    section("STEP 5 — for top discriminator features, standalone Spearman vs ret_t5")
    # Take top 10 from importance
    top_feats = importances.head(10)["feature"].tolist()
    print(f"  Standalone Spearman of feature vs realized ret_t5 (n={len(m):,}):")
    print(f"  {'feature':<25} {'Spearman':>10}  flag")
    feat_results = []
    for f in top_feats:
        if f not in X.columns:
            continue
        rho = float(spearmanr(X[f], m["y_reg"]).statistic)
        flag = "POSITIVE alpha" if rho > 0.05 else ("NEG alpha" if rho < -0.05 else "weak")
        feat_results.append({"feature": f, "standalone_spearman": rho, "flag": flag})
        print(f"  {f:<25} {rho:>+10.4f}  {flag}")

    section("STEP 6 — compare to v3 BROAD specialist's own feature importance")
    # Train XGBoost to predict v3's BROAD-specialist y_cls (binary ret_t5 >= 0.10)
    # This shows what v3 ALREADY weights
    y_v3_target = (m["y_reg"] >= 0.10).astype(int).values
    v3_model = xgb.XGBClassifier(
        objective="binary:logistic", n_estimators=600, max_depth=5,
        learning_rate=0.03, subsample=0.8, colsample_bytree=0.6,
        min_child_weight=5, reg_alpha=0.5, reg_lambda=1.0,
        tree_method="hist", device="cpu", n_jobs=1, random_state=42,
        eval_metric="logloss",
    )
    v3_model.fit(X, y_v3_target, verbose=False)
    v3_imp = pd.DataFrame({
        "feature": X.columns,
        "v3_importance": v3_model.feature_importances_,
    })
    # Join importances side-by-side
    cmp = importances.merge(v3_imp, on="feature").sort_values("importance", ascending=False)
    cmp["under_weighted_by_v3"] = (
        (cmp["importance"] > 0.02) & (cmp["v3_importance"] < cmp["importance"] * 0.5)
    )
    print("  Side-by-side: (residual importance) vs (v3 BROAD-spec importance)")
    print(f"  {'feature':<25} {'res_imp':>9} {'v3_imp':>9} {'underweighted':>14}")
    for _, r in cmp.head(15).iterrows():
        flag = "<-- ENGINEER" if r["under_weighted_by_v3"] else ""
        print(f"  {r['feature']:<25} {r['importance']:>9.4f} {r['v3_importance']:>9.4f} "
              f"{flag:>14}")

    # The candidate features for engineering: top-importance for residual AND
    # under-weighted by v3 AND positive standalone alpha
    candidates = []
    for _, r in cmp.iterrows():
        if not r["under_weighted_by_v3"]:
            continue
        feat_name = r["feature"]
        feat_rho = next((f["standalone_spearman"] for f in feat_results if f["feature"] == feat_name), 0.0)
        if feat_rho > 0.05 or feat_rho < -0.05:
            candidates.append({
                "feature": feat_name,
                "discriminator_importance": float(r["importance"]),
                "v3_importance": float(r["v3_importance"]),
                "standalone_spearman": feat_rho,
            })
    print()
    print(f"  CANDIDATE DISCRIMINATOR FEATURES (under-weighted by v3 + standalone alpha):")
    if candidates:
        for c in candidates:
            print(f"    {c['feature']:<25} disc_imp={c['discriminator_importance']:.4f}  "
                  f"v3_imp={c['v3_importance']:.4f}  standalone_rho={c['standalone_spearman']:+.3f}")
    else:
        print("    (none meeting both criteria)")

    section("STEP 7 — verdict")
    print(f"  OOS R² (residual explainability):  {oos_r2:.4f}")
    print(f"  -> {explainability}")
    print()
    if oos_r2 > 0.30 and len(candidates) > 0:
        print(f"  ACTIONABLE: {len(candidates)} candidate features identified.")
        print(f"     Engineer them as v3 BROAD specialist additions or interactions.")
        print(f"     Target: replicate TabPFN's edge using v3-native features.")
    elif oos_r2 > 0.10:
        print(f"  PARTIAL: TabPFN's edge is partially explainable, but feature engineering")
        print(f"     alone won't capture it all. Combine: add candidate features to v3,")
        print(f"     keep TabPFN as defensive overlay for the remaining gap.")
    else:
        print(f"  NOT ACTIONABLE via feature engineering: TabPFN's edge appears to be")
        print(f"     architectural (in-context learning) rather than feature-explainable.")
        print(f"     Must deploy TabPFN to capture the lift.")

    out = {
        "n_rows": int(len(m)),
        "oos_r2": float(oos_r2),
        "oos_spearman": float(oos_corr),
        "explainability_label": explainability,
        "top10_importance": importances.head(10).to_dict(orient="records"),
        "top10_shap": shap_mean_abs.head(10).to_dict(orient="records"),
        "standalone_spearman_top": feat_results,
        "candidates_for_engineering": candidates,
    }
    out_path = MODELS / "v6_tabpfn_discriminator.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
