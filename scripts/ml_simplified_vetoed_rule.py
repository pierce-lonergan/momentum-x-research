"""Explore TCN-free replacements for the VETOED tier rule.

Session 114's placebo TCN debunk showed the existing rule
  VETOED = (v3t >= 0.30) AND (mag = MID) AND (tcn < 0.30)
is statistically indistinguishable from a random subsample of v3+MID-mag.

This script measures candidate TCN-free replacements on 16-fold WF:

  CANDIDATE A (drop):     no VETOED tier; absorb into BROAD
  CANDIDATE B (band):     v3t in [0.30, 0.40] + mag = MID
  CANDIDATE C (lsp_low):  v3t >= 0.30 + mag = MID + intraday_pct < median
  CANDIDATE D (lsp_p25):  v3t >= 0.30 + mag = MID + intraday_pct < p25
  CANDIDATE E (current):  the production rule (for reference)

For each candidate, reports:
  n picks, mean T+5 %, win %, total y_reg sum, $-PNL with aggressive Kelly
  ($10k bankroll, KELLY_CAP = 0.20 for VETOED)

Goal: pick the cleanest TCN-free replacement that matches or beats current
VETOED for post-Monday production.
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

# Aggressive Kelly cap for VETOED tier (mirrors ml_meta_scorer.py)
KELLY_CAP = 0.20
# Per-tier expected win/loss (frozen from s109 WF for Kelly inputs)
EW_PCT = 0.349   # +34.9% on winning VETOED picks
EL_PCT = -0.142  # -14.2% on losing VETOED picks
BANKROLL = 10_000.0


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly_for_row(p: float, conformal_w: float = 0.5) -> float:
    """Conformal-modulated Kelly with cap."""
    if p < 0.30: return 0.0
    if abs(EL_PCT) < 1e-6 or EW_PCT <= 0: return 0.0
    b = abs(EW_PCT / EL_PCT)
    q = 1.0 - p
    f_star = (b * p - q) / b
    f_star = max(0.0, f_star)
    width_mod = float(np.exp(-2 * conformal_w))
    return float(min(f_star * width_mod, KELLY_CAP))


def evaluate_rule(merged: pd.DataFrame, mask: pd.Series, label: str) -> dict:
    yreg = merged["y_reg"].clip(-0.5, 1.0)
    n = int(mask.sum())
    if n == 0:
        return {"label": label, "n": 0, "avg_pct": 0.0, "win_pct": 0.0,
                "total_yreg": 0.0, "kelly_pnl_dollars": 0.0,
                "mean_kelly_pct": 0.0}
    avg = float(yreg[mask].mean() * 100)
    win = float((yreg[mask] > 0).mean() * 100)
    total = float(yreg[mask].sum())

    # Per-pick Kelly $-PNL
    sub = merged[mask].copy()
    if "v3t_conformal_w" in sub.columns:
        widths = sub["v3t_conformal_w"].fillna(0.5)
    else:
        widths = pd.Series([0.5] * len(sub), index=sub.index)
    kellies = [kelly_for_row(p, w)
               for p, w in zip(sub["prob_continuer"], widths)]
    kelly_pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg[mask])))
    mean_k = float(np.mean(kellies)) * 100

    return {
        "label": label, "n": n, "avg_pct": avg, "win_pct": win,
        "total_yreg": total, "kelly_pnl_dollars": kelly_pnl,
        "mean_kelly_pct": mean_k,
    }


def main():
    section("STEP 1 - Load + join WF predictions")
    v3t_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    tcn_path = DERIVED / "tcn_intraday_walkforward_predictions.parquet"
    ising_path = DERIVED / "ising_daily.parquet"
    aftermath_path = DERIVED / "aftermath_strat.parquet"

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
               v.conformal_width AS v3t_conformal_w,
               t.tcn_proba,
               a.intraday_pct,
               i.mag_5d,
               CASE WHEN i.mag_5d < -0.05 THEN 'LO'
                    WHEN i.mag_5d > 0.05 THEN 'HI'
                    ELSE 'MID' END AS mag_label
        FROM read_parquet('{v3t_path.as_posix()}') v
        INNER JOIN read_parquet('{tcn_path.as_posix()}') t
            ON v.d0 = t.d0 AND v.ticker = t.ticker
        INNER JOIN read_parquet('{aftermath_path.as_posix()}') a
            ON v.d0 = a.d0 AND v.ticker = a.ticker
        JOIN i ON v.d0 = i.d
    """).df()
    print(f"  loaded {len(merged):,} OOS rows (v3t intersect tcn intersect aftermath)")

    # Cohort: rows where v3t qualifies + mag is MID
    cohort = (merged["prob_continuer"] >= 0.30) & (merged["mag_label"] == "MID")
    print(f"  v3+MID cohort: {cohort.sum():,} rows")

    # Compute intraday_pct percentiles within cohort
    cohort_intra = merged.loc[cohort, "intraday_pct"]
    median_intra = float(cohort_intra.median())
    p25_intra = float(cohort_intra.quantile(0.25))
    print(f"  cohort intraday_pct median: {median_intra:+.4f}, p25: {p25_intra:+.4f}")

    section("STEP 2 - Evaluate candidate rules")

    # CANDIDATE A: drop VETOED (just measure cohort = full v3+MID)
    rule_a = cohort
    r_a = evaluate_rule(merged, rule_a, "A: full v3+MID (drop VETOED)")

    # CANDIDATE B: v3t in [0.30, 0.40] + MID
    rule_b = cohort & (merged["prob_continuer"] < 0.40)
    r_b = evaluate_rule(merged, rule_b, "B: v3t [0.30,0.40] + MID")

    # CANDIDATE C: v3+MID + low intraday_pct (below median, LSP 2019 fade prior)
    rule_c = cohort & (merged["intraday_pct"] < median_intra)
    r_c = evaluate_rule(merged, rule_c, "C: v3+MID + intra<median")

    # CANDIDATE D: v3+MID + intra<p25 (more conservative LSP)
    rule_d = cohort & (merged["intraday_pct"] < p25_intra)
    r_d = evaluate_rule(merged, rule_d, "D: v3+MID + intra<p25")

    # CANDIDATE E: production rule (for reference)
    rule_e = cohort & (merged["tcn_proba"] < 0.30)
    r_e = evaluate_rule(merged, rule_e, "E: PRODUCTION (v3+MID+tcn<0.30)")

    # Print results
    print(f"\n  {'rule':<40} {'n':>5} {'avg':>8} {'win%':>6} "
          f"{'kelly$':>10} {'mean_k%':>8}")
    print(f"  {'-'*88}")
    for r in (r_a, r_b, r_c, r_d, r_e):
        print(f"  {r['label']:<40} {r['n']:>5,} {r['avg_pct']:>+7.2f}% "
              f"{r['win_pct']:>5.1f}% ${r['kelly_pnl_dollars']:>+9.2f} "
              f"{r['mean_kelly_pct']:>7.3f}%")

    section("STEP 3 - Recommendation")
    candidates = {
        "A": r_a, "B": r_b, "C": r_c, "D": r_d, "E": r_e,
    }
    # Sort by Kelly $-PNL (production-relevant metric)
    by_pnl = sorted(candidates.items(), key=lambda kv: -kv[1]["kelly_pnl_dollars"])
    print(f"  Top by $-PNL: {by_pnl[0][0]} ({by_pnl[0][1]['label']})")
    print(f"     ${by_pnl[0][1]['kelly_pnl_dollars']:+.2f} on n={by_pnl[0][1]['n']}")
    print()
    # Sort by avg pct
    by_avg = sorted(candidates.items(), key=lambda kv: -kv[1]["avg_pct"])
    print(f"  Top by avg-%: {by_avg[0][0]} ({by_avg[0][1]['label']})")
    print(f"     +{by_avg[0][1]['avg_pct']:.2f}% on n={by_avg[0][1]['n']}")

    out = {
        "production": r_e,
        "candidates": {
            "A_drop_vetoed": r_a, "B_band_v3t": r_b,
            "C_intra_below_median": r_c, "D_intra_below_p25": r_d,
        },
        "winner_pnl": by_pnl[0][0],
        "winner_avg": by_avg[0][0],
        "context": {
            "median_intraday": median_intra,
            "p25_intraday": p25_intra,
            "kelly_cap": KELLY_CAP,
            "ew_pct": EW_PCT,
            "el_pct": EL_PCT,
            "bankroll": BANKROLL,
        },
    }
    out_path = MODELS / "vetoed_rule_alternatives.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
