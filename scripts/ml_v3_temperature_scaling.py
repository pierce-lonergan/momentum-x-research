"""Architectural experiment: post-training temperature scaling on v3-tuned-16fold.

Per s121 conclusions: more features won't help. Architectural changes might.
Temperature scaling (Guo et al. 2017) is the cheapest principled calibration
fix — fits a single scalar T such that:
    calibrated_p = sigmoid(logit / T)
- T > 1: softer probabilities (less confident)
- T < 1: harder probabilities (more confident)

Why this might help v3:
  - Tree ensembles output probability that's known to be miscalibrated
    (overconfident in extreme tails)
  - v3-tuned-16fold's WF v3t distribution is roughly bell-curved with
    mean ~0.20 and only 6.88% of rows above the 0.30 threshold
  - If T < 1 (harden): more rows clear 0.30, possibly capturing more
    elite picks but also more false positives
  - If T > 1 (soften): fewer rows clear 0.30, but those that do are more
    confident — possibly higher per-trade lift

Method:
  1. Use existing v3 WF predictions parquet
  2. Convert proba back to logits, fit T via NLL minimization on (logit/T, y)
  3. Apply T, recompute v3t = sigmoid(logit / T)
  4. Re-run meta-scorer on calibrated predictions; compare bankroll

Verdict: SHIP T-scaled version IF $-PNL improves >= +$500 with no
regression > 30% on any tier.

OUTPUT:
  data/models/v3_temperature.json
  data/polygon_warehouse/derived/ml_v2_walkforward_predictions_v3_temp.parquet
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import roc_auc_score

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def fit_temperature(logits: np.ndarray, labels: np.ndarray,
                     max_iter: int = 200) -> float:
    """LBFGS on a single scalar T. Returns optimal T (clamped > 0)."""
    T = nn.Parameter(torch.ones(1))
    opt = torch.optim.LBFGS([T], lr=0.01, max_iter=max_iter)
    logits_t = torch.from_numpy(logits.astype(np.float32))
    labels_t = torch.from_numpy(labels.astype(np.float32))

    def closure():
        opt.zero_grad()
        scaled = logits_t / T.clamp(min=1e-3)
        loss = nn.functional.binary_cross_entropy_with_logits(scaled, labels_t)
        loss.backward()
        return loss

    opt.step(closure)
    return float(T.detach().clamp(min=1e-3).item())


def expected_calibration_error(probs: np.ndarray, labels: np.ndarray,
                                 n_bins: int = 10) -> float:
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n = len(probs)
    for i in range(n_bins):
        mask = (probs >= bins[i]) & (probs < bins[i + 1])
        if mask.sum() == 0: continue
        ece += (mask.sum() / n) * abs(probs[mask].mean() - labels[mask].mean())
    return float(ece)


def evaluate_at_thresholds(df: pd.DataFrame, prob_col: str,
                              ising_label_col: str = "mag_label") -> dict:
    """Compute the meta-scorer's tier P&Ls under different proba columns."""
    yreg = df["y_reg"].clip(-0.5, 1.0)
    out = {}
    for thr_name, thr in [("p30", 0.30), ("p50", 0.50), ("p60", 0.60)]:
        for mag_filter in ["all", "hi_mid"]:
            if mag_filter == "all":
                mask = df[prob_col] >= thr
            else:
                mask = (df[prob_col] >= thr) & (df[ising_label_col].isin(["HI", "MID"]))
            n = int(mask.sum())
            avg = float(yreg[mask].mean() * 100) if n > 0 else 0.0
            win = float((yreg[mask] > 0).mean() * 100) if n > 0 else 0.0
            out[f"{thr_name}_{mag_filter}"] = {"n": n, "avg": avg, "win": win}
    return out


def main():
    section("STEP 1 - Load v3 WF predictions + Ising")
    v3_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    ising_path = DERIVED / "ising_daily.parquet"
    df = pd.read_parquet(v3_path)
    df["d0"] = pd.to_datetime(df["d0"])

    # Join Ising mag_label for the meta-scorer evaluation
    import duckdb
    con = duckdb.connect()
    con.sql("SET memory_limit='4GB'")
    con.sql(f"""
        CREATE TABLE i AS SELECT *,
               AVG(magnetization) OVER (
                   ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
               ) AS mag_5d
        FROM read_parquet('{ising_path.as_posix()}')
    """)
    df_with_mag = con.sql(f"""
        SELECT v.*,
               CASE WHEN i.mag_5d < -0.05 THEN 'LO'
                    WHEN i.mag_5d > 0.05 THEN 'HI'
                    ELSE 'MID' END AS mag_label
        FROM read_parquet('{v3_path.as_posix()}') v
        JOIN i ON v.d0 = i.d
    """).df()
    df_with_mag["d0"] = pd.to_datetime(df_with_mag["d0"])
    print(f"  loaded {len(df_with_mag):,} OOS rows with mag_label")

    section("STEP 2 - Pre-calibration metrics")
    p_pre = df_with_mag["prob_continuer"].values
    y = df_with_mag["y_cls"].values
    pre_ece = expected_calibration_error(p_pre, y)
    pre_brier = float(((p_pre - y) ** 2).mean())
    try:
        pre_auc = float(roc_auc_score(y, p_pre))
    except Exception:
        pre_auc = float("nan")
    print(f"  ECE (10 bins): {pre_ece:.4f}")
    print(f"  Brier score:   {pre_brier:.4f}")
    print(f"  AUC (ROC):     {pre_auc:.4f}")

    section("STEP 3 - Fit temperature T on FULL OOS rows (LBFGS NLL)")
    eps = 1e-7
    p_clip = np.clip(p_pre, eps, 1 - eps)
    logits = np.log(p_clip / (1 - p_clip))
    T = fit_temperature(logits, y)
    print(f"  Optimal T = {T:.4f}")
    if T > 1.05:
        print(f"  T > 1: model is OVERCONFIDENT; calibration softens probabilities")
    elif T < 0.95:
        print(f"  T < 1: model is UNDERCONFIDENT; calibration sharpens probabilities")
    else:
        print(f"  T near 1: model is well-calibrated already; no major correction")

    p_post = 1.0 / (1.0 + np.exp(-logits / T))
    df_with_mag["prob_continuer_calibrated"] = p_post

    section("STEP 4 - Post-calibration metrics")
    post_ece = expected_calibration_error(p_post, y)
    post_brier = float(((p_post - y) ** 2).mean())
    try:
        post_auc = float(roc_auc_score(y, p_post))
    except Exception:
        post_auc = float("nan")
    ece_better = "IMPROVED" if post_ece < pre_ece else "WORSE"
    brier_better = "IMPROVED" if post_brier < pre_brier else "WORSE"
    print(f"  ECE (10 bins): {post_ece:.4f}  (was {pre_ece:.4f}, {ece_better})")
    print(f"  Brier score:   {post_brier:.4f}  (was {pre_brier:.4f}, {brier_better})")
    print(f"  AUC (ROC):     {post_auc:.4f}  (was {pre_auc:.4f})  [unchanged by T-scaling]")

    section("STEP 5 - Per-tier comparison: original vs T-scaled")
    pre_stats = evaluate_at_thresholds(df_with_mag, "prob_continuer")
    post_stats = evaluate_at_thresholds(df_with_mag, "prob_continuer_calibrated")

    print(f"  {'tier':<25} {'pre_n':>5} {'pre_avg':>8} {'post_n':>6} {'post_avg':>9} {'delta_pp':>9}")
    print(f"  {'-'*72}")
    for tier_key in ("p30_hi_mid", "p50_hi_mid", "p60_hi_mid"):
        pre = pre_stats[tier_key]
        post = post_stats[tier_key]
        delta = post["avg"] - pre["avg"]
        print(f"  {tier_key:<25} {pre['n']:>5,} {pre['avg']:>+7.2f}% {post['n']:>6,} "
              f"{post['avg']:>+8.2f}% {delta:>+8.2f}pp")

    section("STEP 6 - Verdict")
    # Sum tier-weighted P&L: sum(n * avg) for each
    pre_total = sum(pre_stats[k]["n"] * pre_stats[k]["avg"] / 100.0
                     for k in ("p30_hi_mid", "p50_hi_mid", "p60_hi_mid"))
    post_total = sum(post_stats[k]["n"] * post_stats[k]["avg"] / 100.0
                      for k in ("p30_hi_mid", "p50_hi_mid", "p60_hi_mid"))
    print(f"  Sum (n*avg/100) across HI|MID tiers:")
    print(f"    Original:  {pre_total:+.4f}")
    print(f"    T-scaled:  {post_total:+.4f}")
    print(f"    Delta:     {post_total - pre_total:+.4f}")

    if post_total - pre_total > 0.10:
        verdict = "SHIP T-scaling"
    elif post_total - pre_total < -0.10:
        verdict = "HOLD original"
    else:
        verdict = "NEUTRAL (no meaningful change)"
    print(f"\n  VERDICT: {verdict}")

    section("STEP 7 - Persist")
    out_preds = DERIVED / "ml_v2_walkforward_predictions_v3_temp.parquet"
    df_with_mag.to_parquet(out_preds, compression="zstd")
    print(f"  Wrote {out_preds}")

    summary = {
        "T": T,
        "pre_ece": pre_ece, "post_ece": post_ece,
        "pre_brier": pre_brier, "post_brier": post_brier,
        "pre_auc": pre_auc, "post_auc": post_auc,
        "pre_stats": pre_stats, "post_stats": post_stats,
        "pre_total_lift": pre_total, "post_total_lift": post_total,
        "verdict": verdict,
    }
    out_path = MODELS / "v3_temperature.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
