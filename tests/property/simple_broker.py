"""
Track C Phase 1 — SimpleBroker reference model (the oracle).

Per `31_simple_broker_spec.md`:
  - Deterministic, in-process Python class mimicking Alpaca broker semantics
  - Production-shape API methods (8) so the bridge can call it interchangeably
  - Test-rule-only state-injection methods (8) so Hypothesis rules can drive
    broker-side events without going through the production code paths
  - State: orders dict, positions dict, halted_tickers set, account fields

Phase 1 deliverable: SimpleBroker class + 100% unit test coverage on its
own behavior BEFORE Phase 2 wires it to a Hypothesis state machine.

The reference model is the ORACLE. Bugs in this file mean invariants
check against fiction. Audit-complete in spec; implementation here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


TERMINAL_STATES = frozenset({"filled", "done_for_day", "canceled", "expired", "rejected", "replaced"})
TERMINAL_SUCCESS = frozenset({"filled", "done_for_day"})


@dataclass
class OrderRecord:
    """Single order record. Matches Alpaca's get_orders shape closely
    enough that the production code can consume it via dict access."""
    id: str
    symbol: str
    side: str                          # "buy" | "sell"
    qty: int
    type: str                          # "market" | "limit" | "stop" | "stop_limit"
    limit_price: float | None
    stop_price: float | None
    time_in_force: str                 # "day" | "gtc" | "opg" | "ioc"
    status: str = "accepted"
    parent_id: str | None = None       # for OTO child legs
    order_class: str = ""              # "" | "oto" | "bracket"
    filled_qty: int = 0
    filled_avg_price: float = 0.0
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    terminal_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    child_order_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Alpaca-shape dict for production-code consumption."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": str(self.qty),
            "type": self.type,
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "stop_price": str(self.stop_price) if self.stop_price is not None else None,
            "time_in_force": self.time_in_force,
            "status": self.status,
            "order_class": self.order_class,
            "filled_qty": str(self.filled_qty),
            "filled_avg_price": str(self.filled_avg_price) if self.filled_avg_price > 0 else None,
            "submitted_at": self.submitted_at.isoformat(),
            "legs": [],  # populated for parent OTO orders by SimpleBroker
        }


@dataclass
class PositionRecord:
    """Synthesized from filled orders. Side-aware (long-positive,
    short-negative qty)."""
    symbol: str
    qty: int                           # signed (long > 0, short < 0)
    avg_entry_price: float
    side: str                          # "long" | "short"
    cost_basis: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "qty": str(abs(self.qty)),
            "side": self.side,
            "avg_entry_price": str(self.avg_entry_price),
            "current_price": str(self.avg_entry_price),  # No quote sim in v1
            "unrealized_pl": "0.00",
            "cost_basis": str(self.cost_basis),
        }


class BrokerError(Exception):
    """Raised by the broker for canonical failure modes. Carries a
    simulated HTTP status code so the production code's status-checking
    paths (e.g., Bug Z attempt_close_with_status_check) can route on it."""
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class SimpleBroker:
    """Deterministic in-process broker oracle.

    Production-shape API the bridge calls (`submit_order`, `cancel_order`,
    `get_orders`, `get_positions`, `get_account`, `close_position`,
    `get_latest_quote`, `get_account_activities`) plus test-rule state-
    injection methods (`partial_fill`, `terminal_fill`, `reject`,
    `expire_at_eod`, `halt`, `resume`, `trigger_stop`, `set_equity`).
    """

    def __init__(self, *, starting_equity: float = 100_000.00) -> None:
        # Per-order state
        self.orders: dict[str, OrderRecord] = {}
        # Per-ticker state — synthesized from filled orders
        self.positions: dict[str, PositionRecord] = {}
        # Halt state
        self.halted_tickers: set[str] = set()
        # Account
        self._initial_equity = starting_equity
        self.equity: float = starting_equity
        self.cash: float = starting_equity

    # ── Production-shape methods ──────────────────────────────────

    async def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a primary order. Recognises Alpaca's `stop_loss` /
        `take_profit` keys for OTO classes and creates child legs.

        Bug AA.2 fix (2026-04-25): enforce buying-power discipline so
        the oracle behaves like a real broker. Reject buy orders whose
        notional exceeds buying_power (cash * 4 — paper margin approx).
        Without this, Hypothesis can drive equity negative even when
        limit-price discipline holds (counterexample: buy 971 @ $104 =
        $100,984 on a $100,000 account → stop @ $1 → -$100,013 loss).
        """
        symbol = (payload.get("symbol") or "").upper()
        if symbol in self.halted_tickers:
            raise BrokerError(f"{symbol} is halted", status_code=403)

        side = (payload.get("side") or "").lower()
        qty = int(float(payload.get("qty", 0) or 0))
        if qty <= 0:
            raise BrokerError("qty must be > 0", status_code=422)

        # Bug AA.2 — buying-power discipline (real Alpaca rejects with 403
        # `insufficient buying power`). Use limit_price for limit orders;
        # for market/stop orders without a known price, fall back to
        # stop_price (worst case) or skip the check.
        if side == "buy":
            est_price = (
                float(payload["limit_price"]) if payload.get("limit_price")
                else float(payload["stop_price"]) if payload.get("stop_price")
                else None
            )
            if est_price is not None:
                buying_power = self.cash * 4  # paper margin approx
                notional = qty * est_price
                if notional > buying_power:
                    raise BrokerError(
                        f"Bug AA: insufficient buying power — notional={notional:.2f} "
                        f"exceeds buying_power={buying_power:.2f}",
                        status_code=403,
                    )

        order_id = str(uuid.uuid4())
        order_class = payload.get("order_class", "")
        order = OrderRecord(
            id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            type=payload.get("type", "market"),
            limit_price=float(payload.get("limit_price")) if payload.get("limit_price") else None,
            stop_price=float(payload.get("stop_price")) if payload.get("stop_price") else None,
            time_in_force=payload.get("time_in_force", "day"),
            status="accepted",
            order_class=order_class,
        )
        self.orders[order_id] = order

        legs: list[dict[str, Any]] = []
        # Build OTO child legs
        if order_class == "oto":
            stop_loss = payload.get("stop_loss") or {}
            if stop_loss.get("stop_price"):
                stop_id = str(uuid.uuid4())
                stop_side = "sell" if side == "buy" else "buy"  # OTO stop covers the entry
                stop_record = OrderRecord(
                    id=stop_id,
                    symbol=symbol,
                    side=stop_side,
                    qty=qty,
                    type="stop",
                    limit_price=None,
                    stop_price=float(stop_loss["stop_price"]),
                    time_in_force=order.time_in_force,
                    status="held",            # OTO stop is held until parent fills
                    parent_id=order_id,
                    order_class="oto",
                )
                self.orders[stop_id] = stop_record
                order.child_order_ids.append(stop_id)
                legs.append(stop_record.to_dict())

        out = order.to_dict()
        out["legs"] = legs
        return out

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an order. Idempotent (canceling a canceled order
        returns success). Cancel cascades to child legs for OTO parents."""
        order = self.orders.get(order_id)
        if order is None:
            raise BrokerError(f"order {order_id} not found", status_code=404)
        if order.status not in TERMINAL_STATES:
            order.status = "canceled"
            order.terminal_at = datetime.now(timezone.utc)
            order.cancel_requested_at = order.terminal_at
            # Cascade to child legs
            for child_id in order.child_order_ids:
                child = self.orders.get(child_id)
                if child and child.status not in TERMINAL_STATES:
                    child.status = "canceled"
                    child.terminal_at = order.terminal_at
        return order.to_dict()

    async def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        symbols: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter orders by status + optional symbol; sort by submitted_at desc."""
        sf = (status or "all").lower()
        results = []
        for o in self.orders.values():
            if sf == "open":
                if o.status not in ("new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding"):
                    continue
            elif sf == "closed":
                if o.status not in TERMINAL_STATES:
                    continue
            # else "all" → include every order
            if symbols and o.symbol != symbols.upper():
                continue
            results.append(o.to_dict())
        results.sort(key=lambda d: d["submitted_at"], reverse=True)
        return results[:limit]

    async def get_positions(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.positions.values() if p.qty != 0]

    async def get_account(self) -> dict[str, Any]:
        return {
            "status": "ACTIVE",
            "equity": str(self.equity),
            "cash": str(self.cash),
            "buying_power": str(self.cash * 4),  # paper margin approx
            "daytrade_count": "0",
            "trading_blocked": False,
        }

    async def close_position(
        self, symbol: str, fill_price: float | None = None,
    ) -> dict[str, Any]:
        """Submit a market sell of net qty. Mimics Alpaca's
        DELETE /v2/positions/{symbol} which Alpaca implements as a
        market order behind the scenes.

        Bug AD fix (2026-04-25 evening): real Alpaca DELETE /positions
        routes through a market order that fills immediately at the
        prevailing quote. Previously this oracle CREATED the sell order
        but never filled it, leaving the position stuck at the broker.
        Surfaced by Track C Phase 2 invariant I12 in R1 → R3 → R11.
        Now: market-fills the position immediately and updates
        positions/equity/cash via _apply_fill_increment.

        Args:
          symbol:     ticker
          fill_price: optional override price. Defaults to pos.avg_entry_price
                     (deterministic, zero-slippage). Tests pass this to
                     simulate slippage.
        """
        symbol = symbol.upper()
        pos = self.positions.get(symbol)
        if pos is None or pos.qty == 0:
            raise BrokerError(f"position not found for {symbol}", status_code=404)
        side = "sell" if pos.qty > 0 else "buy"
        qty = abs(pos.qty)
        if fill_price is None:
            fill_price = float(pos.avg_entry_price)  # zero-slippage default
        else:
            fill_price = float(fill_price)
        order_id = str(uuid.uuid4())
        order = OrderRecord(
            id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
            status="filled",
            filled_qty=qty,
            filled_avg_price=fill_price,
            terminal_at=datetime.now(timezone.utc),
        )
        self.orders[order_id] = order
        # Bug AD: actually apply the close to positions
        signed_delta = qty if side == "buy" else -qty
        self._apply_fill_increment(symbol, signed_delta, fill_price)
        return order.to_dict()

    async def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """Synthetic NBBO. Returns mid = position avg_entry if held,
        else $1.00 default. Tests override via state injection."""
        symbol = symbol.upper()
        pos = self.positions.get(symbol)
        mid = pos.avg_entry_price if pos else 1.00
        return {
            "bp": str(round(mid - 0.01, 4)),
            "ap": str(round(mid + 0.01, 4)),
            "bs": "100",
            "as": "100",
        }

    async def get_account_activities(self) -> list[dict[str, Any]]:
        """Optional method (per Bug N spec). Returns FILL activities for
        all terminal-success orders. Tests can mark this method as
        absent via `del broker.get_account_activities` to simulate the
        production AlpacaDataClient shape."""
        out = []
        for o in self.orders.values():
            if o.status in TERMINAL_SUCCESS and o.filled_qty > 0:
                out.append({
                    "activity_type": "FILL",
                    "symbol": o.symbol,
                    "side": o.side,
                    "qty": str(o.filled_qty),
                    "price": str(o.filled_avg_price),
                    "transaction_time": (o.terminal_at or o.submitted_at).isoformat(),
                    "order_id": o.id,
                })
        return out

    # ── Test-rule state-injection methods ─────────────────────────

    def partial_fill(self, order_id: str, qty: int, price: float) -> None:
        """Increment filled_qty for an order; mark partially_filled.
        Update OTO child stop leg to active if parent first-fills.

        Bug AA fix (2026-04-25): enforce limit-price discipline so the
        oracle behaves like a real broker. Buy-limit fills must be at
        or below the limit price; sell-limit fills must be at or above.

        Bug AC fix (2026-04-25 evening): apply EVERY fill increment to
        positions (not just terminal). Real brokers increment positions
        on each fill; the oracle previously only updated on full fills,
        which caused tracker.filled_qty=1 vs broker.positions[]=0
        enumeration drift (surfaced by I12 in the R1 → R2 sequence).
        """
        order = self.orders.get(order_id)
        if order is None:
            raise BrokerError(f"order {order_id} not found")
        if order.status in TERMINAL_STATES:
            raise BrokerError(f"order {order_id} already terminal: {order.status}")
        if qty <= 0 or order.filled_qty + qty > order.qty:
            raise BrokerError(f"invalid partial fill qty {qty}")
        # Bug AA — limit-price discipline (real brokers enforce; oracle now does too)
        if order.type == "limit" and order.limit_price is not None:
            if order.side == "buy" and price > order.limit_price:
                raise BrokerError(
                    f"Bug AA: buy-limit @ {order.limit_price} cannot fill at {price}",
                    status_code=422,
                )
            if order.side == "sell" and price < order.limit_price:
                raise BrokerError(
                    f"Bug AA: sell-limit @ {order.limit_price} cannot fill at {price}",
                    status_code=422,
                )
        new_filled = order.filled_qty + qty
        # Update VWAP
        if order.filled_qty == 0:
            order.filled_avg_price = price
        else:
            order.filled_avg_price = (
                (order.filled_avg_price * order.filled_qty + price * qty) / new_filled
            )
        order.filled_qty = new_filled
        order.status = "partially_filled" if new_filled < order.qty else "filled"
        if order.status == "filled":
            order.terminal_at = datetime.now(timezone.utc)
        # Activate OTO stop leg on first fill of parent
        if order.order_class == "oto" and order.filled_qty > 0:
            for child_id in order.child_order_ids:
                child = self.orders.get(child_id)
                if child and child.status == "held":
                    child.status = "new"  # active stop
        # Bug AC — apply this fill increment to positions IMMEDIATELY,
        # not only on terminal. Mirrors real-broker behavior where
        # partial fills create / accrue positions.
        signed_delta = qty if order.side == "buy" else -qty
        self._apply_fill_increment(order.symbol, signed_delta, price)

    def terminal_fill(self, order_id: str, price: float) -> None:
        """Fill the residual; status=filled. Activates OTO stop leg.

        Bug AC fix: position update happens inside partial_fill() now
        (per-increment), so this method just delegates the residual."""
        order = self.orders.get(order_id)
        if order is None:
            raise BrokerError(f"order {order_id} not found")
        if order.status in TERMINAL_STATES:
            return  # already terminal — no-op
        residual_qty = order.qty - order.filled_qty
        if residual_qty > 0:
            self.partial_fill(order_id, residual_qty, price)
        # else already at qty — just mark filled (no position update needed,
        # was applied incrementally in prior partial_fill calls)
        if order.status != "filled":
            order.status = "filled"
            order.terminal_at = datetime.now(timezone.utc)

    def reject(self, order_id: str, reason: str = "rejected") -> None:
        """Mark order rejected. Terminal.

        Bug AE fix (2026-04-26): real brokers can't REJECT an order that
        has already partially filled — only CANCEL it. Reject means the
        broker said no from the start; once any fills land, the terminal
        state must be "canceled" (preserving the partials). Surfaced by
        I17 in R1 → R2 → R4 sequence.
        """
        order = self.orders.get(order_id)
        if order is None:
            raise BrokerError(f"order {order_id} not found")
        if order.status in TERMINAL_STATES:
            return
        # Bug AE: partial fills → canceled, not rejected
        if order.filled_qty > 0:
            order.status = "canceled"
        else:
            order.status = "rejected"
        order.terminal_at = datetime.now(timezone.utc)
        # Cascade to children — same status as parent
        for child_id in order.child_order_ids:
            child = self.orders.get(child_id)
            if child and child.status not in TERMINAL_STATES:
                child.status = order.status
                child.terminal_at = order.terminal_at

    def expire_at_eod(self) -> int:
        """All status=new + tif=day orders → status=expired. Returns
        count of expired orders."""
        count = 0
        for o in self.orders.values():
            if o.status in ("new", "accepted") and o.time_in_force == "day":
                o.status = "expired"
                o.terminal_at = datetime.now(timezone.utc)
                count += 1
        return count

    def halt(self, ticker: str) -> None:
        """Add ticker to halted set. Pending OTOs auto-rejected."""
        ticker = ticker.upper()
        self.halted_tickers.add(ticker)
        for o in self.orders.values():
            if o.symbol == ticker and o.status == "accepted":
                o.status = "rejected"
                o.terminal_at = datetime.now(timezone.utc)

    def resume(self, ticker: str) -> None:
        self.halted_tickers.discard(ticker.upper())

    def trigger_stop(self, order_id: str, fill_price: float | None = None) -> None:
        """A stop order fires as a market sell. Updates positions.

        Bug AB fix (2026-04-25): require an existing position to close.
        Without this, a stop firing against no underlying position
        creates a phantom short — the canonical "stop without position"
        error real Alpaca rejects with `position not found`. Surfaced by
        Track C Phase 2 invariant I12 (position_count_matches_broker)
        within the R1 → R9 → R8 sequence.
        """
        order = self.orders.get(order_id)
        if order is None:
            raise BrokerError(f"order {order_id} not found")
        if order.type != "stop":
            raise BrokerError(f"order {order_id} is not a stop")
        if order.status not in ("new",):
            raise BrokerError(f"stop order {order_id} not active: {order.status}")
        # Bug AB — require matching position to close. A stop can only fire
        # against a held position; otherwise we'd create a phantom short.
        existing = self.positions.get(order.symbol)
        # Stop side="sell" closes long; stop side="buy" closes short
        if existing is None or existing.qty == 0:
            raise BrokerError(
                f"Bug AB: stop {order_id} for {order.symbol} cannot fire — "
                f"no underlying position to close",
                status_code=404,
            )
        if order.side == "sell" and existing.qty <= 0:
            raise BrokerError(
                f"Bug AB: sell-stop {order_id} for {order.symbol} cannot fire — "
                f"existing position is short (qty={existing.qty})",
                status_code=422,
            )
        if order.side == "buy" and existing.qty >= 0:
            raise BrokerError(
                f"Bug AB: buy-stop {order_id} for {order.symbol} cannot fire — "
                f"existing position is long (qty={existing.qty})",
                status_code=422,
            )
        # Fill the stop at the trigger price (or stop_price if no fill_price)
        actual_fill = fill_price if fill_price is not None else order.stop_price
        if actual_fill is None:
            raise BrokerError("no fill price for stop trigger")
        # Stop fills the matching position size (not necessarily order.qty
        # if the original stop covered an OTO size that has since changed).
        # Conservative: fill min(order.qty, |existing.qty|).
        fill_size = min(int(order.qty), int(abs(existing.qty)))
        order.filled_qty = fill_size
        order.filled_avg_price = float(actual_fill)
        order.status = "filled"
        order.terminal_at = datetime.now(timezone.utc)
        # Bug AC discipline: apply as an incremental fill (sell-stop on
        # long → negative signed delta closes the position)
        signed_delta = fill_size if order.side == "buy" else -fill_size
        self._apply_fill_increment(order.symbol, signed_delta, float(actual_fill))

    def set_equity(self, value: float) -> None:
        self.equity = float(value)

    # ── Internal: position synthesis from fills ───────────────────

    def _apply_fill_increment(
        self, symbol: str, signed_qty_delta: int, fill_price: float,
    ) -> None:
        """Apply a single fill increment (signed) to the position dict.

        Bug AC fix: replaces the old "_update_position_from_fill takes
        the cumulative order" pattern. Each fill increment is applied
        independently so partial fills accrue positions correctly.

        Args:
          symbol:           ticker
          signed_qty_delta: positive for buy, negative for sell
          fill_price:       per-share fill price
        """
        pos = self.positions.get(symbol)
        if pos is None or pos.qty == 0:
            # Create new position (or replace zero-qty stub)
            self.positions[symbol] = PositionRecord(
                symbol=symbol, qty=signed_qty_delta,
                avg_entry_price=fill_price,
                side="long" if signed_qty_delta > 0 else "short",
                cost_basis=abs(signed_qty_delta) * fill_price,
            )
            self.cash -= signed_qty_delta * fill_price
            return
        old_qty = pos.qty
        new_qty = old_qty + signed_qty_delta
        if new_qty == 0:
            # Position closed by this fill — realize P&L
            realized = (fill_price - pos.avg_entry_price) * abs(old_qty)
            if old_qty < 0:
                realized = -realized
            self.equity += realized
            self.cash += abs(old_qty) * fill_price * (1 if old_qty > 0 else -1)
            self.positions.pop(symbol)
            return
        # Same-side accumulation OR partial close
        if (old_qty > 0) == (signed_qty_delta > 0):
            # Adding to existing position — recompute avg_entry
            new_basis = pos.cost_basis + abs(signed_qty_delta) * fill_price
            pos.avg_entry_price = new_basis / abs(new_qty)
            pos.cost_basis = new_basis
        # else: partial close (opposite side fill) — keep avg_entry, reduce qty
        pos.qty = new_qty
        pos.side = "long" if new_qty > 0 else "short"
        self.cash -= signed_qty_delta * fill_price

    # _update_position_from_fill removed in Bug AC fix (2026-04-25 evening).
    # Position synthesis is now incremental via _apply_fill_increment, called
    # per-fill from partial_fill / trigger_stop. This matches real-broker
    # behavior where positions accrue on each fill rather than only on terminal.
