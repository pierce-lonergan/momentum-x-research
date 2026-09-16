"""
WebSocket market data stream — bars/quotes/trades (msgpack binary).

Implements the Alpaca market data stream at /v2/{feed}.
Protocol: msgpack binary frames (NOT JSON).

Handshake:
1. Client sends JSON: {"action":"auth","key":"...","secret":"..."}
2. Server sends msgpack: [{"T":"success","msg":"authenticated"}]
3. Client sends JSON: {"action":"subscribe","bars":["*"],"quotes":["AAPL"]}
4. Server sends msgpack: [{"T":"subscription","bars":["*"],"quotes":["AAPL"]}]
5. Server pushes msgpack: [{"T":"b","S":"AAPL","o":150,"h":151,...}]
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..data_engine import DataEngine

logger = logging.getLogger(__name__)

try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False
    logger.warning("msgpack not installed — market data stream will use JSON fallback")


def _pack(data: list[dict]) -> bytes:
    """Pack data as msgpack or JSON bytes fallback."""
    if HAS_MSGPACK:
        return msgpack.packb(data)
    return json.dumps(data).encode()


async def market_data_stream_handler(
    ws: WebSocket,
    data_engine: DataEngine,
    feed: str = "sip",
) -> None:
    """Handle one WebSocket market data stream connection."""
    await ws.accept()

    subscribed_bars: set[str] = set()
    subscribed_quotes: set[str] = set()
    subscribed_trades: set[str] = set()

    try:
        # Step 1: Authentication (10-second window)
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)

        await ws.send_bytes(_pack([{
            "T": "success",
            "msg": "authenticated",
        }]))

        # Step 2: Read subscription messages in background
        async def read_loop():
            try:
                while True:
                    raw = await ws.receive_text()
                    msg = json.loads(raw)
                    action = msg.get("action", "")

                    if action == "subscribe":
                        new_bars = msg.get("bars", [])
                        new_quotes = msg.get("quotes", [])
                        new_trades = msg.get("trades", [])
                        subscribed_bars.update(new_bars)
                        subscribed_quotes.update(new_quotes)
                        subscribed_trades.update(new_trades)

                        await ws.send_bytes(_pack([{
                            "T": "subscription",
                            "trades": list(subscribed_trades),
                            "quotes": list(subscribed_quotes),
                            "bars": list(subscribed_bars),
                        }]))

                        logger.debug(
                            "Market data subscribed: bars=%s quotes=%s trades=%s",
                            new_bars, new_quotes, new_trades,
                        )

                    elif action == "unsubscribe":
                        for s in msg.get("bars", []):
                            subscribed_bars.discard(s)
                        for s in msg.get("quotes", []):
                            subscribed_quotes.discard(s)
                        for s in msg.get("trades", []):
                            subscribed_trades.discard(s)
            except (WebSocketDisconnect, Exception):
                pass

        read_task = asyncio.create_task(read_loop())

        # Step 3: Push market data as it arrives
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        data_engine.register_market_listener(queue)

        try:
            while True:
                event = await queue.get()
                messages: list[dict] = []
                symbol = event.get("symbol", "")

                if event.get("type") == "bar":
                    # Check if subscribed (support "*" wildcard)
                    if "*" in subscribed_bars or symbol in subscribed_bars:
                        messages.append({
                            "T": "b",
                            "S": symbol,
                            "o": event["open"],
                            "h": event["high"],
                            "l": event["low"],
                            "c": event["close"],
                            "v": event["volume"],
                            "t": event["timestamp"],
                            "n": event.get("trade_count", 0),
                            "vw": event.get("vwap", 0),
                        })

                    # Also emit synthetic quote for VWAP computation
                    if "*" in subscribed_quotes or symbol in subscribed_quotes:
                        mid = event["close"]
                        spread = mid * 0.001  # Simplified spread
                        messages.append({
                            "T": "q",
                            "S": symbol,
                            "bp": round(mid - spread, 4),
                            "bs": 100,
                            "ap": round(mid + spread, 4),
                            "as": 100,
                            "t": event["timestamp"],
                            "c": ["R"],
                            "z": "C",
                        })

                    # Emit synthetic trade for VWAP accumulator
                    if "*" in subscribed_trades or symbol in subscribed_trades:
                        messages.append({
                            "T": "t",
                            "S": symbol,
                            "p": event["close"],
                            "s": event["volume"],
                            "t": event["timestamp"],
                            "x": "V",
                            "c": ["@", "T"],
                            "z": "C",
                            "i": 0,
                        })

                if messages:
                    await ws.send_bytes(_pack(messages))
        finally:
            data_engine.unregister_market_listener(queue)
            read_task.cancel()

    except WebSocketDisconnect:
        logger.info("Market data stream client disconnected")
    except asyncio.TimeoutError:
        logger.warning("Market data stream: auth timeout")
        await ws.close(code=4002, reason="Auth timeout")
    except Exception as e:
        logger.error("Market data stream error: %s", e)
        try:
            await ws.close(code=4000, reason=str(e))
        except Exception:
            pass
