# MOMENTUM-X System Architecture Master Document

**Date:** 2026-04-11 (revised 2026-04-11 post-D217/D218 commit)
**Branch:** develop @ 2787407
**Codebase:** 7,186-line main.py + 48,304 lines across 345 Python files (+ 2,320 lines added in D217/D218)
**Data footprint:** 31MB in data/

---

## Section 1: System Purpose and Strategy

### What it trades

Small-cap US equities gapping up 5%+ in pre-market on above-average volume (RVOL > 2x). Typical universe: $1.50-$50 stocks with float under 20M shares. Extended via D199 to include mid-cap catalyst plays ($1B+ market cap, 3%+ gap, 1.5x+ RVOL).

### Design thesis vs production reality

**Design thesis:** Gap-up momentum stocks with confirmed catalysts (FDA, earnings, contracts) trend for minutes to hours. Enter at market open on LLM-confirmed catalyst, exit via multi-strategy intelligence when momentum exhausts.

**Production reality (from worktree data, caveats below):** Median hold time was 150-220 minutes. The bar-1 exit (close at T+60s) shows PF=1.08 in arena but was never the production exit. Production uses exit intelligence: 6 parallel strategies (velocity, pullback, volume exhaustion, gratitude, catalyst half-life, alpha decay) plus trailing stops, early profit takes, and tranche exits.

### Live performance

**Honest assessment:** Unknown. The worktree discovery (D217) invalidated all prior live PF measurements. The code that ran in production for the last two weeks was a stale worktree copy at `<local-path> operator\.claude-worktrees\momentum-x\fervent-ellis\`, not the main repo. Reported "live PF=1.03 trimmed" was computed on worktree trades with contaminated short-book data (PF=0.11 over 25 short trades). Long-only PF from the worktree era is unknown because the journal didn't separate long/short P&L until D215.

The main repo code ran for the first time on 2026-04-10. It hung at ~8:05 AM ET due to a logging pipe deadlock (Section 8). No trades were executed. The measurement phase has not begun.

### Edge

The claimed edge is catalyst identification via LLM-scored news sentiment. Empirical evidence from D203: news agent BULL signal has 16.7% win rate (best predictor), while technical agent BULL has 0% win rate on 115 BUY trades. The MFCS weighting (news=0.55, technical=0.05, RVOL=0.25, float=0.15) reflects this.

---

## Section 2: End-to-End Data Flow

### Trade lifecycle (single trade, Phase 2 BUY path)

**T-5h: Phase 0 Pre-Market Research** (4:00-4:30 AM ET, main.py:1267)
1. Earnings calendar refresh via Finnhub (main.py:1274, gated by `settings.d216.finnhub_earnings_enabled`)
2. FinBERT model pre-load to thread pool (main.py:1289, ~1s if cached)
3. `premarket_research.run_full_prefetch()` (main.py:1306): fetches news for top 44 tickers via Alpaca+Finnhub, SEC filings via EDGAR, computes ATR baselines
4. SEC prefetcher batch-analyzes 30 tickers for dilution risk (main.py:1343)
5. **Output:** `premarket_cache` object consumed in Phase 1.5 and Phase 2

**T-4h to T-5min: Phase 1 Pre-Market Scanning** (4:30-9:25 AM ET, main.py:1359)
1. Every ~60s: `scan_loop.run_single_scan(quotes)` (main.py:1374)
2. Alpaca screener returns 50 most-active + 20 top gainers (main.py:1373)
3. EMC filter applies: gap >= 5%, price $1.50-$50, RVOL >= 2x, float <= 20M (src/scanners/premarket.py)
4. GEX enrichment adds gamma exposure data from options chain (src/scanners/gex.py)
5. **Output:** `watchlist` of 10-20 CandidateStock objects
6. WebSocket launched for real-time VWAP data (main.py:1397)

**T-5min: Phase 1.5 Fast-Path Scoring** (9:25 AM ET, main.py:1419)
1. Runs ONCE. Top 10 candidates scored with news+RVOL agents only (no technical/fundamental)
2. Produces `partial_mfcs` per candidate, cached in orchestrator (main.py:1477)
3. Creates `FastPathEntry` objects with pre-computed entry price, stop, targets
4. **Output:** `fast_path_queue` ready for immediate execution at 9:30

**T=0: Phase 2 Market Open** (9:30 AM ET, main.py:1499)
1. **Fast-path fire** (main.py:1610): queued orders submitted to Alpaca immediately
2. Parallel news+bars fetch for top 10 candidates (main.py:1893)
3. D216 earnings injection: if ticker has scheduled earnings, synthetic NewsItem appended (main.py:1910)
4. `orchestrator.evaluate_candidates(top_candidates)` (main.py:2227):
   - 6 agents dispatched in parallel with 150ms stagger (orchestrator.py:2041)
   - Phase A: 5 analytical agents, 18.75s timeout (orchestrator.py:2146)
   - Phase B: risk agent, sequential after Phase A (orchestrator.py:2257)
   - MFCS computed: weighted sum of agent signals minus risk penalty (orchestrator.py:2595)
   - Kelly tier assigned (1-4) based on MFCS, gap, RVOL, float (kelly_tier.py)
   - D216 dynamic threshold: if >30% agents got EMPTY data, threshold reduced (orchestrator.py:2620)
5. **Output:** list of TradeVerdict objects with action=BUY/HOLD/NO_TRADE

**T+0 to T+1s: BUY Execution** (main.py:3078)
1. 14 sequential gates checked (circuit breaker, max positions, fast-path dedup, duplicate position, stopped-out, recently-closed, portfolio risk, universe tier, catalyst confirmation, news confidence, faller risk, observation period, session regime)
2. `bridge.execute_verdict(verdict, scored=_scored)` (bridge.py:78):
   - Spread filter: reject if bid-ask > 1% (bridge.py:118)
   - AlpacaExecutor submits OTO bracket order: limit buy + child stop (alpaca_executor.py:231)
   - D217 fill confirmation: 3 polls at 2s intervals, reject if not confirmed (bridge.py:213)
   - ManagedPosition created with fill_price, stop_loss, targets, order IDs (bridge.py:275)
   - Position registered in PositionManager (bridge.py:362)
3. ExecutionRecorder records entry with all features (main.py:3105)
4. Trade journal records fill with slippage BPS (main.py:3134)
5. OTO stop canceled, replaced with standalone stop + tranche limit sells (main.py:3158-3280)
6. Stop registered with StopResubmitter for ratcheting (main.py:3288)
7. Session state persisted atomically (main.py:3306)
8. Bar-1 exit scheduled as asyncio task if enabled (main.py:3331)
9. Early profit taker registered (main.py:3428)

**T+1min to T+6h: Phase 3 Intraday Management** (10:00 AM-3:45 PM ET, main.py:3572)
Every ~60s cycle:
1. Broker position poll for stop-out detection (main.py:3890)
2. Tranche fill processing: WebSocket primary, poll fallback (main.py:3895-4016)
3. Bar-1 exit check (main.py:4053)
4. Phase transition stops: Phase 0 (30s ultra-tight) -> Phase 1 (7min, 1.5%) -> Phase 2 (ATR-based) (main.py:4155-4215)
5. Trailing stop ratchet (main.py:4507)
6. D78 smart exit evaluation: 6 parallel strategies produce composite exit urgency (main.py:5030)
7. If EXIT: `client.close_position()` -> `bridge.close_with_attribution()` -> journal record (main.py:5115)
8. VWAP breakout rescan for new candidates (main.py:5355)
9. Rescan for late momentum (main.py:5749)

**T+6h: Phase 4 Session Close** (4:00 PM ET, main.py:6091)
1. Cancel all open orders (main.py:6069)
2. Close all positions via `client.close_position()` with 30s timeout (main.py:6118)
3. Record each close via `bridge.close_with_attribution()` (main.py:6214)
4. Shapley attribution for agent contribution analysis (main.py:6249)
5. Session report generated (main.py:6314)
6. Bar recordings saved for arena replay (main.py:6361)
7. Session data collector saves minute bars (main.py:6377)
8. State reset, shutdown flag set (main.py:6400)

### Data transformations

```
Alpaca Screener -> CandidateStock (scan_loop.py)
CandidateStock + News + SEC + ATR -> PreMarketCache (premarket_research.py)
CandidateStock + 6 AgentSignals -> ScoredCandidate (orchestrator.py)
ScoredCandidate + KellyTier + Gates -> TradeVerdict (orchestrator.py)
TradeVerdict -> OrderResult (alpaca_executor.py)
OrderResult -> ManagedPosition (bridge.py)
ManagedPosition + ExitSignals -> EnrichedTradeResult (position_manager.py)
EnrichedTradeResult -> JournalEntry close fields (trade_journal.py)
```

---

## Section 3: Execution Path Inventory

### 7 Entry Paths

| # | Path | Phase | Type | Status | Trigger | Features Available | Known Issues |
|---|------|-------|------|--------|---------|-------------------|--------------|
| 1 | D207 Aggressive Short | 1.5 | SHORT | **DISABLED** | Scanner gap >30%, no catalyst | gap, rvol, float, market_cap | PF=0.11 over 25 trades. Whipsaw: system went long AND short same ticker same day. |
| 2 | D161 Faller Short | 2 | SHORT | **DISABLED** | Faller score >0.60 after full eval | All (post-eval) | Same short book disaster. |
| 3 | Phase 2 BUY | 2 | LONG | Active | Full 6-agent eval, MFCS >= 0.25, all gates pass | All features | Primary path. Best feature coverage. |
| 4 | D170 Observation | 3 | LONG | Active | Deferred Phase 2 BUY awaiting price confirmation | All (from scored.candidate) | Missing catalyst_type and direction in recorder. |
| 5 | VWAP Breakout | 3 | LONG | Active | Intraday VWAP break with volume confirmation | All except prior_gap_count (partial) | Runs same orchestrator pipeline as Phase 2. |
| 6 | Rescan | 3 | LONG | Active | Phase 3 periodic re-evaluation of new candidates | All except prior_gap_count (partial) | Same pipeline. |
| 7 | Fast-Path | 1.5 | LONG | Active | Pre-scored at 9:25, fires at 9:30:01 | gap, rvol, partial_mfcs, float, market_cap | Uses entry_price (dip-calculated) as fill_price. Actual fill arrives async. |

### Close Paths

| Path | Trigger | P&L Source | Recorder Wired |
|------|---------|------------|----------------|
| Stop-out (D98) | Broker position disappears | Estimated stop price or broker fill | YES |
| D78 Smart Exit | Composite exit urgency > 0.40 | close_position() fill or snapshot price | YES |
| D163 Trailing Stop | Price drops below ratcheted trail | Trail price | YES |
| D164 Early Profit Take | T+120s, position profitable | close_position() fill | YES |
| D146 Bar-1 Exit | T+60s, 100% close | close_position() fill | YES |
| Tranche Fill (T1/T2/T3) | Limit order fills at target | Limit fill price | YES |
| Phase 4 EOD | Market close at 4:00 PM | close_position() fill | YES |

---

## Section 4: Component Catalog

### Scanner Layer
| Component | File | Status | Dependencies | Dependents |
|-----------|------|--------|--------------|------------|
| ScanLoop | src/core/scan_loop.py | Active | AlpacaDataClient, ScannerThresholds | main.py Phase 1 |
| PremarketScanner | src/scanners/premarket.py | Active | ScannerThresholds | ScanLoop |
| GEXCalculator | src/scanners/gex.py | Active | OptionsProvider | ScanLoop |
| IntradayVWAPScanner | src/scanners/intraday_vwap.py | Active | WebSocket VWAP data | main.py Phase 3 |

### Agent Layer (6 active agents + 2 support)
| Component | File | Weight | Status | Known Issues |
|-----------|------|--------|--------|--------------|
| NewsAgent | src/agents/news_agent.py | 0.55 | Active | D216: 3-layer pipeline (headline filter -> FinBERT -> LLM). FinBERT runs via asyncio.to_thread (D217 fix). |
| TechnicalAgent | src/agents/technical_agent.py | 0.05 | Active | D203: 0% win rate on BULL signal. Weight minimized to near-zero. Anti-predictor. |
| FundamentalAgent | src/agents/fundamental_agent.py | 0.00* | Active | Weight is 0 via `institutional` field, but agent still runs (waste of LLM calls). |
| InstitutionalAgent | src/agents/institutional_agent.py | 0.00 | Active | Same: runs but weight=0. |
| DeepSearchAgent | src/agents/deep_search_agent.py | 0.00 | Active | Same. |
| RiskAgent | src/agents/risk_agent.py | 0.25 (lambda) | Active | Best agent (F1=0.667 per D168). Acts as penalty, not weight. |
| ManipulationClassifier | src/agents/manipulation_classifier.py | N/A | Active | Gates manipulation_phase field on position. |
| FinBERTScorer | src/agents/finbert_scorer.py | N/A | Active | Lazy-loaded singleton. Blocks on first load (~1s if cached). |

*Note: FundamentalAgent, InstitutionalAgent, and DeepSearchAgent have weight=0 in scoring but still consume LLM API calls. This is pure waste (~$0.30-2.00 per candidate x 3 agents x 10 candidates = $9-60/session in unnecessary API costs).*

### Orchestration Layer
| Component | File | Status | Key Methods |
|-----------|------|--------|-------------|
| Orchestrator | src/core/orchestrator.py | Active | evaluate_candidates(), _dispatch_agents(), _compute_mfcs() |
| KellyTierClassifier | src/core/kelly_tier.py | Active | classify() -> tier 1-4 |
| DebateEngine | src/agents/debate_engine.py | **DISABLED** | max_debate_attempts=0. Zero conversion rate. |
| EnsembleWrapper | src/agents/ensemble.py | Active | 3x parallel LLM calls per agent, quorum=2 |

### Execution Layer
| Component | File | Status |
|-----------|------|--------|
| ExecutionBridge | src/execution/bridge.py | Active |
| AlpacaExecutor | src/execution/alpaca_executor.py | Active |
| PositionManager | src/execution/position_manager.py | Active |
| StopResubmitter | src/execution/stop_resubmitter.py | Active |
| TrancheMonitor | src/execution/tranche_monitor.py | Active |
| FastPathEngine | src/execution/fast_path.py | Active |
| FallerRiskDetector | src/execution/faller_detection.py | Active |
| SessionRegimeDetector | src/execution/session_regime.py | Active |
| EntryDelayManager | src/execution/entry_delay.py | Active |
| TrailingStopManager | src/execution/trailing_stop.py | Active |
| EarlyProfitTaker | src/execution/early_profit_take.py | Active |
| PortfolioRisk | src/execution/portfolio_risk.py | Active |
| SessionStateManager | src/execution/session_state.py | Active |
| FillStreamBridge | src/execution/fill_stream_bridge.py | Active |

### Exit Intelligence Layer
| Component | File | Strategy |
|-----------|------|----------|
| ExitSignalEngine | src/execution/exit_intelligence.py | 13 micro-signals -> composite urgency |
| VelocityEngine | src/execution/exit_strategies.py | Price acceleration decay |
| PullbackClassifier | src/execution/exit_strategies.py | Pullback pattern detection |
| VolumeExhaustionStrategy | src/execution/exit_strategies.py | Volume fade below threshold |
| GratitudeExitStrategy | src/execution/exit_strategies.py | Profit-taking on gratitude window |
| CatalystHalfLifeStrategy | src/execution/exit_strategies.py | Catalyst type -> empirical decay curve |
| AlphaDecayOracle | src/execution/exit_strategies.py | Return deviation from null curve |
| ArchetypeExitStrategy | src/execution/archetype_exit.py | **DISABLED** (D214: PROMOTE=False) |

### Observability Layer
| Component | File | Status |
|-----------|------|--------|
| HeartbeatWatchdog | src/scheduling/heartbeat.py | Active (daemon thread, 30-min market timeout) |
| HealthControlServer | src/scheduling/health_server.py | Active (port configurable via SERVER_HEALTH_PORT, default 9091, SO_REUSEADDR) |
| TradingControlState | src/scheduling/health_server.py | Active (/pause, /resume, /shutdown, checked every loop iteration) |
| MetricsServer | src/monitoring/server.py | Active (port configurable via SERVER_METRICS_PORT, default 9090) |
| LiveDashboard | src/monitoring/live_dashboard.py | Active (interval configurable via OPS_DASHBOARD_INTERVAL_SECONDS, default 30s) |
| ExecutionRecorder | src/analysis/execution_recorder.py | Active (per-path P&L, FIFO close matching by timestamp) |
| **ExternalWatchdog** | **scripts/watchdog_monitor.ps1** | **Active (D217). Scheduled Task every 2 min during market hours. Checks heartbeat staleness (90s opening window, 2min market, 15min off-hours). Circuit breaker: stops after 3 restarts/hour. Captures py-spy dump before kill.** |
| **WatchdogInstaller** | **scripts/install_watchdog.ps1** | **Active (D217). Installs MomentumX-Watchdog as daily scheduled task with 2-min repetition.** |

### Logging Architecture (D217)

The April 9-10 production hangs were caused by a logging pipe deadlock: Python's `logging.emit()` wrote to stderr, piped through PowerShell `ForEach-Object`, pipe buffer filled, event loop blocked. The heartbeat watchdog also blocked trying to log. Fixed with two-layer architecture:

| Layer | Handler | Purpose | Blocking? |
|-------|---------|---------|-----------|
| Primary | `RotatingFileHandler` → `logs/momentum_YYYY-MM-DD.log` | Persistent audit trail | NO (writes directly to file) |
| Console | `QueueHandler` → bounded queue (1000) → `QueueListener` → stderr | Operator visibility | NO (drops on overflow) |

Log rotation: 50MB per file, 5 backups. Queue size configurable via OPS_LOG_QUEUE_SIZE (requires init reorder to extract — currently hardcoded).

### Analysis Layer
| Component | File | Status |
|-----------|------|--------|
| TradeJournal | src/analysis/trade_journal.py | Active (JSONL append) |
| SessionReportGenerator | src/analysis/session_report.py | Active (EOD JSON) |
| FeatureLogger | src/analysis/feature_logger.py | **FIXED (D218).** Now logs in both `_build_no_trade_verdict()` (rejections) and the BUY verdict path (line 1470). Both paths produce 32-field JSONL rows. Test-protected via `test_feature_logger_coverage.py`. Will produce output on first session with candidate evaluations. |
| SignalHistoryLogger | src/execution/exit_intelligence.py | **VERIFIED WORKING (D218).** Tested with real ExitSignal objects — produces 23-field JSONL rows. Only fires during Phase 3 when positions are open (by design). Will produce output on first session with held positions. Test-protected via `test_signal_history_logger.py`. |
| TradeResultTracker | src/execution/trade_result_tracker.py | **FIXED (D218).** Now records trade results even when Shapley attribution fails (enriched=None). Added `source` provenance field: `"shapley"` (full attribution) or `"basic"` (position-only P&L). Kelly tier system will have real data from first session. |
| PostTradeAnalyzer | src/analysis/post_trade.py | Active (Shapley attribution) |
| PhantomJournal | src/data/phantom_journal.py | Active (counterfactual gate tracking) |

---

## Section 5: Data Persistence Inventory

### Persistent Stores

| Store | Path Pattern | Format | Write Freq | Atomic | Schema Version | Failure Mode |
|-------|-------------|--------|------------|--------|---------------|--------------|
| Session State | data/session_state.json | JSON | Every state change | YES (.tmp+replace+.bak) | v1 (hardcoded) | Stale data on crash (recovered from Alpaca) |
| Trade Journal | data/journals/journal_YYYY-MM-DD_HHMMSS.jsonl | JSONL | Per evaluation | Line-append | None | Lost if buffer unflushed on SIGKILL |
| Trade Results | data/trade_results.jsonl | JSONL | Per close | Line-append | None | **FIXED (D218).** Records on every position close via both Shapley and basic paths. 9 fields including `source` provenance. |
| Feature Vectors | data/features/features_YYYY-MM-DD.jsonl | JSONL | Per eval (BUY + NO_TRADE) | Line-append | None | **FIXED (D218).** Logs in both `_build_no_trade_verdict()` and BUY path. 32 fields per row. |
| Signal History | data/signal_history/signal_log_YYYY-MM-DD.jsonl | JSONL | Per Phase 3 cycle per position | Line-append | None | **VERIFIED (D218).** Works correctly — 23 fields. Empty because no positions held yet from main repo code. |
| Session Reports | data/session_reports/session_*.json | JSON | End of session | NO | None | Corrupted if crash during write_text() |
| Heartbeat | data/heartbeat.json | JSON | Every loop iteration | YES (.tmp+replace) | None | Stale on hang (by design - watchdog checks) |
| Application Log | logs/momentum_YYYY-MM-DD.log | Text | Continuous | NO (RotatingFileHandler) | None | 50MB rotation, 5 backups |
| Agent Cache | data/agent_cache.json | JSON | Periodic | NO | None | LLM memoization - loss = re-fetch |
| Experiment Journal | data/experiments/experiment_journal_*.jsonl | JSONL | Per A/B test | Line-append | None | **NOT GENERATED** in recent sessions |
| Archetype Model | data/archetypes/archetype_model.json | JSON | Training only | N/A | None | D214 PROMOTE=False, read-only |
| Phantom Journal | Append-only file | JSONL | Per gated candidate | Line-append | None | Counterfactual tracking |

### In-Memory State (lost on restart)

| State | Component | Rebuilt From |
|-------|-----------|-------------|
| Open positions | PositionManager._positions | session_state.json + Alpaca API |
| Tranche tracking | TrancheMonitor._order_map | Rebuilt from WebSocket fills |
| Trailing stop state | TrailingStopManager._tracked | Rebuilt from position + market data |
| Session regime | SessionRegimeDetector._regime | Reset to NORMAL (session-scoped) |
| Exit signal history | AlphaHistoryEngine._history | Lost (rebuilt from price action) |
| Stop tracking | StopResubmitter._stops | Rebuilt from Alpaca API |
| Entry delay observations | EntryDelayManager._candidates | Lost (acceptable) |

---

## Section 6: External Dependencies and Integrations

### Alpaca Markets (PRIMARY BROKER)

| Endpoint | Purpose | Timeout | Rate Limit | Failure Handling |
|----------|---------|---------|------------|-----------------|
| GET /v1beta1/screener/stocks/most-actives | Universe discovery | 30s | 200 req/min | Fallback to curated universe |
| GET /v2/stocks/snapshots | Price/spread data | 30s | 9000 req/min | Circuit breaker after 3 failures |
| POST /v2/orders | Order submission | 30s | 200 req/min | Retry 3x with backoff on 429/5xx |
| GET /v2/positions | Position reconciliation | 30s | 200 req/min | Critical - no fallback |
| GET /v2/account | Equity for sizing | 30s | 200 req/min | Cached between calls |
| DELETE /v2/positions/{symbol} | Position close | 30s (D217: 15-30s explicit) | 200 req/min | Fallback to market sell |
| WSS trade updates | Fill notifications | Reconnect with 30s max backoff | N/A | Poll fallback in Phase 3 |
| WSS market data | VWAP computation | Reconnect with 30s max backoff | N/A | Snapshot REST fallback |

### Together AI (LLM PROVIDER)

| Model | Tier | Cost ($/M tokens) | Timeout | Purpose |
|-------|------|-------------------|---------|---------|
| Qwen/Qwen3.5-397B-A17B | 1 (via .env) | ~$2.00 in/out | 25s | News agent, risk agent |
| Qwen/Qwen3-235B-A22B | 2 (via .env) | ~$1.50 in/out | 15s | Technical, fundamental, institutional |
| Qwen/Qwen2.5-7B-Instruct-Turbo | Fallback | ~$0.30 | 15s | Fast fallback |
| meta-llama/Llama-3.3-70B | Emergency | ~$0.88 | 15s | Last resort |

With ensemble (3x calls per agent, 6 agents per candidate, 10 candidates): up to 180 LLM calls per evaluation cycle. At ~$0.01 per call average, ~$1.80 per cycle. 3 agents have weight=0 but still run: ~$0.54/cycle wasted.

### Finnhub (NEWS + EARNINGS)

| Endpoint | Purpose | Timeout | Rate | Failure |
|----------|---------|---------|------|---------|
| /api/v1/company-news | Ticker-specific news | 15s | 60 req/min | Degrade to Alpaca news only |
| /api/v1/calendar/earnings | Earnings schedule | 10s | 60 req/min | No earnings injection |
| /api/v1/stock/profile2 | Sector/industry | 5s | 60 req/min | None defaults |

### SEC EDGAR

| Endpoint | Purpose | Timeout | Rate | Failure |
|----------|---------|---------|------|---------|
| efts.sec.gov/LATEST/search-index | Dilution detection (S-3, 424B5) | 30s | 8 req/sec | Returns CLEAN risk (no block) |

---

## Section 7: Observability Surface

### Currently Measurable

| Metric | Source | Refresh | Accessible |
|--------|--------|---------|------------|
| Process liveness | data/heartbeat.json | Every loop iteration (~60s) | Watchdog script |
| System health | HTTP :9091/health | Real-time | External tools |
| System status | HTTP :9091/status | Real-time | External tools (auth required) |
| Prometheus metrics | HTTP :9090/metrics | Real-time | Grafana (if configured) |
| Per-path P&L | ExecutionRecorder dashboard | Every 50 cycles | Log output |
| Session summary | data/session_reports/ | End of session | Post-session scripts |
| Trade audit trail | data/journals/ | Per evaluation | Post-session analysis |
| Version telemetry | First log line + heartbeat | Once per startup + per pulse | Log + heartbeat |

### NOT Currently Measurable (GAPS)

| Gap | Impact | Severity | Status |
|-----|--------|----------|--------|
| **Feature vectors for ML training** | ~~FeatureLogger instantiated but empty.~~ | ~~HIGH~~ | **CLOSED (D218).** FeatureLogger now logs in both BUY and NO_TRADE paths. 32-field JSONL rows. Test-protected. Awaiting first session with evaluations. |
| **Exit signal history** | ~~SignalHistoryLogger instantiated but empty.~~ | ~~HIGH~~ | **CLOSED (D218).** Verified working with real ExitSignal objects (23 fields). Awaiting first session with held positions. |
| **Trade result history** | ~~data/trade_results.jsonl does not exist.~~ | ~~BLOCKER~~ | **CLOSED (D218).** TradeResultTracker records on every close via both Shapley and basic paths. `source` provenance field added. |
| **Per-trade slippage distribution** | Journal has slippage_bps field but no aggregation or alerting. | MEDIUM | OPEN |
| **LLM call cost tracking** | No per-session or per-candidate cost accounting. | MEDIUM | OPEN |
| **Agent accuracy vs outcome** | PostTradeAnalyzer exists but Shapley runs only at EOD close. No real-time feedback. | MEDIUM | OPEN |
| **FinBERT latency per candidate** | No timing around asyncio.to_thread calls. Cannot measure event loop impact. | LOW | OPEN |
| **Watchdog effectiveness** | ~~Watchdog never fired in production (trigger bug, now fixed).~~ | ~~HIGH~~ | **PARTIALLY CLOSED (D217).** Watchdog installed as daily scheduled task with 2-min repetition. Trigger bug fixed (changed from -Once to -Daily). Circuit breaker tested. py-spy integration tested. Has NOT yet been validated against a real production hang with the new logging architecture. |

---

## Section 8: Architectural Weaknesses and Gaps

### BLOCKER

**W-1: main.py is 7,186 lines.** All 4 phases, all 7 entry paths, all close paths, all gates, all exit strategies, all state management, and all observability are in one file. Adding any feature requires understanding the entire file. A single misplaced `continue` or `break` can skip critical logic for an entire phase. The function `cmd_paper()` alone spans ~6,000 lines. This is not sustainable. (main.py:400-6400)

**~~W-2: trade_results.jsonl does not exist.~~** *(2026-04-11: CLOSED by D218. Downgraded from BLOCKER to RESOLVED.)* TradeResultTracker now records on every position close via both the Shapley path (full attribution) and basic fallback path (position-only P&L when ScoredCandidate not cached). Added `source` provenance field ("shapley" or "basic") for downstream quality assessment. Kelly tier system will have real data from the first trading session. Verified by unit test. (src/execution/bridge.py:443-517, src/execution/trade_result_tracker.py:22-49)

**W-3: ~~Three~~ Two zero-weight agents had ensemble wrappers.** *(2026-04-11: Downgraded from BLOCKER to LOW. See d218_efficiency_sweep.md and d218_premise_verification.md.)* Only InstitutionalAgent and DeepSearchAgent have weight=0 — FundamentalAgent maps to float_structure weight=0.15 and actively contributes. The dispatch skip at orchestrator.py:2118-2124 already prevented zero-weight agents from making LLM calls. D218 additionally removed unnecessary EnsembleWrapper creation for those agents and added startup visibility logging. Net LLM cost impact: $0/month (skip was already working).

### HIGH

**W-4: No schema versioning on any data file.** Session state has `"version": 1` but no migration code. Journal entries have no version field. If the schema changes (fields added/removed/renamed), old data silently becomes incompatible. No ETL pipeline handles schema evolution. (All persistence files)

**~~W-5: Session report writes are not atomic.~~** *(2026-04-11: RESOLVED by D218 Item 4.)* Session report now uses .tmp + os.replace() + .bak pattern matching session_state.py. 5 unit tests verify atomicity: serialization error leaves no file, replace error leaves previous intact, backup created, backup failure non-fatal. (src/analysis/session_report.py:403-448)

**~~W-6: FeatureLogger and SignalHistoryLogger are broken.~~** *(2026-04-11: CLOSED by D218. Downgraded from HIGH to RESOLVED.)*
- **FeatureLogger:** Fixed by adding `log_evaluation()` call inside `_build_no_trade_verdict()` (orchestrator.py:2939). Both BUY and NO_TRADE paths now produce 32-field JSONL rows. Test-protected by `test_feature_logger_coverage.py`.
- **SignalHistoryLogger:** Verified working with real ExitSignal objects (23 fields). Empty because no positions held from main repo code — will populate on first session with trades.
- **TradeResultTracker:** Fixed by adding fallback recording path when Shapley attribution fails. Both paths now call `record()`. Added `source` provenance field.

**W-7: Logging deadlock was architectural, not incidental.** *(2026-04-11: Root cause FIXED by D217. Residual risk remains LOW.)* The D217 logging pipe deadlock (Python stderr -> PowerShell pipe -> full buffer -> blocked event loop) was the root cause of ALL production hangs (April 9 and April 10). Confirmed via py-spy dump showing MainThread blocked at `logging/__init__.py:1154 (emit)` and heartbeat watchdog blocked at same layer. Fixed with RotatingFileHandler (primary, non-blocking file writes) + QueueHandler (console, bounded queue with background drain). Stress tested at 21,000 msg/sec with zero event loop stalls. **Residual risk:** file I/O in trade_journal.py, feature_logger.py, phantom_journal.py still use synchronous `open().write()` — could block on full disk, but at much lower volume than logging (tens of writes vs thousands).

**W-8: No cross-session reconciliation.** The system reconciles with Alpaca on startup (positions, orders) but never reconciles its own JSONL journals against broker records. The reconcile_journal.py script exists but is never run automatically. Journal and broker can drift without detection. (scripts/reconcile_journal.py)

**W-9: Stopped-out ticker tracking has a race window.** Tickers are added to `_stopped_out_tickers` when Phase 3 detects the stop fill, but a new OTO entry can be submitted in the ~60s gap between Phase 3 cycles. The D94b postmortem documented this. The recommended fix (add at stop ORDER SUBMISSION time) has not been implemented. (main.py:3964-3970, docs/d215_short_book_postmortem.md)

**W-10: Position deduplication is last-write-wins.** `PositionManager.add_position()` uses `self._positions[ticker] = position` with no atomic check-and-set. If two execution paths reach `add_position()` for the same ticker (race condition), the second overwrites the first, orphaning a broker position. (src/execution/position_manager.py:531)

### MEDIUM

**W-11: Phase 0 PreMarketCache failure is silent.** `premarket_research.run_full_prefetch()` raised `'PreMarketCache' object has no attribute 'tickers'` on April 10 (logs/paper_2026-04-10.log:04:30:35). The system continued without pre-cached news/SEC data. Phase 2 candidates entered evaluation without SEC dilution checks.

**W-12: 305 configurable parameters with no parameter audit trail.** Config changes happen via .env overrides. No log records which parameters differ from defaults. A stale .env (the secrets file sync issue caught in D217) silently reverts parameters without any alert.

**W-13: Partial fills at EOD are not handled.** If Phase 4 close_position() partially fills (e.g., 30/100 shares), the system records P&L as if full close occurred and removes the position from tracking. Remaining shares become ghost positions with no stop protection overnight. (main.py:6118-6140)

**W-14: Exit strategies run sequentially with no exception isolation.** All 6 exit strategies in ParallelExitEngine.evaluate_all() run in sequence. If one throws, subsequent strategies are skipped. No timeout per strategy. (src/execution/exit_intelligence.py:1049-1077) *(D217 added position existence check before D78 smart exit close — prevents closing already-closed positions. Debate engine gather timeout now configurable via settings.ops.debate_gather_timeout_seconds.)*

**W-15: Heartbeat watchdog also blocked on logging deadlock.** The watchdog daemon thread tried to log a warning when it detected a stale heartbeat, but the logging pipe was full. The watchdog that was supposed to detect the hang was itself hung on the same root cause. This is now mitigated by QueueHandler, but the pattern (watchdog depends on same infrastructure it monitors) remains architecturally fragile. (py-spy dump April 10: Thread 26816, heartbeat.py:229)

### LOW

**W-16: GEX filter passes 100% of candidates.** All session reports show `gex_rejection_rate_pct: 0%`. The GEX enrichment adds data but never actually filters. Dead code in the hot path. (session reports)

**W-17: Debate engine is disabled but code is loaded.** `max_debate_attempts=0` means no debate ever runs, but the DebateEngine class is still imported and instantiated. (config/settings.py:1233)

**W-18: Dead code: ~35 Python files in src/ are never imported.** Includes arena analysis tools, backtesting frameworks, and superseded implementations. ~26% of the codebase is unused. (src/arena/, src/core/backtester.py, etc.)

---

## Section 9: Data Model as It Exists Today

### Core Domain Objects

**CandidateStock** (src/core/models.py:116, frozen dataclass)
Scanner output. 25 fields including ticker, gap_pct, rvol, float_shares, market_cap, gex fields, D212 enrichment (sector, industry, prior_gap_count, is_day2_runner). Source of truth: Alpaca screener + scanner filter. No persistence - lives in memory during scan cycle.

**AgentSignal** (src/core/models.py:197, frozen dataclass)
Agent evaluation result. 13 fields. Subclassed into NewsSignal (adds catalyst_type, sentiment), TechnicalSignal (adds patterns, breakout), RiskSignal (adds risk_verdict, veto_reason), ManipulationSignal (adds manipulation_probability, phase). No direct persistence - embedded in journal entries.

**ScoredCandidate** (src/core/models.py:343, frozen dataclass)
Candidate + MFCS score. 6 fields wrapping CandidateStock + agent signals + composite score. Cached in orchestrator._scored_by_ticker for Shapley attribution on close. No direct persistence.

**TradeVerdict** (src/core/models.py:464, frozen dataclass)
Final trading decision. 20 fields including action, MFCS, entry_price, stop_loss, targets, kelly_tier, direction. Consumed by bridge.execute_verdict(). Serialized into journal entry verdict fields.

**ManagedPosition** (src/execution/position_manager.py:36, mutable dataclass)
Active position tracking. 30+ fields. Mutable (stop_loss, remaining_qty, peak_price updated during hold). Persisted to session_state.json on every change. Source of truth for current position state.

**FastPathEntry** (src/execution/fast_path.py:79, mutable dataclass)
Pre-computed entry for market open. 13 fields including partial_mfcs, dip_factor, status lifecycle. Lives in memory as fast_path_queue. Not directly persisted.

**OrderResult** (src/execution/alpaca_executor.py:36, mutable dataclass)
Broker order response. 13 fields. fill_price updated by D217 polling. Consumed by bridge to create ManagedPosition. Not directly persisted.

**JournalEntry** (src/analysis/trade_journal.py:122, mutable dataclass)
Full trade record. 58+ fields spanning: identity (5), candidate data (10), input provenance (15), agent signals (list), data quality (list), MFCS scoring (8), debate (optional), verdict (10), execution (12), position outcome (10), transaction costs (5), faller gate (3), direction (2). Serialized to JSONL. **This is the richest data object in the system but has no schema version and no migration path.**

**SessionReport** (src/analysis/session_report.py:50, dataclass)
End-of-day summary. 35+ fields. Written once at Phase 4. Not atomic.

**NewsItem** (src/data/news_client.py:42, frozen dataclass)
Normalized news item. 8 fields. Immutable. Consumed by news agent and sentiment tracker.

### D218 Schema Additions

**TradeResult** (src/execution/trade_result_tracker.py:22): Added `source: str` field with default `"shapley"`. Values: `"shapley"` (full Shapley attribution with agent contribution analysis) or `"basic"` (position-only P&L when ScoredCandidate not cached). `from_dict()` handles backward compatibility for records without this field.

### Schema Versioning Status

**None.** Zero data files have schema version fields (except session_state.json's `"version": 1` which has no migration code). All schema evolution is implicit - new fields appear as code changes, old data silently lacks them. The journal entry schema has grown from ~20 fields (D160) to 58+ fields (D216) with no versioning or migration. The D218 `source` field on TradeResult demonstrates the pattern: new field added with backward-compatible default, no migration of existing records.

---

## Section 10: The Database Rebuild Mandate

### Queries the database must answer fast

1. **Trade reconstruction:** Given a ticker and date, reconstruct the full lifecycle: scan -> eval -> agents -> score -> verdict -> order -> fills -> position management -> exit -> P&L. Currently requires joining journal JSONL + session state + Alpaca order history manually.

2. **Per-path P&L:** For each of the 7 execution paths, compute cumulative P&L, win rate, average hold time, average slippage. Currently tracked in-memory by ExecutionRecorder, lost on restart.

3. **Winner profiling:** What distinguishes winning trades from losing trades? Requires: all agent signals, MFCS components, gap/RVOL/float at entry, exit strategy that triggered, hold duration, slippage. Currently requires parsing journal JSONL and correlating with session reports.

4. **Slippage analysis:** Per-path, per-tier, per-time-of-day slippage distribution. Currently: journal has slippage_bps but no aggregation.

5. **Drawdown attribution:** When the system loses money, which path/agent/gate failed? Requires joining entry features with exit outcomes across all trades.

6. **Arena-to-production reconciliation:** Compare arena backtests with live results on same tickers/dates. Currently impossible - arena and production use different data stores with no common key.

7. **Compliance audit trail:** Regulators need: every order with timestamp, every fill, every position change, every cancel. Must be append-only and tamper-evident.

### Guarantees the database must provide

1. **Atomic trade records:** A trade open and its corresponding journal entry must be written in one transaction. No more "journal says position exists but state file disagrees."

2. **Hash-chained audit trail:** Every order, fill, and position change gets an append-only row with a hash of the previous row. Makes retroactive modification detectable.

3. **Schema versioning:** Every table has a version column. Migration scripts handle forward compatibility. Old data is never silently incompatible.

4. **Transactional fill recording:** Ghost positions become structurally impossible. The database enforces: order_submitted -> fill_confirmed -> position_created as an atomic sequence. No position can exist without a confirmed fill.

5. **Referential integrity:** orders.order_id -> fills.order_id -> positions.entry_order_id -> journal.trade_id. No orphaned records.

### What must be preserved

Every field currently in JournalEntry (58+ fields), ManagedPosition (30+ fields), SessionReport (35+ fields). Every implicit relationship currently encoded as matching ticker+timestamp across different files.

### What must be added

1. **Versioned schemas** with migration support
2. **Timestamps on every row** (created_at, updated_at)
3. **Append-only audit layer** for compliance
4. **Tier-0 indexes** on ticker+timestamp+path for fast queries
5. **Fact/dimension separation:** dimension tables (tickers, agents, paths, catalysts) and fact tables (evaluations, orders, fills, positions, signals)
6. **LLM cost tracking** per call, per candidate, per session
7. **Feature vector storage** (currently broken - FeatureLogger empty)
8. **Exit signal traces** (currently broken - SignalHistoryLogger empty)

### Architectural style recommendation

**SQLite for hot write path + DuckDB for analytics.** Rationale:

- SQLite: Zero-ops, single-file, ACID, supports concurrent reads with WAL mode. Perfect for the single-process trading system writing fills, positions, and journal entries.
- DuckDB: Column-oriented analytical queries (winner profiling, P&L aggregation, slippage distributions) are 10-100x faster than SQLite for scan-heavy workloads. Read-only from a copy of the SQLite file.
- Not Postgres: Adds operational complexity (separate server, auth, networking) without corresponding benefit for a single-machine paper trading system. If the system goes multi-machine or needs concurrent write access, Postgres becomes the right choice.

### Migration path

1. **Dual-write period (2 weeks):** New database receives all writes. JSONL files continue as backup. Reads come from JSONL (trusted). Nightly reconciliation script compares DB vs JSONL row counts and checksums.
2. **Backfill:** Parse all existing JSONL journals (14 files, 12MB) into the new schema. Map implicit relationships (matching ticker+timestamp) to explicit foreign keys.
3. **Cutover criteria:** DB and JSONL agree on every trade for 5 consecutive sessions. No orphaned records in DB. All queries in "must answer fast" list run in <100ms.
4. **Rollback plan:** JSONL files are never deleted. If DB corruption detected, revert reads to JSONL, fix DB, re-backfill.

---

## The Three Things That Worry Me Most

**First: main.py at 7,186 lines is a structural single point of failure.** Every bug fix, every feature, every audit touches this one file. The D217 bug sweep fixed 19 bugs in it. The next sweep will find more. The logging deadlock lived in this file for months because nobody could hold the entire control flow in their head. Until main.py is decomposed into phase-specific modules with clear interfaces, the system is one misplaced `continue` away from silent trading failure.

**Second: the system has never been measured running the code it was built on.** The worktree ran stale code for two weeks. The main repo's first run hung on a logging deadlock. As of this writing, zero trades have been executed by the code in the develop branch. Every performance number, every Kelly calibration, every PF measurement in the project's history was computed on code that no longer exists in the execution path. The measurement phase has not started - it begins when the first clean session produces trades, and that has not happened yet.

**Third: ~~three of five analytical agents cost money but contribute nothing.~~** *(2026-04-11: Corrected. See d218_premise_verification.md.)* Only 2 agents (InstitutionalAgent, DeepSearchAgent) have weight=0, and the dispatch skip at orchestrator.py:2118 already prevents them from making LLM calls. FundamentalAgent has weight=0.15 and contributes. The claimed $360/month waste does not exist. This concern is downgraded to LOW — the remaining improvement (D218) was removing unnecessary EnsembleWrapper object creation for skipped agents and adding startup visibility logs.

---

## Section 11: Structural Debt Quantification

### 11.1 main.py Internal Anatomy

`cmd_paper()` is a single async function spanning ~6,127 lines. It contains exactly 1 nested function (`_d217_update_heartbeat`) and 0 class definitions. All phase logic, all 16 entry gates, all 7 close paths, all state management, and all observability are inline.

**Phase size distribution:**

| Phase | Lines | % of cmd_paper | Complexity |
|-------|-------|---------------|------------|
| Phase 0 (pre-market research) | 91 | 1.5% | Low — runs once |
| Phase 1 (scanning) | 54 | 0.9% | Low — scan loop call |
| Phase 1.5 (fast-path scoring) | 78 | 1.3% | Medium — scoring + queue |
| Phase 2 (market open) | 2,058 | 33.6% | **Extreme** — 16 gates + eval + execution + stops + tranches + journal |
| Phase 3 (intraday) | 2,520 | 41.1% | **Extreme** — stops, exits, trailing, VWAP, rescan, tranche fills |
| Phase 4 (session close) | 307 | 5.0% | Medium — close + report |
| Initialization + shutdown | 1,019 | 16.6% | Medium |

Phase 2 and Phase 3 together are 4,578 lines (74.7% of the function). Each is independently un-auditable — a reviewer must hold ~2,500 lines of control flow in their head to trace a single code path.

### 11.2 D-Prefix Variable Accretion

22 distinct D-prefixed variable namespaces exist in main.py (e.g., `_d217_phase`, `_d216_report`, `_d165_pos`, `_d164_pos`, `_d107_watchdog`). Each represents a bug fix or feature that was patched inline without refactoring. The naming convention serves as archaeology — it tells you WHEN code was added but makes the code unreadable to anyone who doesn't know the D-series history. A variable named `_d94b_recently_closed_guard` communicates its provenance but not its purpose. Consolidation into named abstractions (e.g., `entry_guard.is_recently_closed(ticker)`) would make the code self-documenting.

### 11.3 Exception Handler Census

**192 exception handlers in main.py.** Breakdown:

| Category | Count | % | Risk |
|----------|-------|---|------|
| Logs at ERROR/CRITICAL | 48 | 25% | Visible — good |
| Logs at WARNING | 78 | 41% | Visible — acceptable |
| Logs at DEBUG | 43 | 22% | **Invisible in production** (INFO default) |
| Silent `pass` | 42 | 22% | **Data loss risk** |

The 42 silent handlers are concentrated in: premarket velocity recording (line 1394), faller journal entries (line 2046), state persistence (lines 1313, 1319), VWAP gate initialization (line 1218), and various metric updates. Each represents a place where the system can silently lose data or skip a step without any operator-visible evidence.

**Recommendation:** Convert all 42 silent handlers to at minimum `logger.debug()`. Convert the 15 most critical (state persistence, journal recording, gate logic) to `logger.warning()`.

### 11.4 Duplicate Code Blocks

4 major code patterns are duplicated across execution paths:

| Pattern | Occurrences | Lines Each | Total Excess |
|---------|-------------|------------|-------------|
| Stop conversion (OTO -> standalone) | 2 (Phase 2, Fast-Path) | 66-87 | ~66 |
| Tranche submission | 4 (Phase 2, Fast-Path, VWAP, Rescan) | 19-20 | ~57 |
| State persistence (update + save) | 3 (Phase 2, Phase 3 WS, Phase 3 poll) | 15-34 | ~32 |
| Journal entry creation | 2 (Phase 2, Fast-Path) | 18-39 | ~18 |

~173 lines of pure duplication. Each requires synchronized changes — the D217 bug sweep had to modify the same pattern in 2-4 locations per fix.

**Recommendation:** Extract into utility functions: `async def convert_oto_to_standalone(client, position, stop_resubmitter)`, `async def submit_tranches(client, position, targets, tranche_monitor)`, `def persist_position_state(state_mgr, position, daily_pnl)`.

---

## Section 12: Code Quality and Efficiency Critique

### 12.1 Unnecessary Work in the Hot Path

**Imports inside loops.** Lines 1918, 3088, 3158 and others contain `from src.data.news_client import NewsItem` and similar imports INSIDE the Phase 2/3 loop bodies. Python caches module imports after first load, so this isn't a performance issue, but it's a readability anti-pattern — it obscures module dependencies and makes refactoring harder.

**Repeated snapshot dict access.** Phase 3 position management fetches snapshots once (line 3890) then accesses individual fields via `.get()` chains 8+ times per position: `snap.get("latestTrade", {}).get("p", 0)`. Extracting once into local variables (e.g., `last_price = float(snap.get("latestTrade", {}).get("p", 0) or 0)`) would reduce cognitive load and prevent inconsistent fallback defaults.

**Screener called every 60s during pre-market.** `get_most_active_tickers()` and `get_top_movers()` run every scan cycle (lines 254-260). Pre-market movers don't change meaningfully in 60 seconds. Caching for 5 minutes would reduce Alpaca API usage by 80% during the 5-hour pre-market window without losing meaningful signal.

### 12.2 Async Anti-Patterns

**Fire-and-forget tasks.** `asyncio.create_task()` is used at lines 927, 1075, 3422, 3554 to launch background tasks (fill stream, dashboard, bar-1 exit, early profit) that are never gathered or monitored. If any task throws an unhandled exception, the exception is silently lost and the subsystem stops functioning. The fill stream task (line 927) is especially critical — if it dies, tranche fill notifications stop arriving and stop ratcheting breaks.

**Recommendation:** Track all background tasks in a registry. Add a periodic health check (every 60s) that verifies each task is still alive: `for name, task in _background_tasks.items(): if task.done() and task.exception(): logger.critical("Background task %s died: %s", name, task.exception())`.

**Sequential awaits that could be gathered.** Phase 2 news fetch (lines 1850-1893) creates individual fetch tasks per ticker then gathers them. Good. ~~Phase 3 snapshot fetches (lines 4300-4350) fetch positions sequentially~~ *(2026-04-11: D218 premise verification found this claim FALSE — Phase 3 already uses batch `get_snapshots()` calls at lines 3670, 4335, 4347, 4871. All pass ticker lists.)*

### 12.3 Memory Management

**_stopped_out_tickers grows without bound.** The set accumulates every ticker that triggers a stop during the session. This is by design (prevents re-entry) and the growth is bounded by the number of unique tickers traded in a session (typically <50). Acceptable.

**orchestrator._scored_by_ticker cleared per batch** (line 1543). Correct. But if an exception occurs between batch start and the clear call, entries from the previous batch leak. This is a minor leak (tens of KB) that would only matter in an infinite-loop crash scenario.

**TradeJournal entries accumulate in memory** via `self._entries: dict[str, JournalEntry]`. Each entry is ~2KB. At 200 evaluations/day, this is ~400KB. The dict is never trimmed during the session. Acceptable for current scale but would need eviction at >1000 evaluations/day.

### 12.4 Hardcoded Values ~~That Should Be Config~~ — EXTRACTED (D218)

*(2026-04-11: 9 of 11 values extracted to config by D218 Item 3. See d218_efficiency_sweep.md.)*

| Value | Config Path (D218) | Default | Env Override | Status |
|-------|-------------------|---------|-------------|--------|
| Dashboard update interval | `settings.ops.dashboard_interval_seconds` | 30 | OPS_DASHBOARD_INTERVAL_SECONDS | **DONE** |
| Health server port | `settings.server.health_port` | 9091 | SERVER_HEALTH_PORT | **DONE** |
| Metrics server port | `settings.server.metrics_port` | 9090 | SERVER_METRICS_PORT | **DONE** |
| Portfolio max sector | `settings.ops.max_sector_positions` | 4 | OPS_MAX_SECTOR_POSITIONS | **DONE** |
| Portfolio max heat | `settings.ops.max_portfolio_heat_pct` | 40.0 | OPS_MAX_PORTFOLIO_HEAT_PCT | **DONE** |
| Eval batch timeout | `settings.ops.eval_batch_timeout_seconds` | 120.0 | OPS_EVAL_BATCH_TIMEOUT_SECONDS | **DONE** |
| Debate gather timeout | `settings.ops.debate_gather_timeout_seconds` | 60.0 | OPS_DEBATE_GATHER_TIMEOUT_SECONDS | **DONE** |
| Smart exit close timeout | `settings.ops.smart_exit_close_timeout_seconds` | 15.0 | OPS_SMART_EXIT_CLOSE_TIMEOUT_SECONDS | **DONE** |
| Phase 4 close timeout | `settings.ops.eod_close_timeout_seconds` | 30.0 | OPS_EOD_CLOSE_TIMEOUT_SECONDS | **DONE** |
| Phase 3 re-scan frequency | — | varies | — | DEFERRED (not a simple constant) |
| Heartbeat queue size | — | 1000 | — | DEFERRED (setup_logging runs before Settings load) |

---

## Section 13: Fault Tolerance Assessment

### 13.1 Graceful Degradation Matrix

What happens when each external dependency is completely unavailable for 30 minutes:

| Dependency | Scanning | New Entries | Exit Management | Position Safety | Recovery |
|------------|----------|------------|----------------|----------------|---------|
| **Alpaca API** | Fails (no quotes) | Blocked | **PARTIAL FAILURE**: can't fetch snapshots for trailing stops, can't submit close orders | **AT RISK**: existing stops at broker still active, but can't ratchet | Auto-recovers when API returns |
| **Alpaca WebSocket** | Unaffected | Unaffected | Tranche fills undetected (poll fallback at 60s) | Stop orders still active at broker | Reconnects with 30s max backoff |
| **Together AI (LLM)** | Unaffected | Degraded (3-tier fallback -> NEUTRAL signals) | Unaffected (exit uses deterministic signals) | Unaffected | Agents return NEUTRAL with flags |
| **Finnhub** | Unaffected | Degraded (no earnings injection, no sector data) | Unaffected | Unaffected | Graceful None defaults |
| **SEC EDGAR** | Unaffected | Degraded (no dilution detection) | Unaffected | Unaffected | Returns CLEAN risk |

**Critical gap: No SAFE_MODE.** When Alpaca API is down, the system should explicitly enter a mode where it only monitors existing positions (via cached state) and blocks all new entries. Currently, it continues attempting to scan and evaluate, hitting the circuit breaker repeatedly, without any operator-visible indication that the system has effectively halted.

**Recommendation:** Add a `SystemMode` enum: `NORMAL`, `SAFE_MODE` (monitor only), `HALTED` (everything stopped). Set SAFE_MODE when Alpaca circuit breaker opens. Log the transition at CRITICAL level. Include mode in heartbeat file.

### 13.2 Crash Recovery Gaps

State that survives a crash and restart mid-session:

| State | Persisted? | Rebuilt On Restart? | Gap |
|-------|-----------|--------------------|----|
| Open positions (qty, entry, stops) | YES (session_state.json) | YES (merged with Alpaca) | None |
| Stopped-out tickers | YES (session_state.json) | YES | None |
| Tranche order IDs | ~~**NO**~~ **YES** | ~~**NO**~~ **YES** | ~~Orphaned limit orders~~ *(2026-04-11: ALREADY IMPLEMENTED. session_state.py:55, main.py:756. Verified by test_recovery.py.)* |
| Trailing stop peak_price | YES (in position state) | Partially | Last stop submission time/price lost |
| Session regime (NORMAL/DEFENSIVE) | **NO** | Reset to NORMAL | Risk posture reverts without warning |
| Exit signal history | **NO** | Lost | Alpha decay curves restart from zero |
| Early profit take tracking | **NO** | Lost | May re-trigger at wrong time |

**~~Highest-impact gap: Tranche order IDs not persisted.~~** *(2026-04-11: ALREADY IMPLEMENTED. D218 premise verification found this claim FALSE. `PositionState.tranche_order_ids` exists at session_state.py:55 with `field(default_factory=list)`. Startup reconciliation at main.py:756-764 iterates the IDs and calls `tranche_monitor.register_tranche_order()` for each. Tested in test_recovery.py:62. The audit missed this because it checked ManagedPosition in position_manager.py, not PositionState in session_state.py — the field lives in the persistence layer, not the runtime dataclass.)*

**Remaining highest-impact gap:** Session regime not persisted. Crash during DEFENSIVE mode reverts to NORMAL without warning.

### 13.3 Cascading Failure Scenarios

**Scenario 1: Alpaca position endpoint returns stale data.**
Phase 3 polls `get_positions()` every 60s. If Alpaca returns stale data (position still showing after stop filled), the system thinks the position is alive. Stop ratcheting continues on a closed position. When the next poll returns correct data, D98 detects the "disappearance" and records a stop-out — but at the wrong price (uses estimated stop vs actual fill). The journal records incorrect P&L.

**Scenario 2: LLM returns valid JSON with wrong schema.**
If Together AI returns `{"signal": "BUY", "confidence": "high"}` instead of `{"signal": "BULL", "confidence": 0.85}`, the JSON parser succeeds but the signal validation fails. The agent falls to NEUTRAL with `D92_ALL_FAILED` flag. This is correct defensive behavior, but the root cause (schema mismatch) is logged only at DEBUG level and would require log-level changes to diagnose.

**Scenario 3: Disk full during session.**
All JSONL writes (journal, features, signals, phantom) use synchronous `open(..., "a").write()`. On a full disk, `write()` raises `OSError`. The exception is caught by `except Exception: pass` in most write paths. The system continues trading but stops recording any data. At end of session, the session report write also fails silently. No alert, no degradation signal, no operator notification. The entire session's data is lost.

**Recommendation:** Add a disk space check to the heartbeat. If available space drops below 100MB, log at CRITICAL and set a `_low_disk_mode` flag that disables new entries but keeps exit management running.

---

## Section 14: Efficiency and Performance Enhancement Opportunities

### 14.1 LLM Cost Reduction

*(2026-04-11: Corrected after D218 premise verification. See d218_premise_verification.md.)*

**Zero-weight agent dispatch — ALREADY SOLVED.** Only InstitutionalAgent (weight=0) and DeepSearchAgent (weight=0) are zero-weight. FundamentalAgent maps to float_structure weight=0.15 and contributes to MFCS. The dispatch skip at orchestrator.py:2118-2124 already prevented zero-weight agents from making LLM calls. D218 removed unnecessary EnsembleWrapper creation and added visibility logging. Net cost savings: $0/month.

**TechnicalAgent ensemble — ALREADY MINIMAL.** TechnicalAgent is deliberately NOT wrapped in EnsembleWrapper (excluded at orchestrator.py:249-258). It makes 1 LLM call per evaluation, not 3. No reduction possible.

### 14.2 Latency Reduction

*(2026-04-11: Partially corrected after D218 premise verification.)*

**Batch Alpaca snapshot calls — ALREADY SOLVED.** All 4 get_snapshots() calls in Phase 3 already use batch mode (pass a list of tickers, not individual symbols). Lines 3670, 4335, 4347, 4871 all fetch multiple tickers per call. No optimization needed.

**Parallelize Phase 3 position checks — CONFIRMED VALID.** Phase 3 has 6 sequential `for pos in positions:` loops with I/O-bound await calls (cancel_order, close_position, resubmit). These ARE sequential and would benefit from parallelization, but the refactor requires careful compute-vs-mutate separation due to shared state mutations.

### 14.3 Code Structure Improvements

**Extract phase modules.** Replace the 6,127-line `cmd_paper()` with:
```
src/phases/
    phase0_premarket.py    (91 lines)
    phase1_scanning.py     (54 lines)
    phase15_fastpath.py    (78 lines)
    phase2_evaluation.py   (2,058 lines -> further decompose)
    phase3_management.py   (2,520 lines -> further decompose)
    phase4_close.py        (307 lines)
```
Each module exports a single async function: `async def run_phase(ctx: SessionContext) -> PhaseResult`. The SessionContext object carries all shared state (position_manager, orchestrator, settings, etc.) as a dependency injection container.

**Extract gate evaluator.** The 16 sequential gates in Phase 2 (lines 2464-3020) should become a `GateEvaluator` class:
```python
class GateEvaluator:
    def evaluate(self, verdict, context) -> GateResult:
        for gate in self.gates:
            result = gate.check(verdict, context)
            if result.blocked:
                return result
        return GateResult(allowed=True)
```
Each gate is a separate class implementing a `Gate` protocol. Adding or removing a gate becomes a one-line config change instead of inserting/removing an `if ... continue` block in a 2,000-line function.

**Type the orchestrator constructor.** Currently 6 parameters typed as `Any | None`. Concrete interfaces would enable IDE support and catch integration errors at import time instead of runtime.

---

## Section 15: Operational Runbook Requirements

The system has zero documented operational procedures. The following runbooks are critical for production operation and should be created before the measurement phase begins:

### 15.1 Required Runbooks

| Runbook | Priority | Trigger | Missing Today |
|---------|----------|---------|--------------|
| **Orphaned position recovery** | CRITICAL | Position at broker not tracked by system | No procedure exists |
| **Partial Phase 4 close recovery** | CRITICAL | System crashes during EOD close | No procedure exists |
| **Manual entry path disable** | HIGH | Toxic path producing losses | Must edit code + restart |
| **Trade decision investigation** | HIGH | "Why did/didn't we trade X?" | Must grep logs manually |
| **Watchdog false positive** | HIGH | Watchdog kills healthy system | No escalation procedure |
| **Stale secrets/config detection** | MEDIUM | .env overwritten by launcher | No diff logging |
| **Pre-flight health check** | MEDIUM | Verify system ready before 9:30 | No automated check |
| **Journal reconciliation** | MEDIUM | Verify journal matches broker | Script exists but not automated |

### 15.2 Pre-Flight Check System

Before market open (9:25 AM ET), the system should automatically verify:

1. Alpaca API connectivity (get_account succeeds, equity > 0)
2. LLM provider connectivity (one test call to primary model succeeds)
3. Finnhub connectivity (one earnings calendar fetch succeeds)
4. All circuit breakers in CLOSED state
5. Session state date matches today (not stale from previous crash)
6. Position count at broker matches position_manager count
7. No orphaned orders from previous session
8. Disk space > 100MB
9. Python logging writes to file (test write + verify)
10. Heartbeat file updating (pulse_count incrementing)

If any critical check fails: log CRITICAL, alert via webhook, block Phase 2 entry but continue monitoring existing positions.

---

## Closing Assessment (Revised)

The three things that worry me most remain unchanged from the initial assessment, but this deeper audit adds three more:

**Fourth (unchanged): 42 silent exception handlers are actively hiding failures.** 22% of all error handling in main.py swallows exceptions without logging. These are not theoretical risks — the April 10 session had a silent PreMarketCache failure that went unnoticed. Each silent handler is a place where the system can degrade without anyone knowing. A systematic pass converting these to `logger.warning()` is 30 minutes of work with permanent value.

**Fifth (unchanged): the system has no pre-flight check and no SAFE_MODE.** If Alpaca is down at 9:30 AM, the system doesn't halt gracefully — it continues attempting to trade, hitting the circuit breaker, and producing no useful output. There is no automated check before market open that verifies all dependencies are reachable. A 50-line pre-flight script would prevent the most common class of wasted sessions.

**Sixth: ~~crash recovery loses tranche order IDs and~~ session regime.** *(2026-04-11: Tranche IDs verified as already persisted — session_state.py:55, main.py:756. Audit claim was wrong. Session regime persistence remains an open gap.)* A mid-session crash followed by restart produces a system that looks healthy but has orphaned limit orders at the broker and reverted its risk posture from DEFENSIVE to NORMAL without warning. The tranche gap can cause duplicate fills; the regime gap can cause oversized entries during a losing streak. Both are fixable by extending session_state.json — one hour of work to close both gaps.
