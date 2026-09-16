"""D279 (2026-05-05) — PositionManager.get_position() regression guard.

Pin Tuesday 2026-05-05 09:30:34 ET production failure:

    D91: Closing 3 OVERNIGHT positions at market open: ['MRAM', 'VLN', 'WNW']
    D91: Failed to close overnight position MRAM: 'PositionManager' object
         has no attribute 'get_position'
    D91: Failed to close overnight position VLN:  'PositionManager' object
         has no attribute 'get_position'
    D91: Failed to close overnight position WNW:  'PositionManager' object
         has no attribute 'get_position'

`src/execution/bridge.py:_close_overnight_position` (line 646) called
``position_manager.get_position(ticker)`` but `PositionManager` only had
`has_position`, `add_position`, `remove_position`, `open_positions` — no
`get_position`. The AttributeError was caught by the try/except in main.py,
so it surfaced as an ERROR log line but did NOT crash the bot. All three
overnight positions only closed because the D165 tranche-exit ladder
fired independently at 10:00:51 ET — D91, the canonical safety mechanism
for overnight-flat-by-open, was silently broken.

This test suite locks in the API:

  1. `PositionManager.get_position(ticker)` returns the ManagedPosition.
  2. Returns None for unknown tickers (matches dict.get semantics).
  3. End-to-end: `_close_overnight_position` runs the full STEP 1/2/3
     trace without raising AttributeError.
  4. Public-API surface check: `get_position` is a method, not an
     attribute or property — guards against accidental refactors.

If `get_position` is renamed or removed, every test here fails loudly,
not silently.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.position_manager import ManagedPosition, PositionManager


def _build_pm(starting_equity: float = 100_000.0) -> PositionManager:
    cfg = MagicMock()
    cfg.daily_loss_limit_pct = 0.10
    cfg.max_positions = 8
    cfg.stop_loss_pct = 0.055
    cfg.max_position_size_pct = 0.15
    cfg.tier1_max_concurrent = 2
    cfg.tier2_max_concurrent = 4
    cfg.tier3_max_concurrent = 6
    cfg.tier4_max_concurrent = 8
    return PositionManager(config=cfg, starting_equity=starting_equity)


def _broker_position(symbol: str = "MRAM") -> dict:
    return {
        "symbol": symbol, "qty": "100", "side": "long",
        "avg_entry_price": "5.00", "current_price": "5.10",
    }


# ── 1. Direct unit tests ─────────────────────────────────────────────


def test_get_position_returns_managed_position_for_tracked_ticker():
    """The exact API contract used by D91 STEP 1 (read stop_order_id +
    remaining_qty before submitting the market sell)."""
    pm = _build_pm()
    pm.sync_from_broker([_broker_position("MRAM")])
    pos = pm.get_position("MRAM")
    assert pos is not None, "get_position must return ManagedPosition for tracked ticker"
    assert isinstance(pos, ManagedPosition)
    assert pos.ticker == "MRAM"
    # The two attributes _close_overnight_position reads via getattr:
    assert hasattr(pos, "stop_order_id")
    assert hasattr(pos, "remaining_qty")


def test_get_position_returns_none_for_unknown_ticker():
    """Mirrors dict.get semantics. The bridge code branches on `pos is None`
    — must not raise KeyError."""
    pm = _build_pm()
    assert pm.get_position("NEVER_SEEN") is None


def test_get_position_returns_none_after_remove_position():
    """Idempotency: a closed-and-removed position must not be retrievable."""
    pm = _build_pm()
    pm.sync_from_broker([_broker_position("VLN")])
    assert pm.get_position("VLN") is not None
    pm.remove_position("VLN")
    assert pm.get_position("VLN") is None
    assert pm.has_position("VLN") is False


# ── 2. Public-API surface check ──────────────────────────────────────


def test_get_position_is_a_callable_method():
    """Defends against accidental refactors that turn it into a property
    or an attribute. The bridge code calls `position_manager.get_position(t)`
    — if it becomes a property, the call would raise TypeError."""
    pm = _build_pm()
    attr = type(pm).__dict__.get("get_position")
    assert attr is not None, "get_position must exist on PositionManager class"
    assert inspect.isfunction(attr), (
        "get_position must be a method, not a property/attribute. "
        "The bridge code calls it as `position_manager.get_position(t)`."
    )


def test_position_manager_api_surface_includes_d91_dependencies():
    """D91 close-overnight path requires this exact API surface. Renames
    or removals here MUST be caught at PR time. `open_positions` is a
    property; everything else here is a method."""
    required_methods = {"has_position", "get_position", "remove_position",
                          "add_position", "sync_from_broker"}
    actual_methods = {n for n, m in inspect.getmembers(PositionManager,
                                                          predicate=inspect.isfunction)}
    missing = required_methods - actual_methods
    assert not missing, f"PositionManager missing D91-required methods: {missing}"
    # open_positions is a @property, not a method
    assert isinstance(
        inspect.getattr_static(PositionManager, "open_positions"), property
    ), "open_positions must remain a @property (D91 detection enumerates over it)"


# ── 3. End-to-end behavioral test (the actual D91 outage scenario) ──


def _async(coro):
    """Run an async coroutine in tests without pytest-asyncio."""
    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_d91_close_overnight_position_does_not_attribute_error():
    """End-to-end pin of the Tuesday 2026-05-05 09:30:34 outage.

    Previously: bridge._close_overnight_position called
    `position_manager.get_position(ticker)` → AttributeError →
    caught in main.py:2211 → 'D91: Failed to close overnight position'
    logged → operation aborted before the market-sell ever fired.

    Post-fix: get_position resolves the ManagedPosition, STEP 2 fires
    client.close_position, STEP 3 removes it from the tracker.
    """
    from src.execution.bridge import _close_overnight_position

    pm = _build_pm()
    pm.sync_from_broker([_broker_position("WNW")])
    assert pm.has_position("WNW")

    # Mock client: cancel_order + close_position both succeed (paper-trade
    # baseline). _close_overnight_position should make it through every step.
    client = MagicMock()
    client.cancel_order = AsyncMock(return_value=None)
    client.close_position = AsyncMock(return_value={"status": "accepted"})

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker="WNW",
    )

    assert ok is True, "STEP 2 should report success when close_position returns"
    assert pm.has_position("WNW") is False, (
        "STEP 3 must remove WNW from the internal tracker"
    )
    client.close_position.assert_awaited_once_with("WNW")


@pytest.mark.asyncio
async def test_d91_close_with_stop_order_id_cancels_stop_first():
    """STEP 1: when the position has a stop_order_id, that stop must be
    canceled BEFORE the market sell — preserving the established close
    sequence regardless of the get_position fix."""
    from src.execution.bridge import _close_overnight_position

    pm = _build_pm()
    pm.sync_from_broker([_broker_position("MRAM")])
    # Inject a stop_order_id so STEP 1 has work to do
    pm._positions["MRAM"].stop_order_id = "stop-oid-12345"

    call_order: list[str] = []
    client = MagicMock()
    async def _cancel(oid):
        call_order.append(f"cancel:{oid}")
    async def _close(t):
        call_order.append(f"close:{t}")
        return {"status": "accepted"}
    client.cancel_order = _cancel
    client.close_position = _close

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker="MRAM",
    )

    assert ok is True
    assert call_order == ["cancel:stop-oid-12345", "close:MRAM"], (
        f"STEP 1 (cancel) must precede STEP 2 (close), got: {call_order}"
    )
    assert pm.has_position("MRAM") is False


@pytest.mark.asyncio
async def test_d91_close_returns_false_when_position_already_gone():
    """If the position vanished between detection and close (e.g. a stop
    just filled), get_position returns None and the function exits cleanly."""
    from src.execution.bridge import _close_overnight_position

    pm = _build_pm()  # No positions added
    client = MagicMock()
    client.cancel_order = AsyncMock()
    client.close_position = AsyncMock()

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker="GONE",
    )
    assert ok is False
    client.cancel_order.assert_not_awaited()
    client.close_position.assert_not_awaited()
