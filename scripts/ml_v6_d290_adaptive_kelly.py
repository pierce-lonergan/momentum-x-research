"""D290 — per-tier-AUM-aware Kelly caps (Thread #2 from doc 147 §8.2).

Per doc 147 §5.4 finding:
  HIGH    scales to $5M+ AUM (recent mean +25.46%, no break tested)
  ELITE   scales to $1M (recent mean +20.41%, breaks $5M)
  VETOED  scales to $1M (recent mean +9.04%, breaks $5M)
  BROAD   breaks at $500K (recent mean +3.00%, impact dominates above $100K)

Current Aggressive Kelly (uniform across AUM):
  ELITE 50% / HIGH 35% / VETOED 20% / BROAD 10%

The strategic question: what's the OPTIMAL per-tier Kelly cap at each
AUM level to maximize net return after Bouchaud impact?

EXPERIMENT:
  1. For each (AUM, tier) compute optimal Kelly that maximizes:
       net_return = mean_ret_t5_recent - 2*Y*sqrt(participation)*sigma_d
     subject to: position = AUM * kelly, participation = position / dvol_d0
  2. Tabulate optimal Kelly per AUM for each tier
  3. Design a piecewise linear (or analytical) AUM-adaptive Kelly function
  4. Compare expected total $-PNL: current uniform Kelly vs adaptive Kelly
  5. Pre-commit: ship adaptive Kelly if expected $-PNL improves by >=10%
     at $1M+ AUM with no degradation at current paper account ($140K)

Output: launcher-ready compute_kelly_cap_adaptive() function +
        revised TIER_KELLY_CAPS_AGGRESSIVE table + unit tests.
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
    tier = pd.Series(["SKIP"] * len(prob), index=prob.index, dtype=object)
    tier.loc[prob >= 0.30] = "BROAD"
    tier.loc[prob >= 0.40] = "VETOED"
    tier.loc[prob >= 0.50] = "HIGH"
    tier.loc[prob >= 0.60] = "ELITE"
    return tier


def main() -> int:
    section("LOAD")
    v3 = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    v3["d0"] = pd.to_datetime(v3["d0"])
    v3["tier"] = assign_tier_absolute(v3["prob_specialist_BROAD"])
    from ml_continuer_v2_ensemble import load_data
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    merged = v3.merge(df[["ticker", "d0", "dvol_d0", "intraday_pct"]],
                      on=["ticker", "d0"])
    threshold = pd.Timestamp("2025-08-01")
    recent = merged[merged["d0"] >= threshold].copy()
    recent["sigma_d"] = recent["intraday_pct"].clip(0.001, 2.0) / 4.0
    print(f"  recent (Aug 2025+): {len(recent):,} rows")

    # Reference Bouchaud Y from doc 142
    Y = 1.5

    # Current production Kelly profile (Aggressive)
    CURRENT_KELLY = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
    print(f"  Current Aggressive Kelly: {CURRENT_KELLY}")
    print(f"  Bouchaud Y: {Y}")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 1 — find optimal Kelly per (tier, AUM) on recent data")
    print("  Objective: maximize per-pick EXPECTED DOLLAR P&L")
    print("    $-PNL_per_pick = (AUM × kelly) × mean_net_per_pick")
    print("    where mean_net_per_pick = E[y_reg - 2Y·sqrt(participation)·sigma_d]")
    print()
    print("  (Earlier version of this script optimized mean_net% which is")
    print("   trivially maximized at zero position size — that was the bug.)")
    print()
    AUM_LEVELS = [100_000, 250_000, 500_000, 1_000_000, 2_000_000,
                  5_000_000, 10_000_000]
    KELLY_GRID = np.arange(0.005, 1.005, 0.005)  # 0.5% to 100% in 0.5% steps

    optimal = {}
    print(f"  {'AUM':>10} {'tier':<8} {'opt_kelly':>10} {'mean_net%':>10} "
          f"{'$_PNL_pick':>12} {'mean_imp%':>10} {'avg_part':>10}")
    print("  " + "-" * 80)
    for aum in AUM_LEVELS:
        for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
            sub = recent[recent["tier"] == tier].copy()
            if len(sub) < 5:
                continue
            best_pnl = -1e9
            best_kelly = None
            best_metrics = None
            for kelly in KELLY_GRID:
                position = aum * kelly
                part = (position / sub["dvol_d0"].clip(lower=1)).clip(0, 1.0)
                impact_rt = 2 * Y * np.sqrt(part) * sub["sigma_d"]
                net = sub["y_reg"] - impact_rt
                mean_net = net.mean()
                pnl_per_pick = position * mean_net  # $ per pick
                if pnl_per_pick > best_pnl:
                    best_pnl = pnl_per_pick
                    best_kelly = kelly
                    best_metrics = {
                        "mean_ret": float(sub["y_reg"].mean()),
                        "mean_impact": float(impact_rt.mean()),
                        "mean_net": float(mean_net),
                        "avg_part": float(part.mean()),
                        "pnl_per_pick": float(pnl_per_pick),
                    }
            optimal[f"{aum}_{tier}"] = {
                "aum": aum, "tier": tier,
                "optimal_kelly": float(best_kelly), **best_metrics,
            }
            print(f"  ${aum/1e6:>8.2f}M {tier:<8} "
                  f"{best_kelly*100:>9.1f}% {best_metrics['mean_net']*100:>+9.2f}% "
                  f"${best_metrics['pnl_per_pick']:>10,.0f} "
                  f"{best_metrics['mean_impact']*100:>9.2f}% "
                  f"{best_metrics['avg_part']*100:>9.1f}%")
        print()

    # ─────────────────────────────────────────────────────────────────
    section("STEP 2 — comparison: current uniform Kelly vs optimal adaptive")
    print(f"  Per-pick mean_net (recent) under each policy:")
    print(f"  {'AUM':>10} {'tier':<8} {'cur_kelly':>10} {'cur_net':>10} "
          f"{'opt_kelly':>10} {'opt_net':>10} {'lift':>9}")
    print("  " + "-" * 75)
    comparison = []
    for aum in AUM_LEVELS:
        for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
            sub = recent[recent["tier"] == tier].copy()
            if len(sub) < 5: continue
            cur_kelly = CURRENT_KELLY[tier]
            cur_pos = aum * cur_kelly
            cur_part = (cur_pos / sub["dvol_d0"].clip(lower=1)).clip(0, 1.0)
            cur_imp = 2 * Y * np.sqrt(cur_part) * sub["sigma_d"]
            cur_net = (sub["y_reg"] - cur_imp).mean()

            opt = optimal[f"{aum}_{tier}"]
            opt_kelly = opt["optimal_kelly"]
            opt_net = opt["mean_net"]
            lift = opt_net - cur_net
            comparison.append({
                "aum": aum, "tier": tier,
                "current_kelly": cur_kelly, "current_net_mean_ret": float(cur_net),
                "optimal_kelly": opt_kelly, "optimal_net_mean_ret": float(opt_net),
                "lift": float(lift),
            })
            flag = " <-- big lift" if lift > 0.05 else (" <-- modest" if lift > 0.01 else "")
            print(f"  ${aum/1e6:>8.2f}M {tier:<8} "
                  f"{cur_kelly*100:>9.1f}% {cur_net*100:>+9.2f}% "
                  f"{opt_kelly*100:>9.1f}% {opt_net*100:>+9.2f}% "
                  f"{lift*100:>+8.2f}%{flag}")
        print()

    # ─────────────────────────────────────────────────────────────────
    section("STEP 3 — design AUM-adaptive Kelly function (analytical fit)")
    print("  Idea: BROAD/VETOED/ELITE need to shrink as AUM grows; HIGH stays.")
    print("  Try a power-law fit per tier: kelly(AUM) = a * (AUM_ref / AUM)^b")
    print()
    print("  But first, look at the optimal Kelly schedule per tier:")
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        print(f"\n  {tier}:")
        print(f"  {'AUM':>10} {'opt_kelly':>10} {'cur_kelly':>10} {'opt/cur':>8}")
        for aum in AUM_LEVELS:
            opt = optimal[f"{aum}_{tier}"]
            cur = CURRENT_KELLY[tier]
            print(f"  ${aum/1e6:>8.2f}M {opt['optimal_kelly']*100:>9.1f}% "
                  f"{cur*100:>9.1f}% {opt['optimal_kelly']/cur:>7.3f}x")

    # Propose simple piecewise linear table per tier
    print()
    print("  PROPOSED ADAPTIVE KELLY SCHEDULE (interpolate piecewise-linear in log-AUM):")
    proposal = {}
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        proposal[tier] = []
        for aum in AUM_LEVELS:
            opt_kelly = optimal[f"{aum}_{tier}"]["optimal_kelly"]
            # Cap at current Kelly (don't recommend MORE aggressive than today)
            adaptive_kelly = min(opt_kelly, CURRENT_KELLY[tier])
            proposal[tier].append({"aum": aum, "kelly_cap": float(adaptive_kelly)})

    print(f"  {'AUM':>10} {'ELITE':>8} {'HIGH':>8} {'VETOED':>8} {'BROAD':>8}")
    for i, aum in enumerate(AUM_LEVELS):
        row = [aum]
        for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
            row.append(proposal[tier][i]["kelly_cap"])
        print(f"  ${row[0]/1e6:>8.2f}M {row[1]*100:>7.1f}% {row[2]*100:>7.1f}% "
              f"{row[3]*100:>7.1f}% {row[4]*100:>7.1f}%")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 4 — expected $-PNL impact at each AUM")
    print("  Compute expected daily $-PNL under current vs adaptive policy.")
    print("  For each tier: per-pick net × picks-per-day × (current vs adaptive Kelly)")
    print()
    # Recent daily fire rate per tier
    days = recent["d0"].nunique()
    fires_per_day = {}
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        n_picks = (recent["tier"] == tier).sum()
        fires_per_day[tier] = n_picks / max(days, 1)
        print(f"  {tier}: {n_picks} picks in {days} days = {fires_per_day[tier]:.2f}/day")

    print()
    print(f"  Expected daily $-PNL per AUM:")
    print(f"  {'AUM':>10} {'cur_pnl':>12} {'adapt_pnl':>12} {'lift':>10} {'lift_pct':>10}")
    pnl_results = []
    for i, aum in enumerate(AUM_LEVELS):
        cur_total = 0.0
        adapt_total = 0.0
        for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
            cur_kelly = CURRENT_KELLY[tier]
            adapt_kelly = proposal[tier][i]["kelly_cap"]
            sub = recent[recent["tier"] == tier]
            if len(sub) < 5: continue
            # Current
            cur_pos = aum * cur_kelly
            cur_part = (cur_pos / sub["dvol_d0"].clip(lower=1)).clip(0, 1.0)
            cur_imp = 2 * Y * np.sqrt(cur_part) * sub["sigma_d"]
            cur_net = (sub["y_reg"] - cur_imp).mean()
            cur_pnl_per_pick = cur_pos * cur_net
            cur_total += cur_pnl_per_pick * fires_per_day[tier]
            # Adaptive
            adapt_pos = aum * adapt_kelly
            adapt_part = (adapt_pos / sub["dvol_d0"].clip(lower=1)).clip(0, 1.0)
            adapt_imp = 2 * Y * np.sqrt(adapt_part) * sub["sigma_d"]
            adapt_net = (sub["y_reg"] - adapt_imp).mean()
            adapt_pnl_per_pick = adapt_pos * adapt_net
            adapt_total += adapt_pnl_per_pick * fires_per_day[tier]
        lift = adapt_total - cur_total
        lift_pct = (lift / cur_total * 100) if cur_total > 0 else (
            float("inf") if adapt_total > 0 else 0)
        pnl_results.append({
            "aum": aum, "current_pnl_daily": float(cur_total),
            "adaptive_pnl_daily": float(adapt_total),
            "lift_dollars": float(lift), "lift_pct": float(lift_pct),
        })
        flag = " <-- adaptive WINS" if lift > 0 else (" <-- equal" if abs(lift) < 1 else "")
        print(f"  ${aum/1e6:>8.2f}M ${cur_total:>10,.0f} ${adapt_total:>10,.0f} "
              f"${lift:>+8,.0f} {lift_pct:>+8.1f}%{flag}")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 5 — pre-committed verdict")
    print("  Pre-commit: ship adaptive Kelly if expected $-PNL improves by >=10%")
    print("              at $1M+ AUM AND no degradation at $140K paper account.")
    print()
    paper_pnl = next((p for p in pnl_results if p["aum"] == 100_000), None)
    aum_1m_pnl = next((p for p in pnl_results if p["aum"] == 1_000_000), None)
    aum_5m_pnl = next((p for p in pnl_results if p["aum"] == 5_000_000), None)
    if paper_pnl:
        print(f"  At $100K (proxy for paper account): adaptive lift {paper_pnl['lift_pct']:+.1f}%")
    if aum_1m_pnl:
        print(f"  At $1M:                              adaptive lift {aum_1m_pnl['lift_pct']:+.1f}%")
    if aum_5m_pnl:
        print(f"  At $5M:                              adaptive lift {aum_5m_pnl['lift_pct']:+.1f}%")
    print()
    paper_ok = paper_pnl and paper_pnl["lift_dollars"] >= -1.0  # essentially no degradation
    aum_1m_ok = aum_1m_pnl and aum_1m_pnl["lift_pct"] >= 10.0
    aum_5m_ok = aum_5m_pnl and aum_5m_pnl["lift_pct"] >= 10.0
    if paper_ok and (aum_1m_ok or aum_5m_ok):
        print(f"  -> SHIP adaptive Kelly. Lift criteria met.")
    else:
        print(f"  -> Adaptive Kelly does NOT meet pre-committed criteria.")
        if not paper_ok:
            print(f"     Reason: paper-account $-PNL would degrade.")
        if not (aum_1m_ok or aum_5m_ok):
            print(f"     Reason: $1M or $5M AUM lift below 10% threshold.")

    # Persist results for next-commit code
    out = {
        "Y_bouchaud": Y,
        "current_kelly": CURRENT_KELLY,
        "optimal_per_aum_tier": optimal,
        "comparison_per_pick": comparison,
        "adaptive_proposal": proposal,
        "pnl_per_aum": pnl_results,
        "fires_per_day": fires_per_day,
    }
    out_path = MODELS / "v6_d290_adaptive_kelly.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
