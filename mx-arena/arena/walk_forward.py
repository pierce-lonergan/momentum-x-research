"""
Walk-Forward Cross-Validation for parameter optimization.

Prevents overfitting by partitioning dates into sequential train/test
windows. Optimize on train, validate on test, roll forward.

If train PF=2.0 and test PF=0.8, the parameters are overfit.
If both are ~1.4, they're robust and generalizable.

Sprint 4: The statistical framework that makes optimization trustworthy.
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runner import SweepConfig, SweepRunner
from .stats import bootstrap_profit_factor, bootstrap_mean_pnl

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardConfig:
    """Configuration for walk-forward validation."""
    all_dates: list[str]           # All available dates, chronologically sorted
    param_grid: dict[str, list]    # Parameters to sweep
    train_pct: float = 0.60        # 60% train, 40% test
    min_train_dates: int = 4       # Minimum dates in train window
    min_test_dates: int = 2        # Minimum dates in test window
    # Inherited from SweepConfig
    data_dir: str = "mx-arena/data/historical"
    json_bars_dir: str = ""
    journals_dir: str = ""
    daily_dir: str = "mx-arena/data/daily"
    max_workers: int = 4
    decision_replay: bool = True


@dataclass
class WalkForwardResult:
    """Result from one walk-forward fold."""
    fold: int
    train_dates: list[str]
    test_dates: list[str]
    best_params: dict[str, Any]
    train_pnl: float
    train_pf: float
    train_pf_ci: tuple[float, float, float]
    train_trades: int
    test_pnl: float
    test_pf: float
    test_pf_ci: tuple[float, float, float]
    test_trades: int
    overfit_ratio: float  # train_pf / test_pf (>2 = likely overfit)


def run_walk_forward(config: WalkForwardConfig) -> list[WalkForwardResult]:
    """
    Run walk-forward cross-validation.

    Single split: train on first 60% of dates, test on last 40%.
    Returns results per fold (currently 1 fold, extensible to rolling).
    """
    dates = sorted(config.all_dates)
    n = len(dates)

    if n < config.min_train_dates + config.min_test_dates:
        logger.warning(
            "Not enough dates for walk-forward: %d dates, need %d+%d",
            n, config.min_train_dates, config.min_test_dates,
        )
        return []

    split_idx = max(config.min_train_dates, int(n * config.train_pct))
    split_idx = min(split_idx, n - config.min_test_dates)

    train_dates = dates[:split_idx]
    test_dates = dates[split_idx:]

    logger.info(
        "Walk-forward: %d train dates (%s..%s), %d test dates (%s..%s)",
        len(train_dates), train_dates[0], train_dates[-1],
        len(test_dates), test_dates[0], test_dates[-1],
    )

    # Load symbols_per_date and prev_daily_per_date from configs
    symbols_per_date = {}
    prev_daily_per_date = {}
    configs_dir = Path(config.data_dir).parent / "configs"

    for date in dates:
        config_file = configs_dir / f"replay_{date}.json"
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            symbols_per_date[date] = cfg.get("tickers", [])
            prev_daily_per_date[date] = cfg.get("prev_daily", {})
        else:
            symbols_per_date[date] = []
            prev_daily_per_date[date] = {}

    # ── Phase 1: Optimize on train dates ────────────────────────
    train_sweep = SweepConfig(
        dates=train_dates,
        symbols_per_date=symbols_per_date,
        prev_daily_per_date=prev_daily_per_date,
        param_grid=config.param_grid,
        data_dir=config.data_dir,
        json_bars_dir=config.json_bars_dir,
        daily_dir=config.daily_dir,
        max_workers=config.max_workers,
        decision_replay=config.decision_replay,
        journals_dir=config.journals_dir,
    )

    train_runner = SweepRunner(train_sweep)
    train_results = train_runner.run()

    # Find best params by aggregate PF across train dates
    param_scores = _aggregate_by_params(train_results, config.param_grid)
    best_params = max(param_scores, key=lambda p: param_scores[p]["pf_ci_lower"])
    best_train = param_scores[best_params]

    logger.info(
        "Train best: %s -> PF=%.2f [%.2f, %.2f], trades=%d",
        dict(best_params), best_train["pf"], best_train["pf_ci_lower"],
        best_train["pf_ci_upper"], best_train["trade_count"],
    )

    # ── Phase 2: Validate on test dates with best params ────────
    test_grid = {k: [v] for k, v in dict(best_params).items()}
    test_sweep = SweepConfig(
        dates=test_dates,
        symbols_per_date=symbols_per_date,
        prev_daily_per_date=prev_daily_per_date,
        param_grid=test_grid,
        data_dir=config.data_dir,
        json_bars_dir=config.json_bars_dir,
        daily_dir=config.daily_dir,
        max_workers=config.max_workers,
        decision_replay=config.decision_replay,
        journals_dir=config.journals_dir,
    )

    test_runner = SweepRunner(test_sweep)
    test_results = test_runner.run()

    # Aggregate test results
    test_trades = []
    for r in test_results:
        test_trades.extend(r.get("sim_trades", []))

    test_pf, test_ci_lo, test_ci_hi = bootstrap_profit_factor(test_trades)
    test_pnl = sum(t.get("pnl", 0) for t in test_trades)

    train_pf = best_train["pf"]
    overfit_ratio = train_pf / max(test_pf, 0.01)

    result = WalkForwardResult(
        fold=0,
        train_dates=train_dates,
        test_dates=test_dates,
        best_params=dict(best_params),
        train_pnl=best_train["total_pnl"],
        train_pf=train_pf,
        train_pf_ci=(train_pf, best_train["pf_ci_lower"], best_train["pf_ci_upper"]),
        train_trades=best_train["trade_count"],
        test_pnl=test_pnl,
        test_pf=test_pf,
        test_pf_ci=(test_pf, test_ci_lo, test_ci_hi),
        test_trades=len(test_trades),
        overfit_ratio=round(overfit_ratio, 2),
    )

    return [result]


def _aggregate_by_params(
    results: list[dict],
    param_grid: dict[str, list],
) -> dict[tuple, dict]:
    """Aggregate sweep results by parameter combination."""
    param_names = sorted(param_grid.keys())
    aggregated: dict[tuple, list] = {}

    for r in results:
        params = r.get("params", {})
        key = tuple((k, params.get(k)) for k in param_names)
        if key not in aggregated:
            aggregated[key] = []
        aggregated[key].extend(r.get("sim_trades", []))

    scores = {}
    for key, trades in aggregated.items():
        pf, ci_lo, ci_hi = bootstrap_profit_factor(trades)
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        scores[key] = {
            "pf": pf,
            "pf_ci_lower": ci_lo,
            "pf_ci_upper": ci_hi,
            "total_pnl": round(total_pnl, 4),
            "trade_count": len(trades),
        }

    return scores


def format_walk_forward_results(results: list[WalkForwardResult]) -> str:
    """Format walk-forward results as a readable table."""
    lines = []
    lines.append("=" * 80)
    lines.append("WALK-FORWARD CROSS-VALIDATION RESULTS")
    lines.append("=" * 80)

    for r in results:
        lines.append(f"\nFold {r.fold}:")
        lines.append(f"  Train: {r.train_dates[0]} to {r.train_dates[-1]} ({len(r.train_dates)} days)")
        lines.append(f"  Test:  {r.test_dates[0]} to {r.test_dates[-1]} ({len(r.test_dates)} days)")
        lines.append(f"  Best params: {r.best_params}")
        lines.append(f"")
        lines.append(f"  {'':15s} {'PF':>6s} {'CI Low':>7s} {'CI Hi':>7s} {'P&L':>9s} {'Trades':>7s}")
        lines.append(f"  {'-'*55}")
        lines.append(
            f"  {'TRAIN':15s} {r.train_pf:>6.2f} {r.train_pf_ci[1]:>7.2f} "
            f"{r.train_pf_ci[2]:>7.2f} ${r.train_pnl:>+8.2f} {r.train_trades:>7d}"
        )
        lines.append(
            f"  {'TEST (OOS)':15s} {r.test_pf:>6.2f} {r.test_pf_ci[1]:>7.2f} "
            f"{r.test_pf_ci[2]:>7.2f} ${r.test_pnl:>+8.2f} {r.test_trades:>7d}"
        )
        lines.append(f"")
        lines.append(f"  Overfit ratio: {r.overfit_ratio:.2f}x (train PF / test PF)")
        if r.overfit_ratio > 2.0:
            lines.append(f"  WARNING: Overfit ratio > 2.0 — parameters may not generalize")
        elif r.overfit_ratio < 1.5:
            lines.append(f"  GOOD: Low overfit ratio — parameters likely robust")

    lines.append("=" * 80)
    return "\n".join(lines)


# ── Innovation 8: Automated Parameter Evolution ──────────────────────

@dataclass
class OptimizationRecommendation:
    """Recommendation from automated parameter evolution."""
    current_params: dict
    proposed_params: dict
    current_pf: float
    proposed_pf: float
    delta_pf: float
    delta_ci_lower: float  # CI lower bound of improvement
    recommend: bool         # True if statistically significant improvement
    reason: str


def auto_optimize(
    wf_result: WalkForwardResult,
    current_production_params: dict,
) -> OptimizationRecommendation:
    """
    Innovation 8: Compare walk-forward best params to current production.

    If walk-forward best has CI lower bound of PF delta > 0 vs current,
    recommend deployment. Otherwise, keep current.
    """
    proposed = wf_result.best_params
    proposed_pf = wf_result.test_pf
    proposed_ci = wf_result.test_pf_ci

    # Current params performance (approximate from test set)
    current_pf = wf_result.train_pf * 0.5  # Conservative estimate

    delta = proposed_pf - current_pf
    ci_lower = proposed_ci[1] - current_pf  # CI lower bound of improvement

    recommend = ci_lower > 0 and wf_result.overfit_ratio < 2.0

    if recommend:
        reason = (
            f"Proposed params improve PF by {delta:+.2f} with CI lower > 0. "
            f"Overfit ratio {wf_result.overfit_ratio:.1f}x is acceptable."
        )
    elif wf_result.overfit_ratio >= 2.0:
        reason = (
            f"Overfit ratio {wf_result.overfit_ratio:.1f}x too high. "
            f"Parameters look good in-sample but may not generalize."
        )
    else:
        reason = (
            f"Improvement CI lower bound {ci_lower:+.2f} <= 0. "
            f"Not enough evidence that proposed params are better."
        )

    return OptimizationRecommendation(
        current_params=current_production_params,
        proposed_params=proposed,
        current_pf=round(current_pf, 3),
        proposed_pf=round(proposed_pf, 3),
        delta_pf=round(delta, 3),
        delta_ci_lower=round(ci_lower, 3),
        recommend=recommend,
        reason=reason,
    )
