"""Placebo TCN test: validate the VETOED-tier rule with random predictions.

Session 113 found TCN AUC = 0.49 (random). The +6.57% on v2-only n=244 in
session 108 might be:
  A) An empirical-but-real signal: TCN is noisy but its noise correlates
     with v2's mistakes
  B) A statistical fluke that doesn't survive replacement with truly
     random predictions
  C) A REAL signal driven by path-derived features in v3 (orthogonal to
     TCN)

This test:
  1. Replace TCN predictions with uniform [0, 1] random noise
  2. Re-apply the VETOED-tier rule (v3t>=0.30 + MID-mag + tcn<0.30)
  3. Compare bucket P&Ls vs the production TCN

If random TCN → similar VETOED-tier P&L as production TCN, the veto signal
is fluke (option B). If random → MUCH worse, signal is real (option A).
If random → similar broad P&L but different VETOED selection, signal lives
in something else.

Run multiple seeds for statistical robustness.

OUTPUT:
  data/models/placebo_tcn_results.json
  Console: tier P&L for production TCN, mean/std across N placebo seeds
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def evaluate_vetoed_rule(merged: pd.DataFrame, tcn_col: str,
                           v3t_thr: float = 0.30) -> dict:
    """Apply VETOED rule: v3t>=thr AND mag=MID AND tcn<0.30.

    Returns dict of bucket stats.
    """
    yreg = merged["y_reg"].clip(-0.5, 1.0)
    # Bucket masks
    is_mid = merged["mag_label"] == "MID"
    has_v3t = merged["prob_continuer"] >= v3t_thr
    has_low_tcn = merged[tcn_col] < 0.30
    is_high_tcn = merged[tcn_col] >= 0.30

    vetoed_mask = has_v3t & is_mid & has_low_tcn
    both_mask = has_v3t & is_mid & is_high_tcn
    broad_mask = has_v3t & merged["mag_label"].isin(["HI", "MID"])

    def stats(mask):
        n = int(mask.sum())
        if n == 0: return (0, 0.0, 0.0, 0.0)
        return (n, float(yreg[mask].mean() * 100),
                float((yreg[mask] > 0).mean() * 100),
                float(yreg[mask].sum()))

    n_v, avg_v, win_v, sum_v = stats(vetoed_mask)
    n_b, avg_b, win_b, sum_b = stats(both_mask)
    n_br, avg_br, win_br, sum_br = stats(broad_mask)

    return {
        "vetoed": {"n": n_v, "avg_pct": avg_v, "win_pct": win_v, "total_yreg": sum_v},
        "both_mid": {"n": n_b, "avg_pct": avg_b, "win_pct": win_b, "total_yreg": sum_b},
        "broad_mag": {"n": n_br, "avg_pct": avg_br, "win_pct": win_br, "total_yreg": sum_br},
    }


def main():
    section("STEP 1 - Load + join WF predictions")
    v3t_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    tcn_path = DERIVED / "tcn_intraday_walkforward_predictions.parquet"
    ising_path = DERIVED / "ising_daily.parquet"

    if not all(p.exists() for p in (v3t_path, tcn_path, ising_path)):
        print("  ERROR: required parquets missing")
        return 1

    con = duckdb.connect()
    con.sql("SET memory_limit='4GB'")
    con.sql(f"""
        CREATE TABLE i AS SELECT *,
               AVG(magnetization) OVER (
                   ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
               ) AS mag_5d
        FROM read_parquet('{ising_path.as_posix()}')
    """)
    merged = con.sql(f"""
        SELECT v.d0, v.ticker, v.y_cls, v.y_reg, v.prob_continuer,
               t.tcn_proba,
               i.mag_5d,
               CASE WHEN i.mag_5d < -0.05 THEN 'LO'
                    WHEN i.mag_5d > 0.05 THEN 'HI'
                    ELSE 'MID' END AS mag_label
        FROM read_parquet('{v3t_path.as_posix()}') v
        INNER JOIN read_parquet('{tcn_path.as_posix()}') t
            ON v.d0 = t.d0 AND v.ticker = t.ticker
        JOIN i ON v.d0 = i.d
    """).df()
    print(f"  loaded {len(merged):,} OOS rows (v3t intersect tcn intersect ising)")

    section("STEP 2 - Production TCN VETOED rule")
    prod_stats = evaluate_vetoed_rule(merged, "tcn_proba")
    print(f"  {'bucket':<12} {'n':>5} {'avg_pct':>9} {'win_pct':>8}")
    for k, v in prod_stats.items():
        print(f"  {k:<12} {v['n']:>5,} {v['avg_pct']:>+8.2f}% {v['win_pct']:>7.1f}%")

    section("STEP 3 - Placebo TCN (random noise) — N seeds")
    n_seeds = 20
    placebo_results = []
    for seed in range(n_seeds):
        rng = np.random.RandomState(seed)
        merged["tcn_random"] = rng.uniform(0, 1, size=len(merged))
        s = evaluate_vetoed_rule(merged, "tcn_random")
        placebo_results.append(s)

    # Aggregate stats across seeds
    print(f"  {'bucket':<12} {'mean_n':>8} {'mean_avg':>10} {'std_avg':>10} "
          f"{'mean_total':>11}")
    for bucket in ("vetoed", "both_mid", "broad_mag"):
        ns = [r[bucket]["n"] for r in placebo_results]
        avgs = [r[bucket]["avg_pct"] for r in placebo_results]
        totals = [r[bucket]["total_yreg"] for r in placebo_results]
        print(f"  {bucket:<12} {np.mean(ns):>8.1f} {np.mean(avgs):>+9.2f}% "
              f"{np.std(avgs):>+9.2f}% {np.mean(totals):>+10.4f}")

    section("STEP 4 - Difference: production TCN vs placebo")
    print(f"  {'bucket':<12} {'prod_avg':>9} {'placebo_mean':>13} "
          f"{'placebo_std':>12} {'z_score':>8}")
    interpretation = []
    for bucket in ("vetoed", "both_mid", "broad_mag"):
        prod_avg = prod_stats[bucket]["avg_pct"]
        placebo_avgs = [r[bucket]["avg_pct"] for r in placebo_results]
        p_mean = float(np.mean(placebo_avgs))
        p_std = float(np.std(placebo_avgs))
        z = (prod_avg - p_mean) / p_std if p_std > 0 else 0
        flag = ""
        if abs(z) > 2: flag = " *** REAL signal (>2sigma)"
        elif abs(z) > 1: flag = " ** weak signal (>1sigma)"
        else: flag = " (within noise)"
        interpretation.append((bucket, prod_avg, p_mean, p_std, z, flag))
        print(f"  {bucket:<12} {prod_avg:>+8.2f}% {p_mean:>+12.2f}% "
              f"{p_std:>+11.2f}% {z:>+7.2f}{flag}")

    section("STEP 5 - Conclusion")
    veto_z = next(z for b, _, _, _, z, _ in interpretation if b == "vetoed")
    if abs(veto_z) > 2:
        print(f"  *** VETOED rule produces REAL lift beyond chance "
              f"(z={veto_z:+.2f}sigma vs random TCN)")
        print("  TCN signal — even with AUC=0.49 — has signal correlation with v2.")
        print("  Production VETOED rule is statistically valid.")
    elif abs(veto_z) > 1:
        print(f"  ** VETOED rule has WEAK lift vs random "
              f"(z={veto_z:+.2f}sigma); marginal real signal.")
    else:
        print(f"  - VETOED rule lift is INDISTINGUISHABLE from random TCN "
              f"(z={veto_z:+.2f}sigma).")
        print("  The +12.74% VETOED-tier P&L is an artifact of v3+mag interaction,")
        print("  not the TCN. The TCN-veto rule can be REMOVED from production.")

    out = {
        "n_seeds": n_seeds,
        "production": prod_stats,
        "placebo_aggregate": {
            bucket: {
                "mean_n": float(np.mean([r[bucket]["n"] for r in placebo_results])),
                "mean_avg_pct": float(np.mean([r[bucket]["avg_pct"] for r in placebo_results])),
                "std_avg_pct": float(np.std([r[bucket]["avg_pct"] for r in placebo_results])),
            }
            for bucket in ("vetoed", "both_mid", "broad_mag")
        },
        "interpretation": [
            {"bucket": b, "prod_avg_pct": pa, "placebo_mean_pct": pm,
             "placebo_std_pct": ps, "z_score": z, "verdict": flag.strip()}
            for b, pa, pm, ps, z, flag in interpretation
        ],
    }
    out_path = MODELS / "placebo_tcn_results.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
