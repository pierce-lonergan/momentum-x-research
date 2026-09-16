"""doc 222 (B1 Phase 1-3): the event-sourced ledger gauntlet (gates 1-3 + the structural rule).

The whole point of B1: phantom P&L is STRUCTURALLY impossible. These pin the invariants:
  - no FILL books without a broker_event_id (the structural rule)
  - dedup: replaying a fill is a no-op (stream-reconnect safe)
  - fold purity: P&L is a pure function of the fill events
  - the 6/1 phantom: ORDER events without a FILL book $0
"""
from __future__ import annotations

import pytest

from src.ops.ledger import (
    Ledger, ORDER_SUBMITTED, ORDER_ACKED, ORDER_REJECTED,
    FILL_PARTIAL, FILL_COMPLETE,
)
from src.data.trade_updates import parse_trade_update, _compute_broker_event_id


@pytest.fixture
def led(tmp_path):
    return Ledger(db_path=tmp_path / "ledger.db")


# ── the structural rule: no fill books without a broker_event_id ──

def test_fill_without_broker_event_id_is_rejected(led):
    r = led.append(event_type=FILL_COMPLETE, ticker="X", wall_ts="t", side="sell",
                   qty=100, price=10.0)  # no broker_event_id
    assert r == "rejected:fill_without_broker_event_id"
    assert led.realized_pnl("X") == 0.0   # nothing booked


def test_order_events_never_book_pnl(led):
    """The 6/1 phantom in microcosm: ORDER_SUBMITTED/ACKED/REJECTED book NOTHING."""
    led.append(event_type=ORDER_SUBMITTED, ticker="STG", wall_ts="t", side="sell", qty=3382)
    led.append(event_type=ORDER_ACKED, ticker="STG", wall_ts="t", side="sell", qty=3382)
    led.append(event_type=ORDER_REJECTED, ticker="STG", wall_ts="t", side="sell", qty=3382)
    # close 403'd, never filled -> NO fill event -> the fold books $0 (not +$304.20)
    assert led.realized_pnl("STG") == 0.0
    assert led.position("STG").qty == 0


# ── dedup: replaying a fill is a no-op (stream reconnect / duplicate delivery) ──

def test_duplicate_fill_is_idempotent(led):
    kw = dict(event_type=FILL_COMPLETE, ticker="X", wall_ts="t", side="buy",
              broker_event_id="exec:abc", qty=100, price=5.0)
    assert led.append(**kw) == "appended"
    assert led.append(**kw) == "duplicate"      # same broker_event_id -> no-op
    assert led.append(**kw) == "duplicate"
    assert led.position("X").qty == 100          # counted ONCE, not 300


# ── fold: realized P&L is computed correctly off confirmed fills ──

def test_buy_then_sell_books_realized_pnl(led):
    led.append(event_type=FILL_COMPLETE, ticker="X", wall_ts="t1", side="buy",
               broker_event_id="exec:1", qty=100, price=10.0)
    assert led.position("X").qty == 100
    assert led.realized_pnl("X") == 0.0          # open position, nothing realized yet
    led.append(event_type=FILL_COMPLETE, ticker="X", wall_ts="t2", side="sell",
               broker_event_id="exec:2", qty=100, price=11.0)
    assert led.position("X").qty == 0
    assert led.realized_pnl("X") == 100.0        # (11-10)*100


def test_partial_fills_accumulate(led):
    led.append(event_type=FILL_COMPLETE, ticker="X", wall_ts="t1", side="buy",
               broker_event_id="e1", qty=200, price=10.0)
    led.append(event_type=FILL_PARTIAL, ticker="X", wall_ts="t2", side="sell",
               broker_event_id="e2", qty=100, price=12.0)   # realize 100 @ +2
    led.append(event_type=FILL_PARTIAL, ticker="X", wall_ts="t3", side="sell",
               broker_event_id="e3", qty=100, price=9.0)    # realize 100 @ -1
    assert led.position("X").qty == 0
    assert led.realized_pnl("X") == pytest.approx(100.0)     # +200 -100


def test_fold_is_pure_reread(tmp_path):
    """The fold reads only the log — a fresh Ledger on the same db gives the same answer."""
    p = tmp_path / "l.db"
    a = Ledger(db_path=p)
    a.append(event_type=FILL_COMPLETE, ticker="X", wall_ts="t1", side="buy",
             broker_event_id="e1", qty=100, price=10.0)
    a.append(event_type=FILL_COMPLETE, ticker="X", wall_ts="t2", side="sell",
             broker_event_id="e2", qty=100, price=10.5)
    b = Ledger(db_path=p)  # fresh instance, no in-memory state
    assert b.realized_pnl("X") == a.realized_pnl("X") == pytest.approx(50.0)


# ── the dedup key extraction (parser) ──

def test_parse_extracts_execution_id():
    msg = {"data": {"event": "fill", "execution_id": "exec-xyz",
                    "order": {"id": "o1", "symbol": "X", "side": "buy", "type": "market",
                              "qty": "100", "filled_qty": "100", "filled_avg_price": "5.0",
                              "status": "filled", "filled_at": "2026-06-01T14:30:00Z"}}}
    ev = parse_trade_update(msg)
    assert ev.execution_id == "exec-xyz"
    assert ev.broker_event_id == "exec:exec-xyz"   # prefers the real id


def test_broker_event_id_falls_back_to_deterministic_hash():
    """If Alpaca lacks a per-fill id, the dedup key is a DETERMINISTIC hash (replay-safe)."""
    a = _compute_broker_event_id("", "fill", "o1", 100, 5.0, "2026-06-01T14:30:00Z")
    b = _compute_broker_event_id("", "fill", "o1", 100, 5.0, "2026-06-01T14:30:00Z")
    assert a == b and a.startswith("hash:")        # same fill -> same id (idempotent)
    c = _compute_broker_event_id("", "fill", "o1", 100, 5.0, "2026-06-01T14:31:00Z")
    assert c != a                                  # different fill -> different id
