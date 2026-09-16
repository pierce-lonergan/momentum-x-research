#!/usr/bin/env python3
"""CLI for the LLM Arena Labeled Dataset.

Commands:
    seed      — Run auto-labeling pipeline and save to data/llm_arena/
    stats     — Print dataset statistics
    list      — List scenarios (with optional filters)
    show      — Show details for one scenario by ID
    validate  — Validate dataset integrity
    replay    — Replay stored agent signals through the harness
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Make src importable when run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env so API keys are available for live-mode calls
try:
    from dotenv import load_dotenv as _load_dotenv
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _load_dotenv(os.path.join(_repo_root, ".env"), override=False)
except ImportError:
    pass  # dotenv optional — caller can set env vars directly

from src.llm_arena import AutoLabeler, DatasetManager, AgentConfig, AgentHarness
from src.llm_arena.models import CatalystType, LabelConfidence, StockOutcome
from src.llm_arena.scoring import MetricsCalculator, format_scorecard, format_comparison
from src.llm_arena.experiment import ExperimentEngine, format_experiment_result
from src.llm_arena.experiments_library import (
    get_experiment,
    list_available_experiments,
    _EXPERIMENT_REGISTRY,
)
from src.llm_arena.report import ReportGenerator
from src.llm_arena.ensemble_experiment import (
    EnsembleExperiment,
    format_ensemble_result,
)
from src.llm_arena.harness import MODEL_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_data_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    return os.path.join(repo_root, "data", "llm_arena")


def _reports_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "reports")


def _save_report_if_requested(report: str, args, data_dir: str, name: str) -> None:
    """If --save-report is set, save the report to data/llm_arena/reports/."""
    if not getattr(args, "save_report", False):
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{timestamp}.md"
    filepath = os.path.join(_reports_dir(data_dir), filename)
    gen = ReportGenerator()
    gen.save_markdown(report, filepath)
    print(f"\n[report] Saved -> {filepath}")


def _default_source_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "data")


def _print_json(obj):
    print(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def cmd_seed(args):
    data_dir = args.data_dir or _default_data_dir()
    source_dir = args.source_dir or _default_source_dir()

    print(f"[seed] Source data : {source_dir}")
    print(f"[seed] Dataset dir : {data_dir}")

    labeler = AutoLabeler(source_dir)
    scenarios = labeler.run_full_pipeline()

    if not scenarios:
        print("[seed] No scenarios produced — check that data sources exist.")
        return

    mgr = DatasetManager(data_dir)
    # If --merge, load existing first so we don't overwrite
    if args.merge:
        existing = mgr.load()
        print(f"[seed] Loaded {existing} existing scenarios (merge mode)")

    new_count = 0
    for s in scenarios:
        if mgr.add_scenario(s):
            new_count += 1

    mgr.save()

    total = len(mgr.all())
    print(f"[seed] Done. {new_count} new / {total} total scenarios saved to {data_dir}")

    # Quick stats
    stats = mgr.stats()
    print(f"[seed] Outcomes  : {stats['by_outcome']}")
    print(f"[seed] Catalysts : {stats['by_catalyst_type']}")
    print(f"[seed] Traded    : {stats['traded']}  Win rate: {stats['trade_win_rate']:.1%}")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def cmd_stats(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[stats] No scenarios found in {data_dir}. Run `seed` first.")
        return

    stats = mgr.stats()
    gen = ReportGenerator()
    report = gen.generate_dataset_quality_report(stats)
    print(report)

    _save_report_if_requested(report, args, data_dir, "dataset_quality")

    if args.json:
        print("\n--- JSON ---")
        _print_json(stats)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[list] No scenarios found in {data_dir}. Run `seed` first.")
        return

    kwargs = {}
    if args.catalyst:
        kwargs["catalyst_type"] = CatalystType(args.catalyst)
    if args.outcome:
        kwargs["outcome"] = StockOutcome(args.outcome)
    if args.confidence:
        kwargs["label_confidence"] = LabelConfidence(args.confidence)
    if args.traded is not None:
        kwargs["was_traded"] = args.traded
    if args.min_gap is not None:
        kwargs["min_gap_pct"] = args.min_gap

    results = mgr.filter(**kwargs)

    if args.sort == "date":
        results.sort(key=lambda s: s.date)
    elif args.sort == "ticker":
        results.sort(key=lambda s: s.ticker)
    elif args.sort == "gap":
        results.sort(key=lambda s: s.gap_pct or 0, reverse=True)

    limit = args.limit or len(results)
    results = results[:limit]

    print(f"\n{'ID':<25} {'Ticker':<6} {'Date':<12} {'Catalyst':<20} {'Outcome':<8} {'Signal':<12} {'Conf':<14} {'PnL':>8}")
    print("-" * 110)
    for s in results:
        pnl_str = f"${s.trade_pnl:,.0f}" if s.trade_pnl is not None else "—"
        print(
            f"{s.scenario_id:<25} {s.ticker:<6} {str(s.date):<12} "
            f"{s.catalyst_type.value:<20} {s.outcome.value:<8} "
            f"{s.correct_signal.value:<12} {s.label_confidence.value:<14} {pnl_str:>8}"
        )

    print(f"\n{len(results)} scenario(s) shown (of {n} total)")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def cmd_show(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    mgr.load()

    scenario = mgr.get(args.scenario_id)
    if scenario is None:
        print(f"[show] Scenario not found: {args.scenario_id}")
        sys.exit(1)

    d = scenario.to_dict()
    _print_json(d)

    if args.agent_view:
        print("\n--- Agent view (ground truth stripped) ---")
        _print_json(mgr.export_for_agent(args.scenario_id))


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[validate] No scenarios found in {data_dir}.")
        return

    errors = []
    warnings = []

    for s in mgr.all():
        sid = s.scenario_id

        # Required fields
        if not s.ticker:
            errors.append(f"{sid}: missing ticker")
        if s.date is None:
            errors.append(f"{sid}: missing date")
        if s.scenario_id != f"{s.ticker}_{s.date}":
            errors.append(f"{sid}: scenario_id mismatch (expected {s.ticker}_{s.date})")

        # Warn on low quality
        if s.label_confidence.value == "unlabeled":
            warnings.append(f"{sid}: unlabeled (no usable data)")
        if s.open_price is None and s.close_price is None:
            warnings.append(f"{sid}: no price data")

        # Outcome consistency
        if s.max_gain_pct is not None and s.outcome.value == "runner" and s.max_gain_pct < 0.10:
            warnings.append(f"{sid}: RUNNER but max_gain={s.max_gain_pct:.1%}")
        if s.max_drawdown_pct is not None and s.outcome.value == "fader" and s.max_drawdown_pct > -0.05:
            warnings.append(f"{sid}: FADER but max_drawdown={s.max_drawdown_pct:.1%}")

    print(f"\n=== Validation Results ({n} scenarios) ===")
    print(f"  Errors   : {len(errors)}")
    print(f"  Warnings : {len(warnings)}")

    if errors:
        print("\nERRORS:")
        for e in errors[:50]:
            print(f"  [ERROR] {e}")

    if warnings and not args.quiet:
        print(f"\nWARNINGS (first 20 of {len(warnings)}):")
        for w in warnings[:20]:
            print(f"  [WARN]  {w}")

    if not errors:
        print("\n[validate] PASSED")
    else:
        print(f"\n[validate] FAILED — {len(errors)} error(s)")
        sys.exit(1)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def cmd_replay(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[replay] No scenarios found in {data_dir}. Run `seed` first.")
        return

    # Apply filters
    filter_kwargs = {}
    if args.outcome:
        filter_kwargs["outcome"] = StockOutcome(args.outcome)
    if args.catalyst:
        filter_kwargs["catalyst_type"] = CatalystType(args.catalyst)

    scenarios = mgr.filter(**filter_kwargs)

    # Agent filter: keep only scenarios that have the requested agent's signals
    if args.agent:
        # Resolve short names to agent_ids
        _short_to_id = {
            "news": "news_agent",
            "fundamental": "fundamental_agent",
            "technical": "technical_agent",
            "risk": "risk_agent",
            "manipulation": "manipulation_classifier",
            "institutional": "institutional_agent",
        }
        agent_id = _short_to_id.get(args.agent, args.agent)
        scenarios = [
            s for s in scenarios
            if agent_id in s.actual_agent_signals or args.agent in s.actual_agent_signals
        ]

    print(f"[replay] Replaying {len(scenarios)} scenario(s) from {n} total")

    harness = AgentHarness(data_dir)

    if args.agent:
        cfg = AgentConfig(agent_type=args.agent, model_id="replay")
        results = harness.run_batch(scenarios, cfg, mode="replay")
    else:
        results = harness.run_replay(scenarios)

    # Summary
    total = len(results)
    parsed = sum(1 for r in results if r.parse_success)
    bull_like = sum(1 for r in results if r.signal_direction in ("BULL", "STRONG_BULL"))
    bear_like = sum(1 for r in results if r.signal_direction in ("BEAR", "STRONG_BEAR"))
    neutral = sum(1 for r in results if r.signal_direction == "NEUTRAL")

    print(f"\n  Results      : {total}")
    print(f"  Parse OK     : {parsed}/{total} ({parsed/total:.1%})" if total else "  Parse OK     : 0/0")
    print(f"  BULL/STRONG  : {bull_like}")
    print(f"  BEAR/STRONG  : {bear_like}")
    print(f"  NEUTRAL      : {neutral}")

    if args.save:
        path = harness.save_results(args.save)
        print(f"\n[replay] Saved {total} results -> {path}")
    else:
        print("\n[replay] (Use --save NAME to persist results)")


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def cmd_score(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[score] No scenarios found in {data_dir}. Run `seed` first.")
        return

    harness = AgentHarness(data_dir)
    try:
        results = harness.load_results(args.results)
    except FileNotFoundError as exc:
        print(f"[score] {exc}")
        results_path = os.path.join(data_dir, "results")
        available = [
            f[:-5] for f in os.listdir(results_path)
            if f.endswith(".json")
        ] if os.path.isdir(results_path) else []
        if available:
            print(f"[score] Available result sets: {', '.join(sorted(available))}")
        return

    scenarios = mgr.all()

    calc = MetricsCalculator()
    scorecard = calc.score_results(results, scenarios)

    gen = ReportGenerator()
    report = gen.generate_scorecard_report(scorecard)
    print(report)

    _save_report_if_requested(report, args, data_dir, f"scorecard_{args.results}")

    if args.json:
        print("\n--- JSON ---")
        _print_json(scorecard.to_dict())


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def cmd_compare(args):
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[compare] No scenarios found in {data_dir}. Run `seed` first.")
        return

    scenarios = mgr.all()
    calc = MetricsCalculator()

    baseline_harness = AgentHarness(data_dir)
    try:
        baseline_results = baseline_harness.load_results(args.baseline)
    except FileNotFoundError as exc:
        print(f"[compare] Baseline: {exc}")
        return

    variant_harness = AgentHarness(data_dir)
    try:
        variant_results = variant_harness.load_results(args.variant)
    except FileNotFoundError as exc:
        print(f"[compare] Variant: {exc}")
        return

    baseline_sc = calc.score_results(baseline_results, scenarios)
    variant_sc = calc.score_results(variant_results, scenarios)

    gen = ReportGenerator()
    print(gen.generate_scorecard_report(baseline_sc))
    print()
    print(gen.generate_scorecard_report(variant_sc))
    print()

    table = gen.generate_comparison_table(baseline_sc, variant_sc)
    print(table)

    combined = "\n\n".join([
        gen.generate_scorecard_report(baseline_sc),
        gen.generate_scorecard_report(variant_sc),
        table,
    ])
    _save_report_if_requested(combined, args, data_dir, f"compare_{args.baseline}_vs_{args.variant}")


# ---------------------------------------------------------------------------
# experiment
# ---------------------------------------------------------------------------


def cmd_experiment(args):
    """Run a pre-defined experiment and display (+ optionally save) results."""
    data_dir = args.data_dir or _default_data_dir()

    try:
        config = get_experiment(args.name)
    except KeyError as exc:
        print(f"[experiment] {exc}")
        available = [e["name"] for e in list_available_experiments()]
        print(f"[experiment] Available: {', '.join(available)}")
        sys.exit(1)

    # CLI mode override — --live forces live execution even for replay experiments
    if getattr(args, "live", False):
        config.mode = "live"

    # CLI filter overrides
    if args.outcome:
        config.scenario_filters["outcome"] = args.outcome
    if args.catalyst:
        config.scenario_filters["catalyst_type"] = args.catalyst
    if args.min_gap is not None:
        config.scenario_filters["min_gap_pct"] = args.min_gap
    if args.bootstrap:
        config.bootstrap_iterations = args.bootstrap
    if args.confidence:
        config.confidence_level = args.confidence

    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print(f"[experiment] No scenarios found in {data_dir}. Run `seed` first.")
        sys.exit(1)

    # --limit caps the number of scenarios (useful for cost control during live tests)
    limit = getattr(args, "limit", None)
    if limit is not None and limit > 0:
        print(f"[experiment] --limit {limit}: capping scenarios to first {limit}")
        config.scenario_filters["_limit"] = limit
        # Relax min_scenarios to match the limit (user accepts reduced statistical power)
        if limit < config.min_scenarios:
            config.min_scenarios = limit

    if config.mode == "live":
        from src.llm_arena.harness import MODEL_REGISTRY
        print(f"[experiment] *** LIVE MODE — real API calls will be made ***")
        print(f"[experiment] Models in registry: {sorted(MODEL_REGISTRY)}")
        baseline_model = config.baseline.model_id
        print(f"[experiment] Baseline model : {baseline_model}")
        if baseline_model not in MODEL_REGISTRY:
            print(
                f"[experiment] WARNING: baseline model '{baseline_model}' not in MODEL_REGISTRY. "
                f"Live calls will return errors."
            )

    print(f"[experiment] Running '{config.name}' on {n} loaded scenarios ...")
    print(f"[experiment] Mode: {config.mode}  Bootstrap: {config.bootstrap_iterations}")

    harness = AgentHarness(data_dir)
    scorer = MetricsCalculator()
    experiments_dir = os.path.join(data_dir, "experiments")
    engine = ExperimentEngine(mgr, harness, scorer, experiments_dir)

    try:
        result = engine.run_experiment(config)
    except ValueError as exc:
        print(f"[experiment] {exc}")
        sys.exit(1)

    gen = ReportGenerator()
    report = gen.generate_experiment_report(result)
    print(report)

    if not args.no_save:
        path = engine.save_experiment(result)
        print(f"\n[experiment] Saved -> {path}")

    _save_report_if_requested(report, args, data_dir, f"experiment_{config.name}")

    if args.json:
        print("\n--- JSON ---")
        _print_json(result.to_dict())


# ---------------------------------------------------------------------------
# experiments (list)
# ---------------------------------------------------------------------------


def cmd_experiments(args):
    """List saved experiments and available pre-defined experiments."""
    data_dir = args.data_dir or _default_data_dir()
    experiments_dir = os.path.join(data_dir, "experiments")

    # Pre-defined experiments
    available = list_available_experiments()
    print(f"\n{'PRE-DEFINED EXPERIMENTS'}")
    print("-" * 60)
    for e in available:
        mode_tag = "[replay]" if e["mode"] == "replay" else "[live]  "
        print(
            f"  {mode_tag} {e['name']:<30} "
            f"baseline={e['baseline']}"
        )

    # Saved experiments
    if os.path.isdir(experiments_dir):
        harness = AgentHarness(data_dir)
        scorer = MetricsCalculator()
        engine = ExperimentEngine(
            DatasetManager(data_dir), harness, scorer, experiments_dir
        )
        saved = engine.list_experiments()
        if saved:
            print(f"\n{'SAVED EXPERIMENTS'}")
            print("-" * 60)
            print(
                f"  {'Name':<30} {'Winner':<20} {'Scenarios':>9} {'p-value':>9} {'Sig?':>6}"
            )
            print("  " + "-" * 78)
            for s in saved:
                p_str = f"{s['primary_p_value']:.4f}" if s["primary_p_value"] is not None else "  N/A "
                sig_str = "YES" if s.get("significant") else "no "
                print(
                    f"  {s['name']:<30} {s['winner']:<20} "
                    f"{s['scenario_count']:>9} {p_str:>9} {sig_str:>6}"
                )
    else:
        print("\n  (no saved experiments yet)")


# ---------------------------------------------------------------------------
# experiment-result
# ---------------------------------------------------------------------------


def cmd_experiment_result(args):
    """Load and display a previously saved experiment result."""
    data_dir = args.data_dir or _default_data_dir()
    experiments_dir = os.path.join(data_dir, "experiments")

    harness = AgentHarness(data_dir)
    scorer = MetricsCalculator()
    engine = ExperimentEngine(
        DatasetManager(data_dir), harness, scorer, experiments_dir
    )

    try:
        result = engine.load_experiment(args.name)
    except FileNotFoundError as exc:
        print(f"[experiment-result] {exc}")
        saved = engine.list_experiments()
        if saved:
            print(
                f"[experiment-result] Available: "
                f"{', '.join(s['name'] for s in saved)}"
            )
        sys.exit(1)

    gen = ReportGenerator()
    report = gen.generate_experiment_report(result)
    print(report)

    _save_report_if_requested(report, args, data_dir, f"experiment_{args.name}")

    if args.json:
        print("\n--- JSON ---")
        _print_json(result.to_dict())


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# ensemble
# ---------------------------------------------------------------------------

_DEFAULT_ENSEMBLE_MODELS = ["qwen3-235b", "llama-3.3-70b", "mixtral-8x7b"]


def cmd_ensemble(args):
    """Run the multi-model ensemble experiment (calls real APIs)."""
    data_dir = args.data_dir or _default_data_dir()
    mgr = DatasetManager(data_dir)
    n = mgr.load()
    if n == 0:
        print("[ensemble] No scenarios loaded. Run 'seed' first.")
        return

    # Parse models
    models_arg = getattr(args, "models", None)
    if models_arg:
        models = [m.strip() for m in models_arg.split(",")]
    else:
        models = _DEFAULT_ENSEMBLE_MODELS

    # Validate models against registry
    unknown = [m for m in models if m not in MODEL_REGISTRY]
    if unknown:
        print(
            f"[ensemble] Unknown model(s): {', '.join(unknown)}\n"
            f"[ensemble] Available: {', '.join(sorted(MODEL_REGISTRY.keys()))}"
        )
        return

    # Filter + limit scenarios (prefer scenarios with premarket headlines for news agent)
    all_scenarios = mgr.all()
    if args.limit:
        all_scenarios = all_scenarios[: args.limit]

    if not all_scenarios:
        print("[ensemble] No scenarios available after filtering.")
        return

    print(
        f"[ensemble] Running on {len(all_scenarios)} scenarios "
        f"with {len(models)} models..."
    )
    print(f"[ensemble] Models: {', '.join(models)}")
    print(f"[ensemble] Estimated cost: ~${len(all_scenarios) * len(models) * 0.003:.2f}")

    # Progress callback
    def _progress(done: int, total: int) -> None:
        pct = done / total * 100
        print(f"[ensemble] {done}/{total} ({pct:.0f}%)  ", end="\r", flush=True)

    name = args.save or "ensemble_v1"
    experiment = EnsembleExperiment(data_dir)

    result = experiment.run(
        scenarios=all_scenarios,
        models=models,
        name=name,
        agent_type="news",
        rate_limit_delay=args.rate_limit_delay,
        progress_callback=_progress,
    )

    print()  # newline after progress

    # Display results
    report = format_ensemble_result(result)
    print(report)

    # Save
    if args.save:
        path = experiment.save(result, name=args.save)
        print(f"\n[ensemble] Saved -> {path}")

    if args.json:
        print("\n--- JSON ---")
        _print_json(result.to_dict())


# ---------------------------------------------------------------------------
# ensemble-result
# ---------------------------------------------------------------------------


def cmd_ensemble_result(args):
    """Load and display a previously saved ensemble experiment result."""
    data_dir = args.data_dir or _default_data_dir()
    experiment = EnsembleExperiment(data_dir)

    try:
        result = experiment.load(args.name)
    except FileNotFoundError as exc:
        print(f"[ensemble-result] {exc}")
        saved = experiment.list_saved()
        if saved:
            print(f"[ensemble-result] Available: {', '.join(saved)}")
        else:
            print("[ensemble-result] No saved ensemble results found.")
        return

    report = format_ensemble_result(result)
    print(report)

    if args.json:
        print("\n--- JSON ---")
        _print_json(result.to_dict())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_llm_arena",
        description="LLM Arena Labeled Dataset CLI",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to labeled dataset directory (default: data/llm_arena/)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # seed
    p_seed = sub.add_parser("seed", help="Run auto-labeling pipeline and save dataset")
    p_seed.add_argument(
        "--source-dir",
        default=None,
        help="Root data directory containing journals/, trade_results.jsonl, etc.",
    )
    p_seed.add_argument(
        "--merge",
        action="store_true",
        help="Merge into existing dataset instead of overwriting",
    )

    # stats
    p_stats = sub.add_parser("stats", help="Print dataset statistics")
    p_stats.add_argument("--json", action="store_true", help="Also print raw JSON stats")
    p_stats.add_argument("--save-report", action="store_true", dest="save_report", help="Save report to data/llm_arena/reports/")

    # list
    p_list = sub.add_parser("list", help="List scenarios with optional filters")
    p_list.add_argument("--catalyst", choices=[c.value for c in CatalystType], default=None)
    p_list.add_argument("--outcome", choices=[o.value for o in StockOutcome], default=None)
    p_list.add_argument("--confidence", choices=[c.value for c in LabelConfidence], default=None)
    p_list.add_argument("--traded", type=lambda x: x.lower() == "true", default=None, metavar="true|false")
    p_list.add_argument("--min-gap", type=float, default=None, metavar="PCT")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--sort", choices=["date", "ticker", "gap"], default="date")

    # show
    p_show = sub.add_parser("show", help="Show full details for one scenario")
    p_show.add_argument("scenario_id", help="Scenario ID e.g. BRLS_2026-02-10")
    p_show.add_argument("--agent-view", action="store_true", help="Also print stripped agent view")

    # validate
    p_val = sub.add_parser("validate", help="Validate dataset integrity")
    p_val.add_argument("--quiet", action="store_true", help="Suppress warnings, show only errors")

    # score
    p_score = sub.add_parser("score", help="Compute and display a scorecard for saved results")
    p_score.add_argument(
        "--results",
        required=True,
        metavar="NAME",
        help="Experiment name to score (matches data/llm_arena/results/{NAME}.json)",
    )
    p_score.add_argument("--detail", action="store_true", help="Show per-catalyst and per-outcome breakdown")
    p_score.add_argument("--json", action="store_true", help="Also print raw JSON scorecard")
    p_score.add_argument("--save-report", action="store_true", dest="save_report", help="Save report to data/llm_arena/reports/")

    # compare
    p_compare = sub.add_parser("compare", help="Compare two saved result sets side-by-side")
    p_compare.add_argument(
        "--baseline",
        required=True,
        metavar="NAME",
        help="Baseline experiment name",
    )
    p_compare.add_argument(
        "--variant",
        required=True,
        metavar="NAME",
        help="Variant experiment name to compare against baseline",
    )
    p_compare.add_argument("--save-report", action="store_true", dest="save_report", help="Save report to data/llm_arena/reports/")

    # experiment
    p_exp = sub.add_parser("experiment", help="Run a pre-defined A/B experiment")
    p_exp.add_argument(
        "--name",
        required=True,
        choices=sorted(_EXPERIMENT_REGISTRY.keys()),
        help="Experiment name to run",
    )
    p_exp.add_argument(
        "--outcome",
        choices=[o.value for o in StockOutcome],
        default=None,
        help="Filter scenarios by outcome (overrides experiment config)",
    )
    p_exp.add_argument(
        "--catalyst",
        choices=[c.value for c in CatalystType],
        default=None,
        help="Filter scenarios by catalyst type (overrides experiment config)",
    )
    p_exp.add_argument(
        "--min-gap",
        type=float,
        default=None,
        metavar="PCT",
        help="Filter to scenarios with gap_pct >= this value",
    )
    p_exp.add_argument(
        "--bootstrap",
        type=int,
        default=None,
        metavar="N",
        help="Override bootstrap iterations (default: 1000)",
    )
    p_exp.add_argument(
        "--confidence",
        type=float,
        default=None,
        metavar="LEVEL",
        help="Override confidence level (default: 0.95)",
    )
    p_exp.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save experiment results to disk",
    )
    p_exp.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Force live mode — actually call LLM APIs (overrides experiment config)",
    )
    p_exp.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N scenarios (cost control for live tests)",
    )
    p_exp.add_argument("--json", action="store_true", help="Also print raw JSON result")
    p_exp.add_argument("--save-report", action="store_true", dest="save_report", help="Save report to data/llm_arena/reports/")

    # experiments (list)
    sub.add_parser("experiments", help="List available and saved experiments")

    # experiment-result
    p_expr = sub.add_parser(
        "experiment-result", help="Display a previously saved experiment result"
    )
    p_expr.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="Experiment name to display",
    )
    p_expr.add_argument("--json", action="store_true", help="Also print raw JSON result")
    p_expr.add_argument("--save-report", action="store_true", dest="save_report", help="Save report to data/llm_arena/reports/")

    # replay
    p_replay = sub.add_parser("replay", help="Replay stored agent signals through the harness")
    p_replay.add_argument(
        "--agent",
        default=None,
        choices=["news", "fundamental", "technical", "risk", "manipulation", "institutional"],
        help="Replay a specific agent type only (default: all agents in each scenario)",
    )
    p_replay.add_argument(
        "--outcome",
        choices=[o.value for o in StockOutcome],
        default=None,
        help="Filter scenarios by outcome",
    )
    p_replay.add_argument(
        "--catalyst",
        choices=[c.value for c in CatalystType],
        default=None,
        help="Filter scenarios by catalyst type",
    )
    p_replay.add_argument(
        "--save",
        default=None,
        metavar="NAME",
        help="Save results to data/llm_arena/results/{NAME}.json",
    )

    # ensemble
    p_ens = sub.add_parser(
        "ensemble",
        help="Run multi-model ensemble experiment (calls real APIs)",
    )
    p_ens.add_argument(
        "--models",
        default=None,
        metavar="M1,M2,M3",
        help=(
            f"Comma-separated model IDs to ensemble "
            f"(default: {','.join(_DEFAULT_ENSEMBLE_MODELS)})"
        ),
    )
    p_ens.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Number of scenarios to evaluate (default: 50)",
    )
    p_ens.add_argument(
        "--save",
        default=None,
        metavar="NAME",
        help="Save results to data/llm_arena/ensemble/{NAME}.json",
    )
    p_ens.add_argument(
        "--rate-limit-delay",
        type=float,
        default=0.3,
        dest="rate_limit_delay",
        metavar="SECS",
        help="Seconds between scenario batches to avoid rate limits (default: 0.3)",
    )
    p_ens.add_argument("--json", action="store_true", help="Also print raw JSON result")

    # ensemble-result
    p_ensr = sub.add_parser(
        "ensemble-result",
        help="Display a previously saved ensemble experiment result",
    )
    p_ensr.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="Ensemble experiment name (matches data/llm_arena/ensemble/{NAME}.json)",
    )
    p_ensr.add_argument("--json", action="store_true", help="Also print raw JSON result")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "seed": cmd_seed,
        "stats": cmd_stats,
        "list": cmd_list,
        "show": cmd_show,
        "validate": cmd_validate,
        "replay": cmd_replay,
        "score": cmd_score,
        "compare": cmd_compare,
        "experiment": cmd_experiment,
        "experiments": cmd_experiments,
        "experiment-result": cmd_experiment_result,
        "ensemble": cmd_ensemble,
        "ensemble-result": cmd_ensemble_result,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
