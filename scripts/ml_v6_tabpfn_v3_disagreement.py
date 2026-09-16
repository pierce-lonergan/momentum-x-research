"""EXPERIMENT #3 — TabPFN-vs-v3 disagreement analysis on existing OOS preds.

Per user critique: "What if TabPFN is right and v3 is wrong on the same
picks? When TabPFN and v3 disagree on tier assignment for the same
candidate, who is correct in OOS reality?"

This is the most underexploited thing in the data corpus right now.
No new training, no new inference — pure tabular analysis on
predictions we already have.

EXPECTED OUTCOMES:
  Case A: 'TabPFN trades, v3 skips' picks have POSITIVE mean ret_t5
    -> Free alpha source. Add the discriminating feature to v3 instead
       of deploying TabPFN. License/cost concerns disappear.

  Case B: 'TabPFN skips, v3 trades' picks have NEGATIVE mean ret_t5
    -> v3 takes some bad trades; defensive overlay = filter v3 picks
       by 'TabPFN agrees' before live trading.

  Case C: Both disagreement sets are roughly zero
    -> TabPFN and v3 are seeing the same signal differently; ensemble
       won't add much.

PRE-COMMITTED VERDICT:
  Case A trigger: file new feature engineering session BEFORE any
    TabPFN production deployment, to find the discriminating signal.

USAGE:
  python scripts/ml_v6_tabpfn_v3_disagreement.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def assign_tier_absolute(prob: pd.Series) -> pd.Series:
    """Production cascade thresholds (matches meta_scorer_inference.py)."""
    tier = pd.Series(["SKIP"] * len(prob), index=prob.index, dtype=object)
    tier.loc[prob >= 0.30] = "BROAD"
    tier.loc[prob >= 0.40] = "VETOED"
    tier.loc[prob >= 0.50] = "HIGH"
    tier.loc[prob >= 0.60] = "ELITE"
    return tier


def main() -> int:
    section("STEP 1 — load TabPFN OOS preds + v3 BROAD specialist OOS preds")
    pf = pd.read_parquet(DERIVED / "ml_v6_item5_tabpfn_preds.parquet")
    v3 = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    print(f"  TabPFN: {len(pf):,} rows, folds {sorted(pf['fold'].unique())}")
    print(f"  v3:     {len(v3):,} rows, folds {sorted(v3['fold'].unique())}")
    pf["d0"] = pd.to_datetime(pf["d0"])
    v3["d0"] = pd.to_datetime(v3["d0"])

    # The two pred sets use DIFFERENT fold schemes:
    #   TabPFN: 12 folds, 120d train / 15d test, span 2024-05-15 to 2026-04-24
    #   v3 BROAD specialist: 16 folds, original D281 cohort cascade scheme
    # We need rows where BOTH made a prediction. Inner-join on (ticker, d0).
    # The TabPFN preds have y_true; the v3 preds have y_reg. Both should
    # be the same realized ret_t5.
    pf_keyed = pf[["d0", "fold", "y_true", "y_pred"]].rename(
        columns={"y_pred": "tabpfn_pred", "fold": "pf_fold"})
    pf_keyed["ticker"] = None  # TabPFN preds didn't save ticker (oversight)
    # Fall back: aggregate by (d0, y_true) — ret_t5 values are unique enough
    # to identify rows. Better: re-load v3 base data and join.
    print()
    print("  TabPFN preds did not save 'ticker'. Re-deriving via index alignment.")
    # The TabPFN script saves '_idx' which is the row index in the v3 base data.
    # Use that for the join.
    pf_keyed = pf[["fold", "d0", "y_true", "y_pred", "_idx"]].rename(
        columns={"y_pred": "tabpfn_pred"})
    print(f"  TabPFN _idx range: {pf_keyed['_idx'].min()} - {pf_keyed['_idx'].max()}")

    # Re-load v3 base data to get tickers via the same index
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    # Use _idx to look up ticker from base df (not via merge to avoid d0 collision)
    pf_keyed["ticker"] = df["ticker"].iloc[pf_keyed["_idx"].values].values
    print(f"  TabPFN with ticker: {len(pf_keyed):,} rows")

    # Now join to v3 specialist preds on (ticker, d0)
    merged = pf_keyed.merge(
        v3[["ticker", "d0", "prob_specialist_BROAD", "y_reg"]],
        on=["ticker", "d0"], how="inner",
    )
    print(f"  Merged TabPFN + v3 BROAD specialist: {len(merged):,} rows "
          f"(TabPFN had {len(pf):,}, v3 had {len(v3):,})")

    # Sanity check: y_true (from TabPFN) should equal y_reg (from v3 specialist)
    diff_y = (merged["y_true"] - merged["y_reg"]).abs()
    print(f"  y_true vs y_reg max abs diff: {diff_y.max():.6f} (should be ~0)")

    section("STEP 2 — assign production-cascade tier per row")
    merged["v3_tier"] = assign_tier_absolute(merged["prob_specialist_BROAD"])

    # For TabPFN: it's a regression model. We need to map its predictions to
    # tiers. Two reasonable options:
    #   (a) per-day decile rank: top decile -> ELITE-equivalent
    #   (b) absolute thresholds via percentiles of TabPFN preds
    # The user's experiment #3 framing is about top-decile-per-day disagreement
    # vs cascade-trades disagreement. Use per-day decile ranks for TabPFN.
    def assign_tabpfn_tier(g: pd.DataFrame) -> pd.Series:
        n = len(g)
        if n < 4:
            return pd.Series(["SKIP"] * n, index=g.index)
        ranks = g["tabpfn_pred"].rank(ascending=False, method="first")
        tier = pd.Series(["SKIP"] * n, index=g.index, dtype=object)
        tier.loc[ranks <= n * 0.07] = "ELITE"
        tier.loc[(ranks > n * 0.07) & (ranks <= n * 0.19)] = "HIGH"
        tier.loc[(ranks > n * 0.19) & (ranks <= n * 0.37)] = "VETOED"
        tier.loc[(ranks > n * 0.37) & (ranks <= n * 0.62)] = "BROAD"
        return tier

    merged["tabpfn_tier"] = merged.groupby("d0", group_keys=False).apply(
        assign_tabpfn_tier, include_groups=False)

    # Also: a simpler binary "would TabPFN trade this?" flag — is this row in
    # TabPFN's top-quintile (~20%) per day, the size of the cascade union?
    def tabpfn_top_quintile(g: pd.DataFrame) -> pd.Series:
        n = len(g)
        if n < 4: return pd.Series([False] * n, index=g.index)
        threshold = g["tabpfn_pred"].quantile(0.80)
        return g["tabpfn_pred"] >= threshold
    merged["tabpfn_top_q"] = merged.groupby("d0", group_keys=False).apply(
        tabpfn_top_quintile, include_groups=False)
    merged["v3_trades"] = merged["v3_tier"] != "SKIP"

    section("STEP 3 — per-decision agreement matrix")
    print("  v3_trades = (v3 BROAD specialist puts pick in any tier >= BROAD threshold 0.30)")
    print("  tabpfn_top_q = (TabPFN's prediction is in top quintile per day)")
    print()
    cmat = pd.crosstab(merged["v3_trades"], merged["tabpfn_top_q"],
                        margins=True, margins_name="total")
    cmat.index = ["v3 SKIP", "v3 TRADE", "total"]
    cmat.columns = ["TabPFN below-q4", "TabPFN top-q5", "total"]
    print(cmat.to_string())

    section("STEP 4 — realized ret_t5 by disagreement quadrant")
    print("  The headline: do disagreement sets have actionable mean ret_t5?")
    print()
    print(f"  {'quadrant':<35} {'n':>6} {'mean ret_t5':>13} {'win %':>8}")
    print("  " + "-" * 67)
    quadrants = {
        "BOTH SKIP (v3 skip, TabPFN below-q4)":
            (~merged["v3_trades"]) & (~merged["tabpfn_top_q"]),
        "TabPFN trades, v3 SKIPS":
            (~merged["v3_trades"]) & (merged["tabpfn_top_q"]),
        "v3 trades, TabPFN below-q4":
            (merged["v3_trades"]) & (~merged["tabpfn_top_q"]),
        "BOTH TRADE (v3 trades, TabPFN top-q)":
            (merged["v3_trades"]) & (merged["tabpfn_top_q"]),
    }
    quadrant_results = {}
    for label, mask in quadrants.items():
        sub = merged[mask]
        n = len(sub)
        if n == 0:
            print(f"  {label:<35} {n:>6}  {'(empty)':>13}  {'--':>6}")
            continue
        mret = sub["y_reg"].mean()
        win = (sub["y_reg"] > 0).mean()
        quadrant_results[label] = {"n": n, "mean_ret": float(mret), "win_pct": float(win)}
        print(f"  {label:<35} {n:>6} {mret*100:>+11.2f}% {win*100:>6.1f}%")

    section("STEP 5 — focus: 'TabPFN trades, v3 SKIPS' is the headline question")
    pf_only = merged[(~merged["v3_trades"]) & (merged["tabpfn_top_q"])]
    if len(pf_only) > 0:
        # Compare to v3 trade set as a benchmark
        v3_trade_set = merged[merged["v3_trades"]]
        pf_only_mean = pf_only["y_reg"].mean()
        v3_trade_mean = v3_trade_set["y_reg"].mean()
        skipped_mean = merged[~merged["v3_trades"]]["y_reg"].mean()
        print(f"  'TabPFN trades, v3 SKIPS': n={len(pf_only):,}")
        print(f"    mean ret_t5: {pf_only_mean*100:+.2f}%")
        print(f"    win %: {(pf_only['y_reg']>0).mean()*100:.1f}%")
        print()
        print(f"  Benchmarks:")
        print(f"    All v3-trades:       n={len(v3_trade_set):,}  mean {v3_trade_mean*100:+.2f}%")
        print(f"    All v3-skip:         n={len(merged[~merged['v3_trades']]):,}  mean {skipped_mean*100:+.2f}%")
        print()
        # Tier breakdown of the disagreement
        pf_only_tiers = pf_only["tabpfn_tier"].value_counts()
        print("  TabPFN-tier breakdown of 'TabPFN trades, v3 SKIPS':")
        for t, c in pf_only_tiers.items():
            sub = pf_only[pf_only["tabpfn_tier"] == t]
            mean = sub["y_reg"].mean()
            print(f"    {t:<8} n={c:>4}  mean ret_t5 {mean*100:+6.2f}%")

        print()
        # Pre-commit verdict
        print("  PRE-COMMITTED RULE: if 'TabPFN trades, v3 SKIPS' has positive mean ret_t5,")
        print("                      file new feature-engineering session before TabPFN deployment.")
        if pf_only_mean > 0:
            print(f"  -> TRIGGERED: mean = {pf_only_mean*100:+.2f}% > 0%.")
            print(f"     There exists a structural feature TabPFN sees that v3 misses.")
            print(f"     File: 'identify discriminating feature(s)' as next session priority.")
        else:
            print(f"  -> NOT TRIGGERED: mean = {pf_only_mean*100:+.2f}% <= 0%.")
            print(f"     TabPFN's 'extra' picks lose money on average.")

    section("STEP 6 — the inverse: 'v3 trades, TabPFN below-q4' (defensive overlay)")
    v3_only = merged[(merged["v3_trades"]) & (~merged["tabpfn_top_q"])]
    v3_with_pfn_mean = delta = None  # pre-bound: assigned conditionally below
    if len(v3_only) > 0:
        v3_only_mean = v3_only["y_reg"].mean()
        v3_with_pfn = merged[(merged["v3_trades"]) & (merged["tabpfn_top_q"])]
        v3_with_pfn_mean = v3_with_pfn["y_reg"].mean() if len(v3_with_pfn) else float("nan")
        print(f"  'v3 trades, TabPFN below-q4': n={len(v3_only):,}")
        print(f"    mean ret_t5: {v3_only_mean*100:+.2f}%")
        print(f"  'v3 trades, TabPFN agrees': n={len(v3_with_pfn):,}")
        print(f"    mean ret_t5: {v3_with_pfn_mean*100:+.2f}%")
        print()
        delta = v3_with_pfn_mean - v3_only_mean
        print(f"  Lift from filtering v3 picks by 'TabPFN agrees': {delta*100:+.2f}% per pick")
        print()
        print("  Defensive-overlay candidate: skip v3 picks where TabPFN disagrees.")
        if delta > 0.02:
            print(f"  -> {delta*100:+.2f}% lift is MEANINGFUL. Defensive filter recommended.")
        elif delta > 0:
            print(f"  -> {delta*100:+.2f}% lift is marginal.")
        else:
            print(f"  -> Filter would HURT. Don't deploy.")

    section("STEP 7 — persist + summary")
    out = {
        "n_merged": int(len(merged)),
        "confusion_matrix": cmat.to_dict(),
        "quadrant_results": quadrant_results,
        "tabpfn_only_picks": {
            "n": int(len(pf_only)),
            "mean_ret": float(pf_only["y_reg"].mean()) if len(pf_only) else None,
            "win_pct": float((pf_only["y_reg"] > 0).mean()) if len(pf_only) else None,
        },
        "v3_only_picks": {
            "n": int(len(v3_only)),
            "mean_ret": float(v3_only["y_reg"].mean()) if len(v3_only) else None,
            "v3_with_tabpfn_agree_mean": float(v3_with_pfn_mean) if 'v3_with_pfn_mean' in locals() else None,
            "defensive_lift": float(delta) if 'delta' in locals() else None,
        },
    }
    out_path = MODELS / "v6_tabpfn_v3_disagreement.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
