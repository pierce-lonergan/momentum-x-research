"""Experiment 1 of doc 152 — TabPFN augmented variant (Frontier 1, 1-week).

Honest scope substitution from doc 152 §1:
  - Compass §Frontier 1 1-week version: "Continual pretraining of TabPFNv2
    on the user's 60k unlabeled Polygon candidates (à la Real-TabPFN). Single RTX 5070."
  - Reality 1: aftermath_strat has 20,029 labeled / 386 unlabeled. The "60k
    unlabeled" overstates available data by ~3x.
  - Reality 2: tabpfn-extensions 0.4.1's AutoTabPFNRegressor is a stub
    requiring autogluon.tabular (~500MB+ extra install); TunedTabPFNRegressor
    is undocumented; TabPFNUnsupervisedModel is for imputation only.
  - Reality 3: Real-TabPFN's continual-pretraining recipe (arXiv:2507.03971)
    is PriorLabs internal infrastructure, not OSS-released.

Closest feasible aggressive variant on RTX 5070, no new deps:
  Multi-seed TabPFN ensemble. Run TabPFNRegressor with N different
  random_state seeds per fold, mean-aggregate predictions, compare to
  single-seed vanilla TabPFN. This is the OSS-API-level analogue of
  Real-TabPFN's "more pretraining compute -> better calibration":
  multiple inference passes -> variance reduction.

Pre-commits locked in doc 152 §1 (Experiment 1):
  Gate PASS: augmented (ensemble) beats vanilla on recent-subset Spearman
    by >= +0.01, with permutation noise floor p < 0.05.
  Gate MARGINAL: lift in [0, +0.01) OR lift >= +0.01 but p >= 0.05.
  Gate FAIL: ensemble underperforms vanilla on recent subset.

Note: results published in doc 152 §5 will document the variant
substitution explicitly.
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


def neutralized_rho(y_pred, y_true, exposures, proportion=1.0):
    F = exposures.values.astype(float)
    F = F - F.mean(axis=0)
    F_pinv = np.linalg.pinv(F)
    p = y_pred.astype(float)
    p_proj = F @ (F_pinv @ p)
    p_neut = p - proportion * p_proj
    if p_neut.std() > 0:
        p_neut = p_neut / p_neut.std()
    return float(spearmanr(y_true, p_neut).statistic)


def build_exposures(df):
    n = len(df)
    exp = pd.DataFrame({
        "log_market_cap": np.log1p(df.get("market_cap", pd.Series(np.zeros(n))).fillna(0)),
        "log_dvol_d0":    np.log1p(df.get("dvol_d0", pd.Series(np.zeros(n))).fillna(0)),
        "prior_avg_t5":   df.get("prior_avg_t5", pd.Series(np.zeros(n))).fillna(0),
        "intraday_pct":   df.get("intraday_pct", pd.Series(np.zeros(n))).fillna(0),
    })
    return exp


def run_tabpfn_one_seed(X, y, d0, fold_starts, train_days, test_days,
                        max_train, seed):
    """One TabPFNRegressor per fold with given seed; returns OOS preds.

    Per compass §Topic 7 + §Topic 14 §Experiment 1: explicit n_estimators=2
    keeps VRAM in the documented 1.0-1.5 GB envelope at MAX_TRAIN=2500 × 54
    features. tabpfn v7.1.1's default n_estimators is higher and causes
    order-of-magnitude slowdown on RTX 5070 (observed: >75 min for one
    seed with default config; expected: ~30 sec/seed total at n_est=2).
    """
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
            rng = np.random.default_rng(seed + fi)
            idx = rng.choice(len(Xtr), max_train, replace=False)
            Xtr = Xtr[idx]; ytr = ytr[idx]
        t0 = time.time()
        try:
            m = TabPFNRegressor(device="cuda", random_state=seed + fi,
                                 n_estimators=2)
            m.fit(Xtr, ytr)
            pred = m.predict(Xte)
        except Exception as e:
            print(f"  fold {fi:>2} seed {seed}: {e}", flush=True)
            continue
        rho = float(spearmanr(yte, pred).statistic)
        elapsed = time.time() - t0
        print(f"  seed {seed} fold {fi:>2}: tr={tr.sum():>5} te={te.sum():>4} "
              f"Spearman={rho:+.3f} ({elapsed:.0f}s)", flush=True)
        rows.append(pd.DataFrame({
            "fold": fi, "d0": d0.loc[te].values,
            "y_true": yte, "y_pred": pred,
            "_idx": np.where(te)[0], "seed": seed,
        }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-seeds", type=int, default=5,
                    help="ensemble size (each seed = one TabPFN inference pass per fold)")
    ap.add_argument("--max-train", type=int, default=2500,
                    help="MAX_TRAIN for CUDA stability (matches doc 143 baseline)")
    ap.add_argument("--n-permutations", type=int, default=5)
    args = ap.parse_args()

    section("STEP 1 — load v3 panel + engineer features")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  loaded {len(df):,} rows, {X.shape[1]} features")

    n_folds = 12
    train_days = 120
    test_days = 15
    fold_starts = pd.date_range(
        d0.min() + pd.Timedelta(days=train_days),
        d0.max() - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  WF: {n_folds} folds, train={train_days}d, test={test_days}d")
    print(f"  variant: multi-seed ensemble, n_seeds={args.n_seeds}, max_train={args.max_train}")

    section(f"STEP 2 — run TabPFN with {args.n_seeds} seeds (ensemble + vanilla baseline)")
    seed_preds = []
    for si in range(args.n_seeds):
        seed = 42 + si * 1000
        t0 = time.time()
        sp = run_tabpfn_one_seed(X, y, d0, fold_starts, train_days, test_days,
                                  args.max_train, seed)
        elapsed = time.time() - t0
        n_oos = len(sp)
        print(f"  seed {seed} ({si+1}/{args.n_seeds}): {n_oos:,} OOS rows in {elapsed:.0f}s")
        seed_preds.append(sp)

    section("STEP 3 — vanilla (single-seed) vs ensemble (mean across seeds)")
    vanilla = seed_preds[0].copy()  # first seed = vanilla baseline
    # Build ensemble: align on (fold, _idx) and mean y_pred across seeds
    keys = ["fold", "_idx"]
    ens_df = seed_preds[0][keys + ["y_true", "d0"]].copy()
    pred_cols = []
    for si, sp in enumerate(seed_preds):
        col = f"y_pred_s{si}"
        ens_df = ens_df.merge(sp[keys + ["y_pred"]].rename(columns={"y_pred": col}),
                               on=keys, how="left")
        pred_cols.append(col)
    ens_df["y_pred_ens"] = ens_df[pred_cols].mean(axis=1)
    ens_df["y_pred_van"] = ens_df[pred_cols[0]]  # single-seed vanilla
    print(f"  aligned OOS rows: {len(ens_df):,}")

    # Per-fold and aggregate Spearman
    full_van = float(spearmanr(ens_df["y_true"], ens_df["y_pred_van"]).statistic)
    full_ens = float(spearmanr(ens_df["y_true"], ens_df["y_pred_ens"]).statistic)
    print(f"  FULL WF: vanilla Spearman = {full_van:+.4f}   ensemble = {full_ens:+.4f}   delta = {full_ens-full_van:+.4f}")

    section("STEP 4 — recent-subset (folds 8-11) -- the GATE check")
    ens_df["d0"] = pd.to_datetime(ens_df["d0"])
    recent = ens_df[ens_df["fold"] >= 8].copy()
    print(f"  recent rows: {len(recent):,}")
    if len(recent) < 50:
        print("  ERROR: insufficient recent rows")
        return 1
    rec_van = float(spearmanr(recent["y_true"], recent["y_pred_van"]).statistic)
    rec_ens = float(spearmanr(recent["y_true"], recent["y_pred_ens"]).statistic)
    rec_delta = rec_ens - rec_van
    print(f"  RECENT: vanilla = {rec_van:+.4f}   ensemble = {rec_ens:+.4f}   delta = {rec_delta:+.4f}")

    # Per-fold breakdown
    print(f"\n  Per-fold recent breakdown:")
    for fi in sorted(recent["fold"].unique()):
        sub = recent[recent["fold"] == fi]
        v = float(spearmanr(sub["y_true"], sub["y_pred_van"]).statistic)
        e = float(spearmanr(sub["y_true"], sub["y_pred_ens"]).statistic)
        print(f"    fold {fi:>2}  n={len(sub):>4}  vanilla={v:+.4f}  ensemble={e:+.4f}  delta={e-v:+.4f}")

    section("STEP 5 — permutation noise floor (gate p-value)")
    # Null: shuffle seed predictions independently, compute null delta
    null_deltas = []
    for perm in range(args.n_permutations):
        rng = np.random.default_rng(2025 + perm)
        # Per-fold shuffle of ensemble predictions vs vanilla predictions
        recent_perm = recent.copy()
        for fi in recent_perm["fold"].unique():
            m = recent_perm["fold"] == fi
            recent_perm.loc[m, "y_pred_ens"] = rng.permutation(recent_perm.loc[m, "y_pred_ens"].values)
        van_p = float(spearmanr(recent_perm["y_true"], recent_perm["y_pred_van"]).statistic)
        ens_p = float(spearmanr(recent_perm["y_true"], recent_perm["y_pred_ens"]).statistic)
        delta_p = ens_p - van_p
        null_deltas.append(delta_p)
        print(f"  perm {perm+1}/{args.n_permutations}: shuffled delta = {delta_p:+.4f}")
    null_arr = np.array(null_deltas)
    p_value = float((null_arr >= rec_delta).mean())
    print(f"  null mean: {null_arr.mean():+.4f}   max: {null_arr.max():+.4f}")
    print(f"  REAL recent delta: {rec_delta:+.4f}   p-value (one-sided): {p_value:.3f}")

    section("STEP 6 — VERDICT against doc 152 §1 Experiment 1 pre-commits")
    # Gate map:
    #   PASS: rec_delta >= +0.01 AND p < 0.05  -> ship augmented as D288 model
    #   MARGINAL: 0 <= rec_delta < +0.01 OR (rec_delta >= +0.01 AND p >= 0.05)
    #   FAIL: rec_delta < 0
    if rec_delta >= 0.01 and p_value < 0.05:
        verdict = "PASS — replace vanilla TabPFN with multi-seed ensemble for D288"
    elif rec_delta < 0:
        verdict = "FAIL — multi-seed ensemble underperforms vanilla on recent subset"
    else:
        verdict = "MARGINAL — keep vanilla as D288 model, do NOT ship ensemble"
    print(f"  recent-subset delta: {rec_delta:+.4f}")
    print(f"  noise-floor p-value: {p_value:.3f}")
    print(f"  VERDICT: {verdict}")

    out = {
        "variant": "multi-seed-ensemble",
        "n_seeds": args.n_seeds,
        "max_train": args.max_train,
        "n_oos": int(len(ens_df)),
        "n_recent": int(len(recent)),
        "full_vanilla_spearman": full_van,
        "full_ensemble_spearman": full_ens,
        "recent_vanilla_spearman": rec_van,
        "recent_ensemble_spearman": rec_ens,
        "recent_delta": rec_delta,
        "p_value": p_value,
        "null_delta_mean": float(null_arr.mean()),
        "null_delta_max": float(null_arr.max()),
        "verdict": verdict,
    }
    out_path = MODELS / "v6_e1_tabpfn_continual.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    # Also save aligned OOS preds for reproducibility
    ens_df.to_parquet(DERIVED / "ml_v6_e1_tabpfn_ensemble_preds.parquet", compression="zstd")
    print(f"  saved preds: {DERIVED / 'ml_v6_e1_tabpfn_ensemble_preds.parquet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
