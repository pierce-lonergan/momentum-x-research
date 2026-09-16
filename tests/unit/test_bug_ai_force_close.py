"""Bug AI tests: cancel-blocking-stops-then-close coordination.

Pins today's LIDR deadlock scenario as a regression guard:
  1. operator-submitted protective stop reserves all shares
  2. bridge force-close attempt → 403 'insufficient qty available'
  3. with cancel_blocking_stops_first=True → bridge cancels the stop,
     retries, succeeds → 5+ failed close attempts on Mon 2026-04-27
     would not have happened
  4. and the SAFETY contract: if close ultimately fails, re-arm the
     protective stop from snapshot before returning to caller
     (D249 STOP_REARM)

See docs/research-log/48_bug_ai_force_close.md.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.bridge import (
    attempt_close_with_status_check,
    _cancel_blocking_sell_orders,
    _rearm_protective_stop_from_snapshot,
)


# ── Test doubles ────────────────────────────────────────────────────


class _Block403Error(Exception):
    """Mimics httpx.HTTPStatusError body for the LIDR scenario."""

    def __str__(self) -> str:
        return (
            "Client error '403 Forbidden' — body={'available': '0', "
            "'code': 40310000, 'existing_qty': '5264', 'held_for_orders': "
            "'5264', 'message': 'insufficient qty available for order "
            "(requested: 5264, available: 0)', 'symbol': 'LIDR'}"
        )


def _mock_client_lidr_deadlock(
    *,
    initial_close_blocked: bool = True,
    close_succeeds_after_cancel: bool = True,
    blocking_stops: list[dict[str, Any]] | None = None,
):
    """Build a mock client that mimics the Mon 2026-04-27 LIDR scenario.

    - get_orders returns the operator stop @ $2.10
    - close_position raises 403 'insufficient qty' on first call
    - cancel_order succeeds (broker happily cancels)
    - close_position succeeds on second call (after cancel)
    """
    client = MagicMock()
    if blocking_stops is None:
        blocking_stops = [
            {
                "id": "859be64d-718e-4f6d-a09a-932d03a79916",
                "symbol": "LIDR",
                "side": "sell",
                "qty": "5264",
                "type": "stop",
                "stop_price": "2.10",
                "limit_price": None,
                "client_order_id": "manual_bug_ag_protect_1777292538",
                "status": "new",
            },
        ]
    client.get_orders = AsyncMock(return_value=blocking_stops)
    client.cancel_order = AsyncMock(return_value={"status": "canceled"})
    client.submit_order = AsyncMock(return_value={
        "id": "rearm-oid", "status": "accepted",
        "stop_price": "2.10", "qty": "5264",
    })
    # doc 180: the D249 re-arm now uses the dedicated submit_stop_order (the real
    # AlpacaDataClient signature is submit_order(symbol, qty, side, ...), so the
    # old submit_order(payload-dict) call raised in production and left the
    # position naked — the permissive AsyncMock had masked the mismatch).
    client.submit_stop_order = AsyncMock(return_value={
        "id": "rearm-oid", "status": "accepted",
        "stop_price": "2.10", "qty": "5264",
    })

    call_count = {"n": 0}

    async def close_position(symbol):
        call_count["n"] += 1
        if call_count["n"] == 1 and initial_close_blocked:
            raise _Block403Error()
        if not close_succeeds_after_cancel:
            raise _Block403Error()
        return {"id": "close-oid", "filled_avg_price": "2.20", "filled_qty": "5264"}

    client.close_position = AsyncMock(side_effect=close_position)
    return client, call_count


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_force_close_cancels_blocking_stop_then_succeeds():
    """Today's LIDR deadlock: operator stop reserves shares, close fails
    once, function cancels the stop, retries, succeeds."""
    client, call_count = _mock_client_lidr_deadlock(
        initial_close_blocked=True,
        close_succeeds_after_cancel=True,
    )
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        cancel_blocking_stops_first=True,
        retry_backoff_s=0.001,
    )
    assert result["succeeded"] is True
    assert result["fill_price"] == 2.20
    assert result["filled_qty"] == 5264
    assert len(result["cancelled_stops"]) == 1
    assert result["cancelled_stops"][0]["stop_price"] == 2.10
    assert result["cancelled_stops"][0]["qty"] == 5264
    # cancel_order called once for the blocking stop
    client.cancel_order.assert_awaited_once_with(
        "859be64d-718e-4f6d-a09a-932d03a79916"
    )
    # close_position called twice: once blocked, once succeeded after cancel
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_force_close_rearms_stop_when_close_ultimately_fails():
    """Bug Z safety contract: if close fails after we cancelled the stop,
    re-arm the stop from snapshot so position is never naked."""
    client, _ = _mock_client_lidr_deadlock(
        initial_close_blocked=True,
        close_succeeds_after_cancel=False,  # close keeps failing
    )
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        max_retries=2,
        cancel_blocking_stops_first=True,
        retry_backoff_s=0.001,
    )
    assert result["succeeded"] is False
    # Stop was cancelled
    assert len(result["cancelled_stops"]) == 1
    # Stop was RE-ARMED via D249 — submit_stop_order called with snapshot params.
    # doc 180: previously asserted submit_order(payload-dict); that call raised in
    # production ("missing 2 required positional arguments: 'qty' and 'side'") and
    # left the position NAKED. Now uses the dedicated submit_stop_order.
    assert client.submit_stop_order.await_count >= 1
    kwargs = client.submit_stop_order.await_args.kwargs
    assert kwargs["symbol"] == "LIDR"
    assert kwargs["side"] == "sell"
    assert kwargs["stop_price"] == 2.10
    assert kwargs["qty"] == 5264
    assert kwargs["time_in_force"] == "gtc"
    assert kwargs["position_intent"] == "close"


@pytest.mark.asyncio
async def test_force_close_default_off_preserves_bug_z_contract():
    """When cancel_blocking_stops_first=False (default), the function
    must NOT touch competing stops — preserves Bug Z safety contract.
    Even after exhausting retries, no cancel/re-arm work happens."""
    client, _ = _mock_client_lidr_deadlock(
        initial_close_blocked=True,
        close_succeeds_after_cancel=False,  # close fails on EVERY attempt
    )
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        max_retries=2,
        retry_backoff_s=0.001,
        # cancel_blocking_stops_first defaults to False
    )
    assert result["succeeded"] is False
    assert result["cancelled_stops"] == []
    client.cancel_order.assert_not_called()
    # No re-arm because nothing was cancelled
    client.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_force_close_succeeds_first_try_no_cancel_needed():
    """Happy path: close succeeds on first attempt; no stop dance."""
    client = MagicMock()
    client.close_position = AsyncMock(return_value={
        "id": "x", "filled_avg_price": "10.00", "filled_qty": "100",
    })
    client.get_orders = AsyncMock(return_value=[])
    client.cancel_order = AsyncMock()
    result = await attempt_close_with_status_check(
        client=client, ticker="AAPL", qty=100,
        cancel_blocking_stops_first=True,
    )
    assert result["succeeded"] is True
    assert result["cancelled_stops"] == []
    client.get_orders.assert_not_called()
    client.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_blocking_sell_orders_filters_by_symbol_and_side():
    """Helper: only cancels sell orders for the target symbol."""
    client = MagicMock()
    client.get_orders = AsyncMock(return_value=[
        {"id": "1", "symbol": "LIDR", "side": "sell", "type": "stop",
         "qty": "5264", "stop_price": "2.10", "limit_price": None,
         "client_order_id": "x"},
        # Different symbol — must NOT be cancelled
        {"id": "2", "symbol": "AAPL", "side": "sell", "type": "stop",
         "qty": "100", "stop_price": "180.00", "limit_price": None,
         "client_order_id": "y"},
        # Buy order (not reducing the position) — must NOT be cancelled
        {"id": "3", "symbol": "LIDR", "side": "buy", "type": "limit",
         "qty": "1000", "stop_price": None, "limit_price": "2.30",
         "client_order_id": "z"},
    ])
    client.cancel_order = AsyncMock()
    cancelled = await _cancel_blocking_sell_orders(client=client, ticker="LIDR")
    assert len(cancelled) == 1
    assert cancelled[0]["id"] == "1"
    assert cancelled[0]["stop_price"] == 2.10
    client.cancel_order.assert_awaited_once_with("1")


@pytest.mark.asyncio
async def test_rearm_only_stops_not_take_profit_limits():
    """The rearm helper only resubmits stop orders. Take-profit limit
    children (which were intentionally part of an OTO bracket) are not
    re-armed by this helper — that's the OTO path's responsibility."""
    client = MagicMock()
    client.submit_order = AsyncMock(return_value={"id": "rearm"})
    # A take-profit limit snapshot — should NOT be re-armed
    snapshot = {
        "id": "tp1", "side": "sell", "qty": 100, "type": "limit",
        "stop_price": None, "limit_price": 13.50,
    }
    result = await _rearm_protective_stop_from_snapshot(
        client=client, ticker="OGN", snapshot=snapshot,
    )
    assert result is None
    client.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_rearm_handles_broker_failure_gracefully():
    """If re-arm itself fails, log D249 STOP_REARM_FAILED and return
    None — don't crash the caller (operator escalation is the recovery)."""
    client = MagicMock()
    client.submit_order = AsyncMock(side_effect=RuntimeError("broker outage"))
    snapshot = {
        "id": "abcd1234", "side": "sell", "qty": 5264, "type": "stop",
        "stop_price": 2.10, "limit_price": None,
    }
    result = await _rearm_protective_stop_from_snapshot(
        client=client, ticker="LIDR", snapshot=snapshot,
    )
    assert result is None  # function returned without raising
