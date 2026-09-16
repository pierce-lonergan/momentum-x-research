# Momentum-X System Architecture — Current State (D126)

## Overview
Momentum-X is an automated small-cap momentum trading system that identifies gap-up stocks in pre-market, evaluates them through a multi-agent scoring pipeline, and executes intraday long entries with managed exits. Paper trading on Alpaca.

## Pipeline Flow

```
Phase 0: Pre-Market Boot (04:30 ET)
  |-> Model preflight, stale process cleanup, broker position sync

Phase 1: Pre-Market Scanning (04:30 - 09:20 ET)
  |-> Alpaca most-active (50) + movers/gainers (20)
  |-> D123: Derivative symbol cleaning (.WS/.RT/trailing-W -> common)
  |-> EMC filter: gap > 5%, price $3-$50, RVOL > 1.5x (or high-dolvol override)
  |-> GEX filter: options chain analysis
  |-> Result: 5-15 candidates on watchlist

Phase 1.5: Fast-Path Pre-Scoring (09:20 ET)
  |-> Run deterministic agents (technical + risk) on all candidates
  |-> D126: Gap momentum mode activates for gap_pct x rvol > 1.0
  |-> Cache partial MFCS scores for 9:30 fast-path entries

Phase 2: Market Open Evaluation (09:30 ET)
  |-> Full agent evaluation: news, technical, fundamental, institutional, deep_search, manipulation, risk
  |-> D125: fundamental_agent skips LLM when no data (D94_NO_DATA_SKIP)
  |-> MFCS scoring with weighted agent signals
  |-> D101 consensus gate: min 1 directional agent (D125)
  |-> D124 consensus alignment: bearish cannot outnumber bullish
  |-> Kelly tier sizing -> position size
  |-> D106 manipulation classifier gates

Phase 2->Execution: Order Submission
  |-> D125: Context-aware spread filter (1% normal, 3% for MFCS >= 0.15)
  |-> OTO order (buy limit + stop sell)
  |-> D124: position_intent="close" on stop orders
  |-> Stop conversion -> standalone stop + tranche limit sells
  |-> D124: Retry fallback if standalone stop fails

Phase 3: Intraday Monitoring (09:31 - 15:55 ET)
  |-> Continuous re-evaluation every ~60s
  |-> Exit intelligence: 13-signal composite + parallel strategy engine
  |-> D122: Parallel exit strategies (velocity, volume_exhaustion, gratitude, alpha_oracle)
  |-> D122: PullbackClassifier with minimum advance gate (1%)
  |-> D122: AlphaDecayOracle with 5-min minimum evaluation time
  |-> Stop ratcheting via ATR-grounded TIGHTEN
  |-> D122: Gap-day stop widening with D124 dynamic cap (35% max)

Phase 4: End of Day (16:00 ET)
  |-> Force-close remaining positions
  |-> Session metrics + experiment journal
  |-> Signal history logging for post-session analysis
```

## Agent Architecture

### LLM Agents (Tier 1/2)
| Agent | Model Tier | Purpose | Typical Signal on Small-Caps |
|-------|-----------|---------|------------------------------|
| news_agent | Tier 1 (Qwen3.5-397B) | Catalyst identification from headlines | NEUTRAL (no news) or BULL/BEAR |
| fundamental_agent | Tier 2 (Qwen3-235B) | Float structure, SEC filings, short interest | D94_NO_DATA_SKIP (no float data) |
| deep_search_agent | Tier 2 | SEC filings, social data, historical precedent | D94_NO_DATA_SKIP (no data) |
| institutional_agent | Tier 2 | Institutional ownership, 13-F filings | Usually absent/timed out |

### Deterministic Agents (0% failure rate)
| Agent | Purpose | D126 Change |
|-------|---------|-------------|
| technical_agent | RSI, MACD, EMA, VWAP, breakout detection | Gap Momentum Mode override |
| risk_agent | Liquidity, false breakout, catalyst validity | Unchanged |
| manipulation_classifier | Promotional pump detection | Unchanged |

### D126 Gap Momentum Mode
Activates when: `gap_pct x rvol > 1.0 AND gap > 8% AND rvol > 2.5x AND minutes < 30`

Replaces lagging indicators (MACD, EMA) with:
- +2 bullish base (the gap IS the signal)
- VWAP above: +1 bullish; below: +1 bearish
- RSI 50-75: +1 bullish (no penalty for >75)
- Gradual decay: full 0-10min, linear blend 10-30min
- Time confidence: 0.8 at T+0 (not 0.0), 1.0 by T+5

## Scoring: MFCS (Multi-Factor Confidence Score)

Weighted sum of agent signals:
- catalyst_news: 0.35 weight
- technical: 0.30 weight
- volume_rvol: 0.20 weight
- float_structure: 0.15 weight
- deep_search: 0.00 weight (disabled)

Risk adjustment: MFCS = raw_score - (risk x lambda)
Confidence deflation: 0.7x applied to LLM agent confidences

Entry threshold: MFCS > 0.15 (qualified for debate/entry)

## Safety Gates (in order)

1. **EMC filter**: gap > 5%, price $3-$50, RVOL > 1.5x
2. **D101 consensus gate**: min 1 directional agent
3. **D124 consensus alignment**: bearish count < bullish count
4. **MFCS threshold**: score > 0.15
5. **D106 manipulation classifier**: PROMOTIONAL -> half position, tight stop, 10:30 exit
6. **D101 VWAP bias**: reject long entry if price < VWAP (after first 5 min)
7. **D125 spread filter**: 1% max (3% for MFCS >= 0.15)
8. **Kelly tier sizing**: position size based on confidence tier
9. **Portfolio risk limits**: max positions, max per-position allocation

## Stop Loss Architecture

- **ATR-grounded**: entry - max(2.0 x ATR, 4% floor)
- **D124 dynamic gap-day cap**: min(gap x 0.5, 35%) replaces hard 20% cap
- **D122 gap-day widening**: gap_pct x 0.5 as minimum stop distance
- **Ratcheting**: ATR-based TIGHTEN via parallel exit strategies
- **D124 position_intent="close"**: prevents 422 "cannot be sold short"

## Configuration (Key Parameters)

| Parameter | Value | Source |
|-----------|-------|--------|
| min_directional_agents | 1 | D125 |
| confidence_deflation_factor | 0.70 | D106 |
| parallel_strategies_active | True | D122 |
| parallel_min_strategies_for_exit | 1 | D122 |
| gap_momentum_score_threshold | 1.0 | D126 |
| gap_momentum_min_gap | 0.08 | D126 |
| gap_momentum_min_rvol | 2.5 | D126 |
| gap_day_stop_cap | 0.35 | D124 |
| max_entry_spread_pct | 0.01 (0.03 for high MFCS) | D125 |

## Test Coverage
2095 tests total (as of D131):
- Unit tests: ~1900
- Property-based (Hypothesis): 26 (D122)
- D126 gap momentum: 16
- D124 ANNA postmortem: 12
- D123 derivative symbols: 10
- **mx-arena exchange simulator: 74** (D129-D131)

## mx-arena: Exchange Simulator & Optimization Engine (D129-D131)

Full Alpaca exchange simulator that runs unmodified production code against historical market data. Replaces Alpaca cloud with local matching engine for replay, parameter optimization, and scenario stress testing. See `docs/architecture/MX_ARENA.md` for full documentation.

**Key capabilities:**
- Matching engine: market/limit/stop/OTO/bracket orders with 10% partial fills
- REST API (14 endpoints) + WebSocket (JSON + msgpack)
- Historical replay at 180K+ ticks/sec
- Parameter sweep: 8 configs in 3.6s via multiprocessing
- Scenario generator: flash crash, trading halt, short squeeze
- Validated against Mar 26 journal data (7 BUY tickers, real 1-min bars)

**Bot redirection** (zero code changes):
```
ALPACA_BASE_URL=http://localhost:8080
ALPACA_DATA_URL=http://localhost:8080
ALPACA_WS_DATA_URL=ws://localhost:8082
ALPACA_WS_TRADE_URL=ws://localhost:8081
```

**First optimization finding** (stop_loss_pct sweep, Mar 26):
- 2% stop: -$0.96 (best) vs 15% stop: -$3.09 (worst)
- Tighter stops minimize total loss on momentum trades

## Known Issues / Future Work

1. **catalyst_type propagation (D111 bug)**: Positions always get catalyst_type="unknown", causing CatalystHalfLife strategy to use 20-min half-life. Fix: trace catalyst_type from news_agent through to position creation.

2. **PullbackClassifier excluded**: Fixed MIN_ADVANCE_PCT (1%) but kept on exclusion list pending live validation. Remove from exclusion after 2+ sessions of signal history data showing reasonable fire rate (<15%).

3. **Experiment deflation replay**: Fixed (D122) but ~3000 prior records are wasted. New deflation sweep data accumulating daily.

4. **Sequential evaluation bottleneck**: Stocks are evaluated one at a time through the LLM pipeline. On days with 8+ winners, by the time stock #3 is evaluated, stocks #1-2 have already moved. The fast-path partially addresses this but only for deterministic agents.

5. **mx-arena full bot wiring**: Run main.py end-to-end against arena HTTP server (currently validates journal BUY signals against bars, not full pipeline). Next: Optuna Bayesian optimization, Monte Carlo drawdown estimation, shadow trading validation.
