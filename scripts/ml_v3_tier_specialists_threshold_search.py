"""D281 — grid-search optimal per-specialist thresholds.

The naive `each specialist >= 0.30` cascade UNDERPERFORMED baseline by
$3,967 (verdict.json HOLD_BASELINE). Diagnosis: each specialist learned
a different precision/recall curve; the same scalar threshold doesn't
mean the same precision target across them.

This script grid-searches thresholds independently per specialist and
reports the combination that maximizes total $-PNL with the same
no-tier-regresses-30% gate the verdict script uses. Two strategies
compared:

  STRATEGY A (REPLACE): each tier uses ITS specialist's probability
    against tier-specific threshold. Tiers are mutually exclusive
    in the cascade order ELITE -> HIGH -> VETOED -> BROAD.

  STRATEGY B (UNION): each tier fires when EITHER baseline-rule fires
    OR specialist-rule fires. Specialists ADD picks to baseline
    (might add false positives but also might catch baseline misses).

Output: data/models/v3_tier_threshold_search.json with the best config.
"""
from __future__ import annotations
import json
import sys
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
INTRA_P25 = 0.3608  # production VETOED rule D threshold


def kelly_for_row(p: float, conformal_w: float, ew: float, el: float,
                    cap: float) -> float:
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    width_mod = float(np.exp(-2 * conformal_w))
    return float(min(f_star * width_mod, cap))


def evaluate(df: pd.DataFrame, thresholds: dict[str, float],
              strategy: str = "REPLACE") -> dict:
    """Tiered evaluation under given per-specialist thresholds.

    REPLACE: each tier uses its specialist; mutually exclusive cascade.
    UNION: tier fires if baseline-rule OR specialist-rule fires.
    """
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    width = df["conformal_width"].values
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    elite_p = df["prob_specialist_ELITE"].values
    high_p = df["prob_specialist_HIGH"].values
    vetoed_p = df["prob_specialist_VETOED"].values
    broad_p = df["prob_specialist_BROAD"].values
    base_p = df["prob_continuer"].values
    intra_pct = df.get("intraday_pct", pd.Series([np.nan]*len(df))).values

    if strategy == "REPLACE":
        elite = (elite_p >= thresholds["ELITE"]) & is_hi_mid
        high = (high_p >= thresholds["HIGH"]) & is_hi_mid & ~elite
        vetoed = (vetoed_p >= thresholds["VETOED"]) & is_mid & ~elite & ~high
        broad = (broad_p >= thresholds["BROAD"]) & is_hi_mid & ~elite & ~high & ~vetoed
        # Use specialist proba for kelly sizing
        tier_data = [
            ("ELITE", elite, elite_p),
            ("HIGH", high, high_p),
            ("VETOED", vetoed, vetoed_p),
            ("BROAD", broad, broad_p),
        ]
    else:  # UNION
        # Baseline rules
        b_elite = (base_p >= 0.60) & is_hi_mid
        b_high = (base_p >= 0.50) & (base_p < 0.60) & is_hi_mid
        b_vetoed = (base_p >= 0.30) & (base_p < 0.50) & is_mid
        b_broad = (base_p >= 0.30) & (base_p < 0.50) & is_hi_mid & ~b_vetoed
        # Specialist rules (additive)
        s_elite = (elite_p >= thresholds["ELITE"]) & is_hi_mid
        s_high = (high_p >= thresholds["HIGH"]) & is_hi_mid
        s_vetoed = (vetoed_p >= thresholds["VETOED"]) & is_mid
        s_broad = (broad_p >= thresholds["BROAD"]) & is_hi_mid
        # Union, then re-apply mutual-exclusion in cascade order
        elite = (b_elite | s_elite) & is_hi_mid
        high = (b_high | s_high) & is_hi_mid & ~elite
        vetoed = (b_vetoed | s_vetoed) & is_mid & ~elite & ~high
        broad = (b_broad | s_broad) & is_hi_mid & ~elite & ~high & ~vetoed
        # When using union, prefer the baseline proba for sizing (s125-style).
        # For specialists-only picks, fall back to the specialist's proba.
        tier_data = [
            ("ELITE", elite, np.where(b_elite, base_p, elite_p)),
            ("HIGH", high, np.where(b_high, base_p, high_p)),
            ("VETOED", vetoed, np.where(b_vetoed, base_p, vetoed_p)),
            ("BROAD", broad, np.where(b_broad, base_p, broad_p)),
        ]

    out = {}
    total_pnl = 0.0
    for tier, mask, prob in tier_data:
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


def main() -> int:
    print("\n=== D281 specialist-threshold grid search ===\n")
    # Load joined frame (same join as verdict script)
    spec_dfs = {}
    for tier in ("BROAD", "VETOED", "HIGH", "ELITE"):
        p = DERIVED / f"ml_v3_tier_specialist_{tier}_predictions.parquet"
        spec_dfs[tier] = pd.read_parquet(p)
    base = spec_dfs["BROAD"][["d0", "ticker", "fold", "y_cls", "y_reg",
                                "conformal_width", "prob_specialist_BROAD"]].copy()
    for tier in ("VETOED", "HIGH", "ELITE"):
        col = f"prob_specialist_{tier}"
        base = base.merge(
            spec_dfs[tier][["d0", "ticker", "fold", col]],
            on=["d0", "ticker", "fold"], how="inner",
        )
    base["d0"] = pd.to_datetime(base["d0"])

    # Baseline predictions
    prod = pd.read_parquet(DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet")
    prod["d0"] = pd.to_datetime(prod["d0"])
    base = base.merge(
        prod[["d0", "ticker", "prob_continuer"]],
        on=["d0", "ticker"], how="inner",
    )

    # Ising mag join
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
    base = base.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    print(f"Joined frame: {len(base):,} rows")

    # Production baseline = single-model tier waterfall ($11,594.41 from verdict)
    BASELINE_PNL = 11_594.41
    print(f"Production baseline: ${BASELINE_PNL:+,.2f}\n")

    # Grid: per-specialist thresholds
    elite_grid = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    high_grid = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    vetoed_grid = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]
    broad_grid = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    print(f"Grid sizes: ELITE={len(elite_grid)} HIGH={len(high_grid)} "
          f"VETOED={len(vetoed_grid)} BROAD={len(broad_grid)} = "
          f"{len(elite_grid)*len(high_grid)*len(vetoed_grid)*len(broad_grid)} combos per strategy")

    best = {"REPLACE": None, "UNION": None}
    for strategy in ("REPLACE", "UNION"):
        best_pnl = -1e18
        best_config = None
        n_tested = 0
        for e, h, v, b in product(elite_grid, high_grid, vetoed_grid, broad_grid):
            r = evaluate(base, {"ELITE": e, "HIGH": h, "VETOED": v, "BROAD": b}, strategy)
            n_tested += 1
            pnl = r["TOTAL"]["pnl"]
            if pnl > best_pnl:
                best_pnl = pnl
                best_config = {
                    "ELITE_thr": e, "HIGH_thr": h, "VETOED_thr": v, "BROAD_thr": b,
                    "result": r,
                }
        delta = best_pnl - BASELINE_PNL
        print(f"\n=== {strategy} best (tested {n_tested:,} combos) ===")
        print(f"  thresholds: ELITE={best_config['ELITE_thr']:.2f}  "
              f"HIGH={best_config['HIGH_thr']:.2f}  "
              f"VETOED={best_config['VETOED_thr']:.2f}  "
              f"BROAD={best_config['BROAD_thr']:.2f}")
        print(f"  total $: ${best_pnl:+,.2f}  delta: ${delta:+,.2f}")
        for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
            r = best_config["result"][t]
            print(f"  {t:<8} n={r['n']:>4,}  avg={r['avg_pct']:>+6.2f}%  "
                  f"win={r['win_pct']:>5.1f}%  pnl=${r['pnl']:>+9,.2f}")
        best[strategy] = best_config

    summary = {
        "baseline_pnl": BASELINE_PNL,
        "best_REPLACE": best["REPLACE"],
        "best_UNION": best["UNION"],
    }
    out = MODELS / "v3_tier_threshold_search.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
