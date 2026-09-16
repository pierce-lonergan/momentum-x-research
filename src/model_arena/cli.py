"""CLI for cross-model arena.

Usage:
    python -m src.model_arena --task catalyst_classification --n 10
    python -m src.model_arena --task catalyst_classification --models claude-haiku-4-5,qwen-3-235b
    python -m src.model_arena --task catalyst_classification --n 50 --all-models \\
        --output data/model_arena_runs/phase7_smoke.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from pathlib import Path

from src.model_arena.metrics import (
    compute_metrics,
    confusion_matrix,
    rank_by_cost_per_correct,
)
from src.model_arena.models import available_models, load_catalog
from src.model_arena.runner import run_task
from src.model_arena.tasks import load_task

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "model_arena_runs"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.model_arena",
        description="Cross-model evaluation harness — compares many MODELS on one task.",
    )
    p.add_argument("--task", default="catalyst_classification",
                   help="Task name to run (default: catalyst_classification).")
    p.add_argument("--n", type=int, default=None,
                   help="Number of examples to sample (default: all).")
    p.add_argument("--models", default=None,
                   help="Comma-separated model keys (default: all enabled models with API keys).")
    p.add_argument("--all-models", action="store_true",
                   help="Use all enabled models (overrides --models).")
    p.add_argument("--require-verified", action="store_true",
                   help="Skip models marked verified=false in the catalog.")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="Per-call timeout in seconds (default 30).")
    p.add_argument("--concurrency", type=int, default=5,
                   help="Per-provider concurrency (default 5).")
    p.add_argument("--output", default=None,
                   help="CSV output path (default: data/model_arena_runs/<task>_<timestamp>.csv).")
    p.add_argument("--list-models", action="store_true",
                   help="List the catalog and exit.")
    return p


def _list_models() -> int:
    catalog = load_catalog()
    print(f"{'KEY':<22}  {'PROVIDER':<10}  {'VERIFIED':<8}  {'IN $/MTOK':>9}  {'OUT $/MTOK':>10}  KEY?")
    print("-" * 90)
    for m in catalog:
        has_key = "Y" if m.has_api_key() else "n"
        verified = "Y" if m.verified else "n"
        print(f"{m.key:<22}  {m.provider:<10}  {verified:<8}  {m.input_price_per_mtok:>9.2f}  "
              f"{m.output_price_per_mtok:>10.2f}  {has_key}")
    return 0


def _print_summary(metrics_list: list[dict], confusion_by_model: dict) -> None:
    ranked = rank_by_cost_per_correct(metrics_list)
    print("\n" + "=" * 100)
    print("CROSS-MODEL ARENA RESULTS — ranked by cost-per-correct ascending")
    print("=" * 100)
    print(f"{'rank':<5}  {'model':<22}  {'n':>3}  {'acc':>6}  {'F1':>6}  {'p50ms':>7}  "
          f"{'$/call':>9}  {'$/correct':>11}  {'err%':>5}")
    print("-" * 100)
    for i, m in enumerate(ranked, 1):
        cpc = "inf" if m["cost_per_correct_usd"] == float("inf") else f"${m['cost_per_correct_usd']:.4f}"
        print(f"{i:<5}  {m['model_key']:<22}  {m['n']:>3}  "
              f"{m['accuracy']*100:>5.1f}%  {m['macro_f1']*100:>5.1f}%  "
              f"{m['p50_latency_ms']:>6.0f}ms  ${m['avg_cost_per_call_usd']:>7.5f}  "
              f"{cpc:>11}  {m['error_rate']*100:>4.1f}%")


def _write_csv(metrics_list: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(metrics_list[0].keys()) if metrics_list else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in metrics_list:
            writer.writerow(row)


def _write_responses_jsonl(responses: list, path: Path) -> None:
    """Per-example responses for failure-mode analysis."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in responses:
            f.write(json.dumps({
                "sample_id": r.sample_id, "model_key": r.model_key,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "latency_ms": r.latency_ms, "cost_usd": r.cost_usd,
                "raw_response": r.raw_response[:500],  # truncate for size
                "prediction": r.prediction, "truth": r.truth, "correct": r.correct,
                "error": r.error,
            }) + "\n")


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    args = build_parser().parse_args(argv)

    if args.list_models:
        return _list_models()

    # Load task
    try:
        task = load_task(args.task)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Load model set
    keys = None
    if args.models:
        keys = [k.strip() for k in args.models.split(",") if k.strip()]
    elif args.all_models:
        keys = None  # all enabled

    models = available_models(
        keys=keys,
        require_verified=args.require_verified,
        require_key=True,
    )
    if not models:
        print("error: no available models — check API keys and catalog enabled flags",
              file=sys.stderr)
        return 2

    print(f"running task={args.task} on {len(models)} models, "
          f"n_samples={args.n or 'all'}, timeout={args.timeout}s")
    for m in models:
        print(f"  {m.key:<22}  ({m.provider}, {'verified' if m.verified else 'unverified'})")

    # Run
    responses = asyncio.run(run_task(
        task=task,
        models=models,
        n_samples=args.n,
        timeout_s=args.timeout,
        per_provider_concurrency=args.concurrency,
    ))

    metrics_list = compute_metrics(responses)
    cm_by_model: dict = {}  # confusion matrices per model — currently unused in CLI

    # Output
    output_path = Path(args.output) if args.output else (
        _DEFAULT_OUTPUT_DIR / f"{args.task}_n{args.n or len(task.examples)}.csv"
    )
    _write_csv(metrics_list, output_path)
    responses_path = output_path.with_suffix(".responses.jsonl")
    _write_responses_jsonl(responses, responses_path)
    print(f"\nmetrics CSV:    {output_path}")
    print(f"responses JSONL: {responses_path}")

    _print_summary(metrics_list, cm_by_model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
