"""EXPERIMENT #2 — DSR audit of v3 production cascade itself, at N_trials=100.

Per user critique: "You DSR-audited v4. You DSR-audited ELITE. You have
never DSR-audited v3 itself. Production v3 has been the subject of
~50-150 trial configurations across 2024-2026. The cascade alpha
(+20% mean ret_t5 on ELITE/HIGH per doc 142) has never been run through
DSR at that N_trials count."

This is the experiment whose negative outcome destroys the most prior
work, which means highest information-per-compute ratio of anything we
could run.

PRE-COMMITTED VERDICT (per the user's rule):
  v3 cascade DSR @ N=100 < 0.5  ->  file MX_TIERED_LEARNING=0 in
                                    next launcher commit.
  Doc 142's "v3 has real edge, deploy it better" framing collapses.
  d-1 microstructure lift may have been measured on phantom baseline.
  TabPFN +0.07 advantage may be over noise.

What we test:
  - v3 BROAD specialist OOS predictions (the same ones doc 142 used)
  - Apply production-cascade thresholds (BROAD>=0.30, VETOED>=0.40,
    HIGH>=0.50, ELITE>=0.60)
  - Compute daily P&L = mean ret_t5 of picks per day
  - Apply Bailey-LdP DSR formula at N_trials = {1, 10, 50, 100, 150}

  Sensitivity: also report DSR for each tier separately, and for the
  whole-cascade aggregate.

USAGE:
  python scripts/ml_v6_v3_cascade_dsr_audit.py
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


EULER_MASCHERONI = 0.5772156649


def deflated_sharpe(daily_pnl: np.ndarray, n_trials: int) -> dict:
    """Bailey-LdP JPM 2014 DSR with skew/kurt correction.
    Same correct formula as ml_v6_phase_0_validation.py (NOT the buggy
    early version of ml_v6_phase1_executive_decisions.py)."""
    pnl = daily_pnl[~np.isnan(daily_pnl)]
    if len(pnl) < 15:
        return {"error": f"only {len(pnl)} days"}
    mean = pnl.mean(); std = pnl.std(ddof=1)
    if std <= 0: return {"error": "zero std"}
    sr = mean / std
    sr_ann = sr * math.sqrt(252)
    skew = float(((pnl - mean) ** 3).mean() / std ** 3)
    kurt_ex = float(((pnl - mean) ** 4).mean() / std ** 4 - 3)
    T = len(pnl)
    sigma_sr = float(math.sqrt(
        max(1e-8, (1 - skew * sr + (kurt_ex / 4) * sr ** 2) / (T - 1))
    ))
    if n_trials <= 1:
        sr_max_h0_std = 0.0
    else:
        sr_max_h0_std = float(
            (1 - EULER_MASCHERONI) * norm.ppf(1 - 1 / n_trials)
            + EULER_MASCHERONI * norm.ppf(1 - 1 / (n_trials * math.e))
        )
    sr_max_threshold = sr_max_h0_std * sigma_sr  # convert to per-period units
    z = (sr - sr_max_threshold) / sigma_sr if sigma_sr > 0 else 0.0
    return {
        "T_days": T, "sr_per_period": float(sr), "sr_annualized": float(sr_ann),
        "skew": skew, "excess_kurtosis": kurt_ex, "sigma_sr": sigma_sr,
        "n_trials": n_trials,
        "sr_max_h0_std_units": sr_max_h0_std,
        "sr_max_threshold_perperiod": float(sr_max_threshold),
        "z_score": float(z),
        "dsr": float(norm.cdf(z)),
    }


def assign_tier_absolute(prob: pd.Series) -> pd.Series:
    """Production cascade absolute thresholds (matches meta_scorer_inference.py)."""
    tier = pd.Series(["SKIP"] * len(prob), index=prob.index, dtype=object)
    tier.loc[prob >= 0.30] = "BROAD"
    tier.loc[prob >= 0.40] = "VETOED"
    tier.loc[prob >= 0.50] = "HIGH"
    tier.loc[prob >= 0.60] = "ELITE"
    return tier


def main() -> int:
    section("STEP 1 — load v3 BROAD specialist OOS preds (proxy for production cascade)")
    preds = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    preds["d0"] = pd.to_datetime(preds["d0"])
    print(f"  {len(preds):,} rows, {preds['d0'].nunique()} unique days, "
          f"{preds['fold'].nunique()} folds")
    print(f"  d0 range: {preds['d0'].min().date()} -> {preds['d0'].max().date()}")

    section("STEP 2 — assign tier per row via production cascade thresholds")
    preds["tier"] = assign_tier_absolute(preds["prob_specialist_BROAD"])
    counts = preds["tier"].value_counts().reindex(
        ["ELITE", "HIGH", "VETOED", "BROAD", "SKIP"], fill_value=0)
    print("  per-tier counts:")
    for t, c in counts.items():
        print(f"    {t:<8} {c:>6,}  ({c/len(preds)*100:>5.1f}%)")

    section("STEP 3 — compute daily P&L for each cascade configuration")
    # Multiple cascade variants to DSR audit:
    #   - Top-N picks per day with prob >= threshold (the production logic)
    #   - Aggregate per-tier
    print("  Each row's `y_reg` is realized ret_t5; daily P&L = mean ret_t5 of picks per day")
    print()

    # Variant 1: per-tier daily mean
    tier_pnl_daily = {}
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        sub = preds[preds["tier"] == tier]
        if len(sub) == 0:
            continue
        per_day = sub.groupby("d0")["y_reg"].mean()
        pnl = per_day.dropna().values
        tier_pnl_daily[tier] = pnl
        print(f"  {tier:<8} active {len(per_day):>3} days, "
              f"mean per-day ret_t5 {pnl.mean()*100:+6.2f}%, "
              f"std {pnl.std()*100:5.2f}%, "
              f"ann_sharpe {pnl.mean()/pnl.std()*math.sqrt(252):+6.2f}")

    # Variant 2: full cascade union (any pick where tier != SKIP)
    cascade_picks = preds[preds["tier"] != "SKIP"]
    cascade_per_day = cascade_picks.groupby("d0")["y_reg"].mean()
    cascade_pnl = cascade_per_day.dropna().values
    print(f"\n  CASCADE (union, any tier): active {len(cascade_per_day):>3} days, "
          f"mean per-day ret_t5 {cascade_pnl.mean()*100:+6.2f}%, "
          f"std {cascade_pnl.std()*100:5.2f}%, "
          f"ann_sharpe {cascade_pnl.mean()/cascade_pnl.std()*math.sqrt(252):+6.2f}")

    # Variant 3: ELITE+HIGH only (the doc 142 +20% claim)
    eh_picks = preds[preds["tier"].isin(["ELITE", "HIGH"])]
    eh_per_day = eh_picks.groupby("d0")["y_reg"].mean()
    eh_pnl = eh_per_day.dropna().values
    print(f"  ELITE+HIGH only: active {len(eh_per_day):>3} days, "
          f"mean per-day ret_t5 {eh_pnl.mean()*100:+6.2f}%, "
          f"std {eh_pnl.std()*100:5.2f}%, "
          f"ann_sharpe {eh_pnl.mean()/eh_pnl.std()*math.sqrt(252):+6.2f}")

    section("STEP 4 — DSR sweep across N_trials for each variant")
    print("  N_trials sweep: 1, 10, 50, 100, 150, 200")
    print()

    def dsr_table(pnl: np.ndarray, label: str) -> dict:
        print(f"  {label}: T={len(pnl)} days, ann_sharpe={pnl.mean()/pnl.std()*math.sqrt(252):+.2f}")
        print(f"    {'N_trials':>10} {'sr_max_thr':>11} {'z':>10} {'DSR':>10}  verdict")
        out = {}
        for nt in (1, 10, 50, 100, 150, 200):
            d = deflated_sharpe(pnl, nt)
            verdict = "PASS" if d.get("dsr", 0) >= 0.95 else (
                      "marginal" if d.get("dsr", 0) >= 0.5 else "FAIL")
            print(f"    {nt:>10} {d.get('sr_max_threshold_perperiod', float('nan')):>+11.4f} "
                  f"{d.get('z_score', float('nan')):>+10.3f} "
                  f"{d.get('dsr', float('nan')):>10.4f}  {verdict}")
            out[f"N_{nt}"] = d
        return out

    results = {}
    for label, pnl in [("CASCADE (union)", cascade_pnl),
                        ("ELITE+HIGH only", eh_pnl)]:
        print()
        results[label] = dsr_table(pnl, label)

    print()
    print("  Per-tier individual DSR (top-3 picks per day per tier):")
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        sub = preds[preds["tier"] == tier]
        if len(sub) < 30: continue
        per_day_top = (sub
                       .sort_values(["d0", "prob_specialist_BROAD"], ascending=[True, False])
                       .groupby("d0").head(3).groupby("d0")["y_reg"].mean())
        pnl = per_day_top.dropna().values
        if len(pnl) < 15: continue
        print()
        results[f"{tier}_top3"] = dsr_table(pnl, f"{tier} top-3/day")

    section("STEP 5 — pre-committed verdict")
    print("  PRE-COMMIT: if v3 CASCADE DSR @ N=100 < 0.5,")
    print("              file MX_TIERED_LEARNING=0 in next launcher commit")
    print("              and disable the production cascade pending rebuild.")
    print()
    cascade_dsr_n100 = results["CASCADE (union)"].get("N_100", {}).get("dsr", float("nan"))
    eh_dsr_n100 = results["ELITE+HIGH only"].get("N_100", {}).get("dsr", float("nan"))
    print(f"  CASCADE DSR @ N=100:        {cascade_dsr_n100:.4f}")
    print(f"  ELITE+HIGH DSR @ N=100:     {eh_dsr_n100:.4f}")
    print()
    if cascade_dsr_n100 >= 0.95:
        print("  -> CASCADE PASSES DSR @ N=100. v3 cascade has real, multi-testing-corrected edge.")
    elif cascade_dsr_n100 >= 0.5:
        print("  -> CASCADE MARGINAL @ N=100 (>=0.5 but <0.95).")
        print("     Production stays for now; pre-commit doesn't trigger; close call.")
    else:
        print(f"  -> CASCADE FAILS DSR @ N=100 ({cascade_dsr_n100:.4f} < 0.5).")
        print("     PRE-COMMIT FIRES: file MX_TIERED_LEARNING=0 in next launcher commit.")
        print("     v3 cascade alpha is statistical noise under conservative N_trials.")

    out_path = MODELS / "v6_v3_cascade_dsr_audit.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
