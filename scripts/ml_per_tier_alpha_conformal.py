"""Per-tier alpha conformal calibration experiment.

Current production: single conformal_threshold computed at alpha=0.10
across all calibration data. The conformal_width then modulates Kelly
sizing per pick: kelly = base_kelly * exp(-2 * conformal_width).

Hypothesis: tier-specific alphas could deliver tighter intervals on the
elite tier (higher confidence -> larger sizing) and looser on the broad
tier (more inclusive). Tested:
  ELITE   alpha=0.05  (tightest 95% CI)
  HIGH    alpha=0.10  (production default)
  VETOED  alpha=0.15
  BROAD   alpha=0.20  (loosest, more picks pass conformal gate)

For each (alpha, tier) pair, refit the conformal_width modulator on the
v3-tuned-16fold WF predictions, recompute Kelly P&L, compare to baseline.

NOTE: this experiment uses v3-tuned-16fold WF predictions. A real
deployment would compute per-fold conformal thresholds during model
training (in ml_continuer_v2_ensemble.py).

OUTPUT:
  data/models/per_tier_alpha_conformal_summary.json
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

KELLY_CAPS = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}
BANKROLL = 10_000.0


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def conformal_calibrate(probs: np.ndarray, y: np.ndarray, alpha: float) -> float:
    """Standard conformal threshold for given alpha (Angelopoulos 2021)."""
    nc = 1 - np.where(y == 1, probs, 1 - probs)
    n = len(nc)
    q = np.ceil((n + 1) * (1 - alpha)) / n
    return float(np.quantile(nc, min(q, 1.0)))


def conformal_width(probs: np.ndarray, thr: float) -> np.ndarray:
    width = (1 - np.abs(probs - 0.5) * 2) * (1 + thr)
    return np.clip(width, 0, 1)


def kelly_for_row(p: float, conformal_w: float, ew: float, el: float,
                    cap: float) -> float:
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0: return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    width_mod = float(np.exp(-2 * conformal_w))
    return float(min(f_star * width_mod, cap))


def evaluate_with_widths(df: pd.DataFrame, widths: np.ndarray) -> dict:
    """$ P&L per tier using given conformal widths."""
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    prob = df["prob_continuer"].values
    mag = df["mag_label"].values

    # Tier waterfall: ELITE > HIGH > VETOED-rule-E (no TCN; use proba-band proxy) > BROAD
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"
    elite = (prob >= 0.60) & is_hi_mid
    high = (prob >= 0.50) & (prob < 0.60) & is_hi_mid
    vetoed = (prob >= 0.30) & (prob < 0.50) & is_mid
    broad = (prob >= 0.30) & (prob < 0.50) & is_hi_mid & ~vetoed

    out = {}
    total = 0.0
    for tier, mask in [("ELITE", elite), ("HIGH", high),
                         ("VETOED", vetoed), ("BROAD", broad)]:
        idx = np.where(mask)[0]
        if len(idx) == 0:
            out[tier] = {"n": 0, "pnl": 0.0, "mean_width": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        kellies = [kelly_for_row(prob[i], widths[i], ew, el, cap) for i in idx]
        pnl = float(sum(BANKROLL * k * yreg[i] for k, i in zip(kellies, idx)))
        total += pnl
        out[tier] = {"n": int(len(idx)), "pnl": pnl,
                       "mean_width": float(widths[idx].mean()),
                       "mean_kelly": float(np.mean(kellies))}
    out["TOTAL"] = {"pnl": total}
    return out


def main():
    section("STEP 1 - Load v3-tuned-16fold WF predictions + Ising")
    v3_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    ising_path = DERIVED / "ising_daily.parquet"

    con = duckdb.connect()
    con.sql("SET memory_limit='4GB'")
    con.sql(f"""
        CREATE TABLE i AS SELECT *,
               AVG(magnetization) OVER (
                   ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
               ) AS mag_5d
        FROM read_parquet('{ising_path.as_posix()}')
    """)
    df = con.sql(f"""
        SELECT v.*,
               CASE WHEN i.mag_5d < -0.05 THEN 'LO'
                    WHEN i.mag_5d > 0.05 THEN 'HI'
                    ELSE 'MID' END AS mag_label
        FROM read_parquet('{v3_path.as_posix()}') v
        JOIN i ON v.d0 = i.d
    """).df()
    print(f"  loaded {len(df):,} OOS rows")

    section("STEP 2 - Production baseline (single alpha=0.10 globally)")
    # Production uses per-fold conformal thresholds; use the median as a proxy
    baseline_thr = float(df["conformal_width"].median()) if "conformal_width" in df.columns else 0.50
    print(f"  Baseline conformal threshold (median per-fold): {baseline_thr:.4f}")

    # Use existing conformal_width from training
    if "conformal_width" in df.columns:
        baseline_widths = df["conformal_width"].values
    else:
        baseline_widths = conformal_width(df["prob_continuer"].values, baseline_thr)

    baseline_result = evaluate_with_widths(df, baseline_widths)
    print(f"  {'tier':<8} {'n':>5} {'mean_width':>11} {'$pnl':>12}")
    for t in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        r = baseline_result[t]
        print(f"  {t:<8} {r['n']:>5,} {r.get('mean_width', 0):>10.3f} ${r['pnl']:>+11,.2f}")
    print(f"  TOTAL                             ${baseline_result['TOTAL']['pnl']:>+11,.2f}")

    section("STEP 3 - Per-tier alpha experiment")
    print("  Refitting conformal thresholds per tier with different alphas:")
    # For each tier's threshold, calibrate on the rows that BELONG to that tier
    p = df["prob_continuer"].values
    y = df["y_cls"].values
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    tier_masks = {
        "ELITE":  (p >= 0.60) & is_hi_mid,
        "HIGH":   (p >= 0.50) & (p < 0.60) & is_hi_mid,
        "VETOED": (p >= 0.30) & (p < 0.50) & is_mid,
        "BROAD":  (p >= 0.30) & (p < 0.50) & is_hi_mid,
    }

    # Per-tier alphas (smaller alpha = tighter CI = stronger gate)
    alphas = {"ELITE": 0.05, "HIGH": 0.10, "VETOED": 0.15, "BROAD": 0.20}

    # Compute per-tier conformal width arrays (broadcast back to full df)
    custom_widths = baseline_widths.copy()
    for tier, mask in tier_masks.items():
        if mask.sum() < 30:
            print(f"  {tier:<8}: too few rows ({mask.sum()}) — keep baseline width")
            continue
        alpha = alphas[tier]
        thr = conformal_calibrate(p[mask], y[mask], alpha)
        widths_t = conformal_width(p[mask], thr)
        # Apply only to this tier's rows
        custom_widths[mask] = widths_t
        print(f"  {tier:<8} alpha={alpha:.2f}  thr={thr:.4f}  "
              f"mean_width={widths_t.mean():.3f}  (n={mask.sum()})")

    section("STEP 4 - Custom-alpha evaluation")
    custom_result = evaluate_with_widths(df, custom_widths)
    print(f"  {'tier':<8} {'n':>5} {'mean_width':>11} {'$pnl':>12} {'delta_$':>11}")
    for t in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        b = baseline_result[t]
        r = custom_result[t]
        delta = r["pnl"] - b["pnl"]
        print(f"  {t:<8} {r['n']:>5,} {r.get('mean_width', 0):>10.3f} "
              f"${r['pnl']:>+11,.2f} ${delta:>+10,.2f}")
    delta_total = custom_result["TOTAL"]["pnl"] - baseline_result["TOTAL"]["pnl"]
    print(f"  TOTAL                             ${custom_result['TOTAL']['pnl']:>+11,.2f} "
          f"${delta_total:>+10,.2f}")

    section("STEP 5 - Verdict")
    if delta_total > 500:
        verdict = "SHIP per-tier alpha"
    elif delta_total > 100:
        verdict = "WEAK lift — consider"
    elif delta_total > -100:
        verdict = "NEUTRAL"
    else:
        verdict = "HOLD baseline (per-tier hurts)"
    print(f"  Delta total: ${delta_total:+,.2f}")
    print(f"  Verdict:     {verdict}")

    section("STEP 6 - Persist")
    summary = {
        "baseline_pnl": baseline_result["TOTAL"]["pnl"],
        "custom_pnl": custom_result["TOTAL"]["pnl"],
        "delta_dollars": delta_total,
        "alphas": alphas,
        "verdict": verdict,
        "per_tier": {t: {
            "baseline_pnl": baseline_result[t]["pnl"],
            "custom_pnl": custom_result[t]["pnl"],
            "delta_dollars": custom_result[t]["pnl"] - baseline_result[t]["pnl"],
        } for t in ["ELITE", "HIGH", "VETOED", "BROAD"]},
    }
    out = MODELS / "per_tier_alpha_conformal_summary.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
