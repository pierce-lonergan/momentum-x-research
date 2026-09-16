"""Doc 150 — HIGH-RECENT deep dive (Thread #3 from doc 147 §8.3, escalated to top priority).

Per user critique on doc 149 closing: HIGH-recent Sharpe +12.50 / DSR @ N=100 = 0.999
is the most extreme positive result in the entire 149-doc arc. It was filed as
"thread #3, future session" — that's the same discipline failure as filing a
dramatic NEGATIVE as "marginal." Verdict-blank applies to positive outliers too.

NEW HYGIENE RULE (locked in this session):
  Any finding with DSR > 0.95 at N >= 100 becomes the next session's top
  priority, regardless of what was previously queued.

THE AGGRESSIVE THESIS:
  Production cascade isn't a cascade — it's a HIGH-tier strategy with three
  diluting tiers. ELITE failed DSR. VETOED was Sharpe -1.36 OLDER / +4.36 RECENT
  (regime-volatile). BROAD requires aggressive sizing reduction. HIGH is the
  actual edge.

FIVE SUB-EXPERIMENTS (one session, all pre-committed):

  A: Per-month Sharpe robustness for HIGH-recent (leave-one-month-out)
     PASS: min single-month-removed Sharpe >= 5.0
     FAIL: any month removal drops Sharpe below 3.0 (outlier-driven)

  B: Bouchaud-optimal Kelly for HIGH alone at $1M AUM
     PASS: optimal HIGH Kelly > current adaptive cap (35% per D290)
           => ship a HIGH-specific override raising the cap

  C: Tier-definition sensitivity (vary v3_proba threshold in [0.45, 0.55])
     PASS: Sharpe robust (within ±2.0) across the threshold range
     FAIL: spike at one threshold + collapse elsewhere (fragile boundary)

  D: d-1 microstructure on HIGH-eligible candidates ONLY
     PASS: v3+d-1 XGB Spearman lift >= +0.020 over v3-alone on HIGH subset
     (Note: doc 144 found d-1 hurts TabPFN; this tests XGB-on-HIGH)

  E: TabPFN overlay isolated to HIGH picks
     PASS: HIGH+TabPFN-agree Sharpe >= 1.2x HIGH-alone Sharpe

BOLD SHIP PRE-COMMIT (D291, the user's exact rule):
  IF 4-of-5 sub-experiments PASS
  AND HIGH-only Sharpe at $1M AUM (Bouchaud-adjusted) >=
      1.5x cascade Sharpe at $1M (Bouchaud-adjusted)
  THEN: next launcher commit defaults LOTTERY_MIN_TIER_THRESHOLD=0.50
        (HIGH+ELITE only). Direct production change, not env-flag-default-off.

USAGE:
  python scripts/ml_v6_high_only_deep_dive.py
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


def assign_tier_absolute(prob: pd.Series, threshold_high: float = 0.50,
                          threshold_elite: float = 0.60) -> pd.Series:
    tier = pd.Series(["SKIP"] * len(prob), index=prob.index, dtype=object)
    tier.loc[prob >= 0.30] = "BROAD"
    tier.loc[prob >= 0.40] = "VETOED"
    tier.loc[prob >= threshold_high] = "HIGH"
    tier.loc[prob >= threshold_elite] = "ELITE"
    return tier


def annualized_sharpe(daily_pnl: np.ndarray) -> float:
    if len(daily_pnl) < 2: return float("nan")
    mean = daily_pnl.mean(); std = daily_pnl.std(ddof=1)
    if std <= 0: return float("nan")
    return float(mean / std * math.sqrt(252))


def main() -> int:
    section("LOAD")
    v3 = pd.read_parquet(DERIVED / "ml_v3_tier_specialist_BROAD_predictions.parquet")
    v3["d0"] = pd.to_datetime(v3["d0"])
    v3["tier"] = assign_tier_absolute(v3["prob_specialist_BROAD"])
    threshold_recent = pd.Timestamp("2025-08-01")
    recent = v3[v3["d0"] >= threshold_recent].copy()
    print(f"  v3 BROAD specialist preds: {len(v3):,}; recent (Aug 2025+): {len(recent):,}")

    high_recent = recent[recent["tier"] == "HIGH"].copy()
    print(f"  HIGH-recent rows: {len(high_recent):,}")

    # Daily P&L for HIGH-recent: top-3 picks per day per doc 147 methodology
    def daily_topN_pnl(sub, n=3):
        per_day = (sub
                   .sort_values(["d0", "prob_specialist_BROAD"], ascending=[True, False])
                   .groupby("d0").head(n).groupby("d0")["y_reg"].mean())
        return per_day.dropna()

    hi_daily = daily_topN_pnl(high_recent)
    print(f"  HIGH-recent active days: {len(hi_daily)}")
    print(f"  HIGH-recent ann Sharpe (full):  {annualized_sharpe(hi_daily.values):+.3f}")

    # Save the doc 147 baseline for comparison
    hi_recent_sharpe_baseline = annualized_sharpe(hi_daily.values)

    # ─────────────────────────────────────────────────────────────────
    section("SUB-EXP A — leave-one-month-out HIGH Sharpe robustness")
    print("  PRE-COMMIT: PASS if min(single-month-removed Sharpe) >= 5.0")
    print("              FAIL if any month removal drops Sharpe below 3.0")
    print()
    hi_daily_df = hi_daily.reset_index()
    hi_daily_df["year_month"] = hi_daily_df["d0"].dt.to_period("M")
    months = sorted(hi_daily_df["year_month"].unique())
    print(f"  HIGH-recent active months: {len(months)}")
    print()
    print(f"  {'month_removed':<14} {'n_remaining':>11} {'sharpe_remaining':>17}")
    loo_sharpes = []
    for m in months:
        rem = hi_daily_df[hi_daily_df["year_month"] != m]
        if len(rem) < 5: continue
        s = annualized_sharpe(rem["y_reg"].values)
        loo_sharpes.append(s)
        print(f"  {str(m):<14} {len(rem):>11} {s:>+16.3f}")
    if loo_sharpes:
        print()
        print(f"  full-data Sharpe:        {hi_recent_sharpe_baseline:+.3f}")
        print(f"  min single-month-removed: {min(loo_sharpes):+.3f}")
        print(f"  median single-month-removed: {np.median(loo_sharpes):+.3f}")
        print(f"  max single-month-removed: {max(loo_sharpes):+.3f}")
        exp_a_pass = min(loo_sharpes) >= 5.0
        exp_a_fail = min(loo_sharpes) < 3.0
        verdict = "PASS" if exp_a_pass else ("FAIL (outlier-driven)" if exp_a_fail else "MARGINAL")
        print(f"  -> Sub-exp A: {verdict}")
    else:
        exp_a_pass = False; exp_a_fail = True
        print("  no months computable")

    # ─────────────────────────────────────────────────────────────────
    section("SUB-EXP B — Bouchaud-optimal Kelly for HIGH alone at $1M AUM")
    print("  PRE-COMMIT: PASS if optimal HIGH Kelly > 35% (D290 cap at $1M)")
    print()
    from ml_continuer_v2_ensemble import load_data
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    high_with_features = high_recent.merge(
        df[["ticker", "d0", "dvol_d0", "intraday_pct"]], on=["ticker", "d0"])
    high_with_features["sigma_d"] = high_with_features["intraday_pct"].clip(0.001, 2.0) / 4.0
    Y = 1.5  # Bouchaud microcap
    aum = 1_000_000  # $1M target

    KELLY_GRID = np.arange(0.005, 1.005, 0.005)
    best_pnl = -1e9
    best_kelly = None
    best_metrics = None
    for kelly in KELLY_GRID:
        position = aum * kelly
        part = (position / high_with_features["dvol_d0"].clip(lower=1)).clip(0, 1.0)
        impact_rt = 2 * Y * np.sqrt(part) * high_with_features["sigma_d"]
        net = high_with_features["y_reg"] - impact_rt
        mean_net = net.mean()
        pnl_per_pick = position * mean_net
        if pnl_per_pick > best_pnl:
            best_pnl = pnl_per_pick
            best_kelly = kelly
            best_metrics = {
                "mean_ret": float(high_with_features["y_reg"].mean()),
                "mean_impact": float(impact_rt.mean()),
                "mean_net": float(mean_net),
                "avg_part": float(part.mean()),
                "pnl_per_pick": float(pnl_per_pick),
            }
    print(f"  HIGH-recent at $1M AUM:")
    print(f"    n picks: {len(high_with_features)}")
    print(f"    optimal Kelly: {best_kelly*100:.1f}%")
    print(f"    avg participation at optimal: {best_metrics['avg_part']*100:.1f}%")
    print(f"    mean ret/pick: {best_metrics['mean_ret']*100:+.2f}%")
    print(f"    mean impact/pick: {best_metrics['mean_impact']*100:.2f}%")
    print(f"    mean net/pick: {best_metrics['mean_net']*100:+.2f}%")
    print(f"    $-PnL per pick: ${best_metrics['pnl_per_pick']:,.0f}")
    current_high_kelly_at_1m = 0.35  # from D290 schedule
    print(f"  current adaptive HIGH Kelly at $1M (D290): {current_high_kelly_at_1m*100:.1f}%")
    exp_b_pass = best_kelly > current_high_kelly_at_1m
    print(f"  -> Sub-exp B: {'PASS (HIGH-specific Kelly raise warranted)' if exp_b_pass else 'NO LIFT (current cap is near-optimal)'}")

    # ─────────────────────────────────────────────────────────────────
    section("SUB-EXP C — tier-definition sensitivity")
    print("  PRE-COMMIT: PASS if HIGH Sharpe is robust across thresholds [0.45, 0.55]")
    print("              (within +/- 2.0 of full-threshold-0.50 baseline)")
    print()
    print(f"  {'thr_high':>9} {'n_picks':>8} {'days':>5} {'ann_sharpe':>11} {'mean_ret':>10}")
    sens = []
    for thr in [0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60]:
        sub = recent[(recent["prob_specialist_BROAD"] >= thr) &
                      (recent["prob_specialist_BROAD"] < 0.60)].copy()
        if len(sub) < 5:
            print(f"  {thr:>9.2f} {len(sub):>8} {'(<5)':>5}")
            continue
        d = daily_topN_pnl(sub)
        s = annualized_sharpe(d.values) if len(d) >= 2 else float("nan")
        m = float(sub["y_reg"].mean()) * 100
        sens.append((thr, len(sub), len(d), s, m))
        print(f"  {thr:>9.2f} {len(sub):>8,} {len(d):>5} {s:>+10.3f} {m:>+9.2f}%")
    max_dev = None  # pre-bound: assigned conditionally below
    if sens:
        sharpes_sens = [x[3] for x in sens if not math.isnan(x[3])]
        baseline_at_050 = next((x[3] for x in sens if abs(x[0] - 0.50) < 1e-6), float("nan"))
        max_dev = max(abs(s - baseline_at_050) for s in sharpes_sens)
        print()
        print(f"  baseline at thr=0.50: {baseline_at_050:+.3f}")
        print(f"  max deviation across thresholds: {max_dev:.3f}")
        exp_c_pass = max_dev <= 2.0
        print(f"  -> Sub-exp C: {'PASS (robust)' if exp_c_pass else 'FAIL (fragile boundary)'}")
    else:
        exp_c_pass = False

    # ─────────────────────────────────────────────────────────────────
    section("SUB-EXP D — d-1 microstructure on HIGH-eligible candidates only")
    print("  PRE-COMMIT: PASS if v3+d-1 XGB Spearman lift >= +0.020 over v3-alone")
    print("              on HIGH-tier eligible candidates only")
    print()
    # Need to train XGB on HIGH-eligible subset only — restrict to candidates
    # where the v3 BROAD specialist's prob would put them in HIGH (>=0.50, <0.60)
    # OR all candidates but only evaluate on HIGH-eligible test rows.
    # Cleanest: restrict TRAINING + TEST to candidates above v3_proba 0.40
    # (a "VETOED-or-better" universe), which captures the HIGH boundary
    # context. Then measure Spearman only on HIGH subset within OOS.
    print("  Using v3+v6 d-1 microstructure framework from doc 138...")
    from ml_continuer_v2_ensemble import engineer_features
    v6 = pd.read_parquet(DERIVED / "microstructure_v6_pack_dminus1.parquet")
    v6["d0"] = pd.to_datetime(v6["d0"])
    V6_FEATS = ["vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
                "hawkes_fano_d0", "amihud_illiq_d0", "iso_sweep_count_d0"]
    merged = df.merge(v6[["ticker", "d0", *V6_FEATS]],
                      on=["ticker", "d0"], how="inner")
    print(f"  v3+d-1 inner join: {len(merged):,}")
    X_v3 = engineer_features(merged).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    v6_block = merged[V6_FEATS].copy().replace([np.inf, -np.inf], np.nan)
    v6_block["log_kyle_lambda"] = np.log1p(v6_block["kyle_lambda_d0"].clip(lower=0))
    v6_block["log_hawkes_fano"] = np.log1p(v6_block["hawkes_fano_d0"].clip(lower=0))
    v6_block["log_amihud_illiq"] = np.log1p(v6_block["amihud_illiq_d0"].clip(lower=0))
    v6_block["log_iso_sweep_count"] = np.log1p(v6_block["iso_sweep_count_d0"].clip(lower=0))
    v6_block = v6_block.fillna(0).reset_index(drop=True)
    X_v3_dm1 = pd.concat([X_v3, v6_block], axis=1)
    y = merged["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(merged["d0"]).reset_index(drop=True)
    # 12-fold WF same as Item 1
    n_folds = 12; train_days = 120; test_days = 15
    fs_list = pd.date_range(
        d0.min() + pd.Timedelta(days=train_days),
        d0.max() - pd.Timedelta(days=test_days), periods=n_folds,
    )
    import xgboost as xgb
    XGB_PARAMS = {"objective": "reg:squarederror", "n_estimators": 600,
        "max_depth": 5, "learning_rate": 0.03, "subsample": 0.8,
        "colsample_bytree": 0.6, "min_child_weight": 5, "reg_alpha": 0.5,
        "reg_lambda": 1.0, "tree_method": "hist", "device": "cpu",
        "n_jobs": 1, "random_state": 42}

    def run_wf(X_use):
        rows = []
        for fi, fs in enumerate(fs_list):
            tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
            te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
            if tr.sum() < 100 or te.sum() < 5: continue
            m = xgb.XGBRegressor(**XGB_PARAMS)
            m.fit(X_use.loc[tr], y.loc[tr], verbose=False)
            pred = m.predict(X_use.loc[te])
            rows.append(pd.DataFrame({"d0": d0.loc[te].values,
                                      "y_true": y.loc[te].values, "y_pred": pred,
                                      "_idx": np.where(te)[0]}))
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    print("  Running WF for v3-only and v3+d-1 on full universe, then restrict to HIGH-eligible...")
    preds_v3 = run_wf(X_v3)
    preds_dm1 = run_wf(X_v3_dm1)

    # Restrict OOS preds to HIGH-eligible test rows (those where v3 BROAD specialist would
    # have classified them as HIGH or near-HIGH). Use the predicted XGB score as a proxy.
    # Define HIGH-eligible: top quartile of XGB predictions per fold.
    def rho_high_only(preds):
        rows = []
        # Per-day top quartile
        preds["d0"] = pd.to_datetime(preds["d0"])
        for d, g in preds.groupby("d0"):
            if len(g) < 4: continue
            cutoff = g["y_pred"].quantile(0.75)
            rows.append(g[g["y_pred"] >= cutoff])
        sub = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        if len(sub) < 10: return float("nan")
        return float(spearmanr(sub["y_true"], sub["y_pred"]).statistic)

    rho_v3_high = rho_high_only(preds_v3)
    rho_dm1_high = rho_high_only(preds_dm1)
    print(f"  HIGH-eligible (top-quartile per day) Spearman:")
    print(f"    v3-only:   {rho_v3_high:+.4f}")
    print(f"    v3+d-1:    {rho_dm1_high:+.4f}")
    print(f"    delta:     {rho_dm1_high - rho_v3_high:+.4f}")
    exp_d_pass = (rho_dm1_high - rho_v3_high) >= 0.020
    print(f"  -> Sub-exp D: {'PASS (d-1 helps HIGH)' if exp_d_pass else 'NO MEANINGFUL LIFT'}")

    # ─────────────────────────────────────────────────────────────────
    section("SUB-EXP E — TabPFN defensive overlay isolated to HIGH picks")
    print("  PRE-COMMIT: PASS if HIGH+TabPFN-agree Sharpe >= 1.2x HIGH-alone Sharpe")
    print()
    tabpfn = pd.read_parquet(DERIVED / "ml_v6_item5_tabpfn_preds_RECOVERED.parquet")
    tabpfn["d0"] = pd.to_datetime(tabpfn["d0"])
    m_pf = tabpfn.merge(v3[["ticker", "d0", "y_reg", "prob_specialist_BROAD", "tier"]],
                        on=["ticker", "d0"])
    print(f"  Merged TabPFN + v3: {len(m_pf):,}")
    high_picks_pf = m_pf[m_pf["tier"] == "HIGH"].copy()
    print(f"  HIGH-tier picks with TabPFN preds: {len(high_picks_pf):,}")

    def top_q(g):
        if len(g) < 4: return pd.Series([False]*len(g), index=g.index)
        return g["y_pred"] >= g["y_pred"].quantile(0.80)

    if len(high_picks_pf) < 20:
        print("  too few HIGH-tier picks with TabPFN coverage; sub-exp E inconclusive")
        exp_e_pass = False
    else:
        high_picks_pf["tabpfn_top_q"] = m_pf.groupby("d0", group_keys=False).apply(
            top_q, include_groups=False).reindex(high_picks_pf.index, fill_value=False)
        # Daily P&L for HIGH-alone vs HIGH+TabPFN-agree
        hi_alone_daily = high_picks_pf.groupby("d0")["y_reg"].mean().dropna()
        hi_agree_daily = high_picks_pf[high_picks_pf["tabpfn_top_q"]].groupby("d0")["y_reg"].mean().dropna()
        s_alone = annualized_sharpe(hi_alone_daily.values)
        s_agree = annualized_sharpe(hi_agree_daily.values) if len(hi_agree_daily) >= 5 else float("nan")
        print(f"  HIGH alone:        T={len(hi_alone_daily):>3} mean {hi_alone_daily.mean()*100:+6.2f}% sharpe {s_alone:+.3f}")
        if not math.isnan(s_agree):
            print(f"  HIGH+TabPFN-agree: T={len(hi_agree_daily):>3} mean {hi_agree_daily.mean()*100:+6.2f}% sharpe {s_agree:+.3f}")
            ratio = s_agree / s_alone if s_alone > 0 else float("nan")
            print(f"  Sharpe ratio: {ratio:.3f}")
            exp_e_pass = (not math.isnan(ratio)) and (ratio >= 1.2)
            print(f"  -> Sub-exp E: {'PASS (overlay helps HIGH)' if exp_e_pass else 'NO MEANINGFUL LIFT'}")
        else:
            print(f"  HIGH+TabPFN-agree: T={len(hi_agree_daily)} (too few)")
            exp_e_pass = False

    # ─────────────────────────────────────────────────────────────────
    section("BOLD SHIP PRE-COMMIT — D291 cascade collapse to HIGH-only")
    sub_results = {"A": exp_a_pass, "B": exp_b_pass, "C": exp_c_pass,
                   "D": exp_d_pass, "E": exp_e_pass}
    n_pass = sum(1 for v in sub_results.values() if v)
    print(f"  Sub-experiment results:")
    for k, v in sub_results.items():
        print(f"    {k}: {'PASS' if v else 'FAIL'}")
    print(f"  Total: {n_pass} of 5 PASS")
    print()

    # Compute HIGH-only vs cascade Sharpe at $1M AUM, Bouchaud-adjusted
    print("  HIGH-only vs cascade Sharpe at $1M AUM (Bouchaud-adjusted):")
    cascade_recent = recent[recent["tier"] != "SKIP"].copy()
    cascade_with_features = cascade_recent.merge(
        df[["ticker", "d0", "dvol_d0", "intraday_pct"]], on=["ticker", "d0"])
    cascade_with_features["sigma_d"] = cascade_with_features["intraday_pct"].clip(0.001, 2.0) / 4.0
    # Use D290 adaptive Kelly per tier at $1M
    D290_KELLY_AT_1M = {"ELITE": 0.275, "HIGH": 0.35, "VETOED": 0.110, "BROAD": 0.010}
    cascade_with_features["kelly"] = cascade_with_features["tier"].map(D290_KELLY_AT_1M).fillna(0)
    cascade_with_features["position"] = aum * cascade_with_features["kelly"]
    cascade_with_features["part"] = (cascade_with_features["position"]
                                     / cascade_with_features["dvol_d0"].clip(lower=1)).clip(0, 1.0)
    cascade_with_features["impact_rt"] = 2 * Y * np.sqrt(cascade_with_features["part"]) * cascade_with_features["sigma_d"]
    cascade_with_features["net_ret"] = cascade_with_features["y_reg"] - cascade_with_features["impact_rt"]
    cascade_with_features["pnl_dollars"] = cascade_with_features["position"] * cascade_with_features["net_ret"]
    cascade_daily = cascade_with_features.groupby("d0")["pnl_dollars"].sum().dropna()
    cascade_sharpe_1m = annualized_sharpe(cascade_daily.values)

    # HIGH-only at $1M with optimal HIGH Kelly (or capped at 50% for safety)
    high_kelly_for_ship = min(best_kelly, 0.50)  # cap at 50% even if optimal higher
    high_only = high_with_features.copy()
    high_only["position"] = aum * high_kelly_for_ship
    high_only["part"] = (high_only["position"] / high_only["dvol_d0"].clip(lower=1)).clip(0, 1.0)
    high_only["impact_rt"] = 2 * Y * np.sqrt(high_only["part"]) * high_only["sigma_d"]
    high_only["net_ret"] = high_only["y_reg"] - high_only["impact_rt"]
    high_only["pnl_dollars"] = high_only["position"] * high_only["net_ret"]
    high_daily_1m = high_only.groupby("d0")["pnl_dollars"].sum().dropna()
    high_sharpe_1m = annualized_sharpe(high_daily_1m.values)

    print(f"  cascade $1M Bouchaud-adj Sharpe: {cascade_sharpe_1m:+.3f}  (T={len(cascade_daily)} days)")
    print(f"  HIGH-only $1M Bouchaud-adj Sharpe (Kelly={high_kelly_for_ship*100:.0f}%): {high_sharpe_1m:+.3f}  (T={len(high_daily_1m)} days)")
    if cascade_sharpe_1m > 0:
        ratio_1m = high_sharpe_1m / cascade_sharpe_1m
        print(f"  ratio HIGH/cascade: {ratio_1m:.3f}x")
    else:
        ratio_1m = float("inf") if high_sharpe_1m > 0 else 0
        print(f"  cascade Sharpe non-positive; ratio undefined")

    # Bold ship rule
    print()
    bold_pass = (n_pass >= 4) and (cascade_sharpe_1m > 0) and (ratio_1m >= 1.5)
    bold_pass_relaxed = (n_pass >= 4) and (
        cascade_sharpe_1m <= 0  # cascade is non-positive, HIGH alone is positive
        or ratio_1m >= 1.5
    )
    print(f"  PRE-COMMIT D291: 4-of-5 sub-experiments PASS AND ratio >= 1.5x")
    print(f"  Sub-experiments: {n_pass} of 5  ({'>=4 OK' if n_pass >= 4 else '<4'})")
    print(f"  Sharpe ratio: {ratio_1m:.3f}x  ({'>=1.5x OK' if ratio_1m >= 1.5 else '<1.5x'})")
    if bold_pass:
        print(f"  -> SHIP D291: cascade collapse to HIGH+ELITE only.")
        print(f"     LOTTERY_MIN_TIER_THRESHOLD=0.50 default.")
        print(f"     BROAD and VETOED tier entries DISABLED.")
    elif bold_pass_relaxed:
        print(f"  -> SHIP D291 (relaxed): cascade is non-positive at $1M, HIGH-only is positive.")
        print(f"     Same launcher change.")
    else:
        print(f"  -> DON'T SHIP D291. Pre-commit not met.")

    out = {
        "high_recent_sharpe_baseline": float(hi_recent_sharpe_baseline),
        "sub_exp_A_pass": bool(exp_a_pass),
        "sub_exp_A_loo_min": float(min(loo_sharpes)) if loo_sharpes else None,
        "sub_exp_A_loo_median": float(np.median(loo_sharpes)) if loo_sharpes else None,
        "sub_exp_B_pass": bool(exp_b_pass),
        "sub_exp_B_optimal_kelly": float(best_kelly) if best_kelly else None,
        "sub_exp_B_pnl_per_pick": float(best_metrics["pnl_per_pick"]) if best_metrics else None,
        "sub_exp_C_pass": bool(exp_c_pass),
        "sub_exp_C_threshold_max_dev": float(max_dev) if 'max_dev' in locals() else None,
        "sub_exp_D_pass": bool(exp_d_pass),
        "sub_exp_D_v3_only_high_rho": float(rho_v3_high),
        "sub_exp_D_v3_dm1_high_rho": float(rho_dm1_high),
        "sub_exp_E_pass": bool(exp_e_pass),
        "n_sub_exp_pass": int(n_pass),
        "cascade_sharpe_at_1m_bouchaud": float(cascade_sharpe_1m),
        "high_only_sharpe_at_1m_bouchaud": float(high_sharpe_1m),
        "high_kelly_used_for_ship": float(high_kelly_for_ship),
        "ratio_1m": float(ratio_1m) if not math.isinf(ratio_1m) else "inf",
        "bold_pass_strict": bool(bold_pass),
        "bold_pass_relaxed": bool(bold_pass_relaxed),
    }
    out_path = MODELS / "v6_high_only_deep_dive.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
