"""
MOMENTUM-X Alpaca WebSocket Streaming Client

### ARCHITECTURAL CONTEXT
Node ID: data.websocket_client
Graph Link: docs/memory/graph_state.json → "data.websocket_client"

### RESEARCH BASIS
Real-time streaming via Alpaca WebSocket (SIP feed) per ADR-002 §4.
Condition code filtering per ADR-004 §3 (CONSTRAINT-002).
Subscription chunking per ADR-004 §4 (CONSTRAINT-005: 16KB limit).
VWAP computation resolves H-006: VWAP = Σ(price × volume) / Σ(volume).

### CRITICAL INVARIANTS
1. SIP feed mandatory for production (ADR-004 §1, CONSTRAINT-001).
2. Condition codes Z, U, T, 4, C excluded from regular-session VWAP/RVOL.
3. Subscription frames limited to 400 symbols (CONSTRAINT-005).
4. Exponential backoff reconnection: 1s→30s cap (ADR-002 §4).
5. Auth frame MUST be first message after connection (Alpaca WebSocket spec).
"""

from __future__ import annotations

import asyncio
import src.utils.fast_json as json  # D87: orjson drop-in (~3-10x faster)
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from src.utils.trade_filter import (
    is_valid_premarket_trade,
    is_valid_regular_session_trade,
    chunk_symbols,
)

logger = logging.getLogger(__name__)


# ── Stream Configuration ─────────────────────────────────────

@dataclass(frozen=True)
class StreamConfig:
    """
    WebSocket stream configuration.

    Ref: ADR-004 §1 (SIP mandatory), CONSTRAINT-005 (16KB frame limit)
    """

    feed: str = "sip"
    max_symbols_per_subscribe: int = 400  # CONSTRAINT-005: well under 16KB
    reconnect_base_seconds: float = 1.0  # ADR-002 §4
    reconnect_max_seconds: float = 30.0  # ADR-002 §4
    health_ping_interval: int = 30  # ADR-002 §4
    # D130: Optional URL override for mx-arena simulator
    url_override: str = ""

    @property
    def stream_url(self) -> str:
        """WebSocket URL for the configured feed."""
        if self.url_override:
            return self.url_override
        return f"wss://stream.data.alpaca.markets/v2/{self.feed}"


# ── Streaming Data Models ────────────────────────────────────

@dataclass
class TradeUpdate:
    """
    A single executed trade from the SIP feed.

    Ref: DATA-001-EXT §3.3.1 (Trade schema)
    Ref: ADR-004 §3 (Condition code filtering)
    """

    symbol: str
    trade_id: int
    exchange: str
    price: float
    size: int
    conditions: list[str]
    timestamp: str
    tape: str

    @property
    def is_valid_regular_session(self) -> bool:
        """Trade valid for regular-session indicators (VWAP, RVOL)?"""
        return is_valid_regular_session_trade(self.conditions)

    @property
    def is_valid_premarket(self) -> bool:
        """Trade valid for pre-market analysis (allows U/T codes)?"""
        return is_valid_premarket_trade(self.conditions)


@dataclass
class QuoteUpdate:
    """
    Top-of-book bid/ask update.

    Ref: DATA-001-EXT §3.3.2 (Quote schema)
    """

    symbol: str
    bid_exchange: str
    bid_price: float
    bid_size: int
    ask_exchange: str
    ask_price: float
    ask_size: int
    timestamp: str

    @property
    def spread(self) -> float:
        """Absolute bid-ask spread."""
        return self.ask_price - self.bid_price

    @property
    def spread_pct(self) -> float:
        """Spread as percentage of midpoint. Used by risk agent (>3% → veto)."""
        mid = (self.ask_price + self.bid_price) / 2
        if mid <= 0:
            return 0.0
        return self.spread / mid


@dataclass
class BarUpdate:
    """
    Aggregated minute bar (OHLCV).

    Ref: DATA-001-EXT §3.3.3 (Bar schema)
    Note: Bars arrive AFTER minute close — use trade aggregation for instant reaction.
    """

    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: str


# ── VWAP Accumulator (Resolves H-006) ───────────────────────

class VWAPAccumulator:
    """
    Real-time Volume-Weighted Average Price computation.

    Resolves H-006: Previously approximated as price × 0.98 in orchestrator.
    Now computed from actual trade stream:

        VWAP = Σ(price_i × volume_i) / Σ(volume_i)

    Only includes trades that pass regular-session condition filter
    (ADR-004 §3: excludes Z, U, T, 4, C, I, W).

    Node ID: data.websocket_client.VWAPAccumulator
    Ref: MOMENTUM_LOGIC.md §4 (VWAP breakout confirmation)
    """

    def __init__(self) -> None:
        self._cumulative_pv: float = 0.0  # Σ(price × volume)
        self._cumulative_vol: int = 0  # Σ(volume)
        self._trade_count: int = 0

    def add_trade(self, price: float, volume: int) -> None:
        """
        Add a trade to the VWAP accumulator.

        Args:
            price: Trade execution price.
            volume: Trade size (shares).
        """
        self._cumulative_pv += price * volume
        self._cumulative_vol += volume
        self._trade_count += 1

    @property
    def vwap(self) -> float:
        """Current VWAP. Returns 0.0 if no trades accumulated."""
        if self._cumulative_vol == 0:
            return 0.0
        return self._cumulative_pv / self._cumulative_vol

    @property
    def total_volume(self) -> int:
        """Total volume accumulated today."""
        return self._cumulative_vol

    @property
    def trade_count(self) -> int:
        """Number of trades processed."""
        return self._trade_count

    def reset(self) -> None:
        """Reset for new trading session."""
        self._cumulative_pv = 0.0
        self._cumulative_vol = 0
        self._trade_count = 0


# ── Stream Message Processor ─────────────────────────────────

class AlpacaStreamProcessor:
    """
    Parses and filters Alpaca WebSocket messages.

    Separates protocol concerns (parsing, filtering) from transport
    (actual WebSocket connection/reconnection) for testability.

    Node ID: data.websocket_client.processor
    Ref: DATA-001-EXT §3.3 (Data schemas)
    Ref: ADR-004 §3 (Condition code filtering)
    """

    def parse_trade(self, msg: dict[str, Any]) -> TradeUpdate | None:
        """
        Parse a trade message from the WebSocket stream.

        Args:
            msg: Raw JSON dict with T="t" from Alpaca.

        Returns:
            TradeUpdate with condition-based validity flags, or None on error.
        """
        try:
            return TradeUpdate(
                symbol=msg["S"],
                trade_id=msg.get("i", 0),
                exchange=msg.get("x", ""),
                price=float(msg["p"]),
                size=int(msg["s"]),
                conditions=msg.get("c", []),
                timestamp=msg["t"],
                tape=msg.get("z", ""),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to parse trade message: %s — %s", e, msg)
            return None

    def parse_quote(self, msg: dict[str, Any]) -> QuoteUpdate | None:
        """Parse a quote (top-of-book) message."""
        try:
            return QuoteUpdate(
                symbol=msg["S"],
                bid_exchange=msg.get("bx", ""),
                bid_price=float(msg["bp"]),
                bid_size=int(msg["bs"]),
                ask_exchange=msg.get("ax", ""),
                ask_price=float(msg["ap"]),
                ask_size=int(msg["as"]),
                timestamp=msg["t"],
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to parse quote message: %s — %s", e, msg)
            return None

    def parse_bar(self, msg: dict[str, Any]) -> BarUpdate | None:
        """Parse a minute bar (OHLCV) message."""
        try:
            return BarUpdate(
                symbol=msg["S"],
                open=float(msg["o"]),
                high=float(msg["h"]),
                low=float(msg["l"]),
                close=float(msg["c"]),
                volume=int(msg["v"]),
                timestamp=msg["t"],
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to parse bar message: %s — %s", e, msg)
            return None

    def dispatch_message(
        self, msg: dict[str, Any]
    ) -> TradeUpdate | QuoteUpdate | BarUpdate | None:
        """
        Route a WebSocket message to the correct parser based on 'T' field.

        Args:
            msg: Raw JSON dict from WebSocket.

        Returns:
            Parsed update object, or None for unknown/control messages.
        """
        msg_type = msg.get("T")
        if msg_type == "t":
            return self.parse_trade(msg)
        elif msg_type == "q":
            return self.parse_quote(msg)
        elif msg_type == "b":
            return self.parse_bar(msg)
        else:
            # Control messages (success, error, subscription) — logged only
            if msg_type not in ("success", "error", "subscription"):
                logger.debug("Unknown message type: %s", msg_type)
            return None

    @staticmethod
    def build_auth_message(api_key: str, secret_key: str) -> dict[str, str]:
        """
        Build WebSocket authentication frame.

        MUST be the first message sent after connection per Alpaca spec.
        Ref: DATA-001-EXT §2.3 (WebSocket authentication)
        """
        return {
            "action": "auth",
            "key": api_key,
            "secret": secret_key,
        }

    @staticmethod
    def build_subscribe_message(
        symbols: list[str],
        trades: bool = True,
        quotes: bool = False,
        bars: bool = True,
    ) -> dict[str, Any]:
        """
        Build WebSocket subscription frame.

        Args:
            symbols: Tickers to subscribe to.
            trades: Subscribe to trade stream.
            quotes: Subscribe to quote stream (high bandwidth — use sparingly).
            bars: Subscribe to minute bar stream.

        Returns:
            JSON-serializable subscription message.

        Note: Caller must chunk symbols to ≤400 per frame (CONSTRAINT-005).
        """
        msg: dict[str, Any] = {"action": "subscribe"}
        if trades:
            msg["trades"] = symbols
        if quotes:
            msg["quotes"] = symbols
        if bars:
            msg["bars"] = symbols
        return msg


# ── WebSocket Connection Manager ─────────────────────────────

class AlpacaWebSocketClient:
    """
    Production WebSocket client with reconnection and error handling.

    Node ID: data.websocket_client
    Ref: ADR-002 §4 (Reconnection: exponential backoff 1s→30s)
    Ref: ADR-004 §4 (Subscription chunking)
    Ref: DATA-001-EXT CONSTRAINT-005 (16KB frame limit)

    Usage:
        client = AlpacaWebSocketClient(config, api_key, secret_key)
        client.on_trade = my_trade_handler
        await client.connect(symbols=["AAPL", "TSLA"])
    """

    def __init__(
        self,
        config: StreamConfig | None = None,
        api_key: str = "",
        secret_key: str = "",
    ) -> None:
        self._config = config or StreamConfig()
        self._api_key = api_key
        self._secret_key = secret_key
        self._processor = AlpacaStreamProcessor()
        self._vwap: dict[str, VWAPAccumulator] = {}
        self._running = False
        self._reconnect_attempts = 0
        self._ws: Any = None  # Active WebSocket connection for dynamic subscription
        self._subscribed_symbols: set[str] = set()  # Track subscribed symbols

        # Callback hooks — users register handlers
        self.on_trade: Callable[[TradeUpdate], None] | None = None
        self.on_quote: Callable[[QuoteUpdate], None] | None = None
        self.on_bar: Callable[[BarUpdate], None] | None = None
        self.on_error: Callable[[Exception], None] | None = None

    def get_vwap(self, symbol: str) -> float:
        """
        Get current VWAP for a symbol.

        Resolves H-006: Real VWAP from streaming trades instead of price × 0.98.

        Returns:
            VWAP value, or 0.0 if no trades accumulated.
        """
        acc = self._vwap.get(symbol)
        return acc.vwap if acc else 0.0

    def get_volume(self, symbol: str) -> int:
        """Get accumulated volume for a symbol today."""
        acc = self._vwap.get(symbol)
        return acc.total_volume if acc else 0

    def reset_session(self) -> None:
        """Reset all VWAP accumulators for new trading day."""
        for acc in self._vwap.values():
            acc.reset()
        self._vwap.clear()
        logger.info("Session reset: VWAP accumulators cleared")

    async def connect(self, symbols: list[str]) -> None:
        """
        Connect to Alpaca WebSocket and start streaming.

        Handles:
        1. WebSocket connection
        2. Authentication (first frame)
        3. Chunked subscription (≤400 symbols per frame)
        4. Message processing loop
        5. Automatic reconnection with exponential backoff

        Args:
            symbols: List of ticker symbols to stream.
        """
        try:
            import websockets
        except ImportError:
            logger.error(
                "websockets package required. Install with: pip install websockets"
            )
            return

        self._running = True
        url = self._config.stream_url

        while self._running:
            try:
                # D121 BUG-R1: On reconnect, include dynamically added symbols.
                # _subscribed_symbols accumulates all symbols added via add_symbols().
                _all_symbols = list(set(symbols) | self._subscribed_symbols)
                logger.info(
                    "Connecting to %s (%d symbols, %d dynamic)...",
                    url, len(_all_symbols), len(self._subscribed_symbols),
                )

                async with websockets.connect(url) as ws:
                    # D150: Check _running again after connect — stop() may have
                    # been called during the connection handshake
                    if not self._running:
                        break
                    self._ws = ws  # D121 BUG-P9: Store for dynamic subscription
                    # Step 1: Authenticate (MUST be first message)
                    auth_msg = self._processor.build_auth_message(
                        self._api_key, self._secret_key
                    )
                    await ws.send(json.dumps(auth_msg))

                    # Wait for auth response
                    auth_response = await ws.recv()
                    auth_data = json.loads(auth_response)
                    logger.info("Auth response: %s", auth_data)

                    # Sweep fix: Validate auth succeeded before subscribing.
                    # Previously, auth failures were logged as info and the
                    # connection continued unauthenticated, silently failing.
                    _auth_msgs = auth_data if isinstance(auth_data, list) else [auth_data]
                    for _am in _auth_msgs:
                        if isinstance(_am, dict) and _am.get("msg") == "auth_failed":
                            raise ConnectionError(
                                f"WebSocket auth failed: {_am.get('msg', auth_data)}"
                            )

                    # Step 2: Subscribe in chunks (CONSTRAINT-005)
                    chunks = chunk_symbols(
                        _all_symbols,
                        max_per_chunk=self._config.max_symbols_per_subscribe,
                    )
                    for chunk in chunks:
                        sub_msg = self._processor.build_subscribe_message(
                            symbols=chunk,
                            trades=True,
                            quotes=False,  # High bandwidth — enable per-symbol
                            bars=True,
                        )
                        await ws.send(json.dumps(sub_msg))
                        await asyncio.sleep(0.1)  # 100ms between chunks

                    self._subscribed_symbols.update(_all_symbols)
                    logger.info(
                        "Subscribed to %d symbols in %d chunks",
                        len(_all_symbols), len(chunks),
                    )
                    self._reconnect_attempts = 0  # Reset on successful connect

                    # Step 3: Message processing loop
                    async for raw_msg in ws:
                        try:
                            messages = json.loads(raw_msg)
                            # Alpaca sends arrays of messages
                            if isinstance(messages, list):
                                for msg in messages:
                                    self._handle_message(msg)
                            elif isinstance(messages, dict):
                                self._handle_message(messages)
                        except json.JSONDecodeError as e:
                            logger.warning("Invalid JSON from WebSocket: %s", e)

            except Exception as e:
                if not self._running:
                    break

                # Exponential backoff reconnection (ADR-002 §4)
                self._reconnect_attempts += 1
                backoff = min(
                    self._config.reconnect_base_seconds * (2 ** self._reconnect_attempts),
                    self._config.reconnect_max_seconds,
                )
                logger.warning(
                    "WebSocket disconnected: %s. Reconnecting in %.1fs (attempt %d)...",
                    e, backoff, self._reconnect_attempts,
                )

                if self.on_error:
                    self.on_error(e)

                await asyncio.sleep(backoff)

    def stop(self) -> None:
        """Stop the WebSocket client gracefully."""
        self._running = False
        self._ws = None
        logger.info("WebSocket client stopping...")

    async def add_symbols(self, symbols: list[str]) -> int:
        """
        D121 BUG-P9: Subscribe to new symbols on the existing WebSocket connection.

        Only subscribes symbols not already in _subscribed_symbols.
        Returns count of newly subscribed symbols.
        """
        new_symbols = [s for s in symbols if s not in self._subscribed_symbols]
        # D121 BUG-S12: Check .closed as well — after reconnect, _ws may
        # point to a closed connection before the new one is established.
        if not new_symbols or self._ws is None or getattr(self._ws, 'closed', True):
            return 0
        try:
            chunks = chunk_symbols(
                new_symbols,
                max_per_chunk=self._config.max_symbols_per_subscribe,
            )
            for chunk in chunks:
                sub_msg = self._processor.build_subscribe_message(
                    symbols=chunk, trades=True, quotes=False, bars=True,
                )
                await self._ws.send(json.dumps(sub_msg))
                await asyncio.sleep(0.1)
            self._subscribed_symbols.update(new_symbols)
            logger.info(
                "D121 BUG-P9: Dynamically subscribed %d new symbols: %s",
                len(new_symbols), new_symbols[:10],
            )
        except Exception as e:
            logger.warning("D121 BUG-P9: Dynamic subscription failed: %s", e)
            return 0
        return len(new_symbols)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """
        Process a single message: parse, filter, accumulate VWAP, dispatch.

        This is the hot path — keep it fast.
        """
        update = self._processor.dispatch_message(msg)
        if update is None:
            return

        if isinstance(update, TradeUpdate):
            # Accumulate VWAP from valid regular-session trades (H-006 resolution)
            if update.is_valid_regular_session:
                if update.symbol not in self._vwap:
                    self._vwap[update.symbol] = VWAPAccumulator()
                self._vwap[update.symbol].add_trade(update.price, update.size)

            if self.on_trade:
                self.on_trade(update)

        elif isinstance(update, QuoteUpdate):
            if self.on_quote:
                self.on_quote(update)

        elif isinstance(update, BarUpdate):
            if self.on_bar:
                self.on_bar(update)


# ── Trade Updates Stream (Order Events) ──────────────────────


class TradeUpdatesStream:
    """
    WebSocket stream for Alpaca order/trade events (fills, cancels, etc.).

    ### ARCHITECTURAL CONTEXT
    Node ID: data.trade_updates_stream
    Graph Link: data.websocket_client → data.trade_updates

    ### RESEARCH BASIS
    Resolves H-008: Trailing stop management via fill detection.
    Two-phase order strategy (ADR-007):
        Phase 1: Submit bracket order with fixed stop.
        Phase 2: On fill, cancel fixed stop → submit trailing stop.

    ### CRITICAL INVARIANTS
    1. Connects to wss://paper-api.alpaca.markets/stream (paper trading).
    2. Auth frame MUST be first message, then listen on "trade_updates" stream.
    3. TrailingStopManager callbacks dispatched for fill + cancel events.
    4. Reconnection with exponential backoff (same as market data stream).
    5. If disconnected before fill, original bracket stop remains active (safety).

    Usage:
        from src.data.trade_updates import TrailingStopManager
        stream = TradeUpdatesStream(api_key, secret_key)
        stream.trailing_stop_manager = TrailingStopManager()
        await stream.connect()
    """

    # Paper vs Live endpoint (paper by default, ADR-004 §1)
    PAPER_URL = "wss://paper-api.alpaca.markets/stream"
    LIVE_URL = "wss://api.alpaca.markets/stream"

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        url_override: str = "",  # D130: mx-arena simulator override
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        if url_override:
            self._url = url_override
        else:
            self._url = self.PAPER_URL if paper else self.LIVE_URL
        self._running = False
        self._reconnect_attempts = 0

        # Integration points
        self.trailing_stop_manager: Any = None  # TrailingStopManager
        self.on_trade_update: Callable[[Any], None] | None = None
        self.on_error: Callable[[Exception], None] | None = None

    async def connect(self) -> None:
        """
        Connect to Alpaca trade_updates WebSocket.

        Event flow:
        1. Authenticate with API key/secret
        2. Listen for 'trade_updates' events
        3. Dispatch fill/cancel events to TrailingStopManager
        4. Reconnect on failure (exponential backoff)
        """
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required for trade updates stream")
            return

        self._running = True

        while self._running:
            try:
                logger.info("Connecting to trade updates stream: %s", self._url)

                async with websockets.connect(self._url) as ws:
                    # Authenticate
                    auth_msg = {
                        "action": "auth",
                        "key": self._api_key,
                        "secret": self._secret_key,
                    }
                    await ws.send(json.dumps(auth_msg))
                    auth_resp = await ws.recv()
                    logger.info("Trade updates auth: %s", auth_resp)

                    # Sweep fix: Validate auth succeeded before subscribing.
                    try:
                        _auth_parsed = json.loads(auth_resp) if isinstance(auth_resp, str) else auth_resp
                        _auth_list = _auth_parsed if isinstance(_auth_parsed, list) else [_auth_parsed]
                        for _am in _auth_list:
                            if isinstance(_am, dict) and _am.get("msg") == "auth_failed":
                                raise ConnectionError(
                                    f"Trade updates auth failed: {_am}"
                                )
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Could not parse trade updates auth response: %s", auth_resp)

                    # Subscribe to trade_updates
                    listen_msg = {
                        "action": "listen",
                        "data": {"streams": ["trade_updates"]},
                    }
                    await ws.send(json.dumps(listen_msg))

                    self._reconnect_attempts = 0
                    logger.info("Trade updates stream active")

                    # Process events
                    async for raw_msg in ws:
                        try:
                            msg = json.loads(raw_msg)
                            self._dispatch_trade_event(msg)
                        except json.JSONDecodeError as e:
                            logger.warning("Invalid JSON from trade updates: %s", e)

            except Exception as e:
                if not self._running:
                    break

                self._reconnect_attempts += 1
                backoff = min(
                    1.0 * (2 ** self._reconnect_attempts), 30.0
                )
                logger.warning(
                    "Trade updates disconnected: %s. Reconnecting in %.1fs...",
                    e, backoff,
                )
                if self.on_error:
                    self.on_error(e)
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        """Stop the trade updates stream."""
        self._running = False

    def _dispatch_trade_event(self, msg: dict[str, Any]) -> None:
        """
        Dispatch a trade update event to the appropriate handler.

        Integrates with TrailingStopManager (ADR-007):
        - 'fill' event → manager.on_fill() → returns CANCEL_STOP action
        - 'canceled' event → manager.on_stop_canceled() → returns SUBMIT_TRAILING_STOP
        """
        from src.data.trade_updates import parse_trade_update, OrderEvent

        # doc 222 (B1 Phase-1 task#0): tee the RAW payload verbatim BEFORE parsing, so we have
        # ground truth of the wire format (esp. the per-fill execution_id key) + a replay
        # corpus for the event-sourced ledger. Never raises; gated by OPS_RAW_FILL_CAPTURE.
        try:
            from src.ops.fill_capture import capture_raw as _capture_raw
            _capture_raw(msg)
        except Exception:
            pass

        event = parse_trade_update(msg)

        # Generic callback
        if self.on_trade_update:
            self.on_trade_update(event)

        # TrailingStopManager integration (H-008)
        if self.trailing_stop_manager is None:
            return

        if event.event_type == OrderEvent.FILL:
            action = self.trailing_stop_manager.on_fill(
                order_id=event.order_id,
                filled_price=event.filled_avg_price,
                filled_qty=event.filled_qty,
            )
            if action["action"] == "CANCEL_STOP":
                # D310.L3-obs (2026-05-24, doc 171): structured state-
                # transition log so future investigations can grep for
                # the cancel/resubmit chain. The actual cancel happens
                # via self.on_cancel_order callback (wired by executor).
                logger.warning(
                    "D310 CANCEL_REQUESTED %s: stop=%s entry_order=%s — "
                    "Manager wants to cancel original stop and submit "
                    "trailing. ROOT CAUSE INVESTIGATION (doc 171 L3): "
                    "on_cancel_order callback wiring needs verification.",
                    event.symbol, action["stop_order_id"], event.order_id,
                )
                if getattr(self, "on_cancel_order", None) is not None:
                    try:
                        self.on_cancel_order(action["stop_order_id"])
                        logger.info(
                            "D310 CANCEL_DISPATCHED %s: on_cancel_order(%s) called",
                            event.symbol, action["stop_order_id"],
                        )
                    except Exception as _ce:
                        logger.error(
                            "D310 CANCEL_CALLBACK_FAILED %s: %s — "
                            "Layer 2 hedge_integrity_watcher will backstop",
                            event.symbol, _ce,
                        )
                else:
                    # D310.noise (2026-05-24, doc 173): demoted from ERROR
                    # to WARNING. The dead callback fires on EVERY OTO buy
                    # fill; ERROR-level for a known-and-tracked-by-L2
                    # condition habituates operator to ignore the very
                    # signal they need at L3 ship time. Gate ERROR
                    # behind MOMENTUM_D310_DIAG=1 for explicit deep-dives.
                    import os as _os_d310
                    if _os_d310.environ.get("MOMENTUM_D310_DIAG", "") in ("1", "true"):
                        logger.error(
                            "D310 CANCEL_NO_CALLBACK %s: on_cancel_order "
                            "is UNWIRED. L2 hedge_integrity_watcher will "
                            "backstop within 60s. Fix: wire "
                            "self.on_cancel_order in WebSocket setup.",
                            event.symbol,
                        )
                    else:
                        logger.warning(
                            "D310 CANCEL_NO_CALLBACK %s: dead callback "
                            "(known; L2 backstops). Set MOMENTUM_D310_DIAG=1 "
                            "for ERROR-level diagnostics.",
                            event.symbol,
                        )

        elif event.event_type == OrderEvent.CANCELED:
            action = self.trailing_stop_manager.on_stop_canceled(
                stop_order_id=event.order_id,
            )
            if action["action"] == "SUBMIT_TRAILING_STOP":
                logger.warning(
                    "D310 RESUBMIT_REQUESTED %s: trail=%.1f%% qty=%s — "
                    "Manager wants to submit trailing stop after the "
                    "original-stop cancel confirmation. ROOT CAUSE "
                    "INVESTIGATION (doc 171 L3): NXXT 2026-05-18 showed "
                    "this branch fires but the trailing stop never reaches "
                    "the broker. Position remains unhedged.",
                    action["symbol"], action["trail_percent"],
                    action.get("qty", "?"),
                )
                if getattr(self, "on_submit_trailing", None) is not None:
                    try:
                        self.on_submit_trailing(action)
                        logger.info(
                            "D310 RESUBMIT_DISPATCHED %s: on_submit_trailing called "
                            "trail=%.1f%%",
                            action["symbol"], action["trail_percent"],
                        )
                    except Exception as _se:
                        logger.error(
                            "D310 RESUBMIT_CALLBACK_FAILED %s: %s -- "
                            "Layer 2 hedge_integrity_watcher will submit "
                            "emergency stop within 60s",
                            action["symbol"], _se,
                        )
                else:
                    # D310.noise (2026-05-24, doc 173): demoted from ERROR
                    # to WARNING per the habituation argument above. ERROR
                    # gated behind MOMENTUM_D310_DIAG=1.
                    import os as _os_d310
                    if _os_d310.environ.get("MOMENTUM_D310_DIAG", "") in ("1", "true"):
                        logger.error(
                            "D310 RESUBMIT_NO_CALLBACK %s: NXXT 5/18 "
                            "root cause; on_submit_trailing UNWIRED. L2 "
                            "will submit emergency stop within 60s.",
                            action["symbol"],
                        )
                    else:
                        logger.warning(
                            "D310 RESUBMIT_NO_CALLBACK %s: dead callback "
                            "(known; L2 backstops). Set MOMENTUM_D310_DIAG=1 "
                            "for ERROR-level diagnostics.",
                            action["symbol"],
                        )

