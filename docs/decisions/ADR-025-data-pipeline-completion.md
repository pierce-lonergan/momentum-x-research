# ADR-025: Foundation Fixes + Data Pipeline Completion (S027)

**Status**: Accepted
**Date**: 2026-02-08
**Session**: S027

## Context

Critical evaluation (S027) identified that all 6 agents were reasoning over **empty data**.
The orchestrator passed `news_items=[]`, `price_data={}`, `indicators={}`, `options_data={}`,
`sec_filings=[]` to every agent. This meant the highest-weighted agent (News, 30% MFCS)
was confabulating from pre-training knowledge, and the system had never been validated
against reality.

## Phase 1: Foundation Fixes

### D1: Data Completeness Guard
- **File**: `src/core/orchestrator.py`
- Added `_assess_data_completeness()` static method
- Logs per-agent data quality (COMPLETE/PARTIAL/EMPTY) before dispatch
- Stores report in `_last_data_report` for post-trade analysis
- Pushes fill rates to `MetricsRegistry.record_data_completeness()`

### D2: ADV Estimation Fix
- **Files**: `src/core/models.py`, `src/data/alpaca_client.py`, `src/scanners/premarket.py`, `src/core/scan_loop.py`
- Added `avg_daily_volume` field to `CandidateStock` (from Alpaca `prevDailyBar.v`)
- GEX normalization now uses real previous-day volume instead of `premarket_volume * 10`
- Fallback with explicit warning when no ADV data available

### D3: VWAP Proxy Fix
- **File**: `main.py` Phase 3
- Prefers real WebSocket VWAP from `VWAPAccumulator` when available
- Falls back to `prev_close` with explicit warning about false breakout risk

### D4: MyPy Enforcement
- **File**: `.github/workflows/ci.yml`
- Changed from `|| true` (silently ignored) to baseline ratchet at 92 errors
- CI fails if new type errors are introduced above baseline

### D5: Sector Taxonomy Upgrade
- **File**: `src/execution/portfolio_risk.py`
- 3-tier sector lookup: 100+ static tickers, runtime `_sector_cache`, heuristic `_infer_sector()` from company name keywords
- Handles small-cap momentum universe (biotech, cannabis, China-ADR, EV, crypto, SPAC)
- Unknown tickers default to "Other" (no free pass on concentration limits)

### D6: E2E Integration Test
- **File**: `tests/integration/test_e2e_paper_lifecycle.py` (NEW, 14 tests)
- Covers full cmd_paper lifecycle: scan, evaluate, execute, close
- Tests data completeness logging, circuit breaker, portfolio risk, metrics

## Phase 2: Data Pipeline Completion

### D7: NewsClient Wired (30% MFCS weight)
- **File**: `main.py`
- `NewsClient` instantiated with Alpaca + Finnhub API keys
- Per-ticker 24h lookback, max 10 items before evaluation
- Passed via `news_by_ticker` dict to `orchestrator.evaluate_candidates()`

### D8: Technical Indicator Calculator (20% MFCS weight)
- **File**: `src/data/technical_indicators.py` (NEW, 280 lines)
- Computes from 200 1-min Alpaca bars: RSI-14, MACD(12/26/9), Bollinger Bands(20, 2std), floor pivot S/R, VWAP, SMA-9/20, EMA-9/21, volume surge ratio
- `format_price_data()` packages as `{timeframe -> [{o,h,l,c,v}]}` for TechnicalAgent
- Auto-derives 5-min bars from 1-min via aggregation
- Returns empty dicts on insufficient data (never fabricates)
- **File**: `src/data/alpaca_client.py` — Added `get_bars()` method
- **File**: `src/core/orchestrator.py` — Now passes `market_data.get("price_data")` and `market_data.get("indicators")` to TechnicalAgent
- **Tests**: 35 new tests in `tests/unit/test_technical_indicators.py`

### D9: AlpacaOptionsProvider Wired for Live GEX
- **File**: `main.py`
- `GEXCalculator` + `AlpacaOptionsProvider` instantiated and passed to `ScanLoop`
- Live options chain feeds GEX filter during scanning

### D10: SECEdgarClient Wired for Dilution Detection
- **File**: `main.py`
- `SECEdgarClient` instantiated and passed to `Orchestrator(settings, sec_client=sec_client)`
- Auto-queries EDGAR for S-3/424B5 filings per candidate (90-day window)

### D11: Data Completeness Metrics Dashboard
- **File**: `src/monitoring/metrics.py`
- New counters: `data_complete_agents`, `data_partial_agents`, `data_empty_agents`
- `record_data_completeness()` method with per-agent fill rate tracking
- Prometheus export: `mx_agent_data_fill_rate{agent="news_agent"} 0.800`
- `snapshot()` includes `data_completeness` section with overall and per-agent rates

## Impact

| Agent | Before S027 | After S027 |
|-------|------------|------------|
| News (30% weight) | `news_items=[]` | Live Alpaca + Finnhub news |
| Technical (20%) | `price_data={}, indicators={}` | RSI, MACD, BB, S/R, VWAP from 200 bars |
| Fundamental (15%) | `recent_filings=[]` | Live SEC EDGAR dilution detection |
| Institutional (10%) | `options_data={}` | Live GEX from Alpaca options chain |
| Deep Search (5%) | `sec_filings=[]` | SEC filings from orchestrator |
| Risk | Had empty agent signals | Now has enriched agent signals |

Overall data completeness: **~20% to ~80%**.

## Remaining Gaps

- Dark pool data feed: no provider available
- Social sentiment: Reddit/StockTwits client not implemented
- MFCS weights: still initial guesses, need Shapley calibration (Phase 3)
- Historical scenario database needed for CPCV validation (Phase 3)

## Test Results

- 708 total tests (638 main suite + 35 technical indicators + 14 E2E + hypothesis/encoding deselected)
- 85% code coverage
- MyPy: 92 errors baselined (ratchet enforced in CI)
