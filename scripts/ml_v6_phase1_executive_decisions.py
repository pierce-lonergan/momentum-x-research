"""v6 Phase 1 — executive-decision diagnostics (per user critique on doc 137).

Three measurements that gate every downstream decision:

  ITEM 1 (CPU-DETERMINISTIC RE-RUN):
    Re-run v3-only vs v3+d-1 validation with device='cpu' for full
    XGBoost determinism. Single canonical VETOED P@30 number. Removes
    the [+0.025, +0.061] CUDA-determinism range. The deterministic
    number is the gate for any production rec.

  ITEM 2 (STANDALONE DSR ON ELITE):
    Compute Deflated Sharpe Ratio of v3 ELITE specialist's per-day
    top-N picks. Apply with conservative N_trials (10, 50, 100) for
    sensitivity. M.md §3 third option: honest acceptance via DSR
    that ELITE is statistical noise. If DSR < 0.5 even at N=50, ELITE
    capital allocation is gambling against our own analysis.

  ITEM 3 (TIER-CONFUSION MATRIX):
    For control vs treatment OOS predictions, assign each pick a tier
    based on regression-prediction decile. Build 4x4 confusion matrix
    (ctrl_tier, trt_tier). Off-diagonal-up mass = tier expansion (good);
    off-diagonal-down mass = tier cannibalization (bad — explains the
    HIGH P@30 regression in doc 137 §4).

We DO NOT write the verdict in this script. Doc 138 is written AFTER
all three numbers are in hand. The verdict section is left literally
blank until then.

USAGE:
  python scripts/ml_v6_phase1_executive_decisions.py
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# ITEM 1: CPU-deterministic re-run
# ──────────────────────────────────────────────────────────────────────


def walk_forward_cpu(X: pd.DataFrame, y: pd.Series, d0: pd.Series,
                     n_folds: int = 12, train_days: int = 120, test_days: int = 15,
                     seed: int = 42) -> pd.DataFrame:
    """Same WF as ml_v6_phase1_validation but with device='cpu' for determinism.

    CPU XGBoost is deterministic under fixed seed; GPU CUDA reductions are not.
    We also pin n_jobs=1 (single-threaded) to remove parallel-reduction noise."""
    import xgboost as xgb
    XGB_PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist",
        "device": "cpu",       # <-- CRITICAL: deterministic across runs
        "n_jobs": 1,           # <-- single-thread for reproducibility
        "random_state": seed,
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
        if tr.sum() < 100 or te.sum() < 5:
            continue
        m = xgb.XGBRegressor(**XGB_PARAMS)
        m.fit(X.loc[tr], y.loc[tr], verbose=False)
        rows.append(pd.DataFrame({
            "fold": f, "d0": d0.loc[te].values,
            "y_true": y.loc[te].values, "y_pred": m.predict(X.loc[te]),
            "_idx": np.where(te)[0],
        }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def per_tier_p30_cpu(X: pd.DataFrame, y_reg: pd.Series, d0: pd.Series,
                      seed: int = 42) -> dict:
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score
    PARAMS = {
        "objective": "binary:logistic", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1,
        "random_state": seed, "eval_metric": "logloss",
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
            if tr.sum() < 100 or te.sum() < 30 or y_bin.loc[tr].sum() < 5:
                continue
            m = xgb.XGBClassifier(**PARAMS)
            m.fit(X.loc[tr], y_bin.loc[tr], verbose=False)
            p = m.predict_proba(X.loc[te])[:, 1]
            if y_bin.loc[te].sum() > 0 and y_bin.loc[te].sum() < te.sum():
                aucs.append(roc_auc_score(y_bin.loc[te], p))
            top_idx = np.argsort(p)[-min(30, te.sum()):]
            p30s.append(y_bin.loc[te].values[top_idx].mean())
        out[tier] = {"thr": thr, "auc": float(np.mean(aucs)),
                     "p30": float(np.mean(p30s)), "n_folds": len(p30s)}
    return out


def item_1_cpu_deterministic_rerun() -> dict:
    section("ITEM 1 — CPU-DETERMINISTIC RE-RUN of v3+d-1 validation")
    from ml_continuer_v2_ensemble import load_data, engineer_features

    df = load_data(include_paths=True)
    v6_dm1 = pd.read_parquet(DERIVED / "microstructure_v6_pack_dminus1.parquet")
    df["d0"] = pd.to_datetime(df["d0"])
    v6_dm1["d0"] = pd.to_datetime(v6_dm1["d0"])
    V6_FEATURES = ["vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
                   "hawkes_fano_d0", "amihud_illiq_d0", "iso_sweep_count_d0"]
    merged = df.merge(v6_dm1[["ticker", "d0", *V6_FEATURES]],
                      on=["ticker", "d0"], how="inner")

    X_v3 = engineer_features(merged).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    v6_block = merged[V6_FEATURES].copy().replace([np.inf, -np.inf], np.nan)
    v6_block["log_kyle_lambda_d0"] = np.log1p(v6_block["kyle_lambda_d0"].clip(lower=0))
    v6_block["log_hawkes_fano_d0"] = np.log1p(v6_block["hawkes_fano_d0"].clip(lower=0))
    v6_block["log_amihud_illiq_d0"] = np.log1p(v6_block["amihud_illiq_d0"].clip(lower=0))
    v6_block["log_iso_sweep_count_d0"] = np.log1p(v6_block["iso_sweep_count_d0"].clip(lower=0))
    v6_block = v6_block.fillna(0).reset_index(drop=True)
    y = merged["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(merged["d0"]).reset_index(drop=True)

    print(f"  X_v3: {X_v3.shape}  v6_dm1_block: {v6_block.shape}  rows: {len(y):,}")
    X_v3_dm1 = pd.concat([X_v3, v6_block], axis=1)

    print("\n  control (v3-only) — CPU, n_jobs=1, seed=42")
    t0 = time.time()
    preds_ctrl = walk_forward_cpu(X_v3, y, d0, seed=42)
    rho_ctrl = float(spearmanr(preds_ctrl["y_true"], preds_ctrl["y_pred"]).statistic)
    print(f"  control finished in {time.time()-t0:.1f}s, Spearman={rho_ctrl:+.6f}")

    print("\n  treatment (v3+d-1) — CPU, n_jobs=1, seed=42")
    t0 = time.time()
    preds_trt = walk_forward_cpu(X_v3_dm1, y, d0, seed=42)
    rho_trt = float(spearmanr(preds_trt["y_true"], preds_trt["y_pred"]).statistic)
    print(f"  treatment finished in {time.time()-t0:.1f}s, Spearman={rho_trt:+.6f}")

    print("\n  per-tier P@30 — CPU control")
    t0 = time.time()
    tier_ctrl = per_tier_p30_cpu(X_v3, y, d0, seed=42)
    print(f"  control per-tier in {time.time()-t0:.1f}s")
    print("\n  per-tier P@30 — CPU treatment")
    t0 = time.time()
    tier_trt = per_tier_p30_cpu(X_v3_dm1, y, d0, seed=42)
    print(f"  treatment per-tier in {time.time()-t0:.1f}s")

    print()
    print("  SINGLE CANONICAL CPU NUMBERS (deterministic):")
    print("  tier      P@30_ctrl   P@30_trt    delta")
    for tier in ("BROAD", "VETOED", "HIGH", "ELITE"):
        c = tier_ctrl[tier]["p30"]; t = tier_trt[tier]["p30"]
        print(f"  {tier:<8}   {c:>9.4f}   {t:>9.4f}   {t-c:>+8.4f}")
    print(f"  Spearman delta:                            {rho_trt - rho_ctrl:>+8.4f}")
    # Save preds for item 3
    preds_ctrl.to_parquet(DERIVED / "ml_v6_phase1_cpu_control_preds.parquet")
    preds_trt.to_parquet(DERIVED / "ml_v6_phase1_cpu_treatment_preds.parquet")
    return {
        "spearman_ctrl": rho_ctrl, "spearman_trt": rho_trt,
        "spearman_delta": rho_trt - rho_ctrl,
        "tier_ctrl": tier_ctrl, "tier_trt": tier_trt,
        "tier_deltas": {t: tier_trt[t]["p30"] - tier_ctrl[t]["p30"]
                         for t in tier_ctrl},
    }


# ──────────────────────────────────────────────────────────────────────
# ITEM 2: Standalone DSR on ELITE
# ──────────────────────────────────────────────────────────────────────


def deflated_sharpe(daily_pnl: np.ndarray, n_trials: int) -> dict:
    """Bailey-LdP JPM 2014 DSR with skew/kurt correction.

    Formula (matches ml_v6_phase_0_validation.py which produced doc 133):
      sr_max_h0 (in std-units) = (1-gamma)*Z[1-1/N] + gamma*Z[1-1/(Ne)]
      sr_max_threshold = sr_max_h0 * sigma_sr   <-- KEY: convert to per-period units
      z = (sr - sr_max_threshold) / sigma_sr
      DSR = Phi(z)

    Earlier version of this function had a bug: forgot to multiply sr_max_h0
    by sigma_sr, producing implausible H0 thresholds (~25 annualized Sharpe
    for N=10) that made every observed strategy fail trivially.
    """
    pnl = daily_pnl[~np.isnan(daily_pnl)]
    if len(pnl) < 15:
        return {"error": f"only {len(pnl)} days"}
    mean = pnl.mean(); std = pnl.std(ddof=1)
    if std <= 0: return {"error": "zero std"}
    sr = mean / std; sr_ann = sr * np.sqrt(252)
    skew = float(((pnl - mean) ** 3).mean() / std ** 3)
    kurt_ex = float(((pnl - mean) ** 4).mean() / std ** 4 - 3)
    T = len(pnl)
    sigma_sr = float(np.sqrt(
        max(1e-8, (1 - skew * sr + (kurt_ex / 4) * sr ** 2) / (T - 1))
    ))
    if n_trials <= 1:
        sr_max_h0_std_units = 0.0
    else:
        gamma = 0.5772156649
        sr_max_h0_std_units = float(
            (1 - gamma) * norm.ppf(1 - 1 / n_trials) +
            gamma * norm.ppf(1 - 1 / (n_trials * np.e))
        )
    # CRITICAL: convert sr_max from std-units to per-period Sharpe units
    sr_max_threshold = sr_max_h0_std_units * sigma_sr
    z = (sr - sr_max_threshold) / sigma_sr if sigma_sr > 0 else 0.0
    return {
        "T_days": T, "sr_per_period": float(sr), "sr_annualized": float(sr_ann),
        "skew": skew, "excess_kurtosis": kurt_ex, "sigma_sr": sigma_sr,
        "n_trials": n_trials,
        "sr_max_h0_std_units": sr_max_h0_std_units,
        "sr_max_threshold_perperiod": float(sr_max_threshold),
        "z_score": float(z),
        "dsr": float(norm.cdf(z)),
    }


def item_2_elite_dsr() -> dict:
    section("ITEM 2 — STANDALONE DSR on v3 ELITE specialist")
    print("  M.md § caveat: 'honest acceptance via DSR that ELITE is not")
    print("  statistically significant and should not receive Aggressive Kelly capital'")
    print()

    elite_preds = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_ELITE_predictions.parquet")
    print(f"  ELITE specialist preds: {len(elite_preds):,} rows, "
          f"{elite_preds['d0'].nunique()} unique days, {elite_preds['fold'].nunique()} folds")

    # Compute per-day P&L from top-N picks by prob_specialist_ELITE
    out = {}
    for top_n in (3, 5, 10):
        per_day = (elite_preds
                   .sort_values(["d0", "prob_specialist_ELITE"], ascending=[True, False])
                   .groupby("d0")
                   .head(top_n)
                   .groupby("d0")["y_reg"]
                   .mean())
        pnl = per_day.dropna().values
        print(f"\n  TOP-{top_n}/day picks:  T={len(pnl)} trading days "
              f"mean={pnl.mean()*100:+.3f}%  std={pnl.std()*100:.3f}%  "
              f"sharpe_per={pnl.mean()/pnl.std():+.4f}  "
              f"sharpe_ann={pnl.mean()/pnl.std()*np.sqrt(252):+.3f}")
        # DSR sweep across N_trials
        for n_trials in (1, 10, 50, 100):
            d = deflated_sharpe(pnl, n_trials=n_trials)
            print(f"    N_trials={n_trials:>4}  DSR={d.get('dsr',float('nan')):.4f}  "
                  f"z={d.get('z_score',float('nan')):+.3f}  "
                  f"sr_max_H0={d.get('sr_max_under_H0',float('nan')):+.4f}")
            out[f"top{top_n}_n{n_trials}"] = d

    # Compare to BROAD specialist as control (BROAD trains on more positives,
    # we'd expect higher DSR on a wider universe).
    print("\n  COMPARISON: same calculation for v3 BROAD specialist (top-30/day)")
    broad_preds = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    per_day_b = (broad_preds
                 .sort_values(["d0", "prob_specialist_BROAD"], ascending=[True, False])
                 .groupby("d0").head(30).groupby("d0")["y_reg"].mean())
    pnl_b = per_day_b.dropna().values
    print(f"  BROAD top-30/day:  T={len(pnl_b)}  mean={pnl_b.mean()*100:+.3f}%  "
          f"sharpe_ann={pnl_b.mean()/pnl_b.std()*np.sqrt(252):+.3f}")
    for n_trials in (1, 10, 50):
        d = deflated_sharpe(pnl_b, n_trials=n_trials)
        print(f"    N_trials={n_trials:>4}  DSR={d.get('dsr',float('nan')):.4f}  "
              f"z={d.get('z_score',float('nan')):+.3f}")
        out[f"broad_top30_n{n_trials}"] = d
    return out


# ──────────────────────────────────────────────────────────────────────
# ITEM 3: Tier-confusion matrix
# ──────────────────────────────────────────────────────────────────────


def item_3_tier_confusion(item1_results: dict) -> dict:
    section("ITEM 3 — TIER-CONFUSION MATRIX from CPU OOS predictions")
    preds_ctrl = pd.read_parquet(DERIVED / "ml_v6_phase1_cpu_control_preds.parquet")
    preds_trt = pd.read_parquet(DERIVED / "ml_v6_phase1_cpu_treatment_preds.parquet")
    # Align on _idx
    preds = preds_ctrl[["_idx", "d0", "y_true", "y_pred"]].rename(columns={"y_pred": "ctrl_pred"})
    preds = preds.merge(
        preds_trt[["_idx", "y_pred"]].rename(columns={"y_pred": "trt_pred"}),
        on="_idx",
    )
    print(f"  aligned OOS preds: {len(preds):,}")

    # Assign tier per pred via PER-DAY decile rank — production trades top picks
    # PER DAY, not on a global threshold.
    def assign_tier_perday(g: pd.DataFrame, pred_col: str) -> pd.Series:
        n = len(g)
        if n < 4:
            return pd.Series(["SKIP"] * n, index=g.index)
        ranks = g[pred_col].rank(ascending=False, method="first")
        # Tier definitions per production cascade:
        #   ELITE  = top 7% per day  (matches ELITE positive rate ~7%)
        #   HIGH   = next 12%        (cumul 19%)
        #   VETOED = next 18%        (cumul 37%)
        #   BROAD  = next 25%        (cumul 62%)
        #   SKIP   = bottom 38%
        tier = pd.Series(["SKIP"] * n, index=g.index, dtype=object)
        tier.loc[ranks <= n * 0.07] = "ELITE"
        tier.loc[(ranks > n * 0.07) & (ranks <= n * 0.19)] = "HIGH"
        tier.loc[(ranks > n * 0.19) & (ranks <= n * 0.37)] = "VETOED"
        tier.loc[(ranks > n * 0.37) & (ranks <= n * 0.62)] = "BROAD"
        return tier

    preds["ctrl_tier"] = preds.groupby("d0", group_keys=False).apply(
        lambda g: assign_tier_perday(g, "ctrl_pred"))
    preds["trt_tier"] = preds.groupby("d0", group_keys=False).apply(
        lambda g: assign_tier_perday(g, "trt_pred"))

    TIER_ORDER = ["ELITE", "HIGH", "VETOED", "BROAD", "SKIP"]
    cmat = pd.crosstab(preds["ctrl_tier"], preds["trt_tier"]).reindex(
        index=TIER_ORDER, columns=TIER_ORDER, fill_value=0)
    print("\n  Confusion matrix (rows=ctrl tier, cols=trt tier):")
    print(cmat.to_string())

    # Tier-rank ordering for upgrade/downgrade analysis
    tier_rank = {"ELITE": 4, "HIGH": 3, "VETOED": 2, "BROAD": 1, "SKIP": 0}
    preds["ctrl_rank"] = preds["ctrl_tier"].map(tier_rank)
    preds["trt_rank"] = preds["trt_tier"].map(tier_rank)
    upgraded = (preds["trt_rank"] > preds["ctrl_rank"]).sum()
    same = (preds["trt_rank"] == preds["ctrl_rank"]).sum()
    downgraded = (preds["trt_rank"] < preds["ctrl_rank"]).sum()
    n = len(preds)
    print(f"\n  Tier movement (treatment vs control):")
    print(f"    upgraded:    {upgraded:>5,}  ({upgraded/n*100:>5.1f}%)")
    print(f"    same tier:   {same:>5,}  ({same/n*100:>5.1f}%)")
    print(f"    downgraded:  {downgraded:>5,}  ({downgraded/n*100:>5.1f}%)")

    # HIGH-specific: where do ctrl-HIGH picks GO under treatment?
    high_movement = preds[preds["ctrl_tier"] == "HIGH"]["trt_tier"].value_counts()
    print(f"\n  Where do ctrl-HIGH picks land under treatment? (n={(preds['ctrl_tier']=='HIGH').sum()})")
    for t in TIER_ORDER:
        cnt = high_movement.get(t, 0)
        share = cnt / max(1, (preds["ctrl_tier"] == "HIGH").sum()) * 100
        print(f"    -> {t:<8}: {cnt:>4,}  ({share:>5.1f}%)")

    # ELITE-specific: where do ctrl-ELITE picks GO under treatment?
    elite_movement = preds[preds["ctrl_tier"] == "ELITE"]["trt_tier"].value_counts()
    print(f"\n  Where do ctrl-ELITE picks land under treatment? (n={(preds['ctrl_tier']=='ELITE').sum()})")
    for t in TIER_ORDER:
        cnt = elite_movement.get(t, 0)
        share = cnt / max(1, (preds["ctrl_tier"] == "ELITE").sum()) * 100
        print(f"    -> {t:<8}: {cnt:>4,}  ({share:>5.1f}%)")

    # MEAN realized return per tier (control vs treatment)
    print(f"\n  MEAN realized ret_t5 per tier:")
    print("  tier      ctrl_n   ctrl_mean   trt_n     trt_mean    diff")
    for t in TIER_ORDER:
        cm = preds[preds["ctrl_tier"] == t]["y_true"]
        tm = preds[preds["trt_tier"] == t]["y_true"]
        cn = len(cm); tn = len(tm)
        cmean = cm.mean() if cn else float("nan")
        tmean = tm.mean() if tn else float("nan")
        diff = (tmean - cmean) if (cn and tn) else float("nan")
        print(f"  {t:<8}   {cn:>5,}   {cmean:>+9.4f}   {tn:>5,}   {tmean:>+9.4f}   {diff:>+8.4f}")

    return {
        "confusion_matrix": cmat.to_dict(),
        "n_upgraded": int(upgraded), "n_same": int(same), "n_downgraded": int(downgraded),
        "high_movement": high_movement.to_dict(),
        "elite_movement": elite_movement.to_dict(),
        "tier_realized_ret": {
            t: {"ctrl_n": int((preds["ctrl_tier"] == t).sum()),
                "ctrl_mean": float(preds.loc[preds["ctrl_tier"] == t, "y_true"].mean()),
                "trt_n": int((preds["trt_tier"] == t).sum()),
                "trt_mean": float(preds.loc[preds["trt_tier"] == t, "y_true"].mean())}
            for t in TIER_ORDER
        },
    }


def main() -> int:
    out = {}
    out["item_1_cpu_rerun"] = item_1_cpu_deterministic_rerun()
    out["item_2_elite_dsr"] = item_2_elite_dsr()
    out["item_3_tier_confusion"] = item_3_tier_confusion(out["item_1_cpu_rerun"])

    out_path = MODELS / "v6_phase1_executive_decisions.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
