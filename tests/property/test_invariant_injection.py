"""
Track C Phase 2 — invariant injection sanity tests.

CRITICAL purpose: when the RuleBasedStateMachine runs 10,000 examples
and reports zero violations, the natural question is "is the harness
actually capable of detecting violations, or are the rules written so
carefully that no violation is reachable?"

These tests INJECT KNOWN-BAD STATES directly and assert each invariant
correctly fires. If any of these tests pass without the assertion
firing, the corresponding state-machine invariant is dead code.

Pattern per invariant:
  1. Construct a fresh BridgeBrokerStateMachine
  2. Mutate broker / tracker state to violate the invariant
  3. Call the invariant method directly; expect AssertionError or
     pytest.Failed (whichever the @invariant decorator raises)

This is the harness self-test. Without it, "0 counterexamples" is
meaningless.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.property.simple_broker import SimpleBroker, OrderRecord, PositionRecord
from tests.property.test_bridge_state_machine import BridgeBrokerStateMachine


def _fresh_machine() -> BridgeBrokerStateMachine:
    """RuleBasedStateMachine subclasses normally instantiate via the
    Hypothesis runner. For direct invariant testing we construct one
    manually — just calling __init__ is sufficient since `setup` is
    a no-op."""
    m = BridgeBrokerStateMachine()
    return m


def _expect_invariant_fires(callable_, hint: str) -> None:
    """Wrap an invariant call; expect either AssertionError (assert fails)
    or pytest.fail() (Failed). Either is a successful detection."""
    try:
        callable_()
    except (AssertionError, BaseException) as e:  # pytest.Failed inherits BaseException
        if isinstance(e, (AssertionError,)) or "Failed" in type(e).__name__:
            return  # invariant fired correctly
        raise
    pytest.fail(f"Invariant FAILED to fire for: {hint}")


# ── I1 — tracker_matches_broker ────────────────────────────────────


def test_i1_fires_when_tracker_diverges_from_broker_filled_qty() -> None:
    m = _fresh_machine()
    # Submit an order via the broker directly
    response = asyncio.run(m.broker.submit_order({
        "symbol": "AAPL", "side": "buy", "qty": "100", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day", "order_class": "oto",
        "stop_loss": {"stop_price": "9.00"},
    }))
    oid = response["id"]
    m.tracker_positions["AAPL"] = {
        "entry_oid": oid, "stop_oid": "", "stop_price": 9.00,
        "filled_qty": 0, "closed": False,
    }
    m.order_to_ticker[oid] = "AAPL"
    # Broker says 50 filled; tracker still says 0 → I1 must fire
    m.broker.partial_fill(oid, 50, 10.00)
    # Don't sync tracker — that's the divergence
    _expect_invariant_fires(m.i1_tracker_matches_broker, "I1 broker=50, tracker=0")


# ── I2 — stop_matches_broker ───────────────────────────────────────


def test_i2_fires_when_tracker_stop_price_differs_from_broker() -> None:
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "TSLA", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "100.00", "time_in_force": "day", "order_class": "oto",
        "stop_loss": {"stop_price": "95.00"},
    }))
    eoid = response["id"]
    soid = response["legs"][0]["id"]
    # Activate the stop
    m.broker.orders[soid].status = "new"
    m.tracker_positions["TSLA"] = {
        "entry_oid": eoid, "stop_oid": soid, "stop_price": 90.00,  # WRONG — tracker says 90
        "filled_qty": 1, "closed": False,
    }
    m.order_to_ticker[eoid] = "TSLA"
    m.order_to_ticker[soid] = "TSLA"
    _expect_invariant_fires(m.i2_stop_matches_broker, "I2 tracker_sp=90, broker_sp=95")


# ── I3 — no_orders_during_halt ─────────────────────────────────────


def test_i3_fires_when_accepted_order_persists_for_halted_ticker() -> None:
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "NVDA", "side": "buy", "qty": "5", "type": "market",
        "limit_price": None, "time_in_force": "day",
    }))
    oid = response["id"]
    m.order_to_ticker[oid] = "NVDA"
    # Halt the ticker but DON'T flip the order to rejected (the bug)
    m.broker.halted_tickers.add("NVDA")
    # The order is still status=accepted — that's the violation
    assert m.broker.orders[oid].status == "accepted"
    _expect_invariant_fires(m.i3_no_orders_during_halt, "I3 NVDA halted but order accepted")


# ── I4 — no_negative_position ──────────────────────────────────────


def test_i4_fires_when_zero_qty_position_lingers() -> None:
    m = _fresh_machine()
    # Inject a degenerate zero-qty position record (should have been popped)
    m.broker.positions["AMZN"] = PositionRecord(
        symbol="AMZN", qty=0, avg_entry_price=100.0, side="long", cost_basis=0.0,
    )
    _expect_invariant_fires(m.i4_no_negative_position, "I4 AMZN qty=0 lingering")


# ── I5 — cumulative_fill_bounded ───────────────────────────────────


def test_i5_fires_when_filled_qty_exceeds_requested() -> None:
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "MSFT", "side": "buy", "qty": "10", "type": "market",
        "limit_price": None, "time_in_force": "day",
    }))
    oid = response["id"]
    # Force the violation directly (bypass partial_fill's bounds check)
    m.broker.orders[oid].filled_qty = 15  # > requested qty 10
    _expect_invariant_fires(m.i5_cumulative_fill_bounded, "I5 filled=15, requested=10")


# ── I6 — late_fill_on_rejected_order_canceled ──────────────────────


def test_i6_fires_when_bridge_rejected_but_broker_still_accepted() -> None:
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "AAPL", "side": "buy", "qty": "5", "type": "market",
        "limit_price": None, "time_in_force": "day",
    }))
    oid = response["id"]
    # Bridge gave up but did NOT cancel at broker → I6 violation
    m.bridge_rejected.add(oid)
    assert m.broker.orders[oid].status == "accepted"
    _expect_invariant_fires(
        m.i6_rejected_orders_canceled_at_broker,
        "I6 bridge rejected but broker status=accepted",
    )


# ── I7 — tranche_restructure_preserves_tightened_stop ──────────────


def test_i7_fires_when_broker_stop_loosened_below_tracker() -> None:
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "TSLA", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "100.00", "time_in_force": "day", "order_class": "oto",
        "stop_loss": {"stop_price": "95.00"},
    }))
    eoid = response["id"]
    soid = response["legs"][0]["id"]
    # Activate the stop
    m.broker.orders[soid].status = "new"
    # Tracker thinks the stop is tightened to $97; broker is loosened to $95.
    # I7 says broker_sp >= tracker_sp - 0.01 → 95 < 97 - 0.01 → fires
    m.tracker_positions["TSLA"] = {
        "entry_oid": eoid, "stop_oid": soid, "stop_price": 97.00,
        "filled_qty": 1, "closed": False,
    }
    m.order_to_ticker[eoid] = "TSLA"
    m.order_to_ticker[soid] = "TSLA"
    _expect_invariant_fires(
        m.i7_tranche_restructure_preserves_tightened_stop,
        "I7 tracker tightened to 97, broker still at 95",
    )


# ── I8 — equity_conservation ───────────────────────────────────────


def test_i8_fires_on_implausible_equity_drift() -> None:
    m = _fresh_machine()
    # Drive equity to an unphysical value
    m.broker.equity = m.initial_equity * 200  # 200x — beyond plausibility cap
    _expect_invariant_fires(m.i8_equity_conservation, "I8 equity 200x initial")


def test_i8_passes_on_modest_drift() -> None:
    """Negative test — small equity changes should NOT trigger I8."""
    m = _fresh_machine()
    m.broker.equity = m.initial_equity * 1.5  # 50% gain — plausible
    m.i8_equity_conservation()  # should NOT raise


# ── I9 — close_attempt_only_marks_closed_on_broker_2xx ─────────────


def test_i9_fires_on_failed_close_with_tracker_marked_closed() -> None:
    """Inject the canonical Bug Z state."""
    m = _fresh_machine()
    # Set up a position that "exists" at broker
    m.broker.positions["LIDR"] = PositionRecord(
        symbol="LIDR", qty=5264, avg_entry_price=2.30, side="long",
        cost_basis=5264 * 2.30,
    )
    m.tracker_positions["LIDR"] = {
        "entry_oid": "fake-eoid", "stop_oid": "fake-soid", "stop_price": 2.10,
        "filled_qty": 5264, "closed": True,  # ← BUG Z: marked closed despite failed close
    }
    # Log a failed close attempt (broker said no)
    m.close_attempts.append({
        "ticker": "LIDR", "succeeded": False, "broker_has_position": True,
    })
    _expect_invariant_fires(
        m.i9_close_attempt_only_marks_closed_on_broker_2xx,
        "I9 LIDR close failed but tracker.closed=True (Bug Z signature)",
    )


def test_i9_passes_on_failed_close_with_tracker_intact() -> None:
    """Negative test — the CORRECT behaviour: failed close, tracker NOT
    mutated. I9 must NOT fire."""
    m = _fresh_machine()
    m.broker.positions["LIDR"] = PositionRecord(
        symbol="LIDR", qty=5264, avg_entry_price=2.30, side="long",
        cost_basis=5264 * 2.30,
    )
    m.tracker_positions["LIDR"] = {
        "entry_oid": "fake-eoid", "stop_oid": "fake-soid", "stop_price": 2.10,
        "filled_qty": 5264, "closed": False,  # ← CORRECT: not marked closed
    }
    m.close_attempts.append({
        "ticker": "LIDR", "succeeded": False, "broker_has_position": True,
    })
    m.i9_close_attempt_only_marks_closed_on_broker_2xx()  # should NOT raise


def test_i9_passes_on_successful_close_with_tracker_marked_closed() -> None:
    """Negative test — the OTHER correct behaviour: successful close,
    tracker mutated. I9 must NOT fire."""
    m = _fresh_machine()
    m.tracker_positions["LIDR"] = {
        "entry_oid": "fake-eoid", "stop_oid": "fake-soid", "stop_price": 2.10,
        "filled_qty": 0, "closed": True,
    }
    m.close_attempts.append({
        "ticker": "LIDR", "succeeded": True, "broker_has_position": False,
    })
    m.i9_close_attempt_only_marks_closed_on_broker_2xx()  # should NOT raise


# ── Bug AA regression — negative equity from oracle fill discipline ─


# ── Knight-Capital invariants I10-I13 (D263-D266) ──────────────────


def test_i10_fires_on_opposite_side_entries() -> None:
    """Inject two entry orders for the same ticker, opposite sides,
    both in non-terminal status. I10 must fire."""
    m = _fresh_machine()
    response_buy = asyncio.run(m.broker.submit_order({
        "symbol": "AAPL", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "100.00", "time_in_force": "day",
    }))
    response_sell = asyncio.run(m.broker.submit_order({
        "symbol": "AAPL", "side": "sell", "qty": "10", "type": "limit",
        "limit_price": "100.00", "time_in_force": "day",
    }))
    m.order_to_ticker[response_buy["id"]] = "AAPL"
    m.order_to_ticker[response_sell["id"]] = "AAPL"
    _expect_invariant_fires(
        m.i10_no_opposite_side_orders_same_ticker,
        "I10: AAPL has both buy AND sell entry orders open",
    )


def test_i11_fires_on_excessive_equity_drift() -> None:
    """Inject equity at 2× initial → I11 fires (90% bound)."""
    m = _fresh_machine()
    m.broker.equity = m.initial_equity * 2.0  # 100% drift
    _expect_invariant_fires(
        m.i11_equity_drift_bounded_per_session,
        "I11: equity drift > 90% of initial",
    )


def test_i12a_fires_on_position_count_drift() -> None:
    """I12a (count): tracker says 2 open, broker says 1."""
    m = _fresh_machine()
    m.tracker_positions["X"] = {
        "entry_oid": "x1", "stop_oid": "", "stop_price": 1.0,
        "filled_qty": 5, "closed": False,
    }
    m.tracker_positions["Y"] = {
        "entry_oid": "y1", "stop_oid": "", "stop_price": 1.0,
        "filled_qty": 5, "closed": False,
    }
    # Broker only has X
    m.broker.positions["X"] = PositionRecord(
        symbol="X", qty=5, avg_entry_price=10.0, side="long", cost_basis=50.0,
    )
    _expect_invariant_fires(
        m.i12_position_count_matches_broker,
        "I12a: tracker open=2, broker=1",
    )


def test_i12b_fires_on_per_ticker_qty_drift() -> None:
    """I12b (qty drift): both sides agree there is 1 position for
    ticker X — but tracker says 100 shares while broker says 50.
    Pre-Track-C-v2 the count==count check passed (1 == 1) and the
    drift went undetected. The new per-ticker qty assertion fires."""
    m = _fresh_machine()
    m.tracker_positions["X"] = {
        "entry_oid": "x1", "stop_oid": "", "stop_price": 1.0,
        "filled_qty": 100, "closed": False,
    }
    m.broker.positions["X"] = PositionRecord(
        symbol="X", qty=50, avg_entry_price=10.0, side="long", cost_basis=500.0,
    )
    _expect_invariant_fires(
        m.i12_position_count_matches_broker,
        "I12b: tracker.filled_qty=100 != broker.position.qty=50",
    )


def test_i12c_fires_on_ghost_broker_position() -> None:
    """I12c (symmetric / Bug Z class): broker holds shares the tracker
    has no record of. Pre-Track-C-v2 this only fired if it changed the
    overall count; if there were N tracker positions and N broker
    positions but they were for DIFFERENT tickers, count==count would
    pass. The new symmetric assertion catches this."""
    m = _fresh_machine()
    # Tracker thinks it owns AAPL only
    m.tracker_positions["AAPL"] = {
        "entry_oid": "a1", "stop_oid": "", "stop_price": 1.0,
        "filled_qty": 50, "closed": False,
    }
    m.broker.positions["AAPL"] = PositionRecord(
        symbol="AAPL", qty=50, avg_entry_price=10.0, side="long", cost_basis=500.0,
    )
    # But the broker silently also has TSLA — Bug Z scenario:
    # the bridge thinks LIDR was sold but the broker still holds it.
    m.broker.positions["TSLA"] = PositionRecord(
        symbol="TSLA", qty=200, avg_entry_price=15.0, side="long", cost_basis=3000.0,
    )
    _expect_invariant_fires(
        m.i12_position_count_matches_broker,
        "I12c: broker has TSLA position but tracker has no entry — Bug Z ghost",
    )


def test_i13_fires_on_zombie_tracker_entry() -> None:
    """Inject the zombie pattern: broker filled the order, tracker missed
    the update."""
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "market",
        "limit_price": None, "time_in_force": "day",
    }))
    eoid = response["id"]
    # Force broker to fully fill (bypassing the harness's R3)
    m.broker.orders[eoid].filled_qty = 10
    m.broker.orders[eoid].filled_avg_price = 10.0
    m.broker.orders[eoid].status = "filled"
    # Tracker missed it — closed=False, filled_qty=0
    m.tracker_positions["X"] = {
        "entry_oid": eoid, "stop_oid": "",
        "stop_price": 9.0, "filled_qty": 0, "closed": False,
    }
    m.order_to_ticker[eoid] = "X"
    _expect_invariant_fires(
        m.i13_no_zero_qty_open_position,
        "I13: broker filled but tracker.filled_qty=0",
    )


# ── I14-I17 (D267-D270) injection tests ───────────────────────────


def test_i14_fires_on_duplicate_stop_oid_across_positions() -> None:
    """Inject two open positions sharing the same stop_oid → I14 fires."""
    m = _fresh_machine()
    m.tracker_positions["X"] = {
        "entry_oid": "ex", "stop_oid": "shared-stop",
        "stop_price": 9.0, "filled_qty": 5, "closed": False,
    }
    m.tracker_positions["Y"] = {
        "entry_oid": "ey", "stop_oid": "shared-stop",  # SAME stop_oid
        "stop_price": 19.0, "filled_qty": 5, "closed": False,
    }
    _expect_invariant_fires(
        m.i14_stop_oid_uniqueness,
        "I14: shared-stop on both X and Y",
    )


def test_i15a_fires_on_entry_qty_mutation() -> None:
    """Track C tightening (Track-C-after-AG, 2026-04-27): I15 v3 asserts
    `entry.qty == requested_qty` (was tautological `qty > 0` in v1).
    Inject a broker.qty != tracker.requested_qty mutation → I15a fires."""
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = response["id"]
    # Bridge requested 10; broker silently stored 7 (e.g. integer parse,
    # rounding, refactor that swapped the qty argument).
    m.broker.orders[eoid].qty = 7
    m.tracker_positions["X"] = {
        "entry_oid": eoid, "stop_oid": "",
        "stop_price": 9.0, "filled_qty": 0, "closed": False,
        "requested_qty": 10,
    }
    _expect_invariant_fires(
        m.i15_entry_qty_matches_request,
        "I15a: broker.qty=7 but tracker.requested_qty=10",
    )


# I15b under-protect (stop.qty < filled_qty as a single-point check) was
# attempted in i15 v3 but produced organic false positives in transient
# post-fill windows (r1+r2_partial+r9+r3_full → stop=1, filled=2 — the
# bridge would re-restructure on the next poll cycle). Replaced by I18
# below, which checks `stop.qty >= fills_seen_at_stop_create` — a
# snapshot taken at the moment the stop is created. That captures the
# under-protect-AT-CREATION class while letting subsequent fills sit
# in the grace window until the next r9 grows the stop.


def test_i18_fires_on_stop_under_sized_at_creation() -> None:
    """I18: bridge created a replacement stop that doesn't even cover
    the fills already seen at creation time → I18 fires.

    Concretely: tracker.fills_seen_at_stop_create=8 (8 shares were
    already filled when this stop was submitted) but broker.stop.qty=5
    (only 5 shares covered) — 3 shares would be silently unprotected
    on stop trigger. This is the canonical 'bridge.tranche_restructure
    sized the new stop too small' bug, and the canary pins it so any
    future regression that drops the snapshot logic surfaces here at
    injection-test time."""
    m = _fresh_machine()
    eresp = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = eresp["id"]
    sresp = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "sell", "qty": "5", "type": "stop",
        "stop_price": "9.00", "time_in_force": "day",
    }))
    soid = sresp["id"]
    m.tracker_positions["X"] = {
        "entry_oid": eoid, "stop_oid": soid,
        "stop_price": 9.0, "filled_qty": 8, "closed": False,
        "requested_qty": 10,
        # Bridge SAID it was creating the stop for 8 shares but
        # actually submitted qty=5 — silent under-protection at
        # creation time.
        "fills_seen_at_stop_create": 8,
    }
    _expect_invariant_fires(
        m.i18_stop_covers_fills_at_creation,
        "I18: stop.qty=5 < fills_seen_at_stop_create=8",
    )


def test_i18_passes_during_post_fill_grace_window() -> None:
    """I18 must NOT fire when subsequent fills come in after the stop
    was correctly sized at creation — that's the grace window before
    the bridge re-restructures via r9.

    Setup: tracker.fills_seen_at_stop_create=5 (stop created when 5
    shares were filled, sized for 5), then more fills came in
    (filled_qty=8). Stop is technically under-protective right now,
    but the bridge's discipline says 'wait for next r9 to grow it'.
    I18 should accept this state."""
    m = _fresh_machine()
    eresp = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = eresp["id"]
    sresp = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "sell", "qty": "5", "type": "stop",
        "stop_price": "9.00", "time_in_force": "day",
    }))
    soid = sresp["id"]
    m.tracker_positions["X"] = {
        "entry_oid": eoid, "stop_oid": soid,
        "stop_price": 9.0, "filled_qty": 8, "closed": False,
        "requested_qty": 10,
        # Stop was correctly sized for 5 at creation; subsequent
        # fills grew filled_qty to 8 (await next r9 to resize).
        "fills_seen_at_stop_create": 5,
    }
    # Should NOT raise — we're in the grace window.
    m.i18_stop_covers_fills_at_creation()


def test_i15b_fires_on_over_sized_stop() -> None:
    """I15b upper bound: stop.qty > tracker.requested_qty would over-sell
    on trigger, opening a SHORT via the close (Bug B class). This is
    the same defect class surfaced organically by the 2k Hypothesis run
    on 2026-04-27 — r9_tranche_restructure called with a TERMINAL entry
    order id resolved to the current tracker via order_to_ticker but
    sized the new stop from `self.broker.orders[old_entry].qty`. Fixed
    by adding a TERMINAL_STATES guard at the top of r9; this canary
    pins the failure mode so a future regression that drops the guard
    surfaces immediately at injection-test time."""
    m = _fresh_machine()
    eresp = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = eresp["id"]
    # Stop is 15 shares — bigger than the 10 we're allowed to hold.
    # On trigger, this sells 5 more than the position holds → SHORT.
    sresp = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "sell", "qty": "15", "type": "stop",
        "stop_price": "9.00", "time_in_force": "day",
    }))
    soid = sresp["id"]
    m.tracker_positions["X"] = {
        "entry_oid": eoid, "stop_oid": soid,
        "stop_price": 9.0, "filled_qty": 0, "closed": False,
        "requested_qty": 10,
    }
    _expect_invariant_fires(
        m.i15_entry_qty_matches_request,
        "I15b over-size: stop.qty=15 > requested_qty=10",
    )


def test_i16_fires_on_fill_outside_quote_band() -> None:
    """Inject a fill at 3x the limit price → I16 fires."""
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = response["id"]
    # Force a phantom fill at $30 (3x limit, outside ±50% band)
    m.broker.orders[eoid].filled_qty = 10
    m.broker.orders[eoid].filled_avg_price = 30.0
    m.broker.orders[eoid].status = "filled"
    _expect_invariant_fires(
        m.i16_fill_price_within_quote_band,
        "I16: fill @ $30 vs limit $10 (3x)",
    )


def test_i17_fires_on_filled_status_with_zero_qty() -> None:
    """Inject status=filled but filled_qty=0 → I17 fires."""
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = response["id"]
    # Inconsistent: status=filled but no fills
    m.broker.orders[eoid].status = "filled"
    m.broker.orders[eoid].filled_qty = 0
    _expect_invariant_fires(
        m.i17_terminal_status_consistent,
        "I17: status=filled but filled_qty=0",
    )


def test_i17_fires_on_rejected_status_with_fills() -> None:
    """Inject status=rejected but filled_qty>0 → I17 fires."""
    m = _fresh_machine()
    response = asyncio.run(m.broker.submit_order({
        "symbol": "X", "side": "buy", "qty": "10", "type": "limit",
        "limit_price": "10.00", "time_in_force": "day",
    }))
    eoid = response["id"]
    m.broker.orders[eoid].status = "rejected"
    m.broker.orders[eoid].filled_qty = 5  # impossible
    _expect_invariant_fires(
        m.i17_terminal_status_consistent,
        "I17: status=rejected but filled_qty=5",
    )


def test_bug_aa_three_rule_sequence_no_negative_equity() -> None:
    """Hypothesis-shrunk counterexample (2026-04-25, --hypothesis-seed=0
    @ HYP_MAX_EXAMPLES=2000): a 3-rule sequence drove broker.equity to
    -$89, exposing that SimpleBroker.partial_fill accepted any price
    parameter without enforcing the limit-price discipline a real broker
    enforces. Buy-limit @ $1.00 was filled at $102 (~100x worse than
    the limit), then the stop @ $0.90 fired for a -$100,089 realized
    loss against a $100,000 starting account.

    Post-Bug-AA fix: SimpleBroker.partial_fill rejects fills that
    violate the limit-price discipline (buy fills <= limit_price,
    sell fills >= limit_price). This sequence now becomes a no-op at
    R3 — the BrokerError is caught and tracker stays at filled_qty=0,
    so R8 has nothing to trigger.

    Pinned as a unit test so the regression coverage is independent
    of the .hypothesis/ database being present (e.g. fresh CI clone).
    """
    m = _fresh_machine()
    oid = m.r1_submit_entry(ticker="AAPL", qty=990, entry=1.00, stop=1.00)
    assert oid, "submit must succeed (ticker not halted, no double entry)"
    m.r3_full_fill(order_id=oid, price=102.00)  # post-fix: rejected by broker
    m.r8_trigger_stop(order_id=oid)             # post-fix: no-op (no fill = no stop)
    # I8 must hold — equity must not have gone negative
    m.i8_equity_conservation()
    # Plus broker must have correctly rejected the bad-price fill
    broker_order = m.broker.orders[oid]
    assert broker_order.filled_qty == 0, (
        f"Bug AA fix: buy-limit @ $1 must reject fill @ $102; "
        f"broker filled_qty={broker_order.filled_qty}"
    )
    m.teardown()
