"""D220 Phase 4 calibration sweep.

Runs two sweeps on the 502-scenario backfill:
1. STANDARD — cascade BUYs filtered by composite threshold, across the
   ArenaConfig grid.
2. INVERTED — cascade NO_TRADE-excl-ORB candidates filtered by composite
   threshold (the Phase 4.0 finding: pre-open rejects have +22.8% median MFE).

Both sweeps report:
- In-sample (IS) optimal threshold and EV
- 5-fold cross-validated optimal threshold and EV (the honest number)

Output: data/arena_runs/phase4_*.jsonl + heatmap data for the report.

Per the user's calibration: instant_reject_min_price held at 0.50 (the
composite handles price quality via log_price feature). Sweep grid:
  6 (max_float) × 1 (min_price) × 5 (vwap) × 6 (mfcs) × 6 (composite) = 1,080
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections import defaultdict
from itertools import product
from pathlib import Path

import numpy as np
from sklearn.model_selection import KFold

# Top-level imports for multiprocessing picklability
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.composite.score import composite_score, reset_model_cache  # noqa: E402
from src.production_arena.pipeline_runner import run_scenario  # noqa: E402
from src.production_arena.scenarios import load_scenarios  # noqa: E402
from src.production_arena.types import ArenaConfig  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT_DIR = _PROJECT_ROOT / "data" / "arena_runs"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Sweep grid (per user's Phase 4 spec) ────────────────────────────────

SWEEP_GRID = {
    "instant_reject_max_float": [
        200_000_000,
        500_000_000,
        1_000_000_000,
        2_000_000_000,
        5_000_000_000,
        None,  # no float gate
    ],
    # min_price held at 0.50 per user's calibration (composite handles via log_price)
    "vwap_bias_threshold_pct": [0.5, 1.0, 2.0, 3.0, None],  # None = gate disabled
    "mfcs_buy_threshold": [0.20, 0.25, 0.30, 0.35, 0.40, 0.50],
}
COMPOSITE_THRESHOLDS = [0.0, 0.20, 0.25, 0.30, 0.35, 0.40]


# ── Stratified scenario set + composite scores (one-time prep) ──────────


def _prepare_scenarios():
    """Load scenarios, run baseline arena once, attach composite scores."""
    coll = load_scenarios(require_label=True)
    scenarios = list(coll)
    scenarios = [s for s in scenarios if s.labeled_outcome.close_return is not None]
    logger.info("loaded %d labeled scenarios", len(scenarios))

    # Pre-compute composite scores using the prescore model (no arena verdict yet)
    reset_model_cache()
    composite_scores: dict[tuple[str, str], float] = {}
    for s in scenarios:
        try:
            cs = composite_score(s.premarket_features, arena_buy_verdict=None)
        except Exception:
            cs = 0.5
        composite_scores[(s.session_date, s.ticker)] = cs
    logger.info("computed prescore composite for all scenarios")
    return scenarios, composite_scores


# ── Sweep runners ───────────────────────────────────────────────────────


def _run_arena_under_config(scenarios, config, scenario_indices=None):
    """Returns list of (idx, scenario, verdict) for each scenario in subset."""
    out = []
    if scenario_indices is None:
        scenario_indices = list(range(len(scenarios)))
    for idx in scenario_indices:
        s = scenarios[idx]
        v = run_scenario(s, config)
        out.append((idx, s, v))
    return out


def _evaluate_combo(
    scenarios,
    composite_scores,
    arena_overrides: dict,
    composite_threshold: float,
    invert: bool = False,
    indices: list[int] | None = None,
) -> dict:
    """Run one (config, threshold) combo, return aggregate stats.

    Standard mode: trade arena BUYs whose composite >= threshold.
    Inverted mode: trade arena NO_TRADE-excl-ORB whose composite >= threshold.
    """
    config = ArenaConfig(**arena_overrides)
    if indices is None:
        indices = list(range(len(scenarios)))

    n_trades = 0
    n_wins = 0
    pnl_sum = 0.0
    pnl_list: list[float] = []

    for idx in indices:
        s = scenarios[idx]
        v = run_scenario(s, config)

        # Determine eligibility based on mode
        if invert:
            eligible = (
                v.decision == "NO_TRADE"
                and v.gate_rejected != "entry_delay.py:orb_confirmation"
            )
        else:
            eligible = (v.decision == "BUY")

        if not eligible:
            continue

        # Composite filter — use full model with arena verdict
        try:
            cs = composite_score(
                s.premarket_features,
                arena_buy_verdict=(v.decision == "BUY"),
            )
        except Exception:
            cs = 0.5
        if cs < composite_threshold:
            continue

        # Take the trade
        n_trades += 1
        ret = s.labeled_outcome.close_return
        if ret is None:
            continue
        if ret > 0:
            n_wins += 1
        pnl_sum += ret
        pnl_list.append(ret)

    win_rate = n_wins / n_trades if n_trades else 0.0
    avg_pnl = pnl_sum / n_trades if n_trades else 0.0
    return {
        "config": arena_overrides,
        "composite_threshold": composite_threshold,
        "invert": invert,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "sum_pnl": pnl_sum,
        "pnl_list": pnl_list,
    }


# ── In-sample sweep ──────────────────────────────────────────────────────


def in_sample_sweep(scenarios, composite_scores, invert: bool = False) -> list[dict]:
    """Full grid sweep on all scenarios. The IS optimum is the upper bound;
    cross-validated EV is the realistic estimate."""
    combos = list(product(
        SWEEP_GRID["instant_reject_max_float"],
        SWEEP_GRID["vwap_bias_threshold_pct"],
        SWEEP_GRID["mfcs_buy_threshold"],
        COMPOSITE_THRESHOLDS,
    ))
    logger.info("IS sweep (%s): %d combos", "INVERTED" if invert else "STANDARD", len(combos))
    results = []
    t0 = time.perf_counter()
    for i, (mf, vw, mfcs_t, ct) in enumerate(combos):
        overrides = {
            "instant_reject_max_float": mf,
            "vwap_bias_threshold_pct": vw,
            "mfcs_buy_threshold": mfcs_t,
        }
        # Drop None values so ArenaConfig uses defaults for missing fields
        overrides = {k: v for k, v in overrides.items() if v is not None}
        result = _evaluate_combo(scenarios, composite_scores, overrides, ct, invert=invert)
        result["config_max_float"] = mf
        result["config_vwap"] = vw
        result["config_mfcs"] = mfcs_t
        results.append(result)
        if (i + 1) % 200 == 0:
            logger.info("  %d/%d combos in %.1fs", i + 1, len(combos), time.perf_counter() - t0)
    logger.info("  IS sweep done in %.1fs", time.perf_counter() - t0)
    return results


# ── Cross-validated threshold ────────────────────────────────────────────


def cv_optimal_threshold(scenarios, composite_scores, invert: bool, fixed_overrides: dict, k: int = 5) -> dict:
    """For a fixed structural config, find the CV-optimal composite threshold.

    Splits scenarios into k folds; chooses the IS-best threshold on (k-1) folds,
    evaluates on the held-out fold. Reports per-fold + overall metrics.
    """
    n = len(scenarios)
    indices = np.arange(n)
    kf = KFold(n_splits=k, shuffle=True, random_state=42)

    fold_results = []
    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(indices), start=1):
        train_list = train_idx.tolist()
        test_list = test_idx.tolist()

        # Find the threshold that maximizes EV on the train fold
        best_ev = -1e9
        best_threshold = COMPOSITE_THRESHOLDS[0]
        for ct in COMPOSITE_THRESHOLDS:
            r = _evaluate_combo(
                scenarios, composite_scores, fixed_overrides,
                ct, invert=invert, indices=train_list,
            )
            ev = r["sum_pnl"]  # total realized PnL, simple objective
            if ev > best_ev and r["n_trades"] >= 3:  # require min trades
                best_ev = ev
                best_threshold = ct

        # Evaluate that threshold on the held-out test fold
        test_r = _evaluate_combo(
            scenarios, composite_scores, fixed_overrides,
            best_threshold, invert=invert, indices=test_list,
        )
        fold_results.append({
            "fold": fold_idx,
            "chosen_threshold": best_threshold,
            "test_n_trades": test_r["n_trades"],
            "test_win_rate": test_r["win_rate"],
            "test_avg_pnl": test_r["avg_pnl"],
            "test_sum_pnl": test_r["sum_pnl"],
        })

    # Aggregate
    total_test_trades = sum(f["test_n_trades"] for f in fold_results)
    total_test_pnl = sum(f["test_sum_pnl"] for f in fold_results)
    avg_chosen = float(np.mean([f["chosen_threshold"] for f in fold_results]))
    overall_avg_pnl = total_test_pnl / total_test_trades if total_test_trades else 0.0

    return {
        "k_folds": k,
        "fold_results": fold_results,
        "avg_chosen_threshold": avg_chosen,
        "total_test_trades": total_test_trades,
        "total_test_pnl_pct": total_test_pnl,
        "test_avg_pnl_pct": overall_avg_pnl,
    }


# ── Output / aggregation ─────────────────────────────────────────────────


def best_combos(results: list[dict], top_n: int = 10, min_trades: int = 5) -> list[dict]:
    """Top combos by sum_pnl with min_trades filter."""
    eligible = [r for r in results if r["n_trades"] >= min_trades]
    return sorted(eligible, key=lambda r: -r["sum_pnl"])[:top_n]


def heatmap_data(results: list[dict], axis_a: str, axis_b: str, metric: str = "avg_pnl") -> dict:
    """Aggregate results into a 2D map keyed by (axis_a, axis_b).

    For each (a, b) cell, average the metric across all combos with those values.
    Used for the heatmap tables in the report.
    """
    cells: dict[tuple, list[float]] = defaultdict(list)
    for r in results:
        if r["n_trades"] < 5:
            continue
        a = r.get(axis_a, r["config"].get(axis_a, "?"))
        b = r.get(axis_b, r["config"].get(axis_b, "?"))
        cells[(a, b)].append(r[metric])
    return {k: float(np.mean(v)) for k, v in cells.items()}


def main() -> int:
    scenarios, composite_scores = _prepare_scenarios()

    # ── STANDARD sweep (filter cascade BUYs) ──
    logger.info("=" * 60)
    logger.info("STANDARD SWEEP — composite filter on cascade BUYs")
    logger.info("=" * 60)
    std_results = in_sample_sweep(scenarios, composite_scores, invert=False)

    # ── INVERTED sweep (filter cascade NT-excl-ORB) ──
    logger.info("=" * 60)
    logger.info("INVERTED SWEEP — composite filter on cascade NO_TRADE-excl-ORB")
    logger.info("=" * 60)
    inv_results = in_sample_sweep(scenarios, composite_scores, invert=True)

    # ── CV-optimal thresholds ──
    # Use Phase 1 default config for both modes
    fixed_default = {
        "instant_reject_max_float": 2_000_000_000,
        "vwap_bias_threshold_pct": 2.0,
        "mfcs_buy_threshold": 0.25,
    }
    logger.info("CV-optimal thresholds for both modes...")
    cv_std = cv_optimal_threshold(scenarios, composite_scores, invert=False, fixed_overrides=fixed_default)
    cv_inv = cv_optimal_threshold(scenarios, composite_scores, invert=True, fixed_overrides=fixed_default)

    # ── Persist everything ──
    output = {
        "phase": "D220.4",
        "n_scenarios": len(scenarios),
        "sweep_grid": SWEEP_GRID,
        "composite_thresholds": COMPOSITE_THRESHOLDS,
        "fixed_default_for_cv": fixed_default,
        "standard": {
            "n_combos": len(std_results),
            "is_top10": [
                {k: v for k, v in r.items() if k != "pnl_list"}
                for r in best_combos(std_results, top_n=10)
            ],
            "cv_results": cv_std,
        },
        "inverted": {
            "n_combos": len(inv_results),
            "is_top10": [
                {k: v for k, v in r.items() if k != "pnl_list"}
                for r in best_combos(inv_results, top_n=10)
            ],
            "cv_results": cv_inv,
        },
        "heatmaps": {
            "standard_max_float_x_composite": _stringify_keys(heatmap_data(
                std_results, "config_max_float", "composite_threshold", "avg_pnl",
            )),
            "standard_vwap_x_composite": _stringify_keys(heatmap_data(
                std_results, "config_vwap", "composite_threshold", "avg_pnl",
            )),
            "standard_mfcs_x_composite": _stringify_keys(heatmap_data(
                std_results, "config_mfcs", "composite_threshold", "avg_pnl",
            )),
            "inverted_vwap_x_composite": _stringify_keys(heatmap_data(
                inv_results, "config_vwap", "composite_threshold", "avg_pnl",
            )),
            "inverted_max_float_x_composite": _stringify_keys(heatmap_data(
                inv_results, "config_max_float", "composite_threshold", "avg_pnl",
            )),
        },
    }

    out_path = _OUTPUT_DIR / "phase4_sweep_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("wrote %s", out_path)

    # ── Print headline ──
    print("\n" + "=" * 60)
    print("PHASE 4 SWEEP — HEADLINE RESULTS")
    print("=" * 60)
    print(f"\nSTANDARD (cascade BUYs filtered by composite):")
    if std_results:
        top = std_results[0] if not best_combos(std_results) else best_combos(std_results)[0]
        print(f"  IS top: n={top['n_trades']} WR={top['win_rate']*100:.1f}% avg={top['avg_pnl']*100:+.2f}% sum={top['sum_pnl']*100:+.1f}%")
        print(f"  CV: avg_chosen_threshold={cv_std['avg_chosen_threshold']:.3f}  "
              f"test_n_trades={cv_std['total_test_trades']}  "
              f"test_avg_pnl={cv_std['test_avg_pnl_pct']*100:+.2f}%")

    print(f"\nINVERTED (cascade NT-excl-ORB filtered by composite):")
    if inv_results:
        top = best_combos(inv_results)[0] if best_combos(inv_results) else inv_results[0]
        print(f"  IS top: n={top['n_trades']} WR={top['win_rate']*100:.1f}% avg={top['avg_pnl']*100:+.2f}% sum={top['sum_pnl']*100:+.1f}%")
        print(f"  CV: avg_chosen_threshold={cv_inv['avg_chosen_threshold']:.3f}  "
              f"test_n_trades={cv_inv['total_test_trades']}  "
              f"test_avg_pnl={cv_inv['test_avg_pnl_pct']*100:+.2f}%")

    return 0


def _stringify_keys(d: dict) -> dict:
    """JSON requires string keys for tuple keys."""
    return {f"{k[0]}|{k[1]}": v for k, v in d.items()}


if __name__ == "__main__":
    sys.exit(main())
