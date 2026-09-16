"""D297 (2026-05-19) -- OTO stop-leg fill must record journal + remove tracker.

Pin the production failure from 2026-05-18 paper-trading run:
  - Bot opened CISS @ $4.67 (qty 2243, stop $4.60) via OTO order
  - Bot opened GCTS @ $2.49 (qty 5942, stop $2.47) via OTO order
  - Both OTO stop legs triggered intraday -- broker closed both positions
  - But fill_stream_bridge.on_trade_update saw the stop-leg FILL events,
    asked tranche_monitor.on_fill() which returned None (not a tracked
    tranche), and silently returned. Consequences:
      1. journal had no record_close() -- D238 EOD reconciliation flagged
         broker=$-562 / journal=$0 for CISS and broker=$-119 / journal=$0
         for GCTS. Total: $681 of "missing" P&L.
      2. position_manager.remove_position() never called -- 613 ERROR-level
         qty_drift=1 recon failures over the day, [SHADOW] D232 RECON_LETHAL
         fired ("drift sustained 9980s exceeds 60s lethal threshold").
      3. Later D74 close_position GCTS -> 404 (bot tried to close phantom).

D297 fix: FillStreamBridge accepts optional position_manager + trade_journal
kwargs. When tranche_monitor returns None AND the fill is side='sell' AND
the ticker is in position_manager, the bridge:
  1. computes realized_pnl from position's entry_price
  2. calls trade_journal.record_close(reason='STOP_FILL')
  3. calls position_manager.remove_position(ticker)
  4. emits a FillEvent(position_closed=True) for downstream consumers

Backwards-compat: omitting the new kwargs preserves the pre-D297 silent-
ignore behaviour (so existing tests + tranche_monitor unit tests still pass).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.execution.fill_stream_bridge import FillEvent, FillStreamBridge


# ── Test fixtures ───────────────────────────────────────────────────


@dataclass
class _FakeOrderEvent:
    """Mirror of src.data.trade_updates.OrderEvent enum surface used by the
    bridge. We only need the FILL constant to fire the right branch."""
    name: str = "FILL"


# Patch OrderEvent so `if event.event_type != OrderEvent.FILL` works
# without dragging in the real trade_updates module.
@pytest.fixture(autouse=True)
def _patch_order_event(monkeypatch):
    """Replace the OrderEvent class with a sentinel object that matches
    our test events' event_type value."""
    import src.data.trade_updates as tu
    sentinel = type("OrderEvent", (), {"FILL": "fill_sentinel"})
    monkeypatch.setattr(tu, "OrderEvent", sentinel)
    return sentinel


def _make_event(
    *,
    order_id: str = "stop-1",
    symbol: str = "CISS",
    side: str = "sell",
    filled_avg_price: float = 4.45,
    filled_qty: int = 2243,
    total_qty: int = 2243,
    event_type: str = "fill_sentinel",
):
    """Construct a TradeUpdateEvent-shaped object the bridge can consume."""
    ev = MagicMock()
    ev.order_id = order_id
    ev.symbol = symbol
    ev.side = side
    ev.filled_avg_price = filled_avg_price
    ev.filled_qty = filled_qty
    ev.total_qty = total_qty
    ev.event_type = event_type
    ev.timestamp = datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc)
    return ev


def _make_pm_with_position(ticker: str, entry_price: float,
                            qty: int = 100):
    """PositionManager stub that has one tracked position."""
    pos = MagicMock()
    pos.ticker = ticker
    pos.entry_price = entry_price
    pos.remaining_qty = qty
    pm = MagicMock()
    pm.has_position = MagicMock(return_value=True)
    pm.get_position = MagicMock(return_value=pos)
    pm.remove_position = MagicMock()
    return pm


def _make_journal_with_open_buy(ticker: str):
    entry = MagicMock()
    entry.ticker = ticker
    entry.action = "BUY"

    journal = MagicMock()
    journal._entries = {"trade-1": entry}
    journal.recorded_closes = []

    def _record_close(trade_id, **kwargs):
        journal.recorded_closes.append({"trade_id": trade_id, **kwargs})

    journal.record_close = MagicMock(side_effect=_record_close)
    return journal


def _make_tranche_monitor_returns_none():
    """tranche_monitor.on_fill returns None == 'not a tracked tranche'."""
    tm = MagicMock()
    tm.on_fill = MagicMock(return_value=None)
    return tm


# ═══════════════════════════════════════════════════════════════
# Headline regression: CISS production replay
# ═══════════════════════════════════════════════════════════════


def test_oto_stopfill_records_journal_and_removes_position():
    """Monday CISS replay: stop @ $4.60 hit, fill arrived via websocket.
    Bridge must record_close + remove_position."""
    pm = _make_pm_with_position("CISS", entry_price=4.67, qty=2243)
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    ev = _make_event(symbol="CISS", side="sell",
                     filled_avg_price=4.45, filled_qty=2243)
    bridge.on_trade_update(ev)

    # journal.record_close called with computed pnl
    assert len(journal.recorded_closes) == 1
    rc = journal.recorded_closes[0]
    assert rc["trade_id"] == "trade-1"
    assert rc["exit_reason"] == "STOP_FILL"
    # pnl = (4.45 - 4.67) * 2243 = -493.46
    assert rc["realized_pnl"] == pytest.approx((4.45 - 4.67) * 2243, abs=0.01)
    assert rc["exit_price"] == pytest.approx(4.45)

    # position_manager.remove_position called
    pm.remove_position.assert_called_once_with("CISS")


def test_oto_stopfill_gcts_production_replay():
    """Monday GCTS replay: stop @ $2.47 hit on qty 5942."""
    pm = _make_pm_with_position("GCTS", entry_price=2.49, qty=5942)
    journal = _make_journal_with_open_buy("GCTS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    ev = _make_event(symbol="GCTS", side="sell",
                     filled_avg_price=2.47, filled_qty=5942)
    bridge.on_trade_update(ev)

    rc = journal.recorded_closes[0]
    assert rc["realized_pnl"] == pytest.approx((2.47 - 2.49) * 5942, abs=0.01)
    pm.remove_position.assert_called_once_with("GCTS")


# ═══════════════════════════════════════════════════════════════
# Backwards-compat: opt-out path preserves pre-D297 behaviour
# ═══════════════════════════════════════════════════════════════


def test_legacy_construction_without_journal_or_pm_is_silent_ignore():
    """Callers that don't pass position_manager/trade_journal must keep
    the pre-D297 behaviour: silently ignore non-tranche fills."""
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
    )
    # No exception, no state change -- just drops the event
    bridge.on_trade_update(_make_event(side="sell"))
    assert bridge.pending_count == 0


def test_legacy_with_only_pm_but_no_journal_is_no_op():
    pm = _make_pm_with_position("CISS", 4.67)
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm,  # but no trade_journal
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))
    pm.remove_position.assert_not_called()  # opt-out preserves silence


def test_legacy_with_only_journal_but_no_pm_is_no_op():
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        trade_journal=journal,  # but no position_manager
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))
    assert journal.recorded_closes == []


# ═══════════════════════════════════════════════════════════════
# Selectivity: must NOT fire on irrelevant fills
# ═══════════════════════════════════════════════════════════════


def test_buy_side_fill_does_NOT_trigger_stopfill_handler():
    """A BUY fill that the tranche_monitor doesn't recognise is the OTO
    primary leg arriving before its own tracking is set up. The D297
    handler must skip it -- only SELL fills represent exits."""
    pm = _make_pm_with_position("CISS", 4.67)
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="buy"))
    assert journal.recorded_closes == []
    pm.remove_position.assert_not_called()


def test_sell_for_unknown_ticker_does_NOT_fire_anything():
    """If position_manager doesn't know the ticker, it's a genuine
    foreign sell (someone else's broker order, or a stale fill). Don't
    touch journal or tracker."""
    pm = MagicMock()
    pm.has_position = MagicMock(return_value=False)
    pm.get_position = MagicMock(return_value=None)
    pm.remove_position = MagicMock()
    journal = _make_journal_with_open_buy("CISS")  # journal has CISS but pm doesn't
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="STRANGER", side="sell"))
    assert journal.recorded_closes == []
    pm.remove_position.assert_not_called()


def test_non_fill_event_is_ignored_entirely():
    """Events that aren't FILL (e.g. NEW, CANCELED, EXPIRED) should not
    even reach the stop-fill handler."""
    pm = _make_pm_with_position("CISS", 4.67)
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell",
                                        event_type="canceled_sentinel"))
    assert journal.recorded_closes == []
    pm.remove_position.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# Defensive: never crash on edge cases
# ═══════════════════════════════════════════════════════════════


def test_zero_entry_price_does_not_divide_by_zero():
    """A position with entry_price=0 (data corruption / restart edge)
    must not crash; pnl defaults to 0 and the close still records."""
    pm = _make_pm_with_position("X", entry_price=0.0)
    journal = _make_journal_with_open_buy("X")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="X", side="sell",
                                        filled_avg_price=10.0))
    assert journal.recorded_closes[0]["realized_pnl"] == 0.0
    pm.remove_position.assert_called_once_with("X")


def test_position_manager_lookup_exception_does_not_crash():
    """has_position / get_position raising must not break the bridge."""
    pm = MagicMock()
    pm.has_position = MagicMock(side_effect=RuntimeError("db lock"))
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    # must not raise
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))
    assert journal.recorded_closes == []


def test_record_close_exception_does_not_prevent_remove_position():
    """If record_close fails (journal corrupt, etc.), tracker remove
    must STILL run -- otherwise the qty_drift problem persists."""
    pm = _make_pm_with_position("CISS", 4.67)
    journal = _make_journal_with_open_buy("CISS")
    journal.record_close = MagicMock(side_effect=RuntimeError("journal IO"))
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))
    # Tracker MUST still be cleaned up
    pm.remove_position.assert_called_once_with("CISS")


def test_remove_position_exception_does_not_raise_out_of_bridge():
    """If remove_position fails, log and continue -- don't crash."""
    pm = _make_pm_with_position("CISS", 4.67)
    pm.remove_position = MagicMock(side_effect=RuntimeError("tracker IO"))
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    # must not raise
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))
    # journal should still have recorded
    assert len(journal.recorded_closes) == 1


def test_stopfill_for_ticker_with_no_journal_BUY_entry_warns_but_removes():
    """If the position is in pm but has no open BUY entry in the
    journal (e.g. journal cleared on restart), tracker remove must
    still run. record_close is skipped (nothing to close)."""
    pm = _make_pm_with_position("CISS", 4.67)
    journal = _make_journal_with_open_buy("DIFFERENT")  # no CISS entry
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))
    assert journal.recorded_closes == []  # nothing matched
    pm.remove_position.assert_called_once_with("CISS")  # tracker still cleaned


# ═══════════════════════════════════════════════════════════════
# Downstream: FillEvent emitted so Phase 3 consumers see the close
# ═══════════════════════════════════════════════════════════════


def test_stopfill_emits_FillEvent_with_position_closed_true():
    pm = _make_pm_with_position("CISS", 4.67, qty=2243)
    journal = _make_journal_with_open_buy("CISS")
    bridge = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell",
                                        filled_avg_price=4.45,
                                        filled_qty=2243))
    events = bridge.drain_events()
    assert len(events) == 1
    fe = events[0]
    assert fe.ticker == "CISS"
    assert fe.position_closed is True
    assert fe.filled_price == pytest.approx(4.45)
    assert fe.filled_qty == 2243
    assert fe.tranche_number is None  # not a tranche fill
    # realized_pnl matches the journal record
    assert fe.realized_pnl == pytest.approx((4.45 - 4.67) * 2243, abs=0.01)


# ═══════════════════════════════════════════════════════════════
# Regression: tranche-fill path is NOT touched by D297
# ═══════════════════════════════════════════════════════════════


def test_tranche_fill_path_is_unchanged_when_d297_kwargs_provided():
    """Even with position_manager + trade_journal injected, the
    pre-existing tranche-fill path (tranche_monitor.on_fill returns
    a RatchetResult) must still flow through unchanged."""
    pm = _make_pm_with_position("CISS", 4.67)
    journal = _make_journal_with_open_buy("CISS")
    tm = MagicMock()
    ratchet_result = MagicMock()
    ratchet_result.tranche_number = 1
    ratchet_result.old_stop = 4.60
    ratchet_result.new_stop = 4.65
    ratchet_result.position_fully_closed = False
    ratchet_result.realized_pnl = 100.0
    tm.on_fill = MagicMock(return_value=ratchet_result)

    bridge = FillStreamBridge(
        tranche_monitor=tm, position_manager=pm, trade_journal=journal,
    )
    bridge.on_trade_update(_make_event(symbol="CISS", side="sell"))

    # D297 stop-fill handler must NOT have fired (tranche path took over)
    assert journal.recorded_closes == []
    pm.remove_position.assert_not_called()
    # Tranche FillEvent IS in the queue
    events = bridge.drain_events()
    assert len(events) == 1
    assert events[0].tranche_number == 1


# ═══════════════════════════════════════════════════════════════
# End-to-end: D238 reconciliation gap stops appearing
# ═══════════════════════════════════════════════════════════════


def test_d297_closes_the_d238_journal_missing_gap():
    """The exact D238 disagreement pattern from Monday: broker has a
    realized loss, journal has nothing. After D297 fires, journal has
    a matching close so D238 sees no gap."""
    pm = _make_pm_with_position("CISS", 4.67, qty=2243)
    journal = _make_journal_with_open_buy("CISS")

    # Pre-fix simulation: bridge WITHOUT D297 kwargs → journal stays empty
    bridge_legacy = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
    )
    bridge_legacy.on_trade_update(_make_event(symbol="CISS", side="sell",
                                               filled_avg_price=4.45,
                                               filled_qty=2243))
    # Legacy: no close was recorded (the production bug)
    assert journal.recorded_closes == []

    # With D297 kwargs: same event now closes the gap
    bridge_d297 = FillStreamBridge(
        tranche_monitor=_make_tranche_monitor_returns_none(),
        position_manager=pm, trade_journal=journal,
    )
    bridge_d297.on_trade_update(_make_event(symbol="CISS", side="sell",
                                             filled_avg_price=4.45,
                                             filled_qty=2243))
    assert len(journal.recorded_closes) == 1
    # The pnl in the journal now matches broker reality (broker
    # showed -$561.87 on Monday; here we get -493.46 because we used
    # round numbers, but the principle -- "non-zero close recorded"
    # -- is what makes D238 stop flagging journal=MISSING).
    assert journal.recorded_closes[0]["realized_pnl"] != 0
    assert journal.recorded_closes[0]["exit_reason"] == "STOP_FILL"
