"""
Pydantic request models matching Alpaca REST API schema.

These models validate incoming requests from the bot.
Response serialization is handled by OrderState/PositionState.to_alpaca_dict().
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    """POST /v2/orders request body."""
    symbol: str
    qty: Optional[int] = None
    notional: Optional[float] = None
    side: str  # buy / sell
    type: str  # market / limit / stop / stop_limit / trailing_stop
    time_in_force: str = "day"  # day / gtc / ioc / fok / opg / cls
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    trail_price: Optional[float] = None
    extended_hours: bool = False
    client_order_id: Optional[str] = None
    order_class: str = "simple"  # simple / oto / bracket / oco
    take_profit: Optional[dict[str, Any]] = None
    stop_loss: Optional[dict[str, Any]] = None


class ReplaceOrderRequest(BaseModel):
    """PATCH /v2/orders/{id} request body."""
    qty: Optional[int] = None
    time_in_force: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail: Optional[float] = None
    client_order_id: Optional[str] = None
