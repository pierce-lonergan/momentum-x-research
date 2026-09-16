"""D298 (2026-05-19) -- EOD failsafe exception logging must include type.

Pin the production diagnostic gap from 2026-05-15:
  ``D238 EOD_RECONCILIATION_DELTA DEGRADED: get_orders raised () — recon skipped``

Empty parens. The underlying httpx exception had no message (``str(e) == ""``),
so the old ``f"({e})"`` format collapsed to ``"()"`` and operators were left
with no idea WHAT failed. Was it a timeout? A 500? A network blip?

D298 fix: ``_fmt_exc(e)`` always prepends ``type(e).__name__`` so even
empty-message exceptions read as ``raised (ReadTimeout)`` -- actionable.

This file covers:
  - The formatter's per-shape behaviour (empty / non-empty / nested cause)
  - Each of the 4 sites in ``eod_failsafes.py`` that previously had the bug
  - Backwards-compat: when the exception DOES have a message, format
    stays identical to pre-D298 (``ExcType: message`` is a superset)
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.monitoring.eod_failsafes import (
    _fmt_exc,
    run_eod_safety_cancel,
    run_eod_force_close,
    run_eod_broker_truth_recon,
)


# ═══════════════════════════════════════════════════════════════
# _fmt_exc unit tests
# ═══════════════════════════════════════════════════════════════


def test_fmt_exc_empty_exception_shows_just_the_type():
    """The Friday 5/15 case: httpx.ReadTimeout with no args."""
    import httpx
    assert _fmt_exc(httpx.ReadTimeout("")) == "ReadTimeout"
    assert _fmt_exc(RuntimeError()) == "RuntimeError"
    assert _fmt_exc(Exception()) == "Exception"


def test_fmt_exc_non_empty_exception_includes_message():
    assert _fmt_exc(ValueError("foo")) == "ValueError: foo"
    assert _fmt_exc(RuntimeError("not found")) == "RuntimeError: not found"


def test_fmt_exc_strips_whitespace_only_messages():
    """An exception with only whitespace in args should be treated as empty."""
    assert _fmt_exc(RuntimeError("   ")) == "RuntimeError"
    assert _fmt_exc(RuntimeError("\n\t")) == "RuntimeError"


def test_fmt_exc_handles_multiline_messages():
    """Multi-line messages should NOT lose the type prefix."""
    out = _fmt_exc(RuntimeError("line 1\nline 2"))
    assert out.startswith("RuntimeError:")
    assert "line 1" in out


def test_fmt_exc_handles_custom_exception_subclass():
    class _CustomExc(Exception):
        pass
    assert _fmt_exc(_CustomExc()) == "_CustomExc"
    assert _fmt_exc(_CustomExc("oops")) == "_CustomExc: oops"


def test_fmt_exc_handles_httpx_subtypes():
    import httpx
    assert _fmt_exc(httpx.ConnectError("")) == "ConnectError"
    assert _fmt_exc(httpx.PoolTimeout("")) == "PoolTimeout"
    e = httpx.HTTPStatusError("502", request=None, response=None)
    assert _fmt_exc(e).startswith("HTTPStatusError")
    assert "502" in _fmt_exc(e)


def test_fmt_exc_with_baseexception_subclass():
    """KeyboardInterrupt etc. (subclass BaseException, not Exception) should
    still format. Defensive — we should never accidentally swallow these,
    but the formatter itself must not crash."""
    class _BE(BaseException): pass
    assert _fmt_exc(_BE("k")) == "_BE: k"
    assert _fmt_exc(_BE()) == "_BE"


# ═══════════════════════════════════════════════════════════════
# Site 1: D241 get_orders failure
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d241_logs_exception_type_when_get_orders_raises(caplog):
    """Empty exception must still produce a typed log line."""
    import httpx
    client = MagicMock()
    client.get_orders = AsyncMock(side_effect=httpx.ReadTimeout(""))
    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
        result = await run_eod_safety_cancel(client=client)
    # Pre-D298: "raised () — skipping..."  /  Post-D298: "raised (ReadTimeout) — ..."
    msgs = [r.message for r in caplog.records if "D241" in r.message]
    assert any("ReadTimeout" in m for m in msgs), (
        f"D241 log line lost the exception type. Got: {msgs}"
    )
    assert any("raised ()" not in m for m in msgs), (
        "empty parens must no longer appear"
    )
    assert result["broker_reachable"] is False


@pytest.mark.asyncio
async def test_d241_logs_typed_message_when_exception_has_text(caplog):
    """When the exception does have a message, log shows 'Type: message'."""
    client = MagicMock()
    client.get_orders = AsyncMock(side_effect=ValueError("auth failed"))
    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
        await run_eod_safety_cancel(client=client)
    msgs = [r.message for r in caplog.records if "D241" in r.message]
    assert any("ValueError: auth failed" in m for m in msgs), (
        f"D241 log line should include both type and message. Got: {msgs}"
    )


@pytest.mark.asyncio
async def test_d241_orphan_cancel_failure_logs_type(caplog):
    """The per-order cancel failure path also uses _fmt_exc."""
    import httpx
    client = MagicMock()
    client.get_orders = AsyncMock(return_value=[
        {"id": "orphan-1", "symbol": "XYZ", "side": "buy", "qty": "1", "status": "new"},
    ])
    client.cancel_order = AsyncMock(side_effect=httpx.ConnectError(""))
    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
        await run_eod_safety_cancel(client=client, expected_order_ids=set())
    msgs = [r.message for r in caplog.records
            if "D241" in r.message and "FAILED" in r.message]
    assert any("ConnectError" in m for m in msgs)


# ═══════════════════════════════════════════════════════════════
# Site 2: D242 get_positions failure
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d242_logs_exception_type_when_get_positions_raises(caplog):
    import httpx
    client = MagicMock()
    client.get_positions = AsyncMock(side_effect=httpx.PoolTimeout(""))
    pm = MagicMock()
    pm.open_positions = []
    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
        result = await run_eod_force_close(client=client, position_manager=pm)
    msgs = [r.message for r in caplog.records if "D242" in r.message]
    assert any("PoolTimeout" in m for m in msgs)
    assert result["broker_reachable"] is False


# ═══════════════════════════════════════════════════════════════
# Site 3: D242 close_position (ghost-close) failure
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d242_ghost_close_failure_logs_type(caplog):
    """The per-ghost close failure path uses _fmt_exc."""
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[
        {"symbol": "GHOST", "qty": "100", "unrealized_pl": "-50.00"},
    ])
    client.close_position = AsyncMock(side_effect=RuntimeError())
    pm = MagicMock()
    pm.open_positions = []  # tracker doesn't know about GHOST
    with caplog.at_level(logging.ERROR, logger="src.monitoring.eod_failsafes"):
        result = await run_eod_force_close(client=client, position_manager=pm)
    err_msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("RuntimeError" in m and "GHOST" in m for m in err_msgs), (
        f"D242 ghost close failure should include exception type. Got: {err_msgs}"
    )
    assert result["force_closes_failed"] == 1


# ═══════════════════════════════════════════════════════════════
# Site 4: D238 get_orders failure (the original Friday 5/15 site)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d238_logs_exception_type_on_get_orders_failure(caplog):
    """The headline regression: the Friday 5/15 'raised ()' bug."""
    import httpx
    client = MagicMock()
    client.get_orders = AsyncMock(side_effect=httpx.ReadTimeout(""))
    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
        result = await run_eod_broker_truth_recon(
            client=client, journal_pnl=0.0, per_ticker_journal={},
        )
    msgs = [r.message for r in caplog.records if "D238" in r.message]
    assert any("ReadTimeout" in m for m in msgs), (
        f"The Friday 5/15 regression: D238 log should now show "
        f"'(ReadTimeout)' instead of '()'. Got: {msgs}"
    )
    # And the explicit empty-parens pattern must not appear
    for m in msgs:
        assert "raised ()" not in m, (
            f"empty parens regression: {m!r}"
        )
    assert result["broker_reachable"] is False


@pytest.mark.asyncio
async def test_d238_logs_typed_message_when_exception_has_text(caplog):
    """Pre-D298 behaviour for exceptions with messages is preserved
    (just wrapped with a type prefix now)."""
    client = MagicMock()
    client.get_orders = AsyncMock(side_effect=ValueError("invalid token"))
    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
        await run_eod_broker_truth_recon(
            client=client, journal_pnl=0.0, per_ticker_journal={},
        )
    msgs = [r.message for r in caplog.records if "D238" in r.message]
    # Must include BOTH type and message (no message info lost)
    assert any("ValueError" in m and "invalid token" in m for m in msgs)


# ═══════════════════════════════════════════════════════════════
# Coverage guard: every except site uses _fmt_exc
# ═══════════════════════════════════════════════════════════════


def test_no_bare_except_e_format_string_remaining_in_eod_failsafes():
    """Static check: the pre-D298 anti-pattern ``f"... ({e})..."`` (where
    ``e`` is the bare exception) must not appear in eod_failsafes.py.
    All sites must route through ``_fmt_exc(e)`` so empty-message
    exceptions never lose their type."""
    from pathlib import Path
    import re
    src = Path("src/monitoring/eod_failsafes.py").read_text(encoding="utf-8")

    # Match the specific defective pattern: an `except ... as e:` block
    # whose body passes raw `e` (not `_fmt_exc(e)`) into a logger call.
    # We look for `, e,` and `, e)` as the trailing positional arg of
    # a logger call. False positives possible if some other variable
    # named `e` is used in logging, but in this file it's used only
    # as the standard except-variable name.
    raw_e_in_logger = re.findall(
        r"logger\.(?:warning|error|info|debug)\([^)]*,\s+e[,)]",
        src,
    )
    # Filter out the lines that DO use _fmt_exc — those have `_fmt_exc(e)`
    # not bare `e` as the arg.
    bare_e_calls = [m for m in raw_e_in_logger if "_fmt_exc" not in m]
    assert bare_e_calls == [], (
        "D298 violation: eod_failsafes.py still has logger calls passing "
        f"bare `e` instead of `_fmt_exc(e)`. Offending matches:\n  "
        + "\n  ".join(bare_e_calls)
    )


def test_fmt_exc_is_exported_from_eod_failsafes():
    """Other modules should be able to import this helper if they hit
    the same empty-exception pattern."""
    from src.monitoring import eod_failsafes
    assert hasattr(eod_failsafes, "_fmt_exc")
    assert callable(eod_failsafes._fmt_exc)


# ═══════════════════════════════════════════════════════════════
# Recovery: when the exception clears, normal operation resumes
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d238_recovers_cleanly_when_exception_clears(caplog):
    """After a degraded run, the next call (no exception) should produce
    a normal-looking result, NOT carry over the degraded state."""
    client = MagicMock()
    # First call: raises empty exception
    import httpx
    client.get_orders = AsyncMock(side_effect=httpx.ReadTimeout(""))
    result1 = await run_eod_broker_truth_recon(
        client=client, journal_pnl=0.0, per_ticker_journal={},
    )
    assert result1["broker_reachable"] is False
    # Second call: same client, but returns normally now
    client.get_orders = AsyncMock(return_value=[])
    result2 = await run_eod_broker_truth_recon(
        client=client, journal_pnl=0.0, per_ticker_journal={},
    )
    assert result2["broker_reachable"] is True
    assert result2["delta_usd"] == 0.0
