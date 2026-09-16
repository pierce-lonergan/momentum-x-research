"""v6 — Bouchaud square-root impact / capacity analysis for v3 production picks.

Per M.md §10B (the most under-emphasized item in the prior architecture
report) and the user's doc 141 critique:

  "Capacity modeling (Bouchaud impact at $5M AUM), execution quality,
  sizing methodology, drawdown gating — those become the highest-
  leverage moves, not features or architectures."

This is the highest-confidence production work right now per doc 141's
strategic frame: doesn't depend on any of the architecture results,
purely measures how much of v3's apparent edge is being eaten by
market impact at various AUM scales.

Bouchaud square-root impact law (Toth-Eisler-Bouchaud PRX 2011;
Maitrier-Loeper-Kanazawa-Bouchaud 2025 "double square-root"):

  Δp / σ_daily ≈ Y · sign(Q) · √(|Q| / V_daily)

where:
  Δp        = price change in our direction (move against us = cost)
  σ_daily   = realized daily volatility (we approximate via intraday_pct/4)
  Q         = signed position size in dollars
  V_daily   = daily dollar volume (dvol_d0 column)
  Y         = impact coefficient, ~0.5-1 for large caps, 1-2× larger for microcaps

Round-trip impact (entry + exit) ≈ 2 × one-way impact.

Net return per trade = ret_t5 − 2 × Y × √(participation) × σ_daily

USAGE:
  python scripts/ml_v6_capacity_bouchaud.py
  python scripts/ml_v6_capacity_bouchaud.py --y-coef 2.0 --aum-list 100000,1000000,5000000,10000000
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# Production cascade tier sizing (matches lottery_paper_trade.ps1 +
# Aggressive Kelly defaults from main.py)
# ──────────────────────────────────────────────────────────────────────


# Aggressive Kelly caps per tier (% of bankroll per pick)
TIER_KELLY_CAP = {
    "ELITE":  0.50,  # 50% of bankroll on a single ELITE pick
    "HIGH":   0.35,
    "VETOED": 0.20,
    "BROAD":  0.10,
}

# Tier classifier probabilities from v3 BROAD specialist (we use it as a
# proxy for v3-tuned-16f's tier assignment behavior — same training target,
# same WF folds)
def assign_tier_per_day(g: pd.DataFrame, prob_col: str) -> pd.Series:
    """Per-day decile-rank tier assignment, matching doc 138 § ITEM 3."""
    n = len(g)
    if n < 4:
        return pd.Series(["SKIP"] * n, index=g.index)
    ranks = g[prob_col].rank(ascending=False, method="first")
    tier = pd.Series(["SKIP"] * n, index=g.index, dtype=object)
    tier.loc[ranks <= n * 0.07] = "ELITE"
    tier.loc[(ranks > n * 0.07) & (ranks <= n * 0.19)] = "HIGH"
    tier.loc[(ranks > n * 0.19) & (ranks <= n * 0.37)] = "VETOED"
    tier.loc[(ranks > n * 0.37) & (ranks <= n * 0.62)] = "BROAD"
    return tier


def assign_tier_absolute_threshold(prob_series: pd.Series) -> pd.Series:
    """Production-cascade absolute thresholds (mirrors meta_scorer_inference.py
    `assign_tier()` minus the magnetization gate which we don't have here).
      ELITE  if prob >= 0.60
      HIGH   if prob >= 0.50
      VETOED if prob >= 0.40
      BROAD  if prob >= 0.30
      SKIP   below 0.30
    Production thresholds are higher than these (incl. mag gate),
    but this is the closest in-isolation proxy.
    """
    tier = pd.Series(["SKIP"] * len(prob_series), index=prob_series.index, dtype=object)
    tier.loc[prob_series >= 0.30] = "BROAD"
    tier.loc[prob_series >= 0.40] = "VETOED"
    tier.loc[prob_series >= 0.50] = "HIGH"
    tier.loc[prob_series >= 0.60] = "ELITE"
    return tier


# ──────────────────────────────────────────────────────────────────────
# Bouchaud impact computation
# ──────────────────────────────────────────────────────────────────────


def bouchaud_impact(position_dollars: np.ndarray, dvol_dollars: np.ndarray,
                    sigma_daily: np.ndarray, y_coef: float = 1.5) -> np.ndarray:
    """One-way price impact in return units (fraction of price).

    Δp / σ_daily = Y · sign(Q) · √(|Q| / V_daily)
    -> Δp / p ≈ (Y · √(|Q|/V) · σ_daily) for unit price normalization

    We assume position_dollars > 0 (long-only); sign just gives direction
    of impact (against us when buying, against us when selling).
    """
    participation = np.clip(position_dollars / dvol_dollars.clip(min=1), 0, 1.0)
    # one-way impact in units of σ_daily, then × σ_daily to get fractional return
    impact_in_sigma = y_coef * np.sqrt(participation)
    impact_fractional = impact_in_sigma * sigma_daily
    return impact_fractional


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--y-coef", type=float, default=1.5,
                    help="Bouchaud Y coefficient (1.0 large-cap, 1.5-2.0 microcap)")
    ap.add_argument("--aum-list", type=str,
                    default="100000,500000,1000000,5000000,10000000",
                    help="comma-separated list of AUM levels in $ to analyze")
    args = ap.parse_args()
    aum_list = [float(x) for x in args.aum_list.split(",")]

    section("STEP 1 — load v3 BROAD specialist OOS preds + base data")
    # v3 BROAD specialist binary classifier predictions (proxy for prod cascade tier scoring)
    preds = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    preds["d0"] = pd.to_datetime(preds["d0"])
    print(f"  v3 BROAD specialist OOS preds: {len(preds):,} rows, "
          f"{preds['d0'].nunique()} unique days, {preds['fold'].nunique()} folds")

    # Join to base data for dvol_d0, intraday_pct, ret_t5
    from ml_continuer_v2_ensemble import load_data
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    merged = preds.merge(
        df[["ticker", "d0", "dvol_d0", "intraday_pct", "open"]],
        on=["ticker", "d0"], how="inner",
    )
    print(f"  merged: {len(merged):,} rows after join")
    # Clean
    merged["dvol_d0"] = merged["dvol_d0"].clip(lower=1.0)  # min $1 to avoid div-by-0
    merged["intraday_pct"] = merged["intraday_pct"].clip(lower=0.001, upper=2.0)
    # Daily realized vol approx: range / 4 (daily range ~ 4× daily std for Normal)
    merged["sigma_daily"] = merged["intraday_pct"] / 4.0
    # ret_t5 already in [-0.5, +1.0] from training pipeline, no further clip
    print(f"  dvol_d0 quartiles: ${merged['dvol_d0'].quantile(0.25)/1e6:.2f}M / "
          f"${merged['dvol_d0'].quantile(0.50)/1e6:.2f}M / ${merged['dvol_d0'].quantile(0.75)/1e6:.2f}M")
    print(f"  intraday_pct quartiles: {merged['intraday_pct'].quantile(0.25)*100:.1f}% / "
          f"{merged['intraday_pct'].quantile(0.50)*100:.1f}% / {merged['intraday_pct'].quantile(0.75)*100:.1f}%")

    section("STEP 2 — assign tier via PRODUCTION CASCADE THRESHOLDS")
    print("  ELITE prob>=0.60, HIGH 0.50, VETOED 0.40, BROAD 0.30, else SKIP")
    print("  (matches meta_scorer_inference.py:assign_tier() minus the mag gate)")
    merged["tier"] = assign_tier_absolute_threshold(merged["prob_specialist_BROAD"])
    tier_counts = merged["tier"].value_counts().reindex(
        ["ELITE", "HIGH", "VETOED", "BROAD", "SKIP"], fill_value=0)
    print("  per-tier candidate counts:")
    for t, c in tier_counts.items():
        print(f"    {t:<8} {c:>6,}  ({c/len(merged)*100:>5.1f}%)")
    # Mean raw return per tier as a sanity-check
    print("  per-tier mean raw ret_t5:")
    for t in ("ELITE", "HIGH", "VETOED", "BROAD", "SKIP"):
        sub = merged[merged["tier"] == t]
        if len(sub):
            print(f"    {t:<8} mean={sub['y_reg'].mean()*100:+6.2f}%  std={sub['y_reg'].std()*100:5.2f}%  "
                  f"win%={(sub['y_reg']>0).mean()*100:5.1f}%")

    section(f"STEP 3 — capacity analysis at {len(aum_list)} AUM levels (Y={args.y_coef})")
    print(f"  Tier sizing (% of bankroll per pick): {TIER_KELLY_CAP}")
    print()
    print(f"{'AUM':>12} {'tier':<8} {'n_picks':>8} {'pos_$':>12} {'avg_part':>10} "
          f"{'mean_raw':>9} {'mean_impact':>11} {'mean_net':>9}  {'Sharpe_net':>11}")
    print("-" * 105)
    results = []
    for aum in aum_list:
        for tier, kelly_cap in TIER_KELLY_CAP.items():
            sub = merged[merged["tier"] == tier].copy()
            if len(sub) == 0:
                continue
            position = aum * kelly_cap
            sub["participation"] = (position / sub["dvol_d0"]).clip(0, 1.0)
            sub["impact_oneway"] = args.y_coef * np.sqrt(sub["participation"]) * sub["sigma_daily"]
            sub["impact_roundtrip"] = 2 * sub["impact_oneway"]
            sub["raw_ret"] = sub["y_reg"]
            sub["net_ret"] = sub["raw_ret"] - sub["impact_roundtrip"]
            n = len(sub)
            avg_part = sub["participation"].mean()
            mean_raw = sub["raw_ret"].mean()
            mean_imp = sub["impact_roundtrip"].mean()
            mean_net = sub["net_ret"].mean()
            std_net = sub["net_ret"].std()
            sharpe_net = mean_net / std_net * np.sqrt(252) if std_net > 0 else 0
            results.append({
                "aum": aum, "tier": tier, "n_picks": n, "position_dollars": position,
                "avg_participation": float(avg_part), "mean_raw_ret": float(mean_raw),
                "mean_impact_roundtrip": float(mean_imp), "mean_net_ret": float(mean_net),
                "sharpe_net_annualized": float(sharpe_net),
            })
            print(f"  ${aum/1e6:>9.2f}M {tier:<8} {n:>8,} ${position/1e3:>10.0f}K "
                  f"{avg_part*100:>9.1f}% {mean_raw*100:>+8.2f}% "
                  f"{mean_imp*100:>10.2f}% {mean_net*100:>+8.2f}% {sharpe_net:>+10.2f}")
        print()

    section("STEP 4 — capacity ceiling per tier (where mean_net flips negative)")
    res_df = pd.DataFrame(results)
    print("  Capacity ceiling = highest AUM where mean_net_ret stays POSITIVE per tier:")
    print(f"{'tier':<8} {'last_AUM_with_net>0':>22}  {'first_AUM_net<0':>20}")
    for tier in TIER_KELLY_CAP:
        sub = res_df[res_df["tier"] == tier].sort_values("aum")
        positive = sub[sub["mean_net_ret"] > 0]
        negative = sub[sub["mean_net_ret"] <= 0]
        last_pos = positive["aum"].max() if len(positive) else None
        first_neg = negative["aum"].min() if len(negative) else None
        last_str = f"${last_pos/1e6:.2f}M" if last_pos else "ALL fail"
        first_str = f"${first_neg/1e6:.2f}M" if first_neg else "(none)"
        print(f"  {tier:<8} {last_str:>22}  {first_str:>20}")

    out = {
        "y_coef": args.y_coef,
        "aum_levels": aum_list,
        "tier_kelly_caps": TIER_KELLY_CAP,
        "per_tier_capacity_results": results,
    }
    out_path = MODELS / "v6_capacity_bouchaud.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
