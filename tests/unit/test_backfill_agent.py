"""D221 Phase B tests — backfill agent foundation.

Covers:
  - WorkQueue: enqueue idempotency, claim atomicity, stale auto-release,
    crash safety (kill mid-write simulated via process), stats correctness
  - RateBudget: window classification, paused/limited/off-hours/weekend
    transitions with mocked clock, ignore_budget mode
  - Worker: atomic write tempfile cleanup on cancellation, dry_run mode,
    HTTP error classification (429 transient, 4xx permanent, 5xx transient)
  - Coordinator: SIGTERM drains in-flight (signal mocked), max_items cap

15+ tests required. All deterministic; no real network / no real time.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.backfill_agent.budget import (
    BudgetWindow,
    RateBudget,
    WINDOW_RATE_PER_MIN,
    _classify_window,
)
from src.backfill_agent.state import (
    STALE_IN_PROGRESS_SECS,
    WorkItem,
    WorkQueue,
    WorkStatus,
)
from src.backfill_agent.worker import (
    Worker,
    WorkResult,
    _atomic_write_json,
    _append_jsonl_safe,
)


_NY = ZoneInfo("America/New_York")


# ── WorkQueue tests ─────────────────────────────────────────────────────


class TestWorkQueue:

    def test_enqueue_returns_true_on_new(self, tmp_path):
        q = WorkQueue(path=tmp_path / "queue.db")
        assert q.enqueue("AAPL", "2026-04-17") is True
        assert q.enqueue("AAPL", "2026-04-17") is False  # duplicate
        assert q.total_count() == 1

    def test_enqueue_batch(self, tmp_path):
        q = WorkQueue(path=tmp_path / "queue.db")
        items = [(f"T{i}", "2026-04-17", 0) for i in range(10)]
        items.append(("T0", "2026-04-17", 0))  # duplicate
        n = q.enqueue_batch(items)
        assert n == 10
        assert q.total_count() == 10

    def test_claim_atomic_no_double_grab(self, tmp_path):
        """Two concurrent claim_next calls must NOT return the same item."""
        q = WorkQueue(path=tmp_path / "queue.db")
        for i in range(5):
            q.enqueue(f"T{i}", "2026-04-17")
        a = q.claim_next("worker_a", max_items=3)
        b = q.claim_next("worker_b", max_items=3)
        a_ids = {x.id for x in a}
        b_ids = {x.id for x in b}
        assert not (a_ids & b_ids), f"workers grabbed same items: {a_ids & b_ids}"
        assert len(a_ids) + len(b_ids) == 5

    def test_complete_marks_status(self, tmp_path):
        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("AAPL", "2026-04-17")
        items = q.claim_next("w1", 1)
        assert len(items) == 1
        q.complete(items[0].id)
        stats = q.stats()
        assert stats["counts"][WorkStatus.COMPLETED.value] == 1
        assert stats["counts"][WorkStatus.PENDING.value] == 0
        assert stats["counts"][WorkStatus.IN_PROGRESS.value] == 0

    def test_fail_transient_releases_to_pending_under_max(self, tmp_path):
        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("AAPL", "2026-04-17")
        items = q.claim_next("w1", 1)
        q.fail_transient(items[0].id, "network", max_attempts=3)
        stats = q.stats()
        assert stats["counts"][WorkStatus.PENDING.value] == 1

    def test_fail_transient_marks_permanent_at_max_attempts(self, tmp_path):
        """After max_attempts, transient failure becomes permanent."""
        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("AAPL", "2026-04-17")
        # Three claims, three transient failures
        for i in range(3):
            items = q.claim_next("w1", 1)
            assert len(items) == 1
            q.fail_transient(items[0].id, f"attempt {i+1}", max_attempts=3)
        # Item should now be permanent
        stats = q.stats()
        assert stats["counts"][WorkStatus.FAILED_PERMANENT.value] == 1
        assert stats["counts"][WorkStatus.PENDING.value] == 0

    def test_stale_in_progress_auto_released(self, tmp_path, monkeypatch):
        """Items in_progress > STALE_IN_PROGRESS_SECS get released back to pending."""
        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("AAPL", "2026-04-17")
        items = q.claim_next("w1", 1)
        assert len(items) == 1

        # Simulate time passing: directly mutate claimed_at to be old
        with sqlite3.connect(str(q.path)) as conn:
            conn.execute(
                "UPDATE work_items SET claimed_at = ? WHERE id = ?",
                ("2020-01-01T00:00:00.000000Z", items[0].id),
            )
            conn.commit()

        # Next claim_next call triggers _release_stale_locked + claims it
        new_items = q.claim_next("w2", 1)
        assert len(new_items) == 1
        assert new_items[0].id == items[0].id  # same item, re-claimed

    def test_reset_in_progress(self, tmp_path):
        q = WorkQueue(path=tmp_path / "queue.db")
        for i in range(3):
            q.enqueue(f"T{i}", "2026-04-17")
        q.claim_next("w1", 3)
        assert q.stats()["counts"][WorkStatus.IN_PROGRESS.value] == 3
        n = q.reset_in_progress()
        assert n == 3
        assert q.stats()["counts"][WorkStatus.PENDING.value] == 3


# ── RateBudget tests ────────────────────────────────────────────────────


class TestRateBudget:

    def test_classify_window_paused_pre_market(self):
        # 7:00 AM ET on a weekday — production scan window
        et = datetime(2026, 4, 17, 7, 0, tzinfo=_NY)
        assert _classify_window(et) == BudgetWindow.PAUSED

    def test_classify_window_limited_trading_hours(self):
        # 11:30 AM ET on a weekday
        et = datetime(2026, 4, 17, 11, 30, tzinfo=_NY)
        assert _classify_window(et) == BudgetWindow.LIMITED

    def test_classify_window_off_hours(self):
        # 9:00 PM ET on a weekday
        et = datetime(2026, 4, 17, 21, 0, tzinfo=_NY)
        assert _classify_window(et) == BudgetWindow.OFF_HOURS

    def test_classify_window_weekend(self):
        # Saturday 11:00 AM ET
        et = datetime(2026, 4, 18, 11, 0, tzinfo=_NY)
        assert _classify_window(et) == BudgetWindow.WEEKEND

    def test_paused_window_has_zero_rate(self):
        assert WINDOW_RATE_PER_MIN[BudgetWindow.PAUSED] == 0

    def test_off_hours_under_alpaca_server_cap(self):
        """Off-hours rate must stay safely under Alpaca's ~200/min server cap."""
        assert WINDOW_RATE_PER_MIN[BudgetWindow.OFF_HOURS] < 200
        assert WINDOW_RATE_PER_MIN[BudgetWindow.WEEKEND] < 200

    @pytest.mark.asyncio
    async def test_acquire_returns_immediately_when_ignore_set(self):
        """ignore_budget=True bypasses all throttling."""
        clock_val = datetime(2026, 4, 17, 7, 0, tzinfo=_NY)  # PAUSED window
        budget = RateBudget(ignore_budget=True, clock=lambda: clock_val)
        # In paused mode, acquire would normally block forever.
        # With ignore=True it should return immediately.
        await asyncio.wait_for(budget.acquire("alpaca", n=1), timeout=1.0)

    @pytest.mark.asyncio
    async def test_status_safe_to_call_anytime(self):
        budget = RateBudget(clock=lambda: datetime(2026, 4, 17, 21, 0, tzinfo=_NY))
        s = budget.status()
        assert s["window"] == BudgetWindow.OFF_HOURS.value
        assert s["rate_per_min"] == WINDOW_RATE_PER_MIN[BudgetWindow.OFF_HOURS]


# ── Worker / atomic write tests ─────────────────────────────────────────


class TestAtomicWrites:

    def test_atomic_write_creates_file(self, tmp_path):
        target = tmp_path / "subdir" / "out.json"
        _atomic_write_json(target, {"k": "v"})
        assert target.exists()
        assert json.loads(target.read_text()) == {"k": "v"}

    def test_atomic_write_no_tempfile_left_on_success(self, tmp_path):
        target = tmp_path / "out.json"
        _atomic_write_json(target, {"k": "v"})
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == [], f"tempfile leak: {leftovers}"

    def test_atomic_write_cleanup_on_simulated_failure(self, tmp_path, monkeypatch):
        """If os.replace fails, the tempfile must NOT be left on disk."""
        target = tmp_path / "out.json"
        original_replace = os.replace

        def fail_replace(*args, **kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr("os.replace", fail_replace)
        with pytest.raises(OSError, match="simulated rename failure"):
            _atomic_write_json(target, {"k": "v"})
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == [], f"tempfile leak after failure: {leftovers}"

    def test_append_jsonl_safe_appends(self, tmp_path):
        f = tmp_path / "labels.jsonl"
        _append_jsonl_safe(f, {"a": 1})
        _append_jsonl_safe(f, {"b": 2})
        rows = [json.loads(l) for l in f.read_text().splitlines()]
        assert rows == [{"a": 1}, {"b": 2}]


class TestWorker:

    @pytest.mark.asyncio
    async def test_dry_run_skips_api_call(self, tmp_path, monkeypatch):
        """In dry-run mode, missing-bar items return success=True without API call."""
        # Point bar root to a fresh tmp dir so no bars exist
        monkeypatch.setattr("src.backfill_agent.worker._BAR_ROOT", tmp_path / "bars")
        budget = RateBudget(ignore_budget=True)
        w = Worker("w-test", budget, api_key="k", api_secret="s", dry_run=True)
        item = WorkItem(id=1, ticker="AAPL", date="2026-04-17",
                        status=WorkStatus.IN_PROGRESS)
        result = await w.process(item)
        assert result.success is True
        assert result.labeled is False  # dry-run doesn't label either

    @pytest.mark.asyncio
    async def test_worker_handles_429_as_transient(self, tmp_path, monkeypatch):
        """HTTP 429 returns a non-permanent failure (queue retries)."""
        monkeypatch.setattr("src.backfill_agent.worker._BAR_ROOT", tmp_path / "bars")
        budget = RateBudget(ignore_budget=True)

        class _Mock429Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                m = MagicMock()
                m.status_code = 429
                m.text = "rate limited"
                return m

        w = Worker("w-test", budget, api_key="k", api_secret="s",
                   http_client_factory=_Mock429Client)
        item = WorkItem(id=1, ticker="AAPL", date="2026-04-17",
                        status=WorkStatus.IN_PROGRESS)
        result = await w.process(item)
        assert result.success is False
        assert result.fail_permanent is False  # transient, retry
        assert "rate" in (result.error or "").lower() or "429" in (result.error or "")

    @pytest.mark.asyncio
    async def test_worker_handles_404_as_permanent(self, tmp_path, monkeypatch):
        """HTTP 404 (no data for ticker that day) → fail_permanent."""
        monkeypatch.setattr("src.backfill_agent.worker._BAR_ROOT", tmp_path / "bars")
        budget = RateBudget(ignore_budget=True)

        class _Mock404Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                m = MagicMock()
                m.status_code = 404
                m.text = "not found"
                return m

        w = Worker("w-test", budget, api_key="k", api_secret="s",
                   http_client_factory=_Mock404Client)
        item = WorkItem(id=1, ticker="ZZZZ", date="2026-04-17",
                        status=WorkStatus.IN_PROGRESS)
        result = await w.process(item)
        assert result.success is False
        assert result.fail_permanent is True

    @pytest.mark.asyncio
    async def test_worker_skips_bars_already_on_disk(self, tmp_path, monkeypatch):
        """If a valid bar file already exists, worker skips fetching."""
        bar_root = tmp_path / "bars"
        monkeypatch.setattr("src.backfill_agent.worker._BAR_ROOT", bar_root)
        # Pre-create a valid bar file with 35 bars
        date_dir = bar_root / "2026-04-17"
        date_dir.mkdir(parents=True)
        good_bars = [
            {"timestamp": f"2026-04-17T13:{30+i:02d}:00Z", "open": 1.0, "high": 1.1,
             "low": 0.9, "close": 1.05, "volume": 100, "vwap": 1.0}
            for i in range(35)
        ]
        (date_dir / "AAPL.json").write_text(json.dumps({"ticker": "AAPL", "date": "2026-04-17", "bars": good_bars}))

        budget = RateBudget(ignore_budget=True)
        w = Worker("w-test", budget, api_key="k", api_secret="s")
        item = WorkItem(id=1, ticker="AAPL", date="2026-04-17",
                        status=WorkStatus.IN_PROGRESS)
        # Stub label function so it doesn't try to find AAPL in candidates.jsonl
        with patch("src.backfill_agent.worker._load_label_function",
                   return_value=lambda c, b: {"date": "2026-04-17", "ticker": "AAPL", "close": 0.05}), \
             patch("src.backfill_agent.worker.Worker._candidate_lookup",
                   return_value={"date": "2026-04-17", "ticker": "AAPL", "gap_pct": 0.1}):
            result = await w.process(item)
        # Should have used existing bars, not made an HTTP call
        assert result.success is True
        assert result.bars_count == 35

    @pytest.mark.asyncio
    async def test_worker_cancellation_propagates(self, tmp_path, monkeypatch):
        """asyncio.CancelledError must propagate, not be swallowed."""
        monkeypatch.setattr("src.backfill_agent.worker._BAR_ROOT", tmp_path / "bars")
        budget = RateBudget(ignore_budget=True)

        class _SlowClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                await asyncio.sleep(10)
                return MagicMock(status_code=200)

        w = Worker("w-test", budget, api_key="k", api_secret="s",
                   http_client_factory=_SlowClient)
        item = WorkItem(id=1, ticker="AAPL", date="2026-04-17",
                        status=WorkStatus.IN_PROGRESS)
        task = asyncio.create_task(w.process(item))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── Coordinator tests ──────────────────────────────────────────────────


class TestCoordinator:

    @pytest.mark.asyncio
    async def test_coordinator_max_items_cap(self, tmp_path, monkeypatch):
        """Coordinator stops after max_items completed."""
        from src.backfill_agent.coordinator import Coordinator
        monkeypatch.setattr("src.backfill_agent.coordinator._HEARTBEAT_PATH",
                            tmp_path / "heartbeat.json")

        q = WorkQueue(path=tmp_path / "queue.db")
        for i in range(10):
            q.enqueue(f"T{i}", "2026-04-17")

        budget = RateBudget(ignore_budget=True)
        coord = Coordinator(queue=q, budget=budget, workers=2, dry_run=True, max_items=3)
        # Patch worker process to instantly return success
        async def _fast_process(self_, item):
            return WorkResult(item_id=item.id, success=True, bars_count=1, labeled=True)
        with patch("src.backfill_agent.worker.Worker.process", _fast_process):
            await asyncio.wait_for(coord.run_forever(), timeout=10.0)

        # At most 3 should be completed (cap may overshoot by batch size)
        completed = q.stats()["counts"][WorkStatus.COMPLETED.value]
        assert 3 <= completed <= 5  # batch of 2 workers may cause slight overshoot

    @pytest.mark.asyncio
    async def test_coordinator_emits_heartbeat(self, tmp_path, monkeypatch):
        """After processing items, heartbeat.json exists with snapshot."""
        from src.backfill_agent.coordinator import Coordinator
        hb_path = tmp_path / "heartbeat.json"
        monkeypatch.setattr("src.backfill_agent.coordinator._HEARTBEAT_PATH", hb_path)
        # Make pause sentinel point to a non-existent path so default = not paused
        monkeypatch.setattr("src.backfill_agent.coordinator._PAUSE_SENTINEL",
                            tmp_path / "PAUSED")

        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("AAPL", "2026-04-17")
        budget = RateBudget(ignore_budget=True)
        coord = Coordinator(queue=q, budget=budget, workers=1, dry_run=True, max_items=1)

        async def _fast_process(self_, item):
            return WorkResult(item_id=item.id, success=True, bars_count=0, labeled=False)
        with patch("src.backfill_agent.worker.Worker.process", _fast_process):
            await asyncio.wait_for(coord.run_forever(), timeout=10.0)

        assert hb_path.exists()
        snap = json.loads(hb_path.read_text())
        assert "queue" in snap
        assert "budget" in snap
        assert snap["completed_this_run"] == 1
        assert snap["paused"] is False  # sentinel doesn't exist


# ── Pause sentinel tests ────────────────────────────────────────────────


class TestPauseSentinel:
    """The PAUSED sentinel file at data/backfill_agent/PAUSED stops new work
    while keeping the heartbeat fresh."""

    def test_is_paused_false_when_sentinel_absent(self, tmp_path, monkeypatch):
        from src.backfill_agent import coordinator as coord_mod
        monkeypatch.setattr(coord_mod, "_PAUSE_SENTINEL", tmp_path / "PAUSED")
        assert coord_mod.is_paused() is False

    def test_is_paused_true_when_sentinel_present(self, tmp_path, monkeypatch):
        from src.backfill_agent import coordinator as coord_mod
        sentinel = tmp_path / "PAUSED"
        monkeypatch.setattr(coord_mod, "_PAUSE_SENTINEL", sentinel)
        sentinel.touch()
        assert coord_mod.is_paused() is True

    @pytest.mark.asyncio
    async def test_coordinator_skips_work_while_paused(self, tmp_path, monkeypatch):
        """When sentinel exists at startup, coordinator emits heartbeat with
        paused=True and does NOT process items, but exits cleanly on shutdown."""
        from src.backfill_agent import coordinator as coord_mod
        sentinel = tmp_path / "PAUSED"
        sentinel.touch()
        monkeypatch.setattr(coord_mod, "_PAUSE_SENTINEL", sentinel)
        monkeypatch.setattr(coord_mod, "_HEARTBEAT_PATH", tmp_path / "hb.json")
        monkeypatch.setattr(coord_mod, "_PAUSE_POLL_INTERVAL_SEC", 0.05)

        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("PAUSEDTKR", "2026-04-17")
        budget = RateBudget(ignore_budget=True)
        coord = coord_mod.Coordinator(queue=q, budget=budget, workers=1, dry_run=True)

        # Run for ~150ms, then trigger shutdown via signal-equivalent
        async def _trigger_shutdown_after_delay():
            await asyncio.sleep(0.15)
            coord._shutdown_requested = True

        async def _never_called(self_, item):
            raise AssertionError("worker should not run while paused")

        with patch("src.backfill_agent.worker.Worker.process", _never_called):
            await asyncio.wait_for(
                asyncio.gather(coord.run_forever(), _trigger_shutdown_after_delay()),
                timeout=2.0,
            )

        # Heartbeat should show paused=True, no items completed
        snap = json.loads((tmp_path / "hb.json").read_text())
        assert snap["paused"] is True
        assert snap["completed_this_run"] == 0
        # Item still pending
        assert q.stats()["counts"][WorkStatus.PENDING.value] == 1

    @pytest.mark.asyncio
    async def test_coordinator_resumes_when_sentinel_cleared(self, tmp_path, monkeypatch):
        """Sentinel cleared mid-run — next loop iteration resumes processing."""
        from src.backfill_agent import coordinator as coord_mod
        sentinel = tmp_path / "PAUSED"
        sentinel.touch()  # start paused
        monkeypatch.setattr(coord_mod, "_PAUSE_SENTINEL", sentinel)
        monkeypatch.setattr(coord_mod, "_HEARTBEAT_PATH", tmp_path / "hb.json")
        monkeypatch.setattr(coord_mod, "_PAUSE_POLL_INTERVAL_SEC", 0.05)

        q = WorkQueue(path=tmp_path / "queue.db")
        q.enqueue("RESUME1", "2026-04-17")
        budget = RateBudget(ignore_budget=True)
        coord = coord_mod.Coordinator(queue=q, budget=budget, workers=1,
                                       dry_run=True, max_items=1)

        # Clear sentinel after 100ms — coordinator should resume on next poll
        async def _unpause_after_delay():
            await asyncio.sleep(0.1)
            sentinel.unlink()

        async def _fast_process(self_, item):
            return WorkResult(item_id=item.id, success=True, bars_count=0, labeled=False)

        with patch("src.backfill_agent.worker.Worker.process", _fast_process):
            await asyncio.wait_for(
                asyncio.gather(coord.run_forever(), _unpause_after_delay()),
                timeout=3.0,
            )

        # Item should now be completed
        assert q.stats()["counts"][WorkStatus.COMPLETED.value] == 1
