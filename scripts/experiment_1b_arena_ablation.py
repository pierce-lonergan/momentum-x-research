"""Experiment 1B — arena_buy_verdict ablation.

Drops `arena_buy_verdict` from v1's feature set and retrains on the full
5,774 rows. Tests how much of v1's CV AUC = 0.5729 is carried by the
cascade feature specifically vs the other 7 features.

Spec: docs/research-log/08_overfit_vs_regime_experiment.md, section "Experiment 1B".

Output: APPENDS to docs/research-log/09_monday_overfit_regime_results.md
(1A creates the file with its results; 1B adds its section).

Usage:
  python scripts/experiment_1b_arena_ablation.py
  python scripts/experiment_1b_arena_ablation.py --seeds 42,17,31 \
      --bootstrap-n 5000 \
      --append-to docs/research-log/09_monday_overfit_regime_results.md
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Reuse production training pipeline (same model v1 produced)
from src.composite.train import _build_training_set, _matrix_prescore, TrainingRow
from src.composite.features import FEATURE_NAMES

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = _PROJECT_ROOT / "docs" / "research-log" / "09_monday_overfit_regime_results.md"
_BASELINE_V1_AUC = 0.5729  # from yesterday's v1 retrain


def _cv_evaluate_seeded(X: np.ndarray, y: np.ndarray, seed: int, n_splits: int = 5) -> dict:
    """5-fold stratified CV with seed override. Mirror of 1A's helper."""
    if y.sum() < n_splits or (len(y) - y.sum()) < n_splits:
        n_splits = max(2, min(int(y.sum()), int(len(y) - y.sum())))
        if n_splits < 2:
            return {"fold_aucs": [], "auc_mean": float("nan"), "auc_std": float("nan")}

    kf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    fold_aucs: list[float] = []
    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(X, y), start=1):
        X_tr, X_te = X[train_idx], X[test_idx]
        y_tr, y_te = y[train_idx], y[test_idx]
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                penalty="l2", C=1.0, solver="lbfgs",
                max_iter=2000, random_state=seed,
            )),
        ])
        pipe.fit(X_tr, y_tr)
        proba = pipe.predict_proba(X_te)[:, 1]
        if len(set(y_te)) < 2:
            continue
        fold_aucs.append(float(roc_auc_score(y_te, proba)))

    return {
        "fold_aucs": fold_aucs,
        "auc_mean": statistics.mean(fold_aucs) if fold_aucs else float("nan"),
        "auc_std": statistics.stdev(fold_aucs) if len(fold_aucs) > 1 else 0.0,
    }


def _bootstrap_delta_ci(
    fold_aucs: list[float],
    baseline: float,
    n_bootstrap: int = 5000,
    seed: int = 42,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Bootstrap (mean fold AUC) - baseline. Returns (mean_delta, lo, hi)."""
    if not fold_aucs:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    arr = np.array(fold_aucs)
    deltas = []
    for _ in range(n_bootstrap):
        sample = rng.choice(arr, size=len(arr), replace=True)
        deltas.append(float(np.mean(sample)) - baseline)
    deltas.sort()
    lo = deltas[int(n_bootstrap * alpha / 2)]
    hi = deltas[int(n_bootstrap * (1 - alpha / 2))]
    return float(np.mean(deltas)), float(lo), float(hi)


def _train_no_arena(rows: list[TrainingRow], seeds: list[int]) -> dict:
    """Train v1 composite WITHOUT arena_buy_verdict, across seeds."""
    X, y, feat_names = _matrix_prescore(rows)
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    logger.info("X shape=%s y_pos=%d y_neg=%d features=%s",
                X.shape, n_pos, n_neg, feat_names)

    auc_per_seed: list[float] = []
    all_fold_aucs: list[float] = []
    coefs_per_seed: list[dict[str, float]] = []
    for seed in seeds:
        cv = _cv_evaluate_seeded(X, y, seed=seed)
        auc_per_seed.append(cv["auc_mean"])
        all_fold_aucs.extend(cv["fold_aucs"])

        # Refit on full data for coefficient inspection
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                penalty="l2", C=1.0, solver="lbfgs",
                max_iter=2000, random_state=seed,
            )),
        ]).fit(X, y)
        coefs_per_seed.append(
            dict(zip(feat_names, pipe.named_steps["clf"].coef_[0].tolist()))
        )

    # Mean coefficient per feature across seeds (for stability check)
    mean_coefs = {
        f: float(np.mean([c[f] for c in coefs_per_seed]))
        for f in feat_names
    }

    return {
        "n_rows": len(rows),
        "n_pos": n_pos,
        "n_neg": n_neg,
        "feature_names": feat_names,
        "auc_per_seed": auc_per_seed,
        "auc_mean_across_seeds": float(np.mean(auc_per_seed)),
        "auc_std_across_seeds": float(np.std(auc_per_seed)),
        "all_fold_aucs": all_fold_aucs,
        "mean_coefs": mean_coefs,
    }


def _render_report(result: dict, seeds: list[int], n_bootstrap: int) -> str:
    """Render the 1B section appended to the 1A report."""
    d_mean, d_lo, d_hi = _bootstrap_delta_ci(
        result["all_fold_aucs"], baseline=_BASELINE_V1_AUC, n_bootstrap=n_bootstrap,
    )

    lines = []
    lines.append("## Experiment 1B — arena_buy_verdict ablation")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append(f"**Baseline (v1 with arena):** CV AUC = {_BASELINE_V1_AUC:.4f}")
    lines.append("")
    lines.append("Drops `arena_buy_verdict` from v1's feature set and retrains on the")
    lines.append("full row count. Measures how much of v1's AUC came from the cascade")
    lines.append("feature specifically.")
    lines.append("")
    lines.append(f"- Seeds: {seeds}")
    lines.append(f"- Bootstrap resamples: {n_bootstrap}")
    lines.append(f"- Training rows: {result['n_rows']} (pos={result['n_pos']}, neg={result['n_neg']})")
    lines.append(f"- Features used ({len(result['feature_names'])}): "
                 f"{result['feature_names']}")
    lines.append("")
    lines.append("### Headline CV AUC")
    lines.append("")
    lines.append("| variant | mean AUC | std AUC | delta vs v1-with-arena | 95% CI of delta |")
    lines.append("|---|---|---|---|---|")
    lines.append(
        f"| v1-without-arena | {result['auc_mean_across_seeds']:.4f} | "
        f"{result['auc_std_across_seeds']:.4f} | {d_mean:+.4f} | "
        f"[{d_lo:+.4f}, {d_hi:+.4f}] |"
    )
    lines.append("")
    lines.append("### Feature coefficients (mean across seeds, post-scaler)")
    lines.append("")
    lines.append("| feature | coefficient |")
    lines.append("|---|---|")
    coef_items = sorted(
        result["mean_coefs"].items(), key=lambda x: -abs(x[1])
    )
    for f, c in coef_items:
        lines.append(f"| {f} | {c:+.4f} |")
    lines.append("")
    lines.append("### Decision matrix")
    lines.append("")
    lines.append("- v1-without-arena AUC >= 0.56 (CI overlap with 0.57): "
                 "**arena contributes <= 0.01 AUC.** Drop in v2.")
    lines.append("- v1-without-arena AUC 0.52-0.55 (CI disjoint from 0.57): "
                 "**arena carries 0.02-0.05.** Keep but surface through MoE.")
    lines.append("- v1-without-arena AUC < 0.52: "
                 "**arena was carrying ~0.05+.** Don't drop; rebuild around inversion.")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=str, default="42,17,31",
                        help="Comma-separated seeds (default: 42,17,31).")
    parser.add_argument("--bootstrap-n", type=int, default=5000,
                        help="Bootstrap resample count (default: 5000).")
    parser.add_argument("--append-to", type=str, default=str(_DEFAULT_OUTPUT),
                        help="Markdown file to APPEND results to (default: 1A's output).")
    args = parser.parse_args(argv)

    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    output = Path(args.append_to)

    logger.info("=" * 60)
    logger.info("Experiment 1B: arena_buy_verdict ablation")
    logger.info("=" * 60)
    logger.info("Loading training set (this runs the arena per row, slow)...")
    rows = _build_training_set()
    logger.info("Training set: %d rows", len(rows))
    if len(rows) < 100:
        logger.error("training set too small (%d); aborting", len(rows))
        return 2

    logger.info("Training v1 composite WITHOUT arena_buy_verdict...")
    result = _train_no_arena(rows, seeds)
    logger.info(
        "v1-without-arena: AUC mean=%.4f std=%.4f (across %d seeds)",
        result["auc_mean_across_seeds"],
        result["auc_std_across_seeds"],
        len(seeds),
    )

    logger.info("Appending markdown to %s", output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = _render_report(result, seeds, args.bootstrap_n)
    if output.exists():
        with open(output, "a", encoding="utf-8") as f:
            f.write(rendered)
    else:
        # 1A didn't run yet; create with a small header
        header = (
            f"# Experiment 1A/1B — Overfit vs Regime resolution\n\n"
            f"_(1B ran without 1A; this file contains 1B only.)_\n\n"
            f"---\n\n"
        )
        output.write_text(header + rendered, encoding="utf-8")
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
