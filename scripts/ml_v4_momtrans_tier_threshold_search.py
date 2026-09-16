"""Grid-search per-tier thresholds for MoMTrans tier specialists.

D281 grid-searched thresholds for v3-style XGBoost specialists. MoMTrans
probabilities have different calibration (pos_weight=4.0 makes them
over-confident), so the v3 thresholds don't transfer. This script
re-tunes ELITE/HIGH/VETOED/BROAD thresholds against MoMTrans probas.

Same UNION strategy as D281: tier fires if specialist >= threshold AND
mag gate passes. Tiers are mutually exclusive in cascade order
ELITE > HIGH > VETOED > BROAD.

Outputs the SHIP/HOLD verdict against production v3 baseline.
"""
from __future__ import annotations
import json
from itertools import product
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


def kelly(p, w, ew, el, cap):
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    return float(min(f_star * float(np.exp(-2 * w)), cap))


def evaluate(base, thresholds):
    yreg = base["y_reg"].clip(-0.5, 1.0).values
    width = base["conformal_width"].values
    mag = base["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"
    sp = {tier: base[f"prob_specialist_{tier}"].values
          for tier in ("ELITE", "HIGH", "VETOED", "BROAD")}

    elite = (sp["ELITE"] >= thresholds["ELITE"]) & is_hi_mid
    high = (sp["HIGH"] >= thresholds["HIGH"]) & is_hi_mid & ~elite
    vetoed = (sp["VETOED"] >= thresholds["VETOED"]) & is_mid & ~elite & ~high
    broad = (sp["BROAD"] >= thresholds["BROAD"]) & is_hi_mid & ~elite & ~high & ~vetoed

    out = {}
    total = 0.0
    for tier_name, mask, prob in [
        ("ELITE", elite, sp["ELITE"]),
        ("HIGH", high, sp["HIGH"]),
        ("VETOED", vetoed, sp["VETOED"]),
        ("BROAD", broad, sp["BROAD"]),
    ]:
        idx = np.where(mask)[0]
        if not len(idx):
            out[tier_name] = {"n": 0, "pnl": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier_name]; cap = KELLY_CAPS[tier_name]
        prob_eff = np.maximum(prob, 0.31)
        pnl = float(sum(BANKROLL * kelly(prob_eff[i], width[i], ew, el, cap) * yreg[i]
                          for i in idx))
        total += pnl
        out[tier_name] = {"n": int(len(idx)), "pnl": pnl}
    out["TOTAL"] = {"pnl": total}
    return out


def main():
    # Load specialists + production baseline
    base = pd.read_parquet(MODELS / "momtrans_v4_tier_BROAD_predictions.parquet")[
        ["d0", "ticker", "fold", "y_reg", "prob_binary"]
    ].rename(columns={"prob_binary": "prob_specialist_BROAD"})
    base["d0"] = pd.to_datetime(base["d0"])
    for tier in ("VETOED", "HIGH", "ELITE"):
        df_t = pd.read_parquet(MODELS / f"momtrans_v4_tier_{tier}_predictions.parquet")[
            ["d0", "ticker", "fold", "prob_binary"]
        ]
        df_t["d0"] = pd.to_datetime(df_t["d0"])
        base = base.merge(
            df_t.rename(columns={"prob_binary": f"prob_specialist_{tier}"}),
            on=["d0", "ticker", "fold"], how="inner",
        )
    base["conformal_width"] = 0.5

    # Ising mag join
    ising_path = DERIVED / "ising_daily.parquet"
    con = duckdb.connect()
    con.sql(f"""CREATE TABLE i AS SELECT *,
        AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS mag_5d FROM read_parquet('{ising_path.as_posix()}')""")
    mag_df = con.sql("""SELECT d AS d0,
        CASE WHEN mag_5d < -0.05 THEN 'LO' WHEN mag_5d > 0.05 THEN 'HI' ELSE 'MID' END
        AS mag_label FROM i""").df()
    mag_df["d0"] = pd.to_datetime(mag_df["d0"])
    base = base.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])

    # Look at probability distributions
    print("\nMoMTrans specialist probability distributions:")
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        col = f"prob_specialist_{tier}"
        s = base[col]
        print(f"  {tier:<8}: min={s.min():.3f}  p25={s.quantile(0.25):.3f}  "
              f"p50={s.quantile(0.50):.3f}  p75={s.quantile(0.75):.3f}  "
              f"p95={s.quantile(0.95):.3f}  max={s.max():.3f}")

    # Production baseline: $11,594.41 (cohort cascade) vs $13,095.72 (single-model)
    # Use single-model baseline since this is single-strategy comparison
    PROD_PNL = 13_095.72

    # Grid: per-specialist thresholds — denser grid than D281 since
    # MoMTrans probabilities are differently calibrated
    elite_grid = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
    high_grid = [0.40, 0.50, 0.60, 0.70, 0.80]
    vetoed_grid = [0.50, 0.60, 0.70, 0.80, 0.90]
    broad_grid = [0.50, 0.60, 0.70, 0.80]

    n_combos = len(elite_grid) * len(high_grid) * len(vetoed_grid) * len(broad_grid)
    print(f"\nSearching {n_combos} threshold combos against MoMTrans tier specialists")
    print(f"Production baseline: ${PROD_PNL:+,.2f}\n")

    best_pnl = -1e18
    best_config = None
    best_result = None
    for e, h, v, b in product(elite_grid, high_grid, vetoed_grid, broad_grid):
        r = evaluate(base, {"ELITE": e, "HIGH": h, "VETOED": v, "BROAD": b})
        if r["TOTAL"]["pnl"] > best_pnl:
            best_pnl = r["TOTAL"]["pnl"]
            best_config = {"ELITE": e, "HIGH": h, "VETOED": v, "BROAD": b}
            best_result = r

    print(f"BEST CONFIG: {best_config}")
    print(f"  total: ${best_pnl:+,.2f}  delta vs prod: ${best_pnl - PROD_PNL:+,.2f}")
    print(f"  per-tier:")
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        r = best_result[tier]
        print(f"    {tier:<8} n={r['n']:>5,}  ${r['pnl']:>+9,.2f}")

    # SHIP gate
    delta = best_pnl - PROD_PNL
    print()
    if delta >= 500:
        print(f"  VERDICT: WOULD SHIP — Δ=${delta:+,.2f} >= +$500")
    elif delta > -500:
        print(f"  VERDICT: NEUTRAL — Δ=${delta:+,.2f}")
    else:
        print(f"  VERDICT: HOLD — Δ=${delta:+,.2f}")

    summary = {
        "production_baseline_pnl": PROD_PNL,
        "best_config": best_config,
        "best_pnl": best_pnl,
        "delta_vs_prod": delta,
        "best_per_tier": best_result,
    }
    out = MODELS / "momtrans_v4_tier_threshold_search.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
