"""SimAlpacaClient — thin shim mirroring AlpacaDataClient's order-
submission surface, routed through SimExchange + FailureInjector.

Block C.1 of the strategy-harness session. Per stop condition:
"If SimExchange wiring requires invasive changes to AlpacaDataClient
that risk production behavior (>50 LOC across critical-path
modules), STOP and ship harness with a thinner shim that bypasses
production code."

This shim implements ONLY the order-submission methods the strategy
calls (submit_oto_order, submit_oto_short_order, cancel_order,
close_position, get_orders, get_positions). It does NOT implement
get_bars / get_snapshots — strategy uses those for real market data
which arena serves separately via DataEngine.

The strategy code (orchestrator + bridge) can be passed either a
real AlpacaDataClient OR this SimAlpacaClient as its `client`
parameter. Both expose the same async interface for the methods
strategy uses; the sim version routes to arena machinery.

Critical: when MOMENTUM_HALT_NEW_ENTRIES is set, real AlpacaDataClient
returns the halted_by_operator response. SimAlpacaClient mirrors this
EXACTLY — the same env-aware halt logic — so that arena replay tests
the halt switch's behavior in addition to other order paths.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .exchange import OrderState, SimExchange
from .failure_injector import FailureInjector

logger = logging.getLogger(__name__)


class SimAlpacaClient:
    """Mirrors AlpacaDataClient's order-submission surface, routes to
    arena's SimExchange + FailureInjector."""

    def __init__(
        self,
        *,
        sim_exchange: SimExchange,
        failure_injector: Optional[FailureInjector] = None,
    ) -> None:
        self._exchange = sim_exchange
        self._failure_injector = failure_injector or FailureInjector()

    # ── Halt switch parity (mirrors src/data/alpaca_client.py) ────

    @staticmethod
    def _check_halt(*, symbol: str, qty: int, side: str,
                    limit_price: float, stop_loss: float) -> Optional[dict]:
        """Same halt logic as production AlpacaDataClient. Returns
        the halted_by_operator dict if halt is on, else None."""
        halt_env = os.environ.get("MOMENTUM_HALT_NEW_ENTRIES", "").strip().lower()
        halt = halt_env in {"1", "true", "yes", "on"}
        if not halt:
            try:
                from config.settings import Settings as _S
                halt = bool(getattr(_S().execution, "halt_new_entries", False))
            except Exception:
                halt = False
        if halt:
            logger.warning(
                "SimAlpacaClient: D277 HALT_NEW_ENTRIES — refusing OTO "
                "%s %s qty=%d limit=$%.4f stop=$%.4f", side, symbol, qty,
                limit_price, stop_loss,
            )
            return {
                "id": "", "status": "halted_by_operator",
                "halt_reason": "MOMENTUM_HALT_NEW_ENTRIES",
                "symbol": symbol, "qty": str(qty), "side": side, "legs": [],
            }
        return None

    # ── Failure-injector check ────────────────────────────────────

    def _check_failure(self, *, symbol: str, action: str) -> Optional[dict]:
        """If the failure injector matches this action, raise an
        exception that mirrors httpx.HTTPStatusError shape (carries
        the response.text body that Bug AR fix reads)."""
        ts = datetime.now(timezone.utc)
        fail = self._failure_injector.evaluate(
            ticker=symbol, action=action, ts_utc=ts,
        )
        if fail is None:
            return None
        # Construct a minimal response-bearing exception
        class _FakeResp:
            status_code = fail.status_code
            text = fail.body
        class _FakeErr(Exception):
            response = _FakeResp()
            def __str__(self):
                return f"Client error '{fail.status_code}' (sim-injected: {fail.reason})"
        raise _FakeErr()

    # ── Order submission (parity with AlpacaDataClient) ──────────

    async def submit_oto_order(
        self, *, symbol: str, qty: int, limit_price: float, stop_loss: float,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        halt_resp = self._check_halt(
            symbol=symbol, qty=qty, side="buy",
            limit_price=limit_price, stop_loss=stop_loss,
        )
        if halt_resp is not None:
            return halt_resp
        self._check_failure(symbol=symbol, action="submit_oto_order")

        order = self._exchange.submit_order(
            symbol=symbol, qty=qty, side="buy", order_type="limit",
            time_in_force=time_in_force, limit_price=limit_price,
            order_class="oto",
            stop_loss={"stop_price": str(stop_loss)},
        )
        return order.to_alpaca_dict()

    async def submit_oto_short_order(
        self, *, symbol: str, qty: int, limit_price: float, stop_loss: float,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        halt_resp = self._check_halt(
            symbol=symbol, qty=qty, side="sell",
            limit_price=limit_price, stop_loss=stop_loss,
        )
        if halt_resp is not None:
            return halt_resp
        self._check_failure(symbol=symbol, action="submit_oto_short_order")

        order = self._exchange.submit_order(
            symbol=symbol, qty=qty, side="sell", order_type="limit",
            time_in_force=time_in_force, limit_price=limit_price,
            order_class="oto",
            stop_loss={"stop_price": str(stop_loss)},
        )
        return order.to_alpaca_dict()

    async def close_position(self, symbol: str) -> dict[str, Any]:
        self._check_failure(symbol=symbol, action="close_position")
        pos = self._exchange.positions.get(symbol)
        if pos is None or pos.qty <= 0:
            raise RuntimeError(f"no position for {symbol}")
        order = self._exchange.submit_order(
            symbol=symbol, qty=pos.qty, side="sell", order_type="market",
            time_in_force="day", order_class="simple",
        )
        return order.to_alpaca_dict()

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._check_failure(symbol="*", action="cancel_order")
        cancelled = self._exchange.cancel_order(order_id)
        if cancelled is None:
            return {"status": "canceled", "id": order_id}
        return cancelled.to_alpaca_dict()

    async def get_orders(
        self, status: str = "open", limit: int = 500,
    ) -> list[dict[str, Any]]:
        all_orders = list(self._exchange.orders.values())
        if status == "all":
            return [o.to_alpaca_dict() for o in all_orders]
        return [o.to_alpaca_dict() for o in all_orders if o.status == status]

    async def get_positions(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": p.symbol,
                "qty": str(p.qty),
                "avg_entry_price": str(p.avg_entry_price),
                "current_price": str(p.current_price),
                "side": "long" if p.qty > 0 else "short",
            }
            for p in self._exchange.positions.values()
        ]
