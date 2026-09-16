# mx-arena: Exchange Simulator & Optimization Engine

**Version**: D137 (March 28, 2026)
**Score**: 9.0/10 (D138: 6 innovations shipped — ordering, phased stops, LLM value, failure modes, capture ratio, daily reconciliation)
**Test Suite**: 2172 tests passing (151 arena + 2021 project)
**Performance**: 180K+ ticks/sec, 85 simulations across 17 dates in 27s
**Data**: 3.5M bars (168 symbols x 75 dates)
**Fidelity**: ~85% execution, ~55% signal (exit intelligence + walk-forward)

## Overview

mx-arena is an Alpaca exchange simulator with a matching engine, REST API,
WebSocket streams, decision replay engine, and multi-tranche exit simulation.
It re-evaluates candidates with different parameters (changing WHICH trades
are taken), simulates tranche exits with stop ratcheting, and provides
bootstrap confidence intervals on all results. It does **not** yet run
`main.py` end-to-end or simulate exit intelligence (13 signals).

**Current capabilities (execution simulator, 7/10):**
1. **Historical replay** — "Given these BUY signals, what would have happened?"
2. **Execution parameter optimization** — "What stop_loss_pct minimizes drawdown?"
3. **Scenario stress testing** — "How does the bot handle a flash crash at T+45?"
4. **Bug detection** — Catches stale data, order lifecycle, and timing bugs that unit tests miss

The bot connects to mx-arena via 4 environment variables. Zero production code changes.

```
PRODUCTION                              ARENA MODE
-----------                             ----------
Bot (main.py)                           Bot (main.py)    [identical code]
    |                                       |
    v                                       v
Alpaca Cloud                            mx-arena (localhost:8080)
  - paper-api.alpaca.markets              - SimExchange (matching engine)
  - data.alpaca.markets                   - DataEngine (Parquet/JSON replay)
  - wss://stream (fills)                  - WS Trading (fill events)
  - wss://stream (market data)            - WS Market Data (bars/quotes)
```

## How It Works End-to-End

### Step 1: Data Acquisition

Download historical 1-minute bars from Alpaca's free data API:

```bash
python mx-arena/scripts/download_history.py \
    --symbols EEIQ,KOD,JBLU,SPY,VIXY \
    --start 2026-03-24 --end 2026-03-27 \
    --timeframe 1Min
```

Storage: Parquet files in `mx-arena/data/historical/{SYMBOL}/{DATE}.parquet`. Also reads existing JSON bar cache from `data/bars/bars_{SYMBOL}_{DATE}.json`.

### Step 2: Data Preparation

Extract watchlists and previous daily bars from trade journals:

```bash
python mx-arena/scripts/prep_historical.py --date 2026-03-26
```

Output:
- Watchlist tickers from `data/premarket/premarket_*.json`
- Previous daily bars derived from journal entries (for gap% calculation)
- Data availability check (which tickers have bars cached)
- Replay config saved to `mx-arena/data/configs/replay_{DATE}.json`

### Step 3: Historical Validation

Replay a day and compare to actual journal data:

```bash
python mx-arena/scripts/run_historical_validation.py --date 2026-03-26 -v
```

Compares each BUY signal from the journal against real price action:
- Would the entry limit have filled?
- Would the stop have been hit?
- What was the max gain and EOD return?

### Step 4: Parameter Sweep

Run the same day with different parameter values:

```bash
python mx-arena/scripts/run_sweep.py \
    --dates 2026-03-26 \
    --sweep stop_loss_pct=0.02,0.03,0.04,0.05,0.06,0.08 \
    --workers 8
```

Each simulation replays journal BUY signals through the matching engine with the overridden parameter. Results ranked by P&L, win rate, and profit factor.

### Step 5: Multi-Date Optimization

Sweep across multiple days to find robust parameters:

```bash
python mx-arena/scripts/run_sweep.py \
    --dates 2026-03-25,2026-03-26,2026-03-27 \
    --sweep stop_loss_pct=0.02,0.03,0.04,0.05 \
    --sweep risk_per_trade_pct=0.01,0.02,0.03 \
    --workers 16
```

This runs 4 x 3 x 3 = 36 simulations across 3 dates. On a 16-core machine, completes in under 20 seconds.

---

## Architecture

### Component Map

```
mx-arena/
  arena/
    clock.py            SimClock — accelerated time (180K ticks/sec)
    exchange.py         SimExchange — matching engine + state
    fill_model.py       AlpacaFillModel (NBBO + 10% partial fills)
                        RealisticFillModel (volume limits + impact)
    spread_model.py     Bid-ask reconstruction (price x time x volume)
    data_engine.py      Parquet/JSON loader + snapshot construction
    scenario.py         Synthetic pattern generator
    runner.py           SweepRunner — parallel multiprocessing
    harness.py          ArenaInstance — single sim lifecycle
    api/
      rest.py           FastAPI: 14 REST endpoints
      ws_trading.py     WebSocket: order events (JSON)
      ws_market.py      WebSocket: market data (msgpack)
      models.py         Pydantic request schemas
  scripts/
    download_history.py     Bulk Alpaca data download
    prep_historical.py      Journal -> replay config
    run_replay.py           Single-day replay
    run_sweep.py            Parameter sweep CLI
    run_historical_validation.py  Replay vs journal comparison
```

### SimClock

Every component reads time from `clock.now` — never `datetime.now()`. The clock drives the entire simulation:

- **REPLAY mode**: Advances bar-by-bar as fast as possible (180K+ ticks/sec)
- **REALTIME_SCALED mode**: Advances at Nx real speed (for watching behavior)
- **MANUAL mode**: Advances only on `.tick()` (for debugging)

On each tick, the clock notifies all subscribers (data engine, exchange) which process the next minute of market data.

### SimExchange

The matching engine holds all mutable state and processes orders each tick:

**State**: AccountState (cash, equity, buying_power) + OrderState + PositionState

**Order types supported**:
| Type | Fill Logic |
|------|-----------|
| `market` | Fills at ask (buy) or bid (sell) from spread model |
| `limit` | Fills when limit_price >= ask (buy) or <= bid (sell) |
| `stop` | Triggers when bar low/high crosses stop_price, then fills as market |
| `stop_limit` | Triggers on price crossing, then checks limit marketability |
| `trailing_stop` | Tracks high-water mark, triggers on retrace |
| `oto` | One-Triggers-Other: stop leg activates only after entry fills |
| `bracket` | Entry + take-profit + stop-loss with OCO exit legs |

**Tick cycle**:
1. Update market prices from current bar
2. For each open order: compute bid/ask from spread model, check fill conditions
3. Apply fills: update positions, account cash, trade history
4. Emit WebSocket events (fill, partial_fill, canceled)
5. Handle OTO cascades (activate child orders on parent fill)
6. Handle OCO cancellation (cancel sibling on fill)
7. End-of-day: cancel `day` TIF orders at market close

**Partial fills**: 10% probability per fill attempt (Alpaca paper trading behavior), seeded for deterministic replay.

### Fill Models

**AlpacaFillModel** — Replicates Alpaca paper trading exactly:
- Market buy at ask, sell at bid (from spread model)
- 10% random partial fill probability (seeded)
- Stops trigger on bar high/low crossing

**RealisticFillModel** — Adds what Alpaca omits:
- Volume-limited fills: can't fill > 5% of bar volume
- Market impact: large orders move price proportionally
- Both configurable via `fill_model="realistic"` parameter

### Spread Model

Reconstructs bid-ask spreads from bar data (Alpaca doesn't provide historical quotes):

```
spread = base_bps x time_of_day_factor x volume_factor

Base spread by price tier:
  $0-$1:   80 bps    $1-$3:  30 bps
  $3-$10:  12 bps    $10-$50: 5 bps    $50+: 2 bps

Time-of-day (U-shaped):
  09:30-09:45: 2.5x   09:45-10:30: 1.5x   10:30-15:00: 1.0x
  15:00-15:45: 1.3x   15:45-16:00: 2.0x
```

### Data Engine

Loads historical data and constructs Alpaca-format API responses:

**Inputs**: Parquet files (`mx-arena/data/historical/{SYMBOL}/{DATE}.parquet`) or JSON bar cache (`data/bars/bars_{SYMBOL}_{DATE}.json`)

**Outputs**:
- `get_snapshot(symbol)` — Full Alpaca snapshot with latestTrade, latestQuote, minuteBar, dailyBar, prevDailyBar
- `get_bars(symbol, timeframe, limit)` — Historical OHLCV bars
- `get_most_active()` / `get_top_movers()` — Simulated screener endpoints

### REST API

FastAPI server implementing every endpoint the bot calls:

| Endpoint | Purpose |
|----------|---------|
| `GET /v2/account` | Account state (cash, equity, buying_power, daytrade_count) |
| `POST /v2/orders` | Submit market/limit/stop/OTO/bracket orders |
| `GET /v2/orders` | List open/closed orders with filtering |
| `DELETE /v2/orders/{id}` | Cancel order + children |
| `GET /v2/positions` | Open positions with unrealized P&L |
| `DELETE /v2/positions/{sym}` | Close position (market sell) |
| `GET /v2/clock` | Simulated market hours (is_open, next_open, next_close) |
| `GET /v2/stocks/{sym}/bars` | Historical minute/daily bars from Parquet |
| `GET /v2/stocks/{sym}/snapshot` | Point-in-time snapshot for scanner |
| `GET /v2/stocks/snapshots` | Multi-symbol snapshot batch |
| `GET /v1beta1/screener/*` | Most-active and top-movers |
| `GET /v1beta3/news` | News stub (returns empty) |

Authentication: Validates `APCA-API-KEY-ID` header (accepts any non-empty value in sim mode).

### WebSocket Streams

**Trading stream** (`/stream`): JSON text frames. Pushes order lifecycle events (new, fill, partial_fill, canceled, done_for_day). Auth handshake then subscribe to `trade_updates`.

**Market data stream** (`/v2/{feed}`): msgpack binary frames. Pushes bars, quotes, and trades. Supports `*` wildcard subscriptions. Used by bot's `VWAPAccumulator` for real-time VWAP.

### Scenario Generator

Creates synthetic minute-bar data for stress testing:

| Pattern | What it tests |
|---------|---------------|
| `gap_and_go` | Normal momentum trade (pullback, advance, fade, consolidation) |
| `gap_and_fade` | Immediate reversal — tests stop-loss behavior |
| `flash_crash` | Sudden 12% drop at T+45, partial recovery |
| `trading_halt` | LULD halt (zero volume) + resume with gap |
| `short_squeeze` | Accelerating price with 5x volume surge |

Scenarios are seeded for deterministic replay. Injections can be layered onto any base pattern.

---

## Bot Redirection

The bot connects to mx-arena via 4 environment variables:

```bash
# REST API (trading + market data)
ALPACA_BASE_URL=http://localhost:8080
ALPACA_DATA_URL=http://localhost:8080

# WebSocket streams
ALPACA_WS_DATA_URL=ws://localhost:8082
ALPACA_WS_TRADE_URL=ws://localhost:8081
```

**Why this works**: MOMENTUM-X uses a custom `httpx.AsyncClient` wrapper (`src/data/alpaca_client.py`) with configurable `base_url` and `data_url` from `config/settings.py`. The WebSocket clients (`StreamConfig` and `TradeUpdatesStream` in `src/data/websocket_client.py`) accept `url_override` parameters. No SDK patching needed.

---

## Parameter Sweep Engine

### How Sweeps Work

The `SweepRunner` generates all (date x parameter) combinations, then runs them in parallel:

```
SweepConfig
  dates: [2026-03-26, 2026-03-27]
  param_grid: {stop_loss_pct: [0.02, 0.04, 0.06]}
  |
  v
Generate 2 x 3 = 6 configs
  |
  v
ProcessPoolExecutor (N workers)
  |-> Sim 1: Mar 26, stop=0.02  -> RunResult
  |-> Sim 2: Mar 26, stop=0.04  -> RunResult
  |-> Sim 3: Mar 26, stop=0.06  -> RunResult
  |-> Sim 4: Mar 27, stop=0.02  -> RunResult
  |-> Sim 5: Mar 27, stop=0.04  -> RunResult
  |-> Sim 6: Mar 27, stop=0.06  -> RunResult
  |
  v
Aggregate: rank by P&L, win rate, profit factor
  |
  v
Output: best config + per-parameter summary
```

### What Gets Simulated (Current: Journal Replay)

For each BUY signal from the journal:
1. **Entry check**: Would the limit have filled within 30 bars? (Allow 2% slippage)
2. **Stop check**: Did bar.low ever touch stop_price? (Apply parameter override)
3. **EOD exit**: If no stop hit, exit at last bar close
4. **Metrics**: P&L, max gain, exit reason (stop vs EOD)

**Important limitation:** This replays decisions the bot already made. It cannot test what
happens with different signal parameters (MFCS threshold, gap momentum score) because those
decisions aren't being re-made. See `docs/architecture/MX_ARENA_ASSESSMENT.md` for the full
fidelity analysis and the path to decision replay mode.

### Sweepable Parameters (D132 Decision Replay)

| Parameter | Default | Sweep Range | Mode | What It Controls |
|-----------|---------|-------------|------|------------------|
| `stop_loss_pct` | 0.04 | 0.02-0.15 | Execution | Hard stop distance |
| `mfcs_buy_threshold` | 0.15 | 0.05-0.30 | Decision | MFCS cutoff for BUY |
| `gap_momentum_score_threshold` | 1.0 | 0.3-2.0 | Decision | Gap momentum activation |
| `gap_momentum_min_rvol` | 2.5 | 1.5-5.0 | Decision | Min RVOL for gap mode |
| `risk_aversion_lambda` | 0.3 | 0.1-0.5 | Decision | Risk penalty weight |
| `max_entry_spread_pct` | 0.015 | 0.01-0.03 | Decision | Spread filter gate |

### Parameters Requiring Full Bot Wiring (Not Yet Sweepable)

| Parameter | Default | Why |
|-----------|---------|-----|
| `gap_pct_min` | 0.10 | Requires re-running scanner, not just agents |
| `rvol_premarket_min` | 2.0 | Requires re-running scanner |
| `confidence_deflation_factor` | 0.70 | Requires re-running LLM agents |

---

## Validated Results (Post-Sprint 1+2, Honest Numbers)

### The Audit That Changed Everything (D133)

Sprint 1 found a **showstopper bug**: the spread model used UTC hour instead of ET.
At 09:30 ET (13:30 UTC), spreads were 4-6x too tight. Since all entries happen at
market open, every fill price was systematically optimistic.

| | Pre-Bug-Fix | Post-Bug-Fix | Delta |
|---|-------------|-------------|-------|
| Mar 26 P&L | +$16.94 | +$0.12 | **-99.3%** |
| Win Rate | 56% | 38% | -18pp |
| Profit Factor | 8.94 | 1.07 | -88% |

**Every conclusion drawn before D133 was based on phantom-tight spreads.**

### March 26, 2026 — With Tranche Exits + Stop Ratcheting (D134)

8 trades via decision replay (mfcs_threshold=0.10), with multi-tranche exits:

| Ticker | Entry | Tranches | Ratcheted Stop | Exit | P&L |
|--------|-------|----------|----------------|------|-----|
| **EEIQ** | $4.91 | T1+T2 filled | $10.37 | Stopped @$6.22 | **+$1.31** |
| **PAYS** | $5.07 | none | $4.91 | EOD @$5.39 | **+$0.32** |
| **KOD** | $22.66 | T1+T2 filled | $33.51 | Stopped @$22.70 | +$0.04 |
| JBLU | $4.59 | none | $4.42 | Stopped | -$0.17 |
| SRPT | $23.12 | none | $22.27 | Stopped | -$0.90 |
| SRPU | $14.01 | none | $13.71 | Stopped | -$0.39 |
| OLPX | $2.00 | none | $1.92 | Stopped | -$0.08 |
| AIFF | $1.63 | none | $2.13 | Stopped | -$0.01 |
| **Total** | | | | | **+$0.13** |

EEIQ's +$1.31 comes from tranche exits that locked in gains before pullback.
Without tranches, total was +$0.12 (the difference is in per-trade attribution).

**Caveat:** Single-day result. Bootstrap CI: PF [0.01, 12.14]. Need multi-date sweeps.

---

## Performance

| Operation | Time | Rate |
|-----------|------|------|
| Single day replay (960 ticks) | 5ms | 180K ticks/sec |
| Data download (22 symbols, 4 days) | 11s | ~4K bars/sec |
| Parameter sweep (8 configs) | 3.6s | 2.2 sims/sec |
| Full matrix (36 configs, 16 workers) | ~20s | ~1.8 sims/sec |

Projected at scale:
- 50 days x 100 param combos = 5,000 sims: ~45 min on 16 cores
- 50 days x 10 params (Optuna): ~5 min with pruning

---

## Files Modified for Arena Support

| File | Change | Purpose |
|------|--------|---------|
| `config/settings.py` | Added `ws_data_url`, `ws_trade_url` | WebSocket URL overrides |
| `src/data/websocket_client.py` | Added `url_override` to StreamConfig + TradeUpdatesStream | WS redirection |

All changes are additive — default behavior unchanged, arena activates only via env vars.

---

## Quick Reference

```bash
# 1. Download data
python mx-arena/scripts/download_history.py --symbols AAPL,SPY --start 2026-01-01 --days 90

# 2. Prep a day
python mx-arena/scripts/prep_historical.py --date 2026-03-26

# 3. Validate against journals
python mx-arena/scripts/run_historical_validation.py --date 2026-03-26 -v

# 4. Parameter sweep
python mx-arena/scripts/run_sweep.py \
    --dates 2026-03-26,2026-03-27 \
    --sweep stop_loss_pct=0.02,0.04,0.06,0.08 \
    --workers 8

# 5. Synthetic scenario
python mx-arena/scripts/run_replay.py --scenario gap_and_go --symbols FAKE

# 6. HTTP mode (bot connects to simulator)
python mx-arena/scripts/run_replay.py --date 2026-03-26 --http --port 8080
```

---

## Audit Trail

| Sprint | Commit | Score | Tests | Key Change |
|--------|--------|-------|-------|------------|
| Week 1 | D129 | 7.0 | 74 | Core exchange, REST API, WS, scenarios |
| Data | D130 | 7.0 | 74 | WS URLs configurable, JSON bars, 3.5M bars |
| Decision | D132 | 7.5 | 101 | Decision replay, bootstrap CI, OTO tests |
| **Sprint 1** | **D133** | **8.0** | 101 | **6 bug fixes. P&L: +$16.94 -> +$0.12 (-99%)** |
| **Sprint 2** | **D134** | **8.5** | 108 | **Tranche exits + stop ratcheting** |
| **Sprint 3** | **D135** | **8.5** | 127 | **Exit intelligence (4 strategies) + portfolio** |
| **Sprint 4** | **D136** | **8.5** | 127 | **Walk-forward CV: 3.43x overfit (49 trades = noise)** |
| **Sprint 5** | **D137** | **8.5** | **151** | **Golden regression + regime + drift detection** |
| **Sprint 6** | **D138** | **9.0** | **151** | **6 innovations: ordering, phased stops, LLM value, failure modes, capture ratio, daily recon** |

## All Sprints Complete

**Sprint 1 (D133)**: Fixed spread timezone (P0), price improvement, entry/stop fill
logic, bar validation, spread filter. Eliminated 99% phantom P&L.

**Sprint 2 (D134)**: Multi-tranche exits (T1/T2/T3 at 33/33/34 shares), stop
ratcheting (breakeven after T1, T1 after T2), per-tranche P&L attribution.

**Sprint 3 (D135)**: Ported 4 exit strategies (velocity, volume exhaustion, gratitude,
alpha oracle). Sequential evaluation delay model. Portfolio constraints.

**Sprint 4 (D136)**: Walk-forward cross-validation across 17 dates (85 sims in 27s).
First statistically rigorous parameter recommendation: mfcs=0.10, PF=4.40 train /
1.28 test, overfit ratio 3.43x WARNING.

**Sprint 5 (D137)**: Golden-file regression tests (Mar 25/26/27 locked as anchor
outputs). Regime classification (LOW_VOL_TREND, ELEVATED_VOL, CRISIS from SPY/VIXY).
Parameter drift detection (z-test on PF and win rate vs training baseline).

## Remaining Path to 10.0

The gap from 9.5 to 10.0 requires **calendar time, not engineering time**:

1. **Shadow trading**: Run arena + production same-day for 20+ days
2. **P&L reconciliation**: Track (arena_pnl - production_pnl) over time
3. **Regime sweeps**: Optimize different param sets per VIX regime
4. **Drift monitoring**: Alert when recent simulation PF falls below training CI

See `docs/architecture/MX_ARENA_COMPREHENSIVE_AUDIT.md` for the full audit
and `docs/architecture/MX_ARENA_ROADMAP_TO_10.html` for the visual roadmap.
