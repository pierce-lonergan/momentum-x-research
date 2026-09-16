"""
SimExchange — Matching engine + account/order/position state.

Holds all mutable state for one simulation instance. Processes orders
against NBBO on each clock tick. Supports market, limit, stop, stop_limit,
trailing_stop, and OTO (One-Triggers-Other) order types.

The bot uses OTO orders (D57 fix): entry limit + stop-loss where the stop
leg only activates after the entry fills. This is the critical order type.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from random import Random
from typing import Any, Optional

from .clock import SimClock
from .fill_model import AlpacaFillModel, Bar, Fill
from .spread_model import SpreadModel

logger = logging.getLogger(__name__)


@dataclass
class OrderState:
    """Mutable order state tracking the full Alpaca order lifecycle."""
    id: str
    client_order_id: str
    symbol: str
    side: str                       # buy / sell
    type: str                       # market / limit / stop / stop_limit / trailing_stop
    time_in_force: str              # day / gtc / ioc / fok / opg / cls
    qty: int
    filled_qty: int = 0
    filled_avg_price: float = 0.0
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    trail_price: Optional[float] = None
    status: str = "new"             # new / partially_filled / filled / canceled / expired / held
    order_class: str = "simple"     # simple / oto / bracket / oco
    legs: list["OrderState"] = field(default_factory=list)
    parent_id: Optional[str] = None
    created_at: str = ""
    filled_at: Optional[str] = None
    canceled_at: Optional[str] = None
    expired_at: Optional[str] = None
    replaced_by: Optional[str] = None
    replaces: Optional[str] = None
    hwm: Optional[float] = None     # High-water mark for trailing stops
    stop_triggered: bool = False    # For stop_limit orders
    asset_class: str = "us_equity"

    def to_alpaca_dict(self) -> dict[str, Any]:
        """Serialize to Alpaca REST API response format."""
        d: dict[str, Any] = {
            "id": self.id,
            "client_order_id": self.client_order_id,
            "created_at": self.created_at,
            "updated_at": self.created_at,
            "submitted_at": self.created_at,
            "filled_at": self.filled_at,
            "expired_at": self.expired_at,
            "canceled_at": self.canceled_at,
            "failed_at": None,
            "replaced_at": None,
            "replaced_by": self.replaced_by,
            "replaces": self.replaces,
            "asset_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.symbol)),
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "notional": None,
            "qty": str(self.qty),
            "filled_qty": str(self.filled_qty),
            "filled_avg_price": str(self.filled_avg_price) if self.filled_avg_price else None,
            "order_class": self.order_class,
            "order_type": self.type,
            "type": self.type,
            "side": self.side,
            "time_in_force": self.time_in_force,
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "stop_price": str(self.stop_price) if self.stop_price is not None else None,
            "trail_percent": str(self.trail_percent) if self.trail_percent is not None else None,
            "trail_price": str(self.trail_price) if self.trail_price is not None else None,
            "status": self.status,
            "extended_hours": False,
            "legs": [leg.to_alpaca_dict() for leg in self.legs] if self.legs else None,
            "hwm": str(self.hwm) if self.hwm is not None else None,
        }
        return d


@dataclass
class PositionState:
    """Mutable position state."""
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float
    cost_basis: float = 0.0
    side: str = "long"

    @property
    def market_value(self) -> float:
        return round(abs(self.qty) * self.current_price, 2)

    @property
    def unrealized_pl(self) -> float:
        return round(self.qty * (self.current_price - self.avg_entry_price), 2)

    @property
    def unrealized_plpc(self) -> float:
        if self.avg_entry_price == 0:
            return 0.0
        return round((self.current_price - self.avg_entry_price) / self.avg_entry_price, 6)

    @property
    def change_today(self) -> float:
        return 0.0  # Simplified — would need prev close tracking

    def to_alpaca_dict(self) -> dict[str, Any]:
        return {
            "asset_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.symbol)),
            "symbol": self.symbol,
            "exchange": "NASDAQ",
            "asset_class": "us_equity",
            "asset_marginable": True,
            "qty": str(self.qty),
            "avg_entry_price": str(self.avg_entry_price),
            "side": self.side,
            "market_value": str(self.market_value),
            "cost_basis": str(self.cost_basis),
            "unrealized_pl": str(self.unrealized_pl),
            "unrealized_plpc": str(self.unrealized_plpc),
            "unrealized_intraday_pl": str(self.unrealized_pl),
            "unrealized_intraday_plpc": str(self.unrealized_plpc),
            "current_price": str(self.current_price),
            "lastday_price": str(self.avg_entry_price),
            "change_today": str(self.change_today),
            "qty_available": str(self.qty),
        }


@dataclass
class AccountState:
    """Mutable account state."""
    cash: float = 100_000.0
    initial_cash: float = 100_000.0
    daytrade_count: int = 0
    account_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_alpaca_dict(self, positions: dict[str, PositionState]) -> dict[str, Any]:
        long_mv = sum(p.market_value for p in positions.values() if p.qty > 0)
        equity = self.cash + long_mv
        return {
            "id": self.account_id,
            "account_number": "SIM000001",
            "status": "ACTIVE",
            "crypto_status": "INACTIVE",
            "currency": "USD",
            "buying_power": str(round(equity * 4, 2)),  # 4x day-trade BP
            "regt_buying_power": str(round(equity * 2, 2)),
            "daytrading_buying_power": str(round(equity * 4, 2)),
            "non_marginable_buying_power": str(round(self.cash, 2)),
            "cash": str(round(self.cash, 2)),
            "accrued_fees": "0",
            "pending_transfer_in": "0",
            "portfolio_value": str(round(equity, 2)),
            "pattern_day_trader": self.daytrade_count >= 4,
            "trading_blocked": False,
            "transfers_blocked": False,
            "account_blocked": False,
            "created_at": "2025-01-01T00:00:00Z",
            "trade_suspended_by_user": False,
            "multiplier": "4",
            "shorting_enabled": False,
            "equity": str(round(equity, 2)),
            "last_equity": str(round(self.initial_cash, 2)),
            "long_market_value": str(round(long_mv, 2)),
            "short_market_value": "0",
            "initial_margin": str(round(long_mv / 2, 2)),
            "maintenance_margin": str(round(long_mv * 0.25, 2)),
            "last_maintenance_margin": "0",
            "sma": str(round(equity, 2)),
            "daytrade_count": self.daytrade_count,
        }


@dataclass
class TradeEvent:
    """Event emitted on order state changes for WebSocket broadcast."""
    event_type: str         # new, fill, partial_fill, canceled, expired, done_for_day
    order: OrderState
    price: Optional[float] = None
    qty: Optional[int] = None
    position_qty: int = 0
    timestamp: str = ""


class SimExchange:
    """
    Simulated exchange: matching engine + state management.

    Processes orders against NBBO each tick. Manages account, positions,
    and order lifecycle including OTO/bracket cascades.
    """

    def __init__(
        self,
        clock: SimClock,
        fill_model: AlpacaFillModel | None = None,
        spread_model: SpreadModel | None = None,
        initial_cash: float = 100_000.0,
        seed: int = 42,
    ):
        self.clock = clock
        self.fill_model = fill_model or AlpacaFillModel()
        self.spread_model = spread_model or SpreadModel()
        self.rng = Random(seed)

        # State
        self.account = AccountState(cash=initial_cash, initial_cash=initial_cash)
        self.orders: dict[str, OrderState] = {}
        self.positions: dict[str, PositionState] = {}

        # History
        self.trade_history: list[dict[str, Any]] = []
        self.signal_log: list[dict[str, Any]] = []

        # WebSocket event listeners
        self._listeners: list[asyncio.Queue] = []

        # Current market prices (updated each tick)
        self._market_prices: dict[str, float] = {}
        self._market_bars: dict[str, Bar] = {}

    @property
    def all_orders(self) -> list[OrderState]:
        return list(self.orders.values())

    @property
    def realized_pnl(self) -> float:
        return sum(t.get("pnl", 0.0) for t in self.trade_history)

    def register_listener(self, queue: asyncio.Queue) -> None:
        self._listeners.append(queue)

    def unregister_listener(self, queue: asyncio.Queue) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    async def _emit_event(self, event: TradeEvent) -> None:
        """Push event to all WebSocket listeners."""
        for q in self._listeners:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("WS listener queue full, dropping event")

    def update_market_data(self, bars: dict[str, Bar]) -> None:
        """Update current market prices from new bar data."""
        for symbol, bar in bars.items():
            self._market_bars[symbol] = bar
            self._market_prices[symbol] = bar.close
            # Update position current prices
            if symbol in self.positions:
                self.positions[symbol].current_price = bar.close

    async def on_tick(self, timestamp: datetime) -> None:
        """
        Called by SimClock each tick. Process all open orders.
        """
        is_open = self.clock.is_market_open

        for order in list(self.orders.values()):
            if order.status not in ("new", "partially_filled"):
                continue

            # Skip held orders (OTO children waiting for parent)
            if order.status == "held":
                continue

            bar = self._market_bars.get(order.symbol)
            if bar is None:
                continue

            bid, ask = self.spread_model.get_bid_ask(
                bar.close, bar.volume, timestamp
            )

            fill = self.fill_model.try_fill(order, bar, bid, ask, self.rng)
            if fill:
                await self._apply_fill(order, fill, timestamp)

        # End-of-day: cancel 'day' TIF orders at market close
        if not is_open and self.clock.now_et.hour >= 16:
            await self._cancel_day_orders(timestamp)

    async def _apply_fill(
        self, order: OrderState, fill: Fill, timestamp: datetime
    ) -> None:
        """Apply a fill to order + positions + account."""
        ts = timestamp.isoformat()

        # Update order state
        old_filled = order.filled_qty
        order.filled_qty += fill.qty
        # Weighted average fill price
        if order.filled_qty > 0:
            order.filled_avg_price = round(
                (old_filled * order.filled_avg_price + fill.qty * fill.price)
                / order.filled_qty,
                4,
            )

        if order.filled_qty >= order.qty:
            order.status = "filled"
            order.filled_at = ts
            fill_event = "fill"
        else:
            order.status = "partially_filled"
            fill_event = "partial_fill"

        # Update position
        pos_qty = self._update_position(order, fill)

        # Update account cash
        if order.side == "buy":
            self.account.cash -= fill.qty * fill.price
        else:
            self.account.cash += fill.qty * fill.price

        # Record trade
        self.trade_history.append({
            "timestamp": ts,
            "symbol": order.symbol,
            "side": order.side,
            "qty": fill.qty,
            "price": fill.price,
            "order_id": order.id,
        })

        logger.debug(
            "%s FILL: %s %s %d @ $%.2f (order %s)",
            ts, order.side.upper(), order.symbol,
            fill.qty, fill.price, order.id[:8],
        )

        # Emit WS event
        await self._emit_event(TradeEvent(
            event_type=fill_event,
            order=order,
            price=fill.price,
            qty=fill.qty,
            position_qty=pos_qty,
            timestamp=ts,
        ))

        # Handle OTO cascades: activate child orders on parent fill
        if order.status == "filled" and order.order_class in ("oto", "bracket"):
            await self._activate_children(order, ts)

        # Handle OCO: cancel sibling on fill
        if order.status == "filled" and order.parent_id:
            await self._cancel_oco_sibling(order, ts)

    def _update_position(self, order: OrderState, fill: Fill) -> int:
        """Update or create position from fill. Returns new position qty."""
        symbol = order.symbol
        pos = self.positions.get(symbol)

        if order.side == "buy":
            if pos is None:
                pos = PositionState(
                    symbol=symbol,
                    qty=fill.qty,
                    avg_entry_price=fill.price,
                    current_price=fill.price,
                    cost_basis=fill.qty * fill.price,
                )
                self.positions[symbol] = pos
            else:
                # Average up
                total_cost = pos.qty * pos.avg_entry_price + fill.qty * fill.price
                pos.qty += fill.qty
                pos.avg_entry_price = round(total_cost / pos.qty, 4) if pos.qty > 0 else 0
                pos.cost_basis = pos.qty * pos.avg_entry_price
                pos.current_price = fill.price
        else:  # sell
            if pos is not None:
                # Track P&L
                pnl = fill.qty * (fill.price - pos.avg_entry_price)
                if self.trade_history:
                    self.trade_history[-1]["pnl"] = round(pnl, 2)

                pos.qty -= fill.qty
                pos.current_price = fill.price
                if pos.qty <= 0:
                    del self.positions[symbol]
                    self.account.daytrade_count += 1
                    return 0

        return self.positions.get(symbol, PositionState(symbol, 0, 0, 0)).qty

    async def _activate_children(self, parent: OrderState, ts: str) -> None:
        """Activate held child orders after parent fills (OTO/bracket)."""
        for child in parent.legs:
            if child.status == "held":
                child.status = "new"
                child.created_at = ts
                logger.debug(
                    "OTO child activated: %s %s %s (parent %s filled)",
                    child.type, child.side, child.symbol, parent.id[:8],
                )
                await self._emit_event(TradeEvent(
                    event_type="new",
                    order=child,
                    timestamp=ts,
                ))

    async def _cancel_oco_sibling(self, filled_order: OrderState, ts: str) -> None:
        """When one OCO leg fills, cancel the other."""
        parent = self.orders.get(filled_order.parent_id or "")
        if parent is None:
            return
        for leg in parent.legs:
            if leg.id != filled_order.id and leg.status in ("new", "partially_filled"):
                leg.status = "canceled"
                leg.canceled_at = ts
                await self._emit_event(TradeEvent(
                    event_type="canceled",
                    order=leg,
                    timestamp=ts,
                ))

    async def _cancel_day_orders(self, timestamp: datetime) -> None:
        """Cancel all 'day' TIF orders at market close."""
        ts = timestamp.isoformat()
        for order in list(self.orders.values()):
            if (order.time_in_force == "day"
                    and order.status in ("new", "partially_filled")):
                order.status = "expired"
                order.expired_at = ts
                await self._emit_event(TradeEvent(
                    event_type="done_for_day",
                    order=order,
                    timestamp=ts,
                ))

    # ── Public API (called by REST endpoints) ───────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
        order_class: str = "simple",
        take_profit: dict | None = None,
        stop_loss: dict | None = None,
        client_order_id: str | None = None,
    ) -> OrderState:
        """Submit a new order. Returns the created OrderState."""
        ts = self.clock.now.isoformat()
        order_id = str(uuid.uuid4())

        order = OrderState(
            id=order_id,
            client_order_id=client_order_id or str(uuid.uuid4()),
            symbol=symbol.upper(),
            side=side,
            type=order_type,
            time_in_force=time_in_force,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            trail_percent=trail_percent,
            order_class=order_class,
            created_at=ts,
            status="new",
        )

        # Handle OTO: create held child orders
        if order_class == "oto" and stop_loss:
            child = OrderState(
                id=str(uuid.uuid4()),
                client_order_id=str(uuid.uuid4()),
                symbol=symbol.upper(),
                side="sell",
                type="stop",
                time_in_force=time_in_force,
                qty=qty,
                stop_price=stop_loss.get("stop_price"),
                limit_price=stop_loss.get("limit_price"),
                order_class="oto",
                parent_id=order_id,
                created_at=ts,
                status="held",  # Activates only after parent fills
            )
            order.legs.append(child)
            self.orders[child.id] = child

        # Handle bracket: entry + take-profit + stop-loss
        if order_class == "bracket":
            if take_profit:
                tp_child = OrderState(
                    id=str(uuid.uuid4()),
                    client_order_id=str(uuid.uuid4()),
                    symbol=symbol.upper(),
                    side="sell",
                    type="limit",
                    time_in_force=time_in_force,
                    qty=qty,
                    limit_price=take_profit.get("limit_price"),
                    order_class="bracket",
                    parent_id=order_id,
                    created_at=ts,
                    status="held",
                )
                order.legs.append(tp_child)
                self.orders[tp_child.id] = tp_child

            if stop_loss:
                sl_child = OrderState(
                    id=str(uuid.uuid4()),
                    client_order_id=str(uuid.uuid4()),
                    symbol=symbol.upper(),
                    side="sell",
                    type="stop",
                    time_in_force=time_in_force,
                    qty=qty,
                    stop_price=stop_loss.get("stop_price"),
                    limit_price=stop_loss.get("limit_price"),
                    order_class="bracket",
                    parent_id=order_id,
                    created_at=ts,
                    status="held",
                )
                order.legs.append(sl_child)
                self.orders[sl_child.id] = sl_child

        self.orders[order_id] = order

        logger.info(
            "ORDER submitted: %s %s %d %s @ %s (id=%s, class=%s)",
            side, symbol, qty, order_type,
            limit_price or "MKT", order_id[:8], order_class,
        )

        return order

    def cancel_order(self, order_id: str) -> OrderState | None:
        """Cancel an order by ID."""
        order = self.orders.get(order_id)
        if order is None:
            return None
        if order.status in ("filled", "canceled", "expired"):
            return None  # Can't cancel completed orders

        ts = self.clock.now.isoformat()
        order.status = "canceled"
        order.canceled_at = ts

        # Cancel children too
        for leg in order.legs:
            if leg.status in ("new", "partially_filled", "held"):
                leg.status = "canceled"
                leg.canceled_at = ts

        return order

    def get_orders(
        self,
        status: str = "open",
        symbols: str | None = None,
        limit: int = 100,
    ) -> list[OrderState]:
        """Get orders filtered by status and symbols."""
        result = []
        symbol_set = set(symbols.split(",")) if symbols else None

        for order in self.orders.values():
            # Skip child orders (they appear inside parent.legs)
            if order.parent_id:
                continue

            if status == "open" and order.status not in ("new", "partially_filled", "held"):
                continue
            elif status == "closed" and order.status not in ("filled", "canceled", "expired"):
                continue
            # status == "all" returns everything

            if symbol_set and order.symbol not in symbol_set:
                continue

            result.append(order)

        return result[:limit]

    def close_position(self, symbol: str, qty: float | None = None) -> OrderState | None:
        """Close a position by submitting a market sell order."""
        pos = self.positions.get(symbol.upper())
        if pos is None:
            return None

        close_qty = int(qty) if qty else pos.qty
        close_qty = min(close_qty, pos.qty)

        return self.submit_order(
            symbol=symbol.upper(),
            qty=close_qty,
            side="sell",
            order_type="market",
            time_in_force="day",
        )
