"""D281 (2026-05-05) — train 4 cohort-specialized v3 models (Tier A cascade).

Per s124 finding: v3-default (no Optuna) generates +$1,388 (+14.4%) better
ELITE-tier $-PNL than v3-tuned-16f because Optuna's regularization optimizes
for the broad-objective cohort and under-fits the rare ELITE cohort. Per
s111 finding: 16-fold Optuna retune helped HIGH/BROAD by $1,635 but cost
ELITE/VETOED $569. Single-objective hyperparameter tuning cannot serve
all four tiers — a single model is the wrong abstraction for this problem.

This script trains 4 cohort-specialized stacked ensembles, each with a
DIFFERENT y-label (a different return-magnitude threshold), so each
specialist optimizes for a different precision/recall regime:

    BROAD specialist  -> y = (ret_t5 >= 0.10)  ~21% positive class
    VETOED specialist -> y = (ret_t5 >= 0.15)  ~16% positive class
    HIGH specialist   -> y = (ret_t5 >= 0.25)  ~11% positive class
    ELITE specialist  -> y = (ret_t5 >= 0.40)   ~7% positive class

Each specialist uses the SAME 54 v3 features (the s109 set) and the SAME
4-base-learner stacked architecture (XGBoost + LightGBM + LogReg + RF ->
LogReg meta). Only the y-label changes. XGBoost runs on GPU (RTX 5070 ×
12 GB) for ~5x speedup.

OUTPUT (all gitignored under data/models + data/polygon_warehouse/derived):
    continuer_v2_v3_tier_BROAD.pkl
    continuer_v2_v3_tier_VETOED.pkl
    continuer_v2_v3_tier_HIGH.pkl
    continuer_v2_v3_tier_ELITE.pkl
    ml_v3_tier_specialist_BROAD_predictions.parquet  (+ VETOED, HIGH, ELITE)
    v3_tier_specialists_summary.json
"""
from __future__ import annotations
import json
import pickle
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

sys.path.insert(0, str(REPO / "scripts"))
from ml_continuer_v2_ensemble import (  # type: ignore
    load_data, engineer_features, walk_forward_cv,
    fit_lgbm, fit_logreg, fit_rf, fit_meta,
    conformal_calibrate, conformal_width,
)


TIERS = {
    "BROAD":  0.10,  # ~21% positive — the existing baseline
    "VETOED": 0.15,  # ~16% positive
    "HIGH":   0.25,  # ~11% positive
    "ELITE":  0.40,  #  ~7% positive — the rare-event cohort
}


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def fit_xgb_gpu(X: pd.DataFrame, y: pd.Series, params: dict | None = None):
    """Train XGBoost on GPU (RTX 5070 / CUDA). Falls back to CPU silently
    if device='cuda' fails (e.g., on a CI runner without a GPU)."""
    import xgboost as xgb
    p = params or {}
    base_kwargs = dict(
        n_estimators=int(p.get("xgb_n_estimators", 400)),
        max_depth=int(p.get("xgb_max_depth", 4)),
        learning_rate=float(p.get("xgb_lr", 0.04)),
        subsample=float(p.get("xgb_subsample", 0.8)),
        colsample_bytree=float(p.get("xgb_colsample", 0.8)),
        min_child_weight=int(p.get("xgb_min_child", 1)),
        random_state=42, verbosity=0, eval_metric="logloss",
        tree_method="hist",
    )
    try:
        m = xgb.XGBClassifier(device="cuda", **base_kwargs)
        return m.fit(X, y)
    except Exception as e:
        print(f"  WARN: GPU XGBoost failed ({e}); falling back to CPU")
        m = xgb.XGBClassifier(device="cpu", n_jobs=-1, **base_kwargs)
        return m.fit(X, y)


def train_one_specialist(tier_name: str, threshold: float,
                            X: pd.DataFrame, y_reg: pd.Series, dates: pd.Series,
                            df: pd.DataFrame,
                            optuna_params: dict | None) -> tuple[dict, pd.DataFrame]:
    """Train one cohort-specialized stacked ensemble. Returns (final_artifacts,
    walk-forward predictions DataFrame).

    Mirrors the structure of ml_continuer_v2_ensemble.main()'s STEP 3-6 but
    with a tier-specific y-label and GPU XGBoost. The conformal threshold,
    base-learner stack, and meta-learner are computed identically.
    """
    y_cls = (y_reg >= threshold).astype(int)
    pos_pct = y_cls.mean() * 100
    print(f"\n[{tier_name}] threshold ret_t5 >= {threshold:.2f}  "
          f"({y_cls.sum():,} positive / {len(y_cls):,} total = {pos_pct:.2f}%)")

    folds = list(walk_forward_cv(dates, train_window_days=365, test_window_days=30))
    fold_results = []
    all_preds = []

    t_start = time.perf_counter()
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        Xtr, Xte = X[tr_mask], X[te_mask]
        ytr, yte = y_cls[tr_mask], y_cls[te_mask]
        if len(Xte) < 30:
            continue

        # Identical inside-fold split as production: 60% base / 20% meta / 20% calib
        n = len(Xtr)
        rng = np.random.RandomState(42 + fold_i)
        perm = rng.permutation(n)
        n_meta = int(n * 0.20)
        n_calib = int(n * 0.20)
        meta_idx = perm[:n_meta]
        calib_idx = perm[n_meta:n_meta + n_calib]
        base_idx = perm[n_meta + n_calib:]
        Xb, yb = Xtr.iloc[base_idx], ytr.iloc[base_idx]
        Xm, ym = Xtr.iloc[meta_idx], ytr.iloc[meta_idx]
        Xc, yc = Xtr.iloc[calib_idx], ytr.iloc[calib_idx]

        # Tier-specialized base learners (XGBoost on GPU)
        learners = {
            "xgb":    fit_xgb_gpu(Xb, yb, optuna_params),
            "lgbm":   fit_lgbm(Xb, yb, optuna_params),
            "logreg": fit_logreg(Xb, yb),
            "rf":     fit_rf(Xb, yb),
        }
        P_meta = np.column_stack([m.predict_proba(Xm)[:, 1] for m in learners.values()])
        meta = fit_meta(P_meta, ym.values)

        P_test = np.column_stack([m.predict_proba(Xte)[:, 1] for m in learners.values()])
        probs_test = meta.predict_proba(P_test)[:, 1]

        P_calib = np.column_stack([m.predict_proba(Xc)[:, 1] for m in learners.values()])
        probs_calib = meta.predict_proba(P_calib)[:, 1]
        thr_conf = conformal_calibrate(probs_calib, yc.values, alpha=0.10)
        widths = conformal_width(probs_test, thr_conf)

        y_reg_te = df.loc[te_mask, "ret_t5"].clip(-0.50, 1.00)
        y_cls_orig = (y_reg_te >= 0.10).astype(int)  # baseline 10% label for comparison

        fold_results.append({
            "fold": fold_i,
            "test_start": str(dates[te_mask].min()),
            "n_test": len(Xte),
            "tier_pos_rate": float(yte.mean()),
            "p30_n": int((probs_test >= 0.30).sum()),
            "p30_avg": float(y_reg_te[probs_test >= 0.30].mean())
                          if (probs_test >= 0.30).any() else 0.0,
            "conformal_threshold": thr_conf,
        })
        all_preds.append(pd.DataFrame({
            "fold": fold_i,
            "d0": df.loc[te_mask, "d0"].values,
            "ticker": df.loc[te_mask, "ticker"].values,
            "y_cls": y_cls_orig.values,           # standard ret_t5>=0.10 label
            "y_cls_tier": yte.values,              # tier-threshold label
            "y_reg": y_reg_te.values,
            f"prob_specialist_{tier_name}": probs_test,
            "conformal_width": widths,
        }))
        if fold_i % 4 == 0:
            print(f"  fold {fold_i:2d}: n_test={len(Xte):,} "
                  f"p30_n={fold_results[-1]['p30_n']:>3} "
                  f"p30_avg={fold_results[-1]['p30_avg']*100:+.2f}%")

    elapsed = time.perf_counter() - t_start
    print(f"  {tier_name} WF complete in {elapsed:.1f}s "
          f"({len(fold_results)} folds × 4 base learners)")

    # Train final stack on full data (production artifact)
    cutoff = dates.max() - timedelta(days=5)
    final_mask = (dates < cutoff).values
    Xf, yf = X[final_mask], y_cls[final_mask]
    rng = np.random.RandomState(42)
    n = len(Xf)
    perm = rng.permutation(n)
    n_meta = int(n * 0.20)
    n_calib = int(n * 0.20)
    meta_idx = perm[:n_meta]
    calib_idx = perm[n_meta:n_meta + n_calib]
    base_idx = perm[n_meta + n_calib:]
    Xfb, yfb = Xf.iloc[base_idx], yf.iloc[base_idx]
    final_learners = {
        "xgb":    fit_xgb_gpu(Xfb, yfb, optuna_params),
        "lgbm":   fit_lgbm(Xfb, yfb, optuna_params),
        "logreg": fit_logreg(Xfb, yfb),
        "rf":     fit_rf(Xfb, yfb),
    }
    P_meta = np.column_stack([m.predict_proba(Xf.iloc[meta_idx])[:, 1]
                                  for m in final_learners.values()])
    final_meta = fit_meta(P_meta, yf.iloc[meta_idx].values)
    P_calib = np.column_stack([m.predict_proba(Xf.iloc[calib_idx])[:, 1]
                                   for m in final_learners.values()])
    probs_calib = final_meta.predict_proba(P_calib)[:, 1]
    final_thr = conformal_calibrate(probs_calib, yf.iloc[calib_idx].values,
                                       alpha=0.10)

    artifacts = {
        "version": f"v3-tier-{tier_name}",
        "tier_name": tier_name,
        "tier_threshold_ret_t5": threshold,
        "base_learners": final_learners,
        "meta_learner": final_meta,
        "conformal_threshold": float(final_thr),
        "feature_columns": list(X.columns),
        "training_rows": len(Xf),
        "fold_summary": fold_results,
    }
    preds_df = pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()
    return artifacts, preds_df


def main() -> int:
    section("D281 STEP 1 — Load data + engineer v3 features (54 cols)")
    t0 = time.perf_counter()
    df = load_data(include_paths=True)  # v3 = 54 features incl. paths
    print(f"  loaded {len(df):,} rows in {time.perf_counter()-t0:.1f}s")
    X = engineer_features(df)
    X = X.replace([np.inf, -np.inf], 0).fillna(0)
    y_reg = df["ret_t5"].clip(-0.50, 1.00)
    dates = df["d0"]
    print(f"  X: {X.shape}, y_reg.mean()={y_reg.mean()*100:.2f}%")

    # Load production Optuna params (16-fold tuned). All specialists start
    # from the same hyperparameters as the production v3 model — they
    # specialize via the y-label, NOT via re-tuning. (Re-tuning per tier
    # is a Phase 2 follow-up; first prove the y-label specialization works.)
    optuna_path = MODELS / "v3_optuna_full16fold.json"
    if not optuna_path.exists():
        optuna_path = MODELS / "v2_optuna_best_params.json"
    optuna_params = json.loads(optuna_path.read_text()).get("best_params")
    print(f"  Loaded Optuna params from {optuna_path.name}")

    section("D281 STEP 2 — Train 4 cohort specialists (XGBoost on GPU)")
    summaries = {}
    for tier_name, threshold in TIERS.items():
        artifacts, preds = train_one_specialist(
            tier_name, threshold, X, y_reg, dates, df, optuna_params,
        )
        # Persist artifacts + WF predictions
        pkl_path = MODELS / f"continuer_v2_v3_tier_{tier_name}.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(artifacts, f)
        preds_path = DERIVED / f"ml_v3_tier_specialist_{tier_name}_predictions.parquet"
        preds.to_parquet(preds_path, compression="zstd")
        print(f"  -> {pkl_path.name}  +  {preds_path.name}")

        # Per-tier summary stats
        if not preds.empty:
            prob_col = f"prob_specialist_{tier_name}"
            for thr in (0.30, 0.50, 0.60):
                mask = preds[prob_col] >= thr
                n = int(mask.sum())
                if n == 0:
                    continue
                avg = float(preds.loc[mask, "y_reg"].mean() * 100)
                win = float((preds.loc[mask, "y_reg"] > 0).mean() * 100)
                summaries[f"{tier_name}_p{int(thr*100)}"] = {
                    "n": n, "avg_pct": avg, "win_pct": win
                }

    section("D281 STEP 3 — Persist summary")
    summary = {
        "tiers": TIERS,
        "feature_columns": list(X.columns),
        "n_features": len(X.columns),
        "training_rows": len(X),
        "per_tier_at_p30_p50_p60": summaries,
        "next_step": (
            "Run scripts/ml_v3_tier_specialists_verdict.py to compute the "
            "tiered-cascade $-PNL vs production v3-tuned-16f baseline."
        ),
    }
    out_path = MODELS / "v3_tier_specialists_summary.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out_path}")

    print("\n  PER-TIER SPECIALIST PERFORMANCE @ p>=0.30:")
    for tier in TIERS:
        key = f"{tier}_p30"
        if key in summaries:
            s = summaries[key]
            print(f"    {tier:<8} n={s['n']:>4,}  avg={s['avg_pct']:>+6.2f}%  "
                  f"win={s['win_pct']:>5.1f}%")
        else:
            print(f"    {tier:<8} (no rows passed)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
