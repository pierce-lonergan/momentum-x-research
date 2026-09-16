"""D293.5 decomposition test (doc 154).

Resolve the literal Gate 2 failure from doc 153 by isolating the
n_estimators effect from the ensemble effect via independent measurements.

Pre-commits locked in doc 154 ?2:
  Gate A (diagnostic): rho_n_est on 2026-04-24
  Gate B (CRITICAL): rho_ensemble apples-to-apples on 5 recent dates,
                     aggregated >= 0.85 AND >= 4/5 per-date >= 0.80
  Gate C (path consistency): |rho_C - rho_A x rho_B(2026-04-24)| <= 0.10
  Gate D (variance reduction sanity): ensemble std > 0, ensemble vs
                                       y_true Spearman >= mean per-seed
"""
from __future__ import annotations
import argparse
import gc
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
SHADOW_DIR = DERIVED / "tabpfn_shadow"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def fit_and_predict(Xtr, ytr, Xte, *, seed, n_estimators, max_train=2500):
    """One TabPFN fit-and-predict at the given config."""
    import torch
    from tabpfn import TabPFNRegressor
    if len(Xtr) > max_train:
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(Xtr), max_train, replace=False)
        Xtr = Xtr[idx]
        ytr = ytr[idx]
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()
    m = TabPFNRegressor(device="cuda", random_state=seed,
                        n_estimators=n_estimators)
    m.fit(Xtr, ytr)
    pred = m.predict(Xte)
    del m
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        gc.collect()
    return np.asarray(pred, dtype=float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+",
                    default=["2026-04-24", "2026-04-23", "2026-04-22",
                             "2026-04-21", "2026-04-20"],
                    help="Dates for Gate B multi-date apples-to-apples test")
    ap.add_argument("--n-seeds", type=int, default=5,
                    help="Ensemble size (locked at 5 per D293)")
    ap.add_argument("--diagnostic-date", type=str, default="2026-04-24",
                    help="Date for Gate A pure-n_estimators measurement")
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
    print(f"  dates for Gate B: {args.dates}")
    print(f"  diagnostic date for Gate A: {args.diagnostic_date}")

    # =========================================================================
    # GATE A: pure n_estimators effect on 2026-04-24
    # =========================================================================
    section("GATE A -- pure n_estimators effect (1 date diagnostic)")

    # Existing OLD parquet snapshot
    old_path = Path(r"<local-path>")
    if not old_path.exists():
        # Fallback: use the current parquet from before D293.5 ran (this script
        # doesn't regenerate it). User must have the snapshot from doc 153.
        print(f"  ERROR: OLD parquet snapshot not found at {old_path}")
        print(f"  Cannot compute Gate A without the n_estimators=default baseline.")
        print(f"  Use doc 153's smoke-test snapshot.")
        return 1
    old_parquet = pd.read_parquet(old_path)
    print(f"  loaded OLD parquet: {len(old_parquet)} rows from {old_path.name}")
    print(f"    cols: {list(old_parquet.columns)}")

    # Fresh single-seed at n_estimators=2 on the diagnostic date
    target_a = pd.Timestamp(args.diagnostic_date)
    train_a = (d0 >= target_a - pd.Timedelta(days=120)) & (d0 < target_a)
    test_a  = d0 == target_a
    print(f"  diagnostic date {args.diagnostic_date}: train={train_a.sum()} test={test_a.sum()}")

    t0 = time.time()
    fresh_n_est_2 = fit_and_predict(
        X.loc[train_a].values, y.loc[train_a].values, X.loc[test_a].values,
        seed=42, n_estimators=2)
    print(f"  fresh single-seed n_est=2: {time.time()-t0:.1f}s, pred range "
          f"[{fresh_n_est_2.min():+.4f}, {fresh_n_est_2.max():+.4f}]")

    # Align by ticker (OLD parquet has ticker; fresh predictions are in same
    # row order as test_a since both use the same load_data/engineer_features path)
    test_idx = np.where(test_a)[0]
    fresh_df = pd.DataFrame({
        "ticker": df.loc[test_idx, "ticker"].values,
        "fresh_n_est_2": fresh_n_est_2,
        "y_true": y.loc[test_idx].values,
    })
    aligned = old_parquet.merge(fresh_df, on="ticker", how="inner")
    print(f"  aligned rows: {len(aligned)}")

    rho_A, p_A = spearmanr(aligned["tabpfn_pred"], aligned["fresh_n_est_2"])
    print(f"  rho_A (n_est=default vs n_est=2, same seed=42): {rho_A:+.4f}  (p={p_A:.4f})")

    # =========================================================================
    # GATE B: pure ensemble effect, multi-date apples-to-apples
    # =========================================================================
    section(f"GATE B -- pure ensemble effect, {len(args.dates)} dates apples-to-apples")
    per_date_results = []
    all_single = []
    all_ensemble = []
    all_y_true = []
    all_per_seed_preds = {s: [] for s in SEEDS}  # for Gate D

    for date_str in args.dates:
        target = pd.Timestamp(date_str)
        train_m = (d0 >= target - pd.Timedelta(days=120)) & (d0 < target)
        test_m  = d0 == target
        n_train, n_test = int(train_m.sum()), int(test_m.sum())
        if n_test == 0 or n_train < 100:
            print(f"  {date_str}: skip (n_train={n_train}, n_test={n_test})")
            continue

        Xtr = X.loc[train_m].values
        ytr = y.loc[train_m].values
        Xte = X.loc[test_m].values
        yte = y.loc[test_m].values

        # Run all 5 seeds (the first one IS the single-seed baseline)
        t0 = time.time()
        seed_preds = []
        for seed in SEEDS:
            try:
                pred = fit_and_predict(Xtr, ytr, Xte, seed=seed, n_estimators=2)
                seed_preds.append((seed, pred))
            except Exception as e:
                print(f"    seed {seed} FAILED: {e}")
        if len(seed_preds) < 2:
            print(f"  {date_str}: too few successful seeds, skip")
            continue
        elapsed = time.time() - t0

        single = seed_preds[0][1]  # seed=42, the apples-to-apples single-seed
        seed_arr = np.stack([sp for _, sp in seed_preds], axis=0)
        ensemble = seed_arr.mean(axis=0)
        ensemble_std = seed_arr.std(axis=0, ddof=0)

        rho_b_date, _ = spearmanr(single, ensemble)
        # Per-seed Spearman vs y_true
        per_seed_rho_y = [float(spearmanr(sp, yte).statistic) for _, sp in seed_preds]
        ensemble_rho_y = float(spearmanr(ensemble, yte).statistic)

        per_date_results.append({
            "date": date_str,
            "n_train": n_train, "n_test": n_test,
            "rho_b": float(rho_b_date),
            "ensemble_std_mean": float(ensemble_std.mean()),
            "ensemble_std_min": float(ensemble_std.min()),
            "ensemble_std_max": float(ensemble_std.max()),
            "per_seed_rho_y_mean": float(np.mean(per_seed_rho_y)),
            "per_seed_rho_y_min": float(np.min(per_seed_rho_y)),
            "per_seed_rho_y_max": float(np.max(per_seed_rho_y)),
            "ensemble_rho_y": ensemble_rho_y,
            "n_seeds": len(seed_preds),
            "elapsed_sec": round(elapsed, 1),
        })

        all_single.extend(single.tolist())
        all_ensemble.extend(ensemble.tolist())
        all_y_true.extend(yte.tolist())
        for (seed, sp), seed_key in zip(seed_preds, [s for s, _ in seed_preds]):
            all_per_seed_preds[seed_key].extend(sp.tolist())

        print(f"  {date_str}: n_test={n_test:3d}  rho_B={rho_b_date:+.4f}  "
              f"ensemble vs y_true={ensemble_rho_y:+.3f}  per-seed mean={np.mean(per_seed_rho_y):+.3f}  "
              f"std mean={ensemble_std.mean():.4f}  ({elapsed:.1f}s)")

    # Aggregated Gate B
    rho_B_agg, p_B_agg = spearmanr(np.array(all_single), np.array(all_ensemble))
    n_pass_per_date = sum(1 for r in per_date_results if r["rho_b"] >= 0.80)
    print(f"\n  rho_B AGGREGATED (n={len(all_single)} picks pooled): {rho_B_agg:+.4f}  (p={p_B_agg:.4f})")
    print(f"  per-date rho_B >= 0.80: {n_pass_per_date} of {len(per_date_results)}")

    gate_B_pass = (rho_B_agg >= 0.85) and (n_pass_per_date >= 4)
    print(f"  Gate B (rho_B_agg >= 0.85 AND >=4 of 5 per-date >= 0.80): "
          f"{'PASS' if gate_B_pass else 'FAIL'}")

    # =========================================================================
    # GATE C: decomposition path consistency (using existing rho_C = 0.65)
    # =========================================================================
    section("GATE C -- decomposition path consistency")
    rho_C = 0.6504  # from doc 153 ?5 smoke test
    rho_B_2026_04_24 = next((r["rho_b"] for r in per_date_results
                              if r["date"] == "2026-04-24"), None)
    if rho_B_2026_04_24 is None:
        print("  ERROR: 2026-04-24 not in Gate B results, cannot check decomposition")
        gate_C_pass = False
        path_product = None
        decomp_residual = None
    else:
        path_product = rho_A * rho_B_2026_04_24
        decomp_residual = abs(rho_C - path_product)
        gate_C_pass = decomp_residual <= 0.10
        print(f"  rho_A (n_est effect):           {rho_A:+.4f}")
        print(f"  rho_B (ensemble, 2026-04-24):   {rho_B_2026_04_24:+.4f}")
        print(f"  rho_C (literal Gate 2 from doc 153):  {rho_C:+.4f}")
        print(f"  path product rho_A x rho_B:       {path_product:+.4f}")
        print(f"  residual |rho_C - path product|: {decomp_residual:.4f}  (gate <= 0.10)")
        print(f"  Gate C (decomposition consistent): {'PASS' if gate_C_pass else 'FAIL'}")

    # =========================================================================
    # GATE D: ensemble variance-reduction sanity
    # =========================================================================
    section("GATE D -- ensemble variance-reduction sanity")
    d_results = []
    for r in per_date_results:
        cond_std_pos = r["ensemble_std_mean"] > 0
        cond_quality = r["ensemble_rho_y"] >= r["per_seed_rho_y_mean"]
        cond_std_dist = r["ensemble_std_min"] < r["ensemble_std_max"]
        d_results.append({
            "date": r["date"],
            "std_pos": cond_std_pos,
            "quality": cond_quality,
            "std_dist": cond_std_dist,
            "all_pass": cond_std_pos and cond_quality and cond_std_dist,
        })
        print(f"  {r['date']}: std>0={cond_std_pos}  ensemble>=mean-seed={cond_quality}  "
              f"std-distribution non-degenerate={cond_std_dist}")
        print(f"    ensemble Spearman vs y={r['ensemble_rho_y']:+.4f}, "
              f"per-seed mean={r['per_seed_rho_y_mean']:+.4f}, "
              f"per-seed range=[{r['per_seed_rho_y_min']:+.4f}, {r['per_seed_rho_y_max']:+.4f}]")
    gate_D_pass = all(d["all_pass"] for d in d_results) and len(d_results) > 0
    print(f"\n  Gate D (all dates satisfy all 3 sanity conditions): "
          f"{'PASS' if gate_D_pass else 'FAIL'}")

    # =========================================================================
    # COMPOSITE VERDICT (per doc 154 ?2 outcome map)
    # =========================================================================
    section("COMPOSITE VERDICT (doc 154 ?2)")
    if gate_B_pass and gate_C_pass and gate_D_pass:
        verdict = "FULL SHIP -- upgrade D293 to ship retroactively"
    elif not gate_B_pass:
        verdict = "REVERT ensemble code; keep n_estimators=2 fix as D293a"
    elif gate_B_pass and not gate_C_pass:
        verdict = "HOLD -- ensemble works but decomposition inconsistent; investigate third confound"
    elif gate_B_pass and not gate_D_pass:
        verdict = "HOLD -- ensemble correlates but variance reduction sanity failed; possible aggregation bug"
    else:
        verdict = "HOLD -- unexpected gate combination, document and investigate"
    print(f"  Gate B: {'PASS' if gate_B_pass else 'FAIL'}")
    print(f"  Gate C: {'PASS' if gate_C_pass else 'FAIL'}")
    print(f"  Gate D: {'PASS' if gate_D_pass else 'FAIL'}")
    print(f"  VERDICT: {verdict}")

    out = {
        "rho_A": float(rho_A),
        "rho_A_p_value": float(p_A),
        "rho_B_aggregated": float(rho_B_agg),
        "rho_B_per_date": per_date_results,
        "rho_B_pass_count": int(n_pass_per_date),
        "rho_C_from_doc_153": rho_C,
        "rho_B_2026_04_24": float(rho_B_2026_04_24) if rho_B_2026_04_24 else None,
        "path_product": float(path_product) if path_product else None,
        "decomposition_residual": float(decomp_residual) if decomp_residual else None,
        "gate_A_diagnostic": True,  # always reported, no pass/fail
        "gate_B_pass": gate_B_pass,
        "gate_C_pass": gate_C_pass,
        "gate_D_pass": gate_D_pass,
        "gate_D_per_date": d_results,
        "verdict": verdict,
    }
    out_path = MODELS / "v6_d293_5_decompose.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
