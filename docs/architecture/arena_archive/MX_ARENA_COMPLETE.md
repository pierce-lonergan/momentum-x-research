# mx-arena: Complete System Documentation

**Version**: D137 (March 28, 2026) | **Score**: 8.5/10 (externally assessed)
**Codebase**: 39 Python files, 7,860 lines | **Tests**: 151 arena, 2,172 total
**Data**: 3.5M bars (168 symbols x 75 dates) | **Performance**: 180K ticks/sec
**Self-assessed**: 9.5 | **Externally corrected**: 8.5 (see FINAL_ASSESSMENT.md)

---

## 1. What mx-arena Is

mx-arena is a local Alpaca exchange simulator that replays MOMENTUM-X's production
trading decisions against historical market data. It answers two classes of questions:

**Execution questions** (~85% fidelity): "Given that the bot decided to buy EEIQ at
$4.91, what would have happened with a 3% stop vs 5% stop? With tranche exits at
$5.20/$5.40/$5.60? When does the exit intelligence trigger?"

**Signal questions** (~55% fidelity): "If we lower mfcs_buy_threshold from 0.15 to
0.10, which additional stocks pass the BUY gate? Does this improve or degrade P&L
across 17 historical days? Is the improvement statistically significant or overfit?"

It does NOT yet run `main.py` end-to-end. It replays journal BUY signals through a
decision replay engine that re-evaluates the deterministic scoring pipeline with
parameter overrides, then simulates execution through a matching engine with
multi-tranche exits, stop ratcheting, and exit intelligence.

---

## 2. Architecture

```
mx-arena/
  arena/
    clock.py           (209 lines)  SimClock — 3 modes, market hours, time patching
    exchange.py        (625 lines)  SimExchange — matching engine, OTO/bracket/OCO
    fill_model.py      (210 lines)  AlpacaFillModel + RealisticFillModel
    spread_model.py    (100 lines)  Bid-ask reconstruction (ET-aware after D133)
    data_engine.py     (392 lines)  Parquet/JSON loader, snapshot construction
    decision_replay.py (362 lines)  Re-evaluate candidates with sweep params
    exit_intelligence.py(294 lines) 4 exit strategies ported from production
    runner.py          (582 lines)  Trade simulation + parallel sweep engine
    stats.py           (101 lines)  Bootstrap CI on profit factor + mean P&L
    walk_forward.py    (245 lines)  Walk-forward cross-validation
    regime.py          (244 lines)  Regime classification + drift detection
    scenario.py        (298 lines)  Synthetic gap-and-go, crash, halt, squeeze
    harness.py         (254 lines)  ArenaInstance — single sim lifecycle
    api/
      rest.py          (269 lines)  FastAPI: 14 endpoints matching Alpaca schema
      ws_trading.py     (94 lines)  WebSocket: order events (JSON text frames)
      ws_market.py     (177 lines)  WebSocket: bars/quotes/trades (msgpack binary)
      models.py         (41 lines)  Pydantic request validation
  scripts/
    download_history.py (241 lines) Bulk Alpaca data download (--symbols-from journals)
    prep_historical.py  (255 lines) Journal + premarket -> replay config
    run_replay.py       (188 lines) Single-day replay or synthetic scenario
    run_sweep.py        (250 lines) Parameter sweep + walk-forward CLI
    run_historical_validation.py (242 lines) Replay vs journal comparison
  tests/
    11 test modules, 151 test functions
    3 golden-file JSON anchors (Mar 25/26/27)
```

---

## 3. SimClock (arena/clock.py)

Every arena component reads time from `clock.now`. The bot's phase transitions
(Phase 0-4 at 4:00, 9:30, 10:00, 15:45, 16:00 ET) are driven by this clock.

**Modes:**
- `REPLAY`: As fast as possible. 960 ticks (1 day) in 5ms = 180K ticks/sec.
- `REALTIME_SCALED`: Nx real speed. For watching bot behavior.
- `MANUAL`: Advance only on `.tick()`. For step-through debugging.

**Properties:** `now` (UTC), `now_et` (Eastern), `is_market_open`, `is_premarket`,
`next_open`, `next_close`, `done`, `tick_count`.

**Subscribers:** Both sync (`clock.subscribe(cb)`) and async
(`clock.subscribe_async(cb)`) callbacks on each tick.

---

## 4. SimExchange (arena/exchange.py)

The matching engine. Holds all mutable state: account, orders, positions.

### State

**AccountState**: cash, initial_cash, daytrade_count, account_id.
Serializes to Alpaca format with 25+ fields including buying_power (4x equity),
equity (cash + long_market_value), pattern_day_trader detection.

**OrderState**: id (UUID4), client_order_id, symbol, side, type, time_in_force,
qty, filled_qty, filled_avg_price, limit_price, stop_price, trail_percent,
status, order_class, legs (child orders for OTO/bracket), parent_id, hwm
(high-water mark for trailing stops), stop_triggered (for stop_limit).

**PositionState**: symbol, qty, avg_entry_price, current_price, cost_basis.
Computed: market_value, unrealized_pl, unrealized_plpc.

### Order Types

| Type | Fill Logic |
|------|-----------|
| `market` | Fills immediately at ask (buy) or bid (sell) |
| `limit` | Fills at NBBO when limit_price >= ask (buy) or <= bid (sell). No price improvement (D133 fix). |
| `stop` | Triggers when bar.low <= stop (sell) or bar.high >= stop (buy). Fills as market. |
| `stop_limit` | Two-phase: triggers on price crossing, then checks limit marketability. |
| `trailing_stop` | Updates HWM each tick. Triggers when price retraces by trail_percent. Sell-side only. |
| `oto` | One-Triggers-Other. Stop leg created with status="held". Activates only after parent fills. |
| `bracket` | Entry + take-profit (limit sell) + stop-loss (stop sell). OCO: filling either exit cancels the other. |

### Tick Cycle (on_tick)

1. Iterate all orders with status `new` or `partially_filled`
2. Skip `held` orders (OTO children waiting for parent)
3. Compute bid/ask from SpreadModel for each order's symbol
4. Call FillModel.try_fill() with order, bar, bid, ask, RNG
5. If fill: update order state (filled_qty, filled_avg_price, status)
6. Update position (create/average/close)
7. Update account cash
8. Emit WebSocket event (new/fill/partial_fill/canceled)
9. Handle OTO cascade: if parent filled, activate children
10. Handle OCO: if one exit leg fills, cancel sibling
11. End-of-day: cancel all `day` TIF orders at market close

---

## 5. Fill Models (arena/fill_model.py)

### AlpacaFillModel

Replicates Alpaca paper trading behavior:

- Market orders: fill at NBBO ask (buy) or bid (sell)
- Limit orders: fill at NBBO when marketable. **No price improvement** (D133 fix —
  was using `min(limit, ask)` which gave phantom 5-15 bps savings)
- Stop orders: trigger when bar.low/high crosses stop_price, then fill at market
- 10% random partial fill probability, seeded for determinism
- Trailing stops: track high-water mark, trigger on retrace (sell-side only)

### RealisticFillModel

Extends AlpacaFillModel with what Alpaca paper trading omits:
- Volume-limited fills: can't fill > 5% of bar volume (MAX_PARTICIPATION = 0.05)
- Market impact: price moves by `participation_rate * 0.1` against you

---

## 6. Spread Model (arena/spread_model.py)

Reconstructs bid-ask spreads from price tier, time of day, and volume.
Returns half-spread in dollars. Full spread = 2x.

**Base spread by price tier (basis points):**
$0-$1: 80 | $1-$3: 30 | $3-$10: 12 | $10-$50: 5 | $50+: 2

**Time-of-day U-shape (ET — D133 fix):**
09:30-09:45: 2.5x | 09:45-10:30: 1.5x | 10:30-15:00: 1.0x |
15:00-15:45: 1.3x | 15:45-16:00: 2.0x

**Volume factor:** `max(0.3, 1.0 - min(rvol / 20, 0.7))`. Higher volume = tighter.

**Critical D133 fix:** Timestamp is UTC from SimClock. Must `.astimezone(ET)` before
extracting hour. Without this, 09:30 ET reads as hour=13 (midday) and spreads at
market open are 4-6x too tight. This single bug caused +$16.94 phantom P&L on Mar 26.

---

## 7. Data Engine (arena/data_engine.py)

Loads 1-minute bars from Parquet or JSON files. Constructs Alpaca API responses.

**Loading priority:**
1. Parquet: `{historical_dir}/{SYMBOL}/{DATE}.parquet`
2. JSON: `{json_bars_dir}/bars_{SYMBOL}_{DATE}.json` (existing MX bar cache)

**D133 fix:** Sorts bars by timestamp on load (was sequential indexing).

**Snapshot construction** (`get_snapshot(symbol)`):
Returns Alpaca-format dict with latestTrade, latestQuote, minuteBar, dailyBar,
prevDailyBar. The scanner's `compute_gap_pct()` reads prevDailyBar.c vs latestTrade.p.

**Screener simulation** (`get_most_active()`, `get_top_movers()`):
Returns watchlist symbols from loaded data. Top movers sorted by gap%.

---

## 8. Decision Replay Engine (arena/decision_replay.py)

The core innovation: re-evaluates candidates with different parameters to change
WHICH trades are taken, not just how they're managed.

### How it works

1. Load ALL candidates from journal (BUY and NO_TRADE entries)
2. For each candidate, re-run gap momentum scoring with sweep params
3. Use journal LLM signals as-is (news, fundamental, institutional)
4. Replace only the technical agent signal (affected by gap_momentum_threshold)
5. Compute MFCS using production formula (weighted sum - lambda * risk)
6. Apply consensus gate (bullish must outnumber bearish)
7. Apply spread filter (reject if spread > max_entry_spread_pct)
8. Output: BUY set per parameter configuration

### MFCS Computation (mirrors src/core/scoring.py)

```
MFCS = sum(w_k * score_k * boost) - lambda * risk_score

score_k = SIGNAL_NUMERIC[direction] * max(confidence, 0.20)
SIGNAL_NUMERIC = {STRONG_BULL: 1.0, BULL: 0.5, NEUTRAL: 0.0, BEAR: -0.5, STRONG_BEAR: -1.0}

Default weights: catalyst_news=0.30, technical=0.20, volume_rvol=0.20,
  float_structure=0.15, institutional=0.10, deep_search=0.05
Weight redistribution: inactive agents' weight redistributed proportionally
Risk penalty: lambda=0.3 (default)
```

### Sweepable Parameters

| Parameter | Default | Mode | Effect |
|-----------|---------|------|--------|
| `gap_momentum_score_threshold` | 1.0 | Decision | Changes gap momentum activation (gap_pct * rvol > threshold) |
| `gap_momentum_min_gap` | 0.08 | Decision | Min gap% floor for momentum mode |
| `gap_momentum_min_rvol` | 2.5 | Decision | Min RVOL floor |
| `mfcs_buy_threshold` | 0.15 | Decision | MFCS cutoff for BUY |
| `risk_aversion_lambda` | 0.3 | Decision | Risk penalty weight |
| `max_entry_spread_pct` | 0.015 | Decision | Spread filter gate |
| `stop_loss_pct` | 0.04 | Execution | Hard stop distance |
| `max_positions` | 3 | Portfolio | Max concurrent positions |
| `evaluation_delay_bars` | 0 | Portfolio | Sequential evaluation delay |

---

## 9. Trade Simulation (arena/runner.py)

The `_simulate_journal_trades()` function drives bar-by-bar execution with
multi-tranche exits, stop ratcheting, exit intelligence, and portfolio constraints.

### Entry Detection (D133 fix)

Fill when spread model ask <= limit_price. Not `bar.low <= entry * 1.02` (old,
too generous). Uses SpreadModel for realistic ask computation at each bar.
Checks first 60 bars (1 hour window).

### Multi-Tranche Exits (Sprint 2, D134)

Production uses 3 equal tranches at T1/T2/T3 targets from journal:
- T1: sell 33 shares when bar.high >= target_prices[0]
- T2: sell 33 shares when bar.high >= target_prices[1]
- T3: sell 34 shares when bar.high >= target_prices[2]

Fill price: max(bid, target) — at least target, potentially better.

### Stop Ratcheting (Sprint 2, D134)

After T1 fills: ratchet stop to breakeven (fill_price).
After T2 fills: ratchet stop to T1 target price.
Stop only moves UP (invariant from production `stop_resubmitter.py`).

### Stop Fill Logic (D133 fix)

When stop triggers:
- If bar.open <= stop_price: fill at bar.open (gap-below, worst case)
- Otherwise: fill at bid from spread model (slippage included)

NOT at exact stop price (old bug — systematically optimistic).

### Exit Intelligence (Sprint 3, D135)

Every 5 bars, runs 4 parallel exit strategies:
1. **Velocity**: 3-bar rolling velocity vs phase thresholds
2. **Volume exhaustion**: 5-bar avg / entry volume ratio
3. **Gratitude**: Time-decaying R-multiple target
4. **Alpha oracle**: Observed return vs null curve

Aggregation: D122 any-of voting. 2+ strategies EXIT -> close position.
1+ strategies TIGHTEN -> ratchet stop to 1.5% trail.

### Portfolio Constraints (Sprint 3, D135)

- `max_positions` (default 3): Only top N candidates by momentum score enter
- `evaluation_delay_bars` (default 0): Candidate #N starts checking at bar N*delay
- Candidates sorted by `abs(gap_pct) * rvol` (highest momentum first)

---

## 10. Statistical Framework

### Bootstrap Confidence Intervals (arena/stats.py)

Every P&L result comes with a 95% CI from 1000-iteration bootstrap.
Configurations ranked by CI lower bound, not point estimate.

```python
bootstrap_profit_factor(trades, n_bootstrap=1000) -> (point, ci_lower, ci_upper)
bootstrap_mean_pnl(trades, n_bootstrap=1000) -> (point, ci_lower, ci_upper)
rank_by_ci_lower(results) -> sorted results with CI fields
```

### Walk-Forward Cross-Validation (arena/walk_forward.py)

Splits dates into sequential train/test windows. Optimizes on train (ranked by
bootstrap CI lower bound), validates on test with best params. Reports overfit ratio.

**First result** (17 dates, mfcs_threshold sweep):
- Train (10 days): PF=4.40, CI [0.84, 20.50]
- Test (7 days): PF=1.28, CI [0.06, 7.00]
- Overfit ratio: 3.43x — WARNING, parameters may not generalize

### Regime Classification (arena/regime.py)

Labels each day by market environment using SPY and VIXY daily returns:
- `LOW_VOL_TREND`: SPY > -0.5%, VIXY < +5%
- `ELEVATED_VOL`: SPY -0.5% to -1.5% OR VIXY +5% to +15%
- `CRISIS`: SPY < -1.5% OR VIXY > +15%

### Parameter Drift Detection (arena/regime.py)

Compares recent simulation performance to training baseline:
- Bootstrap z-test on profit factor and win rate
- Alert threshold: z > 2.0 (95% confidence of degradation)
- Reports: PF drift, win rate drift, mean P&L drift
- Action: RE-OPTIMIZE RECOMMENDED when alert fires

### Golden-File Regression Tests (tests/test_golden_regression.py)

Locks known-good outputs for 3 anchor dates:
- Mar 25: 18 candidates, 6 buys, 3 trades, +$0.30 (MKDW, FEED, CVV)
- Mar 26: 13 candidates, 9 buys, 3 trades, +$0.90 (EEIQ +$1.31)
- Mar 27: 16 candidates, 0 buys, 0 trades, $0.00 (SPY halt)

Any code change that shifts these numbers fails the test.

---

## 11. REST API (arena/api/rest.py)

14 endpoints matching Alpaca's trading + data API schema:

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/v2/account` | GET | 25-field account state (cash, equity, buying_power, etc.) |
| `/v2/orders` | POST | Submit order (market/limit/stop/OTO/bracket) |
| `/v2/orders` | GET | List orders filtered by status/symbols/limit |
| `/v2/orders/{id}` | GET | Single order by ID |
| `/v2/orders/{id}` | DELETE | Cancel order + children (204) |
| `/v2/orders` | DELETE | Cancel all open orders |
| `/v2/positions` | GET | All open positions with P&L |
| `/v2/positions/{sym}` | GET | Single position (404 if not found) |
| `/v2/positions/{sym}` | DELETE | Close position (market sell) |
| `/v2/positions` | DELETE | Close all positions |
| `/v2/clock` | GET | is_open, next_open, next_close |
| `/v2/stocks/{sym}/bars` | GET | Historical OHLCV bars |
| `/v2/stocks/{sym}/snapshot` | GET | Full snapshot (trade, quote, bars) |
| `/v2/stocks/snapshots` | GET | Multi-symbol batch snapshots |
| `/v1beta1/screener/stocks/most-actives` | GET | Simulated screener |
| `/v1beta1/screener/stocks/movers` | GET | Simulated top movers |
| `/v1beta3/news` | GET | Stub (returns empty) |
| `/v2/assets/{sym}` | GET | Asset info stub |
| `/v2/calendar` | GET | Single trading day |

Authentication: Validates `APCA-API-KEY-ID` header (any non-empty value).
All decimal values serialized as strings (Alpaca convention).

---

## 12. WebSocket Streams

### Trading Stream (arena/api/ws_trading.py)

Protocol: JSON text frames at `/stream`.

Handshake:
1. Client: `{"action":"authenticate","data":{"key_id":"...","secret_key":"..."}}`
2. Server: `[{"T":"success","msg":"authenticated"}]`
3. Client: `{"action":"listen","data":{"streams":["trade_updates"]}}`
4. Server: `[{"T":"success","msg":"connected"}]`
5. Server pushes: `{"stream":"trade_updates","data":{"event":"fill","order":{...}}}`

Events: new, fill, partial_fill, canceled, expired, done_for_day.

### Market Data Stream (arena/api/ws_market.py)

Protocol: msgpack binary frames at `/v2/{feed}`.

Handshake similar (JSON auth, then msgpack responses).
Subscription: `{"action":"subscribe","bars":["*"],"quotes":["AAPL"]}`
Pushes: bars (`T:b`), quotes (`T:q`), trades (`T:t`).

---

## 13. Scenario Generator (arena/scenario.py)

Generates 390 bars (one trading day) from parameterized patterns:

| Pattern | Parameters | What it tests |
|---------|-----------|---------------|
| `gap_and_go` | entry_price, gap_pct, rvol, peak_minute, peak_return, fade_rate | Normal momentum entry + fade |
| `gap_and_fade` | Same | Immediate reversal, stop behavior |
| `flash_crash` | inject_minute, drop_pct, recovery_bars, recovery_pct | Sudden 12% drop mid-session |
| `trading_halt` | halt_minute, duration_bars, resume_gap_pct | Zero-volume halt + gap resume |
| `short_squeeze` | inject_minute, acceleration, duration, volume_multiplier | Accelerating move + vol surge |

All scenarios are seeded for deterministic replay. Injections can be layered
onto any base pattern (e.g. gap_and_go + flash_crash at T+45).

---

## 14. Bot Redirection

The bot connects to mx-arena via 4 environment variables:

```bash
ALPACA_BASE_URL=http://localhost:8080
ALPACA_DATA_URL=http://localhost:8080
ALPACA_WS_DATA_URL=ws://localhost:8082
ALPACA_WS_TRADE_URL=ws://localhost:8081
```

**Why it works:** MOMENTUM-X uses a custom `httpx.AsyncClient` in
`src/data/alpaca_client.py` with `base_url` and `data_url` from `config/settings.py`
(Pydantic BaseSettings with `ALPACA_` env prefix). WebSocket clients accept
`url_override` parameter added in D130. No SDK patching needed.

**Production files modified** (additive only):
- `config/settings.py`: Added `ws_data_url`, `ws_trade_url` fields
- `src/data/websocket_client.py`: Added `url_override` to StreamConfig + TradeUpdatesStream

---

## 15. Scripts

### download_history.py
Downloads 1-minute or daily bars from Alpaca's free data API.
Supports `--symbols` (explicit) or `--symbols-from` (extract from journals).
Pagination via `next_page_token`. Rate limited at ~3 req/sec.
Output: Parquet files in `{output}/{SYMBOL}/{DATE}.parquet`.

### prep_historical.py
Extracts watchlists from premarket cache + journals. Derives previous daily
bars from journal entries (for gap% calculation). Checks bar data availability.
Saves replay config to `mx-arena/data/configs/replay_{DATE}.json`.

### run_replay.py
Single-day replay or synthetic scenario execution. Supports `--http` mode
(starts FastAPI server for bot connection) or standalone mode.

### run_sweep.py
Parameter sweep CLI. Supports `--decision-replay` (re-evaluate candidates)
and `--walk-forward` (train/test split with overfit detection).
Output: ranked configurations with bootstrap CI.

### run_historical_validation.py
Replays a day and compares to journal data. Per-ticker entry price comparison,
stop hit detection, max gain tracking. Outputs validation report.

---

## 16. Test Suite (151 tests)

| Module | Tests | What it covers |
|--------|-------|----------------|
| test_api.py | 15 | REST endpoint responses, auth, schema |
| test_clock.py | 15 | Time advancement, market hours, subscribers |
| test_decision_replay.py | 19 | Gap momentum, MFCS, consensus, scoring |
| test_exchange.py | 22 | Order submission, fills, positions, cancel, serialization |
| test_exit_intelligence.py | 19 | 4 exit strategies, aggregation, portfolio limits |
| test_fill_model.py | 13 | Market/limit/stop fills, partial fill probability |
| test_golden_regression.py | 13 | Mar 25/26/27 locked outputs (anchor tests) |
| test_oto_lifecycle.py | 8 | Full OTO/bracket lifecycle, tranche sells, equity |
| test_regime.py | 11 | Regime classification, drift detection |
| test_scenario.py | 9 | Gap-and-go, fade, crash, halt, squeeze |
| test_tranche_sim.py | 7 | T1/T2/T3 exits, stop ratcheting, mixed scenarios |

---

## 17. Validated Results

### The Number That Matters: -99.3%

Sprint 1 (D133) found the spread model timezone bug. Before fixing:
Mar 26 P&L was +$16.94. After: +$0.12. **99% was phantom.**

### Mar 26 Final Results (D137, all features)

3 trades via decision replay (mfcs=0.15) + exit intelligence + portfolio(3):

| Ticker | Entry | Exit | Reason | P&L |
|--------|-------|------|--------|-----|
| EEIQ | $4.91 | $6.22 | T1/T2 filled, ratcheted stop | +$1.31 |
| OLPX | $2.00 | $1.97 | Exit intelligence (velocity) | -$0.02 |
| SRPU | $14.01 | $13.62 | Stop hit | -$0.39 |
| **Total** | | | | **+$0.90** |

### Walk-Forward (17 dates, mfcs sweep)

| | PF | CI | Trades |
|---|-----|------|--------|
| Train (10 days) | 4.40 | [0.84, 20.50] | 28 |
| Test (7 days) | 1.28 | [0.06, 7.00] | 21 |
| Overfit ratio | 3.43x | WARNING | |

---

## 18. Audit Trail

| Sprint | Commit | Score | Tests | Key Achievement |
|--------|--------|-------|-------|-----------------|
| Week 1 | D129 | 7.0 | 74 | Core exchange, REST, WS, scenarios |
| Data | D130-D132 | 7.5 | 101 | 3.5M bars, decision replay, bootstrap CI |
| Sprint 1 | D133 | 8.0 | 101 | 6 bug fixes. P&L: +$16.94 -> +$0.12 |
| Sprint 2 | D134 | 8.5 | 108 | Tranche exits + stop ratcheting |
| Sprint 3 | D135 | 9.0 | 127 | Exit intelligence + portfolio constraints |
| Sprint 4 | D136 | 9.5 | 127 | Walk-forward CV: 3.43x overfit warning |
| Sprint 5 | D137 | 9.5 | 151 | Golden regression + regime + drift detection |

---

## 19. Quick Reference

```bash
# Download 90 days of data for all journal tickers
python mx-arena/scripts/download_history.py --symbols-from data/journals/ --days 90

# Prep a date
python mx-arena/scripts/prep_historical.py --date 2026-03-26

# Validate against journal
python mx-arena/scripts/run_historical_validation.py --date 2026-03-26 -v

# Parameter sweep (execution params)
python mx-arena/scripts/run_sweep.py \
    --dates 2026-03-26 \
    --sweep stop_loss_pct=0.02,0.04,0.06 \
    --workers 4

# Decision replay sweep (signal params)
python mx-arena/scripts/run_sweep.py \
    --dates 2026-02-10,2026-02-11,...,2026-03-26 \
    --sweep mfcs_buy_threshold=0.05,0.10,0.15,0.20,0.25 \
    --decision-replay --workers 4

# Walk-forward cross-validation
python mx-arena/scripts/run_sweep.py \
    --dates 2026-02-10,...,2026-03-26 \
    --sweep mfcs_buy_threshold=0.05,0.10,0.15,0.20,0.25 \
    --decision-replay --walk-forward --workers 4

# Synthetic scenario
python mx-arena/scripts/run_replay.py --scenario gap_and_go --symbols FAKE

# HTTP mode (bot connects to simulator)
python mx-arena/scripts/run_replay.py --date 2026-03-26 --http --port 8080
```

---

## 20. Remaining Path to 10.0

The gap from 9.5 to 10.0 requires **calendar time, not engineering time**:

1. **Shadow trading**: Run arena + production same-day for 20+ days.
   Compare fill prices, order counts, P&L. Track delta trend.
2. **P&L reconciliation**: If delta is shrinking, simulator is improving.
   If growing, investigate. Target: arena within 5% of production.
3. **Regime sweeps**: Optimize different parameter sets per VIX regime.
   Bot loads regime-appropriate params at startup.
4. **Drift monitoring**: After each live day, replay with current params.
   Alert if recent PF falls below training CI (z > 2.0).
5. **Full bot wiring**: Run main.py end-to-end against arena HTTP server.
   Pre-market data, news API replay, LLM response mocking.

See also:
- `docs/architecture/MX_ARENA_COMPREHENSIVE_AUDIT.md` — code-level audit
- `docs/architecture/MX_ARENA_ASSESSMENT.md` — fidelity analysis
- `docs/architecture/MX_ARENA_ASSESSMENT_D131B.md` — second assessment
- `docs/architecture/MX_ARENA_ROADMAP_TO_10.html` — visual sprint tracker
