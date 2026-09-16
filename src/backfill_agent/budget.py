"""Rate budget — production-aware throttling for backfill API calls.

Hard rules per the D221 spec (Phase A determined Alpaca free-tier server cap is
~200/min, much lower than the in-process 9000/min token-bucket budget):

  04:25-09:35 ET: backfill PAUSED (production scan window — zero competition)
  09:30-16:00 ET: backfill at 20/min (production trading; small headroom)
  16:00-04:25 ET: backfill at 80/min (off-hours; safe under 200/min server cap)
  Saturday/Sunday: backfill at 150/min (near-full)

The budget is per-PROVIDER (currently only "alpaca" — Finnhub usage is in the
labeling pipeline, which is local compute, no API).

Implementation: simple token bucket with refill rate that varies by ET clock.
The acquire() call blocks until budget is available. Pause windows are
implemented as zero refill rate — acquire() loops until window ends.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")


class BudgetWindow(str, Enum):
    PAUSED = "paused"                       # 04:25-09:35 ET (production scan)
    LIMITED = "limited"                     # 09:30-16:00 ET (trading hours)
    OFF_HOURS = "off_hours"                 # 16:00-04:25 ET (overnight)
    WEEKEND = "weekend"                     # Sat/Sun all day


# Tokens per minute per window (per provider).
WINDOW_RATE_PER_MIN: dict[BudgetWindow, int] = {
    BudgetWindow.PAUSED: 0,
    BudgetWindow.LIMITED: 20,
    BudgetWindow.OFF_HOURS: 80,
    BudgetWindow.WEEKEND: 150,
}


def _classify_window(now_et: datetime) -> BudgetWindow:
    """Classify the current ET clock into a budget window."""
    if now_et.weekday() >= 5:                # Sat=5, Sun=6
        return BudgetWindow.WEEKEND
    t = now_et.time()
    # 04:25-09:35 PAUSED — production scan + market open burst
    if time(4, 25) <= t < time(9, 35):
        return BudgetWindow.PAUSED
    # 09:30-16:00 LIMITED — trading hours (small overlap with PAUSED is fine; PAUSED wins)
    if time(9, 35) <= t < time(16, 0):
        return BudgetWindow.LIMITED
    # Everything else off-hours
    return BudgetWindow.OFF_HOURS


@dataclass
class _ProviderState:
    tokens: float
    last_refill_monotonic: float
    last_window: BudgetWindow


class RateBudget:
    """Per-provider token bucket whose rate depends on the current time window.

    Usage:
        budget = RateBudget()
        await budget.acquire("alpaca")      # blocks until 1 token available
        await budget.acquire("alpaca", n=5)  # 5 tokens
    """

    def __init__(
        self,
        ignore_budget: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            ignore_budget: emergency override — return immediately on every acquire.
                Use only for manual debug runs.
            clock: testable clock function returning a tz-aware UTC datetime.
                Default is real wall clock.
        """
        self._ignore = ignore_budget
        self._clock = clock or (lambda: datetime.now(_NY).astimezone(_NY))
        self._states: dict[str, _ProviderState] = {}
        self._lock = asyncio.Lock()

    def _now_et(self) -> datetime:
        return self._clock().astimezone(_NY) if self._clock().tzinfo else self._clock().replace(tzinfo=_NY)

    def _state(self, provider: str) -> _ProviderState:
        if provider not in self._states:
            self._states[provider] = _ProviderState(
                tokens=0.0,
                last_refill_monotonic=asyncio.get_event_loop().time(),
                last_window=BudgetWindow.OFF_HOURS,
            )
        return self._states[provider]

    def current_window(self) -> BudgetWindow:
        return _classify_window(self._now_et())

    def current_rate_per_min(self) -> int:
        return WINDOW_RATE_PER_MIN[self.current_window()]

    def status(self) -> dict:
        """Snapshot for monitor.py — never blocks."""
        w = self.current_window()
        return {
            "window": w.value,
            "rate_per_min": WINDOW_RATE_PER_MIN[w],
            "et_time": self._now_et().strftime("%H:%M:%S %Z"),
            "providers": {
                name: {"tokens": round(s.tokens, 2)}
                for name, s in self._states.items()
            },
        }

    async def acquire(self, provider: str, n: int = 1) -> None:
        """Block until n tokens are available for `provider`."""
        if self._ignore:
            if not getattr(self, "_warned_ignore", False):
                logger.warning("RateBudget: --ignore-budget ENABLED (do NOT leave running)")
                self._warned_ignore = True
            return

        while True:
            async with self._lock:
                window = self.current_window()
                rate_per_sec = WINDOW_RATE_PER_MIN[window] / 60.0
                state = self._state(provider)

                # Log window transitions
                if state.last_window != window:
                    logger.info(
                        "RateBudget[%s]: window %s -> %s (rate=%d/min)",
                        provider, state.last_window.value, window.value,
                        WINDOW_RATE_PER_MIN[window],
                    )
                    state.last_window = window

                # Refill tokens based on monotonic time elapsed (rate may have changed)
                now_mono = asyncio.get_event_loop().time()
                elapsed = now_mono - state.last_refill_monotonic
                state.tokens += elapsed * rate_per_sec
                # Cap at 60s worth to prevent burst after long pause
                cap = max(rate_per_sec * 60, 1.0)
                if state.tokens > cap:
                    state.tokens = cap
                state.last_refill_monotonic = now_mono

                if state.tokens >= n:
                    state.tokens -= n
                    return

                # Compute sleep until next acquire could succeed
                if rate_per_sec > 0:
                    deficit = n - state.tokens
                    sleep_s = max(0.5, deficit / rate_per_sec)
                else:
                    # Paused — wait at least until the next minute, recheck window
                    sleep_s = 60.0

            await asyncio.sleep(min(sleep_s, 60.0))


# ── Test helpers ─────────────────────────────────────────────────────────


def env_ignore_flag() -> bool:
    """Honor BACKFILL_IGNORE_BUDGET env var (paranoid override)."""
    return os.environ.get("BACKFILL_IGNORE_BUDGET", "").lower() in ("1", "true", "yes")
