"""D293.6 calibrated verification (doc 155).

Theory-grounded replacements for doc 154's heuristic Gates C and D,
plus Gate B-extended re-verification on 7 dates.

Pre-commits locked in doc 155 S2:
  Gate B-extended: rho_B_agg >= 0.85 AND >=6 of 7 per-date >= 0.80
  Gate C-revised (Fisher-z partial correlation):
    H0: partial_rho(A, C | B) <= 0.30
    PASS if one-sided p-value > 0.05 at N=24
  Gate D-revised (percentile bootstrap):
    per-date 95% CI on (ensemble - mean per-seed) Spearman delta vs y_true
    PASS if ALL 7 dates have CI lower bound >= -0.05

Composite (binary):
  All 3 PASS -> D293 FULL SHIP retroactively
  Any FAIL -> REVERT ensemble code, keep n_estimators=2 as D293a
"""
from __future__ import annotations
import argparse
import gc
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, norm

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def fit_and_predict(Xtr, ytr, Xte, *, seed, n_estimators=2, max_train=2500):
    import torch
    from tabpfn import TabPFNRegressor
    if len(Xtr) > max_train:
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(Xtr), max_train, replace=False)
        Xtr = Xtr[idx]; ytr = ytr[idx]
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()
    m = TabPFNRegressor(device="cuda", random_state=seed, n_estimators=n_estimators)
    m.fit(Xtr, ytr)
    pred = m.predict(Xte)
    del m
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        gc.collect()
    return np.asarray(pred, dtype=float)


def partial_correlation(rho_AB: float, rho_BC: float, rho_AC: float) -> float:
    """Olkin-Pratt 1958 partial correlation: partial_rho(A, C | B)."""
    denom = math.sqrt((1 - rho_AB**2) * (1 - rho_BC**2))
    if denom == 0:
        return float("nan")
    return (rho_AC - rho_AB * rho_BC) / denom


def fisher_z_one_sided_test(partial_rho: float, threshold: float, n: int, k: int = 1):
    """One-sided test: H0 partial_rho <= threshold vs H1 partial_rho > threshold.

    Uses Fisher z transformation. Returns (z_stat, t_stat, one_sided_p).
    """
    z_obs = math.atanh(partial_rho)
    z_thresh = math.atanh(threshold)
    se_z = 1.0 / math.sqrt(n - 3 - k)
    t_stat = (z_obs - z_thresh) / se_z
    one_sided_p = float(1 - norm.cdf(t_stat))
    return z_obs, t_stat, one_sided_p


def bootstrap_delta_ci(seed_preds: list[np.ndarray], y_true: np.ndarray,
                        n_boot: int = 1000, seed: int = 99):
    """Per-date bootstrap 95% percentile CI on
    (ensemble Spearman vs y_true) - (mean per-seed Spearman vs y_true).

    Returns (point_estimate, ci_lo, ci_hi).
    """
    rng = np.random.default_rng(seed)
    n = len(y_true)
    seed_arr = np.stack(seed_preds, axis=0)  # (n_seeds, n)
    ensemble = seed_arr.mean(axis=0)

    # Point estimate
    ens_rho_y = float(spearmanr(ensemble, y_true).statistic)
    per_seed_rho_y = [float(spearmanr(sp, y_true).statistic) for sp in seed_preds]
    point_delta = ens_rho_y - float(np.mean(per_seed_rho_y))

    # Bootstrap
    deltas = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        if y_true[idx].std() == 0:
            continue  # degenerate resample
        ens_b = ensemble[idx]
        seed_b = seed_arr[:, idx]
        rho_ens = spearmanr(ens_b, y_true[idx]).statistic
        rho_per = [spearmanr(seed_b[i], y_true[idx]).statistic for i in range(seed_b.shape[0])]
        if any(np.isnan(r) for r in rho_per) or np.isnan(rho_ens):
            continue
        deltas.append(rho_ens - float(np.mean(rho_per)))

    if not deltas:
        return point_delta, float("nan"), float("nan")
    arr = np.array(deltas)
    return point_delta, float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+",
                    default=["2026-04-24", "2026-04-23", "2026-04-22",
                             "2026-04-21", "2026-04-20", "2026-04-17",
                             "2026-04-16"],
                    help="7 recent dates for Gate B-extended (default skips 2026-04-15)")
    ap.add_argument("--n-seeds", type=int, default=5)
    ap.add_argument("--n-boot", type=int, default=1000,
                    help="Bootstrap iterations per date for Gate D")
    ap.add_argument("--gate-c-threshold", type=float, default=0.30,
                    help="Gate C-revised threshold for partial correlation")
    ap.add_argument("--gate-d-threshold", type=float, default=-0.05,
                    help="Gate D-revised CI lower bound threshold")
    args = ap.parse_args()

    section("STEP 1 -- load v3 panel + features")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  panel: {len(df):,} rows, {X.shape[1]} features")

    SEEDS = [42 + si * 1000 for si in range(args.n_seeds)]
    print(f"  seeds: {SEEDS}")
    print(f"  dates: {args.dates}  (n={len(args.dates)})")

    # =========================================================================
    # STEP 2 -- generate per-seed predictions for all dates
    # =========================================================================
    section(f"STEP 2 -- run TabPFN ({args.n_seeds} seeds x {len(args.dates)} dates at n_est=2)")
    per_date_data = {}  # {date_str: {"single": pred, "ensemble": pred, "seed_preds": [...], "y": yte}}
    for date_str in args.dates:
        target = pd.Timestamp(date_str)
        train_m = (d0 >= target - pd.Timedelta(days=120)) & (d0 < target)
        test_m  = d0 == target
        n_train, n_test = int(train_m.sum()), int(test_m.sum())
        if n_test < 20 or n_train < 100:
            print(f"  {date_str}: skip (n_train={n_train}, n_test={n_test})")
            continue

        Xtr, ytr = X.loc[train_m].values, y.loc[train_m].values
        Xte, yte = X.loc[test_m].values, y.loc[test_m].values

        t0 = time.time()
        seed_preds = []
        for seed in SEEDS:
            try:
                pred = fit_and_predict(Xtr, ytr, Xte, seed=seed)
                seed_preds.append(pred)
            except Exception as e:
                print(f"    seed {seed} FAILED: {e}")
        if len(seed_preds) < 2:
            print(f"  {date_str}: too few seeds, skip")
            continue
        elapsed = time.time() - t0

        single = seed_preds[0]
        ensemble = np.stack(seed_preds, axis=0).mean(axis=0)
        per_date_data[date_str] = {
            "single": single,
            "ensemble": ensemble,
            "seed_preds": seed_preds,
            "y": yte,
            "n_test": n_test,
            "n_train": n_train,
            "elapsed": elapsed,
        }
        rho_b, _ = spearmanr(single, ensemble)
        print(f"  {date_str}: n_test={n_test:3d} rho_B={rho_b:+.4f} ({elapsed:.1f}s)")

    if len(per_date_data) < len(args.dates):
        print(f"  WARN: only {len(per_date_data)} of {len(args.dates)} dates succeeded")

    # =========================================================================
    # GATE B-EXTENDED: aggregated rho_B + per-date threshold
    # =========================================================================
    section("GATE B-EXTENDED")
    all_single = np.concatenate([d["single"] for d in per_date_data.values()])
    all_ens = np.concatenate([d["ensemble"] for d in per_date_data.values()])
    rho_B_agg, p_B_agg = spearmanr(all_single, all_ens)
    per_date_rho_B = {date: float(spearmanr(d["single"], d["ensemble"]).statistic)
                      for date, d in per_date_data.items()}
    n_pass_per_date = sum(1 for r in per_date_rho_B.values() if r >= 0.80)
    print(f"  rho_B aggregated (n={len(all_single)} picks pooled): {rho_B_agg:+.4f} (p={p_B_agg:.4e})")
    print(f"  per-date rho_B: {per_date_rho_B}")
    print(f"  per-date >= 0.80: {n_pass_per_date} of {len(per_date_data)}")
    gate_B_pass = (rho_B_agg >= 0.85) and (n_pass_per_date >= max(6, len(per_date_data) - 1))
    print(f"  Gate B (rho_B_agg >= 0.85 AND >=6 of {len(per_date_data)} per-date >= 0.80): "
          f"{'PASS' if gate_B_pass else 'FAIL'}")

    # =========================================================================
    # GATE C-REVISED: Fisher-z partial correlation test
    # =========================================================================
    section("GATE C-REVISED -- Fisher-z partial correlation")
    # Need 2026-04-24 in per_date_data and the OLD parquet
    old_path = Path(r"<local-path>")
    if not old_path.exists() or "2026-04-24" not in per_date_data:
        print(f"  ERROR: cannot compute Gate C without OLD parquet + 2026-04-24")
        gate_C_pass = False
        gate_C_diag = None
    else:
        old_parquet = pd.read_parquet(old_path)
        d0424 = per_date_data["2026-04-24"]
        target = pd.Timestamp("2026-04-24")
        test_idx = np.where(d0 == target)[0]
        fresh_df = pd.DataFrame({
            "ticker": df.loc[test_idx, "ticker"].values,
            "single_n2": d0424["single"],
            "ensemble_n2": d0424["ensemble"],
            "y_true": d0424["y"],
        })
        merged = old_parquet.merge(fresh_df, on="ticker", how="inner")
        N = len(merged)
        rho_AB, _ = spearmanr(merged["tabpfn_pred"], merged["single_n2"])     # A vs B
        rho_BC, _ = spearmanr(merged["single_n2"], merged["ensemble_n2"])     # B vs C
        rho_AC, _ = spearmanr(merged["tabpfn_pred"], merged["ensemble_n2"])   # A vs C

        partial_rho = partial_correlation(rho_AB, rho_BC, rho_AC)
        z_obs, t_stat, p_one_sided = fisher_z_one_sided_test(
            partial_rho, args.gate_c_threshold, N, k=1)

        print(f"  N = {N} (aligned old + new for 2026-04-24)")
        print(f"  rho_AB (n_est=default vs n_est=2 single, same seed): {rho_AB:+.4f}")
        print(f"  rho_BC (n_est=2 single vs n_est=2 ensemble):         {rho_BC:+.4f}")
        print(f"  rho_AC (n_est=default vs n_est=2 ensemble):          {rho_AC:+.4f}")
        print(f"  partial_rho(A, C | B) = {partial_rho:+.4f}")
        print(f"  Fisher z = {z_obs:+.4f}, SE(z) = {1/math.sqrt(N-3-1):.4f}")
        print(f"  H0: partial_rho <= {args.gate_c_threshold:.2f}")
        print(f"  t-stat: {t_stat:+.4f}, one-sided p-value: {p_one_sided:.4f}")
        gate_C_pass = p_one_sided > 0.05
        print(f"  Gate C (one-sided p > 0.05, cannot reject H0): "
              f"{'PASS' if gate_C_pass else 'FAIL'}")
        gate_C_diag = {
            "N": int(N),
            "rho_AB": float(rho_AB),
            "rho_BC": float(rho_BC),
            "rho_AC": float(rho_AC),
            "partial_rho": float(partial_rho),
            "fisher_z": float(z_obs),
            "t_stat": float(t_stat),
            "one_sided_p": float(p_one_sided),
            "threshold": float(args.gate_c_threshold),
        }

    # =========================================================================
    # GATE D-REVISED: per-date bootstrap CI
    # =========================================================================
    section("GATE D-REVISED -- per-date bootstrap 95% CI")
    gate_D_per_date = {}
    for date, d in per_date_data.items():
        point, ci_lo, ci_hi = bootstrap_delta_ci(
            d["seed_preds"], d["y"], n_boot=args.n_boot, seed=99)
        gate_D_per_date[date] = {
            "point_delta": float(point),
            "ci_lo": float(ci_lo),
            "ci_hi": float(ci_hi),
            "ci_lo_pass": bool(ci_lo >= args.gate_d_threshold),
        }
        marker = "PASS" if ci_lo >= args.gate_d_threshold else "FAIL"
        print(f"  {date}: delta={point:+.4f}  95% CI=[{ci_lo:+.4f}, {ci_hi:+.4f}]  "
              f"({marker} threshold {args.gate_d_threshold:+.2f})")
    gate_D_pass = all(g["ci_lo_pass"] for g in gate_D_per_date.values()) and len(gate_D_per_date) > 0
    n_d_pass = sum(1 for g in gate_D_per_date.values() if g["ci_lo_pass"])
    print(f"  Gate D (all dates have CI lower bound >= {args.gate_d_threshold:+.2f}): "
          f"{'PASS' if gate_D_pass else 'FAIL'}  ({n_d_pass}/{len(gate_D_per_date)} dates)")

    # =========================================================================
    # COMPOSITE VERDICT (per doc 155 S2 outcome map)
    # =========================================================================
    section("COMPOSITE VERDICT (doc 155 S2)")
    print(f"  Gate B-extended:  {'PASS' if gate_B_pass else 'FAIL'}")
    print(f"  Gate C-revised:   {'PASS' if gate_C_pass else 'FAIL'}")
    print(f"  Gate D-revised:   {'PASS' if gate_D_pass else 'FAIL'}")
    if gate_B_pass and gate_C_pass and gate_D_pass:
        verdict = "FULL SHIP -- upgrade D293 to ship retroactively"
    else:
        failed = []
        if not gate_B_pass: failed.append("B")
        if not gate_C_pass: failed.append("C")
        if not gate_D_pass: failed.append("D")
        verdict = f"REVERT ensemble code; keep n_estimators=2 as D293a (gates failed: {','.join(failed)})"
    print(f"\n  VERDICT: {verdict}")

    out = {
        "n_dates": len(per_date_data),
        "rho_B_aggregated": float(rho_B_agg),
        "rho_B_aggregated_p": float(p_B_agg),
        "per_date_rho_B": per_date_rho_B,
        "n_pass_per_date_B": int(n_pass_per_date),
        "gate_B_pass": bool(gate_B_pass),
        "gate_C_diagnostic": gate_C_diag,
        "gate_C_pass": bool(gate_C_pass),
        "gate_D_per_date": gate_D_per_date,
        "gate_D_pass": bool(gate_D_pass),
        "verdict": verdict,
        "thresholds": {
            "gate_B_agg": 0.85,
            "gate_B_per_date": 0.80,
            "gate_C_partial_rho": args.gate_c_threshold,
            "gate_D_ci_lower": args.gate_d_threshold,
        },
    }
    out_path = MODELS / "v6_d293_6_calibrated.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
