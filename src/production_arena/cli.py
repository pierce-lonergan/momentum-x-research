"""Production arena CLI.

Usage:
    python -m src.production_arena.cli --validate-load
    python -m src.production_arena.cli --date 2026-04-16
    python -m src.production_arena.cli --dates 2026-01-27:2026-04-14 \\
        --output data/arena_runs/full.jsonl --parallel 4
    python -m src.production_arena.cli --ticker IMMP
    python -m src.production_arena.cli --sweep instant_reject_max_float=200000000,2000000000 \\
        --sweep mfcs_buy_threshold=0.20,0.25,0.30
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

from tqdm import tqdm

from src.production_arena.aggregator import (
    aggregate_by_day,
    aggregate_by_gate,
    simulate_session_pnl,
)
from src.production_arena.pipeline_runner import run_scenario, run_scenarios
from src.production_arena.scenarios import load_scenarios
from src.production_arena.types import ArenaConfig, ProductionVerdict
from src.production_arena.verdict import write_verdicts

logger = logging.getLogger(__name__)

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT: Path = _PROJECT_ROOT / "data" / "arena_runs" / "verdicts.jsonl"


# ── Parsing helpers ──────────────────────────────────────────────────────


def _parse_date_range(arg: str) -> tuple[str, str]:
    """'2026-01-27:2026-04-14' -> ('2026-01-27', '2026-04-14')."""
    parts = arg.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"--dates must be 'YYYY-MM-DD:YYYY-MM-DD' (got '{arg}')"
        )
    for p in parts:
        try:
            datetime.strptime(p, "%Y-%m-%d")
        except ValueError as e:
            raise argparse.ArgumentTypeError(f"bad date '{p}' in --dates: {e}")
    return (parts[0], parts[1])


def _parse_sweep(arg: str) -> tuple[str, list]:
    """'key=v1,v2,v3' -> (key, [v1, v2, v3]) with type inference."""
    if "=" not in arg:
        raise argparse.ArgumentTypeError(f"--sweep must be 'KEY=v1,v2,...' (got '{arg}')")
    key, vals = arg.split("=", 1)
    out: list = []
    for v in vals.split(","):
        v = v.strip()
        if v.lower() == "inf":
            out.append(float("inf"))
        elif "." in v:
            try:
                out.append(float(v))
            except ValueError:
                out.append(v)
        else:
            try:
                out.append(int(v))
            except ValueError:
                try:
                    out.append(float(v))
                except ValueError:
                    out.append(v)
    return (key.strip(), out)


def _arena_config_from_overrides(overrides: dict) -> ArenaConfig:
    """Build an ArenaConfig from a dict of {field_name: value} for one sweep combo."""
    valid_fields = {f.name for f in dataclasses.fields(ArenaConfig)}
    kwargs = {}
    for k, v in overrides.items():
        if k not in valid_fields:
            logger.warning("Unknown ArenaConfig field '%s' (ignored)", k)
            continue
        kwargs[k] = v
    return ArenaConfig(**kwargs)


# ── Reporting ────────────────────────────────────────────────────────────


def _print_summary(verdicts: list[ProductionVerdict], elapsed_s: float, label: str = "") -> None:
    """Print a gate-by-gate + P&L summary table."""
    if not verdicts:
        print("(no verdicts)")
        return
    by_gate = aggregate_by_gate(verdicts)
    pnl = simulate_session_pnl(verdicts, return_per_day=False)
    n = len(verdicts)
    n_buy = by_gate.pop("__buy__", 0)
    n_err = by_gate.pop("__error__", 0)
    n_unk = by_gate.pop("__unknown_gate__", 0)
    title = f"=== ARENA RESULT  {label}" if label else "=== ARENA RESULT"
    print(f"\n{title}  ({n} scenarios in {elapsed_s:.1f}s) ===")
    print(f"  decisions: BUY={n_buy}  NO_TRADE={n - n_buy - n_err}  ERROR={n_err}")
    if n_unk:
        print(f"  WARNING: {n_unk} verdicts with unknown gate (data bug)")
    if by_gate:
        print(f"  rejection cascade (top {min(10, len(by_gate))}):")
        for gate, count in sorted(by_gate.items(), key=lambda x: -x[1])[:10]:
            pct = count / n * 100
            print(f"    {count:4d} ({pct:4.1f}%)  {gate}")
    if n_buy > 0:
        print(f"  simulated session P&L:")
        print(f"    starting equity:   ${pnl['starting_equity']:>12,.0f}")
        print(f"    ending equity:     ${pnl['ending_equity']:>12,.0f}")
        print(f"    total return:      {pnl['total_pnl_pct']*100:+.2f}%")
        print(f"    win rate:          {pnl['win_rate']*100:.1f}%  ({pnl['n_trades']} trades)")
        print(f"    max drawdown:      {pnl['max_drawdown_pct']*100:.2f}%")
        if pnl["sharpe_annualized"] is not None:
            print(f"    Sharpe (annual):   {pnl['sharpe_annualized']:.2f}")


# ── Command dispatchers ──────────────────────────────────────────────────


def _cmd_validate_load(args: argparse.Namespace) -> int:
    coll = load_scenarios()
    print(f"loaded {len(coll)} scenarios")
    print(f"stats: {coll.stats}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """The main run path. Loads scenarios, applies any single-config override, runs."""
    # Build the scenario set
    date_range = None
    if args.dates:
        date_range = args.dates
    elif args.date:
        date_range = (args.date, args.date)
    tickers = [args.ticker] if args.ticker else None
    coll = load_scenarios(date_range=date_range, tickers=tickers)
    scenarios = list(coll)
    if not scenarios:
        print("no scenarios match filters", file=sys.stderr)
        return 1
    print(f"running {len(scenarios)} scenarios "
          f"(workers={args.parallel})")

    config = _arena_config_from_overrides(_apply_single_overrides(args))

    # Optional composite-score logging (Phase 3.4). Pre-load BOTH models once
    # rather than once per scenario.
    composite_loaded = False
    if args.log_composite_score:
        try:
            from src.composite.score import composite_score_both  # noqa: F401
            composite_loaded = True
            logger.info("composite-score logging ENABLED")
        except Exception as e:
            logger.warning("composite-score logging requested but disabled: %s", e)

    import time
    t0 = time.perf_counter()
    verdicts: list[ProductionVerdict] = []
    # Build a lookup so we can re-attach the original Scenario for composite
    # scoring (the runner returns just verdicts, no scenario back-reference).
    scenarios_by_key = {(s.session_date, s.ticker): s for s in scenarios}
    iterator = run_scenarios(scenarios, config=config, parallel_workers=args.parallel)
    for v in tqdm(iterator, total=len(scenarios), unit="scn"):
        if composite_loaded:
            v = _annotate_with_composite(v, scenarios_by_key.get((v.session_date, v.ticker)))
        verdicts.append(v)
    elapsed_s = time.perf_counter() - t0

    # Write JSONL
    output_path = Path(args.output) if args.output else _DEFAULT_OUTPUT
    n_written = write_verdicts(verdicts, output_path)
    print(f"\nwrote {n_written} verdicts -> {output_path}")

    _print_summary(verdicts, elapsed_s)
    if composite_loaded:
        _print_composite_correlation(verdicts)
    return 0


def _annotate_with_composite(verdict, scenario):
    """Attach composite_score_prescore + composite_score_full to a verdict.

    Pure-function call into src.composite.score — never raises into the arena.
    """
    if scenario is None:
        return verdict
    try:
        from src.composite.score import composite_score_both
        is_buy = verdict.decision == "BUY"
        scores = composite_score_both(scenario.premarket_features, arena_buy_verdict=is_buy)
    except Exception as e:
        logger.warning("composite annotation failed for %s %s: %s",
                       verdict.ticker, verdict.session_date, e)
        return verdict
    # ProductionVerdict is frozen — use dataclasses.replace
    import dataclasses
    return dataclasses.replace(
        verdict,
        composite_score_prescore=scores.get("prescore"),
        composite_score_full=scores.get("full"),
    )


def _print_composite_correlation(verdicts):
    """Quick diagnostic: how does composite_score correlate with actual PnL on BUYs?"""
    buys = [v for v in verdicts
            if v.decision == "BUY" and v.would_be_pnl_pct is not None
            and v.composite_score_full is not None]
    if len(buys) < 5:
        print("  composite correlation: insufficient BUYs with PnL+composite")
        return
    import statistics
    cs = [v.composite_score_full for v in buys]
    pnl = [v.would_be_pnl_pct for v in buys]
    # Spearman via rank correlation (no scipy dep)
    ranks_cs = sorted(range(len(cs)), key=lambda i: cs[i])
    rank_of_cs = [0] * len(cs)
    for r, i in enumerate(ranks_cs):
        rank_of_cs[i] = r
    ranks_pnl = sorted(range(len(pnl)), key=lambda i: pnl[i])
    rank_of_pnl = [0] * len(pnl)
    for r, i in enumerate(ranks_pnl):
        rank_of_pnl[i] = r
    n = len(cs)
    mean_a = sum(rank_of_cs) / n
    mean_b = sum(rank_of_pnl) / n
    num = sum((rank_of_cs[i] - mean_a) * (rank_of_pnl[i] - mean_b) for i in range(n))
    denom_a = sum((r - mean_a) ** 2 for r in rank_of_cs) ** 0.5
    denom_b = sum((r - mean_b) ** 2 for r in rank_of_pnl) ** 0.5
    spearman = num / (denom_a * denom_b) if denom_a > 0 and denom_b > 0 else 0.0
    mean_cs = statistics.mean(cs)
    print(f"  composite_full vs realized PnL on {n} BUYs:")
    print(f"    mean composite score: {mean_cs:.3f}")
    print(f"    Spearman rank correlation: {spearman:+.3f}")


def _cmd_sweep(args: argparse.Namespace) -> int:
    """Cartesian-product sweep across multiple config axes."""
    sweep_dict = dict(args.sweep)
    keys = list(sweep_dict.keys())
    values = [sweep_dict[k] for k in keys]
    combos = list(product(*values))
    print(f"sweep grid: {len(combos)} combinations across {len(keys)} axes")
    for k in keys:
        print(f"  {k}: {sweep_dict[k]}")

    # Build the scenario set once (reused across sweep combos)
    date_range = args.dates if args.dates else None
    coll = load_scenarios(date_range=date_range)
    scenarios = list(coll)
    print(f"running {len(scenarios)} scenarios per combination = "
          f"{len(scenarios) * len(combos)} total runs")

    output_path = Path(args.output) if args.output else _DEFAULT_OUTPUT.with_suffix(".sweep.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    n_total_written = 0
    import time
    t0_total = time.perf_counter()

    for combo_idx, combo_vals in enumerate(combos, start=1):
        overrides = dict(zip(keys, combo_vals))
        config = _arena_config_from_overrides(overrides)
        label = ", ".join(f"{k}={v}" for k, v in overrides.items())

        t0 = time.perf_counter()
        verdicts: list[ProductionVerdict] = []
        iterator = run_scenarios(scenarios, config=config, parallel_workers=args.parallel)
        for v in tqdm(iterator, total=len(scenarios), unit="scn",
                      desc=f"combo {combo_idx}/{len(combos)}", leave=False):
            verdicts.append(v)
        elapsed_s = time.perf_counter() - t0

        # Tag verdicts with the combo and append
        tagged_path = output_path
        # Stuff the combo into a wrapping line so consumers can demux
        combo_meta = {"_sweep_combo": overrides}
        with open(tagged_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(combo_meta) + "\n")
        n_total_written += write_verdicts(verdicts, tagged_path)
        _print_summary(verdicts, elapsed_s, label=label)

    print(f"\nsweep complete in {time.perf_counter() - t0_total:.1f}s, "
          f"{n_total_written} verdict rows -> {output_path}")
    return 0


def _apply_single_overrides(args: argparse.Namespace) -> dict:
    """Convert --config-override KEY=VAL flags to an overrides dict."""
    out: dict = {}
    for s in args.config_override or []:
        if "=" not in s:
            logger.warning("ignoring malformed --config-override '%s'", s)
            continue
        k, v = s.split("=", 1)
        # Type inference
        v_lower = v.lower()
        if v_lower in ("none", "null"):
            out[k] = None
        elif v_lower in ("true", "false"):
            out[k] = v_lower == "true"
        elif v_lower == "inf":
            out[k] = float("inf")
        else:
            try:
                out[k] = int(v) if "." not in v else float(v)
            except ValueError:
                out[k] = v
    return out


# ── Main ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.production_arena.cli",
        description="Production arena — replay the gate cascade against historical data.",
    )
    p.add_argument("--validate-load", action="store_true",
                   help="Just load scenarios and print stats; don't run.")
    p.add_argument("--date", help="Single session date (YYYY-MM-DD).")
    p.add_argument("--dates", type=_parse_date_range,
                   help="Date range (YYYY-MM-DD:YYYY-MM-DD).")
    p.add_argument("--ticker", help="Filter to one ticker symbol.")
    p.add_argument("--output", help="Output JSONL path. Defaults to data/arena_runs/verdicts.jsonl.")
    p.add_argument("--parallel", type=int, default=1,
                   help="Worker process count for multiprocessing.Pool. Default 1 (serial).")
    p.add_argument("--config-override", action="append",
                   help="KEY=VAL ArenaConfig override. Repeatable.")
    p.add_argument("--sweep", type=_parse_sweep, action="append",
                   help="KEY=v1,v2,... counterfactual sweep axis. Repeatable.")
    p.add_argument("--log-composite-score", action="store_true",
                   help="Annotate every verdict with composite_v0_full + prescore "
                        "scores. Requires models/composite_v0_*.pkl to exist "
                        "(run `python -m src.composite.train` first).")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.validate_load:
        return _cmd_validate_load(args)
    if args.sweep:
        return _cmd_sweep(args)
    return _cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
