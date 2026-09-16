"""Doc 147 — recent-subset audit aimed at the v3 cascade itself.

Per user's critique on doc 146 closing line: "the next genuinely
productive session is gated on D286 shadow data" identifies a NEW
failure mode I haven't faced before. The discipline that killed v4,
v5, ELITE, TabICL, and D289 has NEVER been aimed at the v3 production
cascade specifically on the RECENT-DATA SUBSET that production trades in.

Doc 144 ran cascade DSR on the FULL data (2025-01 to 2026-04, 320 days).
That validation has been load-bearing for everything since. But the
production-relevant period is 2025-08+ (where TabPFN's NEWER-period
analysis lives, where d-1 microstructure has coverage, where production
will deploy). Recent-subset DSR has never been computed.

FOUR EXPERIMENTS, RUN IN ONE SESSION:

  Exp A: Recent-subset DSR on v3 cascade
    - Filter v3 BROAD specialist OOS preds to d0 >= 2025-08-01
    - Apply production tier thresholds, compute daily P&L
    - Apply DSR with N_trials sweep (1, 10, 50, 100, 150, 200)

  Exp B: Per-tier DSR audit (each tier separately)
    - ELITE, HIGH, VETOED, BROAD as standalone selectors
    - Top-3/day picks per tier, daily P&L, DSR sweep
    - If 3+ tiers fail, rethink tier structure entirely

  Exp C: Offline overlay Sharpe (the test most likely to surprise negatively)
    - Doc 144 reported overlay PRECISION lift +5.87pp (now corrected to +5.70pp)
    - But what about the SHARPE ratio of overlay-filtered picks?
    - If concentration into fewer picks REDUCES per-pick variance enough
      to keep Sharpe up, overlay is alpha-additive
    - If concentration just picks fewer-but-equally-noisy names, Sharpe
      stays flat and overlay's value collapses

  Exp D: Recent-subset capacity re-run (doc 142 revision)
    - Doc 142 Bouchaud impact analysis used full-data mean returns
    - Recent-subset means may be lower (regime decay) -> lower capacity ceiling
    - Re-run capacity sweep using only recent-subset tier means

PRE-COMMITTED VERDICTS LOCKED IN WRITING BEFORE THIS SCRIPT RUNS:
  Exp A: cascade recent-subset DSR @ N=100 < 0.5  -> URGENT pause D288
  Exp B: 3+ tiers fail individual DSR @ N=50      -> rethink tier structure
  Exp C: overlay Sharpe <= v3 baseline Sharpe     -> D288 not alpha-additive
  Exp D: recent capacity ceiling < $500K           -> revise BANKROLL_PCT

USAGE:
  python scripts/ml_v6_recent_subset_audit.py
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


EULER_MASCHERONI = 0.5772156649


def deflated_sharpe(daily_pnl: np.ndarray, n_trials: int) -> dict:
    """Bailey-LdP JPM 2014 DSR with skew/kurt correction (correct formula)."""
    pnl = daily_pnl[~np.isnan(daily_pnl)]
    if len(pnl) < 10:  # Lower threshold for small recent subsets
        return {"error": f"only {len(pnl)} days"}
    mean = pnl.mean(); std = pnl.std(ddof=1)
    if std <= 0: return {"error": "zero std"}
    sr = mean / std
    sr_ann = sr * math.sqrt(252)
    skew = float(((pnl - mean) ** 3).mean() / std ** 3)
    kurt_ex = float(((pnl - mean) ** 4).mean() / std ** 4 - 3)
    T = len(pnl)
    sigma_sr = float(math.sqrt(
        max(1e-8, (1 - skew * sr + (kurt_ex / 4) * sr ** 2) / (T - 1))
    ))
    if n_trials <= 1:
        sr_max_h0_std = 0.0
    else:
        sr_max_h0_std = float(
            (1 - EULER_MASCHERONI) * norm.ppf(1 - 1 / n_trials)
            + EULER_MASCHERONI * norm.ppf(1 - 1 / (n_trials * math.e))
        )
    sr_max_threshold = sr_max_h0_std * sigma_sr
    z = (sr - sr_max_threshold) / sigma_sr if sigma_sr > 0 else 0.0
    return {
        "T_days": T, "sr_per_period": float(sr), "sr_annualized": float(sr_ann),
        "skew": skew, "excess_kurtosis": kurt_ex, "sigma_sr": sigma_sr,
        "n_trials": n_trials, "sr_max_threshold": float(sr_max_threshold),
        "z_score": float(z), "dsr": float(norm.cdf(z)),
    }


def assign_tier_absolute(prob: pd.Series) -> pd.Series:
    tier = pd.Series(["SKIP"] * len(prob), index=prob.index, dtype=object)
    tier.loc[prob >= 0.30] = "BROAD"
    tier.loc[prob >= 0.40] = "VETOED"
    tier.loc[prob >= 0.50] = "HIGH"
    tier.loc[prob >= 0.60] = "ELITE"
    return tier


def main() -> int:
    section("LOAD — v3 BROAD specialist OOS preds")
    v3 = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    v3["d0"] = pd.to_datetime(v3["d0"])
    print(f"  v3 BROAD specialist: {len(v3):,} rows, "
          f"d0 range {v3['d0'].min().date()} -> {v3['d0'].max().date()}")
    v3["tier"] = assign_tier_absolute(v3["prob_specialist_BROAD"])

    # Define the recent subset boundary — production-relevant period
    threshold = pd.Timestamp("2025-08-01")
    print(f"  Splitting at {threshold.date()} (production-relevant boundary):")
    full_n = len(v3)
    older = v3[v3["d0"] < threshold]
    recent = v3[v3["d0"] >= threshold]
    print(f"    OLDER  (Jan 2025 - Jul 2025): {len(older):,} rows ({len(older)/full_n*100:.1f}%)")
    print(f"    RECENT (Aug 2025 - Apr 2026): {len(recent):,} rows ({len(recent)/full_n*100:.1f}%)")

    # ─────────────────────────────────────────────────────────────────
    section("EXP A — recent-subset DSR on v3 cascade (the one never run)")
    print("  Cascade union (any tier active): daily P&L = mean ret_t5 of fired picks")
    print()

    def cascade_daily_pnl(df_subset):
        cascade_picks = df_subset[df_subset["tier"] != "SKIP"]
        per_day = cascade_picks.groupby("d0")["y_reg"].mean()
        return per_day.dropna().values

    full_pnl = cascade_daily_pnl(v3)
    older_pnl = cascade_daily_pnl(older)
    recent_pnl = cascade_daily_pnl(recent)
    print(f"  CASCADE daily P&L:")
    print(f"    Full   T={len(full_pnl):>3}  mean {full_pnl.mean()*100:+6.2f}%/day  "
          f"std {full_pnl.std()*100:5.2f}%  ann_sharpe {full_pnl.mean()/full_pnl.std()*math.sqrt(252):+.2f}")
    print(f"    OLDER  T={len(older_pnl):>3}  mean {older_pnl.mean()*100:+6.2f}%/day  "
          f"std {older_pnl.std()*100:5.2f}%  ann_sharpe {older_pnl.mean()/older_pnl.std()*math.sqrt(252):+.2f}")
    print(f"    RECENT T={len(recent_pnl):>3}  mean {recent_pnl.mean()*100:+6.2f}%/day  "
          f"std {recent_pnl.std()*100:5.2f}%  ann_sharpe {recent_pnl.mean()/recent_pnl.std()*math.sqrt(252):+.2f}")
    print()

    print("  DSR sweep (what survives at conservative N_trials counts):")
    print(f"  {'subset':<10} {'T':>4} {'sr_ann':>7}", end="")
    for nt in (1, 10, 50, 100, 150, 200):
        print(f"  {f'N={nt}':>7}", end="")
    print()
    print("  " + "-" * 76)
    exp_a_results = {}
    for label, pnl in [("FULL", full_pnl), ("OLDER", older_pnl), ("RECENT", recent_pnl)]:
        row = []
        sr_ann = pnl.mean() / pnl.std() * math.sqrt(252) if pnl.std() > 0 else 0
        print(f"  {label:<10} {len(pnl):>4} {sr_ann:>+7.2f}", end="")
        sub_results = {}
        for nt in (1, 10, 50, 100, 150, 200):
            d = deflated_sharpe(pnl, nt)
            dsr_val = d.get("dsr", float("nan"))
            print(f"  {dsr_val:>7.3f}", end="")
            sub_results[f"N{nt}"] = d
        print()
        exp_a_results[label] = sub_results

    print()
    recent_dsr_n100 = exp_a_results["RECENT"].get("N100", {}).get("dsr", float("nan"))
    print(f"  PRE-COMMIT: recent-subset cascade DSR @ N=100 < 0.5  ->  pause D288")
    print(f"  RECENT cascade DSR @ N=100: {recent_dsr_n100:.4f}")
    if recent_dsr_n100 < 0.5:
        print(f"  -> PRE-COMMIT FIRES: cascade fails recent-subset DSR. URGENT investigation.")
    elif recent_dsr_n100 < 0.95:
        print(f"  -> Marginal: cascade survives 0.5 gate but doesn't pass 0.95 publication bar.")
    else:
        print(f"  -> Cascade passes recent-subset DSR cleanly. D288 plan stands.")

    # ─────────────────────────────────────────────────────────────────
    section("EXP B — per-tier DSR (each of 4 tiers individually)")
    print("  For each tier: top-3/day picks, daily P&L, DSR sweep at N=10/50/100")
    print()
    print(f"  {'tier':<8} {'subset':<8} {'T':>4} {'sr_ann':>7} {'DSR_N10':>9} {'DSR_N50':>9} {'DSR_N100':>10}")
    print("  " + "-" * 70)
    exp_b_results = {}
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        for label, df_sub in [("FULL", v3), ("OLDER", older), ("RECENT", recent)]:
            tier_sub = df_sub[df_sub["tier"] == tier]
            if len(tier_sub) < 5:
                print(f"  {tier:<8} {label:<8} {'(<5 picks)':>20}")
                continue
            per_day = (tier_sub
                       .sort_values(["d0", "prob_specialist_BROAD"], ascending=[True, False])
                       .groupby("d0").head(3).groupby("d0")["y_reg"].mean())
            pnl = per_day.dropna().values
            if len(pnl) < 10:
                print(f"  {tier:<8} {label:<8} {len(pnl):>4} (T<10, skip DSR)")
                continue
            sr_ann = pnl.mean() / pnl.std() * math.sqrt(252)
            d10 = deflated_sharpe(pnl, 10).get("dsr", float("nan"))
            d50 = deflated_sharpe(pnl, 50).get("dsr", float("nan"))
            d100 = deflated_sharpe(pnl, 100).get("dsr", float("nan"))
            print(f"  {tier:<8} {label:<8} {len(pnl):>4} {sr_ann:>+7.2f} {d10:>9.3f} {d50:>9.3f} {d100:>10.3f}")
            exp_b_results[f"{tier}_{label}"] = {
                "T": int(len(pnl)), "sr_ann": float(sr_ann),
                "dsr_n10": float(d10), "dsr_n50": float(d50), "dsr_n100": float(d100),
            }

    print()
    # Pre-commit: 3+ of 4 tiers fail individual DSR @ N=50 -> rethink tier structure
    failures = []
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        key = f"{tier}_RECENT"
        if key in exp_b_results:
            if exp_b_results[key]["dsr_n50"] < 0.5:
                failures.append(tier)
    print(f"  PRE-COMMIT: 3+ tiers fail RECENT DSR @ N=50  ->  rethink tier structure")
    print(f"  Tiers failing recent DSR @ N=50: {failures} ({len(failures)} of 4)")
    if len(failures) >= 3:
        print(f"  -> PRE-COMMIT FIRES: tier structure should be rebuilt.")
    elif len(failures) > 0:
        print(f"  -> {len(failures)} tier(s) noisy on recent data. Cascade benefits from averaging.")

    # ─────────────────────────────────────────────────────────────────
    section("EXP C — offline overlay Sharpe (the test most likely to surprise)")
    print("  Doc 145 reported overlay PRECISION lift +5.70pp.")
    print("  But what about the SHARPE? Concentration -> fewer picks -> different variance.")
    print("  If overlay Sharpe <= v3 baseline Sharpe, defensive overlay is alpha-FLAT.")
    print()

    # Reload the recovered TabPFN preds for accurate alignment
    tabpfn = pd.read_parquet(DERIVED / "ml_v6_item5_tabpfn_preds_RECOVERED.parquet")
    tabpfn["d0"] = pd.to_datetime(tabpfn["d0"])
    m = tabpfn.merge(v3[["ticker", "d0", "y_reg", "prob_specialist_BROAD", "tier"]],
                     on=["ticker", "d0"])
    print(f"  Merged TabPFN + v3 (clean): {len(m):,} rows")

    # Per-day TabPFN top-quintile flag
    def top_q(g):
        if len(g) < 4: return pd.Series([False]*len(g), index=g.index)
        return g["y_pred"] >= g["y_pred"].quantile(0.80)
    m["tabpfn_top_q"] = m.groupby("d0", group_keys=False).apply(top_q, include_groups=False)
    m["v3_trades"] = m["tier"] != "SKIP"

    # Daily P&L: v3 trades alone vs v3 trades filtered by TabPFN-agree
    def daily_pnl_from_subset(sub):
        per_day = sub.groupby("d0")["y_reg"].mean()
        return per_day.dropna().values

    v3_alone = m[m["v3_trades"]]
    v3_w_pf = m[(m["v3_trades"]) & (m["tabpfn_top_q"])]
    v3_wo_pf = m[(m["v3_trades"]) & (~m["tabpfn_top_q"])]

    pnl_v3 = daily_pnl_from_subset(v3_alone)
    pnl_overlay = daily_pnl_from_subset(v3_w_pf)
    pnl_filtered_out = daily_pnl_from_subset(v3_wo_pf)

    def sharpe_metrics(pnl, label):
        if len(pnl) < 10:
            return {"label": label, "n_picks": 0, "T_days": 0, "error": "T<10"}
        mean = pnl.mean(); std = pnl.std(ddof=1)
        sr_ann = mean/std * math.sqrt(252) if std > 0 else 0
        return {
            "label": label,
            "T_days": int(len(pnl)),
            "mean_pct": float(mean * 100),
            "std_pct": float(std * 100),
            "sharpe_ann": float(sr_ann),
        }

    print(f"  {'variant':<28} {'T':>4} {'mean':>8} {'std':>7} {'Sharpe_ann':>11} {'n_picks':>8}")
    print("  " + "-" * 70)
    variants_metrics = []
    for label, pnl, picks_df in [
        ("v3 trades alone", pnl_v3, v3_alone),
        ("v3 + TabPFN agrees", pnl_overlay, v3_w_pf),
        ("v3 + TabPFN disagrees", pnl_filtered_out, v3_wo_pf),
    ]:
        sm = sharpe_metrics(pnl, label)
        if "error" in sm:
            print(f"  {label:<28} {len(pnl):>4} (T<10)")
            continue
        sm["n_picks"] = int(len(picks_df))
        variants_metrics.append(sm)
        print(f"  {label:<28} {sm['T_days']:>4} {sm['mean_pct']:>+7.2f}% {sm['std_pct']:>6.2f}% "
              f"{sm['sharpe_ann']:>+10.2f} {sm['n_picks']:>7}")

    print()
    baseline_sharpe = overlay_sharpe = None  # pre-bound: assigned conditionally below
    if len(variants_metrics) >= 2:
        baseline_sharpe = next((v["sharpe_ann"] for v in variants_metrics
                                  if v["label"] == "v3 trades alone"), None)
        overlay_sharpe = next((v["sharpe_ann"] for v in variants_metrics
                                  if v["label"] == "v3 + TabPFN agrees"), None)
        if baseline_sharpe is not None and overlay_sharpe is not None:
            sharpe_lift = overlay_sharpe - baseline_sharpe
            print(f"  PRE-COMMIT: overlay Sharpe <= v3 baseline Sharpe  ->  D288 not alpha-additive")
            print(f"  v3 baseline Sharpe (annualized):  {baseline_sharpe:+.2f}")
            print(f"  Overlay Sharpe (annualized):       {overlay_sharpe:+.2f}")
            print(f"  Sharpe lift from overlay:         {sharpe_lift:+.2f}")
            if sharpe_lift <= 0:
                print(f"  -> PRE-COMMIT FIRES: overlay does NOT add risk-adjusted alpha.")
                print(f"     D288 plan needs reconsideration. Overlay concentrates picks but")
                print(f"     loses the risk-adjusted edge. Defensive value uncertain.")
            elif sharpe_lift < 1.0:
                print(f"  -> Modest Sharpe lift. D288 still positive but smaller than precision implied.")
            else:
                print(f"  -> Strong Sharpe lift. D288 plan validated on offline data.")

    # Exp C extension: SAME analysis on RECENT subset only
    print()
    print("  RECENT-subset only repeat of overlay Sharpe:")
    m_recent = m[m["d0"] >= threshold]
    v3_alone_r = m_recent[m_recent["v3_trades"]]
    v3_w_pf_r = m_recent[(m_recent["v3_trades"]) & (m_recent["tabpfn_top_q"])]
    pnl_v3_r = daily_pnl_from_subset(v3_alone_r)
    pnl_overlay_r = daily_pnl_from_subset(v3_w_pf_r)
    print(f"  v3 baseline RECENT:    T={len(pnl_v3_r):>3}  mean {pnl_v3_r.mean()*100:+6.2f}%  "
          f"std {pnl_v3_r.std()*100:5.2f}%  sharpe_ann {pnl_v3_r.mean()/pnl_v3_r.std()*math.sqrt(252) if pnl_v3_r.std()>0 else 0:+.2f}")
    if len(pnl_overlay_r) >= 5:
        print(f"  overlay RECENT:        T={len(pnl_overlay_r):>3}  mean {pnl_overlay_r.mean()*100:+6.2f}%  "
              f"std {pnl_overlay_r.std()*100:5.2f}%  sharpe_ann {pnl_overlay_r.mean()/pnl_overlay_r.std()*math.sqrt(252) if pnl_overlay_r.std()>0 else 0:+.2f}")
    else:
        print(f"  overlay RECENT:        T={len(pnl_overlay_r)} (too few days for stable Sharpe)")

    # ─────────────────────────────────────────────────────────────────
    section("EXP D — recent-subset capacity (revised doc 142 with recent means)")
    print("  Per-tier mean ret_t5 by subset (the input to Bouchaud capacity calc):")
    print(f"  {'tier':<8} {'subset':<8} {'n_picks':>8} {'mean_ret':>10}")
    tier_means = {}
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        for label, df_sub in [("FULL", v3), ("OLDER", older), ("RECENT", recent)]:
            t_sub = df_sub[df_sub["tier"] == tier]
            if len(t_sub) == 0:
                print(f"  {tier:<8} {label:<8} {'(empty)':>8}")
                continue
            mean = t_sub["y_reg"].mean()
            tier_means[f"{tier}_{label}"] = float(mean)
            print(f"  {tier:<8} {label:<8} {len(t_sub):>8,} {mean*100:>+9.2f}%")

    # Re-run doc 142 capacity calc using RECENT means
    print()
    print("  Bouchaud capacity ceiling (Y=1.5, current Aggressive Kelly 50/35/20/10):")
    TIER_KELLY = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
    # Pull base data for dvol_d0 + intraday_pct
    from ml_continuer_v2_ensemble import load_data
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    full_join = v3.merge(df[["ticker","d0","dvol_d0","intraday_pct"]], on=["ticker","d0"])
    full_join["sigma_d"] = full_join["intraday_pct"].clip(0.001, 2.0) / 4.0  # daily vol approx
    Y = 1.5
    print(f"  {'AUM':>10} {'tier':<8} {'mean_ret_recent':>16} {'avg_part':>10} {'mean_imp':>10} {'mean_net':>10}")
    print("  " + "-" * 70)
    capacity_results = {}
    for aum in (100_000, 500_000, 1_000_000, 5_000_000):
        for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
            sub = full_join[(full_join["d0"] >= threshold) & (full_join["tier"] == tier)]
            if len(sub) == 0: continue
            position = aum * TIER_KELLY[tier]
            sub = sub.copy()
            sub["participation"] = (position / sub["dvol_d0"].clip(lower=1)).clip(0, 1.0)
            sub["impact_rt"] = 2 * Y * np.sqrt(sub["participation"]) * sub["sigma_d"]
            mean_ret_recent = sub["y_reg"].mean()
            mean_part = sub["participation"].mean()
            mean_imp = sub["impact_rt"].mean()
            mean_net = mean_ret_recent - mean_imp
            capacity_results[f"AUM_{aum}_{tier}"] = {
                "mean_ret": float(mean_ret_recent), "avg_part": float(mean_part),
                "mean_impact": float(mean_imp), "mean_net": float(mean_net),
            }
            print(f"  ${aum/1e6:>8.2f}M {tier:<8} {mean_ret_recent*100:>+15.2f}% "
                  f"{mean_part*100:>9.1f}% {mean_imp*100:>9.2f}% {mean_net*100:>+9.2f}%")
        print()

    # Find recent-data capacity ceiling
    print("  Recent-data capacity ceiling (last AUM where all-tier mean_net > 0):")
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        last_pos = None
        first_neg = None
        for aum in (100_000, 500_000, 1_000_000, 5_000_000):
            key = f"AUM_{aum}_{tier}"
            if key not in capacity_results: continue
            net = capacity_results[key]["mean_net"]
            if net > 0:
                last_pos = aum
            elif first_neg is None:
                first_neg = aum
        last_str = f"${last_pos/1e6:.2f}M" if last_pos else "ALL fail"
        first_str = f"${first_neg/1e6:.2f}M" if first_neg else "(none)"
        print(f"    {tier:<8} last_AUM_pos={last_str:<10}  first_AUM_neg={first_str}")

    # Pre-commit on capacity
    print()
    overall_ceiling = None
    for aum in (5_000_000, 1_000_000, 500_000, 100_000):
        all_pos = all(
            capacity_results.get(f"AUM_{aum}_{tier}", {}).get("mean_net", -1) > 0
            for tier in ["ELITE", "HIGH"]
        )
        if all_pos:
            overall_ceiling = aum
            break
    print(f"  PRE-COMMIT: recent-subset capacity ceiling < $500K  ->  revise BANKROLL_PCT")
    print(f"  Recent-data capacity ceiling (ELITE+HIGH net > 0): "
          f"${overall_ceiling/1e6:.2f}M" if overall_ceiling else "  Recent: NONE survive")
    if overall_ceiling is None or overall_ceiling < 500_000:
        print(f"  -> PRE-COMMIT FIRES: recent capacity below $500K threshold")
    else:
        print(f"  -> Capacity above threshold. D285 (5%) participation cap holds.")

    section("PERSIST")
    out = {
        "exp_a_recent_dsr": exp_a_results,
        "exp_b_per_tier": exp_b_results,
        "exp_c_overlay_sharpe": variants_metrics,
        "exp_d_recent_capacity": capacity_results,
        "tier_means_by_subset": tier_means,
        "pre_commits": {
            "exp_a_fired": float(recent_dsr_n100) < 0.5 if not np.isnan(recent_dsr_n100) else None,
            "exp_b_fired": len(failures) >= 3,
            "exp_c_fired": (overlay_sharpe is not None and baseline_sharpe is not None
                              and overlay_sharpe <= baseline_sharpe),
            "exp_d_fired": (overall_ceiling is None or overall_ceiling < 500_000),
        },
    }
    out_path = MODELS / "v6_recent_subset_audit.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
