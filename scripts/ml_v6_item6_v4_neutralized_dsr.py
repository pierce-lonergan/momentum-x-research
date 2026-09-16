"""ITEM 6 — Re-validate v4's "+0.015 neutralized rho vs v3" with proper N_trials.

Per user critique on doc 138: doc 133 reported v4 BROAD neutralized rho =
0.091 vs v3 = 0.076 (a +0.015 lift), but treated this as a single-trial
measurement. In reality the v4 architecture program ran ~16 prediction-
generating variants on disk (sweep_0..sweep_6, bigger, cls_only,
tabular_only, base, 4 tier specialists, v5 corn). Plus implicit selection:
we picked v4 BROAD specifically because it had the highest neutralized
rho — that's also a trial.

Conservative N_trials: 16 from artifacts on disk; 20+ if we count
hyperparameter sweeps within each. The user's framing: "if item 6 comes
back negative under proper N_trials accounting, the entire MoMTrans v4/v5
program never demonstrated edge over v3 to begin with."

This script:
  1. Loads each v4/v5 variant's OOS predictions
  2. Computes 100% feature-neutralized Spearman vs ret_t5 for each
  3. Picks the maximum (which is what doc 133 reported for v4 BROAD)
  4. Applies DSR with N_trials sweeping (1, 5, 10, 16, 20, 50)
  5. Verdict: does the best-of-N_variants neutralized rho lift survive
     multiple-testing correction?

DOC 138 RULE: verdict section in the writeup stays blank until this
script's output is in hand.

USAGE:
  python scripts/ml_v6_item6_v4_neutralized_dsr.py
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import norm, spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# Variant catalog — every v4/v5 prediction parquet on disk
# ──────────────────────────────────────────────────────────────────────


VARIANTS = [
    # (label, file, prediction_column, description)
    ("v4_base",            "momtrans_v4_predictions.parquet",            "prob_binary",        "v4 base model"),
    ("v4_tabular_only",    "momtrans_v4_tabular_only_predictions.parquet","prob_binary",       "v4 tabular-only ablation (doc 127 winner)"),
    ("v4_cls_only",        "momtrans_v4_cls_only_predictions.parquet",   "prob_binary",        "v4 classification-only ablation"),
    ("v4_bigger",          "momtrans_v4_bigger_predictions.parquet",     "prob_binary",        "v4 bigger model ablation (doc 127 underperformer)"),
    ("v4_sweep_0",         "momtrans_v4_sweep_0_predictions.parquet",    "prob_binary",        "v4 hparam sweep #0"),
    ("v4_sweep_1",         "momtrans_v4_sweep_1_predictions.parquet",    "prob_binary",        "v4 hparam sweep #1"),
    ("v4_sweep_2",         "momtrans_v4_sweep_2_predictions.parquet",    "prob_binary",        "v4 hparam sweep #2"),
    ("v4_sweep_3",         "momtrans_v4_sweep_3_predictions.parquet",    "prob_binary",        "v4 hparam sweep #3"),
    ("v4_sweep_4",         "momtrans_v4_sweep_4_predictions.parquet",    "prob_binary",        "v4 hparam sweep #4"),
    ("v4_sweep_5",         "momtrans_v4_sweep_5_predictions.parquet",    "prob_binary",        "v4 hparam sweep #5"),
    ("v4_sweep_6",         "momtrans_v4_sweep_6_predictions.parquet",    "prob_binary",        "v4 hparam sweep #6"),
    ("v4_tier_BROAD",      "momtrans_v4_tier_BROAD_predictions.parquet", "prob_binary",        "v4 BROAD specialist (doc 133 cited)"),
    ("v4_tier_VETOED",     "momtrans_v4_tier_VETOED_predictions.parquet","prob_binary",        "v4 VETOED specialist"),
    ("v4_tier_HIGH",       "momtrans_v4_tier_HIGH_predictions.parquet",  "prob_binary",        "v4 HIGH specialist"),
    ("v4_tier_ELITE",      "momtrans_v4_tier_ELITE_predictions.parquet", "prob_binary",        "v4 ELITE specialist"),
    ("v5_corn",            "momtrans_v5_corn_predictions.parquet",       "prob_binary",        "v5 CORN ordinal head (failed program)"),
]


# ──────────────────────────────────────────────────────────────────────
# Neutralization
# ──────────────────────────────────────────────────────────────────────


def build_exposures(merged: pd.DataFrame) -> pd.DataFrame:
    """Same exposure block as doc 133's Phase 0 framework."""
    exp = pd.DataFrame({
        "log_market_cap": np.log1p(merged.get("market_cap", pd.Series(np.zeros(len(merged)))).fillna(0)),
        "log_dvol_d0":    np.log1p(merged.get("dvol_d0", pd.Series(np.zeros(len(merged)))).fillna(0)),
        "prior_avg_t5":   merged.get("prior_avg_t5", pd.Series(np.zeros(len(merged)))).fillna(0),
        "intraday_pct":   merged.get("intraday_pct", pd.Series(np.zeros(len(merged)))).fillna(0),
    })
    # 8 sector dummies — match Phase 0 framework
    if "sic_description" in merged.columns:
        sic = merged["sic_description"].fillna("").str.lower()
        for sec, key in [("pharma", "pharm"), ("bio", "bio"),
                          ("medical", "medic"), ("software", "software"),
                          ("finance", "financ"), ("semi", "semicond"),
                          ("spac", "spac"), ("reit", "reit")]:
            exp[f"sector_{sec}"] = sic.str.contains(key).astype(float)
    return exp


def neutralized_rho(y_pred: pd.Series, y_true: pd.Series, exposures: pd.DataFrame,
                    proportion: float = 1.0) -> float:
    """Project pred onto residual space orthogonal to exposures, then Spearman vs y_true."""
    F = exposures.values.astype(float)
    F = F - F.mean(axis=0)
    F_pinv = np.linalg.pinv(F)
    p = y_pred.values.astype(float)
    p_proj = F @ (F_pinv @ p)
    p_neut = p - proportion * p_proj
    if p_neut.std() > 0:
        p_neut = p_neut / p_neut.std()
    rho = spearmanr(y_true.values, p_neut).statistic
    return float(rho)


# ──────────────────────────────────────────────────────────────────────
# DSR (the CORRECT formula, copy-paste from ml_v6_phase_0_validation.py)
# ──────────────────────────────────────────────────────────────────────


EULER_MASCHERONI = 0.5772156649


def deflated_rho_significance(rho_observed: float, rho_max_field: float,
                               n_obs: int, n_trials: int) -> dict:
    """DSR-analog for Spearman correlation.

    For Spearman rho with n_obs samples, std(rho) under H0 (independence)
    is approximately 1/sqrt(n_obs - 1). Under H_1 (correlation rho_true),
    Fisher's z-transform gives std ~ 1/sqrt(n_obs - 3).

    The 'expected max rho from N IID trials under H0' is the max-order-
    statistic of N standard-normal variables in std(rho) units:
      E[max{rho_i}] approx sigma_rho * [(1-gamma)*Phi^-1(1-1/N) + gamma*Phi^-1(1-1/(N*e))]

    z = (rho_observed - rho_max_field) / sigma_rho   (in same units)
    DSR = Phi(z)
    """
    sigma_rho = 1.0 / math.sqrt(max(2, n_obs - 3))  # Fisher
    if n_trials <= 1:
        rho_max_h0 = 0.0
    else:
        z1 = norm.ppf(1 - 1.0 / n_trials)
        z2 = norm.ppf(1 - 1.0 / (n_trials * math.e))
        rho_max_h0_std = (1 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2
        rho_max_h0 = rho_max_h0_std * sigma_rho   # convert std-units to rho-units
    z = (rho_observed - rho_max_h0) / sigma_rho
    return {
        "rho_observed": float(rho_observed),
        "rho_max_field_under_H0": float(rho_max_h0),
        "sigma_rho": float(sigma_rho),
        "n_obs": int(n_obs),
        "n_trials": int(n_trials),
        "z_score": float(z),
        "deflated_significance": float(norm.cdf(z)),
    }


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


def main() -> int:
    section("STEP 1 — load v3 base data + exposures (same as Phase 0 framework)")
    from ml_continuer_v2_ensemble import load_data
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  base data loaded: {len(df):,} rows")

    section("STEP 2 — build v3 baseline neutralized rho (control)")
    # Use the v3 production prediction set: ml_v3_tier_specialist_BROAD_predictions
    # (this is what doc 133 used as the v3 baseline for the +0.015 comparison).
    v3_path = DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet"
    v3_preds = pd.read_parquet(v3_path)
    v3_preds["d0"] = pd.to_datetime(v3_preds["d0"])
    v3_merged = v3_preds.merge(df, on=["ticker", "d0"], how="inner",
                                 suffixes=("_pred", ""))
    print(f"  v3 BROAD preds: {len(v3_preds):,} rows; merged with base: {len(v3_merged):,}")
    v3_exposures = build_exposures(v3_merged)
    v3_rho_raw = float(spearmanr(v3_merged["y_reg"], v3_merged["prob_specialist_BROAD"]).statistic)
    v3_rho_neut = neutralized_rho(v3_merged["prob_specialist_BROAD"],
                                    v3_merged["y_reg"], v3_exposures, 1.0)
    print(f"  v3 BROAD raw rho:        {v3_rho_raw:+.4f}")
    print(f"  v3 BROAD neutralized:    {v3_rho_neut:+.4f}")

    section(f"STEP 3 — compute neutralized rho for each of {len(VARIANTS)} v4/v5 variants")
    print(f"  {'variant':<20} {'n_rows':>7} {'raw_rho':>10} {'neut_rho':>10} {'lift_vs_v3':>12}")
    print("  " + "-" * 70)
    results = []
    for label, fname, prob_col, desc in VARIANTS:
        path = MODELS / fname
        if not path.exists():
            print(f"  {label:<20} (file not found, skip)")
            continue
        try:
            preds = pd.read_parquet(path)
        except Exception as e:
            print(f"  {label:<20} (read error: {e}, skip)")
            continue
        preds["d0"] = pd.to_datetime(preds["d0"])
        if prob_col not in preds.columns:
            print(f"  {label:<20} (missing column {prob_col}, skip)")
            continue
        merged = preds.merge(df, on=["ticker", "d0"], how="inner", suffixes=("_pred", ""))
        if len(merged) < 100:
            print(f"  {label:<20} (only {len(merged)} rows after join, skip)")
            continue
        exp = build_exposures(merged)
        try:
            rho_raw = float(spearmanr(merged["y_reg"], merged[prob_col]).statistic)
            rho_neut = neutralized_rho(merged[prob_col], merged["y_reg"], exp, 1.0)
        except Exception as e:
            print(f"  {label:<20} (compute error: {e}, skip)")
            continue
        lift = rho_neut - v3_rho_neut
        results.append({
            "variant": label, "description": desc,
            "n_rows": len(merged), "raw_rho": rho_raw,
            "neut_rho": rho_neut, "lift_vs_v3": lift,
        })
        print(f"  {label:<20} {len(merged):>7,} {rho_raw:>+10.4f} {rho_neut:>+10.4f} {lift:>+12.4f}")

    if not results:
        print("\n  no variants succeeded — abort")
        return 1

    section("STEP 4 — pick the BEST v4/v5 variant by neutralized rho")
    results_df = pd.DataFrame(results).sort_values("neut_rho", ascending=False).reset_index(drop=True)
    print("  ranked by neutralized rho (best first):")
    print(results_df[["variant", "n_rows", "neut_rho", "lift_vs_v3"]].head(10).to_string(index=False))

    best = results_df.iloc[0]
    best_n = int(best["n_rows"])
    best_rho = float(best["neut_rho"])
    best_lift = float(best["lift_vs_v3"])
    print()
    print(f"  BEST variant: {best['variant']} ({best['description']})")
    print(f"    neutralized rho:  {best_rho:+.4f}")
    print(f"    lift vs v3:       {best_lift:+.4f}")
    print(f"    n_obs (post-join): {best_n:,}")

    section("STEP 5 — apply DSR with N_trials sweep")
    print("  N_trials accounting:")
    print(f"    Variants on disk:                   {len(VARIANTS)}")
    print(f"    Variants successfully evaluated:    {len(results)}")
    print(f"    Conservative floor (16 disk variants + implicit selection): N=16-20")
    print(f"    Honest realistic count (incl. hparam sweeps in tabular_sweep.db): N=50+")
    print()
    print(f"  Deflated significance for best variant ({best['variant']}):")
    print(f"  {'N_trials':>10} {'rho_max_H0':>12} {'z':>10} {'deflated_p':>12}  verdict")
    print("  " + "-" * 70)
    dsr_results = {}
    for n_trials in (1, 5, 10, len(results), 20, 50, 100):
        d = deflated_rho_significance(best_rho, 0.0, best_n, n_trials)
        verdict = "PASS (>0.95)" if d["deflated_significance"] > 0.95 else (
                  "marginal (>0.5)" if d["deflated_significance"] > 0.5 else
                  "FAIL (<0.5)")
        print(f"  {n_trials:>10} {d['rho_max_field_under_H0']:>+12.4f} "
              f"{d['z_score']:>+10.3f} {d['deflated_significance']:>12.4f}  {verdict}")
        dsr_results[f"n_trials_{n_trials}"] = d

    # Also: did the LIFT survive multiple-testing? Compare against v3 baseline.
    section("STEP 6 — does the LIFT (best v4/v5 - v3) survive multiple-testing?")
    # Difference of two correlations: std(rho1 - rho2) approx sqrt(2/(n-3))
    # under independence (conservative; correlated would be tighter).
    sigma_diff = math.sqrt(2.0 / max(2, best_n - 3))
    z_lift = best_lift / sigma_diff
    p_lift_naive = 1 - norm.cdf(z_lift)
    print(f"  Naive (single-trial) lift z: {z_lift:+.3f}, one-sided p={p_lift_naive:.4f}")
    print(f"  Bonferroni-corrected for {len(results)} variants: p_corrected = {min(1.0, p_lift_naive * len(results)):.4f}")
    print(f"  Bonferroni-corrected for 20 variants:               p_corrected = {min(1.0, p_lift_naive * 20):.4f}")
    print(f"  Bonferroni-corrected for 50 variants:               p_corrected = {min(1.0, p_lift_naive * 50):.4f}")

    # Persist
    out = {
        "v3_baseline": {"raw_rho": v3_rho_raw, "neut_rho": v3_rho_neut, "n_obs": int(v3_rho_raw and len(v3_merged))},
        "variants_evaluated": len(results),
        "best_variant": best["variant"],
        "best_neut_rho": best_rho,
        "best_lift_vs_v3": best_lift,
        "best_n_obs": best_n,
        "all_results": results_df.to_dict(orient="records"),
        "dsr_sweep": dsr_results,
        "lift_significance": {
            "z_lift": float(z_lift),
            "p_naive": float(p_lift_naive),
            "p_bonferroni_n_evaluated": float(min(1.0, p_lift_naive * len(results))),
            "p_bonferroni_n_20": float(min(1.0, p_lift_naive * 20)),
            "p_bonferroni_n_50": float(min(1.0, p_lift_naive * 50)),
        },
    }
    out_path = MODELS / "v6_item6_v4_neutralized_dsr.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
