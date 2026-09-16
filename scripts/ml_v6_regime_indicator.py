"""Doc 148 — regime-shift indicator engineering (Thread #1 from doc 147 §8.1).

Per doc 147 finding: cascade alpha is regime-dependent. OLDER period
(Jan-Jul 2025) had cascade Sharpe +3.92 with VETOED at -1.36 and HIGH
at +3.83. RECENT period (Aug 2025+) had cascade Sharpe +5.08 with HIGH
at +12.50 and VETOED at +4.36.

The strategic question: can we DETECT the regime in real-time so we
can dynamically gate or weight tier specialists?

HONEST LIMITATION UPFRONT:
  N=2 regimes (OLDER, RECENT) is weak. A direct "what differs between
  the two halves" analysis risks finding spurious correlations
  (anything that happened to differ between Jan-Jul 2025 and Aug 2025-
  Apr 2026 could be confounded with seasonality, election cycles,
  market level changes, etc.).

  The defensible approach: ROLLING MONTHLY WINDOWS (N=16 months) for
  predictive correlation, NOT direct binary OLDER-vs-RECENT comparison.
  16 monthly observations is small but meaningful; 2 windows is not.

THREE EXPERIMENTS:

  Exp A: Monthly cascade Sharpe time series
    - Compute month-by-month cascade Sharpe
    - Look at autocorrelation, trend, change points
    - Can we predict next-month Sharpe from this-month features?

  Exp B: Monthly feature aggregates as cascade-Sharpe predictors
    - For each base feature, compute monthly mean and std
    - Lag by 1 month
    - Spearman with concurrent + lagged cascade Sharpe
    - Top-N features become candidate regime gates

  Exp C: OLDER vs RECENT discriminator (defensive comparison)
    - Train binary classifier "which period?" on engineered features
    - Feature importance shows what structurally differs
    - Compare to Exp B findings: features that pass BOTH tests are
      strong regime-indicator candidates; features that pass only Exp C
      are likely spurious

PRE-COMMITTED VERDICTS:
  Strong indicator found: |Spearman(monthly_feature, next_month_Sharpe)|
    >= 0.5 with same-sign for both concurrent and lagged
    -> propose as v3 feature OR launcher gate
  No indicator found: max |Spearman| < 0.3 across all features
    -> regime detection is infeasible from current features;
       file as definitive negative result
  Marginal: 0.3 <= max |Spearman| < 0.5
    -> document as inconclusive; don't deploy on weak signal
"""
from __future__ import annotations
import json
import math
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


def assign_tier_absolute(prob: pd.Series) -> pd.Series:
    tier = pd.Series(["SKIP"] * len(prob), index=prob.index, dtype=object)
    tier.loc[prob >= 0.30] = "BROAD"
    tier.loc[prob >= 0.40] = "VETOED"
    tier.loc[prob >= 0.50] = "HIGH"
    tier.loc[prob >= 0.60] = "ELITE"
    return tier


def main() -> int:
    section("LOAD")
    v3 = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    v3["d0"] = pd.to_datetime(v3["d0"])
    v3["tier"] = assign_tier_absolute(v3["prob_specialist_BROAD"])
    print(f"  v3: {len(v3):,} rows, {v3['d0'].min().date()} to {v3['d0'].max().date()}")

    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  base data: {len(df):,} rows")

    # ─────────────────────────────────────────────────────────────────
    section("EXP A — monthly cascade Sharpe time series")
    cascade = v3[v3["tier"] != "SKIP"].copy()
    cascade["year_month"] = cascade["d0"].dt.to_period("M")
    daily_pnl = cascade.groupby(["year_month", "d0"])["y_reg"].mean().reset_index()
    monthly = daily_pnl.groupby("year_month")["y_reg"].agg(
        mean="mean", std="std", count="count"
    ).reset_index()
    monthly["sharpe_ann"] = (monthly["mean"] / monthly["std"]
                              * math.sqrt(252)).where(monthly["std"] > 0, 0)
    monthly["year_month"] = monthly["year_month"].astype(str)
    print(f"  monthly cascade Sharpe (n={len(monthly)} months):")
    print(f"  {'month':<10} {'n_days':>7} {'mean':>8} {'std':>7} {'sharpe_ann':>11}")
    for _, r in monthly.iterrows():
        print(f"  {r['year_month']:<10} {r['count']:>7} "
              f"{r['mean']*100:>+7.2f}% {r['std']*100:>6.2f}% {r['sharpe_ann']:>+10.2f}")

    # Autocorrelation: does this month's Sharpe predict next month's?
    sh = monthly["sharpe_ann"].values
    ac1 = slope = r_val = None  # pre-bound: assigned conditionally below
    if len(sh) >= 3:
        ac1 = float(np.corrcoef(sh[:-1], sh[1:])[0, 1]) if len(sh) >= 2 else float("nan")
        print(f"\n  Lag-1 autocorrelation of monthly Sharpe: {ac1:+.3f}")
    # Linear trend: is RECENT just a slow ramp or a regime jump?
    if len(sh) >= 3:
        from scipy.stats import linregress
        x = np.arange(len(sh))
        slope, intercept, r_val, _, _ = linregress(x, sh)
        print(f"  Linear trend: slope = {slope:+.3f}/month, R² = {r_val**2:.3f}")

    monthly_data = monthly[["year_month", "sharpe_ann"]].copy()
    monthly_data["month_idx"] = range(len(monthly_data))

    # ─────────────────────────────────────────────────────────────────
    section("EXP B — monthly feature aggregates vs monthly cascade Sharpe")
    print("  For each base feature, compute monthly mean/std,")
    print("  then Spearman vs concurrent and lagged cascade Sharpe.")
    print()

    df["year_month"] = df["d0"].dt.to_period("M")
    # Use base features as candidates: dvol_d0, intraday_pct, ret_open_close_d0,
    # market_cap (log), prior_avg_t5, plus aggregate "universe" features
    candidate_features = [
        "dvol_d0", "intraday_pct", "ret_open_close_d0", "open",
        "prior_avg_t5",
    ]
    # Add aggregate-scope features computed across the day's universe
    # (number of candidates per day, mean prediction, etc.)
    agg_per_month = df.groupby("year_month").agg({
        "dvol_d0":           ["mean", "std", "median"],
        "intraday_pct":      ["mean", "std", "median"],
        "ret_open_close_d0": ["mean", "std"],
        "open":              ["mean", "median"],
        "prior_avg_t5":      ["mean"],
    })
    agg_per_month.columns = ["_".join(c) for c in agg_per_month.columns]
    agg_per_month = agg_per_month.reset_index()
    agg_per_month["candidates_per_month"] = df.groupby("year_month").size().values
    agg_per_month["fired_per_month"] = (
        v3[v3["tier"] != "SKIP"].assign(year_month=lambda x: x["d0"].dt.to_period("M"))
        .groupby("year_month").size().reindex(agg_per_month["year_month"], fill_value=0).values
    )
    agg_per_month["fire_rate"] = (
        agg_per_month["fired_per_month"] / agg_per_month["candidates_per_month"].clip(lower=1)
    )
    # Mean v3 prediction across the month (proxy for "model conviction")
    agg_per_month["mean_v3_proba"] = (
        v3.assign(year_month=lambda x: x["d0"].dt.to_period("M"))
        .groupby("year_month")["prob_specialist_BROAD"].mean()
        .reindex(agg_per_month["year_month"], fill_value=0).values
    )
    # Mean realized ret_t5 across all candidates (universe-level base rate)
    agg_per_month["universe_mean_ret"] = df.groupby("year_month")["ret_t5"].mean().values

    print(f"  Aggregate features per month (n={len(agg_per_month)}):")
    print(agg_per_month.head(3).to_string())

    # Spearman correlation: each feature vs cascade Sharpe (concurrent and lag)
    agg_per_month["year_month_str"] = agg_per_month["year_month"].astype(str)
    joined = agg_per_month.merge(monthly_data, left_on="year_month_str",
                                   right_on="year_month", how="inner",
                                   suffixes=("", "_y"))
    print()
    print(f"  Joined: {len(joined)} months")
    print()

    feat_cols = [c for c in joined.columns if c not in
                  ("year_month", "year_month_str", "year_month_y",
                   "month_idx", "sharpe_ann")]
    results = []
    print(f"  {'feature':<32} {'concur_rho':>11} {'lag1_rho':>10} {'lag1_ahead_rho':>15}")
    print("  " + "-" * 72)
    for fc in feat_cols:
        x = joined[fc].values
        y = joined["sharpe_ann"].values
        if np.isnan(x).all() or np.isnan(y).all() or x.std() == 0:
            continue
        # Concurrent
        valid = ~(np.isnan(x) | np.isnan(y))
        rho_concur = float(spearmanr(x[valid], y[valid]).statistic) if valid.sum() >= 3 else float("nan")
        # Lag-1: feature[t-1] vs sharpe[t]
        if len(x) >= 3:
            rho_lag1 = float(spearmanr(x[:-1], y[1:]).statistic)
            rho_ahead = float(spearmanr(x[1:], y[:-1]).statistic)  # sanity - reverse
        else:
            rho_lag1 = float("nan"); rho_ahead = float("nan")
        results.append({
            "feature": fc, "rho_concurrent": rho_concur,
            "rho_lag1_predicts_next": rho_lag1, "rho_lag1_predicted_by_next": rho_ahead,
        })
        print(f"  {fc:<32} {rho_concur:>+11.3f} {rho_lag1:>+10.3f} {rho_ahead:>+14.3f}")

    # Sort by absolute concurrent + lag1 predictive value
    print()
    print("  Top 5 by max(|rho_concurrent|, |rho_lag1_predicts_next|):")
    res_df = pd.DataFrame(results)
    if len(res_df) > 0:
        res_df["score"] = np.maximum(
            res_df["rho_concurrent"].abs(), res_df["rho_lag1_predicts_next"].abs()
        )
        top = res_df.nlargest(5, "score")
        print(top.to_string(index=False))

    # ─────────────────────────────────────────────────────────────────
    section("EXP C — OLDER vs RECENT direct discriminator (defensive)")
    print("  Caveat: N=2 regimes; this analysis is supplementary to Exp B.")
    print()
    threshold = pd.Timestamp("2025-08-01")
    df_disc = df.copy()
    df_disc["is_recent"] = (df_disc["d0"] >= threshold).astype(int)
    # Engineer features
    X = engineer_features(df_disc).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y_period = df_disc["is_recent"].reset_index(drop=True)
    print(f"  X shape: {X.shape}; OLDER frac: {(y_period == 0).mean():.3f}")

    import xgboost as xgb
    from sklearn.model_selection import KFold
    PARAMS = {
        "objective": "binary:logistic", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1, "random_state": 42,
        "eval_metric": "logloss",
    }
    # Cross-val accuracy as discriminator score
    from sklearn.metrics import roc_auc_score
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    oos_pred = np.zeros(len(y_period))
    for fold_i, (tr, te) in enumerate(kf.split(X)):
        m = xgb.XGBClassifier(**PARAMS)
        m.fit(X.iloc[tr], y_period.iloc[tr], verbose=False)
        oos_pred[te] = m.predict_proba(X.iloc[te])[:, 1]
    oos_auc = float(roc_auc_score(y_period, oos_pred))
    print(f"  OOS AUC (predict OLDER vs RECENT from features): {oos_auc:.4f}")
    print(f"  -> AUC > 0.7 means the periods are STRONGLY differentiable from features alone")
    print(f"  -> AUC ~0.5 means features look the same across periods (no detectable regime)")

    # Feature importance — what changed?
    full_model = xgb.XGBClassifier(**PARAMS)
    full_model.fit(X, y_period, verbose=False)
    importances = pd.DataFrame({
        "feature": X.columns,
        "importance": full_model.feature_importances_,
    }).sort_values("importance", ascending=False)
    print("\n  Top 10 features distinguishing OLDER from RECENT:")
    print(importances.head(10).to_string(index=False))
    print()

    # Cross-reference: are these the same features that predict cascade Sharpe in Exp B?
    if len(res_df) > 0:
        # Build feature-name mapping (Exp B used aggregate names like dvol_d0_mean;
        # Exp C uses raw v3 features). Best-effort overlap on feature stem.
        b_features = set([f.split("_")[0] for f in res_df["feature"].head(10).tolist()])
        c_features = set([f.split("_")[0] for f in importances["feature"].head(10).tolist()])
        overlap = b_features & c_features
        print(f"  Feature-stem overlap between Exp B (top 10 cascade-predictors) and ")
        print(f"  Exp C (top 10 period-discriminators): {sorted(overlap)}")

    # ─────────────────────────────────────────────────────────────────
    section("VERDICT (locked pre-commits)")
    if len(res_df) > 0:
        max_score = res_df["score"].max()
        max_feature = res_df.loc[res_df["score"].idxmax(), "feature"]
        print(f"  Best regime-indicator feature: {max_feature}")
        print(f"  Max(|concurrent_rho|, |lag1_rho|): {max_score:.3f}")
        print()
        # Also need same-sign requirement
        best_row = res_df.loc[res_df["score"].idxmax()]
        same_sign = (np.sign(best_row["rho_concurrent"]) ==
                     np.sign(best_row["rho_lag1_predicts_next"]))
        print(f"  Same-sign concurrent + lag1: {bool(same_sign)}")
        print()
        if max_score >= 0.5 and same_sign:
            print(f"  -> STRONG INDICATOR FOUND. Propose as v3 feature or launcher gate.")
        elif max_score >= 0.3:
            print(f"  -> MARGINAL. Don't deploy on weak signal; document as inconclusive.")
        else:
            print(f"  -> NO INDICATOR. Regime detection infeasible from current features.")
            print(f"     The OLDER->RECENT cascade Sharpe shift is REAL but UNOBSERVABLE")
            print(f"     ahead of time from the feature space we have.")

    # Persist
    out = {
        "n_months": int(len(monthly)),
        "monthly_sharpe": monthly[["year_month", "sharpe_ann", "count"]].astype({"year_month": str}).to_dict(orient="records"),
        "lag1_autocorr_sharpe": float(ac1) if 'ac1' in locals() else None,
        "linear_trend_slope": float(slope) if 'slope' in locals() else None,
        "linear_trend_r2": float(r_val ** 2) if 'r_val' in locals() else None,
        "exp_b_results": res_df.to_dict(orient="records") if len(res_df) > 0 else [],
        "exp_c_oos_auc": oos_auc,
        "exp_c_top10_importances": importances.head(10).to_dict(orient="records"),
    }
    out_path = MODELS / "v6_regime_indicator.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
