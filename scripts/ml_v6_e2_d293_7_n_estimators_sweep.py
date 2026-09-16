"""Experiment 2 of doc 162 — D293.7 n_estimators sweep on fresh data.

Pre-commits locked in doc 162 §1 (E2):
  CLEAR WINNER: any n_est beats current n_est=2 by >= +0.010 recent
                Spearman -> ship D293c (update tabpfn_shadow_runner.N_ESTIMATORS)
  TIE/DEGRAD:   best non-2 within +-0.010 of n_est=2 -> keep current
  PUBLISHABLE:  pairwise rho_n_est across all 4 configs mean < 0.85
                -> publishable n_estimators-sensitivity finding
"""
from __future__ import annotations
import gc
import json
import sys
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def run_tabpfn_one_config(X, y, d0, fold_starts, train_days, test_days,
                           max_train, n_estimators, seed=42):
    """Single n_estimators value, fixed seed=42."""
    import torch
    from tabpfn import TabPFNRegressor
    rows = []
    total_t = 0.0
    for fi, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5:
            continue
        Xtr, ytr = X.loc[tr].values, y.loc[tr].values
        Xte, yte = X.loc[te].values, y.loc[te].values
        if len(Xtr) > max_train:
            rng = np.random.default_rng(seed + fi)
            idx = rng.choice(len(Xtr), max_train, replace=False)
            Xtr, ytr = Xtr[idx], ytr[idx]
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            gc.collect()
        t0 = time.time()
        try:
            m = TabPFNRegressor(device="cuda", random_state=seed,
                                 n_estimators=n_estimators)
            m.fit(Xtr, ytr)
            pred = m.predict(Xte)
        except Exception as e:
            print(f"    fold {fi:>2} (n_est={n_estimators}) FAILED: {e}")
            continue
        elapsed = time.time() - t0
        total_t += elapsed
        rows.append(pd.DataFrame({
            "fold": fi, "d0": d0.loc[te].values,
            "y_true": yte, "y_pred": pred,
            "_idx": np.where(te)[0],
        }))
        del m
    return pd.concat(rows, ignore_index=True), total_t


def main() -> int:
    section("STEP 1 -- load fresh data")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  loaded {len(df):,} rows, max d0 = {d0.max().date()}")

    n_folds = 12
    train_days = 120
    test_days = 15
    fold_starts = pd.date_range(
        d0.min() + pd.Timedelta(days=train_days),
        d0.max() - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  WF: {n_folds} folds, train={train_days}d, test={test_days}d")

    section("STEP 2 -- TabPFN sweep across n_estimators in {1, 2, 4, 8}")
    configs = [1, 2, 4, 8]
    config_results = {}
    for n_est in configs:
        print(f"\n  --- n_estimators={n_est} ---")
        preds, elapsed = run_tabpfn_one_config(
            X, y, d0, fold_starts, train_days, test_days,
            max_train=2500, n_estimators=n_est,
        )
        if len(preds) == 0:
            print(f"  NO predictions for n_est={n_est}, skipping")
            continue
        config_results[n_est] = preds
        print(f"  total wallclock: {elapsed:.1f}s, n_oos = {len(preds):,}")

    section("STEP 3 -- per-config recent-subset Spearman")
    summary = {}
    for n_est, preds in config_results.items():
        recent = preds[preds["fold"] >= 8]  # folds 8-11 = recent (matches doc 152)
        full_rho = float(spearmanr(preds["y_true"], preds["y_pred"]).statistic)
        recent_rho = float(spearmanr(recent["y_true"], recent["y_pred"]).statistic)
        summary[n_est] = {
            "n_oos_full": int(len(preds)),
            "n_oos_recent": int(len(recent)),
            "spearman_full": full_rho,
            "spearman_recent": recent_rho,
        }
        print(f"  n_est={n_est}: full={full_rho:+.4f}  recent (folds 8-11, n={len(recent):,})={recent_rho:+.4f}")

    section("STEP 4 -- pairwise rho between configs (the doc 154 question)")
    pairs = list(combinations(sorted(config_results.keys()), 2))
    pair_rhos = []
    for a, b in pairs:
        # Inner-join preds on (fold, _idx) to align
        pa = config_results[a].rename(columns={"y_pred": f"pred_n{a}"})
        pb = config_results[b][["fold", "_idx", "y_pred"]].rename(columns={"y_pred": f"pred_n{b}"})
        merged = pa.merge(pb, on=["fold", "_idx"], how="inner")
        if len(merged) < 50:
            continue
        rho = float(spearmanr(merged[f"pred_n{a}"], merged[f"pred_n{b}"]).statistic)
        pair_rhos.append(((a, b), rho))
        print(f"  rho(n_est={a}, n_est={b}) = {rho:+.4f}  (n={len(merged):,} aligned)")
    mean_pair_rho = float(np.mean([r for _, r in pair_rhos])) if pair_rhos else 0
    print(f"  mean pairwise rho across all configs: {mean_pair_rho:+.4f}")

    section("STEP 5 -- VERDICT against doc 162 §1 E2 pre-commits")
    # Find best config by recent Spearman
    if 2 in summary:
        baseline_recent = summary[2]["spearman_recent"]
    else:
        baseline_recent = 0.0
    best_config = max(summary.items(), key=lambda kv: kv[1]["spearman_recent"])
    best_n_est, best_data = best_config
    delta_vs_2 = best_data["spearman_recent"] - baseline_recent

    print(f"  Current production: n_est=2, recent Spearman={baseline_recent:+.4f}")
    print(f"  Best config: n_est={best_n_est}, recent Spearman={best_data['spearman_recent']:+.4f}")
    print(f"  Delta vs n_est=2: {delta_vs_2:+.4f}")
    print(f"  Mean pairwise rho: {mean_pair_rho:+.4f}")

    if best_n_est != 2 and delta_vs_2 >= 0.010:
        gate = "CLEAR WINNER"
        verdict = (f"Ship D293c: update tabpfn_shadow_runner.N_ESTIMATORS "
                   f"from 2 to {best_n_est} (Spearman gain {delta_vs_2:+.4f})")
    elif abs(delta_vs_2) < 0.010 or (best_n_est == 2):
        gate = "TIE/DEGRADATION"
        verdict = "Keep n_est=2; close D293.7"
    else:
        gate = "MIXED"
        verdict = f"Best config n_est={best_n_est} gain {delta_vs_2:+.4f} - within tolerance"

    publishable = mean_pair_rho < 0.85
    if publishable:
        verdict += " | PUBLISHABLE: mean pairwise rho < 0.85, n_estimators-sensitivity finding extends doc 154's rho=0.57"

    print(f"  GATE: {gate}")
    print(f"  ACTION: {verdict}")

    out = {
        "max_d0_in_lake": str(d0.max().date()),
        "configs_tested": list(configs),
        "per_config": {str(k): v for k, v in summary.items()},
        "pairwise_rho": {f"{a}_vs_{b}": rho for (a, b), rho in pair_rhos},
        "mean_pairwise_rho": mean_pair_rho,
        "best_config_n_est": int(best_n_est),
        "best_recent_spearman": float(best_data["spearman_recent"]),
        "baseline_n_est_2_recent": float(baseline_recent),
        "delta_vs_2": float(delta_vs_2),
        "gate": gate,
        "publishable": bool(publishable),
        "verdict": verdict,
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    out_path = MODELS / "v6_e2_d293_7_n_estimators_sweep_2026_05_12.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
