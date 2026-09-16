"""
D87: Async-friendly circuit breakers for external service calls.

Simple implementation that works with async/await (pybreaker is sync-only).
When a service fails `fail_max` times consecutively, the breaker OPENS and
rejects all calls for `reset_timeout` seconds. After timeout, it allows
one test call (HALF_OPEN). If that succeeds, it CLOSES. If it fails, re-OPEN.

Configuration:
  - Alpaca REST: trip after 3 failures, 30s recovery
  - LLM providers: trip after 2 failures, 120s recovery
  - News/data APIs: trip after 5 failures, 60s recovery

Usage:
    from src.utils.circuit_breaker import alpaca_breaker, llm_breaker

    async def fetch_data():
        async with alpaca_breaker:
            result = await client.get(...)
        return result

    # Or manual:
    alpaca_breaker.check()  # Raises CircuitBreakerError if open
    try:
        result = await do_work()
        alpaca_breaker.record_success()
    except Exception:
        alpaca_breaker.record_failure()
        raise
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open and rejecting calls."""

    def __init__(self, name: str, remaining_s: float):
        self.name = name
        self.remaining_s = remaining_s
        super().__init__(
            f"Circuit breaker '{name}' is OPEN. "
            f"Retry in {remaining_s:.0f}s."
        )


class AsyncCircuitBreaker:
    """Lightweight async-compatible circuit breaker.

    D108: Supports exponential backoff on reset timeout. After each failed
    HALF_OPEN probe, the timeout doubles (up to MAX_RESET_TIMEOUT). On
    successful recovery, the timeout resets to its original base value.
    """

    # States
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    # D108: Maximum reset timeout after exponential backoff (5 minutes)
    MAX_RESET_TIMEOUT = 300.0

    def __init__(
        self,
        name: str,
        fail_max: int = 3,
        reset_timeout: float = 30.0,
    ):
        self.name = name
        self.fail_max = fail_max
        self._base_reset_timeout = reset_timeout
        self._current_reset_timeout = reset_timeout
        self._state = self.CLOSED
        self._fail_count = 0
        self._last_failure_time: float = 0.0
        self._total_trips = 0

    @property
    def reset_timeout(self) -> float:
        """Current reset timeout (may be elevated by exponential backoff)."""
        return self._current_reset_timeout

    @property
    def state(self) -> str:
        """Current state, accounting for timeout-based transitions."""
        if self._state == self.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._current_reset_timeout:
                return self.HALF_OPEN
        return self._state

    def check(self) -> None:
        """Check if calls are allowed. Raises CircuitBreakerError if OPEN."""
        current = self.state
        if current == self.OPEN:
            remaining = self._current_reset_timeout - (time.monotonic() - self._last_failure_time)
            raise CircuitBreakerError(self.name, max(0, remaining))
        # HALF_OPEN and CLOSED allow calls through

    def record_success(self) -> None:
        """Record a successful call. Resets failure count and closes breaker."""
        if self._state in (self.OPEN, self.HALF_OPEN):
            logger.info(
                "D87: Circuit breaker '%s' recovered (%s -> closed). "
                "Trips: %d. D108: Reset timeout %ds -> %ds.",
                self.name, self._state, self._total_trips,
                int(self._current_reset_timeout),
                int(self._base_reset_timeout),
            )
            # D108: Reset backoff to base timeout on recovery
            self._current_reset_timeout = self._base_reset_timeout
        self._state = self.CLOSED
        self._fail_count = 0

    def record_failure(self, reason: str | Exception | None = None) -> None:
        """Record a failed call. May trip the breaker.

        D279 (2026-05-05): the optional ``reason`` parameter surfaces the
        underlying failure cause in the trip log line. On 2026-05-05 the
        llm_provider breaker tripped 65× without any indication of what
        was failing (turned out to be Together.ai HTTP 500s). Callers
        SHOULD now pass the originating exception or a short string.
        Backward-compatible: existing callers that pass nothing get the
        legacy log format.
        """
        # Capture computed state BEFORE updating timestamp (HALF_OPEN check
        # depends on elapsed time since last failure)
        current_state = self.state
        self._fail_count += 1
        self._last_failure_time = time.monotonic()
        # Stash the most recent reason for diagnostics
        if reason is not None:
            self._last_failure_reason = (
                repr(reason) if isinstance(reason, BaseException) else str(reason)
            )[:240]

        if self._fail_count >= self.fail_max:
            was_not_open = current_state != self.OPEN
            if was_not_open:
                self._total_trips += 1
                # D108: Exponential backoff — double timeout on re-trip
                # (HALF_OPEN probe failed → back off more aggressively)
                if self._total_trips > 1:
                    self._current_reset_timeout = min(
                        self._current_reset_timeout * 2,
                        self.MAX_RESET_TIMEOUT,
                    )
                _reason_suffix = (
                    f" last_failure={getattr(self, '_last_failure_reason', '<none>')}"
                )
                logger.error(
                    "D87: Circuit breaker '%s' TRIPPED (%s -> open). "
                    "%d consecutive failures. Rejecting calls for %ds. "
                    "Total trips: %d.%s",
                    self.name, current_state, self._fail_count,
                    int(self._current_reset_timeout),
                    self._total_trips,
                    _reason_suffix,
                )
                # doc 272 A2: durable incident at trip time (the bus's 5-min
                # dedup_key absorbs flap storms like the 65-trip D279 day).
                # WARN, not CRITICAL: breakers self-heal; the pager pages
                # WARN only when run with --warn. NEVER raises.
                try:
                    from src.ops.incident_bus import emit_incident
                    emit_incident(
                        "CIRCUIT_BREAKER_TRIP", "WARN",
                        context={
                            "breaker": self.name,
                            "consecutive_failures": self._fail_count,
                            "reset_timeout_s": int(self._current_reset_timeout),
                            "total_trips": self._total_trips,
                            "last_failure": getattr(
                                self, "_last_failure_reason", None),
                        },
                        suggested=[
                            "check provider status (Alpaca/Together/news) "
                            "before assuming a code regression",
                        ],
                        dedup_key=f"cb_trip_{self.name}",
                    )
                except Exception as _ie:  # telemetry must never break calls
                    logger.debug(
                        "doc272: CB trip incident emit failed (non-fatal): %s",
                        _ie,
                    )
            self._state = self.OPEN

    async def __aenter__(self) -> AsyncCircuitBreaker:
        """Async context manager — checks breaker on entry."""
        self.check()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Record success or failure based on exception."""
        if exc_type is None:
            self.record_success()
        else:
            # D279: forward the exception so the trip log includes its detail
            self.record_failure(exc_val)
        return False  # Don't suppress exceptions


# ── Per-service circuit breakers ──────────────────────────────────────

alpaca_breaker = AsyncCircuitBreaker(
    name="alpaca_rest",
    fail_max=3,
    reset_timeout=30.0,
)

llm_breaker = AsyncCircuitBreaker(
    name="llm_provider",
    fail_max=5,
    reset_timeout=60.0,
    # D87: Raised from fail_max=2, reset=120s. With parallel evaluation
    # (3 candidates × 6 agents = 18 concurrent calls), 2 failures was too
    # hair-trigger — a single slow batch tripped the breaker and blocked
    # ALL remaining agents. 5 failures = genuine outage, not just slowness.
    # Reset 120→60s: faster recovery when provider stabilizes.
)

news_breaker = AsyncCircuitBreaker(
    name="news_api",
    fail_max=5,
    reset_timeout=60.0,
)


def get_breaker_status() -> dict[str, str]:
    """Get current state of all circuit breakers for dashboard."""
    return {
        "alpaca_rest": alpaca_breaker.state,
        "llm_provider": llm_breaker.state,
        "news_api": news_breaker.state,
    }


def check_system_health() -> tuple[bool, list[str]]:
    """D108: Check if critical circuit breakers allow trading.

    Only the Alpaca REST breaker is considered critical — if the broker is
    unreachable, no new entries should be attempted. LLM and news breakers
    being open triggers graceful degradation (handled elsewhere).

    Returns:
        Tuple of (healthy: bool, problems: list[str]).
    """
    problems: list[str] = []
    if alpaca_breaker.state == AsyncCircuitBreaker.OPEN:
        problems.append(
            f"alpaca_rest: OPEN — broker unavailable "
            f"(retry in {alpaca_breaker.reset_timeout:.0f}s)"
        )
    return (len(problems) == 0, problems)
