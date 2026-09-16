"""ITEM 5 PROPER — TabPFNv2 ceiling test with doc 138 + 141 hygiene baked in.

Per user's previous critique: this is the canonical ceiling test that's
been on the to-do list across two architecture iterations. License now
unblocked via TABPFN_TOKEN env var.

Hygiene rules from prior docs that this script must obey:

  Doc 138 rule: CPU-deterministic XGBoost baseline (device='cpu',
  n_jobs=1) for all comparisons. GPU XGBoost is non-deterministic.

  Doc 141 rule: any positive lift on full WF data MUST be accompanied
  by a same-comparison on the recent-N-folds subset (where N covers
  the production-relevant period, i.e. 2025-08+ for our microcap
  universe). If lift collapses on recent data, the result is not
  pilot-ready.

  Doc 140 rule: noise-floor permutation test (shuffle features, re-run)
  for any positive lift, with p-value vs the null distribution.

  Doc 138 rule: verdict section blank until numbers are in hand.

This script does ALL THREE in one run:
  1. WF training: TabPFN + CPU XGBoost on same 12 folds, full data
  2. Per-fold breakdown: TabPFN−XGB delta by fold, split at 2025-08-01
  3. Recent-only aggregate (folds 8-11): the production-relevant comparison
  4. Noise floor: 10 feature-permutation trials → null distribution

Decision threshold (from user's original critique on Item 5):
  - TabPFN Spearman ≥ 0.18 on RECENT subset → model-class ceiling exists
    for production data → pilot warranted
  - TabPFN Spearman ≈ XGB on RECENT subset → Bayes ceiling, no pilot

Comparison to TabICL (doc 140 + 141):
  - TabICL on full data: +0.027 over XGB (p<0.001 noise floor)
  - TabICL on RECENT subset: -0.029 vs XGB (lift collapsed)
  - TabPFN may differ — same family, different pretraining corpus

USAGE:
  TABPFN_TOKEN=<token> TABPFN_NO_BROWSER=1 \\
    python scripts/ml_v6_item5_tabpfn_proper.py
"""
from __future__ import annotations
import argparse
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


def build_exposures(df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    exp = pd.DataFrame({
        "log_market_cap": np.log1p(df.get("market_cap", pd.Series(np.zeros(n))).fillna(0)),
        "log_dvol_d0":    np.log1p(df.get("dvol_d0", pd.Series(np.zeros(n))).fillna(0)),
        "prior_avg_t5":   df.get("prior_avg_t5", pd.Series(np.zeros(n))).fillna(0),
        "intraday_pct":   df.get("intraday_pct", pd.Series(np.zeros(n))).fillna(0),
    })
    if "sic_description" in df.columns:
        sic = df["sic_description"].fillna("").str.lower()
        for sec, key in [("pharma", "pharm"), ("bio", "bio"),
                          ("medical", "medic"), ("software", "software"),
                          ("finance", "financ"), ("semi", "semicond"),
                          ("spac", "spac"), ("reit", "reit")]:
            exp[f"sector_{sec}"] = sic.str.contains(key).astype(float)
    return exp


def run_wf_tabpfn(X: pd.DataFrame, y: pd.Series, d0: pd.Series,
                   fold_starts: pd.DatetimeIndex,
                   train_days: int = 120, test_days: int = 15,
                   max_train: int = 8000) -> pd.DataFrame:
    """One TabPFNRegressor per fold; returns concat OOS preds."""
    from tabpfn import TabPFNRegressor
    rows = []
    for fi, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5:
            continue
        Xtr = X.loc[tr].values; Xte = X.loc[te].values
        ytr = y.loc[tr].values; yte = y.loc[te].values
        if len(Xtr) > max_train:
            rng = np.random.default_rng(42 + fi)
            idx = rng.choice(len(Xtr), max_train, replace=False)
            Xtr = Xtr[idx]; ytr = ytr[idx]
        t0 = time.time()
        try:
            m = TabPFNRegressor(device="cuda", random_state=42)
            m.fit(Xtr, ytr)
            pred = m.predict(Xte)
        except Exception as e:
            print(f"  fold {fi:>2}: TabPFN error: {e}", flush=True)
            continue
        rho = float(spearmanr(yte, pred).statistic)
        rows.append(pd.DataFrame({
            "fold": fi, "d0": d0.loc[te].values,
            "y_true": yte, "y_pred": pred, "_idx": np.where(te)[0],
        }))
        print(f"  fold {fi:>2}: tr={tr.sum():>5}  te={te.sum():>4}  "
              f"Spearman={rho:+.3f}  ({time.time()-t0:.0f}s)", flush=True)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def run_wf_xgb_cpu(X: pd.DataFrame, y: pd.Series, d0: pd.Series,
                    fold_starts: pd.DatetimeIndex,
                    train_days: int = 120, test_days: int = 15,
                    seed: int = 42) -> pd.DataFrame:
    """v3 XGBoost CPU baseline (deterministic, matches doc 138 Item 1)."""
    import xgboost as xgb
    PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1, "random_state": seed,
    }
    rows = []
    for fi, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5: continue
        m = xgb.XGBRegressor(**PARAMS)
        m.fit(X.loc[tr], y.loc[tr], verbose=False)
        pred = m.predict(X.loc[te])
        rows.append(pd.DataFrame({
            "fold": fi, "d0": d0.loc[te].values,
            "y_true": y.loc[te].values, "y_pred": pred,
            "_idx": np.where(te)[0],
        }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-permutations", type=int, default=10,
                    help="noise-floor permutation count (TabPFN takes ~12s/fold; "
                         "10 perms × 12 folds = ~25 min)")
    ap.add_argument("--skip-noise-floor", action="store_true",
                    help="skip noise-floor permutation test")
    args = ap.parse_args()

    section("STEP 1 — load v3 base data + engineer features (54 cols, no v6)")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  loaded {len(df):,} rows")
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  X: {X.shape}  y: {y.shape}  d0 range: {d0.min().date()} -> {d0.max().date()}")

    n_folds = 12
    train_days = 120
    test_days = 15
    fold_starts = pd.date_range(
        d0.min() + pd.Timedelta(days=train_days),
        d0.max() - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  WF: {n_folds} folds, train={train_days}d, test={test_days}d")

    section("STEP 2 — TabPFNv2 (real, with TABPFN_TOKEN auth)")
    print("  device=cuda, max_train=8000, random_state=42")
    t0 = time.time()
    pf_preds = run_wf_tabpfn(X, y, d0, fold_starts, train_days, test_days)
    print(f"\n  TabPFN finished in {time.time()-t0:.0f}s ({len(pf_preds):,} OOS rows)")

    section("STEP 3 — v3 XGBoost CPU baseline (deterministic)")
    t0 = time.time()
    xg_preds = run_wf_xgb_cpu(X, y, d0, fold_starts, train_days, test_days)
    print(f"  XGBoost CPU finished in {time.time()-t0:.0f}s ({len(xg_preds):,} OOS rows)")

    section("STEP 4 — full-data aggregate metrics")
    pf_rho = float(spearmanr(pf_preds["y_true"], pf_preds["y_pred"]).statistic)
    xg_rho = float(spearmanr(xg_preds["y_true"], xg_preds["y_pred"]).statistic)
    exp = build_exposures(df)
    pf_exp = exp.iloc[pf_preds["_idx"].values].reset_index(drop=True)
    xg_exp = exp.iloc[xg_preds["_idx"].values].reset_index(drop=True)
    pf_neut = neutralized_rho(pf_preds["y_pred"].values, pf_preds["y_true"].values, pf_exp, 1.0)
    xg_neut = neutralized_rho(xg_preds["y_pred"].values, xg_preds["y_true"].values, xg_exp, 1.0)
    print(f"  TabPFN  Spearman: {pf_rho:+.4f}  Neutralized: {pf_neut:+.4f}")
    print(f"  v3 XGB  Spearman: {xg_rho:+.4f}  Neutralized: {xg_neut:+.4f}")
    print(f"  Delta:            {pf_rho - xg_rho:+.4f}  /  {pf_neut - xg_neut:+.4f}")

    section("STEP 5 — per-fold breakdown (doc 141 hygiene rule)")
    pf_preds_f = pf_preds.copy(); pf_preds_f["d0"] = pd.to_datetime(pf_preds_f["d0"])
    xg_preds_f = xg_preds.copy(); xg_preds_f["d0"] = pd.to_datetime(xg_preds_f["d0"])
    print(f"  {'fold':>5} {'start':<12} {'n_test':>7} {'TabPFN':>8} {'XGB':>8} {'delta':>8}")
    deltas = []
    for fi in sorted(pf_preds_f["fold"].unique()):
        p = pf_preds_f[pf_preds_f["fold"] == fi]
        x = xg_preds_f[xg_preds_f["fold"] == fi]
        if not len(p) or not len(x): continue
        p_rho = float(spearmanr(p["y_true"], p["y_pred"]).statistic)
        x_rho = float(spearmanr(x["y_true"], x["y_pred"]).statistic)
        delta = p_rho - x_rho
        start = p["d0"].min().date()
        deltas.append((int(fi), start, len(p), p_rho, x_rho, delta))
        print(f"  {fi:>5} {str(start):<12} {len(p):>7,} {p_rho:>+8.3f} {x_rho:>+8.3f} {delta:>+8.3f}")

    threshold = pd.Timestamp("2025-08-01").date()
    older = [d for d in deltas if d[1] < threshold]
    newer = [d for d in deltas if d[1] >= threshold]
    print(f"\n  Split at {threshold} (d-1 warehouse / production-relevant boundary):")
    if older:
        print(f"    OLDER (n={len(older)} folds): mean TabPFN={np.mean([d[3] for d in older]):+.4f}  "
              f"mean XGB={np.mean([d[4] for d in older]):+.4f}  "
              f"mean delta={np.mean([d[5] for d in older]):+.4f}")
    if newer:
        print(f"    NEWER (n={len(newer)} folds): mean TabPFN={np.mean([d[3] for d in newer]):+.4f}  "
              f"mean XGB={np.mean([d[4] for d in newer]):+.4f}  "
              f"mean delta={np.mean([d[5] for d in newer]):+.4f}")

    print("\n  AGGREGATE Spearman (concat OOS preds, recompute over union):")
    older_p = pd.concat([pf_preds_f[pf_preds_f["fold"] == d[0]] for d in older]) if older else pd.DataFrame()
    older_x = pd.concat([xg_preds_f[xg_preds_f["fold"] == d[0]] for d in older]) if older else pd.DataFrame()
    newer_p = pd.concat([pf_preds_f[pf_preds_f["fold"] == d[0]] for d in newer]) if newer else pd.DataFrame()
    newer_x = pd.concat([xg_preds_f[xg_preds_f["fold"] == d[0]] for d in newer]) if newer else pd.DataFrame()
    older_pf_rho = float(spearmanr(older_p["y_true"], older_p["y_pred"]).statistic) if len(older_p) else float("nan")
    older_xg_rho = float(spearmanr(older_x["y_true"], older_x["y_pred"]).statistic) if len(older_x) else float("nan")
    newer_pf_rho = float(spearmanr(newer_p["y_true"], newer_p["y_pred"]).statistic) if len(newer_p) else float("nan")
    newer_xg_rho = float(spearmanr(newer_x["y_true"], newer_x["y_pred"]).statistic) if len(newer_x) else float("nan")
    print(f"    OLDER (n={len(older_p):,}): TabPFN={older_pf_rho:+.4f}  XGB={older_xg_rho:+.4f}  delta={older_pf_rho-older_xg_rho:+.4f}")
    print(f"    NEWER (n={len(newer_p):,}): TabPFN={newer_pf_rho:+.4f}  XGB={newer_xg_rho:+.4f}  delta={newer_pf_rho-newer_xg_rho:+.4f}")

    # Save predictions for later analysis
    pf_preds.to_parquet(DERIVED / "ml_v6_item5_tabpfn_preds.parquet", compression="zstd")
    xg_preds.to_parquet(DERIVED / "ml_v6_item5_xgb_preds.parquet", compression="zstd")

    noise_result = None
    if not args.skip_noise_floor:
        section(f"STEP 6 — noise-floor permutation test ({args.n_permutations} trials)")
        rng = np.random.default_rng(42)
        nulls_pf = []; nulls_xg = []; nulls_delta = []
        for i in range(args.n_permutations):
            t0 = time.time()
            X_shuf = X.copy()
            for col in X_shuf.columns:
                X_shuf[col] = rng.permutation(X_shuf[col].values)
            null_pf = run_wf_tabpfn(X_shuf, y, d0, fold_starts, train_days, test_days)
            null_xg = run_wf_xgb_cpu(X_shuf, y, d0, fold_starts, train_days, test_days)
            n_pf = float(spearmanr(null_pf["y_true"], null_pf["y_pred"]).statistic)
            n_xg = float(spearmanr(null_xg["y_true"], null_xg["y_pred"]).statistic)
            nulls_pf.append(n_pf); nulls_xg.append(n_xg); nulls_delta.append(n_pf - n_xg)
            print(f"  perm[{i+1:>2}/{args.n_permutations}]: TabPFN={n_pf:+.4f}  XGB={n_xg:+.4f}  "
                  f"delta={n_pf-n_xg:+.4f}  ({time.time()-t0:.0f}s)", flush=True)
        nd = np.array(nulls_delta)
        real_delta = pf_rho - xg_rho
        p_value = float((nd >= real_delta).mean())
        print(f"\n  REAL TabPFN-XGB delta:  {real_delta:+.4f}")
        print(f"  NULL distribution: mean={nd.mean():+.4f}  std={nd.std():.4f}  p95={np.percentile(nd,95):+.4f}")
        print(f"  p-value (one-sided): {p_value:.3f}")
        noise_result = {
            "n_perms": int(args.n_permutations),
            "real_delta": float(real_delta),
            "null_mean": float(nd.mean()), "null_std": float(nd.std()),
            "null_p95": float(np.percentile(nd, 95)),
            "p_value_one_sided": p_value,
            "null_deltas": [float(d) for d in nulls_delta],
        }

    section("STEP 7 — verdict per user's pre-stated rules")
    print(f"  Decision rule (from user's original Item 5 critique):")
    print(f"    TabPFN Spearman >= 0.18 on RECENT subset -> model-class ceiling on prod data -> pilot")
    print(f"    TabPFN <0.18 or losing to XGB on recent  -> Bayes ceiling -> deployment focus")
    print()
    print(f"  TabPFN RECENT (folds 8-11, n={len(newer_p):,}, Oct 2025 - Apr 2026):")
    print(f"    TabPFN Spearman: {newer_pf_rho:+.4f}  (threshold: 0.18)")
    print(f"    XGB    Spearman: {newer_xg_rho:+.4f}")
    print(f"    Delta:           {newer_pf_rho - newer_xg_rho:+.4f}")
    print()
    if newer_pf_rho >= 0.18 and newer_pf_rho > newer_xg_rho:
        print(f"  -> TabPFN beats XGBoost on RECENT subset AND >= 0.18.")
        print(f"  -> Pilot warranted. The user's original Item 5 framing rescued.")
    elif newer_pf_rho >= 0.18:
        print(f"  -> TabPFN >= 0.18 but tied with or losing to XGBoost.")
        print(f"  -> Per doc 141 rule: lift in absolute terms but not vs incumbent.")
        print(f"  -> Not pilot-ready. v3 XGBoost is competitive on recent data.")
    else:
        print(f"  -> TabPFN < 0.18 on recent data.")
        print(f"  -> Bayes ceiling supported. Deployment focus.")

    out = {
        "tabpfn_full_spearman": pf_rho, "tabpfn_full_neut": pf_neut, "tabpfn_n": int(len(pf_preds)),
        "xgb_full_spearman": xg_rho, "xgb_full_neut": xg_neut, "xgb_n": int(len(xg_preds)),
        "delta_full_spearman": pf_rho - xg_rho,
        "delta_full_neut": pf_neut - xg_neut,
        "per_fold_deltas": [{"fold": d[0], "start": str(d[1]), "n": d[2],
                              "tabpfn": d[3], "xgb": d[4], "delta": d[5]} for d in deltas],
        "older_aggregate": {"n": int(len(older_p)), "tabpfn": older_pf_rho,
                             "xgb": older_xg_rho, "delta": older_pf_rho - older_xg_rho},
        "newer_aggregate": {"n": int(len(newer_p)), "tabpfn": newer_pf_rho,
                             "xgb": newer_xg_rho, "delta": newer_pf_rho - newer_xg_rho},
        "noise_floor": noise_result,
    }
    out_path = MODELS / "v6_item5_tabpfn_proper.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
