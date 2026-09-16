"""Post-training temperature scaling for the BCE-trained TCN.

Sessions 110-112 tried two focal-loss variants to fix TCN calibration; both
collapsed the v2-only veto bucket (n=244 -> n=6) because focal IN-TRAINING
upweights minority class so the model over-predicts positive.

This script tries the cleaner approach: keep the BCE TCN unchanged, fit a
SINGLE SCALAR temperature T post-hoc such that:
    calibrated_probas = sigmoid(logits / T)
Where T > 1 = "softer" probabilities (less confident), T < 1 = "harder".

Temperature scaling preserves the RANK ORDER of predictions — so the
v2-only veto bucket stays intact (whoever was predicted high stays high).
But it can fix the WIDTH of the distribution.

Setup (Guo et al. 2017 "On Calibration of Modern Neural Networks"):
  1. Use the WF-fold predictions as "validation set"
  2. Fit T by minimizing NLL on (logit/T, y) — single-param scalar optim
  3. Evaluate the calibrated WF predictions against the meta-scorer pipeline
  4. Compare bucket distributions vs uncalibrated baseline

OUTPUTS:
  data/models/tcn_temperature.json
  data/polygon_warehouse/derived/tcn_intraday_walkforward_predictions_calibrated.parquet
  Console: calibration metrics + tier-bucket distribution comparison
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def fit_temperature(logits: np.ndarray, labels: np.ndarray,
                     max_iter: int = 200, lr: float = 0.01) -> float:
    """Fit scalar temperature T via NLL minimization on (logit/T, y).

    Standard procedure: clamp T > 0 via softplus, minimize via L-BFGS or Adam.
    Returns the optimal T.
    """
    # Initialize T = 1.0 (no scaling)
    T = nn.Parameter(torch.ones(1))
    optimizer = torch.optim.LBFGS([T], lr=lr, max_iter=max_iter)
    logits_t = torch.from_numpy(logits.astype(np.float32))
    labels_t = torch.from_numpy(labels.astype(np.float32))

    def closure():
        optimizer.zero_grad()
        scaled = logits_t / T.clamp(min=1e-3)
        loss = nn.functional.binary_cross_entropy_with_logits(scaled, labels_t)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(T.detach().clamp(min=1e-3).item())


def expected_calibration_error(probs: np.ndarray, labels: np.ndarray,
                                 n_bins: int = 10) -> float:
    """ECE: weighted average of |confidence - accuracy| per bin."""
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n = len(probs)
    for i in range(n_bins):
        mask = (probs >= bins[i]) & (probs < bins[i + 1])
        if mask.sum() == 0: continue
        bin_conf = probs[mask].mean()
        bin_acc = labels[mask].mean()
        ece += (mask.sum() / n) * abs(bin_conf - bin_acc)
    return float(ece)


def main():
    section("STEP 1 - Load BCE TCN WF predictions + labels")
    preds_path = DERIVED / "tcn_intraday_walkforward_predictions.parquet"
    if not preds_path.exists():
        print(f"  ERROR: {preds_path} missing. Run scripts/ml_tcn_intraday.py first.")
        return 1
    df = pd.read_parquet(preds_path)
    print(f"  loaded {len(df):,} OOS WF predictions")
    print(f"  positive rate: {df['y_cls'].mean()*100:.2f}%")
    print(f"  proba mean: {df['tcn_proba'].mean():.4f}")
    print(f"  proba >= 0.30: {(df['tcn_proba'] >= 0.30).sum():,} ({(df['tcn_proba'] >= 0.30).mean()*100:.1f}%)")

    # Convert proba back to logit (since we don't have raw logits saved):
    # logit = log(p / (1-p))
    eps = 1e-7
    p = df["tcn_proba"].clip(eps, 1 - eps).values
    logits = np.log(p / (1 - p))
    labels = df["y_cls"].values

    section("STEP 2 - Pre-calibration metrics")
    pre_ece = expected_calibration_error(p, labels)
    pre_brier = float(((p - labels) ** 2).mean())
    print(f"  ECE (10 bins): {pre_ece:.4f}")
    print(f"  Brier score:   {pre_brier:.4f}")
    print(f"  AUC (ROC):     {compute_auc(p, labels):.4f}")

    section("STEP 3 - Fit temperature T via NLL minimization")
    T = fit_temperature(logits, labels)
    print(f"  Optimal T = {T:.4f}")
    print(f"  (T > 1 = softer / less confident; T < 1 = harder / more confident)")

    # Apply
    p_calibrated = 1.0 / (1.0 + np.exp(-logits / T))
    section("STEP 4 - Post-calibration metrics")
    post_ece = expected_calibration_error(p_calibrated, labels)
    post_brier = float(((p_calibrated - labels) ** 2).mean())
    print(f"  ECE (10 bins): {post_ece:.4f}  (was {pre_ece:.4f}, "
          f"{'improved' if post_ece < pre_ece else 'WORSE'})")
    print(f"  Brier score:   {post_brier:.4f}  (was {pre_brier:.4f}, "
          f"{'improved' if post_brier < pre_brier else 'WORSE'})")
    print(f"  proba >= 0.30: {(p_calibrated >= 0.30).sum():,} "
          f"({(p_calibrated >= 0.30).mean()*100:.1f}%)")
    print(f"  proba >= 0.50: {(p_calibrated >= 0.50).sum():,} "
          f"({(p_calibrated >= 0.50).mean()*100:.1f}%)")

    section("STEP 5 - Evaluate veto-bucket preservation vs v2 ensemble")
    v2_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    if v2_path.exists():
        v2 = pd.read_parquet(v2_path)
        v2["d0"] = pd.to_datetime(v2["d0"])
        df["d0"] = pd.to_datetime(df["d0"])
        df["tcn_proba_calibrated"] = p_calibrated
        merged = df.merge(v2[["d0", "ticker", "prob_continuer"]],
                           on=["d0", "ticker"], how="inner")
        print(f"  merged: {len(merged):,} rows")

        # Apply meta-scorer veto logic at THR=0.30 for both TCN versions
        for tcn_col, label in [("tcn_proba", "BCE original"),
                                ("tcn_proba_calibrated", "BCE + temp scaling")]:
            both = merged[(merged[tcn_col] >= 0.30) & (merged["prob_continuer"] >= 0.30)]
            v2_only = merged[(merged[tcn_col] < 0.30) & (merged["prob_continuer"] >= 0.30)]
            tcn_only = merged[(merged[tcn_col] >= 0.30) & (merged["prob_continuer"] < 0.30)]
            print(f"\n  {label}:")
            print(f"    BOTH agree (long)    n={len(both):>5,}  avg={both['y_reg'].clip(-0.5,1).mean()*100:>+6.2f}%")
            print(f"    v2-only (veto on)    n={len(v2_only):>5,}  avg={v2_only['y_reg'].clip(-0.5,1).mean()*100:>+6.2f}%")
            print(f"    TCN-only (false pos) n={len(tcn_only):>5,}  avg={tcn_only['y_reg'].clip(-0.5,1).mean()*100:>+6.2f}%")

    section("STEP 6 - Persist")
    df["tcn_proba_calibrated"] = p_calibrated
    out_preds = DERIVED / "tcn_intraday_walkforward_predictions_calibrated.parquet"
    df.to_parquet(out_preds, compression="zstd")
    print(f"  Wrote {out_preds}")

    summary = {
        "temperature": T,
        "pre_ece": pre_ece,
        "post_ece": post_ece,
        "pre_brier": pre_brier,
        "post_brier": post_brier,
        "n_predictions": len(df),
    }
    out_path = MODELS / "tcn_temperature.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out_path}")
    return 0


def compute_auc(probs, labels):
    """Quick AUC; manual to avoid sklearn import overhead."""
    try:
        from sklearn.metrics import roc_auc_score
        return float(roc_auc_score(labels, probs))
    except Exception:
        return 0.0


if __name__ == "__main__":
    sys.exit(main())
