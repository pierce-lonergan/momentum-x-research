"""doc 270 (2026-06-10) — multi-leg exit booking: HWH live-specimen replay.

THE BUG (journal/broker delta −$970.94 on 2026-06-10):
  HWH position 16,437 sh @ $2.01 with an OTO stop @ $1.94. At 10:17:21 ET
  the D165 tranche-taker fired a MARKET sell for 5,479 sh (T1, +3% promo
  target); it filled @ $2.02 at 10:17:22 (order cfd01e6a). The protective
  stop was resubmitted for the remaining 10,958 sh (order caff0d75) and
  filled in two prints at 10:17:53-54: 4,715 @ $1.91 (partial_fill) +
  6,243 @ $1.93 (fill, cumulative avg 1.921394).

  fill_stream_bridge._handle_oto_stop_fill treated the FIRST sell fill
  (the 5,479-share D165 leg) as the TERMINAL close of the WHOLE position:
  journal got exactly ONE close row (pnl=+54.79, exit_reason=STOP_FILL,
  exit_price=2.02) and remove_position() evicted the tracker entry — so
  when the real stop wave arrived 31s later, has_position() was False and
  the −$970.94 stop loss was never booked anywhere. Broker net: −$916.15.

  Production evidence (main tree, 2026-06-10):
    data/ops/raw_fills_2026-06-10.jsonl        — the 6 HWH prints
    data/journals/journal_2026-06-10_120123.jsonl — the single close row
    logs/momentum_2026-06-10.log:25402-25409   — D165 T1 HIT → D297
        record_close(+54.79 STOP_FILL) → remove_position → D165 booking
        → stop resubmit; 25425 — D231 RECON GHOST broker_qty=10958.

THE FIX (doc 270, fill_stream_bridge.py):
  Partial-aware, order-cumulative leg booking: each sell fill books ONLY
  its increment ((leg_px − entry) × leg_qty) with the correct reason
  (STOP_FILL when the order_id matches the tracked stop, or on terminal;
  PARTIAL_EXIT otherwise), never removes the position until the broker's
  data-level position_qty hits 0 (fallback: tracker remaining_qty), and
  dedupes broker execution_ids so replayed websocket events book nothing.

These tests replay the EXACT HWH event sequence through REAL components
(PositionManager, TradeJournal, TrancheExitMonitor, StopResubmitter,
parse_trade_update) — only the broker client is a mock.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config.settings import ExecutionConfig
from src.analysis.trade_journal import TradeJournal
from src.data.trade_updates import parse_trade_update
from src.execution.fill_stream_bridge import FillStreamBridge
from src.execution.position_manager import ManagedPosition, PositionManager
from src.execution.stop_resubmitter import StopResubmitter
from src.execution.tranche_monitor import TrancheExitMonitor


# ── HWH constants (from raw_fills_2026-06-10.jsonl) ─────────────────────

ENTRY_PX = 2.01
TOTAL_QTY = 16_437

T1_OID = "cfd01e6a-6beb-412c-97b3-a1597e255a8c"   # D165 market sell
STOP0_OID = "29fb8dac-b65a-4037-9055-7ba489d22711"  # original OTO stop (canceled)
STOP1_OID = "caff0d75-ad3e-4c8d-98d0-7cda813b3156"  # resubmitted stop (filled)

T1_QTY, T1_PX = 5_479, 2.02
S1_QTY, S1_PX = 4_715, 1.91          # stop partial print
S2_QTY, S2_PX = 6_243, 1.93          # stop terminal print
STOP_CUM_QTY = S1_QTY + S2_QTY       # 10,958
STOP_CUM_AVG = 1.921394              # broker cumulative avg at terminal

# Broker truth: realized net across all six prints
BROKER_NET = (
    T1_QTY * (T1_PX - ENTRY_PX)
    + S1_QTY * (S1_PX - ENTRY_PX)
    + S2_QTY * (S2_PX - ENTRY_PX)
)  # = +54.79 − 471.50 − 499.44 = −916.15


# ── Alpaca-shaped event payloads (mirror raw_fills JSONL) ────────────────


def _ws_msg(
    *,
    event: str,
    oid: str,
    order_qty: int,
    cum_filled_qty: int,
    cum_avg_price: float,
    print_price: float,
    print_qty: int,
    position_qty: int,
    execution_id: str,
    order_type: str = "market",
    side: str = "sell",
    symbol: str = "HWH",
    ts: str = "2026-06-10T14:17:22.118073631Z",
):
    """Build a trade_updates message exactly as Alpaca delivers it:
    order-level filled_qty/filled_avg_price are CUMULATIVE; data-level
    price/qty are this print; data-level position_qty is the broker's
    remaining share count AFTER the print."""
    return {
        "stream": "trade_updates",
        "data": {
            "event": event,
            "execution_id": execution_id,
            "price": str(print_price),
            "qty": str(print_qty),
            "position_qty": str(position_qty),
            "order": {
                "id": oid,
                "symbol": symbol,
                "asset_class": "us_equity",
                "qty": str(order_qty),
                "filled_qty": str(cum_filled_qty),
                "filled_avg_price": str(cum_avg_price),
                "order_class": "",
                "order_type": order_type,
                "type": order_type,
                "side": side,
                "time_in_force": "day",
                "status": "filled" if event == "fill" else "partially_filled",
                "filled_at": ts,
                "created_at": ts,
            },
        },
    }


def _ev_t1():
    """10:17:22 ET — D165 tranche-take market sell fills 5,479 @ 2.02."""
    return parse_trade_update(_ws_msg(
        event="fill", oid=T1_OID, order_qty=T1_QTY,
        cum_filled_qty=T1_QTY, cum_avg_price=T1_PX,
        print_price=T1_PX, print_qty=T1_QTY,
        position_qty=TOTAL_QTY - T1_QTY,            # 10,958 left at broker
        execution_id="afcd6bb1-6613-4c9b-b3b5-d9a73816522b",
        ts="2026-06-10T14:17:22.118073631Z",
    ))


def _ev_stop_partial():
    """10:17:53 ET — resubmitted stop partial print 4,715 @ 1.91."""
    return parse_trade_update(_ws_msg(
        event="partial_fill", oid=STOP1_OID, order_qty=STOP_CUM_QTY,
        cum_filled_qty=S1_QTY, cum_avg_price=S1_PX,
        print_price=S1_PX, print_qty=S1_QTY,
        position_qty=S2_QTY,                        # 6,243 left at broker
        execution_id="517fb0e1-375f-47ac-b558-d7f215e058ab",
        order_type="stop",
        ts="2026-06-10T14:17:53.714296361Z",
    ))


def _ev_stop_terminal():
    """10:17:54 ET — stop terminal print 6,243 @ 1.93 (cum avg 1.921394)."""
    return parse_trade_update(_ws_msg(
        event="fill", oid=STOP1_OID, order_qty=STOP_CUM_QTY,
        cum_filled_qty=STOP_CUM_QTY, cum_avg_price=STOP_CUM_AVG,
        print_price=S2_PX, print_qty=S2_QTY,
        position_qty=0,                             # flat at broker
        execution_id="7f70e177-366c-4403-9edf-6f0dd63a555b",
        order_type="stop",
        ts="2026-06-10T14:17:54.406389738Z",
    ))


# ── Real-component harness ───────────────────────────────────────────────


def _build_harness(tmp_path):
    """Real PM + journal + tranche monitor + stop resubmitter; mock client."""
    pm = PositionManager(config=ExecutionConfig(), starting_equity=215_885.0)
    pos = ManagedPosition(
        ticker="HWH",
        qty=TOTAL_QTY,
        entry_price=ENTRY_PX,
        signal_price=1.97,
        stop_loss=1.94,
        target_prices=[2.03, 2.09, 2.17],
        order_id="fb6d3706-a3d0-40ff-84c0-5159e499a1d5",
        stop_order_id=STOP0_OID,
    )
    pm.add_position(pos)

    journal = TradeJournal(session_date="2026-06-10", journal_dir=tmp_path)
    candidate = SimpleNamespace(
        ticker="HWH", current_price=1.97, previous_close=1.25,
        gap_pct=60.8, rvol=1.8,
    )
    entry = journal.create_entry("509dc9e5-HWH", candidate, phase="RESCAN")
    entry.action = "BUY"
    entry.entry_price = ENTRY_PX
    journal.record_fill(
        "509dc9e5-HWH",
        order_id="fb6d3706-a3d0-40ff-84c0-5159e499a1d5",
        fill_price=ENTRY_PX, fill_qty=TOTAL_QTY,
    )

    resubmitter = StopResubmitter(client=MagicMock())
    resubmitter.register_stop(
        "HWH", order_id=STOP0_OID, stop_price=1.94, qty=TOTAL_QTY,
    )
    monitor = TrancheExitMonitor(position_manager=pm, stop_resubmitter=resubmitter)

    bridge = FillStreamBridge(
        tranche_monitor=monitor,
        stop_resubmitter=resubmitter,
        position_manager=pm,
        trade_journal=journal,
    )
    return pm, pos, journal, resubmitter, bridge


def _simulate_d165_owner_actions(pm, pos, resubmitter):
    """What main.py's D165 block does after its market sell confirms
    (main.py ~6043-6100): decrement remaining_qty, book the PM sinks,
    cancel the old stop and resubmit one sized for the remainder."""
    pos.remaining_qty = max(0, pos.remaining_qty - T1_QTY)
    pos.realized_pnl += T1_QTY * (T1_PX - ENTRY_PX)
    pm.record_realized_pnl(T1_QTY * (T1_PX - ENTRY_PX))
    resubmitter.remove("HWH")
    resubmitter.register_stop(
        "HWH", order_id=STOP1_OID, stop_price=1.94, qty=STOP_CUM_QTY,
    )
    pos.stop_order_id = STOP1_OID


def _closed_rows(journal):
    """All journal rows carrying a realized close, in insertion order."""
    return [
        e for e in journal._entries.values()
        if e.realized_pnl is not None
    ]


# ═════════════════════════════════════════════════════════════════════════
# 1. The exact HWH replay — the headline regression
# ═════════════════════════════════════════════════════════════════════════


def test_hwh_replay_books_every_leg_and_matches_broker_net(tmp_path):
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)

    # ── Leg 1: D165 tranche market sell fills (websocket beats D165) ──
    bridge.on_trade_update(_ev_t1())

    rows = _closed_rows(journal)
    assert len(rows) == 1
    assert rows[0].realized_pnl == pytest.approx(54.79, abs=0.01)
    assert rows[0].exit_price == pytest.approx(2.02, abs=0.001)
    # The pre-fix bug stamped STOP_FILL here; the leg is NOT a stop fill.
    assert rows[0].exit_reason == "PARTIAL_EXIT"
    # Position must SURVIVE the partial leg (pre-fix: removed → ghost)
    assert pm.has_position("HWH"), "first leg must not terminal the position"
    # Tracker untouched by the bridge for bot-initiated sells: the D165
    # main-loop path owns the decrement (double-decrement would shrink
    # the re-armed stop and leave shares naked).
    assert pos.remaining_qty == TOTAL_QTY

    # ── D165 main-loop booking runs (the owner) ──
    _simulate_d165_owner_actions(pm, pos, resubmitter)
    assert pos.remaining_qty == STOP_CUM_QTY

    # ── Leg 2: stop partial print 4,715 @ 1.91 ──
    bridge.on_trade_update(_ev_stop_partial())

    rows = _closed_rows(journal)
    assert len(rows) == 2
    assert rows[1].realized_pnl == pytest.approx(-471.50, abs=0.01)
    assert rows[1].exit_reason == "STOP_FILL"
    assert rows[1].exit_price == pytest.approx(1.91, abs=0.001)
    assert pm.has_position("HWH"), "partial stop print must not terminal"
    # Broker-initiated stop print: bridge owns the decrement
    assert pos.remaining_qty == S2_QTY

    # ── Leg 3: stop terminal print 6,243 @ 1.93 (cum avg 1.921394) ──
    bridge.on_trade_update(_ev_stop_terminal())

    rows = _closed_rows(journal)
    assert len(rows) == 3
    # Increment math: cum notional − booked notional → this print's px
    assert rows[2].realized_pnl == pytest.approx(
        S2_QTY * (S2_PX - ENTRY_PX), abs=0.25,
    )
    assert rows[2].exit_reason == "STOP_FILL"
    # TERMINAL only now (broker position_qty == 0)
    assert not pm.has_position("HWH")

    # ── The money assertion: journal == broker ──
    journal_total = sum(e.realized_pnl for e in rows)
    assert journal_total == pytest.approx(BROKER_NET, abs=0.02)
    assert journal_total == pytest.approx(-916.18, abs=0.1)  # briefing figure

    # TWO+ distinct bookings with correct reasons
    reasons = [e.exit_reason for e in rows]
    assert len(rows) >= 2
    assert reasons.count("STOP_FILL") == 2
    assert reasons.count("PARTIAL_EXIT") == 1


def test_hwh_replayed_events_do_not_double_book(tmp_path):
    """Websocket reconnects redeliver events. Same execution_ids must
    book NOTHING the second (and third) time."""
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)

    bridge.on_trade_update(_ev_t1())
    _simulate_d165_owner_actions(pm, pos, resubmitter)
    bridge.on_trade_update(_ev_stop_partial())
    bridge.on_trade_update(_ev_stop_terminal())

    rows = _closed_rows(journal)
    total_before = sum(e.realized_pnl for e in rows)
    n_before = len(rows)

    # Full replay, twice, in original order
    for _ in range(2):
        bridge.on_trade_update(_ev_t1())
        bridge.on_trade_update(_ev_stop_partial())
        bridge.on_trade_update(_ev_stop_terminal())

    rows_after = _closed_rows(journal)
    assert len(rows_after) == n_before
    assert sum(e.realized_pnl for e in rows_after) == pytest.approx(
        total_before, abs=0.001,
    )
    assert not pm.has_position("HWH")


def test_hwh_out_of_order_delivery_books_same_total(tmp_path):
    """Terminal stop fill arriving BEFORE its partial print (out-of-order
    websocket delivery): the cumulative math books the whole stop wave at
    the terminal event; the late partial books zero new shares. The total
    must still equal broker net."""
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)

    bridge.on_trade_update(_ev_t1())
    _simulate_d165_owner_actions(pm, pos, resubmitter)

    # Terminal first: books the FULL stop order (10,958 @ cum avg 1.921394)
    bridge.on_trade_update(_ev_stop_terminal())
    rows = _closed_rows(journal)
    assert len(rows) == 2
    assert rows[1].realized_pnl == pytest.approx(
        STOP_CUM_QTY * (STOP_CUM_AVG - ENTRY_PX), abs=0.02,
    )
    assert not pm.has_position("HWH")

    # Late partial: cumulative qty 4,715 ≤ already-booked 10,958 → no-op
    bridge.on_trade_update(_ev_stop_partial())
    rows = _closed_rows(journal)
    assert len(rows) == 2
    assert sum(e.realized_pnl for e in rows) == pytest.approx(
        BROKER_NET, abs=0.02,
    )


def test_stop_fill_before_tranche_fill_race(tmp_path):
    """Race the briefing asks about: the ORIGINAL full-size stop fills
    FIRST (no tranche ever sold). One terminal STOP_FILL booking for the
    whole position; a stray later sell fill for the (now flat) ticker
    books nothing and does not crash."""
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)

    full_stop = parse_trade_update(_ws_msg(
        event="fill", oid=STOP0_OID, order_qty=TOTAL_QTY,
        cum_filled_qty=TOTAL_QTY, cum_avg_price=1.93,
        print_price=1.93, print_qty=TOTAL_QTY,
        position_qty=0,
        execution_id="ffffffff-0000-0000-0000-000000000001",
        order_type="stop",
    ))
    bridge.on_trade_update(full_stop)

    rows = _closed_rows(journal)
    assert len(rows) == 1
    assert rows[0].exit_reason == "STOP_FILL"
    assert rows[0].realized_pnl == pytest.approx(
        TOTAL_QTY * (1.93 - ENTRY_PX), abs=0.02,
    )
    assert not pm.has_position("HWH")

    # A late tranche sell fill for the flat ticker: ignored, no crash
    bridge.on_trade_update(_ev_t1())
    assert len(_closed_rows(journal)) == 1


def test_partial_legs_emit_fill_events_terminal_only_on_last(tmp_path):
    """Downstream consumers (Phase 3 drain) must see each leg, with
    position_closed=True ONLY on the terminal leg."""
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)

    bridge.on_trade_update(_ev_t1())
    _simulate_d165_owner_actions(pm, pos, resubmitter)
    bridge.on_trade_update(_ev_stop_partial())
    bridge.on_trade_update(_ev_stop_terminal())

    events = bridge.drain_events()
    assert len(events) == 3
    assert [e.position_closed for e in events] == [False, False, True]
    assert [e.exit_reason for e in events] == [
        "PARTIAL_EXIT", "STOP_FILL", "STOP_FILL",
    ]
    assert events[0].filled_qty == T1_QTY
    assert events[1].filled_qty == S1_QTY
    assert events[2].filled_qty == S2_QTY
    assert sum(e.realized_pnl for e in events) == pytest.approx(
        BROKER_NET, abs=0.02,
    )


def test_registered_tranche_partial_print_left_to_monitor(tmp_path):
    """A partial print of a REGISTERED tranche limit must NOT be booked
    by the doc-270 handler — the tranche monitor books the whole order
    exactly once at its terminal fill (booking prints here would
    double-book)."""
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)
    monitor = bridge._tranche_monitor
    monitor.register_tranche_order(
        order_id="tranche-T1-oid", ticker="HWH",
        tranche_number=1, target_price=2.03, qty=T1_QTY,
    )

    partial_of_tranche = parse_trade_update(_ws_msg(
        event="partial_fill", oid="tranche-T1-oid", order_qty=T1_QTY,
        cum_filled_qty=2_000, cum_avg_price=2.03,
        print_price=2.03, print_qty=2_000,
        position_qty=TOTAL_QTY - 2_000,
        execution_id="ffffffff-0000-0000-0000-000000000002",
        order_type="limit",
    ))
    bridge.on_trade_update(partial_of_tranche)

    assert _closed_rows(journal) == []
    assert pm.has_position("HWH")
    assert pos.remaining_qty == TOTAL_QTY


def test_fallback_terminal_detection_without_position_qty(tmp_path):
    """Synthetic events without the data-level position_qty (older
    payloads / test doubles) fall back to tracker remaining_qty for
    terminal detection — the D297 CISS/GCTS contract."""
    pm, pos, journal, resubmitter, bridge = _build_harness(tmp_path)

    msg = _ws_msg(
        event="fill", oid=STOP0_OID, order_qty=TOTAL_QTY,
        cum_filled_qty=TOTAL_QTY, cum_avg_price=1.94,
        print_price=1.94, print_qty=TOTAL_QTY,
        position_qty=0,
        execution_id="ffffffff-0000-0000-0000-000000000003",
        order_type="stop",
    )
    del msg["data"]["position_qty"]
    ev = parse_trade_update(msg)
    assert ev.position_qty is None

    bridge.on_trade_update(ev)
    rows = _closed_rows(journal)
    assert len(rows) == 1
    assert rows[0].exit_reason == "STOP_FILL"
    assert not pm.has_position("HWH")
