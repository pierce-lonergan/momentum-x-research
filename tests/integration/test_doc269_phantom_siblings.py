"""doc 269 (Phase A1) — phantom-P&L family, last members.

Pins the cure applied to the D164 EARLY_PROFIT_TAKE (P2 task + P3 loop) and
D165 tranche-take partial-exit paths, plus the OCC journal-overwrite fix:

  House disease: an exit path calls close/submit, swallows the 403 that
  fires when qty is held_for_orders by the protective OTO stop, books P&L
  from a computed/MTM price instead of a confirmed fill, and cancels/loses
  the stop -> phantom P&L + naked position (LIDR/LFS 5/27, APPS 5/28,
  TNGX/ABAT 6/8, OCC 6/8).

  Cure (mirrors D78 SMART_EXIT ~6280 and the doc-263 D163 fix ~6080):
  route the close through attempt_close_with_status_check(partial=True,
  cancel_blocking_stops_first=True) + the Bug-Z gate -- on failure: no
  booking, no stop-cancel, position stays tracked + protected; on success:
  book ONLY from result["fill_price"] x confirmed qty and resize the
  protective stop for the remaining shares.

  Journal (OCC 6/8): record_close must never stamp a close onto a row that
  already carries a realized close -- redirect to the still-OPEN same-ticker
  BUY, else append; the unknown-trade_id fallback targets OPEN BUYs only.

Test-double style mirrors tests/unit/test_bug_ai_force_close.py (shaped
fakes + the LIDR 403 body) and tests/property/simple_broker.py (Alpaca
order-dict shapes: string-typed numerics, status lifecycle).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.execution.bridge import attempt_close_with_status_check


# ── Test doubles ─────────────────────────────────────────────────────


class _Block403Error(Exception):
    """Mimics the httpx.HTTPStatusError repr for the held_for_orders 403
    (same body as tests/unit/test_bug_ai_force_close.py / LIDR)."""

    def __init__(self, symbol: str = "TST", qty: int = 100) -> None:
        self._symbol = symbol
        self._qty = qty
        super().__init__()

    def __str__(self) -> str:
        return (
            "Client error '403 Forbidden' — body={'available': '0', "
            "'code': 40310000, 'existing_qty': '%d', 'held_for_orders': "
            "'%d', 'message': 'insufficient qty available for order "
            "(requested: %d, available: 0)', 'symbol': '%s'}"
            % (self._qty, self._qty, self._qty, self._symbol)
        )


class _FakePartialBroker:
    """Minimal broker double for the doc-269 partial-close path.

    Holds one position (qty 100) protected by one OTO-style sell stop that
    reserves the FULL qty. Modes:
      - "qty_403":      every close_position 403s with the held_for_orders
                        body (even after the stop is cancelled) -> the
                        helper's D248 dance + D249 re-arm must run.
      - "other_error":  every close_position raises a non-qty error -> the
                        helper must NEVER touch the protective stop.
      - "fill_immediate": DELETE response is already filled (SimpleBroker-
                        style market fill).
      - "fill_after_poll": DELETE response is merely 'accepted'; the order
                        shows up FILLED via get_orders (the confirm-poll).
    """

    def __init__(self, mode: str, *, ticker: str = "TST",
                 fill_price: float = 10.50) -> None:
        self.mode = mode
        self.ticker = ticker
        self.fill_price = fill_price
        self.stop = {
            "id": "stop-001",
            "symbol": ticker,
            "side": "sell",
            "qty": "100",
            "type": "stop",
            "stop_price": "9.45",
            "limit_price": None,
            "client_order_id": "oto-protect-001",
            "status": "new",
            "filled_qty": "0",
            "filled_avg_price": None,
        }
        self.close_order: dict[str, Any] | None = None
        self.close_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []
        self.rearm_calls: list[dict[str, Any]] = []
        self.get_orders_calls = 0

    # — production-shape surface the helper needs —

    async def close_position(self, symbol: str, qty: int | None = None):
        self.close_calls.append({"symbol": symbol, "qty": qty})
        if self.mode == "qty_403":
            raise _Block403Error(symbol, qty or 100)
        if self.mode == "other_error":
            raise RuntimeError("broker 500: internal error (not qty-related)")
        oid = f"close-{len(self.close_calls)}"
        if self.mode == "fill_immediate":
            self.close_order = {
                "id": oid, "symbol": symbol, "side": "sell",
                "qty": str(qty or 100), "type": "market", "status": "filled",
                "filled_qty": str(qty or 100),
                "filled_avg_price": str(self.fill_price),
            }
        else:  # fill_after_poll
            self.close_order = {
                "id": oid, "symbol": symbol, "side": "sell",
                "qty": str(qty or 100), "type": "market", "status": "accepted",
                "filled_qty": "0", "filled_avg_price": None,
            }
        return dict(self.close_order)

    async def get_orders(self, status: str = "open", limit: int = 100,
                         symbols: str | None = None):
        self.get_orders_calls += 1
        out = []
        if self.stop["status"] in ("new", "accepted", "held"):
            if status in ("open", "all"):
                out.append(dict(self.stop))
        if self.close_order is not None and status in ("all", "closed", "open"):
            if self.mode == "fill_after_poll":
                # The market order has filled by the time anyone polls.
                self.close_order["status"] = "filled"
                self.close_order["filled_qty"] = self.close_order["qty"]
                self.close_order["filled_avg_price"] = str(self.fill_price)
            out.append(dict(self.close_order))
        return out

    async def cancel_order(self, order_id: str):
        self.cancel_calls.append(order_id)
        if order_id == self.stop["id"]:
            self.stop["status"] = "canceled"
        return {}

    async def submit_stop_order(self, **kwargs):
        self.rearm_calls.append(kwargs)
        # Re-armed protection becomes the live stop.
        self.stop = {
            "id": f"stop-rearm-{len(self.rearm_calls)}",
            "symbol": kwargs.get("symbol", self.ticker),
            "side": kwargs.get("side", "sell"),
            "qty": str(kwargs.get("qty", 0)),
            "type": "stop",
            "stop_price": str(kwargs.get("stop_price", 0)),
            "limit_price": None,
            "client_order_id": "d249-rearm",
            "status": "new",
            "filled_qty": "0",
            "filled_avg_price": None,
        }
        return {"id": self.stop["id"], "status": "accepted"}

    async def get_positions(self):
        # Position gone == qty freed; keeps _await_qty_available fast.
        return []

    # — assertion helpers —

    def live_protective_stops(self) -> list[dict[str, Any]]:
        return [dict(self.stop)] if self.stop["status"] in ("new", "accepted") else []


class _PosStub:
    """Position-tracker stub with the fields the D164/D165 sites touch."""

    def __init__(self, ticker: str = "TST", entry: float = 10.00,
                 qty: int = 100) -> None:
        self.ticker = ticker
        self.direction = "long"
        self.entry_price = entry
        self.remaining_qty = qty
        self.realized_pnl = 0.0
        self.stop_loss = 9.45
        self.stop_order_id = "stop-001"
        self.close_attempt_failed = False


def _mirror_d164_gate_and_booking(
    close_res: dict[str, Any],
    pos: _PosStub,
    qty_requested: int,
    journal_calls: list[dict[str, Any]],
) -> bool:
    """EXACT mirror of the doc-269 call-site contract in main.py (D164 P2
    ~4380, D164 P3 ~5700, D165 ~5850). test_main_py_source_pins() keeps this
    mirror honest against the real main.py source.

    Returns True iff P&L was booked."""
    # Bug-Z gate: broker close NOT confirmed -> no booking, no stop-cancel,
    # position stays tracked + protected.
    if not close_res["succeeded"] or not close_res["fill_price"]:
        try:
            pos.close_attempt_failed = True
        except Exception:
            pass
        return False
    # Book ONLY from the broker-confirmed fill — never a snapshot.
    fill_px = float(close_res["fill_price"])
    qty_done = min(int(close_res.get("filled_qty") or qty_requested), qty_requested)
    pos.remaining_qty = max(0, pos.remaining_qty - qty_done)
    partial_pnl = (fill_px - pos.entry_price) * qty_done
    pos.realized_pnl += partial_pnl
    journal_calls.append(
        {"exit_price": fill_px, "realized_pnl": partial_pnl, "qty": qty_done}
    )
    return True


# ── (a) failure path: 403 -> NO booking, stop NOT cancelled, tracked ──


@pytest.mark.asyncio
async def test_partial_close_non_qty_error_books_nothing_and_never_touches_stop():
    """A D164/D165-style partial close that fails for a non-qty reason:
    the helper must never cancel the protective stop, and the Bug-Z gate
    must book nothing — position remains tracked and fully protected."""
    broker = _FakePartialBroker("other_error")
    pos = _PosStub()
    tracker = {pos.ticker: pos}
    journal_calls: list[dict[str, Any]] = []

    result = await attempt_close_with_status_check(
        client=broker, ticker=pos.ticker, qty=50,
        max_retries=2, retry_backoff_s=0.001,
        cancel_blocking_stops_first=True, partial=True,
    )

    assert result["succeeded"] is False
    booked = _mirror_d164_gate_and_booking(result, pos, 50, journal_calls)

    assert booked is False
    # NO P&L booked
    assert pos.realized_pnl == 0.0
    assert journal_calls == []
    # qty untouched
    assert pos.remaining_qty == 100
    # stop NOT cancelled — the helper never touched it
    assert broker.cancel_calls == []
    assert broker.live_protective_stops()[0]["id"] == "stop-001"
    # position still tracked (the gate never removes/mutates the tracker)
    assert pos.ticker in tracker
    # escalation marker for the operator
    assert pos.close_attempt_failed is True


@pytest.mark.asyncio
async def test_partial_close_persistent_qty_403_books_nothing_and_rearms_protection():
    """The TNGX/ABAT shape: the sell 403s because the OTO stop reserves the
    full qty. With cancel_blocking_stops_first=True the helper cancels the
    blocking stop to retry; when the close STILL fails it must re-arm the
    stop from snapshot (D249) — net effect: nothing booked, position never
    left naked, tracker untouched."""
    broker = _FakePartialBroker("qty_403")
    pos = _PosStub()
    tracker = {pos.ticker: pos}
    journal_calls: list[dict[str, Any]] = []

    result = await attempt_close_with_status_check(
        client=broker, ticker=pos.ticker, qty=50,
        max_retries=2, retry_backoff_s=0.001,
        cancel_blocking_stops_first=True, partial=True,
    )

    assert result["succeeded"] is False
    booked = _mirror_d164_gate_and_booking(result, pos, 50, journal_calls)

    assert booked is False
    assert pos.realized_pnl == 0.0
    assert journal_calls == []
    assert pos.remaining_qty == 100
    assert pos.ticker in tracker
    # The blocking stop WAS cancelled for the retry dance...
    assert "stop-001" in broker.cancel_calls
    # ...but D249 re-armed it with the snapshot params: protection survives.
    assert len(broker.rearm_calls) == 1
    rearm = broker.rearm_calls[0]
    assert rearm["symbol"] == pos.ticker
    assert rearm["side"] == "sell"
    assert float(rearm["stop_price"]) == 9.45
    assert int(rearm["qty"]) == 100  # FULL qty — nothing was sold
    live = broker.live_protective_stops()
    assert len(live) == 1 and float(live[0]["stop_price"]) == 9.45


# ── (b) success path: books exactly result fill_price x confirmed qty ──


@pytest.mark.asyncio
async def test_partial_close_success_books_exactly_fill_price_times_qty():
    """Success books EXACTLY result['fill_price'] x confirmed qty — never a
    snapshot. The DELETE response is only 'accepted'; the confirmed fill
    comes from the order poll (the doc-269 confirm step)."""
    broker = _FakePartialBroker("fill_after_poll", fill_price=10.50)
    pos = _PosStub(entry=10.00, qty=100)
    journal_calls: list[dict[str, Any]] = []

    result = await attempt_close_with_status_check(
        client=broker, ticker=pos.ticker, qty=50,
        max_retries=3, retry_backoff_s=0.001,
        cancel_blocking_stops_first=True, partial=True,
    )

    assert result["succeeded"] is True
    assert result["fill_price"] == pytest.approx(10.50)
    assert result["filled_qty"] == 50
    # Only the partial qty was requested at the broker
    assert broker.close_calls == [{"symbol": "TST", "qty": 50}]

    booked = _mirror_d164_gate_and_booking(result, pos, 50, journal_calls)
    assert booked is True
    # books exactly fill_price x qty against entry: (10.50 - 10.00) * 50
    assert pos.realized_pnl == pytest.approx(25.0)
    assert len(journal_calls) == 1
    assert journal_calls[0]["exit_price"] == pytest.approx(10.50)
    assert journal_calls[0]["realized_pnl"] == pytest.approx(25.0)
    assert journal_calls[0]["qty"] == 50
    assert pos.remaining_qty == 50
    # No stop dance was needed on the happy path
    assert broker.cancel_calls == []


@pytest.mark.asyncio
async def test_partial_close_refuses_nonpositive_qty():
    """partial=True with qty<=0 must hard-fail WITHOUT touching the broker —
    close_position(qty=None/0) would degrade to a FULL-position DELETE."""
    broker = _FakePartialBroker("fill_immediate")
    for bad_qty in (0, -5):
        result = await attempt_close_with_status_check(
            client=broker, ticker="TST", qty=bad_qty,
            max_retries=2, retry_backoff_s=0.001,
            cancel_blocking_stops_first=True, partial=True,
        )
        assert result["succeeded"] is False
        assert "qty > 0" in (result["last_error"] or "")
    assert broker.close_calls == []  # broker never touched


@pytest.mark.asyncio
async def test_partial_close_success_immediate_fill_in_delete_response():
    """SimpleBroker-style brokers fill the DELETE synchronously — the helper
    must accept the in-response fill without needing the poll."""
    broker = _FakePartialBroker("fill_immediate", fill_price=12.25)
    result = await attempt_close_with_status_check(
        client=broker, ticker="TST", qty=40,
        max_retries=3, retry_backoff_s=0.001,
        cancel_blocking_stops_first=True, partial=True,
    )
    assert result["succeeded"] is True
    assert result["fill_price"] == pytest.approx(12.25)
    assert result["filled_qty"] == 40
    assert broker.close_calls == [{"symbol": "TST", "qty": 40}]


# ── (c) journal regression: OCC buy->close->re-buy->close ─────────────


class _MockCandidate:
    def __init__(self, ticker: str = "OCC") -> None:
        self.ticker = ticker
        self.current_price = 10.0
        self.previous_close = 8.0
        self.gap_pct = 25.0
        self.rvol = 5.0


def _open_buy_entry(journal, trade_id: str, ticker: str = "OCC"):
    entry = journal.create_entry(trade_id, _MockCandidate(ticker))
    entry.action = "BUY"
    return entry


def test_journal_rebuy_close_yields_two_rows_with_distinct_pnl(tmp_path):
    """Reproduces OCC 6/8: buy -> close -> re-buy -> close. The second close
    is addressed (caller-style) at the FIRST same-ticker BUY row — pre-fix it
    clobbered round-trip #1's realized_pnl (+$936 journal/broker delta).
    Post-fix it must land on the still-OPEN re-buy row: TWO rows, distinct
    realized_pnl."""
    from src.analysis.trade_journal import TradeJournal

    journal = TradeJournal(session_date="2026-06-08", journal_dir=tmp_path)
    _open_buy_entry(journal, "OCC-buy-1")
    journal.record_close(
        "OCC-buy-1", exit_price=11.00, realized_pnl=100.0,
        exit_time=datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc),
        exit_reason="EARLY_PROFIT_TAKE",
    )

    # Intraday re-buy
    _open_buy_entry(journal, "OCC-buy-2")

    # The buggy caller pattern: resolve trade_id as the FIRST same-ticker
    # BUY row -> that is OCC-buy-1, which is already CLOSED.
    first_buy_tid = next(
        tid for tid, e in journal._entries.items()
        if e.ticker == "OCC" and e.action == "BUY"
    )
    assert first_buy_tid == "OCC-buy-1"
    journal.record_close(
        first_buy_tid, exit_price=9.50, realized_pnl=-50.0,
        exit_time=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
        exit_reason="STOP_FILL",
    )

    e1 = journal._entries["OCC-buy-1"]
    e2 = journal._entries["OCC-buy-2"]
    # Round-trip #1 preserved — NOT clobbered
    assert e1.realized_pnl == pytest.approx(100.0)
    assert e1.exit_price == pytest.approx(11.00)
    assert e1.exit_reason == "EARLY_PROFIT_TAKE"
    # Round-trip #2 landed on the open re-buy row
    assert e2.realized_pnl == pytest.approx(-50.0)
    assert e2.exit_price == pytest.approx(9.50)
    # TWO rows with distinct realized_pnl
    pnls = [e.realized_pnl for e in journal._entries.values()
            if e.ticker == "OCC" and e.realized_pnl is not None]
    assert sorted(pnls) == [-50.0, 100.0]


def test_journal_never_overwrites_closed_row_appends_instead(tmp_path):
    """A close addressed at a CLOSED row with no open same-ticker BUY must
    append a new row — never overwrite the realized close."""
    from src.analysis.trade_journal import TradeJournal

    journal = TradeJournal(session_date="2026-06-08", journal_dir=tmp_path)
    _open_buy_entry(journal, "OCC-buy-1")
    journal.record_close("OCC-buy-1", exit_price=11.00, realized_pnl=100.0)

    # No open BUY remains; a stray duplicate close arrives for the same tid.
    journal.record_close("OCC-buy-1", exit_price=9.00, realized_pnl=-77.0)

    e1 = journal._entries["OCC-buy-1"]
    assert e1.realized_pnl == pytest.approx(100.0)  # preserved
    assert e1.exit_price == pytest.approx(11.00)
    # The duplicate close was appended as its own row
    appended = [
        e for tid, e in journal._entries.items()
        if tid != "OCC-buy-1" and e.ticker == "OCC"
    ]
    assert len(appended) == 1
    assert appended[0].realized_pnl == pytest.approx(-77.0)
    assert appended[0].action == "BUY"  # counted by session_summary


def test_journal_unknown_tid_fallback_targets_open_buy_only(tmp_path):
    """The unknown-trade_id fallback used to grab the FIRST BUY row of ANY
    ticker (closed or not). It must now skip closed rows and land on the
    still-open BUY."""
    from src.analysis.trade_journal import TradeJournal

    journal = TradeJournal(session_date="2026-06-08", journal_dir=tmp_path)
    _open_buy_entry(journal, "AAA-buy-1", ticker="AAA")
    journal.record_close("AAA-buy-1", exit_price=5.00, realized_pnl=10.0)
    _open_buy_entry(journal, "BBB-buy-1", ticker="BBB")

    journal.record_close("no-such-tid", exit_price=7.00, realized_pnl=33.0)

    # Closed AAA row untouched; open BBB row received the close.
    assert journal._entries["AAA-buy-1"].realized_pnl == pytest.approx(10.0)
    assert journal._entries["AAA-buy-1"].exit_price == pytest.approx(5.00)
    assert journal._entries["BBB-buy-1"].realized_pnl == pytest.approx(33.0)


def test_journal_partial_then_final_close_preserves_both_pnls(tmp_path):
    """D164 partial take stamps the BUY row; the later FULL close (D78/D163
    caller loop) re-addresses the same row. Pre-fix the final close ERASED
    the partial-take P&L (same clobber disease); post-fix both bookings
    survive and total realized equals their sum."""
    from src.analysis.trade_journal import TradeJournal

    journal = TradeJournal(session_date="2026-06-08", journal_dir=tmp_path)
    _open_buy_entry(journal, "TST-buy-1", ticker="TST")
    # D164 partial: +25
    journal.record_close("TST-buy-1", exit_price=10.50, realized_pnl=25.0,
                         exit_reason="EARLY_PROFIT_TAKE")
    # Final close of the remainder: +12 (addressed via first-BUY loop)
    journal.record_close("TST-buy-1", exit_price=10.24, realized_pnl=12.0,
                         exit_reason="SMART_EXIT")

    rows = [e for e in journal._entries.values() if e.ticker == "TST"]
    total = sum(e.realized_pnl or 0 for e in rows)
    assert total == pytest.approx(37.0)
    assert journal._entries["TST-buy-1"].realized_pnl == pytest.approx(25.0)


# ── source pins: keep the mirrors honest against main.py ─────────────


def test_main_py_source_pins():
    """Pins the doc-269 surgery in main.py itself (house style: cf.
    test_stop_order_tif_gtc). If someone reverts a site to the old
    submit_order + snapshot-booking pattern, this fails before live."""
    main_src = Path(__file__).resolve().parents[2].joinpath("main.py").read_text(
        encoding="utf-8"
    )
    # The cured sites call the status-checked helper in partial mode...
    assert main_src.count("partial=True,") >= 3, (
        "expected >=3 partial-mode attempt_close_with_status_check call sites "
        "(D164 P2, D164 P3, D165)"
    )
    # ...with the Bug-Z escalation markers
    assert main_src.count("D269 EARLY_PROFIT_ESCALATE") >= 4  # 2 per D164 site
    assert "D269 D165 TRANCHE" in main_src
    # The diseased patterns are gone
    assert "_sell_resp = await client.submit_order(" not in main_src
    assert "_d164_sell_resp = await client.submit_order(" not in main_src
    assert "_d165_resp = await client.submit_order(" not in main_src
    assert "D164 P3 submit_order failed" not in main_src
    # Success path resizes the protective stop for the remaining shares
    assert main_src.count("D269 STOP RESIZED") >= 2  # D164 P2 + P3


def test_bridge_partial_contract_signature():
    """The helper accepts the partial kwarg and the production client
    accepts the partial-close qty (DELETE ?qty=)."""
    import inspect
    from src.execution import bridge
    from src.data.alpaca_client import AlpacaDataClient

    sig = inspect.signature(bridge.attempt_close_with_status_check)
    assert "partial" in sig.parameters
    assert sig.parameters["partial"].default is False

    csig = inspect.signature(AlpacaDataClient.close_position)
    assert "qty" in csig.parameters
    assert csig.parameters["qty"].default is None
