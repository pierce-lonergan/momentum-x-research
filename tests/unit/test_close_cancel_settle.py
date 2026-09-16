"""doc 216: the highest-ROI execution fix — cancel-settle race in attempt_close_with_status_check.

On 6/1 the D76 EOD close 403'd on CMND/OPTU ('insufficient qty, held_for_orders=N'): D248
cancelled the blocking stop, but the cancel hadn't SETTLED within the blind 0.5s wait, so
all 3 retries 403'd, the close FAILED, the stop re-arm ALSO 403'd, and both positions went
NAKED + carried overnight (the dominant P&L leak). Fix: poll qty_available until the cancel
settles, on a SEPARATE budget that doesn't consume the close-retry budget. These tests pin
that a close which is qty-blocked-then-settles SUCCEEDS instead of failing.
"""
from __future__ import annotations

import pytest

from src.execution import bridge


class _FakeClient:
    """Simulates Alpaca: close_position 403s while shares are reserved, succeeds once a
    cancel has 'settled'. get_positions reports qty_available freeing after N polls."""

    def __init__(self, *, settle_after_polls: int, qty: int = 100):
        self.qty = qty
        self.settle_after_polls = settle_after_polls
        self.polls = 0
        self.cancelled = False
        self.close_calls = 0

    async def close_position(self, ticker):
        self.close_calls += 1
        # 403 until the blocking stop is cancelled AND qty has settled free
        if not self.cancelled or self.polls < self.settle_after_polls:
            raise RuntimeError(
                "Client error '403 Forbidden' ... body={'available':'0','code':40310000,"
                "'held_for_orders':'%d','message':'insufficient qty available'}" % self.qty)
        return {"filled_avg_price": 3.50, "filled_qty": self.qty, "status": "filled"}

    async def get_orders(self, **kw):
        # one blocking sell stop until cancelled
        if self.cancelled:
            return []
        return [{"id": "stop1", "symbol": "X", "side": "sell", "type": "stop",
                 "qty": str(self.qty), "stop_price": "3.20", "status": "new"}]

    async def cancel_order(self, oid):
        self.cancelled = True
        return {"id": oid, "status": "canceled"}

    async def get_positions(self):
        self.polls += 1
        avail = self.qty if (self.cancelled and self.polls >= self.settle_after_polls) else 0
        return [{"symbol": "X", "qty": str(self.qty), "qty_available": str(avail)}]


@pytest.mark.asyncio
async def test_close_succeeds_after_cancel_settles():
    """The 6/1 CMND scenario: 403 until cancel settles (after a couple polls) -> must SUCCEED,
    not exhaust retries and go naked."""
    c = _FakeClient(settle_after_polls=2, qty=100)
    res = await bridge.attempt_close_with_status_check(
        client=c, ticker="X", qty=100, max_retries=3, cancel_blocking_stops_first=True)
    assert res["succeeded"] is True
    assert c.cancelled is True
    # it should NOT have burned all close attempts on the unsettled 403
    assert res["attempts"] <= 3


@pytest.mark.asyncio
async def test_await_qty_available_polls_until_free():
    c = _FakeClient(settle_after_polls=3, qty=100)
    c.cancelled = True  # pretend cancel already issued
    ok = await bridge._await_qty_available(c, "X", 100, timeout_s=5.0, poll_s=0.01)
    assert ok is True


@pytest.mark.asyncio
async def test_await_qty_available_times_out_if_never_frees():
    """If qty never frees, the poll returns False (bounded) — never hangs."""
    c = _FakeClient(settle_after_polls=9999, qty=100)
    c.cancelled = True
    ok = await bridge._await_qty_available(c, "X", 100, timeout_s=0.05, poll_s=0.01)
    assert ok is False


@pytest.mark.asyncio
async def test_close_still_fails_cleanly_if_qty_never_frees():
    """If the qty NEVER frees, the close must FAIL cleanly (not hang, not infinite-loop) so
    the caller leaves the position tracked + protected for the failsafe."""
    c = _FakeClient(settle_after_polls=9999, qty=100)
    res = await bridge.attempt_close_with_status_check(
        client=c, ticker="X", qty=100, max_retries=3, cancel_blocking_stops_first=True)
    assert res["succeeded"] is False   # clean failure, bounded
