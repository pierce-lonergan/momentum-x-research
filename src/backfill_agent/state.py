"""Persistent work queue backed by SQLite (WAL mode for crash safety).

One DB file: data/backfill_agent/queue.db. Single-process or multi-process safe
via SQLite's atomic UPDATE...WHERE locking semantics. Workers claim items
atomically; in-progress items older than 10 minutes get auto-released back to
pending (handles worker crash mid-task).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = _PROJECT_ROOT / "data" / "backfill_agent" / "queue.db"

# Stale in-progress threshold — if a worker crashed mid-task, release after 10 min.
STALE_IN_PROGRESS_SECS = 600


class WorkStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED_TRANSIENT = "failed_transient"   # may retry
    FAILED_PERMANENT = "failed_permanent"   # do not retry
    BLOCKED = "blocked"                      # awaiting external dependency


@dataclass(frozen=True)
class WorkItem:
    """One unit of backfill work — fetch + label one ticker-day."""
    id: int
    ticker: str
    date: str                               # YYYY-MM-DD
    status: WorkStatus
    priority: int = 0                       # higher = process first
    last_attempt_at: str | None = None
    attempt_count: int = 0
    last_error: str | None = None
    completed_at: str | None = None


# ── Schema ───────────────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,
    status          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    completed_at    TEXT,
    claimed_by      TEXT,
    claimed_at      TEXT,
    UNIQUE(ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_status_priority
    ON work_items(status, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_claimed_at
    ON work_items(status, claimed_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ── WorkQueue ────────────────────────────────────────────────────────────


class WorkQueue:
    """Crash-safe work queue. One process owns the DB at a time but is safe to
    crash and restart — all state is on disk in WAL mode.

    Public methods are atomic: enqueue is idempotent (UNIQUE on ticker+date),
    claim_next uses a row-level UPDATE...WHERE pattern.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            for stmt in _SCHEMA.strip().split(";"):
                if stmt.strip():
                    conn.execute(stmt)

    # ── Enqueue ──

    def enqueue(self, ticker: str, date: str, priority: int = 0) -> bool:
        """Idempotent: returns True if a NEW row was inserted, False if already present.

        Uses UNIQUE(ticker, date) to prevent duplicates on re-add. Re-adding does
        NOT bump priority of existing rows (intentional — explicit re-prioritize
        via separate API).
        """
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO work_items (ticker, date, status, priority) "
                    "VALUES (?, ?, ?, ?)",
                    (ticker, date, WorkStatus.PENDING.value, priority),
                )
                return cur.rowcount == 1
            except sqlite3.IntegrityError:
                return False

    def enqueue_batch(self, items: Iterable[tuple[str, str, int]]) -> int:
        """Bulk enqueue. Returns count of NEW rows inserted (skips duplicates)."""
        n = 0
        with self._connect() as conn:
            for ticker, date, priority in items:
                try:
                    cur = conn.execute(
                        "INSERT INTO work_items (ticker, date, status, priority) "
                        "VALUES (?, ?, ?, ?)",
                        (ticker, date, WorkStatus.PENDING.value, priority),
                    )
                    n += cur.rowcount
                except sqlite3.IntegrityError:  # noqa: silent-handler
                    pass  # UNIQUE(ticker, date) — expected for idempotent re-enqueue
        return n

    # ── Claim ──

    def claim_next(self, worker_id: str, max_items: int = 1) -> list[WorkItem]:
        """Atomically claim up to `max_items` pending items. Returns claimed items.

        Implementation: SELECT pending IDs by priority, then UPDATE to in_progress
        WHERE status='pending' AND id IN (...). The UPDATE only succeeds for rows
        that are STILL pending — if another worker raced and claimed first, this
        worker gets fewer items, never the same item.
        """
        with self._connect() as conn:
            # First, auto-release any stale in_progress items
            self._release_stale_locked(conn)

            # Find candidates
            rows = conn.execute(
                "SELECT id FROM work_items WHERE status = ? "
                "ORDER BY priority DESC, id ASC LIMIT ?",
                (WorkStatus.PENDING.value, max_items),
            ).fetchall()
            if not rows:
                return []

            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            now = _now_iso()
            conn.execute(
                f"UPDATE work_items SET status = ?, claimed_by = ?, "
                f"claimed_at = ?, last_attempt_at = ?, "
                f"attempt_count = attempt_count + 1 "
                f"WHERE id IN ({placeholders}) AND status = ?",
                [WorkStatus.IN_PROGRESS.value, worker_id, now, now, *ids,
                 WorkStatus.PENDING.value],
            )

            # Re-read claimed rows
            rows = conn.execute(
                f"SELECT id, ticker, date, status, priority, last_attempt_at, "
                f"attempt_count, last_error, completed_at "
                f"FROM work_items WHERE id IN ({placeholders}) AND status = ?",
                [*ids, WorkStatus.IN_PROGRESS.value],
            ).fetchall()

        return [
            WorkItem(
                id=r["id"], ticker=r["ticker"], date=r["date"],
                status=WorkStatus(r["status"]), priority=r["priority"],
                last_attempt_at=r["last_attempt_at"],
                attempt_count=r["attempt_count"],
                last_error=r["last_error"],
                completed_at=r["completed_at"],
            )
            for r in rows
        ]

    def _release_stale_locked(self, conn: sqlite3.Connection) -> int:
        """Release in_progress items older than STALE_IN_PROGRESS_SECS back to pending."""
        cutoff = datetime.now(timezone.utc).timestamp() - STALE_IN_PROGRESS_SECS
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        cur = conn.execute(
            "UPDATE work_items SET status = ?, claimed_by = NULL, claimed_at = NULL "
            "WHERE status = ? AND claimed_at < ?",
            (WorkStatus.PENDING.value, WorkStatus.IN_PROGRESS.value, cutoff_iso),
        )
        if cur.rowcount > 0:
            logger.warning(
                "queue: auto-released %d stale in_progress items (>%ds old)",
                cur.rowcount, STALE_IN_PROGRESS_SECS,
            )
        return cur.rowcount

    # ── Result handlers ──

    def complete(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE work_items SET status = ?, completed_at = ?, "
                "claimed_by = NULL, claimed_at = NULL, last_error = NULL "
                "WHERE id = ?",
                (WorkStatus.COMPLETED.value, _now_iso(), item_id),
            )

    def fail_transient(self, item_id: int, error: str, max_attempts: int = 3) -> None:
        """Transient failure — release back to pending if attempts < max, else permanent."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempt_count FROM work_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return
            new_status = (
                WorkStatus.FAILED_PERMANENT
                if row["attempt_count"] >= max_attempts
                else WorkStatus.PENDING
            )
            conn.execute(
                "UPDATE work_items SET status = ?, claimed_by = NULL, "
                "claimed_at = NULL, last_error = ? WHERE id = ?",
                (new_status.value, error[:500], item_id),
            )

    def fail_permanent(self, item_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE work_items SET status = ?, claimed_by = NULL, "
                "claimed_at = NULL, last_error = ? WHERE id = ?",
                (WorkStatus.FAILED_PERMANENT.value, error[:500], item_id),
            )

    # ── Stats ──

    def stats(self) -> dict:
        """Counts by status, throughput (1h, 24h), ETA at current rate."""
        with self._connect() as conn:
            counts = {s.value: 0 for s in WorkStatus}
            for r in conn.execute(
                "SELECT status, COUNT(*) as n FROM work_items GROUP BY status"
            ).fetchall():
                counts[r["status"]] = r["n"]

            # Throughput: completed items in last 1h and 24h
            now = datetime.now(timezone.utc)
            one_hour_ago = now.timestamp() - 3600
            day_ago = now.timestamp() - 86400
            one_h_iso = datetime.fromtimestamp(one_hour_ago, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            day_iso = datetime.fromtimestamp(day_ago, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            t1h = conn.execute(
                "SELECT COUNT(*) as n FROM work_items WHERE status = ? AND completed_at >= ?",
                (WorkStatus.COMPLETED.value, one_h_iso),
            ).fetchone()["n"]
            t24h = conn.execute(
                "SELECT COUNT(*) as n FROM work_items WHERE status = ? AND completed_at >= ?",
                (WorkStatus.COMPLETED.value, day_iso),
            ).fetchone()["n"]

            # Recent errors for monitor
            recent_errors = [
                dict(r) for r in conn.execute(
                    "SELECT ticker, date, last_error, last_attempt_at "
                    "FROM work_items WHERE last_error IS NOT NULL "
                    "ORDER BY last_attempt_at DESC LIMIT 10"
                ).fetchall()
            ]

        # ETA: pending / (items per second) — guard against div by zero
        pending = counts.get(WorkStatus.PENDING.value, 0)
        rate_per_sec = (t1h / 3600) if t1h > 0 else 0.0
        eta_seconds = (pending / rate_per_sec) if rate_per_sec > 0 else float("inf")

        return {
            "counts": counts,
            "throughput_1h": t1h,
            "throughput_24h": t24h,
            "eta_seconds": eta_seconds,
            "recent_errors": recent_errors,
        }

    # ── Maintenance ──

    def reset_in_progress(self) -> int:
        """Force-release all in_progress items. Used by coordinator on startup."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE work_items SET status = ?, claimed_by = NULL, claimed_at = NULL "
                "WHERE status = ?",
                (WorkStatus.PENDING.value, WorkStatus.IN_PROGRESS.value),
            )
            return cur.rowcount

    def total_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
