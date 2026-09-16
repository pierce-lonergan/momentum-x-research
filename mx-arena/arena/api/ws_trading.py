"""
WebSocket trading stream — order lifecycle events (JSON).

Implements the Alpaca trading stream at /stream.
Protocol: JSON text frames.

Handshake:
1. Client sends: {"action":"authenticate","data":{"key_id":"...","secret_key":"..."}}
2. Server responds: {"data":{"status":"authorized"}}
3. Client sends: {"action":"listen","data":{"streams":["trade_updates"]}}
4. Server responds: {"data":{"streams":["trade_updates"]}}
5. Server pushes: {"stream":"trade_updates","data":{"event":"fill","order":{...},...}}
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from ..exchange import SimExchange, TradeEvent

logger = logging.getLogger(__name__)


async def trading_stream_handler(
    ws: WebSocket,
    exchange: SimExchange,
) -> None:
    """Handle one WebSocket trading stream connection."""
    await ws.accept()

    try:
        # Step 1: Authentication
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)
        if msg.get("action") != "authenticate":
            await ws.close(code=4001, reason="Expected authenticate")
            return

        # Accept any credentials in sim mode
        await ws.send_json([{
            "T": "success",
            "msg": "authenticated",
        }])

        # Step 2: Listen/Subscribe
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)
        streams = msg.get("data", {}).get("streams", [])
        await ws.send_json([{
            "T": "success",
            "msg": "connected",
        }])

        logger.info("Trading stream connected, subscribed to: %s", streams)

        # Step 3: Event loop — push trade updates
        queue: asyncio.Queue[TradeEvent] = asyncio.Queue(maxsize=1000)
        exchange.register_listener(queue)

        try:
            while True:
                event = await queue.get()
                order_dict = event.order.to_alpaca_dict()

                payload = {
                    "stream": "trade_updates",
                    "data": {
                        "event": event.event_type,
                        "order": order_dict,
                        "timestamp": event.timestamp,
                        "price": str(event.price) if event.price else None,
                        "qty": str(event.qty) if event.qty else None,
                        "position_qty": str(event.position_qty),
                    },
                }
                await ws.send_json(payload)
        finally:
            exchange.unregister_listener(queue)

    except WebSocketDisconnect:
        logger.info("Trading stream client disconnected")
    except asyncio.TimeoutError:
        logger.warning("Trading stream: authentication timeout")
        await ws.close(code=4002, reason="Auth timeout")
    except Exception as e:
        logger.error("Trading stream error: %s", e)
        try:
            await ws.close(code=4000, reason=str(e))
        except Exception:
            pass
