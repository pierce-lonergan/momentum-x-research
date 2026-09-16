# Building a complete Alpaca paper trading simulator

**A local exchange simulator can fully replace Alpaca's servers for automated trading system testing, letting an unmodified alpaca-py bot run against simulated markets at hundreds-of-times real speed.** No existing open-source project fills this gap—every Alpaca integration today proxies to their cloud servers. The architecture requires a FastAPI server implementing ~15 REST endpoints and two WebSocket protocols, a matching engine that replicates Alpaca's NBBO-based fill logic, and a historical data replay engine. The alpaca-py SDK's built-in `url_override` parameter makes redirection trivial, and the entire system parallelizes naturally across hundreds of instances using Ray or Kubernetes for parameter sweeps and robustness testing.

---

## The full API surface your simulator must implement

Alpaca's trading API lives at two base URLs: `paper-api.alpaca.markets` for trading operations and `data.alpaca.markets` for market data. Your simulator must serve both, plus two distinct WebSocket protocols. The critical REST endpoints break into four groups.

**Account and state endpoints** form the foundation. `GET /v2/account` returns a `TradeAccount` object with **~25 fields** including `cash`, `buying_power`, `equity`, `long_market_value`, `short_market_value`, `pattern_day_trader`, `daytrade_count`, and `multiplier`. Every decimal value is a string (not float) to preserve precision. `GET /v2/clock` returns `is_open`, `next_open`, `next_close`—your simulator must track simulated market hours accurately. `GET /v2/calendar` returns the trading calendar with early-close dates.

**Order endpoints** are the most complex. `POST /v2/orders` accepts market, limit, stop, stop_limit, and trailing_stop types, with six time-in-force options (day, gtc, opg, cls, ioc, fok) and four order classes (simple, bracket, oco, oto). The response returns a full Order object with **UUID v4 identifiers**, ISO 8601 timestamps, and lifecycle states. `PATCH /v2/orders/{id}` implements order replacement by canceling the original and creating a new order, linking them via `replaced_by`/`replaces` fields. `DELETE /v2/orders/{id}` cancels individual orders. `GET /v2/orders` supports filtering by `status` (open/closed/all), `symbols`, `side`, and pagination with `limit` up to 500.

**Position endpoints** are straightforward: `GET /v2/positions` returns all open positions with fields like `avg_entry_price`, `qty`, `market_value`, `unrealized_pl`, `current_price`, and `change_today`. `DELETE /v2/positions/{symbol}` closes a position, optionally partially via `qty` or `percentage` parameters.

**Market data endpoints** require the richest implementation: `GET /v2/stocks/{symbol}/bars` with timeframe, date range, adjustment, and feed parameters; `GET /v2/stocks/{symbol}/snapshot` returning latest trade, quote, minute bar, daily bar, and previous daily bar; and multi-symbol variants. Bar responses include OHLCV plus `n` (trade count) and `vw` (VWAP). Authentication uses `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` custom headers on every request, with a **200 requests/minute** rate limit.

## How Alpaca actually fills paper trades

Understanding Alpaca's fill logic is essential for faithful simulation. Their paper trading runs the **identical order pipeline as live trading** except orders never reach real exchanges. Instead, fills execute against the **real-time National Best Bid and Offer (NBBO)**.

**Market orders** fill at the current ask price (for buys) or bid price (for sells). **Limit buy orders** fill only when `limit_price >= best_ask`; limit sells fill when `limit_price <= best_bid`. **Stop orders** trigger when a consolidated tape print crosses the stop price, then convert to market orders. **Trailing stops** track a high-water mark and trigger when price retraces by the trail amount—critically, these only trigger during regular trading hours. **Bracket orders** (OTOCO pattern) create an entry order plus take-profit and stop-loss legs; the exit legs activate only after the entry completely fills, and filling either exit cancels the other.

The most important detail for simulator fidelity: **10% of the time, eligible orders receive partial fills for a random quantity**. If the order price remains marketable after a partial fill, the remainder re-evaluates for subsequent filling. This is purely synthetic—it has no relationship to actual market depth.

Alpaca paper trading explicitly does **not** simulate five critical real-world factors: market impact (your orders don't move prices), slippage from latency, order queue position for non-marketable limit orders, liquidity constraints (a 1-million-share order fills at top-of-book regardless of actual depth), and price improvement. It also omits dividends, regulatory fees, borrow fees, and margin interest. These are the dimensions where your simulator can actually improve on Alpaca's paper trading by adding configurable realism.

## Two WebSocket protocols with different encodings

Your simulator must implement two distinct WebSocket servers. The **trading stream** at `/stream` uses JSON text frames and handles order lifecycle events. Authentication follows a specific sequence: client sends `{"action":"authenticate","data":{"key_id":"...","secret_key":"..."}}`, server responds with `{"data":{"status":"authorized"}}`, client subscribes via `{"action":"listen","data":{"streams":["trade_updates"]}}`, and the server pushes events like `{"stream":"trade_updates","data":{"event":"fill","order":{...},"price":"150.25","qty":"100","position_qty":"100"}}`. The event types map directly to order lifecycle states: `new`, `partial_fill`, `fill`, `canceled`, `expired`, `replaced`, `done_for_day`.

The **market data stream** at `/v2/{feed}` (where feed is `iex`, `sip`, or `test`) uses **msgpack binary frames**—a critical difference from the trading stream. Messages arrive in arrays and use single-character type codes: `"T":"t"` for trades, `"T":"q"` for quotes, `"T":"b"` for bars. The subscription mechanism uses `{"action":"subscribe","trades":["AAPL"],"quotes":["AMD"],"bars":["*"]}` with wildcard support. There's a **10-second authentication window** after connection. Your simulator must handle reconnection gracefully since the alpaca-py SDK auto-reconnects and re-subscribes on disconnection.

## Redirecting the SDK requires zero production code changes

The alpaca-py SDK provides `url_override` on every client constructor—explicitly designed for proxy/testing scenarios. Every REST client (`TradingClient`, `StockHistoricalDataClient`) and every WebSocket client (`StockDataStream`, `TradingStream`) accepts this parameter. For truly zero-code-change redirection, **monkey-patching the `BaseURL` enum** before importing production code is the most reliable approach:

```python
from alpaca.common.enums import BaseURL

# Patch before production code imports
BaseURL.TRADING_PAPER._value_ = 'http://localhost:8080'
BaseURL.DATA._value_ = 'http://localhost:8080'
BaseURL.MARKET_DATA_STREAM._value_ = 'ws://localhost:8081'
BaseURL.TRADING_STREAM_PAPER._value_ = 'ws://localhost:8082'

import my_production_bot  # Now connects to simulator
my_production_bot.main()
```

An alternative is patching `RESTClient.__init__` and `DataStream.__init__` to inject `url_override` into every instantiation. The mitmproxy approach works for REST traffic but complicates WebSocket interception. DNS-level redirection via `/etc/hosts` requires serving HTTPS with self-signed certificates—fragile and not recommended. The enum-patching approach is cleanest because it intercepts all client types with a single mechanism and requires no network-layer configuration.

## The recommended simulation architecture

No existing open-source project provides a local Alpaca API simulator. The closest analogues—pylivetrader's smoke test backend (internal only, not API-exposed), Backtrader's BackBroker (excellent matching logic but not a server), and QuantConnect's LEAN (C# handler architecture, comprehensive but complex)—each solve pieces of the puzzle without exposing a compatible REST/WebSocket interface.

**FastAPI** is the clear framework choice: native async support, built-in WebSocket handling, Pydantic model validation matching Alpaca's schema patterns, and the ability to serve **45,000+ concurrent WebSocket connections** per process. The architecture consists of five core components:

The **state manager** holds in-memory account state, order book, position tracker, and asset catalog, protected by `asyncio.Lock` for thread-safe concurrent access. The **matching engine** evaluates orders against current price data each tick: market orders fill immediately at bid/ask, limit orders check marketability, stops check trigger conditions, brackets manage parent-child relationships, and trailing stops track high-water marks. The **market data engine** loads historical bars from Parquet files and replays them through a simulated clock that can run at arbitrary speed multipliers. The **WebSocket broadcaster** maintains connected client sets and pushes trade updates (JSON) and market data (msgpack) through `asyncio.Queue` event propagation. The **REST API layer** maps FastAPI routes to state manager queries and matching engine commands.

For the matching engine specifically, implement Alpaca's actual behavior: fill market orders at the current ask (buy) or bid (sell), check limit order marketability against NBBO, trigger stops on price crossings, apply the **10% random partial fill** rule, enforce time-in-force rules (cancel day orders at session close, auto-cancel GTC after 90 days), and manage bracket/OCO order relationships. Backtrader's `BackBroker` source code provides an excellent reference implementation for bracket orders, OCO logic, and volume-based filling.

## Historical data strategy and synthetic market generation

**Alpaca provides approximately 10 years of 1-minute bar data for free** through accounts (even unfunded ones), making it the best primary data source. The SDK's `StockHistoricalDataClient.get_stock_bars()` returns paginated results with up to 10,000 items per request via `next_page_token`. Always request `adjustment="split"` for backtesting. For tick-level data or validation, Polygon.io ($79/month) and Databento (pay-per-use, institutional grade) are the strongest alternatives.

Store downloaded data in **Parquet files organized by `{data_type}/{timeframe}/{symbol}/{date}.parquet`**. Parquet delivers 5–10x compression over CSV, supports columnar reads (grab only close and volume without loading OHLC), and works natively with Pandas, Polars, and DuckDB. For production-scale time series, ArcticDB (by Man Group) provides versioned storage with LMDB local backend and billion-row-per-second query performance. For sharing data across parallel simulation processes, **numpy memory-mapped files on `/dev/shm`** provide zero-copy reads with no serialization overhead.

Reconstructing realistic quote streams from minute bars requires synthesizing bid-ask spreads and intra-bar price paths. Spreads follow well-established patterns: **1–3 basis points** for large-cap stocks, 5–20 for mid-caps, 20–100+ for small-caps, with a U-shaped intraday profile (wider at open and close, tightest midday). Generate synthetic ticks within each bar using a 4-point path (Open → Low → High → Close for bullish bars, reversed for bearish) with Gaussian noise bounded by the bar's high-low range. Volume follows the same U-shaped intraday curve, modeled with a quadratic baseline plus exponential spikes at open and close.

For scenario testing beyond historical replay, layer increasingly realistic models. **Geometric Brownian Motion** provides baseline price paths with constant volatility. **Merton jump-diffusion** adds Poisson-distributed jumps for earnings surprises and news events. The **Heston stochastic volatility model** captures volatility clustering with mean-reverting variance correlated to price (typically ρ ≈ −0.7 for equities). **Regime-switching models** with Markov chain transitions between bull/bear/crisis states generate realistic multi-regime scenarios. For correlated multi-stock simulation, **Cholesky decomposition** of historical correlation matrices preserves cross-asset relationships. The `fsynth` library combines Heston + Merton + regime switching into a production-ready generator.

Specific scenario injection—flash crashes (sudden 5–10% drops with partial recovery), gap-up-and-fade patterns, trading halts (zero volume with frozen price), and short squeezes (accelerating moves with increasing volume)—can be layered onto any base price path. Circuit breakers trigger at **7%, 13%, and 20%** declines from the previous close, halting matching for configurable durations.

## Running hundreds of simulations in parallel

Trading simulation is an **embarrassingly parallel** problem—each run is independent and stateless. The parallelization strategy depends on scale.

**For up to 50 instances on a single machine**, Ray provides the best developer experience. Place shared historical data in Ray's object store once with `ray.put(data)`, then launch simulations as remote functions. Ray's Plasma store uses shared memory under the hood, so workers access data zero-copy. The critical performance pattern: collect all task futures first, then call `ray.get()` once—never inside a loop. Memory-mapped NumPy arrays on `/dev/shm` provide even faster shared data access, with benchmarks showing copy-on-write fork semantics as the fastest option (1.5GB arrays shared across hundreds of processes with zero overhead).

**For 50–500 instances**, Docker Compose with `deploy: replicas: N` or `docker compose up --scale simulator=100` provides clean isolation. Omit fixed host ports and `container_name` when scaling—Docker auto-assigns both. Pass scenario parameters via environment variables (`SCENARIO_ID`, strategy parameters) or mounted config files. Each container runs one simulator + bot pair communicating over localhost.

**For 500+ instances**, Kubernetes Indexed Jobs are purpose-built for this. Set `completions: 500` and `parallelism: 50`, and each pod receives a unique `JOB_COMPLETION_INDEX` that maps to a parameter configuration. Run on **AWS Spot Instances** for up to 90% cost savings—simulations are ideal Spot workloads because they're stateless and fault-tolerant. A fleet of 300 m6i.xlarge instances costs approximately $18/hour on Spot, delivering 1,200 vCPUs.

**Optuna** is the recommended parameter optimization framework, using its Tree-structured Parzen Estimator (TPE) for Bayesian optimization. Multiple workers connect to a shared PostgreSQL database and coordinate trials automatically. The pruning feature (MedianPruner or HyperbandPruner) terminates unpromising trials early, dramatically reducing total computation. For trading strategies specifically, use **walk-forward optimization**: divide data into sequential in-sample/out-of-sample windows, optimize on in-sample, validate on out-of-sample, roll forward, and concatenate all OOS results. Each window is independently parallelizable.

Per-instance memory requirements run approximately **100–500MB** (Python interpreter, strategy code, instance-specific state) plus one shared copy of historical data. For 100 instances, budget 10–50GB RAM plus shared data; for 500, budget 50–250GB.

## Validating that your simulator tells the truth

The most dangerous failure mode in trading simulation is **false confidence from inaccurate fills**. Validation requires a multi-stage approach.

**Shadow trading** is the gold standard: run the production bot against both the real Alpaca paper trading API and your simulator simultaneously with identical market data inputs. Compare fill prices, fill timing, order statuses, and end-of-day positions. Use Kolmogorov-Smirnov tests to compare fill price distributions. Track mean slippage (`actual_fill - expected_fill`) across both systems. If distributions disagree materially, investigate before trusting simulator results.

**Regression testing** requires a library of deterministic scenarios with known expected outcomes: market gap opens, halted stocks resuming, zero-volume periods, extreme volatility, partial fills, order rejections, and bracket order cascades. Store expected outputs as golden files and assert exact matches on every code change.

The classic backtesting pitfalls remain the biggest threats to valid results. **Look-ahead bias** (using future data at decision time) is prevented structurally by event-driven architecture where each decision accesses only information available at that moment. **Survivorship bias** (only testing currently listed securities) requires downloading historical constituent lists, not just current ones. **Unrealistic fill assumptions** are the gap your simulator must honestly address—Alpaca's own paper trading assumes infinite liquidity and zero queue position, so your simulator should optionally model market impact based on order size relative to bar volume. **Overfitting** is detected through walk-forward optimization (comparing in-sample vs. out-of-sample performance), Monte Carlo analysis (bootstrap out-of-sample returns 1,000–10,000 times to estimate realistic drawdown distributions), and the Deflated Sharpe Ratio which adjusts for multiple testing. A strategy with an 8% backtest max drawdown commonly shows **18% drawdown at the 90th percentile Monte Carlo**—position sizing should use the conservative estimate.

## Conclusion

Building a complete Alpaca paper trading simulator is architecturally straightforward but demands meticulous attention to API contract fidelity. The alpaca-py SDK's `url_override` parameter and the `BaseURL` enum-patching technique solve the redirection problem cleanly. FastAPI provides the right foundation for serving both REST and WebSocket protocols with async performance. The matching engine should replicate Alpaca's documented behavior (NBBO-based fills, 10% random partial fills, no slippage) as a baseline, then add configurable realism layers (market impact, queue position, volume-limited fills) that exceed paper trading's fidelity. Historical data from Alpaca's free tier (10 years of minute bars) stored in Parquet files with memory-mapped sharing enables efficient parallel replay across hundreds of instances. The most novel insight from this research: your simulator can actually be *more accurate* than Alpaca's paper trading by modeling liquidity constraints and market impact—Alpaca's own system explicitly ignores these, creating a systematic optimism bias that your simulator can correct.