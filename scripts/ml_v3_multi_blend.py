"""Multi-v3-model blend experiment (architectural diversification).

Per s121 conclusion: more features won't help; architectural changes might.
This is the cheapest architectural diversification: blend predictions
from multiple v3-flavor models that already exist.

Models in the blend:
  v3            — default v3 (no Optuna tuning)
  v3_tuned_6f   — Optuna tuned on 6 most-recent folds (s107)
  v3_tuned_16f  — Optuna tuned on full 16 folds (s109; PRODUCTION)
  v3_temp       — v3_tuned_16f + post-training T-scaling (s123)

Three blend strategies tested:
  MEAN     — simple arithmetic mean of probabilities
  MEDIAN   — robust to outlier model predictions
  TRIMMED  — drop highest+lowest, average middle (n>=3)

For each blend, evaluate at the meta-scorer tier waterfall (HI|MID gate)
and compare $-PNL vs production v3_tuned_16f baseline.

Verdict: SHIP best blend if WF lift > $500 AND no tier regression > 30%.

OUTPUT:
  data/polygon_warehouse/derived/ml_v3_blend_predictions.parquet
  data/models/v3_blend_summary.json
  Console: per-blend tier P&L + verdict
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

# Aggressive Kelly caps + per-tier expected win/loss (s109 frozen)
KELLY_CAPS = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}
BANKROLL = 10_000.0


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_v3_variants() -> pd.DataFrame:
    """Load all v3-flavor predictions joined on (d0, ticker)."""
    paths = {
        "v3":           DERIVED / "ml_v2_walkforward_predictions_v3.parquet",
        "v3_tuned_6f":  DERIVED / "ml_v2_walkforward_predictions_v3_tuned.parquet",
        "v3_tuned_16f": DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet",
        "v3_temp":      DERIVED / "ml_v2_walkforward_predictions_v3_temp.parquet",
    }
    base = None
    for name, p in paths.items():
        if not p.exists():
            print(f"  WARN: {p.name} missing — skip {name}")
            continue
        df = pd.read_parquet(p)
        df["d0"] = pd.to_datetime(df["d0"])
        # The temp parquet uses prob_continuer_calibrated; prefer it
        if name == "v3_temp" and "prob_continuer_calibrated" in df.columns:
            df = df[["d0", "ticker", "y_cls", "y_reg", "prob_continuer_calibrated"]].copy()
            df = df.rename(columns={"prob_continuer_calibrated": f"p_{name}"})
        else:
            df = df[["d0", "ticker", "y_cls", "y_reg", "prob_continuer"]].copy()
            df = df.rename(columns={"prob_continuer": f"p_{name}"})
        if base is None:
            base = df
        else:
            # Drop y_cls/y_reg from secondary frames to avoid suffixed dupes
            base = base.merge(df[["d0", "ticker", f"p_{name}"]],
                                 on=["d0", "ticker"], how="inner")
    return base


def kelly_for_row(p: float, conformal_w: float, ew: float, el: float,
                    cap: float) -> float:
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0: return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    width_mod = float(np.exp(-2 * conformal_w))
    return float(min(f_star * width_mod, cap))


def evaluate_blend(df: pd.DataFrame, prob_col: str) -> dict:
    """Evaluate $-PNL with the meta-scorer tier waterfall on a probability col.

    Mimics ml_meta_scorer's pipeline. Uses default conformal width 0.5 (no
    per-fold conformal; this experiment isolates the blend effect).
    """
    yreg = df["y_reg"].clip(-0.5, 1.0)
    prob = df[prob_col]
    mag = df["mag_label"]

    is_hi_mid = mag.isin(["HI", "MID"])
    is_mid = mag == "MID"

    elite = (prob >= 0.60) & is_hi_mid
    high = (prob >= 0.50) & (prob < 0.60) & is_hi_mid
    # VETOED rule D: v3>=0.30 + MID + intra<p25 (intra not in this df; skip)
    # For this experiment use rule E proxy: any v3 in [0.30, 0.50) on MID
    vetoed = (prob >= 0.30) & (prob < 0.50) & is_mid
    broad = (prob >= 0.30) & (prob < 0.50) & is_hi_mid & ~vetoed

    tier_results = {}
    total_pnl = 0.0
    for tier, mask in [("ELITE", elite), ("HIGH", high),
                         ("VETOED", vetoed), ("BROAD", broad)]:
        n = int(mask.sum())
        if n == 0:
            tier_results[tier] = {"n": 0, "avg_pct": 0.0, "win_pct": 0.0,
                                    "kelly_pnl": 0.0}
            continue
        avg = float(yreg[mask].mean() * 100)
        win = float((yreg[mask] > 0).mean() * 100)
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        kellies = [kelly_for_row(p, 0.5, ew, el, cap) for p in prob[mask]]
        pnl = float(sum(BANKROLL * k * y for k, y in zip(kellies, yreg[mask])))
        total_pnl += pnl
        tier_results[tier] = {"n": n, "avg_pct": avg, "win_pct": win,
                                "kelly_pnl": pnl}
    tier_results["TOTAL"] = {"n": int((elite | high | vetoed | broad).sum()),
                                "kelly_pnl": total_pnl}
    return tier_results


def main():
    section("STEP 1 - Load v3 variants + Ising mag")
    df = load_v3_variants()
    if df is None or len(df) == 0:
        print("  ERROR: no v3 prediction parquets found")
        return 1
    pred_cols = [c for c in df.columns if c.startswith("p_")]
    print(f"  loaded {len(df):,} rows joined on (d0, ticker) across {len(pred_cols)} variants:")
    for c in pred_cols:
        print(f"    {c}: mean={df[c].mean():.3f}, std={df[c].std():.3f}")

    # Join Ising mag_label
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
    df = df.merge(mag_df, on="d0", how="left")
    print(f"  joined Ising: {df['mag_label'].notna().sum()}/{len(df)} have mag_label")

    section("STEP 2 - Build blends")
    df["p_blend_mean"] = df[pred_cols].mean(axis=1)
    df["p_blend_median"] = df[pred_cols].median(axis=1)
    if len(pred_cols) >= 3:
        # Trimmed: drop high + low, average middle
        sorted_p = df[pred_cols].apply(lambda r: sorted(r.tolist()), axis=1, result_type="expand")
        # Keep all but first and last column
        df["p_blend_trimmed"] = sorted_p.iloc[:, 1:-1].mean(axis=1)
    blends = ["p_blend_mean", "p_blend_median"]
    if "p_blend_trimmed" in df.columns:
        blends.append("p_blend_trimmed")
    print(f"  built {len(blends)} blends from {len(pred_cols)} variants")

    section("STEP 3 - Per-tier evaluation: each variant vs each blend")
    print(f"  {'variant':<22} {'ELITE':>10} {'HIGH':>10} {'VETOED':>10} {'BROAD':>10} {'TOTAL':>11}")
    print(f"  {'-'*78}")
    results = {}
    baseline_total = 0.0
    for col in pred_cols + blends:
        r = evaluate_blend(df, col)
        results[col] = r
        is_baseline = col == "p_v3_tuned_16f"
        marker = " *" if is_baseline else "  "
        print(f"  {col:<22}{marker} ${r.get('ELITE', {}).get('kelly_pnl', 0):>+8.0f} "
              f"${r.get('HIGH', {}).get('kelly_pnl', 0):>+8.0f} "
              f"${r.get('VETOED', {}).get('kelly_pnl', 0):>+8.0f} "
              f"${r.get('BROAD', {}).get('kelly_pnl', 0):>+8.0f} "
              f"${r['TOTAL']['kelly_pnl']:>+10.0f}")
        if is_baseline:
            baseline_total = r["TOTAL"]["kelly_pnl"]

    section("STEP 4 - Verdict")
    print(f"  Baseline (v3_tuned_16f): $+{baseline_total:.2f}")
    print()
    # Find best blend
    best = None
    best_pnl = baseline_total
    for col in blends:
        pnl = results[col]["TOTAL"]["kelly_pnl"]
        delta = pnl - baseline_total
        marker = " *** SHIP" if delta > 500 else ("  +" if delta > 0 else "  -")
        print(f"  {col:<22} {marker} ${pnl:+,.2f}  delta=${delta:+.2f}")
        if pnl > best_pnl:
            best_pnl = pnl
            best = col
    print()
    if best:
        delta_pct = (best_pnl - baseline_total) / baseline_total * 100 if baseline_total else 0
        print(f"  WINNER: {best}  +${best_pnl - baseline_total:.2f} ({delta_pct:+.1f}%)")
    else:
        print(f"  No blend beats baseline. HOLD v3_tuned_16f.")

    section("STEP 5 - Persist")
    out = DERIVED / "ml_v3_blend_predictions.parquet"
    df.to_parquet(out, compression="zstd")
    print(f"  Wrote {out}")

    summary = {
        "n_rows": len(df),
        "variants": pred_cols,
        "blends": blends,
        "per_variant_pnl": {c: results[c]["TOTAL"]["kelly_pnl"]
                              for c in pred_cols + blends},
        "baseline_total": baseline_total,
        "best_blend": best,
        "best_blend_pnl": best_pnl,
        "delta_dollars": best_pnl - baseline_total,
    }
    out_path = MODELS / "v3_blend_summary.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
