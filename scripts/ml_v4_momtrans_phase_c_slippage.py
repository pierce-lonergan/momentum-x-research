"""Phase C: slippage-adjusted backtest of the MoMTrans tier cascade.

Phases A + B both passed: the cascade beats production OOS and at
production-realistic capacity (cap=10/day, +$71k lift). Phase C is
the final research gate before considering an env-gated production
path: does the lift survive realistic microcap slippage?

Slippage model: flat per-pick haircut on REALIZED return, applied
identically to MoMTrans cascade and to the production v3 baseline.
The model captures market impact + bid/ask spread cost. For microcaps
in the $1.50-$20 price band with $250-$2000 notional positions:
  - Conservative (paper-trading-realistic): 25-50 bps
  - Realistic (live retail with PFOF):       50-100 bps
  - Pessimistic (live, contesting alpha):    100-200 bps

This script sweeps the haircut to characterize how much slippage the
cascade can absorb before its lift over production collapses.
Specifically, the BREAK-EVEN haircut is the slippage at which lift
hits exactly $0 — any market-impact estimate below that means the
cascade still wins.

Same Phase-A frozen thresholds + Phase-B cap=10/day setup as before.
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
DAILY_CAP = 10  # production-realistic; from Phase B
SLIPPAGE_GRID_BPS = [0, 25, 50, 75, 100, 150, 200, 300, 500]


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly(p, w, ew, el, cap):
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    return float(min(f_star * float(np.exp(-2 * w)), cap))


def assign_momtrans_tier(base: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    out = base.copy()
    is_hi_mid = out["mag_label"].isin(["HI", "MID"])
    is_mid = out["mag_label"] == "MID"
    sp = {tier: out[f"prob_specialist_{tier}"] for tier in
          ("ELITE", "HIGH", "VETOED", "BROAD")}
    out["tier"] = "SKIP"
    out["tier_prob"] = 0.0
    elite = (sp["ELITE"] >= thresholds["ELITE"]) & is_hi_mid
    out.loc[elite, "tier"] = "ELITE"
    out.loc[elite, "tier_prob"] = sp["ELITE"][elite]
    high = (sp["HIGH"] >= thresholds["HIGH"]) & is_hi_mid & ~elite
    out.loc[high, "tier"] = "HIGH"
    out.loc[high, "tier_prob"] = sp["HIGH"][high]
    vetoed = (sp["VETOED"] >= thresholds["VETOED"]) & is_mid & ~elite & ~high
    out.loc[vetoed, "tier"] = "VETOED"
    out.loc[vetoed, "tier_prob"] = sp["VETOED"][vetoed]
    broad = (sp["BROAD"] >= thresholds["BROAD"]) & is_hi_mid & ~elite & ~high & ~vetoed
    out.loc[broad, "tier"] = "BROAD"
    out.loc[broad, "tier_prob"] = sp["BROAD"][broad]
    return out


def assign_prod_tier(prod: pd.DataFrame) -> pd.DataFrame:
    p = prod.copy()
    is_hi_mid = p["mag_label"].isin(["HI", "MID"])
    is_mid = p["mag_label"] == "MID"
    p["tier"] = "SKIP"
    p["tier_prob"] = 0.0
    elite = (p["prob_continuer"] >= 0.60) & is_hi_mid
    high = (p["prob_continuer"] >= 0.50) & (p["prob_continuer"] < 0.60) & is_hi_mid
    vetoed = (p["prob_continuer"] >= 0.30) & (p["prob_continuer"] < 0.50) & is_mid
    broad = (p["prob_continuer"] >= 0.30) & (p["prob_continuer"] < 0.50) & is_hi_mid & ~vetoed
    p.loc[elite, "tier"] = "ELITE"; p.loc[high, "tier"] = "HIGH"
    p.loc[vetoed, "tier"] = "VETOED"; p.loc[broad, "tier"] = "BROAD"
    p.loc[elite, "tier_prob"] = p.loc[elite, "prob_continuer"].values
    p.loc[high, "tier_prob"] = p.loc[high, "prob_continuer"].values
    p.loc[vetoed, "tier_prob"] = p.loc[vetoed, "prob_continuer"].values
    p.loc[broad, "tier_prob"] = p.loc[broad, "prob_continuer"].values
    return p


def cap_per_day(df: pd.DataFrame, cap: int) -> pd.DataFrame:
    fired = df[df["tier"] != "SKIP"].copy()
    if fired.empty:
        return fired
    fired["tier_priority"] = fired["tier"].map(TIER_ORDER)
    fired = fired.sort_values(["d0", "tier_priority", "tier_prob"],
                                  ascending=[True, True, False])
    fired["rank_in_day"] = fired.groupby("d0").cumcount()
    return fired[fired["rank_in_day"] < cap].reset_index(drop=True)


def evaluate_with_slippage(picks: pd.DataFrame, slippage_bps: float,
                              prob_floor: float = 0.31) -> dict:
    """Compute Aggressive-Kelly $-PNL with a slippage haircut on every pick.
    Slippage: realized_ret -= slippage_bps / 10000 (round-trip cost)."""
    if picks.empty:
        return {"n": 0, "pnl_total": 0.0, "per_tier": {}}
    haircut = slippage_bps / 10_000.0
    out = {"per_tier": {}, "n": int(len(picks))}
    total = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        sub = picks[picks["tier"] == tier]
        if sub.empty:
            out["per_tier"][tier] = {"n": 0, "pnl": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        prob = np.maximum(sub["tier_prob"].values, prob_floor)
        widths = sub["conformal_width"].values
        # Apply slippage haircut to realized return (net of round-trip costs)
        yreg = sub["y_reg"].clip(-0.5, 1.0).values - haircut
        kellies = [kelly(pp, w, ew, el, cap) for pp, w in zip(prob, widths)]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg)))
        total += pnl
        out["per_tier"][tier] = {"n": int(len(sub)), "pnl": pnl}
    out["pnl_total"] = total
    return out


def main() -> int:
    section("Phase C — slippage-adjusted backtest")

    # Load data (mirrors Phase B loader)
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
    prod["conformal_width"] = prod.get("conformal_width", 0.5)
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

    # Phase A frozen thresholds + Phase B cap=10/day
    PA = json.loads((MODELS / "momtrans_v4_phase_a_oos_validation.json").read_text())
    thresholds = PA["best_thresholds"]
    print(f"  Frozen Phase-A thresholds: {thresholds}")
    print(f"  Daily cap: {DAILY_CAP} (Phase-B production-realistic)")

    base_tier = assign_momtrans_tier(base, thresholds)
    prod_tier = assign_prod_tier(prod)
    mt_picks = cap_per_day(base_tier, DAILY_CAP)
    prod_picks = cap_per_day(prod_tier, DAILY_CAP)
    print(f"\n  MoMTrans picks (capped):  {len(mt_picks):,}")
    print(f"  Production picks (capped): {len(prod_picks):,}")

    section(f"Slippage sweep — both strategies pay the same haircut")
    print(f"\n  {'bps':>5} {'mt_$':>11} {'prod_$':>11} {'lift_$':>11} "
          f"{'mt_$/pick':>10} {'prod_$/pick':>11}")
    print("  " + "-" * 70)
    rows = []
    for bps in SLIPPAGE_GRID_BPS:
        mt = evaluate_with_slippage(mt_picks, bps)
        pp = evaluate_with_slippage(prod_picks, bps)
        lift = mt["pnl_total"] - pp["pnl_total"]
        mt_per = mt["pnl_total"] / max(mt["n"], 1)
        pp_per = pp["pnl_total"] / max(pp["n"], 1)
        print(f"  {bps:>5} ${mt['pnl_total']:>+10,.2f} ${pp['pnl_total']:>+10,.2f} "
              f"${lift:>+10,.2f} ${mt_per:>+9.2f} ${pp_per:>+10.2f}")
        rows.append({
            "slippage_bps": bps, "mt_pnl": mt["pnl_total"],
            "prod_pnl": pp["pnl_total"], "lift": lift,
            "mt_per_pick": mt_per, "prod_per_pick": pp_per,
        })

    section("Break-even slippage analysis")
    # Find the bps at which lift crosses zero (linear interpolation)
    lifts = [r["lift"] for r in rows]
    bps_vals = [r["slippage_bps"] for r in rows]
    cross = None
    for i in range(len(rows) - 1):
        if lifts[i] >= 0 and lifts[i+1] < 0:
            # Linear interp
            x0, y0 = bps_vals[i], lifts[i]
            x1, y1 = bps_vals[i+1], lifts[i+1]
            cross = x0 + (0 - y0) * (x1 - x0) / (y1 - y0)
            break
    if cross is None:
        if all(l > 0 for l in lifts):
            cross = float("inf")
            print(f"  Cascade lift POSITIVE at all tested slippage levels (max {max(bps_vals)} bps)")
        else:
            cross = 0.0
            print(f"  Cascade lift NEGATIVE even at 0 bps slippage")
    else:
        print(f"  BREAK-EVEN slippage: ~{cross:.0f} bps")
        print(f"  Microcap-realistic estimate: 50-100 bps")
        print(f"  Headroom vs realistic: {cross - 75:.0f} bps")

    # Verdict
    pp_at_50 = next(r for r in rows if r["slippage_bps"] == 50)
    pp_at_100 = next(r for r in rows if r["slippage_bps"] == 100)
    section("Verdict")
    print(f"  At 50 bps slippage:  lift = ${pp_at_50['lift']:+,.2f}")
    print(f"  At 100 bps slippage: lift = ${pp_at_100['lift']:+,.2f}")
    print(f"  At 200 bps slippage: lift = ${next(r for r in rows if r['slippage_bps']==200)['lift']:+,.2f}")
    if pp_at_100["lift"] >= 5_000:
        verdict = "PASS — cascade beats production even at 100 bps slippage"
    elif pp_at_50["lift"] >= 5_000:
        verdict = "MARGINAL — cascade survives 50 bps but breaks down by 100 bps"
    elif pp_at_50["lift"] >= 0:
        verdict = "WEAK — cascade barely positive at 50 bps; needs lower-slippage execution"
    else:
        verdict = "FAIL — cascade alpha eroded by slippage"
    print(f"  VERDICT: {verdict}")

    summary = {
        "thresholds": thresholds, "daily_cap": DAILY_CAP,
        "n_mt_picks": int(len(mt_picks)),
        "n_prod_picks": int(len(prod_picks)),
        "slippage_sweep": rows,
        "break_even_bps": cross,
        "verdict": verdict,
    }
    out = MODELS / "momtrans_v4_phase_c_slippage.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
