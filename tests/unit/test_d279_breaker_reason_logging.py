"""D279 (2026-05-05) — circuit-breaker trip log surfaces last-failure reason.

Pin Tuesday 2026-05-05 production observation: the llm_provider breaker
tripped 65× across the morning with log lines like

    D87: Circuit breaker 'llm_provider' TRIPPED (closed -> open).
    5 consecutive failures. Rejecting calls for 60s. Total trips: 1

— with NO indication of WHAT was failing. Operators had to scroll through
LiteLLM INFO lines to figure out it was Together.ai HTTP 500s.

Post-fix: `record_failure(reason)` accepts an optional exception/string;
the trip log line now ends with `last_failure=...`. Backward-compatible
(reason defaults to None — old callers that pass nothing still work and
get the legacy trip line with `last_failure=<none>`).
"""
from __future__ import annotations

import logging

import pytest

from src.utils.circuit_breaker import AsyncCircuitBreaker


def test_record_failure_accepts_optional_reason():
    """Backward-compatible signature: existing callers still work."""
    breaker = AsyncCircuitBreaker(name="test", fail_max=2, reset_timeout=5.0)
    breaker.record_failure()  # No-arg, legacy
    breaker.record_failure("just a string")
    assert breaker.state == AsyncCircuitBreaker.OPEN


def test_trip_log_includes_exception_repr(caplog):
    """When an exception is passed, the trip log includes its repr()."""
    breaker = AsyncCircuitBreaker(name="llm_test", fail_max=2, reset_timeout=5.0)
    err = RuntimeError("HTTP 500 from together_ai")

    with caplog.at_level(logging.ERROR):
        breaker.record_failure(err)
        breaker.record_failure(err)

    trip_messages = [r.message for r in caplog.records if "TRIPPED" in r.message]
    assert len(trip_messages) == 1
    msg = trip_messages[0]
    assert "last_failure=" in msg, f"trip log missing last_failure suffix: {msg}"
    assert "RuntimeError" in msg, f"exception class missing from trip log: {msg}"
    assert "HTTP 500 from together_ai" in msg, (
        f"exception message missing from trip log: {msg}"
    )


def test_trip_log_includes_string_reason(caplog):
    """Strings are passed through verbatim (truncated to 240 chars)."""
    breaker = AsyncCircuitBreaker(name="alpaca_test", fail_max=1, reset_timeout=5.0)

    with caplog.at_level(logging.ERROR):
        breaker.record_failure("HTTP 422 url=/v2/orders body={'code': 40010001}")

    trips = [r.message for r in caplog.records if "TRIPPED" in r.message]
    assert len(trips) == 1
    assert "HTTP 422" in trips[0]
    assert "/v2/orders" in trips[0]


def test_trip_log_truncates_very_long_reason(caplog):
    """Reason is capped at 240 chars to prevent log bloat under repeated failures."""
    breaker = AsyncCircuitBreaker(name="bulk_test", fail_max=1, reset_timeout=5.0)
    huge = "X" * 10_000

    with caplog.at_level(logging.ERROR):
        breaker.record_failure(huge)

    trips = [r.message for r in caplog.records if "TRIPPED" in r.message]
    assert len(trips) == 1
    # Capped: should NOT contain the full 10k Xs
    assert trips[0].count("X") <= 250, (
        f"reason was not truncated: contains {trips[0].count('X')} X's"
    )


def test_trip_log_legacy_no_reason_renders_none_marker(caplog):
    """Legacy callers (no reason) still trip cleanly; log shows <none>."""
    breaker = AsyncCircuitBreaker(name="legacy_test", fail_max=1, reset_timeout=5.0)

    with caplog.at_level(logging.ERROR):
        breaker.record_failure()

    trips = [r.message for r in caplog.records if "TRIPPED" in r.message]
    assert len(trips) == 1
    assert "last_failure=<none>" in trips[0]


@pytest.mark.asyncio
async def test_aexit_forwards_exception_to_record_failure(caplog):
    """Async context-manager pattern: `async with breaker:` should pass
    the raised exception into record_failure so it surfaces in the trip log."""
    breaker = AsyncCircuitBreaker(name="ctx_test", fail_max=1, reset_timeout=5.0)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("upstream-API-down")

    trips = [r.message for r in caplog.records if "TRIPPED" in r.message]
    assert len(trips) == 1
    assert "ValueError" in trips[0]
    assert "upstream-API-down" in trips[0]
