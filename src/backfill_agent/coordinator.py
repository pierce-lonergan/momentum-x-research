"""Coordinator — main async loop with SIGTERM handler.

  on startup:
    - register signal handlers (SIGINT, SIGTERM = graceful shutdown)
    - reset any in_progress items left from a prior crashed run
  loop:
    - if shutdown_requested: drain in_flight tasks, exit
    - claim N items (concurrency = workers)
    - dispatch via asyncio.gather
    - persist results to queue
    - emit heartbeat
    - sleep briefly between iterations to avoid tight-looping when queue is empty
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.backfill_agent.budget import RateBudget, env_ignore_flag
from src.backfill_agent.state import WorkQueue, WorkStatus
from src.backfill_agent.worker import Worker, WorkResult

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HEARTBEAT_PATH = _PROJECT_ROOT / "data" / "backfill_agent" / "heartbeat.json"
_PAUSE_SENTINEL = _PROJECT_ROOT / "data" / "backfill_agent" / "PAUSED"
_HEARTBEAT_INTERVAL_SEC = 30
_PAUSE_POLL_INTERVAL_SEC = 60


def is_paused() -> bool:
    """Pause is signaled by the existence of data/backfill_agent/PAUSED.

    Sentinel-file rather than env var because env changes don't propagate to
    a running process — using env would force restart-to-pause, which defeats
    the purpose of a runtime pause toggle.
    """
    return _PAUSE_SENTINEL.exists()


class Coordinator:
    def __init__(
        self,
        queue: WorkQueue | None = None,
        budget: RateBudget | None = None,
        workers: int = 3,
        dry_run: bool = False,
        max_items: int | None = None,
    ) -> None:
        self.queue = queue or WorkQueue()
        self.budget = budget or RateBudget(ignore_budget=env_ignore_flag())
        self.n_workers = workers
        self.dry_run = dry_run
        self.max_items = max_items                # for testing / CLI --max-items

        self._shutdown_requested = False
        self._in_flight: set[asyncio.Task] = set()
        self._completed_this_run = 0
        self._started_at = datetime.now(timezone.utc)

    def _setup_signal_handlers(self) -> None:
        """Register SIGTERM / SIGINT handlers for graceful shutdown.

        On Windows, SIGTERM is unavailable; we handle SIGINT (Ctrl+C) and
        SIGBREAK (Ctrl+Break / Task Scheduler stop).
        """
        loop = asyncio.get_running_loop()

        def _handler(signum, frame=None):
            logger.warning("coordinator: signal %s received — graceful shutdown", signum)
            self._shutdown_requested = True

        if sys.platform == "win32":
            # Windows: only signal.signal works (no loop.add_signal_handler)
            signal.signal(signal.SIGINT, _handler)
            try:
                signal.signal(signal.SIGBREAK, _handler)
            except (AttributeError, ValueError):  # noqa: silent-handler
                pass  # SIGBREAK absent on some Windows builds — non-fatal
        else:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _handler, sig)

    def _emit_heartbeat(self) -> None:
        """Write a snapshot to data/backfill_agent/heartbeat.json. Atomic write."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "started_at": self._started_at.isoformat(),
            "shutdown_requested": self._shutdown_requested,
            "paused": is_paused(),
            "in_flight": len(self._in_flight),
            "completed_this_run": self._completed_this_run,
            "n_workers": self.n_workers,
            "dry_run": self.dry_run,
            "budget": self.budget.status(),
            "queue": self.queue.stats(),
        }
        _HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HEARTBEAT_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(snapshot, f, default=str, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _HEARTBEAT_PATH)

    async def _process_one(self, worker: Worker, item) -> tuple[int, WorkResult]:
        """Wrap worker.process so we can attribute results back to items."""
        try:
            result = await worker.process(item)
            return (item.id, result)
        except asyncio.CancelledError:
            # Coordinator's drain triggered cancel; queue auto-releases via stale sweep
            raise

    def _persist_result(self, item_id: int, result: WorkResult) -> None:
        if result.success:
            self.queue.complete(item_id)
            self._completed_this_run += 1
        else:
            err = result.error or "unknown error"
            if result.fail_permanent:
                self.queue.fail_permanent(item_id, err)
            else:
                self.queue.fail_transient(item_id, err, max_attempts=3)

    async def run_forever(self) -> int:
        """Main loop. Returns exit code (0 normal, 1 error)."""
        logger.info(
            "coordinator: starting — workers=%d dry_run=%s max_items=%s",
            self.n_workers, self.dry_run, self.max_items,
        )
        self._setup_signal_handlers()

        # Reset stragglers from prior crashed run
        n_reset = self.queue.reset_in_progress()
        if n_reset:
            logger.info("coordinator: reset %d in_progress items left from prior run", n_reset)

        # Pre-create workers (each holds an Alpaca key)
        workers = [
            Worker(
                worker_id=f"w{i}",
                budget=self.budget,
                dry_run=self.dry_run,
            )
            for i in range(self.n_workers)
        ]

        last_heartbeat = 0.0
        loop = asyncio.get_running_loop()
        was_paused = False

        try:
            while not self._shutdown_requested:
                # Hit max_items cap?
                if self.max_items is not None and self._completed_this_run >= self.max_items:
                    logger.info("coordinator: reached max_items=%d, exiting",
                                self.max_items)
                    break

                # PAUSE poller: sentinel file at data/backfill_agent/PAUSED
                # disables work for as long as it exists. Heartbeat continues
                # so the monitor doesn't go stale.
                if is_paused():
                    if not was_paused:
                        logger.warning(
                            "coordinator: PAUSED — sentinel file %s exists. "
                            "Idling until removed.", _PAUSE_SENTINEL,
                        )
                        was_paused = True
                    if loop.time() - last_heartbeat >= _HEARTBEAT_INTERVAL_SEC:
                        self._emit_heartbeat()
                        last_heartbeat = loop.time()
                    await asyncio.sleep(_PAUSE_POLL_INTERVAL_SEC)
                    continue
                if was_paused:
                    logger.info("coordinator: PAUSE sentinel cleared — resuming work")
                    was_paused = False

                items = self.queue.claim_next(worker_id="coord", max_items=self.n_workers)
                if not items:
                    # Empty queue — emit heartbeat, sleep
                    if loop.time() - last_heartbeat >= _HEARTBEAT_INTERVAL_SEC:
                        self._emit_heartbeat()
                        last_heartbeat = loop.time()
                    await asyncio.sleep(60)
                    continue

                # Dispatch
                tasks = []
                for w, it in zip(workers, items):
                    task = asyncio.create_task(self._process_one(w, it))
                    self._in_flight.add(task)
                    tasks.append(task)

                done = await asyncio.gather(*tasks, return_exceptions=True)
                for task in tasks:
                    self._in_flight.discard(task)

                for outcome in done:
                    if isinstance(outcome, BaseException):
                        logger.error("coordinator: task raised %s", outcome)
                        continue
                    item_id, result = outcome
                    self._persist_result(item_id, result)

                # Heartbeat after each batch
                if loop.time() - last_heartbeat >= _HEARTBEAT_INTERVAL_SEC:
                    self._emit_heartbeat()
                    last_heartbeat = loop.time()

            # Drain in-flight on shutdown
            if self._in_flight:
                logger.info("coordinator: draining %d in-flight tasks (max 30s)",
                            len(self._in_flight))
                done, pending = await asyncio.wait(self._in_flight, timeout=30.0)
                for task in pending:
                    task.cancel()
                for task in done:
                    try:
                        outcome = task.result()
                        if isinstance(outcome, tuple):
                            item_id, result = outcome
                            self._persist_result(item_id, result)
                    except (asyncio.CancelledError, Exception) as e:
                        logger.warning("coordinator: drained task error: %s", e)

            self._emit_heartbeat()
            logger.info("coordinator: shutdown complete (completed_this_run=%d)",
                        self._completed_this_run)
            return 0

        except Exception as e:
            logger.exception("coordinator: fatal error: %s", e)
            return 1
