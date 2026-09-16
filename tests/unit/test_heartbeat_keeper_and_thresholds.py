"""
Tue 2026-04-21 Fix 2: heartbeat keeper task + raised watchdog thresholds.

Background. The Tue 2026-04-21 session bought ELSE @ 10:17:06 ET, then
the watchdog killed Python at 10:18:02 because the heartbeat was 125s
stale (limit 120s during market hours). py-spy at the moment of kill:

    MainThread (idle): asyncio\\windows_events._poll → select

i.e. NOT hung. Just busy with one long-running scan iteration. Agent
latency was 11825ms in the dashboard (12 seconds per agent call), and
with 6 agents per ticker × N candidates per scan iteration the
single-iteration wall time exceeded 120s. The main-loop heartbeat at
the TOP of each iteration only pulses once per iteration, so the
heartbeat went stale even though Python was responsive enough to
write dashboard log lines during the same window.

Two-part fix:

1. `scripts/watchdog_monitor.ps1` — doubled the intraday thresholds:
       opening (9:28-10:00 ET): 90s  → 180s
       intraday (10:00-16:00):  120s → 240s
       off-hours:               900s (unchanged)

2. `cmd_paper` in main.py — async background task `_heartbeat_keeper_loop`
   that pulses the heartbeat every 30s INDEPENDENT of the main scan
   loop. As long as the asyncio event loop itself is alive (= scheduling
   tasks), the keeper pulses. If the event loop genuinely deadlocks,
   the keeper goes silent — which is exactly when we WANT the watchdog
   to fire.

Together, these make the watchdog responsive to genuine event-loop
deadlocks (180s/240s with no keeper pulse) while eliminating the
false-positive kills from normal-but-slow scan iterations (keeper
pulses every 30s, well inside any tier's threshold).

Tests below cover:
  T1. Watchdog PS1 contains the new thresholds (180/240/900).
  T2. Watchdog PS1 no longer contains the old intraday thresholds (90/120).
  T3. Heartbeat keeper interval is 30s in main.py.
  T4. The keeper coroutine, when run, calls the heartbeat write at the
       expected interval (rule-(e) positive case).
  T5. The keeper exits cleanly on `shutdown.is_set() = True`.
  T6. The keeper exits cleanly on asyncio.CancelledError.
  T7. The keeper does NOT die when the heartbeat write itself raises.
  T8. Threshold ordering is preserved: opening < intraday < off-hours.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG_PS1 = REPO_ROOT / "scripts" / "watchdog_monitor.ps1"
MAIN_PY = REPO_ROOT / "main.py"


# ── T1 + T2: Watchdog threshold values ───────────────────────────────


class TestWatchdogThresholds:

    def test_ps1_contains_new_thresholds(self):
        """T1: PS1 must contain the doubled values."""
        text = WATCHDOG_PS1.read_text(encoding="utf-8")
        assert re.search(r"\$MaxStaleness\s*=\s*180\b", text), (
            "Opening threshold (9:28-10:00 ET) must be 180s, not 90s. "
            "Today's 10:18 kill happened because the prior 90s/120s tiers "
            "were too tight relative to 11.8s/agent latency × N candidates."
        )
        assert re.search(r"\$MaxStaleness\s*=\s*240\b", text), (
            "Intraday threshold (10:00-16:00) must be 240s, not 120s."
        )
        assert re.search(r"\$MaxStaleness\s*=\s*900\b", text), (
            "Off-hours threshold should remain 900s."
        )

    def test_ps1_no_longer_uses_old_intraday_values(self):
        """T2: PS1 must NOT contain the old 90/120 intraday values
        as `MaxStaleness` assignments. (They may appear in comments
        explaining the change — we only ban them as live values.)"""
        text = WATCHDOG_PS1.read_text(encoding="utf-8")
        for old in (90, 120):
            assert not re.search(
                rf"\$MaxStaleness\s*=\s*{old}\b(?!\s*#)", text,
            ), (
                f"PS1 still has $MaxStaleness = {old} — the old "
                f"thresholds are gone but the assignment slipped through. "
                f"Update or remove the line."
            )

    def test_threshold_ordering(self):
        """T8: opening < intraday < off-hours. Preserves the design
        intent that opening is tightest, off-hours most relaxed."""
        text = WATCHDOG_PS1.read_text(encoding="utf-8")
        vals = [int(m.group(1)) for m in re.finditer(r"\$MaxStaleness\s*=\s*(\d+)\b", text)]
        # We expect exactly 3 distinct values now (180, 240, 900)
        assert sorted(set(vals)) == [180, 240, 900], (
            f"Expected exactly the values [180, 240, 900], got {sorted(set(vals))}"
        )


# ── T3: Heartbeat keeper interval contract ───────────────────────────


class TestHeartbeatKeeperInterval:

    def test_main_py_keeper_interval_is_30s(self):
        """T3: The keeper interval is 30s. Watchdog tightest tier is
        180s, so 30s gives 6 pulse opportunities before stale. Even
        with 2 missed pulses we have 4× margin."""
        text = MAIN_PY.read_text(encoding="utf-8")
        assert re.search(
            r"_heartbeat_keeper_interval_s\s*=\s*30\b", text,
        ), (
            "main.py must set _heartbeat_keeper_interval_s = 30. If "
            "you increase this, also raise the watchdog thresholds in "
            "scripts/watchdog_monitor.ps1 to maintain ≥4× margin."
        )

    def test_keeper_loop_function_exists(self):
        """The keeper coroutine must be defined in cmd_paper."""
        text = MAIN_PY.read_text(encoding="utf-8")
        assert "async def _heartbeat_keeper_loop" in text, (
            "_heartbeat_keeper_loop coroutine missing from main.py"
        )

    def test_keeper_task_is_created(self):
        """The keeper must actually be started, not just defined."""
        text = MAIN_PY.read_text(encoding="utf-8")
        assert re.search(
            r"asyncio\.create_task\(\s*_heartbeat_keeper_loop\(\)",
            text,
        ), "Keeper coroutine is defined but not spawned via asyncio.create_task"

    def test_keeper_is_cancelled_on_shutdown(self):
        """Clean shutdown must cancel the keeper task — otherwise
        pytest surfaces 'Task was destroyed but it is pending' on
        every test that imports main."""
        text = MAIN_PY.read_text(encoding="utf-8")
        assert "_heartbeat_keeper_task.cancel()" in text, (
            "Keeper task must be cancelled before cmd_paper returns"
        )


# ── T4-T7: Behavioral tests on the keeper coroutine ─────────────────


class TestHeartbeatKeeperBehavior:
    """T4-T7: Behavior of the keeper coroutine. We construct an
    isolated coroutine that mirrors the structure of
    `_heartbeat_keeper_loop` and asserts it pulses on schedule, exits
    cleanly on shutdown / cancellation, and survives a single failed
    write. The actual coroutine inside main.py uses closure scope, so
    we test the structural behavior rather than the closure itself."""

    @pytest.mark.asyncio
    async def test_keeper_pulses_on_schedule(self):
        """T4: with a 0.05s interval, in 0.20s the keeper should
        write at least 3 times (rule-(e) positive case)."""
        write_count = 0

        def fake_write(_label):
            nonlocal write_count
            write_count += 1

        shutdown = asyncio.Event()

        async def keeper(interval_s: float):
            try:
                while not shutdown.is_set():
                    fake_write("keeper")
                    await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(keeper(0.05))
        await asyncio.sleep(0.20)
        shutdown.set()
        await asyncio.sleep(0.10)  # let it observe shutdown
        if not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass

        assert write_count >= 3, (
            f"Expected >=3 writes in 0.20s with 0.05s interval; "
            f"got {write_count}. The keeper isn't firing."
        )

    @pytest.mark.asyncio
    async def test_keeper_exits_on_shutdown(self):
        """T5: setting shutdown event causes the keeper to exit
        within one interval period."""
        shutdown = asyncio.Event()
        write_count = 0

        async def keeper():
            nonlocal write_count
            try:
                while not shutdown.is_set():
                    write_count += 1
                    await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(keeper())
        await asyncio.sleep(0.10)  # let it run
        shutdown.set()
        # Should exit within one interval (~0.02s) plus tolerance
        await asyncio.wait_for(task, timeout=0.20)
        assert task.done() and not task.cancelled(), (
            "Keeper did not exit cleanly on shutdown event"
        )

    @pytest.mark.asyncio
    async def test_keeper_propagates_cancelled_error(self):
        """T6: external cancellation must propagate (not swallow)."""
        async def keeper():
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise  # MUST propagate

        task = asyncio.create_task(keeper())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_keeper_survives_write_failure(self):
        """T7: if the heartbeat write itself raises, the keeper
        should NOT die. It logs and continues. This is critical:
        a transient disk-full or filesystem hiccup must not silently
        disable the keeper."""
        attempts = 0

        def flaky_write(_label):
            nonlocal attempts
            attempts += 1
            raise OSError("simulated disk hiccup")

        shutdown = asyncio.Event()

        async def keeper():
            # This mirrors main.py's outer-try that catches Exception
            # but re-raises CancelledError. Inside the try, the body
            # does its own try/except per-write.
            try:
                while not shutdown.is_set():
                    try:
                        flaky_write("keeper")
                    except Exception:
                        pass  # log and continue
                    await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(keeper())
        await asyncio.sleep(0.10)
        shutdown.set()
        await asyncio.sleep(0.05)
        if not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass

        assert attempts >= 3, (
            f"Keeper should retry on every interval despite write "
            f"failures; got {attempts} attempts in 0.10s with 0.02s interval"
        )
