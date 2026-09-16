"""
SimClock — Accelerated time control for arena simulations.

The clock is the heartbeat of the entire simulation. Every component reads
time from the clock — never from datetime.now(). The bot's phase transitions
(Phase 1-4 at 4:00, 9:30, 10:00, 15:45, 16:00 ET) are driven by this clock.

Modes:
- REPLAY: advance bar-by-bar as fast as possible (~30ms/day)
- REALTIME_SCALED: advance at Nx real speed (for watching behavior)
- MANUAL: advance only when .tick() is called (for debugging)
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time as _time_module
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class ClockMode(enum.Enum):
    REPLAY = "replay"
    REALTIME_SCALED = "realtime_scaled"
    MANUAL = "manual"


class SimClock:
    """
    Simulated market clock with accelerated replay.

    All arena components must read time from clock.now, never datetime.now().
    The harness monkey-patches datetime.now() and time.time() to delegate here.
    """

    def __init__(
        self,
        start: datetime,
        end: datetime,
        mode: ClockMode = ClockMode.REPLAY,
        speed_multiplier: float = 1000.0,
    ):
        self._current = start
        self._end = end
        self._mode = mode
        self._speed_multiplier = speed_multiplier
        self._subscribers: list[Callable[[datetime], Any]] = []
        self._async_subscribers: list[Callable[[datetime], Any]] = []

        # Market hours (ET)
        self._market_open = time(9, 30)
        self._market_close = time(16, 0)
        self._premarket_start = time(4, 0)

        # Track ticks for performance monitoring
        self._tick_count = 0

    @property
    def now(self) -> datetime:
        """Current simulated time (always UTC)."""
        return self._current

    @property
    def now_et(self) -> datetime:
        """Current simulated time in Eastern."""
        return self._current.astimezone(ET)

    @property
    def is_market_open(self) -> bool:
        et = self.now_et
        return self._market_open <= et.time() < self._market_close

    @property
    def is_premarket(self) -> bool:
        et = self.now_et
        return self._premarket_start <= et.time() < self._market_open

    @property
    def next_open(self) -> datetime:
        """Next market open (9:30 ET) from current time."""
        et = self.now_et
        today_open = et.replace(
            hour=9, minute=30, second=0, microsecond=0
        )
        if et.time() >= self._market_open:
            # Next day
            today_open += timedelta(days=1)
            # Skip weekends
            while today_open.weekday() >= 5:
                today_open += timedelta(days=1)
        return today_open.astimezone(timezone.utc)

    @property
    def next_close(self) -> datetime:
        """Next market close (16:00 ET) from current time."""
        et = self.now_et
        today_close = et.replace(
            hour=16, minute=0, second=0, microsecond=0
        )
        if et.time() >= self._market_close:
            today_close += timedelta(days=1)
            while today_close.weekday() >= 5:
                today_close += timedelta(days=1)
        return today_close.astimezone(timezone.utc)

    @property
    def done(self) -> bool:
        return self._current >= self._end

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def subscribe(self, callback: Callable[[datetime], Any]) -> None:
        """Register a sync callback for each tick."""
        self._subscribers.append(callback)

    def subscribe_async(self, callback: Callable[[datetime], Any]) -> None:
        """Register an async callback for each tick."""
        self._async_subscribers.append(callback)

    async def tick(self, delta: timedelta = timedelta(minutes=1)) -> None:
        """Advance clock by delta and notify all subscribers."""
        self._current += delta
        self._tick_count += 1

        for cb in self._subscribers:
            cb(self._current)
        for cb in self._async_subscribers:
            await cb(self._current)

    async def run(self) -> None:
        """Run clock to completion."""
        logger.info(
            "SimClock starting: %s → %s (%s mode)",
            self._current.isoformat(),
            self._end.isoformat(),
            self._mode.value,
        )
        t0 = _time_module.perf_counter()

        while self._current < self._end:
            await self.tick()

            if self._mode == ClockMode.REALTIME_SCALED:
                await asyncio.sleep(60.0 / self._speed_multiplier)
            elif self._mode == ClockMode.REPLAY:
                # Yield to event loop periodically
                if self._tick_count % 10 == 0:
                    await asyncio.sleep(0)

        elapsed = _time_module.perf_counter() - t0
        logger.info(
            "SimClock done: %d ticks in %.3fs (%.0f ticks/sec)",
            self._tick_count,
            elapsed,
            self._tick_count / max(elapsed, 0.001),
        )

    def reset(self, start: datetime, end: datetime) -> None:
        """Reset clock for a new simulation run."""
        self._current = start
        self._end = end
        self._tick_count = 0


# ── Monkey-patching helpers ──────────────────────────────────────────

_active_clock: SimClock | None = None
_original_datetime_now = datetime.now
_original_time_time = _time_module.time


def activate_time_patch(clock: SimClock) -> None:
    """
    Monkey-patch datetime.now() and time.time() to return simulated time.

    Must be called BEFORE importing the bot code.
    """
    global _active_clock
    _active_clock = clock

    # We can't directly replace datetime.now on the builtin type,
    # but we can patch it at the module level where the bot imports it.
    # The harness will handle this via importlib tricks.


def get_sim_time() -> float:
    """Replacement for time.time() — returns simulated epoch."""
    if _active_clock is not None:
        return _active_clock.now.timestamp()
    return _original_time_time()


def get_sim_now(tz: timezone | None = None) -> datetime:
    """Replacement for datetime.now() — returns simulated time."""
    if _active_clock is not None:
        now = _active_clock.now
        if tz is not None:
            return now.astimezone(tz)
        return now
    return _original_datetime_now(tz)
