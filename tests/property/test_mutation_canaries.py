"""Mutation canary tests for Track C — proves the Hypothesis state-machine
SEARCH catches the named bug classes when the rule logic is intentionally
mutated.

Distinct from `tests/property/test_invariant_injection.py`:
  - injection tests construct a known-bad state by hand and call the
    invariant directly (proves the invariant is alive)
  - canary tests INJECT THE BUG INTO A RULE and run the full Hypothesis
    search loop (proves the search reaches the bad state and the
    invariant catches it)

Together they form defense in depth: even if the search misses a class
of state, the injection tests cover it; even if the invariant logic is
broken, the canary tests catch the search-equivalent.

Each canary subclasses `BridgeBrokerStateMachine` and overrides ONE rule
with a buggy implementation. The expectation is that the matching
invariant (I6/I7/I9) fires within a small example budget (≤500). If a
canary ever STOPS firing, the harness has weakened and the suite needs
review.

Per `docs/research-log/25_bug_hunting_playbook.md` §6 — "the harness must
catch its own named bugs to be trusted with novel ones."
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.stateful import rule

from src.execution.bridge import attempt_close_with_status_check
from tests.property.simple_broker import SimpleBroker, BrokerError, TERMINAL_STATES
from tests.property.test_bridge_state_machine import (
    BridgeBrokerStateMachine,
    stop_loosen_pcts,
    tickers,
)


# Aggressive settings — canaries should trip FAST. If they don't trip
# within 500 examples, the harness has lost sensitivity.
# (Bug Z canary pre-populates the tracker so it doesn't need to find
# R1+R3+R11 organically; W and V fire on simpler paths still findable
# in the default budget.)
_CANARY_SETTINGS = hyp_settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.filter_too_much,
    ],
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.target, Phase.shrink],
)


# ── Bug W mutation: R9 loosens the stop instead of preserving ────────


class _BugWMutated(BridgeBrokerStateMachine):
    """Inject Bug W into R9 — bridge restructure LOOSENS the stop instead
    of preserving the previously-tightened price.

    Expected: I7 (stop_matches_broker / tranche_restructure_preserves_tightened_stop)
    fires within the search budget."""

    submitted_orders = BridgeBrokerStateMachine.submitted_orders

    @rule(order_id=submitted_orders, loosen_pct=stop_loosen_pcts)
    def r9_tranche_restructure(self, order_id: str, loosen_pct: float) -> None:
        tracker = self._tracker_for(order_id)
        if tracker is None or tracker.get("closed"):
            return
        old_stop_oid = tracker.get("stop_oid", "")
        if not old_stop_oid or old_stop_oid not in self.broker.orders:
            return
        old_stop = self.broker.orders[old_stop_oid]
        if old_stop.status not in ("new", "held"):
            return
        old_stop_price = float(old_stop.stop_price or tracker["stop_price"])
        ticker = self.order_to_ticker[order_id]
        try:
            self._run(self.broker.cancel_order(old_stop_oid))
        except BrokerError:
            return
        # BUG W: ALWAYS loosen — drop the stop by 5%+|loosen_pct|. The tracker
        # keeps the OLD tightened price; broker gets the new lower price.
        new_stop_price = max(old_stop_price * (0.95 - abs(loosen_pct)), 0.01)
        try:
            response = self._run(self.broker.submit_order({
                "symbol": ticker, "side": "sell",
                "qty": str(tracker.get("filled_qty") or self.broker.orders[order_id].qty),
                "type": "stop", "stop_price": str(new_stop_price),
                "time_in_force": "day",
            }))
        except BrokerError:
            return
        new_oid = response["id"]
        if new_oid in self.broker.orders:
            self.broker.orders[new_oid].status = "new"
        # Tracker keeps the OLD price → I7 violation when broker_sp < tracker_sp
        tracker["stop_oid"] = new_oid
        # tracker["stop_price"] intentionally NOT updated to new_stop_price
        self.order_to_ticker[new_oid] = ticker


_BugWMutated.TestCase.settings = _CANARY_SETTINGS


def test_bug_w_mutation_caught_by_search() -> None:
    """Canary: with R9 mutated to loosen the stop, the Hypothesis search
    MUST find a sequence that fires I7 within 500 examples."""
    with pytest.raises(AssertionError, match="(D248|D253|stop_matches_broker|tranche_restructure_preserves_tightened_stop)"):
        _BugWMutated.TestCase().runTest()


# ── Bug V mutation: R10 rejects without canceling at broker ──────────


class _BugVMutated(BridgeBrokerStateMachine):
    """Inject Bug V into R10 — bridge gives up on an order but does NOT
    cancel at the broker. The broker order remains live, so a later fill
    creates a ghost position.

    Expected: I6 (late_fill_on_rejected_order_canceled) fires within
    the search budget. The order ends up in `bridge_rejected` but its
    broker status is NOT in TERMINAL_STATES."""

    submitted_orders = BridgeBrokerStateMachine.submitted_orders

    @rule(order_id=submitted_orders)
    def r10_bridge_timeout_rejection(self, order_id: str) -> None:
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.filled_qty > 0 or order.status in TERMINAL_STATES:
            return
        # BUG V: bridge marks rejected but DOES NOT cancel at broker.
        self.bridge_rejected.add(order_id)
        tracker = self._tracker_for(order_id)
        if tracker:
            tracker["closed"] = True
        # NO self._run(self.broker.cancel_order(order_id)) — that's the bug


_BugVMutated.TestCase.settings = _CANARY_SETTINGS


def test_bug_v_mutation_caught_by_search() -> None:
    """Canary: with R10 mutated to skip the broker cancel, the search
    MUST find a sequence that fires I6 within 500 examples."""
    with pytest.raises(AssertionError, match="(D252|late_fill_on_rejected_order_canceled)"):
        _BugVMutated.TestCase().runTest()


# ── Bug Z mutation: close always 403, R11 marks tracker closed anyway ─


class _BugZMutated(BridgeBrokerStateMachine):
    """Inject Bug Z scenario at TWO levels:

      1. Patch broker.close_position to always raise 403 (simulating the
         LIDR Bug Z catastrophe — broker accepts position but rejects close).
      2. Override R11 to mark tracker.closed=True regardless of result.succeeded
         (the catastrophic mutation: bridge marks position closed even
         when the broker close failed, leaving naked exposure).

    Expected: I9 (close_attempt_only_marks_closed_on_broker_2xx) fires
    within the search budget. The signature: a ticker has tracker.closed=True
    AND its last close attempt failed AND broker still has the position."""

    submitted_orders = BridgeBrokerStateMachine.submitted_orders

    def __init__(self) -> None:
        super().__init__()
        # Patch close_position to always 403 — simulates the LIDR scenario.
        # Method signature accepts (symbol, fill_price=None) per Bug AD fix.
        async def _always_403(symbol: str, fill_price: float | None = None) -> dict:
            raise BrokerError(f"BUG Z 403 simulated for {symbol}", status_code=403)
        self.broker.close_position = _always_403

        # Pre-populate a position so R11 has something to close immediately.
        # Without this, the harness must reach R1→R3→R11 organically; with
        # all the post-Knight-Capital tightening (gap-through guards,
        # entry-cancel-on-stop), that 3-rule path requires many more
        # examples to find. Pre-populating ensures the canary fires fast.
        from datetime import datetime, timezone
        from tests.property.simple_broker import OrderRecord, PositionRecord
        _eoid = "preloaded-bug-z-entry"
        self.broker.orders[_eoid] = OrderRecord(
            id=_eoid, symbol="AAPL", side="buy", qty=10,
            type="limit", limit_price=10.0, stop_price=None,
            time_in_force="day", status="filled",
            filled_qty=10, filled_avg_price=10.0,
            terminal_at=datetime.now(timezone.utc),
        )
        self.broker.positions["AAPL"] = PositionRecord(
            symbol="AAPL", qty=10, avg_entry_price=10.0,
            side="long", cost_basis=100.0,
        )
        self.tracker_positions["AAPL"] = {
            "entry_oid": _eoid, "stop_oid": "",
            "stop_price": 9.0, "filled_qty": 10, "closed": False,
        }
        self.order_to_ticker[_eoid] = "AAPL"

    @rule(ticker=tickers)
    def r11_attempt_close(self, ticker: str) -> None:
        tracker = self.tracker_positions.get(ticker)
        if tracker is None or tracker.get("closed"):
            return
        if tracker.get("filled_qty", 0) <= 0:
            return

        broker_ref = self.broker

        class _ClientShim:
            async def close_position(self, sym: str) -> dict:
                return await broker_ref.close_position(sym)

            async def cancel_order(self, oid: str) -> dict:
                return await broker_ref.cancel_order(oid)

        result = self._run(
            attempt_close_with_status_check(
                client=_ClientShim(),
                ticker=ticker,
                qty=tracker.get("filled_qty", 0),
                max_retries=2,
                retry_backoff_s=0.001,
            )
        )
        succeeded = bool(result.get("succeeded"))
        self.close_attempts.append({
            "ticker": ticker,
            "succeeded": succeeded,
            "broker_has_position": ticker in broker_ref.positions,
        })
        # BUG Z: ALWAYS mark closed, regardless of result.succeeded
        tracker["closed"] = True
        tracker["filled_qty"] = 0


_BugZMutated.TestCase.settings = _CANARY_SETTINGS


def test_bug_z_mutation_caught_by_search() -> None:
    """Canary: with close_position always 403 + R11 mutated to mark
    closed regardless, the search MUST find a sequence that fires I9
    OR I12 (both correctly catch the catastrophe — I9 is the direct
    Bug Z signature; I12 catches the broker-vs-tracker drift the
    catastrophe creates)."""
    with pytest.raises(AssertionError, match=(
        "(D255|D265|close_attempt_only_marks_closed_on_broker_2xx|position_count_matches_broker)"
    )):
        _BugZMutated.TestCase().runTest()
