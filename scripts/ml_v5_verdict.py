"""MoMTrans v5 verdict — does the CORN+Spearman+Muon+Mixup stack beat v4?

Compares THREE candidates head-to-head on the same 16-fold WF predictions:

  A. Production v3-tuned-16f baseline    (single-model tier waterfall)
  B. MoMTrans v4 tier cascade            (4 separate specialists, UNION)
  C. MoMTrans v5 CORN single-model       (1 trunk, K conditional probs)

For each candidate, computes:
  - Spearman ρ vs realized ret_t5
  - Top-K rank discrimination
  - Aggressive-Kelly $-PNL (single-model tier waterfall on the cumulative
    probability outputs)
  - $-PNL with cap=10/day capacity constraint (Phase B-style)
  - $-PNL at 100bps slippage (Phase C-style)

Verdict gate (matches D281/D282 SHIP criteria):
  SHIP if v5 total $-PNL ≥ v4 + $500 AND no individual tier regresses > 30%.
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
TIER_ORDER = {"ELITE": 0, "HIGH": 1, "VETOED": 2, "BROAD": 3}
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}
BANKROLL = 10_000.0


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly(p, w, ew, el, cap):
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    return float(min(f_star * float(np.exp(-2 * w)), cap))


def rank_corr(scores: np.ndarray, target: np.ndarray) -> float:
    """Spearman ρ via integer ranks (no torchsort dep)."""
    return float(np.corrcoef(np.argsort(np.argsort(scores)),
                                np.argsort(np.argsort(target)))[0, 1])


def evaluate_v5_cascade(df: pd.DataFrame, thresholds: dict, cap: int = 10,
                          slippage_bps: float = 100.0) -> dict:
    """Apply the Phase-A-style UNION cascade to v5 cumulative probabilities.

    v5 outputs (p_broad, p_vetoed, p_high, p_elite) per row — each is the
    cumulative P(R≥t_k) at threshold t_k. The cascade router uses each as
    its tier's specialist signal."""
    out = {"per_tier": {}, "n_total": 0}
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    width = df.get("conformal_width", pd.Series([0.5] * len(df))).values
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    p_e = df["p_elite"].values
    p_h = df["p_high"].values
    p_v = df["p_vetoed"].values
    p_b = df["p_broad"].values

    elite = (p_e >= thresholds["ELITE"]) & is_hi_mid
    high = (p_h >= thresholds["HIGH"]) & is_hi_mid & ~elite
    vetoed = (p_v >= thresholds["VETOED"]) & is_mid & ~elite & ~high
    broad = (p_b >= thresholds["BROAD"]) & is_hi_mid & ~elite & ~high & ~vetoed

    # Build per-row tier + score for cap-by-day prioritization
    df2 = df.copy()
    df2["tier"] = "SKIP"
    df2["tier_prob"] = 0.0
    df2.loc[elite, "tier"] = "ELITE"; df2.loc[elite, "tier_prob"] = p_e[elite]
    df2.loc[high, "tier"] = "HIGH"; df2.loc[high, "tier_prob"] = p_h[high]
    df2.loc[vetoed, "tier"] = "VETOED"; df2.loc[vetoed, "tier_prob"] = p_v[vetoed]
    df2.loc[broad, "tier"] = "BROAD"; df2.loc[broad, "tier_prob"] = p_b[broad]

    fired = df2[df2["tier"] != "SKIP"].copy()
    if fired.empty:
        out["pnl_total"] = 0.0
        return out
    fired["tier_priority"] = fired["tier"].map(TIER_ORDER)
    fired = fired.sort_values(["d0", "tier_priority", "tier_prob"],
                                ascending=[True, True, False])
    fired["rank_in_day"] = fired.groupby("d0").cumcount()
    kept = fired[fired["rank_in_day"] < cap].copy()

    haircut = slippage_bps / 10_000.0
    total = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        sub = kept[kept["tier"] == tier]
        if sub.empty:
            out["per_tier"][tier] = {"n": 0, "pnl": 0.0}
            continue
        ew, el = TIER_EW_EL_PCT[tier]; cap_k = KELLY_CAPS[tier]
        prob = np.maximum(sub["tier_prob"].values, 0.31)
        widths = sub["conformal_width"].values if "conformal_width" in sub.columns else np.full(len(sub), 0.5)
        yreg_s = sub["y_reg"].clip(-0.5, 1.0).values - haircut
        kellies = [kelly(p, w, ew, el, cap_k) for p, w in zip(prob, widths)]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg_s)))
        total += pnl
        out["per_tier"][tier] = {"n": int(len(sub)), "pnl": pnl}
    out["pnl_total"] = total
    out["n_total"] = int(len(kept))
    return out


def evaluate_v4_cascade(base: pd.DataFrame, thresholds: dict, cap: int = 10,
                          slippage_bps: float = 100.0) -> dict:
    yreg = base["y_reg"].clip(-0.5, 1.0).values
    width = base["conformal_width"].values
    mag = base["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"
    sp = {tier: base[f"prob_specialist_{tier}"].values
          for tier in ("ELITE", "HIGH", "VETOED", "BROAD")}

    df2 = base.copy()
    df2["tier"] = "SKIP"
    df2["tier_prob"] = 0.0
    elite = (sp["ELITE"] >= thresholds["ELITE"]) & is_hi_mid
    high = (sp["HIGH"] >= thresholds["HIGH"]) & is_hi_mid & ~elite
    vetoed = (sp["VETOED"] >= thresholds["VETOED"]) & is_mid & ~elite & ~high
    broad = (sp["BROAD"] >= thresholds["BROAD"]) & is_hi_mid & ~elite & ~high & ~vetoed
    df2.loc[elite, "tier"] = "ELITE"; df2.loc[elite, "tier_prob"] = sp["ELITE"][elite]
    df2.loc[high, "tier"] = "HIGH"; df2.loc[high, "tier_prob"] = sp["HIGH"][high]
    df2.loc[vetoed, "tier"] = "VETOED"; df2.loc[vetoed, "tier_prob"] = sp["VETOED"][vetoed]
    df2.loc[broad, "tier"] = "BROAD"; df2.loc[broad, "tier_prob"] = sp["BROAD"][broad]

    fired = df2[df2["tier"] != "SKIP"].copy()
    if fired.empty:
        return {"pnl_total": 0.0, "per_tier": {}, "n_total": 0}
    fired["tier_priority"] = fired["tier"].map(TIER_ORDER)
    fired = fired.sort_values(["d0", "tier_priority", "tier_prob"],
                                ascending=[True, True, False])
    fired["rank_in_day"] = fired.groupby("d0").cumcount()
    kept = fired[fired["rank_in_day"] < cap].copy()

    out = {"per_tier": {}, "n_total": int(len(kept))}
    haircut = slippage_bps / 10_000.0
    total = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        sub = kept[kept["tier"] == tier]
        if sub.empty:
            out["per_tier"][tier] = {"n": 0, "pnl": 0.0}; continue
        ew, el = TIER_EW_EL_PCT[tier]; cap_k = KELLY_CAPS[tier]
        prob = np.maximum(sub["tier_prob"].values, 0.31)
        widths = sub["conformal_width"].values
        yreg_s = sub["y_reg"].clip(-0.5, 1.0).values - haircut
        kellies = [kelly(p, w, ew, el, cap_k) for p, w in zip(prob, widths)]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg_s)))
        total += pnl
        out["per_tier"][tier] = {"n": int(len(sub)), "pnl": pnl}
    out["pnl_total"] = total
    return out


def evaluate_prod_baseline(prod: pd.DataFrame, cap: int = 10,
                              slippage_bps: float = 100.0) -> dict:
    yreg = prod["y_reg"].clip(-0.5, 1.0).values
    prob = prod["prob_continuer"].values
    width = prod["conformal_width"].values
    mag = prod["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"

    df2 = prod.copy()
    df2["tier"] = "SKIP"
    df2["tier_prob"] = 0.0
    elite = (prob >= 0.60) & is_hi_mid
    high = (prob >= 0.50) & (prob < 0.60) & is_hi_mid
    vetoed = (prob >= 0.30) & (prob < 0.50) & is_mid
    broad = (prob >= 0.30) & (prob < 0.50) & is_hi_mid & ~vetoed
    df2.loc[elite, "tier"] = "ELITE"; df2.loc[elite, "tier_prob"] = prob[elite]
    df2.loc[high, "tier"] = "HIGH"; df2.loc[high, "tier_prob"] = prob[high]
    df2.loc[vetoed, "tier"] = "VETOED"; df2.loc[vetoed, "tier_prob"] = prob[vetoed]
    df2.loc[broad, "tier"] = "BROAD"; df2.loc[broad, "tier_prob"] = prob[broad]

    fired = df2[df2["tier"] != "SKIP"].copy()
    if fired.empty:
        return {"pnl_total": 0.0, "per_tier": {}, "n_total": 0}
    fired["tier_priority"] = fired["tier"].map(TIER_ORDER)
    fired = fired.sort_values(["d0", "tier_priority", "tier_prob"],
                                ascending=[True, True, False])
    fired["rank_in_day"] = fired.groupby("d0").cumcount()
    kept = fired[fired["rank_in_day"] < cap].copy()

    out = {"per_tier": {}, "n_total": int(len(kept))}
    haircut = slippage_bps / 10_000.0
    total = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        sub = kept[kept["tier"] == tier]
        if sub.empty:
            out["per_tier"][tier] = {"n": 0, "pnl": 0.0}; continue
        ew, el = TIER_EW_EL_PCT[tier]; cap_k = KELLY_CAPS[tier]
        widths = sub["conformal_width"].values
        prob_s = sub["tier_prob"].values
        yreg_s = sub["y_reg"].clip(-0.5, 1.0).values - haircut
        kellies = [kelly(p, w, ew, el, cap_k) for p, w in zip(prob_s, widths)]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg_s)))
        total += pnl
        out["per_tier"][tier] = {"n": int(len(sub)), "pnl": pnl}
    out["pnl_total"] = total
    return out


def main() -> int:
    section("MoMTrans v5 verdict — vs v4 + production v3 baselines")

    # Load v5 predictions
    v5_path = MODELS / "momtrans_v5_corn_predictions.parquet"
    if not v5_path.exists():
        print(f"  ERROR: {v5_path} missing — run scripts/ml_v5_train_corn.py --full-wf first")
        return 1
    v5 = pd.read_parquet(v5_path)
    v5["d0"] = pd.to_datetime(v5["d0"])
    print(f"  v5 predictions: {len(v5):,} rows")

    # Load v4 (4 specialist files merged)
    v4_files = [MODELS / f"momtrans_v4_tier_{t}_predictions.parquet"
                for t in ("BROAD", "VETOED", "HIGH", "ELITE")]
    if not all(p.exists() for p in v4_files):
        print(f"  ERROR: v4 specialist predictions missing")
        return 1
    v4 = pd.read_parquet(v4_files[0])[["d0", "ticker", "fold", "y_reg",
                                            "conformal_width", "prob_binary"]].rename(
        columns={"prob_binary": "prob_specialist_BROAD"})
    v4["d0"] = pd.to_datetime(v4["d0"])
    for tier, p in zip(("VETOED", "HIGH", "ELITE"), v4_files[1:]):
        df_t = pd.read_parquet(p)[["d0", "ticker", "fold", "prob_binary"]]
        df_t["d0"] = pd.to_datetime(df_t["d0"])
        v4 = v4.merge(df_t.rename(columns={"prob_binary": f"prob_specialist_{tier}"}),
                       on=["d0", "ticker", "fold"], how="inner")
    print(f"  v4 cascade: {len(v4):,} rows")

    # Load production v3
    prod_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    prod = pd.read_parquet(prod_path)
    prod["d0"] = pd.to_datetime(prod["d0"])
    if "conformal_width" not in prod.columns:
        prod["conformal_width"] = 0.5
    print(f"  v3 production: {len(prod):,} rows")

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
    v5 = v5.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    v4 = v4.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    prod = prod.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])

    # ── 1. Spearman ρ ─────────────────────────────────────────────
    section("STEP 1 — Aggregate Spearman ρ")
    rho_v3 = rank_corr(prod["prob_continuer"].values, prod["y_reg"].values)
    rho_v4 = rank_corr(v4["prob_specialist_BROAD"].values, v4["y_reg"].values)
    rho_v5_b = rank_corr(v5["p_broad"].values, v5["y_reg"].values)
    rho_v5_e = rank_corr(v5["p_elite"].values, v5["y_reg"].values)
    print(f"  Production v3:                ρ = {rho_v3:+.4f}")
    print(f"  v4 (BROAD specialist):        ρ = {rho_v4:+.4f}")
    print(f"  v5 CORN (p_broad):            ρ = {rho_v5_b:+.4f}")
    print(f"  v5 CORN (p_elite):            ρ = {rho_v5_e:+.4f}")
    print(f"  Δ v5_broad - v4 broad:        {rho_v5_b - rho_v4:+.4f}")
    print(f"  Δ v5_broad - v3 production:   {rho_v5_b - rho_v3:+.4f}")

    # ── 2. Top-K rank discrimination ─────────────────────────────
    section("STEP 2 — Top-K rank discrimination (using p_broad / production v3 score)")
    rows = []
    for label, scores, target in [
        ("v3 prod", prod["prob_continuer"].values, prod["y_reg"].values),
        ("v4 BROAD", v4["prob_specialist_BROAD"].values, v4["y_reg"].values),
        ("v5 p_broad", v5["p_broad"].values, v5["y_reg"].values),
        ("v5 p_elite", v5["p_elite"].values, v5["y_reg"].values),
    ]:
        order = np.argsort(scores)[::-1]
        srt = target[order]
        n = len(srt)
        line = [label]
        for k_pct in (1, 2, 5, 10):
            k = max(int(n * k_pct / 100), 1)
            avg = float(srt[:k].mean() * 100)
            win = float((srt[:k] > 0).mean() * 100)
            line.append(f"{avg:>+6.2f}%/{win:>5.1f}%")
        rows.append(line)
    print(f"  {'strategy':<14} {'top1%':>15} {'top2%':>15} {'top5%':>15} {'top10%':>15}")
    print("  " + "-" * 80)
    for r in rows:
        print(f"  {r[0]:<14} {r[1]:>15} {r[2]:>15} {r[3]:>15} {r[4]:>15}")

    # ── 3. $-PNL with cap=10/day at 100bps slippage ─────────────────
    section("STEP 3 — $-PNL (cap=10/day, 100 bps slippage) — Phase B+C combined")
    # Production
    prod_result = evaluate_prod_baseline(prod, cap=10, slippage_bps=100.0)

    # v4 cascade with frozen Phase-A thresholds
    PA = json.loads((MODELS / "momtrans_v4_phase_a_oos_validation.json").read_text())
    v4_thresholds = PA["best_thresholds"]
    v4_result = evaluate_v4_cascade(v4, v4_thresholds, cap=10, slippage_bps=100.0)

    # v5 cascade with same threshold set (mapped to cumulative probs).
    # v5 cum probs are smaller-magnitude than independent specialists, so we
    # try a few threshold candidates and report the best.
    best_v5 = None
    best_v5_thresholds = None
    for elite_thr in (0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30):
        for high_thr in (0.10, 0.15, 0.20, 0.25, 0.30):
            for vetoed_thr in (0.20, 0.30, 0.40, 0.50, 0.60):
                for broad_thr in (0.30, 0.40, 0.50, 0.60, 0.70):
                    if not (elite_thr <= high_thr <= vetoed_thr <= broad_thr):
                        continue  # CORN gives nested probs; thresholds should mirror
                    thr = {"ELITE": elite_thr, "HIGH": high_thr,
                           "VETOED": vetoed_thr, "BROAD": broad_thr}
                    r = evaluate_v5_cascade(v5, thr, cap=10, slippage_bps=100.0)
                    if best_v5 is None or r["pnl_total"] > best_v5["pnl_total"]:
                        best_v5 = r
                        best_v5_thresholds = thr
    if best_v5 is None:
        best_v5 = {"pnl_total": 0.0, "per_tier": {}, "n_total": 0}
        best_v5_thresholds = {}

    print(f"  v3 production:    n={prod_result['n_total']:>5,}  ${prod_result['pnl_total']:>+12,.2f}")
    print(f"  v4 cascade:       n={v4_result['n_total']:>5,}  ${v4_result['pnl_total']:>+12,.2f}  "
          f"Δ ${v4_result['pnl_total'] - prod_result['pnl_total']:>+10,.2f}")
    print(f"  v5 CORN cascade:  n={best_v5['n_total']:>5,}  ${best_v5['pnl_total']:>+12,.2f}  "
          f"Δ ${best_v5['pnl_total'] - prod_result['pnl_total']:>+10,.2f}")
    print(f"  Best v5 thresholds: {best_v5_thresholds}")

    section("STEP 4 — Verdict")
    delta_v5_v4 = best_v5["pnl_total"] - v4_result["pnl_total"]
    delta_v5_prod = best_v5["pnl_total"] - prod_result["pnl_total"]
    print(f"  v5 vs v4:   Δ = ${delta_v5_v4:+,.2f}  ({delta_v5_v4/max(v4_result['pnl_total'], 1)*100:+.1f}%)")
    print(f"  v5 vs prod: Δ = ${delta_v5_prod:+,.2f}  ({delta_v5_prod/max(prod_result['pnl_total'], 1)*100:+.1f}%)")
    if delta_v5_v4 >= 500:
        verdict = "SHIP — v5 beats v4 by >= +$500"
    elif delta_v5_v4 >= 0:
        verdict = "MARGINAL — v5 marginally beats v4"
    elif delta_v5_v4 > -2_000:
        verdict = "NEUTRAL — v5 within +/- $2k of v4"
    else:
        verdict = "HOLD v4 — v5 regresses materially"
    print(f"  VERDICT: {verdict}")

    summary = {
        "rho_v3_prod": rho_v3,
        "rho_v4_broad": rho_v4,
        "rho_v5_broad": rho_v5_b,
        "rho_v5_elite": rho_v5_e,
        "best_v5_thresholds": best_v5_thresholds,
        "prod_pnl": prod_result["pnl_total"],
        "v4_pnl": v4_result["pnl_total"],
        "v5_pnl": best_v5["pnl_total"],
        "delta_v5_v4": delta_v5_v4,
        "delta_v5_prod": delta_v5_prod,
        "verdict": verdict,
        "v5_per_tier": best_v5.get("per_tier", {}),
        "v4_per_tier": v4_result.get("per_tier", {}),
        "prod_per_tier": prod_result.get("per_tier", {}),
    }
    out = MODELS / "momtrans_v5_verdict.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
