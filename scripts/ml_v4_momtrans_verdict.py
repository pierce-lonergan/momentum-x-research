"""MoMTrans v4 verdict — does the multi-task transformer beat baseline?

Loads:
  - Production: ml_v2_walkforward_predictions_v3_tuned_16fold.parquet
  - MoMTrans:   data/models/momtrans_v4_predictions.parquet

Computes Aggressive-Kelly $-PNL on a $10k bankroll for THREE strategies:

  A) PRODUCTION baseline (single-model tier waterfall on v3-tuned-16f)
  B) MoMTrans-binary-only (use binary head's prob_continuer like v3t)
  C) MoMTrans-cohort (use multi-class cohort head's argmax for tier)

VERDICT (same gate as D281):
  SHIP if total_$_lift >= +$500 AND no individual tier regresses > 30%.

This is RESEARCH evaluation only. The lottery launcher does NOT load
MoMTrans. A positive verdict here would unlock a follow-on session to
wire `MX_USE_MOMTRANS=1` into MetaScorer.
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
COHORT_TO_IDX = {"SKIP": 0, "BROAD": 1, "VETOED": 2, "HIGH": 3, "ELITE": 4}
IDX_TO_COHORT = {v: k for k, v in COHORT_TO_IDX.items()}


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly_for_row(p: float, conformal_w: float, ew: float, el: float,
                    cap: float) -> float:
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    width_mod = float(np.exp(-2 * conformal_w))
    return float(min(f_star * width_mod, cap))


def evaluate_baseline(df: pd.DataFrame) -> dict:
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    prob = df["prob_continuer"].values
    width = df["conformal_width"].values
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    elite = (prob >= 0.60) & is_hi_mid
    high = (prob >= 0.50) & (prob < 0.60) & is_hi_mid
    vetoed = (prob >= 0.30) & (prob < 0.50) & is_mid
    broad = (prob >= 0.30) & (prob < 0.50) & is_hi_mid & ~vetoed

    out = {}
    total_pnl = 0.0
    for tier, mask in [("ELITE", elite), ("HIGH", high),
                         ("VETOED", vetoed), ("BROAD", broad)]:
        idx = np.where(mask)[0]
        if len(idx) == 0:
            out[tier] = {"n": 0, "pnl": 0.0, "avg_pct": 0.0, "win_pct": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        kellies = [kelly_for_row(prob[i], width[i], ew, el, cap) for i in idx]
        pnl = float(sum(BANKROLL * k * yreg[i] for k, i in zip(kellies, idx)))
        total_pnl += pnl
        out[tier] = {
            "n": int(len(idx)), "pnl": pnl,
            "avg_pct": float(yreg[idx].mean() * 100),
            "win_pct": float((yreg[idx] > 0).mean() * 100),
        }
    out["TOTAL"] = {"pnl": total_pnl}
    return out


def evaluate_momtrans_binary(df: pd.DataFrame) -> dict:
    """MoMTrans binary head used as drop-in replacement for v3t. Same tier
    waterfall as production. Uses MoMTrans quantile head's interquartile
    range (q90 - q10) as a conformal-width substitute."""
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    prob = df["prob_binary"].values
    # Use IQR as the prediction-uncertainty modulator. Wider IQR -> higher
    # uncertainty -> smaller width_mod. Normalize to [0, 1] roughly.
    iqr = (df["q90"] - df["q10"]).values
    width = np.clip(iqr / max(iqr.max(), 0.01), 0.0, 1.0)
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    elite = (prob >= 0.60) & is_hi_mid
    high = (prob >= 0.50) & (prob < 0.60) & is_hi_mid
    vetoed = (prob >= 0.30) & (prob < 0.50) & is_mid
    broad = (prob >= 0.30) & (prob < 0.50) & is_hi_mid & ~vetoed

    out = {}
    total_pnl = 0.0
    for tier, mask in [("ELITE", elite), ("HIGH", high),
                         ("VETOED", vetoed), ("BROAD", broad)]:
        idx = np.where(mask)[0]
        if len(idx) == 0:
            out[tier] = {"n": 0, "pnl": 0.0, "avg_pct": 0.0, "win_pct": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        kellies = [kelly_for_row(prob[i], width[i], ew, el, cap) for i in idx]
        pnl = float(sum(BANKROLL * k * yreg[i] for k, i in zip(kellies, idx)))
        total_pnl += pnl
        out[tier] = {
            "n": int(len(idx)), "pnl": pnl,
            "avg_pct": float(yreg[idx].mean() * 100),
            "win_pct": float((yreg[idx] > 0).mean() * 100),
        }
    out["TOTAL"] = {"pnl": total_pnl}
    return out


def evaluate_momtrans_cohort(df: pd.DataFrame) -> dict:
    """MoMTrans cohort head (4-way + SKIP) directly maps to tier."""
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    cohort_pred = df["cohort_pred"].values
    iqr = (df["q90"] - df["q10"]).values
    width = np.clip(iqr / max(iqr.max(), 0.01), 0.0, 1.0)
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    elite = (cohort_pred == COHORT_TO_IDX["ELITE"]) & is_hi_mid
    high = (cohort_pred == COHORT_TO_IDX["HIGH"]) & is_hi_mid & ~elite
    vetoed = (cohort_pred == COHORT_TO_IDX["VETOED"]) & is_mid & ~elite & ~high
    broad = (cohort_pred == COHORT_TO_IDX["BROAD"]) & is_hi_mid & ~elite & ~high & ~vetoed

    out = {}
    total_pnl = 0.0
    # For cohort assignments use prob_binary as the proba going into Kelly
    prob = df["prob_binary"].values
    for tier, mask in [("ELITE", elite), ("HIGH", high),
                         ("VETOED", vetoed), ("BROAD", broad)]:
        idx = np.where(mask)[0]
        if len(idx) == 0:
            out[tier] = {"n": 0, "pnl": 0.0, "avg_pct": 0.0, "win_pct": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        # Use elevated proba floor for cohort-assigned picks (model declared
        # confidence by tier choice, even if binary head < 0.30)
        kellies = [
            kelly_for_row(max(prob[i], 0.31), width[i], ew, el, cap)
            for i in idx
        ]
        pnl = float(sum(BANKROLL * k * yreg[i] for k, i in zip(kellies, idx)))
        total_pnl += pnl
        out[tier] = {
            "n": int(len(idx)), "pnl": pnl,
            "avg_pct": float(yreg[idx].mean() * 100),
            "win_pct": float((yreg[idx] > 0).mean() * 100),
        }
    out["TOTAL"] = {"pnl": total_pnl}
    return out


def print_result(label: str, baseline: dict, result: dict) -> tuple[float, float]:
    print(f"  {'tier':<8} {'n':>5} {'avg_pct':>9} {'win_pct':>9} {'$pnl':>12} {'delta_$':>11}")
    max_regression = 0.0
    worst_tier = None
    for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
        b = baseline[t]
        r = result[t]
        delta = r["pnl"] - b["pnl"]
        if b["pnl"] > 0:
            pct_change = (r["pnl"] - b["pnl"]) / b["pnl"]
            if pct_change < max_regression:
                max_regression = pct_change
                worst_tier = t
        print(f"  {t:<8} {r['n']:>5,} {r['avg_pct']:>+8.2f}% {r['win_pct']:>8.1f}% "
              f"${r['pnl']:>+11,.2f} ${delta:>+10,.2f}")
    delta_total = result["TOTAL"]["pnl"] - baseline["TOTAL"]["pnl"]
    print(f"  TOTAL                                  ${result['TOTAL']['pnl']:>+11,.2f} "
          f"${delta_total:>+10,.2f}")
    print(f"  Worst-tier regression: {max_regression*100:+.1f}% ({worst_tier})")
    return delta_total, max_regression


def main() -> int:
    section("MoMTrans v4 verdict — vs production v3-tuned-16f baseline")

    momtrans_path = MODELS / "momtrans_v4_predictions.parquet"
    if not momtrans_path.exists():
        print(f"  ERROR: {momtrans_path} missing — run "
              f"scripts/ml_v4_momtrans_train.py --full-wf first")
        return 1
    momtrans = pd.read_parquet(momtrans_path)
    momtrans["d0"] = pd.to_datetime(momtrans["d0"])
    print(f"  MoMTrans predictions: {len(momtrans):,} rows")

    prod = pd.read_parquet(DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet")
    prod["d0"] = pd.to_datetime(prod["d0"])
    print(f"  Production predictions: {len(prod):,} rows")

    # Join Ising mag_label
    ising_path = DERIVED / "ising_daily.parquet"
    con = duckdb.connect()
    con.sql(f"""CREATE TABLE i AS SELECT *,
        AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS mag_5d FROM read_parquet('{ising_path.as_posix()}')""")
    mag_df = con.sql("""SELECT d AS d0, mag_5d,
        CASE WHEN mag_5d < -0.05 THEN 'LO' WHEN mag_5d > 0.05 THEN 'HI' ELSE 'MID' END
        AS mag_label FROM i""").df()
    mag_df["d0"] = pd.to_datetime(mag_df["d0"])

    momtrans = momtrans.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    prod = prod.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])

    section("STRATEGY A — PRODUCTION baseline (v3-tuned-16f single model)")
    baseline = evaluate_baseline(prod)
    for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
        r = baseline[t]
        print(f"  {t:<8} n={r['n']:>5,} avg={r['avg_pct']:>+6.2f}% "
              f"win={r['win_pct']:>5.1f}% ${r['pnl']:>+11,.2f}")
    print(f"  TOTAL                                  ${baseline['TOTAL']['pnl']:>+11,.2f}")

    section("STRATEGY B — MoMTrans BINARY HEAD (v3t-replacement)")
    result_binary = evaluate_momtrans_binary(momtrans)
    delta_binary, regression_binary = print_result("BINARY", baseline, result_binary)

    section("STRATEGY C — MoMTrans COHORT HEAD (multi-class tier assignment)")
    result_cohort = evaluate_momtrans_cohort(momtrans)
    delta_cohort, regression_cohort = print_result("COHORT", baseline, result_cohort)

    section("VERDICT")

    def gate(label: str, delta: float, regression: float) -> str:
        if regression < -0.30:
            return f"HOLD_BASELINE (regression {regression*100:+.1f}% breaks -30% gate)"
        if delta >= 500:
            return f"SHIP ({label}: Δ=${delta:+,.2f})"
        if delta >= 100:
            return f"WEAK_LIFT_CONSIDER (Δ=${delta:+,.2f})"
        if delta > -100:
            return "NEUTRAL"
        return f"HOLD_BASELINE (Δ=${delta:+,.2f})"

    verdict_binary = gate("BINARY", delta_binary, regression_binary)
    verdict_cohort = gate("COHORT", delta_cohort, regression_cohort)
    print(f"  BINARY head verdict: {verdict_binary}")
    print(f"  COHORT head verdict: {verdict_cohort}")
    print(f"\n  Even a positive verdict ships ENV-GATED (MX_USE_MOMTRANS=1).")
    print(f"  Production stays on v3-tuned-16f + D281 cohort cascade.")

    summary = {
        "baseline_pnl": baseline["TOTAL"]["pnl"],
        "momtrans_binary_pnl": result_binary["TOTAL"]["pnl"],
        "momtrans_cohort_pnl": result_cohort["TOTAL"]["pnl"],
        "delta_binary": delta_binary,
        "delta_cohort": delta_cohort,
        "regression_binary_worst_tier_pct": regression_binary * 100,
        "regression_cohort_worst_tier_pct": regression_cohort * 100,
        "verdict_binary": verdict_binary,
        "verdict_cohort": verdict_cohort,
        "per_strategy": {
            "baseline": baseline,
            "momtrans_binary": result_binary,
            "momtrans_cohort": result_cohort,
        },
    }
    out = MODELS / "momtrans_v4_verdict.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
