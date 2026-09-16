"""
MOMENTUM-X Fill Stream Bridge

### ARCHITECTURAL CONTEXT
Node ID: execution.fill_stream_bridge
Graph Link: docs/memory/graph_state.json → "execution.fill_stream_bridge"

### RESEARCH BASIS
Bridges the real-time TradeUpdatesStream (WebSocket) to the TrancheExitMonitor
and StopResubmitter, replacing the ~10s position-polling approach with
sub-second fill detection.

Event flow:
  1. TradeUpdatesStream receives fill event from Alpaca WebSocket
  2. FillStreamBridge.on_trade_update() dispatches to TrancheExitMonitor
  3. If tranche fill detected → RatchetResult returned
  4. If ratchet moves stop → StopResubmitter.resubmit() called
  5. All results queued for Phase 3 consumption

Ref: ADR-007 (Trade Updates WebSocket)
Ref: ADR-020 D2 (Tranche Exit Monitor)
Ref: ADR-022 D2 (Stop Resubmitter)

### CRITICAL INVARIANTS
1. Fill events processed in order (no reordering).
2. Ratchet + resubmit is atomic per fill event.
3. Unknown order_ids silently ignored (safety).
4. Queue is bounded (1000 max) to prevent memory leaks.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FillEvent:
    """
    Processed fill event with all downstream results.

    Node ID: execution.fill_stream_bridge.FillEvent
    """
    order_id: str
    ticker: str
    filled_price: float
    filled_qty: int
    timestamp: datetime
    tranche_number: int | None = None
    old_stop: float | None = None
    new_stop: float | None = None
    stop_resubmitted: bool = False
    position_closed: bool = False
    realized_pnl: float = 0.0
    # doc 270: attribution of the booked exit leg (STOP_FILL /
    # PARTIAL_EXIT / ""). "" for tranche-path events (tranche_number
    # carries the attribution there).
    exit_reason: str = ""


class FillStreamBridge:
    """
    Bridges WebSocket fill events to tranche monitoring and stop management.

    Node ID: execution.fill_stream_bridge
    Ref: ADR-007 (WebSocket), ADR-020 (Tranche), ADR-022 (Stop Resubmit)

    Usage:
        bridge = FillStreamBridge(
            tranche_monitor=tranche_monitor,
            stop_resubmitter=stop_resubmitter,
        )
        # Wire into TradeUpdatesStream
        trade_stream.on_trade_update = bridge.on_trade_update
        # In Phase 3 loop:
        events = bridge.drain_events()
        for ev in events:
            logger.info("Fill: %s T%d @ $%.2f", ev.ticker, ev.tranche_number, ev.filled_price)
    """

    MAX_QUEUE_SIZE = 1000

    def __init__(
        self,
        tranche_monitor: Any,
        stop_resubmitter: Any | None = None,
        *,
        position_manager: Any | None = None,
        trade_journal: Any | None = None,
    ) -> None:
        """
        Args:
            tranche_monitor: TrancheExitMonitor for fill processing.
            stop_resubmitter: StopResubmitter for stop ratcheting (optional).
            position_manager: PositionManager (optional, kwarg-only).
                When provided, OTO stop-leg fills are detected and
                propagated into the tracker via remove_position(). See
                D297 docstring below. Backwards-compat: omitting it
                preserves the pre-D297 behaviour (silent ignore of
                non-tranche sell fills) for existing tests.
            trade_journal: TradeJournal (optional, kwarg-only).
                When provided, OTO stop-leg fills are recorded via
                record_close(exit_reason="STOP_FILL"). Without this,
                EOD reconciliation (D238) reports broker positions
                that journal never recorded.

        D297 (2026-05-19) fix for Bug A + B:
            Pre-D297, when an OTO secondary stop-leg fill arrived via
            websocket, this bridge dispatched the event to
            tranche_monitor.on_fill(), got None back (because the
            stop-leg order_id isn't a registered tranche), and exited
            silently. Consequence: broker had no position, internal
            tracker still thought it did, journal had no close record.
            On 2026-05-18 this caused 613 ERROR-level qty_drift recon
            failures AND a $571 D238 EOD reconciliation gap (CISS
            -$562 + GCTS -$119 both broker=closed/journal=missing).
        """
        self._tranche_monitor = tranche_monitor
        self._stop_resubmitter = stop_resubmitter
        self._position_manager = position_manager
        self._trade_journal = trade_journal
        self._event_queue: deque[FillEvent] = deque(maxlen=self.MAX_QUEUE_SIZE)
        self._pending_resubmits: list[tuple[str, float, int]] = []  # (ticker, new_stop, new_qty)
        # ── doc 270 (HWH 2026-06-10): per-leg exit booking state ──────
        # Dedupe of broker executions (websocket reconnects redeliver).
        self._booked_exec_ids: set[Any] = set()
        self._booked_exec_order: deque[Any] = deque(maxlen=8192)
        # Per-order CUMULATIVE booked progress. Alpaca fill/partial_fill
        # events carry order-cumulative filled_qty + filled_avg_price, so
        # each event books only the INCREMENT over what was already
        # booked for that order_id. This makes booking idempotent under
        # duplicate delivery and correct under out-of-order delivery
        # (a late partial after the terminal fill books 0 new shares).
        self._order_booked_qty: dict[str, int] = {}
        self._order_booked_notional: dict[str, float] = {}
        self._ORDER_STATE_CAP = 4096

    def on_trade_update(self, event: Any) -> None:
        """
        Callback for TradeUpdatesStream — dispatches fill events.

        This is called from the WebSocket event loop synchronously.
        For async stop resubmission, fills are queued and processed
        in drain_and_resubmit().

        Args:
            event: TradeUpdateEvent from parse_trade_update().
        """
        from src.data.trade_updates import OrderEvent

        # doc 270 (HWH 2026-06-10): partial prints of EXIT orders must be
        # booked per-leg, not dropped. Route PARTIAL_FILL events straight
        # to the partial-aware exit handler — NEVER to tranche_monitor
        # (its on_fill dedupes by order_id and expects exactly one
        # terminal event per registered tranche order; the handler itself
        # skips order_ids registered as tranches for the same reason).
        # getattr-guarded: some unit tests monkeypatch OrderEvent with a
        # FILL-only sentinel.
        _pf = getattr(OrderEvent, "PARTIAL_FILL", None)
        if _pf is not None and event.event_type == _pf:
            self._handle_oto_stop_fill(event)
            return

        if event.event_type != OrderEvent.FILL:
            return

        from src.execution.tranche_monitor import TrancheFillEvent

        fill_event = TrancheFillEvent(
            order_id=event.order_id,
            ticker=event.symbol,
            filled_price=event.filled_avg_price,
            filled_qty=event.filled_qty,
        )

        result = self._tranche_monitor.on_fill(fill_event)
        if result is None:
            # D297 (2026-05-19): unknown order is the OTO stop-leg
            # signature. If it's a SELL fill for a ticker the position
            # manager tracks, treat it as an OTO stop-fill close: record
            # the journal close and remove from internal tracker.
            self._handle_oto_stop_fill(event)
            return  # done — either we journaled the stop-fill or it
                    # was a genuine unknown (e.g. manual broker order)

        fill = FillEvent(
            order_id=event.order_id,
            ticker=event.symbol,
            filled_price=event.filled_avg_price,
            filled_qty=event.filled_qty,
            timestamp=event.timestamp,
            tranche_number=result.tranche_number,
            old_stop=result.old_stop,
            new_stop=result.new_stop,
            position_closed=result.position_fully_closed,
            realized_pnl=result.realized_pnl,
        )

        # Queue stop resubmission if ratchet occurred
        # Guard: do NOT resubmit if position is fully closed (remaining=0)
        # — otherwise we'd create a phantom stop order for qty=1 that could
        # open a new short position if triggered.
        if result.new_stop > result.old_stop and self._stop_resubmitter is not None:
            remaining = event.total_qty - event.filled_qty
            if remaining > 0 and not result.position_fully_closed:
                self._pending_resubmits.append((event.symbol, result.new_stop, remaining))

        logger.info(
            "FILL STREAM: %s T%d @ $%.2f | Stop: $%.2f → $%.2f | PnL: $%.2f%s",
            fill.ticker, fill.tranche_number or 0, fill.filled_price,
            fill.old_stop or 0, fill.new_stop or 0, fill.realized_pnl,
            " [CLOSED]" if fill.position_closed else "",
        )

        self._event_queue.append(fill)

    def _handle_oto_stop_fill(self, event: Any) -> None:
        """D297 (2026-05-19) + doc 270 (2026-06-10): handle a non-tranche
        SELL fill (OTO/standalone stop leg, D165 market tranche-take,
        manual broker sell) for a tracked position — PARTIAL-AWARE.

        doc 270 root cause (HWH 2026-06-10, journal/broker −$970.94):
            Pre-doc-270 this handler treated ANY sell fill for a tracked
            ticker as the TERMINAL close of the WHOLE position: it booked
            ``record_close(exit_reason="STOP_FILL")`` at the fill's price
            /qty and called ``remove_position()``. When the D165 tranche
            market-sell (5,479 of 16,437 sh @ $2.02) filled at 10:17:22,
            the position was journaled CLOSED at +$54.79/STOP_FILL and
            evicted from the tracker; when the real stop then filled the
            remaining 10,958 sh @ 1.91/1.93 at 10:17:53-54,
            ``has_position()`` was False and the −$970.94 stop wave was
            never booked anywhere.

        doc 270 semantics:
          - Alpaca fill/partial_fill events carry order-CUMULATIVE
            ``filled_qty``/``filled_avg_price``. Each event books only
            the INCREMENT over what this bridge already booked for that
            ``order_id`` (idempotent under duplicate delivery; correct
            under out-of-order delivery).
          - A leg smaller than the position books ONLY that leg:
            ``(leg_px − entry) × leg_qty``, journaled via
            ``record_close`` (doc-269 journal semantics append per-leg
            rows and never clobber closed rows). The position is NOT
            removed.
          - exit_reason: "STOP_FILL" when the order_id matches the
            position's tracked stop (``pos.stop_order_id`` or the
            StopResubmitter's tracked stop) OR when the leg terminates
            the position (legacy D297 contract — an unknown full
            liquidation is, in practice, the stop); otherwise
            "PARTIAL_EXIT" (e.g. the D165 tranche-take market sell).
          - Terminal detection prefers the broker's data-level
            ``position_qty`` (shares remaining AFTER the print — truth);
            falls back to tracker ``remaining_qty`` when absent.
          - Tracker mutation: terminal → ``remove_position()``;
            non-terminal STOP leg → decrement ``remaining_qty`` (the
            bridge is the only owner for broker-initiated stop prints);
            non-terminal NON-stop leg → tracker left alone (the
            submitting main-loop path — D165/D164 — decrements it and
            books the PM sinks itself; double-decrement would resize
            the protective stop too small and leave shares naked).
          - Dedupe: broker execution_id remembered; replayed events
            book nothing.

        Safety: every step try/except-guarded; never raises into the
        websocket loop; pm/journal None → legacy silent-ignore no-op.
        """
        if self._position_manager is None or self._trade_journal is None:
            return  # legacy callers; pre-D297 silent-ignore behaviour
        side = (getattr(event, "side", "") or "").lower()
        if side != "sell":
            return  # only stop-leg / exit fills go through this path
        ticker = getattr(event, "symbol", "") or ""
        if not ticker:
            return
        oid = str(getattr(event, "order_id", "") or "")

        # ── doc 270 STEP 0a: tranche-owned orders are not ours ────────
        # A REGISTERED tranche order reaches here only via PARTIAL_FILL
        # routing (its terminal FILL goes to tranche_monitor.on_fill) or
        # because the monitor deduped a duplicate terminal FILL. Both
        # must be skipped: the monitor books the full order exactly once
        # at terminal; booking prints here would double-book.
        try:
            _tm = self._tranche_monitor
            if _tm is not None and oid:
                _omap = getattr(_tm, "_order_map", None)
                if _omap is not None and oid in _omap:
                    return  # live registered tranche — monitor owns it
                _done = getattr(_tm, "_processed_fill_ids", None)
                if _done is not None and oid in _done:
                    return  # duplicate of an already-booked tranche fill
        except Exception:
            pass  # advisory guard only

        # ── doc 270 STEP 0b: execution-id dedupe (ws redelivery) ─────
        exec_key: Any = None
        try:
            exec_key = (
                getattr(event, "execution_id", "")
                or getattr(event, "broker_event_id", "")
                or None
            )
        except Exception:
            exec_key = None
        if exec_key is not None and exec_key in self._booked_exec_ids:
            logger.debug(
                "doc270 %s: duplicate execution %s — already booked, skipping",
                ticker, exec_key,
            )
            return

        # Look up the position BEFORE any mutation, so we can compute pnl
        try:
            if not self._position_manager.has_position(ticker):
                return  # genuine unknown -- not one of our positions
            pos = self._position_manager.get_position(ticker)
        except Exception as _pme:
            logger.warning(
                "D297 %s: position_manager lookup raised (%s); skipping",
                ticker, _pme,
            )
            return
        if pos is None:
            return
        try:
            _direction = getattr(pos, "direction", "long")
            if isinstance(_direction, str) and _direction.lower() == "short":
                # A SELL on a short position ADDS to it — booking it as an
                # exit would be wrong. Short exits are BUY fills (separate
                # path); leave shorts to their own engine.
                logger.debug(
                    "doc270 %s: sell fill on SHORT position ignored", ticker,
                )
                return
        except Exception:
            pass

        # ── doc 270 STEP 1: incremental leg math (cumulative events) ──
        try:
            entry_px = float(getattr(pos, "entry_price", 0.0) or 0.0)
            cum_qty = int(getattr(event, "filled_qty", 0) or 0)
            cum_avg = float(getattr(event, "filled_avg_price", 0.0) or 0.0)
            prev_qty = int(self._order_booked_qty.get(oid, 0))
            prev_notional = float(self._order_booked_notional.get(oid, 0.0))
            inc_qty = cum_qty - prev_qty
            if inc_qty <= 0:
                logger.debug(
                    "doc270 %s: order %s cum_qty=%d already booked %d — "
                    "no new shares, skipping",
                    ticker, oid[:8], cum_qty, prev_qty,
                )
                return
            leg_notional = (cum_qty * cum_avg) - prev_notional
            leg_px = (leg_notional / inc_qty) if inc_qty > 0 else cum_avg
            if leg_px < 0:
                leg_px = cum_avg  # defensive: malformed cumulative data
            realized_pnl = ((leg_px - entry_px) * inc_qty) if entry_px > 0 else 0.0

            # ── classification: stop leg vs bot/manual exit leg ───────
            _stop_oids: set[str] = set()
            try:
                _so = getattr(pos, "stop_order_id", "") or ""
                if isinstance(_so, str) and _so:
                    _stop_oids.add(_so)
            except Exception:
                pass
            try:
                if self._stop_resubmitter is not None:
                    _trk = self._stop_resubmitter.get_tracked_stop(ticker)
                    _toid = getattr(_trk, "order_id", "") if _trk else ""
                    if isinstance(_toid, str) and _toid:
                        _stop_oids.add(_toid)
            except Exception:
                pass
            is_stop = bool(oid) and oid in _stop_oids

            # ── terminal detection: broker position_qty is truth ──────
            # Strict typing: only trust real numerics (int/float/numeric
            # str). Test doubles (MagicMock) implement __float__ → 1.0,
            # which would silently fake "1 share left at broker".
            _bpq = getattr(event, "position_qty", None)
            _bpq_f: float | None = None
            if isinstance(_bpq, (int, float)) and not isinstance(_bpq, bool):
                _bpq_f = float(_bpq)
            elif isinstance(_bpq, str) and _bpq.strip():
                try:
                    _bpq_f = float(_bpq)
                except ValueError:
                    _bpq_f = None
            try:
                remaining = int(getattr(pos, "remaining_qty", 0) or 0)
            except (TypeError, ValueError):
                remaining = inc_qty
            if _bpq_f is not None:
                terminal = _bpq_f <= 0
            else:
                terminal = inc_qty >= max(0, remaining)

            exit_reason = "STOP_FILL" if (is_stop or terminal) else "PARTIAL_EXIT"
        except Exception as _ce:
            logger.warning(
                "doc270 %s: leg computation raised (%s); fill NOT booked "
                "— D222/D238 recon will surface any resulting gap",
                ticker, _ce,
            )
            return

        # ── doc 270 STEP 2: mark dedupe state BEFORE side effects ─────
        # (never-double-book beats at-least-once: a journal IO failure
        # must not lead to a re-book on websocket redelivery)
        try:
            if exec_key is not None:
                if (self._booked_exec_order.maxlen is not None
                        and len(self._booked_exec_order)
                        >= self._booked_exec_order.maxlen):
                    _evict = self._booked_exec_order.popleft()
                    self._booked_exec_ids.discard(_evict)
                self._booked_exec_order.append(exec_key)
                self._booked_exec_ids.add(exec_key)
            if (oid not in self._order_booked_qty
                    and len(self._order_booked_qty) >= self._ORDER_STATE_CAP):
                _oldest = next(iter(self._order_booked_qty))
                self._order_booked_qty.pop(_oldest, None)
                self._order_booked_notional.pop(_oldest, None)
            self._order_booked_qty[oid] = cum_qty
            self._order_booked_notional[oid] = cum_qty * cum_avg
        except Exception:
            pass  # bookkeeping is advisory; booking proceeds

        # ── STEP 3: journal record_close for THIS LEG (never fatal) ───
        # doc-269 record_close semantics make per-leg booking safe: the
        # first leg closes the BUY row; later legs append rows (never
        # clobbering). Per-ticker journal realized = Σ legs = broker net.
        try:
            for _j_tid, _j_ent in list(self._trade_journal._entries.items()):
                if _j_ent.ticker == ticker and _j_ent.action == "BUY":
                    self._trade_journal.record_close(
                        _j_tid,
                        exit_price=leg_px,
                        realized_pnl=realized_pnl,
                        exit_time=getattr(event, "timestamp",
                                           datetime.now(timezone.utc)),
                        exit_reason=exit_reason,
                    )
                    logger.info(
                        "D297/doc270 %s: journal leg booked "
                        "(qty=%d @ $%.4f pnl=$%+.2f reason=%s%s)",
                        ticker, inc_qty, leg_px, realized_pnl, exit_reason,
                        " TERMINAL" if terminal else " partial",
                    )
                    break
            else:
                logger.warning(
                    "D297 %s: exit fill detected but no open BUY entry "
                    "in journal (D238 may still flag journal=MISSING)",
                    ticker,
                )
        except Exception as _je:
            logger.warning(
                "D297 %s: record_close raised (%s); proceeding to tracker update",
                ticker, _je,
            )

        # ── STEP 4: tracker update (advisory) ─────────────────────────
        if terminal:
            try:
                self._position_manager.remove_position(ticker)
                logger.info(
                    "D297 %s: position_manager.remove_position OK -- next "
                    "qty_drift recon should be clean",
                    ticker,
                )
            except Exception as _re:
                logger.warning(
                    "D297 %s: remove_position raised (%s); qty_drift may "
                    "continue until next reconcile",
                    ticker, _re,
                )
        elif is_stop:
            # Broker-initiated partial stop print: nobody in the main
            # loop will decrement for it — the bridge owns it.
            try:
                pos.remaining_qty = max(0, int(pos.remaining_qty) - inc_qty)
                logger.info(
                    "doc270 %s: partial STOP print — remaining_qty -> %d",
                    ticker, pos.remaining_qty,
                )
            except Exception as _de:
                logger.warning(
                    "doc270 %s: remaining_qty decrement raised (%s)",
                    ticker, _de,
                )
        else:
            # Bot-initiated partial exit (D165 tranche-take / D164 early
            # profit take): the submitting path decrements remaining_qty
            # and books the PM sinks itself. Touching the tracker here
            # would double-decrement and shrink the re-armed stop.
            logger.info(
                "doc270 %s: partial exit leg journaled; tracker left to "
                "the submitting path (remaining=%s)",
                ticker, getattr(pos, "remaining_qty", "?"),
            )

        # ── STEP 5: emit FillEvent so downstream consumers see it ────
        try:
            fill = FillEvent(
                order_id=oid,
                ticker=ticker,
                filled_price=leg_px,
                filled_qty=inc_qty,
                timestamp=getattr(event, "timestamp",
                                   datetime.now(timezone.utc)),
                tranche_number=None,
                old_stop=None,
                new_stop=None,
                stop_resubmitted=False,
                position_closed=bool(terminal),
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
            )
            self._event_queue.append(fill)
        except Exception:
            pass  # queue full or other transient; not fatal

    async def drain_and_resubmit(self) -> list[FillEvent]:
        """
        Drain queued fill events and process pending stop resubmissions.

        Called from the Phase 3 async loop. Handles the async
        cancel_order + submit_stop_order operations.

        Returns:
            List of processed FillEvent objects since last drain.
        """
        # D150: Atomic swap instead of list+clear — prevents race where
        # WebSocket appends a fill between the snapshot and the clear.
        _old_queue = self._event_queue
        self._event_queue = deque(maxlen=self.MAX_QUEUE_SIZE)
        events = list(_old_queue)

        # Process pending stop resubmissions
        # D121 BUG-S2: Guard against _stop_resubmitter being None.
        # If no resubmitter was injected, discard pending items.
        if not self._stop_resubmitter:
            self._pending_resubmits = []
            return events
        pending = self._pending_resubmits
        self._pending_resubmits = []

        for ticker, new_stop, new_qty in pending:
            try:
                # D150: Re-fetch current remaining qty from position manager
                # to avoid stale qty if multiple fills arrived between queue and drain.
                _current_qty = new_qty
                try:
                    if self._tranche_monitor and hasattr(self._tranche_monitor, '_find_position'):
                        _pos = self._tranche_monitor._find_position(ticker)
                        if _pos and isinstance(getattr(_pos, 'remaining_qty', None), int) and _pos.remaining_qty > 0:
                            _current_qty = _pos.remaining_qty
                except Exception:
                    pass  # Fall back to queued qty
                result = await self._stop_resubmitter.resubmit(
                    ticker=ticker,
                    new_stop_price=new_stop,
                    new_qty=max(1, _current_qty),
                )
                if result.success:
                    logger.info(
                        "STOP RATCHETED (stream): %s → $%.2f (oid=%s)",
                        ticker, new_stop, result.new_order_id,
                    )
                    # Mark the corresponding fill event
                    for ev in events:
                        if ev.ticker == ticker and ev.new_stop == new_stop:
                            ev.stop_resubmitted = True
                            break
                else:
                    logger.warning(
                        "Stop resubmit failed (stream): %s — %s",
                        ticker, result.error,
                    )
            except Exception as e:
                logger.warning("Stop resubmit error (stream): %s — %s", ticker, e)

        return events

    def drain_events(self) -> list[FillEvent]:
        """
        Drain queued fill events without processing resubmissions.

        Use drain_and_resubmit() in async context for full processing.

        Returns:
            List of FillEvent objects since last drain.
        """
        events = list(self._event_queue)
        self._event_queue.clear()
        return events

    @property
    def pending_count(self) -> int:
        """Number of queued fill events."""
        return len(self._event_queue)

    @property
    def pending_resubmits(self) -> int:
        """Number of pending stop resubmissions."""
        return len(self._pending_resubmits)

    def reset(self) -> None:
        """Clear all state for new session."""
        self._event_queue = deque(maxlen=self.MAX_QUEUE_SIZE)
        self._pending_resubmits = []
        # doc 270: per-leg booking state is session-scoped
        self._booked_exec_ids = set()
        self._booked_exec_order = deque(maxlen=8192)
        self._order_booked_qty = {}
        self._order_booked_notional = {}
