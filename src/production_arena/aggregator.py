"""Production Arena — verdict aggregation and counterfactual sweeps.

Inputs are iterables of `ProductionVerdict` (from pipeline_runner.py) or
`Scenario` (from scenarios.py). All functions are pure compute against
in-memory data — no I/O, no network, no global mutation.

The headline metric is `simulate_session_pnl`, which walks verdicts in
chronological order, opens positions at Tier 1 sizing (2% of equity by
default = $2,840 on $142K), respects `max_concurrent` per day, and
returns ending equity, win rate, annualized Sharpe, and max drawdown.

`counterfactual_sweep` runs a Cartesian product of `param_grid` overrides
through a caller-supplied `pipeline_runner_fn`. Set `parallel_workers > 1`
to fan the (scenario, config) tuples across a `multiprocessing.Pool`.

See docs/research-log/02_arena_critique.md for design rationale and
docs/research-log/05_action_plan.md Day 3 for usage.
"""

from __future__ import annotations

import itertools
import logging
import math
import multiprocessing
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from src.production_arena.types import (
    ArenaConfig,
    ProductionVerdict,
    Scenario,
    SweepResult,
)

# Module-level anchor for any future on-disk artifacts. Aggregator itself
# does not touch the filesystem, but expose this so callers can locate
# data/arena/ relative to the repo root, not relative to cwd.
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("production_arena.aggregator")


# Sentinel keys used in the gate-rollup dict so callers can distinguish
# rejections from buys/errors without a separate branching path.
GATE_KEY_BUY = "__buy__"
GATE_KEY_ERROR = "__error__"
GATE_KEY_UNKNOWN = "__unknown_gate__"

TRADING_DAYS_PER_YEAR: int = 252


# ── Per-gate aggregation ────────────────────────────────────────────────


def aggregate_by_gate(verdicts: Iterable[ProductionVerdict]) -> dict[str, int]:
    """Count rejections per gate (file:line).

    BUY verdicts are counted under '__buy__'. ERROR verdicts under '__error__'.
    NO_TRADE verdicts whose `gate_rejected` is None get bucketed under
    '__unknown_gate__' so the totals reconcile with the input count.

    Returns a plain dict for JSON serialization.
    """
    counts: dict[str, int] = defaultdict(int)
    for v in verdicts:
        if v.decision == "BUY":
            counts[GATE_KEY_BUY] += 1
        elif v.decision == "ERROR":
            counts[GATE_KEY_ERROR] += 1
        else:  # NO_TRADE
            gate = v.gate_rejected if v.gate_rejected else GATE_KEY_UNKNOWN
            counts[gate] += 1
    return dict(counts)


# ── Per-day aggregation ─────────────────────────────────────────────────


def aggregate_by_day(verdicts: Iterable[ProductionVerdict]) -> dict[str, dict[str, Any]]:
    """Roll up verdicts by `session_date`.

    Per-day fields:
        total: int             — every verdict for the day
        buy: int               — count of decision == "BUY"
        error: int             — count of decision == "ERROR"
        reject_by_gate: dict   — {gate: count} for NO_TRADE verdicts
        pnl_pct_sum: float     — sum of would_be_pnl_pct across BUY verdicts
        mfe_pct_sum: float     — sum of mfe_pct across BUY verdicts (None → 0)
        tickers_evaluated: list[str]  — sorted unique tickers (set serialized)

    The tickers set is converted to a sorted list inline so the returned
    structure is JSON-serializable without further massaging.
    """
    # Use a working dict of mutable accumulators, then post-process to
    # convert the ticker set to a sorted list before returning.
    work: dict[str, dict[str, Any]] = {}

    for v in verdicts:
        day = v.session_date
        bucket = work.get(day)
        if bucket is None:
            bucket = {
                "total": 0,
                "buy": 0,
                "error": 0,
                "reject_by_gate": defaultdict(int),
                "pnl_pct_sum": 0.0,
                "mfe_pct_sum": 0.0,
                "_tickers": set(),
            }
            work[day] = bucket

        bucket["total"] += 1
        bucket["_tickers"].add(v.ticker)

        if v.decision == "BUY":
            bucket["buy"] += 1
            if v.would_be_pnl_pct is not None:
                bucket["pnl_pct_sum"] += float(v.would_be_pnl_pct)
            if v.mfe_pct is not None:
                bucket["mfe_pct_sum"] += float(v.mfe_pct)
        elif v.decision == "ERROR":
            bucket["error"] += 1
        else:  # NO_TRADE
            gate = v.gate_rejected if v.gate_rejected else GATE_KEY_UNKNOWN
            bucket["reject_by_gate"][gate] += 1

    out: dict[str, dict[str, Any]] = {}
    for day, bucket in work.items():
        out[day] = {
            "total": bucket["total"],
            "buy": bucket["buy"],
            "error": bucket["error"],
            "reject_by_gate": dict(bucket["reject_by_gate"]),
            "pnl_pct_sum": bucket["pnl_pct_sum"],
            "mfe_pct_sum": bucket["mfe_pct_sum"],
            "tickers_evaluated": sorted(bucket["_tickers"]),
        }
    return out


# ── Session P&L simulation ──────────────────────────────────────────────


def _annualized_sharpe(daily_returns: list[float]) -> float | None:
    """Annualized Sharpe assuming daily returns and 252 trading days/year.

    Returns None for fewer than 5 daily observations or zero stdev (no
    dispersion → undefined Sharpe). Risk-free rate assumed 0; this is
    consistent with how SweepResult.sharpe_annualized is computed.
    """
    if len(daily_returns) < 5:
        return None
    try:
        stdev = statistics.stdev(daily_returns)
    except statistics.StatisticsError:
        return None
    if stdev == 0:
        return None
    mean = statistics.fmean(daily_returns)
    return (mean / stdev) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _max_drawdown_pct(equity_curve: list[float]) -> float:
    """Peak-to-trough max drawdown as a positive percentage of peak equity.

    Empty or single-point curves yield 0.0. Returns the worst drawdown
    encountered as a fraction (0.143 == 14.3% drawdown).
    """
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def simulate_session_pnl(
    verdicts: Iterable[ProductionVerdict],
    starting_equity: float = 142_000.0,
    kelly_tier_pct: float = 0.02,
    max_concurrent: int = 3,
    return_per_day: bool = True,
) -> dict[str, Any]:
    """Walk verdicts chronologically and simulate the resulting account curve.

    Each BUY:
      - position_size = starting_equity * kelly_tier_pct (NO compounding —
        size stays fixed; this matches the user's Tier-1 risk budget of
        $2,840 on $142K equity)
      - skip if `max_concurrent` BUYs have already been opened that day
      - realized $ P&L = position_size * would_be_pnl_pct
      - missing would_be_pnl_pct is treated as a 0% trade (still consumes
        a concurrency slot — the verdict said BUY)

    Returns a JSON-friendly dict (see module docstring schema). Sharpe is
    None when fewer than 5 daily observations exist (the standard guard
    we use elsewhere — sub-week samples are not a meaningful Sharpe).
    """
    # Materialize and sort: deterministic order is required for the equity
    # curve to be reproducible. Sort key is (date, ticker) — ticker breaks
    # ties so two BUYs on the same day order the same way every run.
    materialized = sorted(verdicts, key=lambda v: (v.session_date, v.ticker))

    equity = float(starting_equity)
    position_size = float(starting_equity) * float(kelly_tier_pct)
    n_trades = 0
    n_winners = 0
    daily_pnl_dollars: dict[str, float] = defaultdict(float)
    daily_trade_count: dict[str, int] = defaultdict(int)
    daily_open_count: dict[str, int] = defaultdict(int)
    equity_curve: list[float] = [equity]
    per_day_records: list[dict[str, Any]] = []

    current_day: str | None = None

    for v in materialized:
        if v.decision != "BUY":
            continue

        day = v.session_date
        if current_day is None:
            current_day = day

        # Concurrency throttle is per-day (the user's `max_concurrent=3`
        # rule resets each session — it's not a true position book).
        if daily_open_count[day] >= max_concurrent:
            continue

        daily_open_count[day] += 1
        n_trades += 1

        pnl_pct = v.would_be_pnl_pct if v.would_be_pnl_pct is not None else 0.0
        pnl_dollars = position_size * float(pnl_pct)

        equity += pnl_dollars
        daily_pnl_dollars[day] += pnl_dollars
        daily_trade_count[day] += 1
        if pnl_pct > 0:
            n_winners += 1

    # Build the equity curve by replaying days in sorted order. We snapshot
    # equity at the close of each day so the drawdown calc reflects
    # session-to-session swings, not intra-day churn.
    running = float(starting_equity)
    daily_returns: list[float] = []
    for day in sorted(daily_pnl_dollars.keys()):
        prev = running
        running += daily_pnl_dollars[day]
        equity_curve.append(running)
        if prev > 0:
            daily_returns.append(daily_pnl_dollars[day] / prev)
        per_day_records.append({
            "date": day,
            "pnl_pct": (daily_pnl_dollars[day] / prev) if prev > 0 else 0.0,
            "n_trades": daily_trade_count[day],
        })

    ending_equity = equity
    total_pnl_pct = (
        (ending_equity - starting_equity) / starting_equity if starting_equity > 0 else 0.0
    )
    win_rate = (n_winners / n_trades) if n_trades > 0 else 0.0
    sharpe = _annualized_sharpe(daily_returns)
    max_dd = _max_drawdown_pct(equity_curve)

    return {
        "starting_equity": float(starting_equity),
        "ending_equity": float(ending_equity),
        "total_pnl_pct": float(total_pnl_pct),
        "n_trades": int(n_trades),
        "win_rate": float(win_rate),
        "sharpe_annualized": sharpe,
        "max_drawdown_pct": float(max_dd),
        "daily_pnl": per_day_records if return_per_day else [],
    }


# ── Counterfactual sweep ────────────────────────────────────────────────


@dataclass(frozen=True)
class _SweepJob:
    """Internal worker payload — picklable so it crosses Pool boundaries.

    Keep this a plain frozen dataclass; multiprocessing pickles by value
    and any closure or lambda would break the worker fanout.
    """
    scenario: Scenario
    config: ArenaConfig
    combo_key: tuple[tuple[str, Any], ...]


def _run_one_job(
    job: _SweepJob,
    pipeline_runner_fn: Callable[[Scenario, ArenaConfig], ProductionVerdict],
) -> tuple[tuple[tuple[str, Any], ...], ProductionVerdict]:
    """Execute one (scenario, config) job and return the keyed verdict."""
    verdict = pipeline_runner_fn(job.scenario, job.config)
    return job.combo_key, verdict


def _build_combos(param_grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product over `param_grid`. Empty grid yields one empty combo."""
    if not param_grid:
        return [{}]
    keys = sorted(param_grid.keys())  # sorted for deterministic combo order
    value_lists = [param_grid[k] for k in keys]
    combos: list[dict[str, Any]] = []
    for tup in itertools.product(*value_lists):
        combos.append({k: v for k, v in zip(keys, tup)})
    return combos


def _config_from_combo(combo: dict[str, Any]) -> ArenaConfig:
    """Construct an ArenaConfig from a combo dict, ignoring unknown keys.

    Unknown keys log a warning rather than raising — this lets callers
    sweep over combos that exercise both production and shadow params
    without crashing on the shadow-only ones during early bring-up.
    """
    valid = {
        "instant_reject_max_float",
        "instant_reject_min_price",
        "vwap_bias_threshold_pct",
        "mfcs_buy_threshold",
        "enable_shadow_score",
    }
    kwargs: dict[str, Any] = {}
    for k, v in combo.items():
        if k in valid:
            kwargs[k] = v
        else:
            logger.warning("counterfactual_sweep: ignoring unknown param %r", k)
    return ArenaConfig(**kwargs)


def _aggregate_to_sweep_result(
    combo: dict[str, Any],
    verdicts: list[ProductionVerdict],
) -> SweepResult:
    """Collapse a list of verdicts into one SweepResult row."""
    n_scenarios = len(verdicts)
    buys = [v for v in verdicts if v.decision == "BUY"]
    n_trades = len(buys)

    returns_pct = [
        float(v.would_be_pnl_pct) for v in buys if v.would_be_pnl_pct is not None
    ]
    n_winners = sum(1 for r in returns_pct if r > 0)
    n_losers = sum(1 for r in returns_pct if r < 0)
    win_rate = (n_winners / n_trades) if n_trades > 0 else 0.0
    avg_return = statistics.fmean(returns_pct) if returns_pct else 0.0
    median_return = statistics.median(returns_pct) if returns_pct else 0.0

    # MFE-capture ratio: would_be_pnl_pct / mfe_pct, averaged across BUYs
    # that have both fields populated (skip the unlabeled ones).
    capture_ratios: list[float] = []
    for v in buys:
        if v.would_be_pnl_pct is None or v.mfe_pct is None or v.mfe_pct == 0:
            continue
        capture_ratios.append(float(v.would_be_pnl_pct) / float(v.mfe_pct))
    avg_mfe_capture = statistics.fmean(capture_ratios) if capture_ratios else 0.0

    # Equity curve from the BUY sequence (chronological by session date).
    sorted_buys = sorted(buys, key=lambda v: (v.session_date, v.ticker))
    equity = 1.0  # unit-equity model — captures shape, not dollars
    curve = [equity]
    daily_pnl: dict[str, float] = defaultdict(float)
    for v in sorted_buys:
        r = float(v.would_be_pnl_pct) if v.would_be_pnl_pct is not None else 0.0
        equity *= 1.0 + r
        curve.append(equity)
        daily_pnl[v.session_date] += r
    max_dd = _max_drawdown_pct(curve)
    sharpe = _annualized_sharpe(list(daily_pnl.values()))

    return SweepResult(
        param_combo=dict(combo),
        n_scenarios=n_scenarios,
        n_trades=n_trades,
        n_winners=n_winners,
        n_losers=n_losers,
        win_rate=float(win_rate),
        avg_return_pct=float(avg_return),
        median_return_pct=float(median_return),
        max_drawdown_pct=float(max_dd),
        sharpe_annualized=sharpe,
        avg_mfe_captured_pct=float(avg_mfe_capture),
    )


def counterfactual_sweep(
    scenarios: Iterable[Scenario],
    pipeline_runner_fn: Callable[[Scenario, ArenaConfig], ProductionVerdict],
    param_grid: dict[str, list[Any]],
    parallel_workers: int = 1,
) -> list[SweepResult]:
    """Run the Cartesian product of `param_grid` and aggregate per-combo.

    For a grid like
        {
            'instant_reject_max_float': [200_000_000, 1_000_000_000, 2_000_000_000],
            'mfcs_buy_threshold': [0.20, 0.25, 0.30],
        }
    this produces 9 SweepResult rows.

    Parallelism:
      - parallel_workers == 1: simple in-process for-loop.
      - parallel_workers > 1: multiprocessing.Pool fans (scenario, config)
        tuples across workers.

    SAFETY: When parallel_workers > 1, `pipeline_runner_fn` MUST be a
    top-level (module-level) function that is picklable. Closures, lambdas,
    and bound methods on local objects will fail at fork/spawn time. The
    in-process branch (parallel_workers == 1) accepts any callable.
    """
    materialized_scenarios = list(scenarios)
    combos = _build_combos(param_grid)

    # Build all (scenario, config) jobs up front so we can either iterate
    # sequentially or hand them to a pool with one round-trip.
    jobs: list[_SweepJob] = []
    combo_index: list[dict[str, Any]] = []
    for combo in combos:
        cfg = _config_from_combo(combo)
        combo_key = tuple(sorted(combo.items()))
        combo_index.append(combo)
        for sc in materialized_scenarios:
            jobs.append(_SweepJob(scenario=sc, config=cfg, combo_key=combo_key))

    # Bucket verdicts back into their combo on the way out.
    by_combo: dict[tuple[tuple[str, Any], ...], list[ProductionVerdict]] = defaultdict(list)

    if parallel_workers <= 1:
        for job in jobs:
            key, verdict = _run_one_job(job, pipeline_runner_fn)
            by_combo[key].append(verdict)
    else:
        # starmap requires picklable args — pipeline_runner_fn is the
        # caller's contract to satisfy. We do NOT silently fall back to
        # in-process: if pickling fails the user needs the real error.
        with multiprocessing.Pool(processes=parallel_workers) as pool:
            results = pool.starmap(
                _run_one_job,
                [(job, pipeline_runner_fn) for job in jobs],
            )
        for key, verdict in results:
            by_combo[key].append(verdict)

    out: list[SweepResult] = []
    for combo in combo_index:
        key = tuple(sorted(combo.items()))
        out.append(_aggregate_to_sweep_result(combo, by_combo[key]))
    return out


# ── Convenience generator (unused by the API but handy for callers) ─────


def iter_buys(verdicts: Iterable[ProductionVerdict]) -> Iterator[ProductionVerdict]:
    """Yield only the BUY verdicts from a stream — preserves laziness."""
    for v in verdicts:
        if v.decision == "BUY":
            yield v
