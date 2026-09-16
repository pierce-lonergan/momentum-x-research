# mx-arena: Full Alpaca Exchange Simulator for MOMENTUM-X

## 1. Design principles

The simulator exists to answer one question: "If I had run this exact code on
March 25, what would have happened?" — and then to answer it 500 times in
parallel with different parameters.

Three non-negotiable constraints:

**Zero production code changes.** The MOMENTUM-X bot (`main.py`) runs
identically against the simulator and against Alpaca's live paper trading. The
SDK is redirected via BaseURL enum patching before the bot's imports execute.
No mocks, no adapters, no test flags in production code.

**Clock-driven determinism.** Every simulation run with the same inputs produces
the same outputs. Randomness (partial fills, latency jitter) is seeded per-run.
This means you can reproduce any result and debug it bar-by-bar.

**Sub-second per simulated day.** A full trading day (390 minutes) should replay
in under 1 second on a single core. This enables 1000+ parameter configurations
across 50 historical days in under an hour on a 16-core machine.

---

## 2. Package structure

```
mx-arena/
├── arena/
│   ├── __init__.py
│   ├── clock.py              # SimClock — accelerated time control
│   ├── exchange.py           # SimExchange — matching engine + state
│   ├── data_engine.py        # Historical replay + synthetic generation
│   ├── spread_model.py       # Bid-ask spread reconstruction
│   ├── fill_model.py         # Fill logic (NBBO, partial, slippage)
│   ├── scenario.py           # Scenario library (flash crash, halt, etc.)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── rest.py           # FastAPI REST endpoints
│   │   ├── ws_trading.py     # WebSocket trading stream (JSON)
│   │   ├── ws_market.py      # WebSocket market data stream (msgpack)
│   │   └── models.py         # Pydantic response models matching Alpaca schema
│   ├── harness.py            # Single-instance launcher (bot + sim)
│   ├── orchestrator.py       # Multi-instance parallel runner
│   ├── aggregator.py         # Result collection + analysis
│   └── sdk_patch.py          # BaseURL enum patching
├── data/
│   ├── historical/           # Parquet files: {symbol}/{date}.parquet
│   ├── scenarios/            # YAML scenario definitions
│   └── results/              # Per-run output (trades, P&L, signals)
├── scripts/
│   ├── download_history.py   # Bulk download from Alpaca data API
│   ├── run_sweep.py          # Parameter sweep entry point
│   ├── run_replay.py         # Single-day replay entry point
│   └── analyze_results.py    # Post-sweep analysis + visualization
├── tests/
│   ├── test_exchange.py      # Matching engine unit tests
│   ├── test_fill_model.py    # Fill logic property tests
│   ├── test_api_compat.py    # API response schema validation
│   └── test_lifecycle.py     # Full order lifecycle integration tests
├── pyproject.toml
└── README.md
```

---

## 3. Component specifications

### 3.1 SimClock (`arena/clock.py`)

The clock is the heartbeat of the entire simulation. Every component reads time
from the clock — never from `datetime.now()`.

```python
class SimClock:
    """
    Simulated market clock with accelerated replay.
    
    Modes:
    - REPLAY: advance bar-by-bar from historical data (fastest)
    - REALTIME_SCALED: advance at N× real speed (for watching behavior)
    - MANUAL: advance only when .tick() is called (for debugging)
    """
    
    def __init__(self, 
                 start: datetime,       # e.g. 2026-03-25 04:30:00 ET
                 end: datetime,         # e.g. 2026-03-25 16:00:00 ET
                 mode: ClockMode = ClockMode.REPLAY):
        self._current = start
        self._end = end
        self._mode = mode
        self._subscribers: list[Callable] = []  # called on each tick
        self._market_open = time(9, 30)
        self._market_close = time(16, 0)
    
    @property
    def now(self) -> datetime:
        return self._current
    
    @property
    def is_market_open(self) -> bool:
        return self._market_open <= self._current.time() < self._market_close
    
    def tick(self, delta: timedelta = timedelta(minutes=1)):
        """Advance clock and notify all subscribers."""
        self._current += delta
        for callback in self._subscribers:
            callback(self._current)
    
    def subscribe(self, callback: Callable[[datetime], None]):
        self._subscribers.append(callback)
    
    async def run(self):
        """Run clock to completion (REPLAY mode: as fast as possible)."""
        while self._current < self._end:
            self.tick()
            if self._mode == ClockMode.REALTIME_SCALED:
                await asyncio.sleep(60 / self._speed_multiplier)
```

**Critical integration point:** The bot's `time.time()` and `datetime.now()`
calls must also see simulated time. The SDK patch module replaces these at
import time:

```python
# In sdk_patch.py, before importing the bot:
import arena.clock as _clk

_original_time = time.time
_original_now = datetime.now

def _sim_time():
    return _clk.active_clock.now.timestamp()

def _sim_now(tz=None):
    return _clk.active_clock.now

time.time = _sim_time
datetime.now = _sim_now
```

### 3.2 SimExchange (`arena/exchange.py`)

The exchange holds all mutable state: account, orders, positions, and trade
history. It processes orders on each clock tick.

**State model:**

```python
@dataclass
class AccountState:
    cash: Decimal           # Starting: 100_000
    equity: Decimal         # cash + sum(position.market_value)
    buying_power: Decimal   # 4x for day trades (PDT rules)
    long_market_value: Decimal
    daytrade_count: int
    
@dataclass  
class OrderState:
    id: str                 # UUID4
    client_order_id: str
    symbol: str
    side: str               # buy / sell
    type: str               # market / limit / stop / stop_limit / trailing_stop
    time_in_force: str      # day / gtc / ioc / fok / opg / cls
    qty: Decimal
    filled_qty: Decimal
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    trail_percent: Optional[Decimal]
    status: str             # new / partially_filled / filled / canceled / ...
    order_class: str        # simple / bracket / oco / oto
    legs: list[OrderState]  # child orders for bracket/oto
    created_at: datetime
    filled_at: Optional[datetime]
    hwm: Optional[Decimal]  # high-water mark for trailing stops

@dataclass
class PositionState:
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    cost_basis: Decimal
```

**Matching engine — tick cycle:**

```python
async def on_tick(self, timestamp: datetime, bars: dict[str, Bar]):
    """Called by SimClock on each tick. Process all open orders."""
    
    for symbol, bar in bars.items():
        self._update_market_price(symbol, bar)
    
    for order in self._open_orders():
        bar = bars.get(order.symbol)
        if not bar:
            continue
            
        if order.status == 'held':
            # OTO child — check if parent is filled
            if self._parent_filled(order):
                order.status = 'new'
                await self._emit_ws('trade_updates', 'new', order)
            continue
        
        fill = self._try_fill(order, bar, timestamp)
        if fill:
            await self._apply_fill(fill, timestamp)
            await self._emit_ws('trade_updates', fill.event, order)
            
            # Handle bracket/OTO cascades
            if fill.event == 'fill' and order.order_class in ('oto', 'bracket'):
                await self._activate_children(order)
            if fill.event == 'fill' and order.order_class == 'oco':
                await self._cancel_sibling(order)
```

**Fill model (`arena/fill_model.py`):**

```python
class AlpacaFillModel:
    """
    Replicates Alpaca paper trading fill behavior.
    
    - Market buy: fills at ask (close + half_spread)
    - Market sell: fills at bid (close - half_spread)
    - Limit buy: fills when limit_price >= ask
    - Limit sell: fills when limit_price <= bid
    - Stop: triggers when bar trades through stop_price
    - 10% random partial fill probability (seeded)
    """
    
    PARTIAL_FILL_PROBABILITY = 0.10
    
    def try_fill(self, order: OrderState, bar: Bar, 
                 spread: Decimal, rng: Random) -> Optional[Fill]:
        
        bid = bar.close - spread / 2
        ask = bar.close + spread / 2
        
        if order.type == 'market':
            price = ask if order.side == 'buy' else bid
            qty = self._maybe_partial(order.qty - order.filled_qty, rng)
            return Fill(price=price, qty=qty)
        
        elif order.type == 'limit':
            if order.side == 'buy' and order.limit_price >= ask:
                qty = self._maybe_partial(order.qty - order.filled_qty, rng)
                return Fill(price=min(order.limit_price, ask), qty=qty)
            elif order.side == 'sell' and order.limit_price <= bid:
                qty = self._maybe_partial(order.qty - order.filled_qty, rng)
                return Fill(price=max(order.limit_price, bid), qty=qty)
        
        elif order.type == 'stop':
            triggered = (
                (order.side == 'sell' and bar.low <= order.stop_price) or
                (order.side == 'buy' and bar.high >= order.stop_price)
            )
            if triggered:
                price = ask if order.side == 'buy' else bid
                return Fill(price=price, qty=order.qty - order.filled_qty)
        
        elif order.type == 'trailing_stop':
            # Update high-water mark
            if order.side == 'sell':
                order.hwm = max(order.hwm or Decimal(0), bar.high)
                trigger = order.hwm * (1 - order.trail_percent / 100)
                if bar.low <= trigger:
                    return Fill(price=bid, qty=order.qty - order.filled_qty)
        
        return None
    
    def _maybe_partial(self, remaining: Decimal, rng: Random) -> Decimal:
        if rng.random() < self.PARTIAL_FILL_PROBABILITY and remaining > 1:
            return Decimal(rng.randint(1, int(remaining) - 1))
        return remaining
```

**Enhanced fill models (configurable realism beyond Alpaca):**

```python
class RealisticFillModel(AlpacaFillModel):
    """
    Adds realism that Alpaca paper trading explicitly omits:
    - Volume-limited fills (can't fill more than bar_volume * participation)
    - Market impact (large orders move price)
    - Queue position (non-marketable limits wait)
    """
    
    MAX_PARTICIPATION = 0.05  # Can't be >5% of bar volume
    IMPACT_COEFFICIENT = 0.1  # Price impact per unit of participation
    
    def try_fill(self, order, bar, spread, rng):
        fill = super().try_fill(order, bar, spread, rng)
        if fill is None:
            return None
        
        # Volume limit
        max_qty = int(bar.volume * self.MAX_PARTICIPATION)
        if fill.qty > max_qty:
            fill.qty = Decimal(max(1, max_qty))
        
        # Market impact
        participation = float(fill.qty) / max(float(bar.volume), 1)
        impact = participation * self.IMPACT_COEFFICIENT
        if order.side == 'buy':
            fill.price *= (1 + Decimal(str(impact)))
        else:
            fill.price *= (1 - Decimal(str(impact)))
        
        return fill
```

### 3.3 API Gateway (`arena/api/`)

FastAPI server implementing the Alpaca REST + WebSocket contracts.

**REST endpoints (arena/api/rest.py):**

```python
app = FastAPI()

@app.get("/v2/account")
async def get_account():
    state = exchange.account
    return {
        "id": ACCOUNT_ID,
        "status": "ACTIVE",
        "cash": str(state.cash),
        "buying_power": str(state.buying_power),
        "equity": str(state.equity),
        "long_market_value": str(state.long_market_value),
        "pattern_day_trader": state.daytrade_count >= 4,
        "daytrade_count": state.daytrade_count,
        "currency": "USD",
        # ... ~20 more fields matching Alpaca schema
    }

@app.post("/v2/orders")
async def create_order(request: OrderRequest):
    # Validate: buying power, position limits, market hours, symbol exists
    order = exchange.submit_order(
        symbol=request.symbol,
        qty=request.qty,
        side=request.side,
        type=request.type,
        time_in_force=request.time_in_force,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        order_class=request.order_class,
        take_profit=request.take_profit,
        stop_loss=request.stop_loss,
    )
    return order.to_alpaca_dict()

@app.get("/v2/orders")
async def list_orders(status: str = "open", symbols: str = None):
    orders = exchange.get_orders(status=status, symbols=symbols)
    return [o.to_alpaca_dict() for o in orders]

@app.delete("/v2/orders/{order_id}")
async def cancel_order(order_id: str):
    exchange.cancel_order(order_id)
    return Response(status_code=204)

@app.get("/v2/positions")
async def list_positions():
    return [p.to_alpaca_dict() for p in exchange.positions.values()]

@app.get("/v2/positions/{symbol}")
async def get_position(symbol: str):
    pos = exchange.positions.get(symbol)
    if not pos:
        raise HTTPException(404, f"position does not exist")
    return pos.to_alpaca_dict()

@app.delete("/v2/positions/{symbol}")
async def close_position(symbol: str, qty: float = None):
    exchange.close_position(symbol, qty)
    return Response(status_code=200)

@app.get("/v2/clock")
async def get_clock():
    return {
        "timestamp": clock.now.isoformat(),
        "is_open": clock.is_market_open,
        "next_open": clock.next_open.isoformat(),
        "next_close": clock.next_close.isoformat(),
    }

# Market data endpoints
@app.get("/v2/stocks/{symbol}/bars")
async def get_bars(symbol: str, timeframe: str, start: str, end: str):
    bars = data_engine.get_bars(symbol, timeframe, start, end)
    return {"bars": [b.to_alpaca_dict() for b in bars]}

@app.get("/v2/stocks/{symbol}/snapshot")
async def get_snapshot(symbol: str):
    return data_engine.get_snapshot(symbol, clock.now).to_alpaca_dict()

@app.get("/v2/stocks/snapshots")
async def get_multi_snapshot(symbols: str):
    syms = symbols.split(",")
    return {s: data_engine.get_snapshot(s, clock.now).to_alpaca_dict() 
            for s in syms}
```

**WebSocket trading stream (arena/api/ws_trading.py):**

```python
@app.websocket("/stream")
async def trading_stream(ws: WebSocket):
    await ws.accept()
    
    # Authentication handshake
    auth = await ws.receive_json()
    if auth.get("action") != "authenticate":
        await ws.close(4001)
        return
    await ws.send_json({"data": {"status": "authorized"}})
    
    # Subscription
    sub = await ws.receive_json()
    streams = sub.get("data", {}).get("streams", [])
    await ws.send_json({"data": {"streams": streams}})
    
    # Event loop — push trade updates as they happen
    queue = asyncio.Queue()
    exchange.register_listener(queue)
    
    try:
        while True:
            event = await queue.get()
            await ws.send_json({
                "stream": "trade_updates",
                "data": {
                    "event": event.type,      # fill, partial_fill, canceled, new
                    "order": event.order.to_alpaca_dict(),
                    "price": str(event.price) if event.price else None,
                    "qty": str(event.qty) if event.qty else None,
                    "position_qty": str(event.position_qty),
                    "timestamp": event.timestamp.isoformat(),
                }
            })
    finally:
        exchange.unregister_listener(queue)
```

**WebSocket market data stream (arena/api/ws_market.py):**

```python
import msgpack

@app.websocket("/v2/{feed}")
async def market_data_stream(ws: WebSocket, feed: str):
    await ws.accept()
    
    # Auth (10-second window)
    auth = await asyncio.wait_for(ws.receive_json(), timeout=10)
    await ws.send_bytes(msgpack.packb([{"T": "success", "msg": "authenticated"}]))
    
    subscribed_bars = set()
    subscribed_quotes = set()
    subscribed_trades = set()
    
    # Handle subscription messages in background
    async def read_loop():
        async for msg in ws.iter_json():
            if msg.get("action") == "subscribe":
                subscribed_bars.update(msg.get("bars", []))
                subscribed_quotes.update(msg.get("quotes", []))
                subscribed_trades.update(msg.get("trades", []))
    
    asyncio.create_task(read_loop())
    
    # Push data as clock ticks
    queue = asyncio.Queue()
    data_engine.register_market_listener(queue)
    
    try:
        while True:
            event = await queue.get()
            messages = []
            
            if event.type == "bar" and event.symbol in subscribed_bars:
                messages.append({
                    "T": "b", "S": event.symbol,
                    "o": event.open, "h": event.high,
                    "l": event.low, "c": event.close,
                    "v": event.volume, "t": event.timestamp.isoformat(),
                    "n": event.trade_count, "vw": event.vwap,
                })
            
            if event.type == "quote" and event.symbol in subscribed_quotes:
                messages.append({
                    "T": "q", "S": event.symbol,
                    "bp": event.bid_price, "bs": event.bid_size,
                    "ap": event.ask_price, "as": event.ask_size,
                    "t": event.timestamp.isoformat(),
                })
            
            if messages:
                await ws.send_bytes(msgpack.packb(messages))
    finally:
        data_engine.unregister_market_listener(queue)
```

### 3.4 Data Engine (`arena/data_engine.py`)

Loads historical data and generates synthetic market scenarios.

**Historical data format (Parquet):**

```
data/historical/
├── MKDW/
│   ├── 2026-03-25.parquet    # columns: timestamp, o, h, l, c, v, vw, n
│   ├── 2026-03-24.parquet
│   └── ...
├── FEED/
│   └── ...
└── _daily/
    ├── MKDW.parquet          # daily bars for ATR computation
    └── ...
```

Each Parquet file contains 1-minute bars for one symbol-day. The `_daily/`
subdirectory holds daily bars for the ATR/VWAP calculations that the bot's
scanner and stop logic need.

**Download script (scripts/download_history.py):**

```python
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

client = StockHistoricalDataClient(api_key, secret_key)

def download_day(symbol: str, date: str):
    """Download 1-min bars for one symbol-day, save as Parquet."""
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=f"{date}T04:00:00Z",      # include pre-market
        end=f"{date}T20:00:00Z",         # include after-hours
        adjustment="split",
        feed="sip",                       # consolidated tape
    )
    bars = client.get_stock_bars(request)
    df = bars.df.reset_index()
    df.to_parquet(f"data/historical/{symbol}/{date}.parquet")
```

**Spread model (`arena/spread_model.py`):**

```python
class HistoricalSpreadModel:
    """
    Reconstructs bid-ask spreads from bar data.
    
    Spread = base_spread × time_of_day_factor × volatility_factor
    
    Base spread by price tier:
    - $0.50-$3:   15-50 bps (wide, illiquid)
    - $3-$10:     5-20 bps
    - $10-$50:    2-10 bps  
    - $50+:       1-5 bps
    
    Time-of-day factor (U-shaped):
    - 09:30-09:45: 2.5x (opening auction chop)
    - 09:45-10:30: 1.5x (settling)
    - 10:30-15:00: 1.0x (midday)
    - 15:00-15:45: 1.3x (approaching close)
    - 15:45-16:00: 2.0x (closing auction)
    """
    
    def get_spread(self, price: float, volume: float, 
                   time: datetime, rvol: float = 1.0) -> float:
        # Base spread from price tier
        if price < 3:
            base_bps = 30
        elif price < 10:
            base_bps = 12
        elif price < 50:
            base_bps = 5
        else:
            base_bps = 2
        
        # Adjust for volume (higher volume = tighter spreads)
        vol_factor = max(0.3, 1.0 - min(rvol / 20, 0.7))
        
        # Time of day U-shape
        hour = time.hour + time.minute / 60
        if hour < 9.75:
            tod = 2.5
        elif hour < 10.5:
            tod = 1.5
        elif hour < 15:
            tod = 1.0
        elif hour < 15.75:
            tod = 1.3
        else:
            tod = 2.0
        
        spread_bps = base_bps * vol_factor * tod
        return price * spread_bps / 10000
```

**Synthetic generator:**

```python
class SyntheticGenerator:
    """
    Generates minute-bar data for scenario testing.
    
    Models (composable):
    - GBM: Geometric Brownian Motion (baseline)
    - Heston: Stochastic volatility (regime-dependent vol)
    - JumpDiffusion: Merton model (news events, earnings)
    - GapAndGo: Opens with gap, momentum continuation
    - GapAndFade: Opens with gap, immediate reversal
    """
    
    def gap_and_go(self, entry_price: float, gap_pct: float,
                   rvol: float, peak_minute: int = 20,
                   peak_return: float = 0.15, fade_rate: float = 0.3,
                   seed: int = 42) -> list[Bar]:
        """
        Generates a gap-up-and-go pattern.
        
        Phase 1 (0-5 min): Initial pullback from gap (2-5%)
        Phase 2 (5-peak_minute): Strong momentum advance
        Phase 3 (peak_minute-60): Gradual fade
        Phase 4 (60-390): Choppy consolidation
        """
        rng = Random(seed)
        bars = []
        price = entry_price
        
        for minute in range(390):
            if minute < 5:
                # Opening pullback
                drift = rng.gauss(-0.003, 0.005)
            elif minute < peak_minute:
                # Momentum advance
                progress = (minute - 5) / (peak_minute - 5)
                drift = rng.gauss(peak_return / peak_minute, 0.004)
            elif minute < 60:
                # Gradual fade
                drift = rng.gauss(-fade_rate / 60, 0.003)
            else:
                # Consolidation
                drift = rng.gauss(0, 0.002)
            
            price *= (1 + drift)
            noise = abs(rng.gauss(0, 0.002))
            
            # U-shaped volume
            hour = 9.5 + minute / 60
            vol_base = rvol * 10000
            if minute < 15:
                vol = vol_base * (3.0 - minute * 0.13)
            elif minute > 375:
                vol = vol_base * (1.5 + (minute - 375) * 0.1)
            else:
                vol = vol_base * max(0.3, 1.0 - minute / 500)
            
            bars.append(Bar(
                timestamp=market_open + timedelta(minutes=minute),
                open=price,
                high=price * (1 + noise),
                low=price * (1 - noise),
                close=price,
                volume=max(100, int(vol * rng.uniform(0.7, 1.3))),
                vwap=price * rng.uniform(0.998, 1.002),
            ))
        
        return bars
```

### 3.5 SDK Interceptor (`arena/sdk_patch.py`)

```python
"""
Patches alpaca-py SDK to connect to the local simulator.

Usage:
    import arena.sdk_patch
    arena.sdk_patch.activate(
        rest_url="http://localhost:8080",
        ws_trading_url="ws://localhost:8081",
        ws_market_url="ws://localhost:8082",
    )
    
    # Now import and run the bot — it connects to the simulator
    import main
    main.run()
"""

import importlib

def activate(rest_url: str, ws_trading_url: str, ws_market_url: str):
    """Patch BaseURL enum values before any alpaca-py client is created."""
    
    from alpaca.common.enums import BaseURL
    
    # Trading API
    BaseURL.TRADING_PAPER._value_ = rest_url
    BaseURL.TRADING_LIVE._value_ = rest_url  # Safety: live also goes to sim
    
    # Data API  
    BaseURL.DATA._value_ = rest_url
    
    # WebSocket streams
    BaseURL.MARKET_DATA_STREAM._value_ = ws_market_url
    BaseURL.TRADING_STREAM_PAPER._value_ = ws_trading_url
    BaseURL.TRADING_STREAM_LIVE._value_ = ws_trading_url


def activate_for_in_process(exchange, data_engine, clock):
    """
    Alternative: skip the HTTP layer entirely. Patch SDK client methods
    to call exchange/data_engine directly. ~10x faster than HTTP.
    
    Trades network overhead for coupling — use for sweep mode,
    not for fidelity testing.
    """
    from alpaca.trading.client import TradingClient
    
    _orig_submit = TradingClient.submit_order
    
    def _patched_submit(self, order_data):
        return exchange.submit_order_from_request(order_data)
    
    TradingClient.submit_order = _patched_submit
    # ... patch get_all_positions, get_all_orders, etc.
```

### 3.6 Harness — Single Instance (`arena/harness.py`)

```python
class ArenaInstance:
    """
    Encapsulates one complete simulation: clock + exchange + data + API + bot.
    
    This is the unit of parallelization — the orchestrator spawns many of these.
    """
    
    def __init__(self, config: ArenaConfig):
        self.config = config
        self.clock = SimClock(
            start=config.session_start,
            end=config.session_end,
            mode=config.clock_mode,
        )
        self.data_engine = DataEngine(
            historical_dir=config.data_dir,
            symbols=config.symbols,
            clock=self.clock,
            spread_model=HistoricalSpreadModel(),
        )
        self.exchange = SimExchange(
            clock=self.clock,
            fill_model=config.fill_model or AlpacaFillModel(),
            initial_cash=config.initial_cash,
            seed=config.seed,
        )
        
        # Wire clock -> data -> exchange
        self.clock.subscribe(self.data_engine.on_tick)
        self.clock.subscribe(self.exchange.on_tick)
    
    async def run_http_mode(self) -> RunResult:
        """Full HTTP simulation — highest fidelity, ~5s per day."""
        # Start API servers
        rest_port = self.config.rest_port
        ws_trade_port = self.config.ws_trade_port
        ws_market_port = self.config.ws_market_port
        
        servers = await start_servers(
            self.exchange, self.data_engine, self.clock,
            rest_port, ws_trade_port, ws_market_port,
        )
        
        # Patch SDK and launch bot
        sdk_patch.activate(
            f"http://localhost:{rest_port}",
            f"ws://localhost:{ws_trade_port}",
            f"ws://localhost:{ws_market_port}",
        )
        
        # Apply parameter overrides to bot config
        apply_config_overrides(self.config.param_overrides)
        
        # Run bot + clock concurrently
        bot_task = asyncio.create_task(run_bot())
        clock_task = asyncio.create_task(self.clock.run())
        
        await clock_task  # Clock finishes first (end of day)
        bot_task.cancel()
        
        return self._collect_results()
    
    async def run_direct_mode(self) -> RunResult:
        """In-process simulation — no HTTP, ~0.5s per day."""
        sdk_patch.activate_for_in_process(
            self.exchange, self.data_engine, self.clock
        )
        apply_config_overrides(self.config.param_overrides)
        
        # Drive everything from the clock
        await self.clock.run()
        
        return self._collect_results()
    
    def _collect_results(self) -> RunResult:
        return RunResult(
            config_id=self.config.id,
            date=self.config.date,
            params=self.config.param_overrides,
            trades=self.exchange.trade_history,
            pnl=self.exchange.realized_pnl,
            positions_eod=list(self.exchange.positions.values()),
            orders_total=len(self.exchange.all_orders),
            signal_history=self.exchange.signal_log,
        )
```

### 3.7 Orchestrator — Parallel Runs (`arena/orchestrator.py`)

```python
import ray

@ray.remote
def run_simulation(config: ArenaConfig, shared_data_ref) -> RunResult:
    """Ray remote function for one simulation instance."""
    config.data_dir = shared_data_ref  # Zero-copy via Ray object store
    instance = ArenaInstance(config)
    return asyncio.run(instance.run_direct_mode())


class ArenaOrchestrator:
    """
    Manages hundreds of parallel simulation runs.
    
    Modes:
    - PARAMETER_SWEEP: Same day, different parameters
    - ROBUSTNESS: Same parameters, different days
    - FULL_MATRIX: Different days × different parameters
    - OPTUNA: Bayesian optimization with pruning
    """
    
    def __init__(self, base_config: ArenaConfig):
        self.base = base_config
        ray.init(ignore_reinit_error=True)
    
    def sweep(self, param_grid: dict, dates: list[str]) -> pd.DataFrame:
        """
        Run all parameter × date combinations in parallel.
        
        Example param_grid:
        {
            "gap_momentum_score_threshold": [0.5, 1.0, 1.5, 2.0],
            "parallel_exit_min_confidence": [0.5, 0.6, 0.7, 0.8],
            "initial_stop_atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        }
        
        With 4 params × 4 values × 50 days = 3,200 simulations.
        At 0.5s each on 16 cores = ~100 seconds total.
        """
        # Load shared data once
        shared_data = load_all_historical(dates)
        data_ref = ray.put(shared_data)
        
        # Generate configs
        configs = []
        for date in dates:
            for combo in itertools.product(*param_grid.values()):
                overrides = dict(zip(param_grid.keys(), combo))
                config = self.base.copy()
                config.date = date
                config.param_overrides = overrides
                config.id = f"{date}_{hash(frozenset(overrides.items()))}"
                configs.append(config)
        
        # Launch all
        futures = [run_simulation.remote(c, data_ref) for c in configs]
        results = ray.get(futures)
        
        return self._to_dataframe(results)
    
    def optuna_optimize(self, date_pool: list[str], n_trials: int = 200,
                        n_workers: int = 8) -> dict:
        """
        Bayesian optimization using Optuna with walk-forward validation.
        
        Splits date_pool into 60% train / 40% test.
        Optimizes on train, validates on test.
        Returns best params + OOS performance.
        """
        import optuna
        
        train_dates = date_pool[:int(len(date_pool) * 0.6)]
        test_dates = date_pool[int(len(date_pool) * 0.6):]
        
        def objective(trial):
            params = {
                "gap_momentum_score_threshold": trial.suggest_float(
                    "gap_momentum_score_threshold", 0.3, 3.0),
                "parallel_exit_min_confidence": trial.suggest_float(
                    "parallel_exit_min_confidence", 0.3, 0.95),
                "initial_stop_atr_multiplier": trial.suggest_float(
                    "initial_stop_atr_multiplier", 1.0, 4.0),
                "confidence_deflation_factor": trial.suggest_float(
                    "confidence_deflation_factor", 0.4, 0.9),
                "mfcs_buy_threshold": trial.suggest_float(
                    "mfcs_buy_threshold", 0.10, 0.35),
            }
            
            # Run on train dates
            results = []
            for date in train_dates:
                config = self.base.copy()
                config.date = date
                config.param_overrides = params
                instance = ArenaInstance(config)
                result = asyncio.run(instance.run_direct_mode())
                results.append(result)
                
                # Optuna pruning: if clearly unprofitable, stop early
                running_pf = compute_profit_factor(results)
                trial.report(running_pf, len(results))
                if trial.should_prune():
                    raise optuna.TrialPruned()
            
            return compute_profit_factor(results)
        
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(n_warmup_steps=5),
        )
        study.optimize(objective, n_trials=n_trials, n_jobs=n_workers)
        
        # Validate best params on test set
        best = study.best_params
        test_results = self.sweep(
            {k: [v] for k, v in best.items()}, test_dates
        )
        
        return {
            "best_params": best,
            "train_pf": study.best_value,
            "test_pf": compute_profit_factor_from_df(test_results),
            "train_test_gap": study.best_value - compute_profit_factor_from_df(test_results),
            "n_trials": len(study.trials),
            "n_pruned": len([t for t in study.trials if t.state == optuna.trial.TrialState.PRUNED]),
        }
```

---

## 4. What the bot sees vs what's simulated

| Bot calls | Simulator provides | Fidelity notes |
|-----------|-------------------|----------------|
| `GET /v2/account` | Full AccountState with 25+ fields | All computed from exchange state |
| `POST /v2/orders` (OTO) | Parent + held child orders | OTO/bracket/OCO lifecycle fully modeled |
| `DELETE /v2/orders/{id}` | Cancel with WS event | Immediate; no race conditions (simplification) |
| `GET /v2/positions` | Real-time position state | Updated atomically on fill |
| `GET /v2/stocks/{sym}/snapshot` | Latest + previous daily bar + VWAP | Critical for scanner's gap calculation |
| `GET /v2/stocks/{sym}/bars` | Historical bars from Parquet | Identical data format to Alpaca |
| `GET /v2/clock` | Simulated time + market hours | Drives bot's phase transitions |
| WS `trade_updates` | Fill/cancel/new events | JSON format, same field names |
| WS market data (bars) | Minute bars as msgpack | Matches Alpaca's streaming format |
| WS market data (quotes) | Synthetic quotes from spread model | Reconstructed, not real quotes |
| `time.time()` / `datetime.now()` | Simulated time | Monkey-patched before bot import |

**What's NOT simulated (known gaps):**

| Gap | Impact | Mitigation |
|-----|--------|------------|
| News API (`GET /v1beta1/news`) | News agent gets no data | Pre-seed news from historical cache or mock responses |
| SEC EDGAR (`sec_client.py`) | Manipulation classifier gets no filings | Pre-seed SEC data from historical cache |
| LLM API calls (Qwen, DeepSeek) | News/fundamental agents fail | Use deterministic-only mode, or mock LLM responses from trade journal |
| WebSocket reconnection timing | Auto-reconnect is instant in sim | Add configurable reconnection delay |
| Alpaca rate limits (200/min) | Bot never hits limits | Add optional rate limiter for realism |

---

## 5. Data acquisition plan

**Phase 1 — Bootstrap (run once, ~2 hours):**

Download 90 days of 1-minute bars for all tickers that appeared on the
MOMENTUM-X watchlist. From paper trading logs + backtest cache, this is
approximately 100 symbols × 90 days = 9,000 symbol-days.

```bash
python scripts/download_history.py \
    --symbols data/watchlist_history.txt \
    --start 2025-12-25 \
    --end 2026-03-26 \
    --timeframe 1Min \
    --output data/historical/
```

Storage: ~50MB per 100 symbols × 90 days in Parquet format.

**Phase 2 — Daily snapshot enrichment:**

For each historical day, also download snapshots at key times (09:25, 09:30,
09:35) to provide the `prevDailyBar`, `dailyBar`, and `latestTrade` data the
scanner needs. Store as JSON alongside the Parquet bars.

**Phase 3 — Pre-market data:**

Download 04:00-09:30 ET bars separately (extended hours) for Phase 1
pre-market scanning. Alpaca's API supports `feed="sip"` with extended hours.

---

## 6. Scenario library (`arena/scenario.py`)

Predefined scenarios for stress testing, loaded from YAML:

```yaml
# scenarios/flash_crash.yaml
name: "Flash crash at T+45"
base: gap_and_go         # start with a gap-up winner
inject_at_minute: 45
injection:
  type: flash_crash
  drop_pct: 0.12         # 12% drop in 1 bar
  recovery_bars: 5        # partial recovery over 5 bars
  recovery_pct: 0.07      # recovers 7% of the 12%

# scenarios/halt_resume.yaml  
name: "LULD halt at T+15, resume T+20"
base: gap_and_go
inject_at_minute: 15
injection:
  type: trading_halt
  duration_bars: 5
  resume_gap_pct: -0.05   # resumes 5% lower

# scenarios/short_squeeze.yaml
name: "Short squeeze acceleration"
base: gap_and_go
inject_at_minute: 30
injection:
  type: squeeze
  acceleration: 0.005     # 0.5% per bar acceleration
  duration_bars: 20
  volume_multiplier: 5
```

---

## 7. Validation strategy

**Level 1 — Schema conformance:**
Every REST response and WebSocket message is validated against Pydantic models
derived from Alpaca's API documentation. Run `test_api_compat.py` which
compares simulator output schemas to captured Alpaca responses.

**Level 2 — Order lifecycle correctness:**
Property-based tests (Hypothesis) verify that order state transitions are valid
(no new→filled without intermediate states), position quantities never go
negative, account equity always equals cash + positions, and brackets always
cascade correctly.

**Level 3 — Shadow trading:**
Run the bot against both real Alpaca paper trading and the simulator
simultaneously. Compare fill prices, order counts, position quantities, and P&L
at end of day. Flag any divergence > 0.1%.

**Level 4 — Historical replay validation:**
Replay a day where you have real Alpaca trade journal data (Days 17, 20).
Compare simulator trades to actual Alpaca trades. Fills should match within
spread model tolerance.

---

## 8. Performance budget

| Component | Per-tick cost | 390-tick day |
|-----------|-------------|-------------|
| Clock tick + subscriber notify | ~1μs | 0.4ms |
| Data engine bar lookup | ~5μs | 2ms |
| Matching engine (8 orders) | ~50μs | 20ms |
| Spread model computation | ~2μs | 0.8ms |
| State update (position/account) | ~10μs | 4ms |
| **Direct mode total** | **~70μs** | **~30ms** |
| HTTP overhead (if API mode) | ~500μs | 200ms |
| WebSocket serialization | ~100μs | 40ms |
| **HTTP mode total** | **~700μs** | **~300ms** |

**Direct mode: 30ms per simulated day = 33 days/second/core.**
**HTTP mode: 300ms per simulated day = 3 days/second/core.**

On a 16-core machine:
- Direct mode: 500 day-simulations in ~1 second
- HTTP mode: 500 day-simulations in ~10 seconds
- Full matrix (50 days × 100 param combos): 5,000 sims in ~10-100 seconds

---

## 9. Implementation roadmap

**Week 1: Core exchange + REST**
- SimClock with REPLAY mode
- SimExchange with market/limit/stop orders
- AlpacaFillModel (NBBO + 10% partial fills)
- REST endpoints: account, orders, positions, clock
- Historical data loader (Parquet)
- SDK patch (BaseURL enum)
- Tests: order lifecycle, fill model properties

**Week 2: WebSocket + OTO brackets**
- WS trading stream (JSON)
- WS market data stream (msgpack)
- OTO/bracket/OCO order handling
- Spread model
- Snapshot endpoint (critical for scanner)
- ArenaInstance harness (HTTP mode)

**Week 3: Parallelization + data**
- Download 90 days of history
- ArenaOrchestrator with Ray
- Direct mode (in-process, no HTTP)
- Parameter sweep runner
- Result aggregator + DataFrame output
- Shared data via mmap

**Week 4: Scenarios + optimization**
- Synthetic generators (GBM, gap-and-go, gap-and-fade)
- Scenario library (flash crash, halt, squeeze)
- Optuna integration with walk-forward
- Monte Carlo bootstrap from sweep results
- Shadow trading validation
- Analysis dashboard

---

## 10. Quick start (after implementation)

```bash
# Download historical data
python scripts/download_history.py --days 90

# Replay March 25 with current D126 config
python scripts/run_replay.py --date 2026-03-25

# Parameter sweep: 4 params × 4 values × 50 days = 3,200 sims
python scripts/run_sweep.py \
    --dates 2026-01-02:2026-03-25 \
    --sweep gap_momentum_score_threshold=0.5,1.0,1.5,2.0 \
    --sweep parallel_exit_min_confidence=0.5,0.6,0.7,0.8 \
    --sweep initial_stop_atr_multiplier=1.5,2.0,2.5,3.0 \
    --sweep confidence_deflation_factor=0.5,0.6,0.7,0.8 \
    --workers 16

# Bayesian optimization
python scripts/run_sweep.py \
    --mode optuna \
    --dates 2026-01-02:2026-03-25 \
    --n-trials 200 \
    --workers 8

# Stress test with synthetic scenarios
python scripts/run_replay.py \
    --scenario scenarios/flash_crash.yaml \
    --scenario scenarios/halt_resume.yaml \
    --sweep parallel_exit_min_confidence=0.3,0.5,0.7,0.9

# Analyze results
python scripts/analyze_results.py --input data/results/latest/
```
