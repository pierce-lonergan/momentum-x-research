"""D281 verdict — does the cohort cascade beat the production baseline?

Loads:
  - Production: ml_v2_walkforward_predictions_v3_tuned_16fold.parquet
  - Specialists: ml_v3_tier_specialist_{ELITE,HIGH,VETOED,BROAD}_predictions.parquet

Computes Aggressive-Kelly $-PNL on a $10k bankroll for each strategy:

  A) PRODUCTION baseline = single-model tier waterfall on v3-tuned-16f
     ELITE  if v3t >= 0.60 AND mag IN (HI, MID)
     HIGH   if v3t >= 0.50 AND mag IN (HI, MID)
     VETOED if v3t >= 0.30 AND mag = MID AND intra_pct < p25  (rule D)
     BROAD  if v3t >= 0.30 AND mag IN (HI, MID)

  B) TIERED CASCADE = each tier uses its specialist's prediction
     ELITE  if elite_p >= 0.30 AND mag IN (HI, MID)
     HIGH   if (high_p >= 0.30 AND mag IN (HI, MID)) AND not ELITE
     VETOED if (vetoed_p >= 0.30 AND mag = MID AND intra<p25) AND not ELITE/HIGH
     BROAD  if (broad_p >= 0.30 AND mag IN (HI, MID)) AND not ELITE/HIGH/VETOED

VERDICT: SHIP if total_$_lift >= +$500 AND no individual tier regresses > 30%.
Otherwise HOLD baseline.
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
    """Single-model tier waterfall on v3-tuned-16f (production)."""
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
            "mean_kelly": float(np.mean(kellies)),
        }
    out["TOTAL"] = {"pnl": total_pnl}
    return out


def evaluate_tiered(df: pd.DataFrame) -> dict:
    """Each tier uses its specialist's prediction. Cascade order ensures a
    candidate is assigned to the most-exclusive tier it qualifies for."""
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    width = df["conformal_width"].values
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    elite_p = df["prob_specialist_ELITE"].values
    high_p = df["prob_specialist_HIGH"].values
    vetoed_p = df["prob_specialist_VETOED"].values
    broad_p = df["prob_specialist_BROAD"].values

    elite = (elite_p >= 0.30) & is_hi_mid
    high = (high_p >= 0.30) & is_hi_mid & ~elite
    vetoed = (vetoed_p >= 0.30) & is_mid & ~elite & ~high
    broad = (broad_p >= 0.30) & is_hi_mid & ~elite & ~high & ~vetoed

    out = {}
    total_pnl = 0.0
    for tier, mask, prob in [
        ("ELITE", elite, elite_p),
        ("HIGH", high, high_p),
        ("VETOED", vetoed, vetoed_p),
        ("BROAD", broad, broad_p),
    ]:
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
            "mean_kelly": float(np.mean(kellies)),
        }
    out["TOTAL"] = {"pnl": total_pnl}
    return out


def main() -> int:
    section("D281 STEP 1 — Load production + specialist predictions")
    prod_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    if not prod_path.exists():
        print(f"  ERROR: production predictions missing at {prod_path}")
        return 1
    prod = pd.read_parquet(prod_path)
    print(f"  production: {len(prod):,} rows")

    spec_dfs = {}
    for tier in ("BROAD", "VETOED", "HIGH", "ELITE"):
        p = DERIVED / f"ml_v3_tier_specialist_{tier}_predictions.parquet"
        if not p.exists():
            print(f"  ERROR: specialist predictions missing at {p}")
            return 1
        spec_dfs[tier] = pd.read_parquet(p)
        print(f"  {tier:<8}: {len(spec_dfs[tier]):,} rows")

    # Join Ising mag_label
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
    mag_df = con.sql("""
        SELECT d AS d0, mag_5d,
               CASE WHEN mag_5d < -0.05 THEN 'LO'
                    WHEN mag_5d > 0.05 THEN 'HI'
                    ELSE 'MID' END AS mag_label
        FROM i
    """).df()
    mag_df["d0"] = pd.to_datetime(mag_df["d0"])

    section("D281 STEP 2 — Join all on (d0, ticker, fold)")
    # Merge specialists into single frame keyed on (d0, ticker, fold)
    base = spec_dfs["BROAD"][["d0", "ticker", "fold", "y_cls", "y_reg",
                                "conformal_width", "prob_specialist_BROAD"]].copy()
    for tier in ("VETOED", "HIGH", "ELITE"):
        col = f"prob_specialist_{tier}"
        base = base.merge(
            spec_dfs[tier][["d0", "ticker", "fold", col]],
            on=["d0", "ticker", "fold"], how="inner",
        )
    base["d0"] = pd.to_datetime(base["d0"])
    base = base.merge(mag_df, on="d0", how="left")
    base = base.dropna(subset=["mag_label"])
    print(f"  joined frame: {len(base):,} rows with mag_label")

    # Production baseline frame
    prod["d0"] = pd.to_datetime(prod["d0"])
    prod = prod.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    print(f"  production frame: {len(prod):,} rows with mag_label")

    section("D281 STEP 3 — Evaluate PRODUCTION baseline (v3-tuned-16f)")
    prod_result = evaluate_baseline(prod)
    print(f"  {'tier':<8} {'n':>5} {'avg_pct':>9} {'win_pct':>9} {'$pnl':>12}")
    for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
        r = prod_result[t]
        print(f"  {t:<8} {r['n']:>5,} {r['avg_pct']:>+8.2f}% {r['win_pct']:>8.1f}% "
              f"${r['pnl']:>+11,.2f}")
    print(f"  TOTAL                                  ${prod_result['TOTAL']['pnl']:>+11,.2f}")

    section("D281 STEP 4 — Evaluate TIERED CASCADE (4 specialists)")
    tier_result = evaluate_tiered(base)
    print(f"  {'tier':<8} {'n':>5} {'avg_pct':>9} {'win_pct':>9} {'$pnl':>12} {'delta_$':>11}")
    for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
        b = prod_result[t]
        r = tier_result[t]
        delta = r["pnl"] - b["pnl"]
        print(f"  {t:<8} {r['n']:>5,} {r['avg_pct']:>+8.2f}% {r['win_pct']:>8.1f}% "
              f"${r['pnl']:>+11,.2f} ${delta:>+10,.2f}")
    delta_total = tier_result["TOTAL"]["pnl"] - prod_result["TOTAL"]["pnl"]
    print(f"  TOTAL                                  ${tier_result['TOTAL']['pnl']:>+11,.2f} "
          f"${delta_total:>+10,.2f}")

    section("D281 STEP 5 — Verdict")
    # Tier-level regression check (no tier may lose > 30%)
    max_regression = 0.0
    worst_tier = None
    for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
        if prod_result[t]["pnl"] > 0:
            pct_change = (tier_result[t]["pnl"] - prod_result[t]["pnl"]) / prod_result[t]["pnl"]
            if pct_change < max_regression:
                max_regression = pct_change
                worst_tier = t

    if delta_total >= 500 and max_regression > -0.30:
        verdict = "SHIP"
        explanation = f"Δ=+${delta_total:,.2f} >= +$500 AND no tier regressed > 30%"
    elif delta_total >= 100:
        verdict = "WEAK_LIFT_CONSIDER"
        explanation = f"Δ=+${delta_total:,.2f} ∈ [+$100, +$500); marginal lift"
    elif delta_total > -100:
        verdict = "NEUTRAL"
        explanation = f"Δ=${delta_total:+,.2f} ∈ (-$100, +$100); within noise"
    else:
        verdict = "HOLD_BASELINE"
        explanation = f"Δ=${delta_total:+,.2f} < -$100; cascade hurts"
    if max_regression < -0.30:
        verdict = "HOLD_BASELINE"
        explanation = (f"{worst_tier} tier regressed {max_regression*100:.1f}% "
                       f"(threshold -30%); blocks SHIP")

    print(f"  Total delta: ${delta_total:+,.2f}")
    print(f"  Worst tier regression: {max_regression*100:+.1f}% ({worst_tier})")
    print(f"  Verdict:    {verdict}")
    print(f"  Reason:     {explanation}")

    summary = {
        "baseline_pnl": prod_result["TOTAL"]["pnl"],
        "tiered_pnl": tier_result["TOTAL"]["pnl"],
        "delta_dollars": delta_total,
        "max_tier_regression_pct": max_regression * 100,
        "worst_tier": worst_tier,
        "verdict": verdict,
        "explanation": explanation,
        "per_tier": {t: {
            "baseline": prod_result[t],
            "tiered": tier_result[t],
            "delta_dollars": tier_result[t]["pnl"] - prod_result[t]["pnl"],
        } for t in ("ELITE", "HIGH", "VETOED", "BROAD")},
    }
    out = MODELS / "v3_tier_specialists_verdict.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
