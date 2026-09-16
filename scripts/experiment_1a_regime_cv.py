"""Experiment 1A — Regime-stratified walk-forward CV on v1 composite.

Routes the overfit-vs-regime question for v2 architecture. Spec lives at
docs/research-log/08_overfit_vs_regime_experiment.md.

Method (default, no external data):
  1. Load the 5,774-row training set via load_scenarios + arena verdicts
     (same pipeline that produced v1).
  2. Compute per-date cross-sectional aggregates:
       cs_mean_gap_pct, cs_std_dollar_volume, cs_count_candidates,
       cs_mean_orb_range
  3. Cluster dates into K=3 regimes via KMeans on standardized 4-vector.
  4. Per regime: stratified 5-fold CV on close_win using v1 composite
     (8 features). Repeat for 3 seeds (42, 17, 31).
  5. Bootstrap CI on per-regime AUC delta vs global v1 baseline (0.5729).
  6. Append a markdown report to docs/research-log/09_monday_overfit_regime_results.md

Usage:
  python scripts/experiment_1a_regime_cv.py
  python scripts/experiment_1a_regime_cv.py --n-clusters 3 --seeds 42,17,31 \
      --bootstrap-n 5000 \
      --output docs/research-log/09_monday_overfit_regime_results.md
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Reuse the production training pipeline so we're measuring the SAME model
# the live system would produce, not a re-implementation.
from src.composite.train import _build_training_set, _matrix_full, TrainingRow
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


# ── Regime computation ────────────────────────────────────────────────


def _per_date_aggregates(rows: list[TrainingRow]) -> dict[str, np.ndarray]:
    """Compute the 4-vector of cross-sectional aggregates per date."""
    by_date: dict[str, list[TrainingRow]] = defaultdict(list)
    for r in rows:
        by_date[r.session_date].append(r)

    dates = sorted(by_date)
    feats = []
    for d in dates:
        drows = by_date[d]
        gaps = [r.candidate_dict.get("gap_pct") or 0.0 for r in drows]
        dvols = [
            float(r.candidate_dict.get("dollar_volume") or 0.0)
            for r in drows
        ]
        log_dvols = [np.log1p(max(0.0, v)) for v in dvols]
        orbs = [r.candidate_dict.get("orb_range_pct") or 0.0 for r in drows]
        feats.append([
            float(np.mean(gaps)) if gaps else 0.0,
            float(np.std(log_dvols)) if len(log_dvols) > 1 else 0.0,
            float(len(drows)),
            float(np.mean(orbs)) if orbs else 0.0,
        ])
    return {"dates": np.array(dates), "X": np.array(feats)}


def _cluster_dates(date_features: dict, k: int, seed: int = 42) -> dict[str, int]:
    """KMeans on standardized features. Returns date -> cluster_id."""
    X = date_features["X"]
    Xs = StandardScaler().fit_transform(X)
    km = KMeans(n_clusters=k, n_init=10, random_state=seed)
    labels = km.fit_predict(Xs)
    return dict(zip(date_features["dates"].tolist(), labels.tolist()))


# ── CV with seed override ─────────────────────────────────────────────


def _cv_evaluate_seeded(X: np.ndarray, y: np.ndarray, seed: int, n_splits: int = 5) -> dict:
    """5-fold stratified CV with explicit seed override.

    Mirrors src/composite/train._cv_evaluate but lets caller vary the
    KFold random_state for the seeded-runs requirement of 1A/1B.
    """
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
        "n_splits_used": len(fold_aucs),
    }


# ── Per-regime training ───────────────────────────────────────────────


def _train_regime(
    rows: list[TrainingRow],
    regime_id: int,
    date_to_regime: dict[str, int],
    seeds: list[int],
) -> dict:
    """Train v1 composite on rows belonging to one regime, across seeds.

    Returns metrics dict including per-fold AUCs, mean across seeds,
    arena_buy_verdict coefficient, and stratified BUY/NO_TRADE win-rate
    gap.
    """
    regime_rows = [r for r in rows if date_to_regime.get(r.session_date) == regime_id]
    if not regime_rows:
        return {
            "regime_id": regime_id, "n_rows": 0,
            "n_dates": 0, "auc_per_seed": [],
            "auc_mean_across_seeds": float("nan"),
            "auc_std_across_seeds": float("nan"),
            "arena_buy_n": 0, "arena_buy_winrate": float("nan"),
            "arena_no_trade_n": 0, "arena_no_trade_winrate": float("nan"),
            "stratified_gap_pp": float("nan"),
            "arena_coef_mean": float("nan"),
            "all_fold_aucs": [],
        }

    n_rows = len(regime_rows)
    n_dates = len({r.session_date for r in regime_rows})

    X, y = _matrix_full(regime_rows)
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_pos < 2 or n_neg < 2:
        logger.warning(
            "regime %d has insufficient class balance (n_pos=%d n_neg=%d); skipping",
            regime_id, n_pos, n_neg,
        )
        return {
            "regime_id": regime_id, "n_rows": n_rows, "n_dates": n_dates,
            "auc_per_seed": [], "auc_mean_across_seeds": float("nan"),
            "auc_std_across_seeds": float("nan"),
            "arena_buy_n": 0, "arena_buy_winrate": float("nan"),
            "arena_no_trade_n": 0, "arena_no_trade_winrate": float("nan"),
            "stratified_gap_pp": float("nan"),
            "arena_coef_mean": float("nan"),
            "all_fold_aucs": [],
        }

    auc_per_seed: list[float] = []
    all_fold_aucs: list[float] = []
    arena_coefs: list[float] = []
    for seed in seeds:
        cv = _cv_evaluate_seeded(X, y, seed=seed)
        auc_per_seed.append(cv["auc_mean"])
        all_fold_aucs.extend(cv["fold_aucs"])

        # Refit on full regime data to get coefficient
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                penalty="l2", C=1.0, solver="lbfgs",
                max_iter=2000, random_state=seed,
            )),
        ]).fit(X, y)
        coefs = dict(zip(FEATURE_NAMES, pipe.named_steps["clf"].coef_[0].tolist()))
        arena_coefs.append(coefs.get("arena_buy_verdict", float("nan")))

    # Stratified BUY/NO_TRADE win rates
    buys = [r for r in regime_rows if r.arena_buy]
    nots = [r for r in regime_rows if not r.arena_buy]
    buy_wr = float(np.mean([r.win_close for r in buys])) if buys else float("nan")
    not_wr = float(np.mean([r.win_close for r in nots])) if nots else float("nan")
    gap_pp = (not_wr - buy_wr) * 100 if (buys and nots) else float("nan")

    return {
        "regime_id": regime_id,
        "n_rows": n_rows,
        "n_dates": n_dates,
        "auc_per_seed": auc_per_seed,
        "auc_mean_across_seeds": float(np.mean(auc_per_seed)),
        "auc_std_across_seeds": float(np.std(auc_per_seed)),
        "arena_buy_n": len(buys),
        "arena_buy_winrate": buy_wr,
        "arena_no_trade_n": len(nots),
        "arena_no_trade_winrate": not_wr,
        "stratified_gap_pp": gap_pp,
        "arena_coef_mean": float(np.mean(arena_coefs)),
        "all_fold_aucs": all_fold_aucs,
    }


# ── Bootstrap CI on AUC delta ─────────────────────────────────────────


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


# ── Markdown report ───────────────────────────────────────────────────


def _render_report(
    n_clusters: int,
    seeds: list[int],
    n_bootstrap: int,
    regime_results: list[dict],
    overall_n: int,
) -> str:
    """Render the 1A section of docs/research-log/09_monday_overfit_regime_results.md."""
    lines = []
    lines.append("# Experiment 1A/1B — Overfit vs Regime resolution")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append(f"**Spec:** docs/research-log/08_overfit_vs_regime_experiment.md")
    lines.append(f"**Baseline (global v1):** CV AUC = {_BASELINE_V1_AUC:.4f}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Experiment 1A — Regime-stratified walk-forward CV on v1")
    lines.append("")
    lines.append(f"- Regime method: synthetic cross-sectional aggregates "
                 f"(mean gap_pct, std log dollar_volume, count, mean ORB range)")
    lines.append(f"- K clusters: {n_clusters}")
    lines.append(f"- Seeds: {seeds}")
    lines.append(f"- Bootstrap resamples: {n_bootstrap}")
    lines.append(f"- Total training rows: {overall_n}")
    lines.append("")
    lines.append("### Per-regime CV AUC")
    lines.append("")
    lines.append("| regime | n_rows | n_dates | mean AUC | std AUC | "
                 "delta vs v1 | 95% CI of delta |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in regime_results:
        if r["n_rows"] == 0 or not r["all_fold_aucs"]:
            lines.append(f"| {r['regime_id']} | {r['n_rows']} | {r['n_dates']} | "
                         f"n/a | n/a | n/a | n/a |")
            continue
        d_mean, d_lo, d_hi = _bootstrap_delta_ci(
            r["all_fold_aucs"], baseline=_BASELINE_V1_AUC, n_bootstrap=5000
        )
        lines.append(
            f"| {r['regime_id']} | {r['n_rows']} | {r['n_dates']} | "
            f"{r['auc_mean_across_seeds']:.4f} | {r['auc_std_across_seeds']:.4f} | "
            f"{d_mean:+.4f} | [{d_lo:+.4f}, {d_hi:+.4f}] |"
        )
    lines.append("")
    lines.append("### Per-regime stratified win rates + arena coefficient")
    lines.append("")
    lines.append("| regime | arena BUY n | BUY winrate | arena NO_TRADE n | "
                 "NO_TRADE winrate | gap (pp) | arena_coef (mean across seeds) |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in regime_results:
        if r["n_rows"] == 0:
            continue
        lines.append(
            f"| {r['regime_id']} | {r['arena_buy_n']} | "
            f"{r['arena_buy_winrate']:.3f} | {r['arena_no_trade_n']} | "
            f"{r['arena_no_trade_winrate']:.3f} | "
            f"{r['stratified_gap_pp']:+.1f}pp | {r['arena_coef_mean']:+.4f} |"
        )
    lines.append("")
    lines.append("### Decision matrix (apply to the per-regime AUCs above)")
    lines.append("")
    lines.append("- All 3 regime AUCs within +/-0.02 of 0.57 + CIs overlap "
                 "-> **Overfit reading.** Drop arena_buy_verdict in v2.")
    lines.append("- One regime AUC >= 0.70 + another <= 0.55 + CIs disjoint "
                 "-> **Regime reading.** Build MoE; move from experiment #4 to critical path.")
    lines.append("- Mixed (no clean split) -> **Indistinguishable.** Iterate on regime featurization.")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-clusters", type=int, default=3,
                        help="Number of regime clusters (default: 3).")
    parser.add_argument("--seeds", type=str, default="42,17,31",
                        help="Comma-separated seeds for CV variance (default: 42,17,31).")
    parser.add_argument("--bootstrap-n", type=int, default=5000,
                        help="Bootstrap resample count (default: 5000).")
    parser.add_argument("--output", type=str, default=str(_DEFAULT_OUTPUT),
                        help="Markdown output path (overwrites; 1B appends to it).")
    args = parser.parse_args(argv)

    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    output = Path(args.output)

    logger.info("=" * 60)
    logger.info("Experiment 1A: regime-stratified CV")
    logger.info("=" * 60)
    logger.info("Loading training set (this runs the arena per row, slow)...")
    rows = _build_training_set()
    logger.info("Training set: %d rows", len(rows))
    if len(rows) < 100:
        logger.error("training set too small (%d); aborting", len(rows))
        return 2

    logger.info("Computing per-date cross-sectional aggregates...")
    date_features = _per_date_aggregates(rows)
    logger.info("  %d unique dates", len(date_features["dates"]))

    logger.info("Clustering dates into %d regimes...", args.n_clusters)
    date_to_regime = _cluster_dates(date_features, k=args.n_clusters, seed=42)
    cluster_sizes = defaultdict(int)
    for c in date_to_regime.values():
        cluster_sizes[c] += 1
    for cid in sorted(cluster_sizes):
        logger.info("  regime %d: %d dates", cid, cluster_sizes[cid])

    logger.info("Training per-regime composites (3 seeds each)...")
    regime_results = []
    for cid in sorted(set(date_to_regime.values())):
        logger.info("  -> regime %d", cid)
        result = _train_regime(rows, cid, date_to_regime, seeds)
        regime_results.append(result)
        logger.info(
            "    n_rows=%d n_dates=%d auc_mean=%.4f gap=%.1fpp arena_coef=%.3f",
            result["n_rows"], result["n_dates"],
            result["auc_mean_across_seeds"],
            result["stratified_gap_pp"],
            result["arena_coef_mean"],
        )

    logger.info("Rendering markdown report -> %s", output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        _render_report(args.n_clusters, seeds, args.bootstrap_n,
                       regime_results, len(rows)),
        encoding="utf-8",
    )
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
