"""doc 218: The Adversary — an execution-breaking sim broker + invariant checker.

doc 217's decision: build the adversarial market as a ROBUSTNESS tool against our DETERMINISTIC
execution plumbing (can't be overfit), NOT a selection-edge discoverer (which would overfit
to an invented distribution). The Adversary drives our REAL close/exit/recon code through a
sim broker whose timing/errors it controls, and tries to break invariants:
  - NEVER NAKED:   a held position must have a protective stop at the broker.
  - NEVER PHANTOM: P&L booked ONLY on a confirmed broker fill.
  - NEVER STRANDED: a position the bot believes closed must be gone at the broker.
  - DRAWDOWN HOLDS / RECON DELTA == 0.

The flagship weapon = the held_for_orders 403 with adversary-controlled SETTLE LATENCY (the
exact mechanism behind the doc-216 cancel-settle race / the phantoms / the naked carries).
This file is the sim broker + the scoreboard; harness scenarios live in scripts/adversary_run.py.
Pure in-memory; deterministic given a seed; no network.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The Alpaca 40310000 body shape our real is_qty_blocked detector matches on (bridge.py:558).
_QTY_403_BODY = ('{{"available":"0","code":40310000,"existing_qty":"{q}",'
                 '"held_for_orders":"{q}","message":"insufficient qty available for order '
                 '(requested: {q}, available: 0)","symbol":"{s}"}}')


class AdversaryError(Exception):
    """A broker error whose str() carries the Alpaca JSON body (so the real code's substring
    match on 'insufficient qty available' / '40310000' / 'available: 0' fires)."""


@dataclass
class _SimOrder:
    id: str
    symbol: str
    side: str
    qty: int
    type: str             # "stop" | "market" | "limit"
    stop_price: float = 0.0
    status: str = "new"   # new | canceled | filled
    # adversary: a cancel is requested at T, but qty stays RESERVED until T+settle_delay.
    cancel_requested_tick: int | None = None


@dataclass
class _SimPosition:
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float


class AdversaryBroker:
    """A drop-in for AlpacaDataClient that the Adversary controls. Implements the 6 methods
    attempt_close_with_status_check + its helpers call: close_position, cancel_order,
    get_orders, get_positions, submit_stop_order, submit_order.

    The core realism (and weapon): a STOP order RESERVES the position's qty (held_for_orders).
    close_position 403s with the 40310000 body while reserved. Cancelling the stop frees the
    qty only after `settle_delay_ticks` polls — the adversary sets this. settle_delay huge =>
    the cancel never settles in the close's budget => the real code must FAIL CLEANLY (not go
    naked, not phantom). A 'tick' advances on each get_positions() call (the settle-poll's clock)."""

    def __init__(self, *, settle_delay_ticks: int = 2, close_hard_fail: bool = False,
                 partial_fill_frac: float | None = None,
                 stop_fills_at_tick: int | None = None,
                 deadline_tick: int | None = None,
                 halt_until_tick: int | None = None):
        self.settle_delay_ticks = settle_delay_ticks
        self.close_hard_fail = close_hard_fail   # adversary: close 403s even with qty free
        # doc 219 weapons:
        self.partial_fill_frac = partial_fill_frac      # close fills only this fraction of qty
        self.stop_fills_at_tick = stop_fills_at_tick    # the reserving stop TRIGGERS at this tick
        self.deadline_tick = deadline_tick              # the EOD bell: ticks past it = "after close"
        self.halt_until_tick = halt_until_tick          # trading halt: close 403s until this tick
        self._positions: dict[str, _SimPosition] = {}
        self._orders: dict[str, _SimOrder] = {}
        self._tick = 0
        self._oid_seq = 0
        self.events: list[str] = []
        self.deadline_breached = False                  # set if a settle-poll crossed the bell

    def _maybe_stop_fill(self):
        """doc 219: the reserving protective stop TRIGGERS mid-close (price hit it). The
        position vanishes from under the close path — a classic race. The bot must not then
        book a phantom or re-buy."""
        if self.stop_fills_at_tick is None or self._tick < self.stop_fills_at_tick:
            return
        for o in self._orders.values():
            if o.type == "stop" and o.status == "new":
                p = self._positions.get(o.symbol)
                if p and p.qty != 0:
                    o.status = "filled"
                    self.events.append(f"STOP_FILLED {o.symbol} @ {o.stop_price} tick={self._tick} "
                                       f"(position vanished mid-close)")
                    self._positions.pop(o.symbol, None)

    # ── setup (the scenario seeds these) ──
    def seed_position(self, symbol, qty, entry, current=None):
        self._positions[symbol] = _SimPosition(symbol, qty, entry, current or entry)

    def seed_stop(self, symbol, qty, stop_price):
        self._oid_seq += 1
        oid = f"stop-{self._oid_seq}"
        self._orders[oid] = _SimOrder(oid, symbol, "sell", qty, "stop", stop_price, "new")
        return oid

    # ── reserved-qty bookkeeping (the held_for_orders mechanism) ──
    def _reserved(self, symbol) -> int:
        """Shares reserved by a still-active OR not-yet-settled-cancel stop."""
        r = 0
        for o in self._orders.values():
            if o.symbol != symbol or o.side != "sell" or o.type not in ("stop", "limit"):
                continue
            if o.status == "new":
                r += o.qty
            elif o.status == "canceled" and o.cancel_requested_tick is not None:
                # still reserved until settle_delay_ticks have elapsed since the cancel
                if self._tick - o.cancel_requested_tick < self.settle_delay_ticks:
                    r += o.qty
        return r

    def _available(self, symbol) -> int:
        p = self._positions.get(symbol)
        if not p:
            return 0
        return max(0, abs(p.qty) - self._reserved(symbol))

    # ── the 6 client methods our real code calls ──
    async def close_position(self, ticker):
        p = self._positions.get(ticker)
        if not p or p.qty == 0:
            # doc 219: position already gone (e.g. the stop filled mid-close). The real code
            # treats "not found" as a 404-ish; raise so it's handled as a non-fill, not a phantom.
            raise AdversaryError(f"position not found for {ticker} (already closed/stop-filled)")
        # doc 219: trading halt — close 403s (different body) until the halt lifts
        if self.halt_until_tick is not None and self._tick < self.halt_until_tick:
            self.events.append(f"close_HALT {ticker} tick={self._tick}<{self.halt_until_tick}")
            raise AdversaryError(
                '{"code":42210000,"message":"trading halted for ' + ticker + '"}')
        avail = self._available(ticker)
        if self.close_hard_fail or avail < abs(p.qty):
            self.events.append(f"close_403 {ticker} avail={avail}/{abs(p.qty)} tick={self._tick}")
            raise AdversaryError(_QTY_403_BODY.format(q=abs(p.qty), s=ticker))
        # success — flatten (possibly PARTIAL: the adversary fills only part of the qty)
        fill = p.current_price
        full = abs(p.qty)
        if self.partial_fill_frac is not None and 0 < self.partial_fill_frac < 1:
            filled = max(1, int(full * self.partial_fill_frac))
            p.qty = (full - filled) if p.qty > 0 else -(full - filled)  # remainder stays open!
            self.events.append(f"PARTIAL_CLOSE {ticker} {filled}/{full} @ {fill} "
                               f"(remainder {full - filled} still open) tick={self._tick}")
            return {"filled_avg_price": fill, "filled_qty": filled, "status": "partially_filled"}
        self._positions.pop(ticker, None)
        self.events.append(f"CLOSED {ticker} @ {fill} tick={self._tick}")
        return {"filled_avg_price": fill, "filled_qty": full, "status": "filled"}

    async def cancel_order(self, oid):
        o = self._orders.get(oid)
        if o and o.status == "new":
            o.status = "canceled"
            o.cancel_requested_tick = self._tick   # qty stays reserved until settle_delay
            self.events.append(f"cancel {oid} (settles in {self.settle_delay_ticks} ticks)")
        return {"id": oid, "status": "canceled"}

    async def get_orders(self, status=None, **kw):
        out = []
        for o in self._orders.values():
            if o.status == "new":
                out.append({"id": o.id, "symbol": o.symbol, "side": o.side,
                            "type": o.type, "qty": str(o.qty),
                            "stop_price": str(o.stop_price), "status": o.status})
        return out

    async def get_positions(self):
        self._tick += 1   # a 'tick' = one settle-poll; this is the cancel-settle clock
        # doc 219: a settle-poll that crosses the EOD bell means the close ran past market
        # close (the 6/1 D90 "Market is CLOSED" carry). Track it as an invariant signal.
        if self.deadline_tick is not None and self._tick > self.deadline_tick \
                and not self.deadline_breached:
            self.deadline_breached = True
            self.events.append(f"DEADLINE_BREACHED at tick={self._tick} (close ran past the bell)")
        self._maybe_stop_fill()   # the reserving stop may trigger mid-close
        out = []
        for p in self._positions.values():
            if p.qty == 0:
                continue
            out.append({"symbol": p.symbol, "qty": str(p.qty),
                        "qty_available": str(self._available(p.symbol)),
                        "avg_entry_price": str(p.avg_entry_price),
                        "current_price": str(p.current_price)})
        return out

    async def submit_stop_order(self, symbol, qty, side, stop_price,
                                time_in_force="gtc", position_intent=None):
        p = self._positions.get(symbol)
        # can't re-arm if no qty is free to back the stop (the D249 re-arm 403)
        if p and self._available(symbol) < qty:
            self.events.append(f"rearm_403 {symbol} (qty reserved)")
            raise AdversaryError(_QTY_403_BODY.format(q=qty, s=symbol))
        self._oid_seq += 1
        oid = f"stop-{self._oid_seq}"
        self._orders[oid] = _SimOrder(oid, symbol, side, qty, "stop", stop_price, "new")
        self.events.append(f"stop_armed {symbol} @ {stop_price} oid={oid}")
        return {"id": oid, "status": "new", "stop_price": str(stop_price)}

    async def submit_order(self, *a, **kw):
        self._oid_seq += 1
        oid = f"ord-{self._oid_seq}"
        return {"id": oid, "status": "accepted"}


# ── the scoreboard: invariants the Adversary tries to break ──

@dataclass
class InvariantReport:
    scenario: str
    broke: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def survived(self) -> bool:
        return not self.broke


def check_invariants(scenario: str, broker: AdversaryBroker, *,
                     close_result: dict | None, booked_pnl: bool,
                     escalated: bool = False) -> InvariantReport:
    """Score one scenario. The Adversary WINS if any invariant is broken.

    `escalated` = a CRITICAL NAKED_POSITION_RISK incident was emitted. A position that the
    broker makes impossible to protect (qty permanently reserved) is an UNAVOIDABLE naked
    state — surviving means we ESCALATE it loudly (operator must intervene), not that we
    magically protect it. So: SILENT naked = adversary wins; ESCALATED naked = survived."""
    rep = InvariantReport(scenario=scenario)
    for sym, p in broker._positions.items():
        if p.qty == 0:
            continue
        has_stop = any(o.symbol == sym and o.side == "sell" and o.type == "stop"
                       and o.status == "new" for o in broker._orders.values())
        if not has_stop and not escalated:
            rep.broke.append(f"SILENT-NAKED: {sym} qty={p.qty} open, NO stop, NO escalation")
        elif not has_stop and escalated:
            rep.notes.append(f"naked {sym} but ESCALATED (CRITICAL incident -> operator)")
        else:
            rep.notes.append(f"open {sym} qty={p.qty} but protected (stop armed)")
    # PHANTOM: P&L booked while the close did NOT succeed
    if booked_pnl and not (close_result and close_result.get("succeeded")):
        rep.broke.append("PHANTOM: P&L booked but broker close did not succeed")
    # the close path must always return a clean succeeded flag (never hang/None)
    if close_result is not None and "succeeded" not in close_result:
        rep.broke.append("MALFORMED: close_result missing 'succeeded'")
    # doc 219: PARTIAL — a partially-filled close that reports succeeded=True while shares
    # remain open at the broker is a stranding risk (the bot thinks it's flat, it isn't).
    if close_result and close_result.get("succeeded"):
        fq = close_result.get("filled_qty")
        for sym, p in broker._positions.items():
            if p.qty != 0 and fq is not None:
                rep.broke.append(
                    f"PARTIAL-STRAND: {sym} reported closed but {p.qty} shares remain at broker")
    # doc 219: DEADLINE — the close ran past the EOD bell (the 6/1 overnight-carry mechanism).
    # Not a hard break by itself (the close may still succeed), but a flagged risk note.
    if broker.deadline_breached:
        rep.notes.append("DEADLINE: close ran past the EOD bell (carry risk if it then failed)")
        if not (close_result and close_result.get("succeeded")):
            rep.broke.append("DEADLINE-CARRY: close failed AND ran past the bell -> overnight carry")
    return rep
