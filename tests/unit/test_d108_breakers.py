"""
Tests for D108 WS1: Circuit Breaker Hardening.

Covers:
  - Exponential backoff on reset timeout
  - Backoff cap at MAX_RESET_TIMEOUT
  - Backoff reset on recovery
  - Aggregate system health check
"""

from __future__ import annotations

import time

import pytest

from src.utils.circuit_breaker import (
    AsyncCircuitBreaker,
    alpaca_breaker,
    check_system_health,
)


class TestExponentialBackoff:
    """D108: Tests for exponential backoff on circuit breaker reset timeout."""

    def test_backoff_doubles_on_repeated_trips(self):
        """Trip breaker multiple times → timeout doubles each time."""
        breaker = AsyncCircuitBreaker(name="test", fail_max=1, reset_timeout=10.0)

        # Trip 1: timeout stays at base (first trip doesn't double)
        breaker.record_failure()
        assert breaker.state == AsyncCircuitBreaker.OPEN
        assert breaker.reset_timeout == 10.0

        # Simulate timeout expiry → HALF_OPEN, then failed probe → trip 2
        breaker._last_failure_time = time.monotonic() - 11.0
        assert breaker.state == AsyncCircuitBreaker.HALF_OPEN
        breaker._fail_count = 0
        breaker.record_failure()
        assert breaker.reset_timeout == 20.0

        # Trip 3
        breaker._last_failure_time = time.monotonic() - 21.0
        breaker._fail_count = 0
        breaker.record_failure()
        assert breaker.reset_timeout == 40.0

    def test_backoff_caps_at_max(self):
        """Timeout never exceeds MAX_RESET_TIMEOUT (300s)."""
        breaker = AsyncCircuitBreaker(name="test_cap", fail_max=1, reset_timeout=200.0)

        # Trip 1 at 200s
        breaker.record_failure()
        assert breaker.reset_timeout == 200.0

        # Trip 2 → would be 400s but capped at 300s
        breaker._last_failure_time = time.monotonic() - 201.0
        breaker._fail_count = 0
        breaker.record_failure()
        assert breaker.reset_timeout == 300.0

        # Trip 3 → stays at 300s
        breaker._last_failure_time = time.monotonic() - 301.0
        breaker._fail_count = 0
        breaker.record_failure()
        assert breaker.reset_timeout == 300.0

    def test_backoff_resets_on_recovery(self):
        """Recovery resets timeout to base value."""
        breaker = AsyncCircuitBreaker(name="test_reset", fail_max=1, reset_timeout=10.0)

        # Trip twice to get elevated timeout
        breaker.record_failure()  # Trip 1
        breaker._last_failure_time = time.monotonic() - 11.0
        breaker._fail_count = 0
        breaker.record_failure()  # Trip 2 → timeout doubles to 20
        assert breaker.reset_timeout == 20.0

        # Recovery should reset to base
        breaker.record_success()
        assert breaker.state == AsyncCircuitBreaker.CLOSED
        assert breaker.reset_timeout == 10.0


class TestSystemHealthGate:
    """D108: Tests for aggregate system health check."""

    def test_healthy_when_all_closed(self):
        """All breakers closed → system is healthy."""
        # Save original state
        original_state = alpaca_breaker._state
        original_fail_count = alpaca_breaker._fail_count

        alpaca_breaker._state = AsyncCircuitBreaker.CLOSED
        alpaca_breaker._fail_count = 0

        healthy, problems = check_system_health()
        assert healthy is True
        assert problems == []

        # Restore
        alpaca_breaker._state = original_state
        alpaca_breaker._fail_count = original_fail_count

    def test_unhealthy_when_alpaca_open(self):
        """Alpaca breaker OPEN → system is unhealthy."""
        original_state = alpaca_breaker._state
        original_fail_count = alpaca_breaker._fail_count
        original_time = alpaca_breaker._last_failure_time

        alpaca_breaker._state = AsyncCircuitBreaker.OPEN
        alpaca_breaker._fail_count = alpaca_breaker.fail_max
        alpaca_breaker._last_failure_time = time.monotonic()

        healthy, problems = check_system_health()
        assert healthy is False
        assert len(problems) == 1
        assert "alpaca_rest" in problems[0]

        # Restore
        alpaca_breaker._state = original_state
        alpaca_breaker._fail_count = original_fail_count
        alpaca_breaker._last_failure_time = original_time
