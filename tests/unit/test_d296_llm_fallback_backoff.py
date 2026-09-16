"""D296 (2026-05-13) — Jittered backoff between LLM fallback retries.

Pin the production LLM circuit-breaker storm from 2026-05-13:
- 21 D87 trips between 09:20-10:32 ET (Together.ai RateLimit + Timeout)
- knock-on: every triggered debate (35) skipped for budget,
  news_agent defaulted NEUTRAL on 169/205 candidates,
  fundamental_agent NEUTRAL on 130/211, 0 BUY votes from any debate,
  bot effectively muted for the morning decision window.
- root cause: when primary LLM call failed, fallback fired IMMEDIATELY
  (zero backoff). Concurrent agent tasks all retried in lockstep,
  multiplying rate-limit pressure.

Post-D296: ``_llm_fallback_backoff_ms(err)`` returns a jittered sleep
before each fallback attempt. RateLimitError → ~1500ms base. Other
errors (Timeout, ServiceUnavailable) → ~200ms base. Multiplier
uniform [0.5, 1.5] so concurrent tasks de-stagger.
"""
from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import pytest


# ── Helper accessor ─────────────────────────────────────────────────


def _backoff(err: Exception) -> int:
    from src.agents.base import _llm_fallback_backoff_ms
    return _llm_fallback_backoff_ms(err)


# ── Rate-limit detection variants ───────────────────────────────────


@pytest.mark.parametrize(
    "err_text",
    [
        "litellm.RateLimitError: Together_aiException - too many requests",
        "RateLimitError",
        "rate_limit exceeded",
        "HTTP 429 too many requests",
        "Too Many Requests",
    ],
)
def test_rate_limit_errors_get_long_backoff(err_text):
    """Any error string matching rate-limit keywords gets the longer
    backoff (≥750ms = 0.5 × 1500ms minimum jitter)."""
    out = _backoff(RuntimeError(err_text))
    assert out >= 750, (
        f"rate-limit '{err_text}' got backoff {out}ms, expected ≥750ms"
    )
    assert out <= 2250, (
        f"rate-limit '{err_text}' got backoff {out}ms, expected ≤2250ms (1.5×1500)"
    )


# ── Non-rate-limit errors (timeouts, 5xx) get short backoff ─────────


@pytest.mark.parametrize(
    "err_text",
    [
        "litellm.Timeout: Request timed out",
        "ServiceUnavailableError: Together_aiException",
        "ConnectionError",
        "InternalServerError 500",
        "empty response from model",
    ],
)
def test_non_rate_limit_errors_get_short_backoff(err_text):
    out = _backoff(RuntimeError(err_text))
    # Short range: 0.5 × 200 = 100ms min, 1.5 × 200 = 300ms max
    assert out >= 100, (
        f"transient '{err_text}' got {out}ms, expected ≥100ms"
    )
    assert out <= 300, (
        f"transient '{err_text}' got {out}ms, expected ≤300ms"
    )


# ── Jitter spreads concurrent retries ───────────────────────────────


def test_jitter_yields_distinct_values_for_concurrent_calls():
    """If 10 concurrent agents all retry with the same error type, the
    jitter must spread them out so they don't all land in the same
    rate-limit window. We assert that across 100 samples, the standard
    deviation is meaningfully > 0."""
    err = RuntimeError("RateLimitError: too many requests")
    samples = [_backoff(err) for _ in range(100)]
    distinct = len(set(samples))
    assert distinct >= 30, (
        f"jitter produced only {distinct} distinct values across 100 samples — "
        "expected ≥30 (would mean retries cluster too tightly)"
    )


# ── Bounds enforced (never zero, never absurdly large) ──────────────


def test_backoff_is_always_positive():
    """A zero/negative backoff would defeat the purpose."""
    for err in [
        RuntimeError("rate limit"),
        RuntimeError("timeout"),
        RuntimeError(""),
        ValueError("anything"),
    ]:
        out = _backoff(err)
        assert out > 0, f"backoff for {err!r} was {out} (must be > 0)"


def test_backoff_is_bounded_for_any_error():
    """Catastrophe-resistance: even pathological errors get sensible bounds."""
    for err in [
        RuntimeError("totally unknown error type"),
        Exception("rate_limit") ,
        ValueError("RateLimitError"),
    ]:
        out = _backoff(err)
        assert out <= 2250, f"backoff {out}ms for {err!r} exceeds upper bound"


# ── Determinism with fixed seed (regression: jitter is reproducible) ─


def test_jitter_reproducible_with_seed():
    """random.uniform must be the source of the jitter — seed and
    re-seed should give the same result."""
    err = RuntimeError("RateLimitError")
    random.seed(42)
    a = _backoff(err)
    random.seed(42)
    b = _backoff(err)
    assert a == b, (
        f"backoff is non-deterministic given a seeded RNG: {a} vs {b}"
    )


# ── Integration sanity: imported symbols match the public contract ──


def test_module_exposes_helper_and_constants():
    """The constants are tunable knobs; the helper is the public API."""
    from src.agents import base
    assert hasattr(base, "_llm_fallback_backoff_ms")
    assert hasattr(base, "_LLM_BACKOFF_BASE_MS_RATE_LIMIT")
    assert hasattr(base, "_LLM_BACKOFF_BASE_MS_OTHER")
    # Sanity: rate-limit base must be larger than the other base
    assert base._LLM_BACKOFF_BASE_MS_RATE_LIMIT > base._LLM_BACKOFF_BASE_MS_OTHER
