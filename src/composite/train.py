"""Train the V0 composite probability score.

Pipeline:
1. Load 407 labeled rows from data/backfill/features_labeled.jsonl.
2. Run the production arena over them to get arena_buy_verdict per row
   (training conditional on cascade survival, per the user's calibration).
3. Extract features via src.composite.features.extract_features (single source of truth).
4. Fit sklearn LogisticRegression (L2) with 5-fold stratified CV.
5. Compute train AUC and CV-mean AUC; if gap > 0.10, flag overfit.
6. Save model + metadata. Generate docs/research-log/06_composite_v0_training.md.

Two models are trained:
- composite_v0_full.pkl  — uses all features including arena_buy_verdict.
                           Used at scoring time when arena verdict is known.
- composite_v0_prescore.pkl — uses all features EXCEPT arena_buy_verdict.
                              Used when scoring at the candidate level before
                              the gate cascade has run (early filter, Phase 5).

D221 Phase D additions:
- --min-rows N           refuse to train if fewer (default 1000)
- --output-version vN    auto-incremented from models/composite_*.pkl
- --feature-stability-check  WARN if CV AUC differs from prior version by >0.05;
                              require --i-know-what-im-doing to overwrite
- --i-know-what-im-doing acknowledgement flag for stability override
- --output-dir DIR        models output dir (default models/)
- --doc-output PATH       training report path (default docs/.../06_composite_v0_training.md)

Run:
  python -m src.composite.train                         # original behavior + new defaults
  python -m src.composite.train --min-rows 1000         # require 1k rows
  python -m src.composite.train --output-version v1     # write composite_v1_*.pkl
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Composite imports — own canonical feature extractor, no production code.
from src.composite.features import FEATURE_NAMES, extract_features

# The arena import is acceptable here because train.py is offline tooling, NOT
# the scoring path. score.py (the hot path) MUST stay isolated.
from src.production_arena.pipeline_runner import run_scenario
from src.production_arena.scenarios import load_scenarios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_MODELS_DIR: Path = _PROJECT_ROOT / "models"
_DOC_OUTPUT: Path = _PROJECT_ROOT / "docs" / "research-log" / "06_composite_v0_training.md"

# Overfit guard: if (train_auc - cv_mean_auc) > this, flag and reduce features.
_OVERFIT_AUC_GAP_THRESHOLD = 0.10

# Class-balance guard: ping if win_close ratio is worse than this.
_CLASS_BALANCE_FLOOR = 0.30


@dataclass
class TrainingRow:
    """Per-scenario training row: features + label + arena verdict."""
    ticker: str
    session_date: str
    arena_buy: bool
    win_close: bool
    close_return: float
    candidate_dict: dict[str, Any]


# ── Data prep ──────────────────────────────────────────────────────────


def _build_training_set() -> list[TrainingRow]:
    """Load labeled scenarios + run arena to attach arena_buy_verdict."""
    coll = load_scenarios(require_label=True)
    rows: list[TrainingRow] = []
    n_skipped_no_pnl = 0
    for s in coll:
        if s.labeled_outcome.close_return is None:
            n_skipped_no_pnl += 1
            continue
        verdict = run_scenario(s)
        rows.append(
            TrainingRow(
                ticker=s.ticker,
                session_date=s.session_date,
                arena_buy=(verdict.decision == "BUY"),
                win_close=(s.labeled_outcome.close_return > 0),
                close_return=s.labeled_outcome.close_return,
                candidate_dict=dict(s.premarket_features),
            )
        )
    logger.info(
        "training set: %d rows (skipped %d without close_return)",
        len(rows), n_skipped_no_pnl,
    )
    return rows


def _matrix_full(rows: list[TrainingRow]) -> tuple[np.ndarray, np.ndarray]:
    """X with arena_buy_verdict. y = win_close."""
    X = np.array([
        extract_features(r.candidate_dict, arena_buy_verdict=r.arena_buy).as_list()
        for r in rows
    ])
    y = np.array([1 if r.win_close else 0 for r in rows], dtype=int)
    return X, y


def _matrix_prescore(rows: list[TrainingRow]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """X WITHOUT arena_buy_verdict (last column dropped). Used for early-stage
    scoring before the gate cascade has run."""
    X = np.array([
        extract_features(r.candidate_dict, arena_buy_verdict=None).as_list()
        for r in rows
    ])
    # arena_buy_verdict is the LAST column per FEATURE_NAMES — drop it
    X_pre = X[:, :-1]
    feat_pre = list(FEATURE_NAMES)[:-1]
    y = np.array([1 if r.win_close else 0 for r in rows], dtype=int)
    return X_pre, y, feat_pre


# ── Model + CV ─────────────────────────────────────────────────────────


def _fit_pipeline(X: np.ndarray, y: np.ndarray, c_value: float = 1.0) -> Pipeline:
    """Standard scaler + L2-regularized logistic regression."""
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            penalty="l2",
            C=c_value,
            solver="lbfgs",
            max_iter=2000,
            random_state=42,
        )),
    ])
    pipe.fit(X, y)
    return pipe


def _cv_evaluate(X: np.ndarray, y: np.ndarray, n_splits: int = 5) -> dict:
    """5-fold stratified CV. Returns per-fold AUC, mean, std, Brier, calibration table."""
    if y.sum() < n_splits or (len(y) - y.sum()) < n_splits:
        logger.warning(
            "Class imbalance too severe for %d-fold stratified CV (n_pos=%d, n_neg=%d). "
            "Falling back to %d folds.",
            n_splits, int(y.sum()), int(len(y) - y.sum()), max(2, min(int(y.sum()), int(len(y) - y.sum()))),
        )
        n_splits = max(2, min(int(y.sum()), int(len(y) - y.sum())))

    kf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    fold_aucs: list[float] = []
    fold_briers: list[float] = []

    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(X, y), start=1):
        X_tr, X_te = X[train_idx], X[test_idx]
        y_tr, y_te = y[train_idx], y[test_idx]

        pipe = _fit_pipeline(X_tr, y_tr)
        proba = pipe.predict_proba(X_te)[:, 1]

        if len(set(y_te)) < 2:
            logger.warning("fold %d has single class; skipping AUC", fold_idx)
            continue
        auc = roc_auc_score(y_te, proba)
        brier = brier_score_loss(y_te, proba)
        fold_aucs.append(auc)
        fold_briers.append(brier)
        logger.info("  fold %d/%d: auc=%.4f brier=%.4f", fold_idx, n_splits, auc, brier)

    return {
        "fold_aucs": fold_aucs,
        "auc_mean": statistics.mean(fold_aucs) if fold_aucs else float("nan"),
        "auc_std": statistics.stdev(fold_aucs) if len(fold_aucs) > 1 else 0.0,
        "brier_mean": statistics.mean(fold_briers) if fold_briers else float("nan"),
        "n_splits_used": len(fold_aucs),
    }


def _train_auc(pipe: Pipeline, X: np.ndarray, y: np.ndarray) -> float:
    proba = pipe.predict_proba(X)[:, 1]
    if len(set(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, proba))


def _feature_importance(pipe: Pipeline, feature_names: list[str]) -> dict[str, float]:
    """Logistic regression coefficients (post-scaler) sorted by |weight|."""
    coefs = pipe.named_steps["clf"].coef_[0].tolist()
    return dict(zip(feature_names, coefs))


# ── Calibration table (no plotting deps) ──────────────────────────────


def _calibration_table(pipe: Pipeline, X: np.ndarray, y: np.ndarray, n_bins: int = 5) -> list[dict]:
    """Reliability table — used in lieu of a calibration plot in the markdown."""
    proba = pipe.predict_proba(X)[:, 1]
    bins = np.linspace(0, 1, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = float(bins[i]), float(bins[i + 1])
        mask = (proba >= lo) & (proba < hi if i < n_bins - 1 else proba <= hi)
        n = int(mask.sum())
        if n == 0:
            out.append({"bin": f"[{lo:.2f}, {hi:.2f})", "n": 0, "predicted": float("nan"), "actual": float("nan")})
            continue
        out.append({
            "bin": f"[{lo:.2f}, {hi:.2f})",
            "n": n,
            "predicted": float(proba[mask].mean()),
            "actual": float(y[mask].mean()),
        })
    return out


# ── Output writers ─────────────────────────────────────────────────────


def _write_model(pipe: Pipeline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(pipe, f)
    logger.info("wrote model -> %s (%d bytes)", path, path.stat().st_size)


def _write_metadata(meta: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=False, default=str)
    logger.info("wrote metadata -> %s", path)


def _write_training_doc(content: str) -> None:
    _DOC_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_DOC_OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    logger.info("wrote training report -> %s", _DOC_OUTPUT)


# ── D221 Phase D — versioning + stability helpers ─────────────────────


_VERSION_RE = re.compile(r"composite_(v\d+)_(?:full|prescore|metadata)")


def discover_existing_versions(models_dir: Path = _MODELS_DIR) -> list[str]:
    """Return sorted list of existing version tags, e.g. ['v0', 'v1']."""
    if not models_dir.exists():
        return []
    versions = set()
    for p in models_dir.glob("composite_v*_*"):
        m = _VERSION_RE.match(p.stem)
        if m:
            versions.add(m.group(1))
    return sorted(versions, key=lambda v: int(v[1:]))


def auto_increment_version(models_dir: Path = _MODELS_DIR) -> str:
    """Next available version string. v0 if none exist."""
    existing = discover_existing_versions(models_dir)
    if not existing:
        return "v0"
    last_n = int(existing[-1][1:])
    return f"v{last_n + 1}"


def load_prior_version_metadata(version: str, models_dir: Path = _MODELS_DIR) -> dict | None:
    """Return the prior version's metadata (for stability comparison)."""
    p = models_dir / f"composite_{version}_metadata.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("failed to load prior metadata at %s: %s", p, e)
        return None


def feature_stability_check(
    new_cv_auc: float, prior_meta: dict | None, threshold: float = 0.05,
) -> tuple[bool, str]:
    """Compare new CV AUC to prior version's. Returns (is_stable, message)."""
    if prior_meta is None:
        return True, "no prior version to compare"
    try:
        prior_full = prior_meta["models"]["full"]["cv_auc_mean"]
    except (KeyError, TypeError):
        return True, "prior metadata lacks models.full.cv_auc_mean"
    delta = new_cv_auc - prior_full
    if abs(delta) > threshold:
        return False, (
            f"new CV AUC {new_cv_auc:.4f} differs from prior "
            f"{prior_meta.get('version','?')} CV AUC {prior_full:.4f} "
            f"by {delta:+.4f} (threshold ±{threshold:.2f})"
        )
    return True, f"stable: new {new_cv_auc:.4f} vs prior {prior_full:.4f} (delta {delta:+.4f})"


# ── CLI entry ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.composite.train",
        description="Train the composite probability score (D221: versioned + stability-aware).",
    )
    p.add_argument("--min-rows", type=int, default=1000,
                   help="Refuse to train if labeled rows < N (default 1000). "
                        "Set to 0 to disable.")
    p.add_argument("--output-version", default=None,
                   help="Version tag (e.g. v1). Default: auto-increment from existing models/.")
    p.add_argument("--output-dir", default=str(_MODELS_DIR),
                   help="Where to write composite_<version>_*.pkl (default models/).")
    p.add_argument("--doc-output", default=str(_DOC_OUTPUT),
                   help="Training report markdown path.")
    p.add_argument("--feature-stability-check", action="store_true",
                   help="WARN if new CV AUC differs from prior version by >0.05; "
                        "require --i-know-what-im-doing to proceed.")
    p.add_argument("--i-know-what-im-doing", action="store_true",
                   help="Acknowledge instability — only meaningful with "
                        "--feature-stability-check.")
    return p


# ── Main ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    doc_output = Path(args.doc_output)

    logger.info("=" * 60)
    logger.info("Composite Score Training")
    logger.info("=" * 60)

    rows = _build_training_set()
    if not rows:
        logger.error("no training rows; aborting")
        return 1

    # D221: minimum-rows guard
    if args.min_rows > 0 and len(rows) < args.min_rows:
        logger.error(
            "training aborted: %d rows < --min-rows %d. "
            "Pass --min-rows 0 to override (and accept the noise).",
            len(rows), args.min_rows,
        )
        return 2

    # D221: resolve output version
    version = args.output_version or auto_increment_version(output_dir)
    if not re.fullmatch(r"v\d+", version):
        logger.error("invalid --output-version %r (expected vN, e.g. v1)", version)
        return 2
    logger.info("output version: %s", version)

    # Class-balance check (per user's calibration: ping if <30/70)
    pos_rate = sum(r.win_close for r in rows) / len(rows)
    arena_buy_pos = sum(1 for r in rows if r.arena_buy and r.win_close)
    arena_buy_n = sum(1 for r in rows if r.arena_buy)
    arena_buy_pos_rate = arena_buy_pos / arena_buy_n if arena_buy_n else 0.0
    arena_nt_pos = sum(1 for r in rows if not r.arena_buy and r.win_close)
    arena_nt_n = sum(1 for r in rows if not r.arena_buy)
    arena_nt_pos_rate = arena_nt_pos / arena_nt_n if arena_nt_n else 0.0
    class_balance_ok = pos_rate >= _CLASS_BALANCE_FLOOR

    logger.info(
        "class balance: %.1f%% positive overall (n=%d), %s",
        pos_rate * 100, len(rows), "OK" if class_balance_ok else "BELOW FLOOR — flag in report",
    )
    logger.info(
        "stratified: arena BUY (n=%d) win_rate=%.1f%%; arena NO_TRADE (n=%d) win_rate=%.1f%%",
        arena_buy_n, arena_buy_pos_rate * 100, arena_nt_n, arena_nt_pos_rate * 100,
    )

    # ── Train FULL model (with arena_buy_verdict) ──
    logger.info("--- training composite_v0_full ---")
    X_full, y = _matrix_full(rows)
    cv_full = _cv_evaluate(X_full, y, n_splits=5)
    pipe_full = _fit_pipeline(X_full, y)
    train_auc_full = _train_auc(pipe_full, X_full, y)
    overfit_gap_full = train_auc_full - cv_full["auc_mean"]
    importance_full = _feature_importance(pipe_full, list(FEATURE_NAMES))
    calib_full = _calibration_table(pipe_full, X_full, y)
    logger.info(
        "FULL: train_auc=%.4f cv_auc=%.4f gap=%.4f%s",
        train_auc_full, cv_full["auc_mean"], overfit_gap_full,
        "  [OVERFIT]" if overfit_gap_full > _OVERFIT_AUC_GAP_THRESHOLD else "",
    )

    # ── Train PRESCORE model (without arena_buy_verdict) ──
    logger.info("--- training composite_v0_prescore ---")
    X_pre, y_pre, feat_pre = _matrix_prescore(rows)
    cv_pre = _cv_evaluate(X_pre, y_pre, n_splits=5)
    pipe_pre = _fit_pipeline(X_pre, y_pre)
    train_auc_pre = _train_auc(pipe_pre, X_pre, y_pre)
    overfit_gap_pre = train_auc_pre - cv_pre["auc_mean"]
    importance_pre = _feature_importance(pipe_pre, feat_pre)
    calib_pre = _calibration_table(pipe_pre, X_pre, y_pre)
    logger.info(
        "PRESCORE: train_auc=%.4f cv_auc=%.4f gap=%.4f%s",
        train_auc_pre, cv_pre["auc_mean"], overfit_gap_pre,
        "  [OVERFIT]" if overfit_gap_pre > _OVERFIT_AUC_GAP_THRESHOLD else "",
    )

    # ── D221: feature stability check (against prior version) ──
    if args.feature_stability_check:
        # Compare to the version JUST BEFORE this one (auto-detected if N>0)
        existing = discover_existing_versions(output_dir)
        prior_version = None
        if existing:
            # Find versions strictly less than the target
            target_n = int(version[1:])
            for v in reversed(existing):
                if int(v[1:]) < target_n:
                    prior_version = v
                    break
        prior_meta = load_prior_version_metadata(prior_version, output_dir) if prior_version else None
        is_stable, msg = feature_stability_check(cv_full["auc_mean"], prior_meta, threshold=0.05)
        if is_stable:
            logger.info("stability check: %s", msg)
        else:
            logger.warning("STABILITY WARNING: %s", msg)
            if not args.i_know_what_im_doing:
                logger.error(
                    "refusing to overwrite production model. "
                    "Pass --i-know-what-im-doing to proceed."
                )
                return 3

    # ── Persist ──
    _write_model(pipe_full, output_dir / f"composite_{version}_full.pkl")
    _write_model(pipe_pre, output_dir / f"composite_{version}_prescore.pkl")

    metadata = {
        "version": version,
        "train_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_train_rows": len(rows),
        "class_balance_pos_rate": pos_rate,
        "class_balance_ok": class_balance_ok,
        "stratified": {
            "arena_buy": {"n": arena_buy_n, "win_rate": arena_buy_pos_rate},
            "arena_no_trade": {"n": arena_nt_n, "win_rate": arena_nt_pos_rate},
        },
        "models": {
            "full": {
                "feature_names": list(FEATURE_NAMES),
                "train_auc": train_auc_full,
                "cv_auc_mean": cv_full["auc_mean"],
                "cv_auc_std": cv_full["auc_std"],
                "cv_brier_mean": cv_full["brier_mean"],
                "cv_fold_aucs": cv_full["fold_aucs"],
                "overfit_gap": overfit_gap_full,
                "overfit_flagged": overfit_gap_full > _OVERFIT_AUC_GAP_THRESHOLD,
                "feature_importance": importance_full,
                "calibration_table": calib_full,
            },
            "prescore": {
                "feature_names": feat_pre,
                "train_auc": train_auc_pre,
                "cv_auc_mean": cv_pre["auc_mean"],
                "cv_auc_std": cv_pre["auc_std"],
                "cv_brier_mean": cv_pre["brier_mean"],
                "cv_fold_aucs": cv_pre["fold_aucs"],
                "overfit_gap": overfit_gap_pre,
                "overfit_flagged": overfit_gap_pre > _OVERFIT_AUC_GAP_THRESHOLD,
                "feature_importance": importance_pre,
                "calibration_table": calib_pre,
            },
        },
        "notes": [
            "AUC is ARENA-VALIDATED — synthesized agent signals make this optimistic vs live.",
            "Real-LLM validation awaits Phase 5 shadow data.",
            "arena_buy_verdict is expected to have NEGATIVE coefficient: cascade anti-selects.",
        ],
    }
    _write_metadata(metadata, output_dir / f"composite_{version}_metadata.json")

    # ── Markdown report ──
    doc_output.parent.mkdir(parents=True, exist_ok=True)
    with open(doc_output, "w", encoding="utf-8", newline="\n") as f:
        f.write(_format_report(metadata))
    logger.info("wrote training report -> %s", doc_output)

    logger.info("=" * 60)
    logger.info("DONE — version %s — see %s", version, doc_output)
    return 0


def _format_report(meta: dict) -> str:
    """Generate the human-readable training report."""
    full = meta["models"]["full"]
    pre = meta["models"]["prescore"]
    s = meta["stratified"]

    def _format_table(rows: list[dict], cols: list[str]) -> str:
        lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
        return "\n".join(lines)

    def _fmt_importance(imp: dict[str, float]) -> str:
        items = sorted(imp.items(), key=lambda kv: -abs(kv[1]))
        return "\n".join(f"| {k} | {v:+.4f} |" for k, v in items)

    out = f"""# Composite Score V0 — Training Report

**Trained:** {meta['train_date']}
**Training rows:** {meta['n_train_rows']} (from `data/backfill/features_labeled.jsonl`)
**Models written:** `models/composite_v0_full.pkl`, `models/composite_v0_prescore.pkl`

## Headline

| Model | Train AUC | CV AUC mean ± std | Brier | Overfit gap | Status |
|-------|-----------|-------------------|-------|-------------|--------|
| **full** (with `arena_buy_verdict`) | {full['train_auc']:.4f} | {full['cv_auc_mean']:.4f} ± {full['cv_auc_std']:.4f} | {full['cv_brier_mean']:.4f} | {full['overfit_gap']:+.4f} | {"⚠️ OVERFIT" if full['overfit_flagged'] else "OK"} |
| **prescore** (without verdict) | {pre['train_auc']:.4f} | {pre['cv_auc_mean']:.4f} ± {pre['cv_auc_std']:.4f} | {pre['cv_brier_mean']:.4f} | {pre['overfit_gap']:+.4f} | {"⚠️ OVERFIT" if pre['overfit_flagged'] else "OK"} |

**Overfit gap** = train AUC − CV mean AUC. Threshold for flagging: > 0.10.

## Class balance and stratification

Overall positive rate (`win_close`): **{meta['class_balance_pos_rate']*100:.1f}%** ({"OK" if meta['class_balance_ok'] else "BELOW 30% FLOOR — verify before shipping"})

Stratified by arena verdict:

| Stratum | n | win_rate |
|---------|---|----------|
| Arena BUY | {s['arena_buy']['n']} | {s['arena_buy']['win_rate']*100:.1f}% |
| Arena NO_TRADE | {s['arena_no_trade']['n']} | {s['arena_no_trade']['win_rate']*100:.1f}% |

**Important:** the arena BUY win rate is *lower* than the arena NO_TRADE win rate. This is the **gate-cascade anti-selection** finding from Phase 3.0 — the current 13-gate cascade is systematically picking less-likely-to-win candidates than it rejects. The composite score's `arena_buy_verdict` coefficient is expected to be **negative** as a result. This is real signal the model can learn from, not a data bug.

## Feature importance — FULL model

| Feature | Coefficient (post-scaling) |
|---------|----------------------------|
{_fmt_importance(full['feature_importance'])}

## Feature importance — PRESCORE model

| Feature | Coefficient (post-scaling) |
|---------|----------------------------|
{_fmt_importance(pre['feature_importance'])}

## Calibration — FULL

{_format_table(full['calibration_table'], ['bin', 'n', 'predicted', 'actual'])}

## Calibration — PRESCORE

{_format_table(pre['calibration_table'], ['bin', 'n', 'predicted', 'actual'])}

## Per-fold CV AUCs

| Fold | FULL | PRESCORE |
|------|------|----------|
""" + "\n".join(
        f"| {i+1} | {a:.4f} | {b:.4f} |"
        for i, (a, b) in enumerate(zip(full['cv_fold_aucs'], pre['cv_fold_aucs']))
    ) + f"""

## Honest assessment of training quality

- **407 labeled rows** is a thin training set. CV stdev across folds tells you the model's stability — anything above 0.05 means the model could swing meaningfully on resampling. See per-fold table.
- **AUC is arena-validated, not live-validated.** Phase 2's pipeline_runner uses synthesized agent signals (deterministic functions of candidate features). Real LLM agents will produce noisier signals, so live-data AUC will likely be **lower** than what's reported here. This is a known limitation; Phase 5 shadow mode is what closes the gap with live data.
- **Day-of-week feature deliberately excluded.** Per the user's calibration: too noisy for 407 rows.
- **Two models, not one:** the FULL model uses arena_buy_verdict; the PRESCORE model doesn't. PRESCORE is for early-stage filtering before the gate cascade has run (Phase 5 shadow could call PRESCORE on every candidate at scan time, then call FULL after the cascade for a refined estimate).

## Decisions to make for Phase 4

1. **Ship which model as primary?** Both are saved. Phase 5 shadow mode logs both. Phase 4's threshold sweep should compare them.
2. **What threshold?** AUC tells you the model can rank, not where to cut. Phase 4's sweep finds the cut that maximizes Sharpe given the scoring distribution.
3. **Should arena_buy_verdict's coefficient be capped?** If FULL says "ignore the cascade entirely," that's directly relevant to the structural-redesign discussion in `04_structural_redesign.md`.

## Notes from the prompt
{chr(10).join("- " + n for n in meta['notes'])}
"""
    return out


if __name__ == "__main__":
    sys.exit(main())
