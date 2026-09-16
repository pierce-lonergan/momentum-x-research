"""v6 Phase 1 DIAGNOSTICS — null-floor + per-decile + ablation + per-tier + lookahead audit.

Per the user's pushback on doc 135 (which I should have written this way from the start):

  D1. NOISE FLOOR via permutation test
      Shuffle each v6 column independently across rows. Re-run validation.
      Repeat 30+ times. The Spearman delta distribution is the noise band.
      If +0.016 lift is INSIDE this band, the v6 signal is statistically
      indistinguishable from random.

  D2. PER-DECILE LIFT
      Sort OOS predictions for control + treatment, bucket into 10 deciles,
      compute mean(y_true) per decile. If v6 lift is concentrated in
      decile 10 (top tail), the M.md "ELITE is a data problem" thesis
      is supported. If diffuse across deciles, v6 is broad-rank-helping.
      The two stories are mutually exclusive — diagnostic disambiguates.

  D3. LIQUIDITY-PROXY ABLATION
      OFI is NaN→0 filled. Many microcaps have no first-30-min trades.
      Replace OFI with binary `has_first_30_trades` and re-run.
      If most of the lift survives, the OFI signal was actually a
      liquidity-gate proxy in disguise.

  D4. PER-TIER EVALUATION
      Train binary classifier y = (ret_t5 >= 0.40) for ELITE specifically.
      Measure precision@N for top-N picks. Compare control vs treatment.
      M.md predicts v6 should help ELITE specifically.

  D5. LOOKAHEAD AUDIT
      Static: print which v6 features observe trades AFTER 09:30:00 ET
      and therefore cannot feed an "enter at open" decision.

These diagnostics answer: is the lift real, where does it act, is it
just liquidity-encoding, does it concentrate in ELITE, and can it even
be wired into production?

USAGE:
  python scripts/ml_v6_phase1_diagnostics.py
  python scripts/ml_v6_phase1_diagnostics.py --n-permutations 30
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

sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


V6_FEATURES = [
    "vpin_d0",
    "ofi_first30_d0",
    "kyle_lambda_d0",
    "hawkes_fano_d0",
    "amihud_illiq_d0",
    "iso_sweep_count_d0",
]


# ──────────────────────────────────────────────────────────────────────
# Shared infrastructure (mirrors ml_v6_phase1_validation.py for reproducibility)
# ──────────────────────────────────────────────────────────────────────


def load_data(v6_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, pd.DataFrame]:
    """Returns X_v3, v6_block, y, d0, full_merged_for_exposures.

    v6_block is the engineered feature block (raw + log-transformed).
    Caller decides how to combine X_v3 + v6_block.
    """
    from ml_continuer_v2_ensemble import load_data as _load, engineer_features

    df = _load(include_paths=True)
    v6 = pd.read_parquet(v6_path)
    df["d0"] = pd.to_datetime(df["d0"])
    v6["d0"] = pd.to_datetime(v6["d0"])
    merged = df.merge(v6[["ticker", "d0", *V6_FEATURES]],
                      on=["ticker", "d0"], how="inner")

    X_v3 = engineer_features(merged)
    X_v3 = X_v3.replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)

    v6_block = merged[V6_FEATURES].copy()
    v6_block = v6_block.replace([np.inf, -np.inf], np.nan)
    v6_block["log_kyle_lambda_d0"] = np.log1p(v6_block["kyle_lambda_d0"].clip(lower=0))
    v6_block["log_hawkes_fano_d0"] = np.log1p(v6_block["hawkes_fano_d0"].clip(lower=0))
    v6_block["log_amihud_illiq_d0"] = np.log1p(v6_block["amihud_illiq_d0"].clip(lower=0))
    v6_block["log_iso_sweep_count_d0"] = np.log1p(v6_block["iso_sweep_count_d0"].clip(lower=0))
    v6_block = v6_block.fillna(0).reset_index(drop=True)

    y = merged["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(merged["d0"]).reset_index(drop=True)
    return X_v3, v6_block, y, d0, merged


def walk_forward_xgb(X: pd.DataFrame, y: pd.Series, d0: pd.Series,
                     n_folds: int = 12,
                     train_window_days: int = 120,
                     test_window_days: int = 15,
                     verbose: bool = True) -> pd.DataFrame:
    import xgboost as xgb
    XGB_PARAMS = {
        "objective": "reg:squarederror",
        "n_estimators": 600, "max_depth": 5, "learning_rate": 0.03,
        "subsample": 0.8, "colsample_bytree": 0.6, "min_child_weight": 5,
        "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cuda",
        "n_jobs": -1, "random_state": 42,
    }
    d_min, d_max = d0.min(), d0.max()
    fold_starts = pd.date_range(
        d_min + pd.Timedelta(days=train_window_days),
        d_max - pd.Timedelta(days=test_window_days),
        periods=n_folds,
    )
    all_preds = []
    for fold_i, fstart in enumerate(fold_starts):
        train_mask = (d0 >= fstart - pd.Timedelta(days=train_window_days)) & (d0 < fstart)
        test_mask = (d0 >= fstart) & (d0 < fstart + pd.Timedelta(days=test_window_days))
        if train_mask.sum() < 100 or test_mask.sum() < 5:
            continue
        Xtr, ytr = X.loc[train_mask], y.loc[train_mask]
        Xte, yte = X.loc[test_mask], y.loc[test_mask]
        model = xgb.XGBRegressor(**XGB_PARAMS)
        model.fit(Xtr, ytr, verbose=False)
        pred = model.predict(Xte)
        all_preds.append(pd.DataFrame({
            "fold": fold_i,
            "d0": d0.loc[test_mask].values,
            "y_true": yte.values,
            "y_pred": pred,
            "_idx": np.where(test_mask)[0],
        }))
        if verbose:
            print(f"  fold {fold_i:>2}: train={train_mask.sum():>5,} test={test_mask.sum():>4,} "
                  f"  Spearman={spearmanr(yte, pred).statistic:+.3f}")
    return pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────
# D1. Noise floor via permutation
# ──────────────────────────────────────────────────────────────────────


def diagnostic_1_noise_floor(X_v3: pd.DataFrame, v6_block: pd.DataFrame,
                              y: pd.Series, d0: pd.Series,
                              n_permutations: int = 30) -> dict:
    """Shuffle each v6 column independently across rows; rerun validation.
    Report distribution of Spearman delta vs control.

    The user's critique: a +0.016 lift is only meaningful if the noise
    floor is much narrower than ±0.016. This test establishes the floor.
    """
    section(f"D1. NOISE FLOOR — {n_permutations} permutations of v6 features")
    # First, the control baseline
    print("  computing control (v3-only)...")
    preds_ctrl = walk_forward_xgb(X_v3, y, d0, verbose=False)
    rho_ctrl = float(spearmanr(preds_ctrl["y_true"], preds_ctrl["y_pred"]).statistic)
    print(f"  control Spearman: {rho_ctrl:+.4f}")
    # Real treatment
    print("  computing real treatment (v3+v6)...")
    X_real = pd.concat([X_v3, v6_block], axis=1)
    preds_real = walk_forward_xgb(X_real, y, d0, verbose=False)
    rho_real = float(spearmanr(preds_real["y_true"], preds_real["y_pred"]).statistic)
    real_delta = rho_real - rho_ctrl
    print(f"  real treatment Spearman: {rho_real:+.4f}")
    print(f"  REAL DELTA: {real_delta:+.4f}")
    print()

    # Permutation runs
    rng = np.random.default_rng(42)
    null_deltas = []
    print(f"  running {n_permutations} permutations...")
    for i in range(n_permutations):
        v6_shuffled = v6_block.copy()
        for col in v6_shuffled.columns:
            v6_shuffled[col] = rng.permutation(v6_shuffled[col].values)
        X_null = pd.concat([X_v3, v6_shuffled.reset_index(drop=True)], axis=1)
        preds_null = walk_forward_xgb(X_null, y, d0, verbose=False)
        rho_null = float(spearmanr(preds_null["y_true"], preds_null["y_pred"]).statistic)
        delta = rho_null - rho_ctrl
        null_deltas.append(delta)
        if (i + 1) % 5 == 0:
            arr = np.array(null_deltas)
            print(f"    [{i+1:>3}/{n_permutations}] mean_delta={arr.mean():+.4f}  "
                  f"std={arr.std():.4f}  p95={np.percentile(arr,95):+.4f}", flush=True)

    null_arr = np.array(null_deltas)
    p_value = float((null_arr >= real_delta).mean())
    result = {
        "control_spearman": rho_ctrl,
        "real_treatment_spearman": rho_real,
        "real_delta": real_delta,
        "null_n": len(null_arr),
        "null_mean": float(null_arr.mean()),
        "null_std": float(null_arr.std()),
        "null_p05": float(np.percentile(null_arr, 5)),
        "null_p50": float(np.percentile(null_arr, 50)),
        "null_p95": float(np.percentile(null_arr, 95)),
        "p_value_one_sided": p_value,
    }
    print()
    print(f"  control Spearman:        {rho_ctrl:+.4f}")
    print(f"  real treatment delta:    {real_delta:+.4f}")
    print(f"  null delta distribution: mean={null_arr.mean():+.4f} "
          f"std={null_arr.std():.4f}")
    print(f"  null p05/p50/p95:        {np.percentile(null_arr,5):+.4f} / "
          f"{np.percentile(null_arr,50):+.4f} / {np.percentile(null_arr,95):+.4f}")
    print(f"  p-value (one-sided):     {p_value:.3f}")
    if p_value < 0.05:
        print("  VERDICT: real lift is OUTSIDE noise floor (p<0.05) — signal likely real")
    elif p_value < 0.20:
        print("  VERDICT: real lift is BORDERLINE — directionally promising but unproven")
    else:
        print("  VERDICT: real lift is INSIDE noise floor — indistinguishable from random")
    return result


# ──────────────────────────────────────────────────────────────────────
# D2. Per-decile lift
# ──────────────────────────────────────────────────────────────────────


def diagnostic_2_per_decile(X_v3: pd.DataFrame, v6_block: pd.DataFrame,
                             y: pd.Series, d0: pd.Series) -> dict:
    """For control and treatment, sort all OOS predictions, bucket into
    10 deciles, compute mean y_true per decile. The shape of the lift
    curve disambiguates "broad ranking improvement" vs "top-tail capture"."""
    section("D2. PER-DECILE LIFT — where does v6 act on the prediction distribution?")
    preds_ctrl = walk_forward_xgb(X_v3, y, d0, verbose=False)
    X_v3_v6 = pd.concat([X_v3, v6_block], axis=1)
    preds_trt = walk_forward_xgb(X_v3_v6, y, d0, verbose=False)

    def decile_table(preds: pd.DataFrame) -> pd.DataFrame:
        p = preds.copy()
        p["decile"] = pd.qcut(p["y_pred"], 10, labels=False, duplicates="drop")
        return p.groupby("decile")["y_true"].agg(["mean", "count"]).reset_index()

    ctrl_dec = decile_table(preds_ctrl)
    trt_dec = decile_table(preds_trt)
    merged = ctrl_dec.merge(trt_dec, on="decile", suffixes=("_ctrl", "_trt"))
    merged["lift"] = merged["mean_trt"] - merged["mean_ctrl"]

    print("  decile  ctrl_mean   trt_mean      lift   ctrl_n  trt_n")
    print("  " + "-" * 60)
    for _, r in merged.iterrows():
        print(f"  {int(r['decile']):>6}   {r['mean_ctrl']:>+8.4f}   "
              f"{r['mean_trt']:>+8.4f}   {r['lift']:>+8.4f}   "
              f"{int(r['count_ctrl']):>6}   {int(r['count_trt']):>6}")
    # Where is the biggest absolute lift?
    top_lift_idx = merged["lift"].abs().idxmax()
    top_lift_dec = int(merged.loc[top_lift_idx, "decile"])
    top_lift_val = float(merged.loc[top_lift_idx, "lift"])
    print()
    print(f"  largest absolute lift in decile {top_lift_dec}: {top_lift_val:+.4f}")
    if top_lift_dec == 9 and top_lift_val > 0:
        print("  VERDICT: v6 helps the top-tail (M.md ELITE thesis SUPPORTED)")
    elif top_lift_dec == 0 and top_lift_val < 0:
        print("  VERDICT: v6 helps the bottom-tail (avoiding faders)")
    else:
        print("  VERDICT: v6 lift is NOT concentrated at the tails")
    return {
        "decile_table": merged.to_dict(orient="records"),
        "max_abs_lift_decile": top_lift_dec,
        "max_abs_lift_value": top_lift_val,
    }


# ──────────────────────────────────────────────────────────────────────
# D3. Liquidity-proxy ablation
# ──────────────────────────────────────────────────────────────────────


def diagnostic_3_liquidity_ablation(X_v3: pd.DataFrame, v6_block: pd.DataFrame,
                                     y: pd.Series, d0: pd.Series,
                                     merged: pd.DataFrame,
                                     v6_path: Path) -> dict:
    """Replace OFI with binary `has_first_30_trades`. If most of the
    lift survives, OFI was a liquidity-gate proxy in disguise."""
    section("D3. LIQUIDITY-PROXY ABLATION — is OFI a real signal or a liquidity gate?")

    # Reload raw v6 to get the original NaN pattern of ofi_first30_d0
    v6_raw = pd.read_parquet(v6_path)
    v6_raw["d0"] = pd.to_datetime(v6_raw["d0"])
    raw_join = merged[["ticker", "d0"]].merge(
        v6_raw[["ticker", "d0", "ofi_first30_d0"]],
        on=["ticker", "d0"], how="left",
    )
    has_first30 = raw_join["ofi_first30_d0"].notna().astype(float).reset_index(drop=True)
    nonnull_pct = has_first30.mean()
    print(f"  has_first_30_trades non-null rate: {nonnull_pct:.1%} of {len(has_first30):,} rows")

    # Variant A: full v6 pack as-is
    print("  variant A: full v6 pack")
    Xa = pd.concat([X_v3, v6_block], axis=1)
    preds_a = walk_forward_xgb(Xa, y, d0, verbose=False)
    rho_a = float(spearmanr(preds_a["y_true"], preds_a["y_pred"]).statistic)

    # Variant B: drop OFI entirely
    print("  variant B: v6 pack WITHOUT ofi_first30_d0")
    v6_no_ofi = v6_block.drop(columns=["ofi_first30_d0"])
    Xb = pd.concat([X_v3, v6_no_ofi], axis=1)
    preds_b = walk_forward_xgb(Xb, y, d0, verbose=False)
    rho_b = float(spearmanr(preds_b["y_true"], preds_b["y_pred"]).statistic)

    # Variant C: replace OFI with binary has_first_30
    print("  variant C: v6 pack with OFI replaced by has_first_30 binary")
    v6_with_binary = v6_no_ofi.copy()
    v6_with_binary["has_first_30_trades"] = has_first30.values
    Xc = pd.concat([X_v3, v6_with_binary], axis=1)
    preds_c = walk_forward_xgb(Xc, y, d0, verbose=False)
    rho_c = float(spearmanr(preds_c["y_true"], preds_c["y_pred"]).statistic)

    # Control (already in D1 but re-run for clarity)
    preds_ctrl = walk_forward_xgb(X_v3, y, d0, verbose=False)
    rho_ctrl = float(spearmanr(preds_ctrl["y_true"], preds_ctrl["y_pred"]).statistic)

    print()
    print(f"  control (v3-only):                 Spearman {rho_ctrl:+.4f}")
    print(f"  variant A (full v6 pack):          Spearman {rho_a:+.4f}  delta {rho_a-rho_ctrl:+.4f}")
    print(f"  variant B (v6 without OFI):        Spearman {rho_b:+.4f}  delta {rho_b-rho_ctrl:+.4f}")
    print(f"  variant C (v6 + has_first_30):     Spearman {rho_c:+.4f}  delta {rho_c-rho_ctrl:+.4f}")
    print()

    ofi_contrib = rho_a - rho_b  # how much OFI itself adds
    binary_contrib = rho_c - rho_b  # how much the binary alone adds
    print(f"  OFI feature contribution:    {ofi_contrib:+.4f}")
    print(f"  Binary-only contribution:    {binary_contrib:+.4f}")
    if abs(binary_contrib) >= 0.5 * abs(ofi_contrib):
        print("  VERDICT: OFI lift is largely explained by liquidity-gate effect")
    else:
        print("  VERDICT: OFI is contributing real order-flow signal beyond the gate")
    return {
        "control_spearman": rho_ctrl,
        "variant_a_full_v6": rho_a,
        "variant_b_no_ofi": rho_b,
        "variant_c_binary": rho_c,
        "ofi_feature_contribution": ofi_contrib,
        "binary_only_contribution": binary_contrib,
        "first30_coverage_pct": float(nonnull_pct),
    }


# ──────────────────────────────────────────────────────────────────────
# D4. Per-tier evaluation
# ──────────────────────────────────────────────────────────────────────


def diagnostic_4_per_tier(X_v3: pd.DataFrame, v6_block: pd.DataFrame,
                           y_reg: pd.Series, d0: pd.Series) -> dict:
    """Train binary classifiers for each tier; measure precision@N for top-N picks.

    Tiers (M.md alignment):
      ELITE   = ret_t5 >= 0.40
      HIGH    = ret_t5 >= 0.25
      VETOED  = ret_t5 >= 0.15
      BROAD   = ret_t5 >= 0.10

    The hypothesis: v6 helps ELITE specifically, not BROAD."""
    section("D4. PER-TIER EVALUATION — does v6 help ELITE specifically?")
    import xgboost as xgb
    XGB_PARAMS = {
        "objective": "binary:logistic",
        "n_estimators": 600, "max_depth": 5, "learning_rate": 0.03,
        "subsample": 0.8, "colsample_bytree": 0.6, "min_child_weight": 5,
        "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cuda",
        "n_jobs": -1, "random_state": 42, "eval_metric": "logloss",
    }
    TIERS = {"BROAD": 0.10, "VETOED": 0.15, "HIGH": 0.25, "ELITE": 0.40}
    X_v3_v6 = pd.concat([X_v3, v6_block], axis=1)

    results = {}
    print(f"{'tier':<8} {'thr':>5} {'pos%':>6} "
          f"{'AUC_ctrl':>10} {'AUC_trt':>10} {'AUC_delta':>10} "
          f"{'P@30_ctrl':>10} {'P@30_trt':>10} {'P@30_delta':>10}")
    print("-" * 96)
    for tier, thr in TIERS.items():
        y_bin = (y_reg >= thr).astype(int)
        pos_rate = y_bin.mean()

        d_min, d_max = d0.min(), d0.max()
        fold_starts = pd.date_range(
            d_min + pd.Timedelta(days=120), d_max - pd.Timedelta(days=15), periods=12,
        )
        ctrl_auc, trt_auc = [], []
        ctrl_p30, trt_p30 = [], []
        for fstart in fold_starts:
            tr = (d0 >= fstart - pd.Timedelta(days=120)) & (d0 < fstart)
            te = (d0 >= fstart) & (d0 < fstart + pd.Timedelta(days=15))
            if tr.sum() < 100 or te.sum() < 30 or y_bin.loc[tr].sum() < 5:
                continue
            for X_use, auc_acc, p30_acc in (
                (X_v3, ctrl_auc, ctrl_p30),
                (X_v3_v6, trt_auc, trt_p30),
            ):
                model = xgb.XGBClassifier(**XGB_PARAMS)
                model.fit(X_use.loc[tr], y_bin.loc[tr], verbose=False)
                p = model.predict_proba(X_use.loc[te])[:, 1]
                # AUC
                from sklearn.metrics import roc_auc_score
                if y_bin.loc[te].sum() > 0 and y_bin.loc[te].sum() < te.sum():
                    auc_acc.append(roc_auc_score(y_bin.loc[te], p))
                # Precision@30 (top-30 by predicted prob in this test fold)
                k = min(30, te.sum())
                top_idx = np.argsort(p)[-k:]
                p30_acc.append(y_bin.loc[te].values[top_idx].mean())
        results[tier] = {
            "threshold": thr, "pos_rate": float(pos_rate),
            "auc_ctrl": float(np.mean(ctrl_auc)), "auc_trt": float(np.mean(trt_auc)),
            "auc_delta": float(np.mean(trt_auc) - np.mean(ctrl_auc)),
            "p30_ctrl": float(np.mean(ctrl_p30)), "p30_trt": float(np.mean(trt_p30)),
            "p30_delta": float(np.mean(trt_p30) - np.mean(ctrl_p30)),
            "n_folds_evaluated": min(len(ctrl_auc), len(trt_auc)),
        }
        r = results[tier]
        print(f"{tier:<8} {thr:>5.2f} {pos_rate*100:>5.1f}% "
              f"{r['auc_ctrl']:>10.4f} {r['auc_trt']:>10.4f} {r['auc_delta']:>+10.4f} "
              f"{r['p30_ctrl']:>10.3f} {r['p30_trt']:>10.3f} {r['p30_delta']:>+10.3f}")
    print()

    # Headline: which tier has the largest ELITE-friendly lift?
    elite_p30 = results.get("ELITE", {}).get("p30_delta", 0)
    broad_p30 = results.get("BROAD", {}).get("p30_delta", 0)
    if elite_p30 > 0.02 and elite_p30 > broad_p30:
        print(f"  VERDICT: v6 lifts ELITE precision@30 by {elite_p30:+.3f} "
              f"(BROAD only {broad_p30:+.3f}) — M.md thesis SUPPORTED")
        print("  Recommendation: ship v6 as ELITE-tier-only feature add")
    elif elite_p30 < -0.02:
        print(f"  VERDICT: v6 HURTS ELITE precision@30 by {elite_p30:+.3f} — DO NOT SHIP")
    else:
        print(f"  VERDICT: v6 effect on ELITE precision is ambiguous "
              f"(ELITE Δ={elite_p30:+.3f}, BROAD Δ={broad_p30:+.3f})")
    return results


# ──────────────────────────────────────────────────────────────────────
# D5. Lookahead audit (static documentation)
# ──────────────────────────────────────────────────────────────────────


LOOKAHEAD_TABLE = {
    "vpin_d0":            ("full RTH d0",         "post-09:30"),
    "ofi_first30_d0":     ("[09:30, 10:00) ET d0", "post-09:30"),
    "kyle_lambda_d0":     ("full RTH d0 5min bars", "post-09:30"),
    "hawkes_fano_d0":     ("full RTH d0",         "post-09:30"),
    "amihud_illiq_d0":    ("full RTH d0 5min bars", "post-09:30"),
    "iso_sweep_count_d0": ("full RTH d0",         "post-09:30"),
}


def diagnostic_5_lookahead_audit() -> dict:
    section("D5. LOOKAHEAD AUDIT — when can each v6 feature feed a decision?")
    print("  feature              source window           observable for entry-at")
    print("  " + "-" * 67)
    for feat, (window, latest) in LOOKAHEAD_TABLE.items():
        print(f"  {feat:<20} {window:<22} {latest}")
    print()
    print("  IMPLICATION: every v6 feature uses post-09:30 ET trades on d0.")
    print("  - Cannot feed an 'enter at 09:30:00 open' decision (lookahead).")
    print("  - CAN feed a 'should I hold past 09:35 / take profit / scale up' decision.")
    print("  - Production wiring requires explicit decision-time alignment.")
    print("  - Research-evaluation-of-ret_t5 rankings (this validation) is fine —")
    print("    we use d0 morning info to predict d0..d+5 return, no leakage there.")
    return {"lookahead_table": LOOKAHEAD_TABLE}


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v6-path", default=str(DERIVED / "microstructure_v6_pack.parquet"))
    ap.add_argument("--n-permutations", type=int, default=20)
    ap.add_argument("--skip-d1", action="store_true", help="skip noise-floor (slow)")
    args = ap.parse_args()
    v6_path = Path(args.v6_path)

    section("LOAD")
    X_v3, v6_block, y, d0, merged = load_data(v6_path)
    print(f"  X_v3: {X_v3.shape}  v6_block: {v6_block.shape}  y: {y.shape}  d0: {d0.shape}")

    out: dict = {}
    out["d5_lookahead"] = diagnostic_5_lookahead_audit()
    if not args.skip_d1:
        out["d1_noise_floor"] = diagnostic_1_noise_floor(X_v3, v6_block, y, d0,
                                                          n_permutations=args.n_permutations)
    out["d2_per_decile"] = diagnostic_2_per_decile(X_v3, v6_block, y, d0)
    out["d3_liquidity_ablation"] = diagnostic_3_liquidity_ablation(
        X_v3, v6_block, y, d0, merged, v6_path)
    out["d4_per_tier"] = diagnostic_4_per_tier(X_v3, v6_block, y, d0)

    out_path = MODELS / "v6_phase1_diagnostics.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
