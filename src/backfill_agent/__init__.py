"""D221 Backfill Agent — long-running daemon to fill the historical bar gap.

Architecture:
  state.py        — SQLite WorkQueue (crash-safe via WAL mode)
  budget.py       — RateBudget (production-aware throttling)
  worker.py       — Single ticker-day processor (atomic writes, cancellable)
  coordinator.py  — Main async loop with SIGTERM handler
  monitor.py      — Read-only status reporter
  cli.py          — `python -m src.backfill_agent --mode {gap-fill|forward|backward|continuous}`

Hard rules:
  - DOES NOT touch src/core, src/agents, src/orchestrator
  - Reuses scripts/backfill_features.compute_features_and_outcomes() as the labeling primitive
  - Writes labels to per-day shards (data/backfill/labels_shards/labels_<date>.jsonl)
    rather than the single features_labeled.jsonl, to avoid contention with
    any concurrent reader/writer
  - Writes bars to data/bar_recordings/<date>/<ticker>.json (atomic via tempfile + rename)
  - Pauses entirely during 4:25-9:35 ET (production scan window)
"""

from src.backfill_agent.state import WorkQueue, WorkItem, WorkStatus
from src.backfill_agent.budget import RateBudget, BudgetWindow
from src.backfill_agent.worker import Worker, WorkResult

__all__ = [
    "WorkQueue", "WorkItem", "WorkStatus",
    "RateBudget", "BudgetWindow",
    "Worker", "WorkResult",
]
