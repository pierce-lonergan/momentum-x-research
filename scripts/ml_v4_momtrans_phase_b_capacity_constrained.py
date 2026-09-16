"""Phase B: capacity-constrained backtest of the MoMTrans tier cascade.

Phase A validated the +$78k lift survives an out-of-sample fold split.
But the cascade still fires 5.5x more picks than production. Live
microcap trading has a CAPACITY CEILING — there's only so much
deployment a single trader (or a microcap stock's float) can absorb
without slippage erasing the alpha.

Phase B simulates daily-pick-cap policies. For each candidate cap K
∈ {5, 10, 20, 30, 50, 100, ∞}:
  1. Within each trading day, run the tier cascade as in Phase A
  2. If more than K picks fire that day, KEEP TOP-K by tier hierarchy
     (ELITE > HIGH > VETOED > BROAD), breaking ties by specialist
     confidence
  3. Compute aggregate $-PNL across all 16 WF folds
  4. Compare to production v3 baseline

The result tells us: if you can only deploy K picks per day, what's
the cascade's $-PNL vs production?

Production currently fires ~10/day average (488 picks / ~470 trading
days = 1.04/day on average, but bursty). Cap K=10 is the realistic
capacity-constrained baseline.
"""
from __future__ import annotations
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

KELLY_CAPS = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
TIER_ORDER = {"ELITE": 0, "HIGH": 1, "VETOED": 2, "BROAD": 3}
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}
BANKROLL = 10_000.0
DAILY_CAPS = [5, 10, 15, 20, 30, 50, 100, 99999]


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly(p, w, ew, el, cap):
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    return float(min(f_star * float(np.exp(-2 * w)), cap))


def assign_tier_per_row(base: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """Assign each row to its tier (or SKIP) based on specialist probas
    and the cascade order ELITE > HIGH > VETOED > BROAD."""
    out = base.copy()
    is_hi_mid = out["mag_label"].isin(["HI", "MID"])
    is_mid = out["mag_label"] == "MID"
    sp = {tier: out[f"prob_specialist_{tier}"] for tier in
          ("ELITE", "HIGH", "VETOED", "BROAD")}

    out["tier"] = "SKIP"
    out["tier_prob"] = 0.0
    elite_mask = (sp["ELITE"] >= thresholds["ELITE"]) & is_hi_mid
    out.loc[elite_mask, "tier"] = "ELITE"
    out.loc[elite_mask, "tier_prob"] = sp["ELITE"][elite_mask]

    high_mask = (sp["HIGH"] >= thresholds["HIGH"]) & is_hi_mid & ~elite_mask
    out.loc[high_mask, "tier"] = "HIGH"
    out.loc[high_mask, "tier_prob"] = sp["HIGH"][high_mask]

    vetoed_mask = (sp["VETOED"] >= thresholds["VETOED"]) & is_mid & ~elite_mask & ~high_mask
    out.loc[vetoed_mask, "tier"] = "VETOED"
    out.loc[vetoed_mask, "tier_prob"] = sp["VETOED"][vetoed_mask]

    broad_mask = (sp["BROAD"] >= thresholds["BROAD"]) & is_hi_mid & ~elite_mask & ~high_mask & ~vetoed_mask
    out.loc[broad_mask, "tier"] = "BROAD"
    out.loc[broad_mask, "tier_prob"] = sp["BROAD"][broad_mask]
    return out


def evaluate_capped(base_with_tier: pd.DataFrame, daily_cap: int) -> dict:
    """For each trading day, KEEP TOP-K picks by (tier_priority, prob desc).
    Then compute Aggressive-Kelly $-PNL."""
    fired = base_with_tier[base_with_tier["tier"] != "SKIP"].copy()
    if fired.empty:
        return {"n_total": 0, "pnl_total": 0.0, "per_tier": {}}

    fired["tier_priority"] = fired["tier"].map(TIER_ORDER)
    # Sort within each day: tier_priority asc (ELITE first), then prob desc
    fired = fired.sort_values(["d0", "tier_priority", "tier_prob"],
                                  ascending=[True, True, False])
    # Keep top-K per day
    fired["rank_in_day"] = fired.groupby("d0").cumcount()
    kept = fired[fired["rank_in_day"] < daily_cap].copy()

    out = {"n_total": int(len(kept)), "per_tier": {}}
    total_pnl = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        sub = kept[kept["tier"] == tier]
        if sub.empty:
            out["per_tier"][tier] = {"n": 0, "pnl": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        prob_eff = np.maximum(sub["tier_prob"].values, 0.31)
        widths = sub["conformal_width"].values
        yreg = sub["y_reg"].clip(-0.5, 1.0).values
        kellies = [kelly(p, w, ew, el, cap)
                   for p, w in zip(prob_eff, widths)]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg)))
        total_pnl += pnl
        out["per_tier"][tier] = {
            "n": int(len(sub)), "pnl": pnl,
            "avg_pct": float(yreg.mean() * 100),
        }
    out["pnl_total"] = total_pnl
    return out


def evaluate_production_capped(prod: pd.DataFrame, daily_cap: int) -> dict:
    """Production v3 single-model tier waterfall, also capped at K/day."""
    is_hi_mid = prod["mag_label"].isin(["HI", "MID"])
    is_mid = prod["mag_label"] == "MID"
    p = prod.copy()
    p["tier"] = "SKIP"
    p["tier_prob"] = 0.0
    elite = (p["prob_continuer"] >= 0.60) & is_hi_mid
    high = (p["prob_continuer"] >= 0.50) & (p["prob_continuer"] < 0.60) & is_hi_mid
    vetoed = (p["prob_continuer"] >= 0.30) & (p["prob_continuer"] < 0.50) & is_mid
    broad = (p["prob_continuer"] >= 0.30) & (p["prob_continuer"] < 0.50) & is_hi_mid & ~vetoed
    # Set tier and tier_prob in two separate assignments (pandas can't accept
    # mixed-type list-of-2 in a single .loc set)
    p.loc[elite, "tier"] = "ELITE"
    p.loc[high, "tier"] = "HIGH"
    p.loc[vetoed, "tier"] = "VETOED"
    p.loc[broad, "tier"] = "BROAD"
    p.loc[elite, "tier_prob"] = p.loc[elite, "prob_continuer"].values
    p.loc[high, "tier_prob"] = p.loc[high, "prob_continuer"].values
    p.loc[vetoed, "tier_prob"] = p.loc[vetoed, "prob_continuer"].values
    p.loc[broad, "tier_prob"] = p.loc[broad, "prob_continuer"].values

    fired = p[p["tier"] != "SKIP"].copy()
    if fired.empty:
        return {"n_total": 0, "pnl_total": 0.0, "per_tier": {}}
    fired["tier_priority"] = fired["tier"].map(TIER_ORDER)
    fired = fired.sort_values(["d0", "tier_priority", "tier_prob"],
                                ascending=[True, True, False])
    fired["rank_in_day"] = fired.groupby("d0").cumcount()
    kept = fired[fired["rank_in_day"] < daily_cap].copy()

    out = {"n_total": int(len(kept)), "per_tier": {}}
    total = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        sub = kept[kept["tier"] == tier]
        if sub.empty:
            out["per_tier"][tier] = {"n": 0, "pnl": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        widths = sub["conformal_width"].values
        prob = sub["tier_prob"].values
        yreg = sub["y_reg"].clip(-0.5, 1.0).values
        kellies = [kelly(pp, w, ew, el, cap) for pp, w in zip(prob, widths)]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg)))
        total += pnl
        out["per_tier"][tier] = {"n": int(len(sub)), "pnl": pnl}
    out["pnl_total"] = total
    return out


def main() -> int:
    section("Phase B — capacity-constrained backtest")

    # Load specialists + production (mirrors Phase A loader)
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

    prod = pd.read_parquet(DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet")
    prod["d0"] = pd.to_datetime(prod["d0"])

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
    prod = prod.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])

    # Use Phase A's frozen TUNE-set thresholds (the OOS-validated ones)
    PA = json.loads((MODELS / "momtrans_v4_phase_a_oos_validation.json").read_text())
    thresholds = PA["best_thresholds"]
    print(f"  Using Phase-A frozen thresholds (OOS-validated): {thresholds}")

    base_tier = assign_tier_per_row(base, thresholds)
    fired = base_tier[base_tier["tier"] != "SKIP"]
    print(f"\n  Total cascade picks (uncapped): {len(fired):,}")
    print(f"  Trading days w/ at least 1 pick: {fired['d0'].dt.date.nunique():,}")
    daily_counts = fired.groupby(fired["d0"].dt.date).size()
    print(f"  Daily pick distribution: mean={daily_counts.mean():.1f}  "
          f"p50={daily_counts.median():.0f}  p75={daily_counts.quantile(0.75):.0f}  "
          f"p95={daily_counts.quantile(0.95):.0f}  max={daily_counts.max():,}")

    section("Capacity sweep — MoMTrans cascade vs production at each cap")
    print(f"\n  {'cap':>5} {'mt_n':>6} {'mt_$':>10} {'prod_n':>6} {'prod_$':>10} "
          f"{'lift_$':>10} {'lift_pct':>9}")
    print("  " + "-" * 64)
    rows = []
    for cap in DAILY_CAPS:
        mt = evaluate_capped(base_tier, cap)
        pp = evaluate_production_capped(prod, cap)
        lift = mt["pnl_total"] - pp["pnl_total"]
        lift_pct = (lift / pp["pnl_total"] * 100) if pp["pnl_total"] > 0 else 0.0
        cap_label = "∞" if cap >= 99999 else str(cap)
        print(f"  {cap_label:>5} {mt['n_total']:>6,} ${mt['pnl_total']:>+9,.2f} "
              f"{pp['n_total']:>6,} ${pp['pnl_total']:>+9,.2f} "
              f"${lift:>+9,.2f} {lift_pct:>+8.1f}%")
        rows.append({
            "daily_cap": cap, "cap_label": cap_label,
            "mt_n": mt["n_total"], "mt_pnl": mt["pnl_total"],
            "prod_n": pp["n_total"], "prod_pnl": pp["pnl_total"],
            "lift": lift, "lift_pct": lift_pct,
            "mt_per_tier": mt["per_tier"], "prod_per_tier": pp["per_tier"],
        })

    section("Verdict")
    cap10 = next(r for r in rows if r["daily_cap"] == 10)
    cap20 = next(r for r in rows if r["daily_cap"] == 20)
    print(f"  At cap=10/day:  MoMTrans=${cap10['mt_pnl']:+,.2f} vs prod=${cap10['prod_pnl']:+,.2f} -> "
          f"lift ${cap10['lift']:+,.2f}")
    print(f"  At cap=20/day:  MoMTrans=${cap20['mt_pnl']:+,.2f} vs prod=${cap20['prod_pnl']:+,.2f} -> "
          f"lift ${cap20['lift']:+,.2f}")

    if cap10["lift"] >= 5_000:
        verdict = "PASS — cascade beats production at production-realistic cap=10/day"
    elif cap10["lift"] >= 0:
        verdict = "MARGINAL — cascade beats production at cap=10 but lift < +$5k"
    else:
        verdict = "FAIL — cascade loses to production at cap=10/day (capacity bound)"
    print(f"  VERDICT: {verdict}")

    summary = {
        "thresholds": thresholds,
        "uncapped_total_picks": int(len(fired)),
        "daily_pick_distribution": {
            "mean": float(daily_counts.mean()),
            "p50": float(daily_counts.median()),
            "p75": float(daily_counts.quantile(0.75)),
            "p95": float(daily_counts.quantile(0.95)),
            "max": int(daily_counts.max()),
        },
        "capacity_sweep": rows,
        "verdict": verdict,
    }
    out = MODELS / "momtrans_v4_phase_b_capacity.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
