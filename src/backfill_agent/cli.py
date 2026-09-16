"""CLI for D221 backfill agent.

Usage:
    python -m src.backfill_agent --status
    python -m src.backfill_agent --enqueue-from data/backfill/candidates.jsonl \
        --filter missing_bars --priority 100
    python -m src.backfill_agent --mode gap-fill --workers 3 --dry-run --max-items 50
    python -m src.backfill_agent --mode continuous --workers 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Auto-load .env so ALPACA_API_KEY is available without manual export
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parents[2] / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:  # noqa: silent-handler
    pass

from src.backfill_agent.budget import RateBudget, env_ignore_flag
from src.backfill_agent.coordinator import Coordinator
from src.backfill_agent.monitor import render_status
from src.backfill_agent.state import WorkQueue, WorkStatus

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.backfill_agent",
        description="D221 backfill agent — fills historical bar/label gaps.",
    )
    p.add_argument("--mode",
                   choices=("gap-fill", "forward", "backward", "continuous"),
                   default=None,
                   help="Operation mode. Default: continuous when no other action specified.")
    p.add_argument("--status", action="store_true",
                   help="One-shot status report (no daemon start). Exit 0.")
    p.add_argument("--enqueue-from",
                   help="Path to candidates.jsonl. Enqueues all rows that lack bar files.")
    p.add_argument("--filter", default="missing_bars",
                   choices=("missing_bars", "all"),
                   help="Filter for --enqueue-from. Default: only candidates lacking bars.")
    p.add_argument("--priority", type=int, default=0,
                   help="Priority for new enqueues. Higher = earlier processing.")
    p.add_argument("--workers", type=int, default=3,
                   help="Concurrent workers. Default 3 (low to keep budget impact small).")
    p.add_argument("--max-items", type=int, default=None,
                   help="Stop after N completed items (for testing).")
    p.add_argument("--dry-run", action="store_true",
                   help="Skip actual API calls. Items still move through the queue.")
    p.add_argument("--ignore-budget", action="store_true",
                   help="Disable rate budget (DANGER — may saturate Alpaca during prod scan).")
    p.add_argument("--reset-stale", action="store_true",
                   help="Force-release all in_progress items back to pending.")
    p.add_argument("--per-day-cap", type=int, default=None,
                   help="When using --enqueue-from, cap rows per session_date.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _cmd_status() -> int:
    print(render_status())
    return 0


def _cmd_enqueue(args: argparse.Namespace) -> int:
    src_path = Path(args.enqueue_from)
    if not src_path.exists():
        print(f"error: {src_path} not found", file=sys.stderr)
        return 2

    with open(src_path, encoding="utf-8") as f:
        candidates = [json.loads(l) for l in f if l.strip()]
    print(f"loaded {len(candidates)} candidates from {src_path}")

    # Apply filter
    if args.filter == "missing_bars":
        bar_root = _PROJECT_ROOT / "data" / "bar_recordings"
        candidates = [
            c for c in candidates
            if not (bar_root / c["date"] / f"{c['ticker']}.json").exists()
        ]
        print(f"after missing_bars filter: {len(candidates)} candidates")

    # Per-day cap
    if args.per_day_cap is not None:
        from collections import defaultdict
        by_day = defaultdict(list)
        for c in candidates:
            by_day[c["date"]].append(c)
        capped = []
        for d, lst in by_day.items():
            # Sort by gap_pct desc, take top N
            lst.sort(key=lambda x: -x.get("gap_pct", 0))
            capped.extend(lst[:args.per_day_cap])
        candidates = capped
        print(f"after per-day-cap={args.per_day_cap}: {len(candidates)} candidates")

    queue = WorkQueue()
    n_inserted = queue.enqueue_batch(
        ((c["ticker"], c["date"], args.priority) for c in candidates)
    )
    print(f"enqueued {n_inserted} new items (skipped {len(candidates) - n_inserted} duplicates)")
    print(f"\ntotal queue size: {queue.total_count()}")
    return 0


def _cmd_reset_stale() -> int:
    queue = WorkQueue()
    n = queue.reset_in_progress()
    print(f"reset {n} in_progress items to pending")
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    budget = RateBudget(ignore_budget=args.ignore_budget or env_ignore_flag())
    queue = WorkQueue()
    coord = Coordinator(
        queue=queue, budget=budget,
        workers=args.workers, dry_run=args.dry_run,
        max_items=args.max_items,
    )
    return await coord.run_forever()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    if args.status:
        return _cmd_status()
    if args.reset_stale:
        return _cmd_reset_stale()
    if args.enqueue_from:
        return _cmd_enqueue(args)

    # Default to continuous if no mode specified
    mode = args.mode or "continuous"
    if mode in ("forward", "backward", "continuous"):
        # Forward/backward modes are not implemented in Phase B (per spec — Phase B is
        # foundation only; forward/backward extension is a Phase C-onward feature).
        # Treat them as gap-fill for now.
        if mode != "gap-fill":
            print(f"NOTE: --mode {mode} not yet implemented — running gap-fill",
                  file=sys.stderr)
    return asyncio.run(_cmd_run(args))


if __name__ == "__main__":
    sys.exit(main())
