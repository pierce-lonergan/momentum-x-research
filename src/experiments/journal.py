"""
MOMENTUM-X Experiment Journal

### ARCHITECTURAL CONTEXT
Node ID: experiments.journal
Graph Link: docs/memory/graph_state.json → "experiments.journal"

### DESIGN DECISIONS
- Append-only JSONL format matching src/analysis/trade_journal.py pattern
- File per day: data/experiments/experiment_journal_{DATE}.jsonl
- All writes wrapped in try/except for crash safety
- Experiment journal failures never crash the trading session

Ref: D102 (Experimentation Framework Phase 1)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.experiments.models import ExperimentJournalEntry

logger = logging.getLogger(__name__)


class ExperimentJournal:
    """
    Append-only JSONL journal for experiment replay results.

    Each line is a serialized ExperimentJournalEntry capturing the primary
    evaluation result plus all variant replay results for one candidate.

    Usage:
        journal = ExperimentJournal(journal_dir=Path("data/experiments"))
        journal.record(entry)   # Appends to today's JSONL file
        entries = ExperimentJournal.load(path)  # Read back for analysis

    Ref: D102 (Experimentation Framework Phase 1)
    """

    def __init__(
        self,
        journal_dir: Path | str = "data/experiments",
        session_date: str | None = None,
    ) -> None:
        self._journal_dir = Path(journal_dir)
        self._session_date = session_date or datetime.now(timezone.utc).strftime(
            "%Y-%m-%d"
        )
        self._entries_recorded = 0

    @property
    def entries_recorded(self) -> int:
        """Number of entries written this session."""
        return self._entries_recorded

    @property
    def journal_path(self) -> Path:
        """Path to today's experiment journal file."""
        return (
            self._journal_dir
            / f"experiment_journal_{self._session_date}.jsonl"
        )

    def record(self, entry: ExperimentJournalEntry) -> None:
        """
        Append an experiment journal entry to the JSONL file.

        Crash-safe: creates directory if needed, wraps in try/except.
        A journal write failure is logged but never propagated.

        Args:
            entry: The experiment replay results to record.
        """
        try:
            self._journal_dir.mkdir(parents=True, exist_ok=True)

            # Serialize to JSON line
            line = entry.model_dump_json()

            # Append to file (atomic-ish: write + newline)
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")

            self._entries_recorded += 1

            # Summary log
            variant_count = len(entry.variant_results)
            would_enter_count = sum(
                1 for v in entry.variant_results if v.would_enter
            )
            logger.debug(
                "D102: Experiment journal: %s %s → %d variants, %d would-enter",
                entry.ticker,
                entry.primary_action,
                variant_count,
                would_enter_count,
            )

        except Exception as e:
            # Journal failures are non-fatal — never crash the trading loop
            logger.warning(
                "D102: Experiment journal write failed (non-fatal): %s", e
            )

    @staticmethod
    def load(path: Path | str) -> list[ExperimentJournalEntry]:
        """
        Load experiment journal entries from a JSONL file.

        Args:
            path: Path to the JSONL file.

        Returns:
            List of ExperimentJournalEntry objects.
            Malformed lines are skipped with a warning.
        """
        path = Path(path)
        if not path.exists():
            return []

        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = ExperimentJournalEntry.model_validate_json(line)
                    entries.append(entry)
                except Exception as e:
                    logger.warning(
                        "D102: Skipping malformed journal line %d in %s: %s",
                        line_num,
                        path,
                        e,
                    )

        return entries

    @staticmethod
    def load_latest(
        journal_dir: Path | str = "data/experiments",
        date: str | None = None,
    ) -> list[ExperimentJournalEntry]:
        """
        Load the most recent experiment journal file.

        Args:
            journal_dir: Directory containing experiment journal files.
            date: Specific date to load (YYYY-MM-DD). If None, loads most recent.

        Returns:
            List of ExperimentJournalEntry objects.
        """
        journal_dir = Path(journal_dir)

        if date:
            path = journal_dir / f"experiment_journal_{date}.jsonl"
            return ExperimentJournal.load(path)

        # Find most recent journal file
        candidates = sorted(
            journal_dir.glob("experiment_journal_*.jsonl"),
            reverse=True,
        )
        if not candidates:
            return []

        return ExperimentJournal.load(candidates[0])

    def summarize(self) -> dict[str, Any]:
        """
        Summarize the current session's experiment journal.

        Returns:
            Dict with per-experiment summary statistics.
        """
        entries = self.load(self.journal_path)
        if not entries:
            return {"total_evaluations": 0, "experiments": {}}

        # Aggregate by experiment_id → variant_id → counts
        summary: dict[str, dict[str, dict[str, int]]] = {}
        total_variants = 0

        for entry in entries:
            for vr in entry.variant_results:
                if vr.experiment_id not in summary:
                    summary[vr.experiment_id] = {}
                if vr.variant_id not in summary[vr.experiment_id]:
                    summary[vr.experiment_id][vr.variant_id] = {
                        "would_enter": 0,
                        "would_skip": 0,
                    }

                if vr.would_enter:
                    summary[vr.experiment_id][vr.variant_id]["would_enter"] += 1
                else:
                    summary[vr.experiment_id][vr.variant_id]["would_skip"] += 1

                total_variants += 1

        return {
            "total_evaluations": len(entries),
            "total_variant_results": total_variants,
            "experiments": summary,
        }
