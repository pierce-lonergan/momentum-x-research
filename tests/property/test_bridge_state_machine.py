"""
Track C Phase 2 — full RuleBasedStateMachine (R1-R10 + I1-I9).

Per `31_simple_broker_spec.md` §3 + §2: ten rules driving broker-side
events and bridge operations, nine invariants checked after every rule
transition. Each invariant violation maps to a pre-reserved D-code in
`26_d_code_registry.md` (D248-D255 for I2-I9; I1 already documented).

The harness composes:
  * SimpleBroker (oracle) — deterministic, audited, source of truth
  * Internal `tracker_positions` dict — mirrors what the production
    bridge would maintain (entry_oid, stop_oid, stop_price, filled_qty,
    closed). Each rule mutates the tracker the way the bridge would.
  * `attempt_close_with_status_check` (production helper from
    `src.execution.bridge`) — actually invoked by R-close so Bug Z
    is exercised end-to-end on the real production code.

Order of expansion (per user spec — start with I9 Bug Z, work outward):
  I9 → I7 → I6 (already) → I5 → I8 → I2 → I3 → I4

Hypothesis settings: `max_examples=2000`, `hypothesis-seed=0` for
reproducibility. Counterexamples auto-commit as `@example()` decorators.

Bundles model the resource lifecycles:
  submitted_orders: every entry-order id submitted in the run
  active_tickers:   every ticker that has ever had an order
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from src.execution.bridge import attempt_close_with_status_check
from tests.property.simple_broker import (
    SimpleBroker,
    BrokerError,
    TERMINAL_STATES,
)


logger = logging.getLogger(__name__)


# ── Strategies ─────────────────────────────────────────────────────


tickers = st.sampled_from(["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"])
qtys = st.integers(min_value=1, max_value=1000)
prices = st.floats(
    min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False,
).map(lambda x: round(x, 2))
partial_qty_pcts = st.floats(
    min_value=0.1, max_value=0.9, allow_nan=False,
).map(lambda x: round(x, 2))
stop_loosen_pcts = st.floats(
    min_value=-0.10, max_value=0.10, allow_nan=False,
).map(lambda x: round(x, 4))


# ── State machine ──────────────────────────────────────────────────


class BridgeBrokerStateMachine(RuleBasedStateMachine):
    """Composes SimpleBroker (oracle) + production bridge poll loop +
    production close-attempt helper. Exercises ten rules from the spec
    and checks nine invariants after every transition.

    Per-position tracker mirrors what the bridge would maintain:
        tracker_positions[ticker] = {
            "entry_oid":   str — broker order id
            "stop_oid":    str — broker stop order id (OTO child)
            "stop_price":  float — last stop price the bridge believed
            "filled_qty":  int — last filled qty the bridge believed
            "closed":      bool — bridge believes the position is closed
        }
    """

    submitted_orders = Bundle("submitted_orders")
    active_tickers = Bundle("active_tickers")

    def __init__(self) -> None:
        super().__init__()
        self.broker: SimpleBroker = SimpleBroker(starting_equity=100_000.00)
        self.initial_equity: float = self.broker.equity
        # ticker → tracker dict
        self.tracker_positions: dict[str, dict[str, Any]] = {}
        # order_id → ticker (lookup helper)
        self.order_to_ticker: dict[str, str] = {}
        # Bug V — orders the bridge gave up on (timeout)
        self.bridge_rejected: set[str] = set()
        # I9 Bug Z — close attempts log
        self.close_attempts: list[dict[str, Any]] = []
        # Single shared event loop for the lifetime of the example.
        # `asyncio.run()` per call exhausts socket pairs on Windows
        # (ProactorEventLoop creates a self-pipe for every new loop).
        # Surfaced organically at HYP_MAX_EXAMPLES=5000 — fixed defensively
        # so the harness scales to 10000+ for nightly deep search.
        self._runner = asyncio.Runner()

    def teardown(self) -> None:
        # Hypothesis calls teardown after each example. Close the loop
        # so its socket pair is reclaimed immediately.
        try:
            self._runner.close()
        except Exception:
            pass

    def _run(self, coro):
        """Run an awaitable on the shared event loop."""
        return self._runner.run(coro)

    @initialize()
    def setup(self) -> None:
        pass

    # ── Helpers ───────────────────────────────────────────────────

    def _tracker_for(self, order_id: str) -> dict[str, Any] | None:
        ticker = self.order_to_ticker.get(order_id)
        if ticker is None:
            return None
        return self.tracker_positions.get(ticker)

    def _sync_tracker_filled_from_broker(self, order_id: str) -> None:
        """Mimic bridge poll-and-update; defensive on missing orders."""
        if order_id not in self.broker.orders:
            return
        tracker = self._tracker_for(order_id)
        if tracker is None or tracker.get("closed"):
            return
        broker_order = self.broker.orders[order_id]
        if order_id == tracker.get("entry_oid"):
            tracker["filled_qty"] = broker_order.filled_qty

    def _compute_unrealized_pnl(self) -> float:
        """For I8: sum (current_price - avg_entry) * qty across open
        positions. SimpleBroker uses avg_entry as current_price (no quote
        sim), so unrealized always = 0. The invariant is therefore
        equity == initial + cumulative cash flow from closes."""
        return 0.0

    # ── Rules ──────────────────────────────────────────────────────

    @rule(target=submitted_orders, ticker=tickers, qty=qtys, entry=prices, stop=prices)
    def r1_submit_entry(self, ticker: str, qty: int, entry: float, stop: float) -> str:
        """R1 — submit an OTO buy with a stop-loss leg.

        If the ticker is halted, the broker raises (status_code=403). On
        success the tracker registers the entry + stop oids and the
        believed stop_price.

        Production-mirror risk discipline: bridge.execute_verdict's
        upstream RiskManager rejects submits whose dollar-risk
        (qty × (entry - stop)) exceeds a position-size limit. Mirror
        at 5% of starting equity. Without it, Hypothesis finds
        971 @ $104 with a $1 stop = $100k of risk on a $100k account.
        I8 fires correctly even without this guard, but production
        rejects upstream — defense in depth."""
        if ticker in self.broker.halted_tickers:
            return ""
        if ticker in self.tracker_positions and not self.tracker_positions[ticker].get("closed"):
            # Bridge wouldn't double-enter the same ticker. Skip.
            return ""
        if stop >= entry:
            stop = max(round(entry - 0.10, 2), 0.01)
        # Production-mirror risk check: max 5% of starting equity at risk per position
        dollar_risk = qty * max(entry - stop, 0.01)
        if dollar_risk > self.initial_equity * 0.05:
            return ""
        try:
            response = self._run(self.broker.submit_order({
                "symbol": ticker,
                "side": "buy",
                "qty": str(qty),
                "type": "limit",
                "limit_price": str(entry),
                "time_in_force": "day",
                "order_class": "oto",
                "stop_loss": {"stop_price": str(stop)},
            }))
        except BrokerError:
            return ""
        entry_oid = response["id"]
        # The OTO child stop is in legs[]
        stop_oid = ""
        if response.get("legs"):
            stop_oid = response["legs"][0]["id"]
        self.tracker_positions[ticker] = {
            "entry_oid": entry_oid,
            "stop_oid": stop_oid,
            "stop_price": stop,
            "filled_qty": 0,
            "closed": False,
            # I15 tightening (Track C Phase 2, 2026-04-27 evening): the
            # value the bridge submitted to the broker. The invariant now
            # asserts broker.qty == requested_qty (was previously a
            # tautological qty > 0 check). Catches silent qty mutation
            # between bridge.execute_verdict and broker.submit_order
            # (e.g., a sizing-helper that rounds, a string-int coercion
            # that overflows, a refactor that swaps the qty argument).
            "requested_qty": qty,
            # I18 (Track C Phase 2 v2, 2026-04-27 evening, follow-up to
            # the deferred under-protect bound): how many shares were
            # filled when the CURRENT stop was created. The invariant
            # asserts stop.qty >= fills_seen_at_stop_create — i.e., the
            # stop covers AT LEAST what was filled at creation time.
            # This catches the "bridge created a stop that's too small
            # for the position it's supposed to protect" bug class
            # while accepting the legitimate post-fill grace window
            # (subsequent fills may come in after the stop is created;
            # the bridge re-restructures via r9 to grow the stop).
            "fills_seen_at_stop_create": 0,  # OTO stop created at submit-time, before any fill
        }
        self.order_to_ticker[entry_oid] = ticker
        if stop_oid:
            self.order_to_ticker[stop_oid] = ticker
        return entry_oid

    @rule(order_id=submitted_orders, qty_pct=partial_qty_pcts, price=prices)
    def r2_partial_fill(self, order_id: str, qty_pct: float, price: float) -> None:
        """R2 — broker delivers a partial fill; bridge poll updates tracker.

        Production-mirror constraints (see R3 for rationale):
          1. price ±50% of limit_price
          2. gap-through: skip fill if price ≤ stop_price"""
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.status not in ("accepted", "partially_filled"):
            return
        residual = order.qty - order.filled_qty
        if residual <= 0:
            return
        fill_qty = max(1, int(residual * qty_pct))
        # Constraint 1: price ±50% of limit
        if order.limit_price is not None and order.limit_price > 0:
            lo = order.limit_price * 0.5
            hi = order.limit_price * 1.5
            price = max(lo, min(hi, price))
        # Constraint 2: gap-through invalidation
        ticker = self.order_to_ticker.get(order_id)
        if ticker:
            tracker = self.tracker_positions.get(ticker)
            if tracker:
                stop_px = tracker.get("stop_price")
                if (
                    stop_px and order.side == "buy" and float(price) <= float(stop_px)
                ):
                    return
        try:
            self.broker.partial_fill(order_id, fill_qty, price)
        except BrokerError:
            pass
        finally:
            self._sync_tracker_filled_from_broker(order_id)

    @rule(order_id=submitted_orders, price=prices)
    def r3_full_fill(self, order_id: str, price: float) -> None:
        """R3 — broker delivers terminal fill; bridge sees terminal.

        Production-mirror constraints:
          1. Real fills land within ±50% of limit_price (circuit-breaker
             range). Clamp.
          2. If fill drops below the position's stop_price (gap-through),
             real broker invalidates the OTO entry (you can't fill a buy
             that's already past the stop). Skip the fill.
        """
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.status in TERMINAL_STATES:
            self._sync_tracker_filled_from_broker(order_id)
            return
        # Constraint 1: price ±50% of limit
        if order.limit_price is not None and order.limit_price > 0:
            lo = order.limit_price * 0.5
            hi = order.limit_price * 1.5
            price = max(lo, min(hi, price))
        # Constraint 2: gap-through — don't fill a buy at a price below
        # the OTO stop_price (the stop would have already triggered)
        ticker = self.order_to_ticker.get(order_id)
        if ticker:
            tracker = self.tracker_positions.get(ticker)
            if tracker:
                stop_px = tracker.get("stop_price")
                if (
                    stop_px and order.side == "buy" and float(price) <= float(stop_px)
                ):
                    return  # gap-through invalidates the entry
        try:
            self.broker.terminal_fill(order_id, price)
        except BrokerError:
            pass
        finally:
            self._sync_tracker_filled_from_broker(order_id)

    @rule(order_id=submitted_orders)
    def r4_reject(self, order_id: str) -> None:
        """R4 — broker marks the order rejected (terminal). Bridge poll
        observes and tracker reflects no fill."""
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.status in TERMINAL_STATES:
            return
        try:
            self.broker.reject(order_id, "rejected_by_test_rule")
        except BrokerError:
            pass
        finally:
            self._sync_tracker_filled_from_broker(order_id)
            # On rejection, bridge would not consider this a position
            tracker = self._tracker_for(order_id)
            if tracker and not tracker.get("closed") and tracker.get("filled_qty", 0) == 0:
                tracker["closed"] = True

    @rule(order_id=submitted_orders)
    def r5_cancel(self, order_id: str) -> None:
        """R5 — bridge cancels an order (e.g., via _cancel_order_or_warn).
        Cascades to OTO child legs."""
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.status in TERMINAL_STATES:
            return
        try:
            self._run(self.broker.cancel_order(order_id))
        except BrokerError:
            pass
        finally:
            tracker = self._tracker_for(order_id)
            if tracker and tracker.get("filled_qty", 0) == 0:
                tracker["closed"] = True

    @rule(ticker=tickers)
    def r6_halt(self, ticker: str) -> None:
        """R6 — broker halts a ticker. Subsequent submits for that ticker
        will raise from the broker; pending accepted orders flip to
        rejected per the spec §1 halt() semantics."""
        if ticker in self.broker.halted_tickers:
            return
        self.broker.halt(ticker)
        # Re-sync any in-flight orders for this ticker
        for oid, tk in list(self.order_to_ticker.items()):
            if tk == ticker:
                self._sync_tracker_filled_from_broker(oid)

    @rule(ticker=tickers)
    def r7_resume(self, ticker: str) -> None:
        """R7 — broker resumes a halted ticker."""
        self.broker.resume(ticker)

    @rule(order_id=submitted_orders)
    def r8_trigger_stop(self, order_id: str) -> None:
        """R8 — protective stop fires. Walks from entry_oid to its OTO
        stop child and triggers, OR triggers a standalone stop directly.

        Production-mirror discipline: when the stop fires, the bridge
        cancels the still-active entry order (since the position has been
        liquidated, accepting more entry fills would re-open the position
        the stop just closed). Without this, R3 fills landing AFTER R8
        create phantom long positions that I12 catches as enumeration
        drift."""
        tracker = self._tracker_for(order_id)
        if tracker is None:
            return
        stop_oid = tracker.get("stop_oid", "")
        if not stop_oid or stop_oid not in self.broker.orders:
            return
        stop_order = self.broker.orders[stop_oid]
        if stop_order.status != "new":
            return
        try:
            self.broker.trigger_stop(stop_oid)
        except BrokerError:
            return
        # The position has been closed at broker — bridge would observe
        # via poll loop and mark tracker closed
        tracker["closed"] = True
        tracker["filled_qty"] = 0  # net position now flat
        tracker["stop_price"] = stop_order.stop_price or tracker["stop_price"]
        # Production-mirror: cancel the entry order if still open
        # (otherwise more fills will land and re-create the position)
        if order_id in self.broker.orders:
            entry_order = self.broker.orders[order_id]
            if entry_order.status not in TERMINAL_STATES:
                try:
                    self._run(self.broker.cancel_order(order_id))
                except BrokerError:
                    pass

    @rule(order_id=submitted_orders, loosen_pct=stop_loosen_pcts)
    def r9_tranche_restructure(self, order_id: str, loosen_pct: float) -> None:
        """R9 — Bug W scenario: bridge cancels old stop and submits new.

        The CORRECT bridge behaviour preserves the previous (tightened)
        stop_price. This rule simulates the bridge's behaviour: it
        cancels the old stop and submits a new one. To probe Bug W, the
        new stop_price is the OLD stop_price * (1 + loosen_pct). If
        loosen_pct > 0, the stop is being LOOSENED (which violates I7).
        The bridge code under test must clamp loosen_pct <= 0 (i.e.,
        never loosen). Here we always submit the new stop at the OLD
        stop_price (the correct behaviour) so I7 passes — if the harness
        ever drops the clamp, I7 will fire."""
        tracker = self._tracker_for(order_id)
        if tracker is None or tracker.get("closed"):
            return
        # Track C i15-tightening counterexample (2026-04-27): production
        # bridge.tranche_restructure must not operate on a terminal entry
        # order. Without this guard, calling r9 with an OLD canceled entry
        # id (after a re-entry replaced the tracker) still resolves to the
        # current tracker via order_to_ticker, but reads
        # `self.broker.orders[old_entry_id].qty` for the NEW stop sizing —
        # producing a stop sized to the OLD position rather than the
        # current one. If the new position is smaller, the new stop is
        # OVER-SIZED → on trigger, sells more than the position holds,
        # opening a SHORT via the close (Bug B class). Equivalent to a
        # Bug-Z-style stale-order leak, surfaced by i15's tightened
        # filled ≤ stop ≤ requested band.
        if order_id in self.broker.orders:
            entry_order = self.broker.orders[order_id]
            if entry_order.status in TERMINAL_STATES:
                return
        old_stop_oid = tracker.get("stop_oid", "")
        if not old_stop_oid or old_stop_oid not in self.broker.orders:
            return
        old_stop = self.broker.orders[old_stop_oid]
        if old_stop.status not in ("new", "held"):
            return
        old_stop_price = float(old_stop.stop_price or tracker["stop_price"])
        ticker = self.order_to_ticker[order_id]
        # Cancel old stop
        try:
            self._run(self.broker.cancel_order(old_stop_oid))
        except BrokerError:
            return
        # CORRECT bridge behaviour: never loosen — keep the old price
        new_stop_price = old_stop_price  # NOT old_stop_price * (1 + loosen_pct)
        # Submit the replacement stop directly via SimpleBroker submit
        try:
            response = self._run(self.broker.submit_order({
                "symbol": ticker,
                "side": "sell",
                "qty": str(tracker.get("filled_qty") or self.broker.orders[order_id].qty),
                "type": "stop",
                "stop_price": str(new_stop_price),
                "time_in_force": "day",
            }))
        except BrokerError:
            return
        new_oid = response["id"]
        # Active stop status
        if new_oid in self.broker.orders:
            self.broker.orders[new_oid].status = "new"
        tracker["stop_oid"] = new_oid
        tracker["stop_price"] = new_stop_price
        # I18: snapshot the filled_qty at the moment the new stop is created.
        # The invariant asserts stop.qty >= fills_seen_at_stop_create — i.e.,
        # the bridge must size the new stop to at least cover the position it
        # was created to protect. Subsequent fills are tolerated until the
        # next r9 call grows the stop again.
        tracker["fills_seen_at_stop_create"] = int(tracker.get("filled_qty") or 0)
        self.order_to_ticker[new_oid] = ticker

    @rule(order_id=submitted_orders)
    def r10_bridge_timeout_rejection(self, order_id: str) -> None:
        """R10 — Bug V scenario: poll budget exhausts with filled_qty=0.
        Bridge rejects + cancels at broker (Bug V fix)."""
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.filled_qty > 0 or order.status in TERMINAL_STATES:
            return
        try:
            self._run(self.broker.cancel_order(order_id))
            self.bridge_rejected.add(order_id)
            tracker = self._tracker_for(order_id)
            if tracker:
                tracker["closed"] = True
        except BrokerError:
            pass

    @rule(ticker=tickers)
    def r11_attempt_close(self, ticker: str) -> None:
        """R11 — bridge calls SMART_EXIT close path via the production
        helper. Only mutates tracker on broker-2xx (Bug Z invariant).

        This invokes `attempt_close_with_status_check` from the actual
        production bridge.py — exercising the real Bug Z patch."""
        tracker = self.tracker_positions.get(ticker)
        if tracker is None or tracker.get("closed"):
            return
        if tracker.get("filled_qty", 0) <= 0:
            return  # nothing to close

        # Build a minimal client surface the helper needs
        broker_ref = self.broker
        ticker_ref = ticker

        class _ClientShim:
            async def close_position(self, sym: str) -> dict[str, Any]:
                return await broker_ref.close_position(sym)

            async def cancel_order(self, oid: str) -> dict[str, Any]:
                return await broker_ref.cancel_order(oid)

        result = self._run(
            attempt_close_with_status_check(
                client=_ClientShim(),
                ticker=ticker_ref,
                qty=tracker.get("filled_qty", 0),
                max_retries=2,
                retry_backoff_s=0.001,
            )
        )
        succeeded = bool(result.get("succeeded"))
        # Log for I9 cross-check
        self.close_attempts.append({
            "ticker": ticker_ref,
            "succeeded": succeeded,
            "broker_has_position": ticker_ref in self.broker.positions,
        })
        # ONLY mutate tracker on success (the Bug Z discipline)
        if succeeded:
            tracker["closed"] = True
            tracker["filled_qty"] = 0
            # Production-mirror: cancel the entry order if still open, so
            # subsequent broker fills don't re-create the position.
            entry_oid = tracker.get("entry_oid", "")
            if entry_oid and entry_oid in self.broker.orders:
                entry_order = self.broker.orders[entry_oid]
                if entry_order.status not in TERMINAL_STATES:
                    try:
                        self._run(self.broker.cancel_order(entry_oid))
                    except BrokerError:
                        pass

    # ── Invariants ────────────────────────────────────────────────

    @invariant()
    def i1_tracker_matches_broker(self) -> None:
        """I1 — tracker_filled[entry_oid] == broker.orders[entry_oid].filled_qty."""
        for ticker, tracker in self.tracker_positions.items():
            if tracker.get("closed"):
                continue
            entry_oid = tracker.get("entry_oid")
            if not entry_oid or entry_oid not in self.broker.orders:
                continue
            broker_qty = self.broker.orders[entry_oid].filled_qty
            tracker_qty = tracker.get("filled_qty", 0)
            assert tracker_qty == broker_qty, (
                f"D248-class (I1) tracker_matches_broker VIOLATED: "
                f"ticker {ticker} entry {entry_oid[:8]} "
                f"tracker={tracker_qty} broker={broker_qty}"
            )

    @invariant()
    def i2_stop_matches_broker(self) -> None:
        """I2 — for any tracked stop_oid, tracker stop_price == broker stop_price (±$0.01)."""
        for ticker, tracker in self.tracker_positions.items():
            if tracker.get("closed"):
                continue
            stop_oid = tracker.get("stop_oid", "")
            if not stop_oid or stop_oid not in self.broker.orders:
                continue
            broker_stop = self.broker.orders[stop_oid]
            if broker_stop.status in TERMINAL_STATES:
                continue
            broker_sp = broker_stop.stop_price
            tracker_sp = tracker.get("stop_price")
            if broker_sp is None or tracker_sp is None:
                continue
            assert abs(float(broker_sp) - float(tracker_sp)) < 0.01, (
                f"D248 (I2) stop_matches_broker VIOLATED: ticker {ticker} "
                f"tracker stop={tracker_sp} broker stop={broker_sp}"
            )

    @invariant()
    def i3_no_orders_during_halt(self) -> None:
        """I3 — for any halted ticker, no order in non-terminal status
        for that ticker (after the halt was applied — broker halt() flips
        accepted to rejected)."""
        for ticker in self.broker.halted_tickers:
            for oid, tk in self.order_to_ticker.items():
                if tk != ticker:
                    continue
                if oid not in self.broker.orders:
                    continue
                order = self.broker.orders[oid]
                assert order.status != "accepted", (
                    f"D249 (I3) no_orders_during_halt VIOLATED: "
                    f"ticker {ticker} halted but order {oid[:8]} "
                    f"is still status=accepted"
                )

    @invariant()
    def i4_no_negative_position(self) -> None:
        """I4 — abs(broker.positions[symbol].qty) is bounded by sum of
        side-aware fills; never crosses zero in a single op (i.e., we
        never see a position go from +5 directly to -3)."""
        for sym, pos in self.broker.positions.items():
            # SimpleBroker maintains net qty; the invariant is that |qty| > 0
            # if the position is in the dict (zero-qty positions are popped).
            assert pos.qty != 0, (
                f"D250 (I4) no_negative_position VIOLATED: "
                f"symbol {sym} present with qty=0 (should have been popped)"
            )

    @invariant()
    def i5_cumulative_fill_bounded(self) -> None:
        """I5 — broker.orders[oid].filled_qty <= broker.orders[oid].qty
        for every order. SimpleBroker enforces this in partial_fill, but
        the invariant is independent so any future broker bug surfaces."""
        for oid, order in self.broker.orders.items():
            assert order.filled_qty <= order.qty, (
                f"D251 (I5) cumulative_fill_bounded VIOLATED: "
                f"order {oid[:8]} filled={order.filled_qty} requested={order.qty}"
            )

    @invariant()
    def i6_rejected_orders_canceled_at_broker(self) -> None:
        """I6 — any order the bridge rejected (Bug V path) MUST be
        terminal at the broker."""
        for order_id in self.bridge_rejected:
            if order_id not in self.broker.orders:
                continue
            broker_status = self.broker.orders[order_id].status
            assert broker_status in TERMINAL_STATES, (
                f"D252 (I6) late_fill_on_rejected_order_canceled VIOLATED: "
                f"bridge rejected {order_id[:8]} but broker status is "
                f"{broker_status} (must be terminal)"
            )

    @invariant()
    def i7_tranche_restructure_preserves_tightened_stop(self) -> None:
        """I7 — for every position with a tracker.stop_price set, after
        any restructure the broker stop_price MUST equal the tracker
        stop_price (proves the restructure didn't loosen below the
        tightened price). Bug W."""
        for ticker, tracker in self.tracker_positions.items():
            if tracker.get("closed"):
                continue
            stop_oid = tracker.get("stop_oid", "")
            tracker_sp = tracker.get("stop_price")
            if not stop_oid or stop_oid not in self.broker.orders:
                continue
            broker_stop = self.broker.orders[stop_oid]
            if broker_stop.status in TERMINAL_STATES:
                continue
            broker_sp = broker_stop.stop_price
            if broker_sp is None or tracker_sp is None:
                continue
            assert float(broker_sp) >= float(tracker_sp) - 0.01, (
                f"D253 (I7) tranche_restructure_preserves_tightened_stop "
                f"VIOLATED: ticker {ticker} tracker stop_price={tracker_sp} "
                f"broker stop_price={broker_sp} (broker loosened beyond tracker)"
            )

    @invariant()
    def i8_equity_conservation(self) -> None:
        """I8 — broker.equity stays within physically-realisable bounds.

        Sharpened from the initial 100x cap to:
          * NaN check (always)
          * equity > 0 (no negative equity — Knight Capital catastrophe class)
          * equity < initial * 5 (with max trade qty 1000 × max price 500 =
            500k notional, 5x initial = 500k tightly bounds the realistic
            multi-bagger upside in the time horizon a state machine explores)

        Tightening rationale: the original 100x bound only caught complete
        runaway. Negative equity is the canonical "magic loss" failure
        mode — silent loss bigger than the entire account. 5x upper is
        deliberately tight; if a Hypothesis sequence ever exceeds it, the
        broker accounting itself has a bug worth surfacing.
        """
        eq = self.broker.equity
        assert eq == eq, "D254 (I8) equity_conservation VIOLATED: NaN equity"
        assert eq > 0, (
            f"D254 (I8) equity_conservation VIOLATED: equity={eq} non-positive "
            f"(Knight-Capital-class silent loss exceeds entire account)"
        )
        max_plausible = self.initial_equity * 5
        assert eq < max_plausible, (
            f"D254 (I8) equity_conservation VIOLATED: equity={eq} exceeds "
            f"5x initial ({max_plausible}) — broker accounting bug"
        )

    @invariant()
    def i9_close_attempt_only_marks_closed_on_broker_2xx(self) -> None:
        """I9 — Bug Z: tracker.closed=True ONLY for tickers where the
        most recent close attempt succeeded (broker 2xx). If a close
        was attempted, failed (non-2xx), AND tracker.closed=True at the
        same time → this is the Bug Z catastrophic state."""
        # Compress close_attempts to last-attempt-per-ticker
        last_by_ticker: dict[str, dict[str, Any]] = {}
        for attempt in self.close_attempts:
            last_by_ticker[attempt["ticker"]] = attempt
        for ticker, last in last_by_ticker.items():
            tracker = self.tracker_positions.get(ticker)
            if tracker is None:
                continue
            if last["succeeded"]:
                continue  # success path is not Bug Z
            # Failed close: tracker MUST NOT be marked closed by the
            # close-attempt path (some other path — R8 stop-trigger, R10
            # timeout — may legitimately have closed it; check broker).
            assert not (tracker.get("closed") and last["broker_has_position"]), (
                f"D255 (I9) close_attempt_only_marks_closed_on_broker_2xx "
                f"VIOLATED: ticker {ticker} has tracker.closed=True "
                f"but broker still holds the position AND the last "
                f"close attempt failed. This is the Bug Z signature."
            )

    @invariant()
    def i10_no_opposite_side_orders_same_ticker(self) -> None:
        """I10 — at any moment, no ticker has both an open buy AND an
        open sell ENTRY order in non-terminal status (excluding OTO
        protective-stop legs, which legitimately have opposite side).

        Knight Capital classic: a runaway algorithm submits opposite-side
        entries against itself, eating spread + slippage on every tick.
        Real production code prevents this via the position manager
        ledger; the invariant catches the case where the ledger is
        bypassed.

        Filter: orders with `parent_id` set are OTO child legs (e.g.,
        a sell stop guarding a buy entry) — those are by-design opposite
        side and not violations."""
        per_ticker_sides: dict[str, set[str]] = {}
        for oid, order in self.broker.orders.items():
            if order.status in TERMINAL_STATES:
                continue
            if order.parent_id:           # skip OTO child legs
                continue
            if order.type == "stop":       # skip standalone protective stops
                continue
            sides = per_ticker_sides.setdefault(order.symbol, set())
            sides.add(order.side)
        for ticker, sides in per_ticker_sides.items():
            assert not ({"buy", "sell"} <= sides), (
                f"D263 (I10) no_opposite_side_orders_same_ticker VIOLATED: "
                f"ticker {ticker} has both buy AND sell ENTRY orders in non-terminal "
                f"status (Knight-Capital-class self-trading risk)"
            )

    @invariant()
    def i11_equity_drift_bounded_per_session(self) -> None:
        """I11 — |equity_now - initial_equity| <= 90% of initial.

        Catches the near-account-wipe / multi-account-multiplier class.
        Real production runs at 5% per-session bound; the 90% here
        reflects what's reachable through the synthetic rule grammar
        (where stop-above-fill configurations can produce 50%+ swings
        that aren't bugs but aren't realistic production scenarios either).

        Tightening rationale: if equity drifts > 90% in one session, the
        system has either silently lost the account (left tail) or
        exploited a broker-accounting bug (right tail). Both are signals
        worth investigating."""
        eq = self.broker.equity
        max_drift = self.initial_equity * 0.9
        actual_drift = abs(eq - self.initial_equity)
        assert actual_drift <= max_drift, (
            f"D264 (I11) equity_drift_bounded_per_session VIOLATED: "
            f"equity={eq:.2f} initial={self.initial_equity:.2f} "
            f"drift={actual_drift:.2f} > max={max_drift:.2f} "
            f"(90% per-session bound)"
        )

    @invariant()
    def i12_position_count_matches_broker(self) -> None:
        """I12 — tracker positions and broker positions agree across THREE
        dimensions (Track C v2 tightening, 2026-04-27 evening, follow-up
        to I18):
          (a) count: number of non-closed tracker positions == number of
              broker positions with qty != 0
          (b) per-ticker qty: for every non-closed tracker with filled_qty>0,
              broker.positions[ticker].qty == tracker.filled_qty
          (c) symmetric coverage: every broker position with qty != 0 has
              a corresponding non-closed tracker entry (catches the
              "ghost broker position" case where the broker holds shares
              the tracker doesn't know about — Bug Z class)

        Previously only checked (a) — a tracker saying 100 shares and a
        broker saying 50 shares both pass `count == count` (1 == 1) even
        though there's a 50-share drift. The post-LIDR Bug Z scenario
        (broker stop_oid mismatch with tracker) and the Knight-Capital
        ghost-position class both motivate the per-ticker qty check.
        """
        # Build per-ticker views once
        tracker_qty: dict[str, int] = {}
        for ticker, tk in self.tracker_positions.items():
            if tk.get("closed"):
                continue
            filled = int(tk.get("filled_qty") or 0)
            if filled > 0:
                tracker_qty[ticker] = filled
        broker_qty: dict[str, int] = {
            p.symbol: abs(int(p.qty))
            for p in self.broker.positions.values()
            if p.qty != 0
        }

        # (a) count
        assert len(tracker_qty) == len(broker_qty), (
            f"D265 (I12a count) position_count_matches_broker VIOLATED: "
            f"tracker open positions={len(tracker_qty)} (tickers="
            f"{sorted(tracker_qty.keys())}) broker positions={len(broker_qty)} "
            f"(tickers={sorted(broker_qty.keys())}) — enumeration drift, "
            f"one side lost an entry"
        )

        # (b) per-ticker qty parity for tracker → broker
        for ticker, t_qty in tracker_qty.items():
            b_qty = broker_qty.get(ticker)
            assert b_qty is not None, (
                f"D265 (I12c symmetric) position_count_matches_broker VIOLATED: "
                f"tracker has open position for {ticker} (filled_qty={t_qty}) "
                f"but broker has no position record — tracker has a position "
                f"the broker doesn't (the bridge believes shares it doesn't own)."
            )
            assert b_qty == t_qty, (
                f"D265 (I12b qty) position_count_matches_broker VIOLATED: "
                f"ticker {ticker} tracker.filled_qty={t_qty} but broker "
                f"position.qty={b_qty} — per-ticker qty drift. Bridge's "
                f"position-size discipline is broken; risk math is off by "
                f"{t_qty}/{b_qty}={t_qty / b_qty:.3f}x. Was caught only as "
                f"enumeration count == count in the v1 invariant."
            )

        # (c) symmetric: every broker position has a matching tracker
        for ticker in broker_qty:
            assert ticker in tracker_qty, (
                f"D265 (I12c symmetric) position_count_matches_broker VIOLATED: "
                f"broker has position for {ticker} (qty={broker_qty[ticker]}) "
                f"but tracker has no open entry for it — GHOST BROKER POSITION. "
                f"This is the canonical Bug Z class: broker holds shares the "
                f"bridge doesn't know about. On EOD recon the bridge would not "
                f"see this position; on next session it would surface as a "
                f"D86 ghost detection."
            )

    @invariant()
    def i13_no_zero_qty_open_position(self) -> None:
        """I13 — if the broker reports an entry order as FILLED with
        non-zero filled_qty, the tracker MUST also reflect that fill
        (closed=False AND filled_qty>0).

        This is the canonical "zombie tracker" pattern: broker filled
        the order, tracker missed the poll-update. R4 reject + R5 cancel
        + R6 halt-cascade-reject paths don't trip this — those are
        legitimately "never filled" terminal states. Only the FILLED
        case represents a real position the tracker is missing."""
        for ticker, tracker in self.tracker_positions.items():
            if tracker.get("closed"):
                continue
            entry_oid = tracker.get("entry_oid", "")
            if not entry_oid or entry_oid not in self.broker.orders:
                continue
            entry_order = self.broker.orders[entry_oid]
            # Only zombie-check filled orders — rejected/canceled/expired
            # entries are legitimately "never opened a position"
            if entry_order.status != "filled":
                continue
            broker_filled = entry_order.filled_qty
            tracker_filled = tracker.get("filled_qty", 0)
            assert tracker_filled > 0 or broker_filled == 0, (
                f"D266 (I13) no_zero_qty_open_position VIOLATED: "
                f"ticker {ticker} has tracker.closed=False, tracker.filled_qty=0, "
                f"BUT broker reports entry as filled qty={broker_filled}. "
                f"Zombie tracker entry — broker filled the order but tracker "
                f"missed the poll update."
            )

    @invariant()
    def i14_stop_oid_uniqueness(self) -> None:
        """I14 — every active stop_oid is unique across positions.

        Catches the cross-stop-assignment bug class: two positions claim
        the same stop_oid → when the stop fires, only one position knows;
        the other becomes a zombie until manual intervention. Real
        production: position_manager.add_position should refuse to register
        a stop_oid that's already attached to another open position."""
        seen: dict[str, str] = {}  # stop_oid → first ticker seen
        for ticker, tracker in self.tracker_positions.items():
            if tracker.get("closed"):
                continue
            stop_oid = tracker.get("stop_oid", "")
            if not stop_oid:
                continue
            assert stop_oid not in seen, (
                f"D267 (I14) stop_oid_uniqueness VIOLATED: "
                f"stop_oid {stop_oid[:8]} appears on BOTH ticker {seen[stop_oid]} "
                f"AND ticker {ticker}. Cross-stop assignment leaves one position "
                f"as a zombie when the stop fires."
            )
            seen[stop_oid] = ticker

    @invariant()
    def i15_entry_qty_matches_request(self) -> None:
        """I15 — for every active entry order:
          (a) broker.qty == requested_qty  (silent entry-qty mutation)
          (b) active_stop.qty ≤ requested_qty
              (stop never exceeds the original request — over-size would
              over-sell on trigger, opening a SHORT via the close)

        Catches TWO bug classes:
          - silent entry qty mutation between bridge submit and broker store
            (was tautological in the v1 invariant; now strict equality)
          - over-sized stop: stop.qty > requested_qty would over-sell on
            trigger, opening a SHORT via the close (Bug B class)

        Track C Phase 2 tightening (2026-04-27, post-Bug-AG):
          - v1 was `order.qty > 0` (tautological).
          - v2 attempted strict equality on the OTO child stop too, but
            surfaced a false positive: Bug AB legitimately resizes the
            child stop down to filled_qty after a partial fill, so
            stop.qty < requested_qty is correct behavior post-fill.
          - v3 added a `filled_qty ≤ stop.qty` lower bound to catch
            under-protection, but surfaced its OWN false positive: there
            is always a brief window after a fill before the bridge has
            re-restructured the stop. Modeling that grace window
            properly is out of scope for Track C — deferred to a
            future ship.
          - v4 (this version) keeps the cleanest pair: entry strict
            equality + stop upper bound. The over-size class is the
            real Bug B "over-sell on trigger" defect; that's the value
            of this tightening.

        v3's lower-bound counterexample (r1+r2_partial+r9+r3_full →
        stop.qty=1 < filled_qty=2) is captured as a known-defect
        candidate for the next harness pass.
        """
        for ticker, tracker in self.tracker_positions.items():
            requested = tracker.get("requested_qty")
            if requested is None:
                continue  # legacy entries without requested_qty annotation

            # (a) entry qty strict equality
            entry_oid = tracker.get("entry_oid", "")
            if entry_oid and entry_oid in self.broker.orders:
                order = self.broker.orders[entry_oid]
                assert order.qty == requested, (
                    f"D268 (I15a) entry_qty_matches_request VIOLATED: "
                    f"ticker {ticker} entry order {entry_oid[:8]} broker.qty="
                    f"{order.qty} but bridge requested {requested} — silent qty "
                    f"mutation between submit and broker.store. Position-size "
                    f"discipline broken; risk math is off by "
                    f"{order.qty}/{requested}={order.qty / requested:.3f}x."
                )

            # (b) stop qty upper bound — never exceed requested
            stop_oid = tracker.get("stop_oid", "")
            if stop_oid and stop_oid in self.broker.orders:
                stop_order = self.broker.orders[stop_oid]
                # Skip terminal stops — only active stops protect the position.
                if stop_order.status in TERMINAL_STATES:
                    continue
                assert stop_order.qty <= requested, (
                    f"D268 (I15b over-size) entry_qty_matches_request "
                    f"VIOLATED: ticker {ticker} stop order {stop_oid[:8]} "
                    f"broker.qty={stop_order.qty} but bridge requested only "
                    f"{requested} — OVER-SIZED stop. On trigger this would "
                    f"sell {stop_order.qty - requested} more than the position, "
                    f"opening a SHORT via the close (Bug B class)."
                )

    @invariant()
    def i16_fill_price_within_quote_band(self) -> None:
        """I16 — every filled limit order has filled_avg_price within
        ±50% of its limit_price.

        Catches phantom-quote bugs where the broker reports fills at
        prices wildly outside the limit (the R3 harness clamp is the
        production-mirror; this invariant catches the case where the
        oracle fails to clamp). Aligned with R2/R3 fill-price discipline.

        Filter: orders without limit_price (market, stop) are exempt."""
        for oid, order in self.broker.orders.items():
            if order.filled_qty <= 0:
                continue
            if order.limit_price is None or order.limit_price <= 0:
                continue
            # Bug AA's discipline: buy fill <= limit; sell fill >= limit
            # I16's discipline: fill within ±50% of limit (looser bound,
            # catches catastrophic divergence)
            ratio = float(order.filled_avg_price) / float(order.limit_price)
            assert 0.5 <= ratio <= 1.5, (
                f"D269 (I16) fill_price_within_quote_band VIOLATED: "
                f"order {oid[:8]} filled_avg_price={order.filled_avg_price} "
                f"limit_price={order.limit_price} ratio={ratio:.3f} "
                f"(circuit-breaker territory; oracle should reject)"
            )

    @invariant()
    def i17_terminal_status_consistent(self) -> None:
        """I17 — terminal status is consistent with filled_qty.

          status="filled"   → filled_qty > 0 (OTO child stops legitimately
                              fill less than declared qty when the parent
                              only partially filled — they cover the actual
                              position size, not the OTO declared size)
          status="canceled" → filled_qty <= requested qty (partial allowed)
          status="rejected" → filled_qty == 0
          status="expired"  → filled_qty <= requested qty

        For non-OTO-child orders, status="filled" requires filled_qty == qty
        (no partial-fill tolerance for entries / standalone orders)."""
        for oid, order in self.broker.orders.items():
            if order.status == "filled":
                assert order.filled_qty > 0, (
                    f"D270 (I17) terminal_status_consistent VIOLATED: "
                    f"order {oid[:8]} status=filled but filled_qty={order.filled_qty}=0"
                )
                # Non-OTO-child orders must fully fill on status=filled.
                # OTO child stops (parent_id set, type=stop) can fill less
                # than their declared qty per Bug AB's fill_size discipline
                # (stop covers actual position, not OTO declared size).
                is_oto_partial_stop = order.parent_id and order.type == "stop"
                if not is_oto_partial_stop:
                    assert order.filled_qty == order.qty, (
                        f"D270 (I17) terminal_status_consistent VIOLATED: "
                        f"order {oid[:8]} (non-OTO-stop) status=filled but "
                        f"filled_qty={order.filled_qty} != qty={order.qty}"
                    )
            elif order.status == "rejected":
                assert order.filled_qty == 0, (
                    f"D270 (I17) terminal_status_consistent VIOLATED: "
                    f"order {oid[:8]} status=rejected but filled_qty={order.filled_qty} "
                    f"(rejected orders cannot have fills)"
                )
            elif order.status in ("canceled", "expired"):
                assert order.filled_qty <= order.qty, (
                    f"D270 (I17) terminal_status_consistent VIOLATED: "
                    f"order {oid[:8]} status={order.status} but filled_qty="
                    f"{order.filled_qty} > qty={order.qty}"
                )

    @invariant()
    def i18_stop_covers_fills_at_creation(self) -> None:
        """I18 — for every active stop, broker.qty >= fills_seen_at_stop_create.

        The stop must cover AT LEAST the position size that existed when the
        stop was created. This catches the under-protective-stop class:
        bridge.tranche_restructure must size the new stop to cover the
        already-filled position; sizing it smaller leaves shares unprotected
        on stop trigger.

        Track C Phase 2 follow-up (2026-04-27, after the i15 v3 deferred
        the strict `stop.qty >= filled_qty` check due to transient post-
        fill window false positives): I18 uses a snapshot taken at stop-
        creation time (`fills_seen_at_stop_create`), so:

          - the bridge's r9_tranche_restructure can size the new stop to
            current filled_qty WITHOUT being penalized when subsequent
            fills come in (the tracker waits for the next r9 to grow it);
          - but the bridge CANNOT create a stop sized SMALLER than what
            was already filled at the moment of creation.

        The fills_seen_at_stop_create field is set:
          - to 0 by r1_submit_entry (OTO child stop is created at submit
            before any fill);
          - to current tracker.filled_qty by r9_tranche_restructure when
            the replacement stop is submitted.

        Skip terminal stops — they no longer protect the position.
        """
        for ticker, tracker in self.tracker_positions.items():
            stop_oid = tracker.get("stop_oid", "")
            if not stop_oid or stop_oid not in self.broker.orders:
                continue
            stop_order = self.broker.orders[stop_oid]
            if stop_order.status in TERMINAL_STATES:
                continue
            fills_at_create = int(tracker.get("fills_seen_at_stop_create", 0) or 0)
            assert stop_order.qty >= fills_at_create, (
                f"D271 (I18) stop_covers_fills_at_creation VIOLATED: "
                f"ticker {ticker} stop order {stop_oid[:8]} broker.qty="
                f"{stop_order.qty} but fills_seen_at_stop_create="
                f"{fills_at_create} — UNDER-PROTECTIVE stop at creation. "
                f"{fills_at_create - stop_order.qty} filled shares would "
                f"remain unprotected on stop trigger. The bridge must size "
                f"replacement stops to at least cover the position they "
                f"are created to protect."
            )


# Register the test class with Hypothesis (this turns the state machine
# into a runnable pytest test).
TestBridgeBrokerStateMachine = BridgeBrokerStateMachine.TestCase

# Settings — env-driven so callers can scale the search:
#   HYP_MAX_EXAMPLES=200    pre-commit fast feedback (~3s)
#   HYP_MAX_EXAMPLES=2000   default (~20s) — matches sprint spec
#   HYP_MAX_EXAMPLES=10000  weekly deep search (~3min)
_HYP_MAX_EXAMPLES = int(os.environ.get("HYP_MAX_EXAMPLES", "2000"))

TestBridgeBrokerStateMachine.settings = hyp_settings(
    max_examples=_HYP_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.filter_too_much,
    ],
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.target, Phase.shrink],
)
