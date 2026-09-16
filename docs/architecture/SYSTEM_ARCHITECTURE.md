# MOMENTUM-X: System Architecture

**Version**: D120 (March 21, 2026)
**Last Updated**: D121 — 13 cross-system bug fixes (orphaned orders, WebSocket subscription, signal cache, tranche guards), D120 Strategy Arena, D119 simulation arena, D118 catalyst profiler
**Test Suite**: 1,939 tests passing
**Protocol**: TR-P §II — `/docs/architecture/`

---

## System Phases

The system operates across four market phases, each with distinct scanning behaviors:

### Phase 0: Pre-Market Research (3:30–4:00 AM ET)
- **Research Engine**: Per-ticker caching of news (24h lookback), SEC filings, technical baselines
- **Output**: Cached data ready for Phase 1 scanner and Phase 2 agents

### Phase 1: Pre-Market Scanning (4:00 AM — 9:20 AM ET)
- **Scanner**: EMC filter — gap detection, RVOL calculation, float cross-reference
  - D105: Tiered price floor ($0.50–$50), absolute volume override (500K), sub-$3 admission with $10M dolvol + 5x RVOL
- **GEX Filter**: Live options chain → GEXCalculator → reject high positive gamma exposure
- **Output**: Ranked candidate list (max 20) ready by 9:15 AM ET

### Phase 1.5: Fast-Path Entry (9:20 AM) — D106
- **FastPathScorer**: 5-agent scoring (3 LLM + 2 deterministic) on top candidates
- **Smart Cache Bypass**: If premarket MFCS cache valid (price stable <3%, fresh <15 min), reuse LLM signals + re-run deterministic only (~45s saved)
- **OTO Orders**: Limit orders placed at 9:30:01 for candidates with MFCS ≥ 0.35

### Phase 2: Full Evaluation (9:30+ AM ET)
- **Pre-Entry Gates**: Spread ≤1%, VIX < 35, VIX shock delta < 15% (D104), SPY > -1% (D104), $5M dollar volume, VWAP directional bias (skipped 9:30–9:35)
- **Agent Evaluation**: 5-agent parallel eval — 2 deterministic (Risk, Technical) + 2 LLM (News, Fundamental) + 1 Tier 1 LLM (ManipulationClassifier)
  - D106: 0.70x confidence deflation on LLM agents only
- **Manipulation Gate**: PROMOTIONAL_LATE → NO_TRADE; PROMOTIONAL_EARLY → half position, 1.5x ATR, aggressive targets
- **MFCS Scoring**: Bipolar [-1.0, +1.0], buy threshold 0.25, consensus ≥2 directional agents
- **Portfolio Risk**: Sector concentration (max 2) + portfolio heat (max 5%) + catalyst concentration (max 2 same type, D110)
- **Execution**: Fixed-risk sizing (`qty = 1% equity / stop_distance`), ATR-based stops, OTO orders
- **Experiment Replay**: D102 — 22 parameter variants replayed at zero LLM cost (~2ms)
- **Entry Catalyst Profiler** (D118): Per-trade catalyst durability profiling → adjusts half-life, ATR multiplier, gratitude decay, and risk scale

### Offline: Strategy Simulation Arena (D119+D120)
- **Arena CLI**: `scripts/run_arena.py` — replays historical sessions through 22 strategy profiles
- **Two-layer replay**: Entry re-evaluation (MFCS recompute) + Exit simulation (bar-by-bar replay)
- **Elo Tournament**: Round-robin pairwise ranking across all sessions. 1,155 matchups.
- **Champion**: `ablation_sizing` (aggressive_all + mfcs_scaling_denom=0.35) — Elo 1450, +$2,508
- **Counterfactual analysis**: Per-trade what-if comparison across all profiles
- **Details**: See `docs/research/STRATEGY_ARENA_FINDINGS.md`

### Phase 3: Intraday Management (9:30 AM — 3:45 PM ET)
- **Position Manager**: Trailing stops, 3-tranche profit taking, time-based closes
- **Exit Intelligence**: 13-signal composite (threshold 0.40, tighten 0.15)
  - D106: +DistributionDetector (7 weighted signals, <1ms)
- **Parallel Exit Engine** (D109+D110): 6 independent strategies, any-of architecture
  - VelocityEngine, PullbackClassifier, VolumeExhaustion, GratitudeExit, CatalystHalfLife, AlphaDecayOracle
  - Currently freeze-safe: compute+log only (`parallel_strategies_active=False`)
- **Contagion Network** (D110): Cross-position TIGHTEN signal propagation (10-min decay)
- **Chandelier Exit**: `stop = peak - ATR × adaptive_mult` (1.5x/2.0x/3.0x by regime)
- **Periodic Re-Scan**: Every 15 cycles for newly emerging candidates

### Phase 4: EOD Close (3:45 PM ET)
- Close all remaining positions

---

## Module Dependency Graph

```
config/settings.py
    │
    ├──▶ src/data/alpaca_client.py        (Alpaca WebSocket + REST + get_bars)
    ├──▶ src/data/news_client.py          (Alpaca News + Finnhub)
    ├──▶ src/data/sec_client.py           (SEC EDGAR EFTS)
    ├──▶ src/data/options_provider.py     (Alpaca Options API)
    ├──▶ src/data/technical_indicators.py (RSI/MACD/BB/S-R/VWAP)
    ├──▶ src/data/historical_loader.py    (Alpaca historical bars + CSV cache)
    │
    ├──▶ src/scanners/premarket.py        (EMC: gap + RVOL + ATR filter)
    ├──▶ src/scanners/gex.py              (GEXCalculator: net gamma exposure)
    ├──▶ src/scanners/gex_filter.py       (Hard GEX rejection threshold)
    ├──▶ src/scanners/intraday_vwap.py    (VWAP breakout detection)
    ├──▶ src/core/scan_loop.py            (Orchestrates scanner + GEX + EMC)
    │
    ├──▶ src/core/models.py               (Domain models: CandidateStock, etc.)
    ├──▶ src/core/scoring.py              (Bipolar MFCS computation)
    ├──▶ src/core/adaptive_router.py      (D112: 3-tier evaluation depth routing)
    ├──▶ src/core/kelly_tier.py           (D115: 4-tier Kelly conviction classifier)
    ├──▶ src/core/backtester.py           (CPCV implementation + null hypotheses)
    │
    ├──▶ src/agents/news_agent.py         (Catalyst classification + sentiment — Qwen3.5-397B)
    ├──▶ src/agents/fundamental_agent.py  (Float verification + dilution — Qwen3-235B)
    ├──▶ src/agents/deterministic_risk.py (Zero-LLM risk assessment — D101)
    ├──▶ src/agents/deterministic_technical.py (RSI/MACD/BB/EMA/ATR — D101)
    ├──▶ src/agents/manipulation_classifier.py (4-phase lifecycle — D106)
    ├──▶ src/agents/debate_engine.py      (Disabled: max_debates=0)
    ├──▶ src/agents/prompt_arena.py       (Variant tracking + Elo — math reused by Strategy Arena)
    │
    ├──▶ src/arena/strategy_arena.py      (D119+D120: 22-profile Elo tournament, two-layer replay)
    ├──▶ src/arena/__init__.py            (D119: Strategy Simulation Arena package)
    │
    ├──▶ src/execution/alpaca_executor.py   (Order management — OTO + standalone)
    ├──▶ src/execution/bridge.py            (Verdict → Order → Position)
    ├──▶ src/execution/position_manager.py  (Stops, exits, sizing)
    ├──▶ src/execution/exit_intelligence.py (13-signal composite + parallel engine wiring)
    ├──▶ src/execution/exit_strategies.py   (6 parallel strategies + contagion + oracle — D109+D110)
    ├──▶ src/execution/tranche_monitor.py   (3-tranche exit tracking)
    ├──▶ src/execution/stop_resubmitter.py  (Stop ratcheting — D108: 3x retry)
    ├──▶ src/execution/fill_stream_bridge.py(WebSocket fill → tranche → stop)
    ├──▶ src/execution/portfolio_risk.py    (Sector + heat + catalyst concentration)
    ├──▶ src/execution/trade_result_tracker.py (D115: Rolling win rate for Kelly tier)
    ├──▶ src/execution/fast_path.py         (5-agent fast-path scorer — D106)
    │
    ├──▶ src/monitoring/metrics.py          (Prometheus + data completeness + disk snapshots)
    ├──▶ src/monitoring/server.py           (HTTP /metrics + /status endpoint)
    │
    ├──▶ scripts/etl_sqlite.py              (Post-session JSONL→SQLite ETL — D109)
    ├──▶ scripts/backtest.py                (Backtest harness + null hypotheses — D109)
    ├──▶ scripts/run_arena.py              (D119+D120: Strategy Arena CLI — 22 profiles, tournament)
    ├──▶ scripts/replay_optimizer.py       (Trade replay engine — SimConfig, simulate_trade)
    │
    └──▶ src/core/orchestrator.py           (Pipeline: data → agents → scoring → risk → execute)
```

---

## Data Flow (Single Candidate Evaluation — Post-D115)

```
1. Scanner detects candidate (pure Python, no LLM)
   ├── EMC filter: Gap% > 5%, RVOL > 2.0 (OR vol > 500K), ATR ratio > 1.5
   ├── Price filter: $0.50-$50 (sub-$3 requires $10M dolvol + 5x RVOL)
   └── GEX filter: Live options chain → GEXCalculator → reject high positive GEX
   ↓
2. Data enrichment per candidate (~80% fill rate)
   ├── News:       Alpaca + Finnhub → 24h lookback, max 10 items per ticker
   ├── Technical:  200 1-min bars → RSI, MACD, BB, S/R, VWAP, EMA/SMA
   ├── SEC:        EDGAR EFTS → S-3/424B5 dilution detection (90-day window)
   └── Options:    Alpaca options chain → GEX normalization + gamma flip
   ↓
3. Pre-entry gates (all must pass)
   ├── Spread ≤ 1% of price
   ├── VIX < 35 (block) / VIX < 25 (reduce) / VIX shock < 15% delta
   ├── SPY > -1% (halt gate)
   ├── Dollar volume > $5M
   └── VWAP: price > VWAP (skipped 9:30-9:35)
   ↓
3b. D112: Adaptive Compute Router (3-tier depth routing, <1ms)
   ├── Tier 1 INSTANT_REJECT: RVOL < 1.5, price < $0.50, float > 200M,
   │   gap < 3%, 424B5 dilution, corporate actions, pump pattern → NO_TRADE
   ├── Tier 2 DETERMINISTIC_ONLY: strong det. MFCS > 0.40 or weak < 0.05
   │   → skip LLM, reuse D107 deterministic path (<100ms)
   └── Tier 3 FULL_PIPELINE: genuinely ambiguous → proceed to step 4
   ↓
4. Orchestrator dispatches to 5 agents IN PARALLEL (asyncio.gather)
   ├── News Agent         → AgentSignal (catalyst type, sentiment) — Qwen3.5-397B, 0.70x deflation
   ├── Fundamental Agent  → AgentSignal (float, dilution) — Qwen3-235B, 0.70x deflation
   ├── Deterministic Risk → AgentSignal (liquidity, halt risk) — zero LLM, <1ms
   ├── Deterministic Tech → AgentSignal (RSI, MACD, BB, EMA) — zero LLM, <1ms
   └── ManipulationClassifier → Phase (ORGANIC/PROMO_EARLY/PROMO_LATE/UNCERTAIN) — Tier 1 LLM
   ↓
5. Manipulation gate
   ├── PROMOTIONAL_LATE → NO_TRADE (automatic rejection)
   └── PROMOTIONAL_EARLY → half position, 1.5x ATR stop, +3/+6/+10% targets
   ↓
6. Signal Aggregator computes Bipolar MFCS (pure math, no LLM)
   ├── STRONG_BULL=1.0, BULL=0.5, NEUTRAL=0.0, BEAR=-0.5, STRONG_BEAR=-1.0
   └── Requires ≥2 non-NEUTRAL directional agents
   ↓
7. Portfolio risk gate
   ├── Sector concentration: max 2 per sector
   ├── Catalyst concentration: max 2 with same catalyst_type (D110)
   └── Portfolio heat: max 5%
   ↓
7b. D115: Kelly Tier Classification (shadow mode — classify + log, use Tier 1 values)
   ├── Tier 1 Standard: 1% risk, 15% max position (default)
   ├── Tier 2 High Conviction: 2% risk, 25% max (MFCS≥0.60, 3+ agents, proven catalyst)
   ├── Tier 3 Exceptional: 4% risk, 35% max (MFCS≥0.80, gap 10-30%, float≤20M)
   └── Tier 4 Statistical Outlier: 5% risk, 40% max (MFCS≥0.90, confirmed catalyst)
   ↓
8. If MFCS > 0.25: Execution Engine places OTO order via Alpaca
   ├── Fixed-risk sizing: qty = floor(equity × risk_per_trade_pct / stop_distance)
   └── ATR-based stop: entry - max(2.0 × ATR_14d, entry × 4%)
   ↓
9. D102 Experiment Replay: 22 parameter variants at zero LLM cost (~2ms)
   ↓
10. Position Manager monitors
    ├── 13-signal exit intelligence (composite threshold 0.40)
    ├── 6-strategy parallel exit engine (any-of, freeze-safe log-only)
    │   ├── VelocityEngine (3-min rolling velocity, 4 phases)
    │   ├── PullbackClassifier (30%/50% retracement state machine)
    │   ├── VolumeExhaustion (entry-bar volume ratio)
    │   ├── GratitudeExit (time-decaying R-multiple, 3.0R→0.75R)
    │   ├── CatalystHalfLife (per-catalyst shelf life + velocity gate)
    │   └── AlphaDecayOracle (observed vs null return curve)
    ├── ContagionNetwork (cross-position TIGHTEN propagation, 10-min decay)
    ├── Chandelier exit (peak - ATR × adaptive_mult)
    ├── 3-tranche profit taking (+5%/+10%/+20%)
    └── 20-min momentum exit (cut if < +1R in 20 min)
    ↓
11. Outcome recorded → Trade journal → Experiment journal → SQLite ETL
```

---

## Technology Stack

| Component | Choice | Version | Notes |
|---|---|---|---|
| Language | Python | 3.12+ | — |
| Async Runtime | asyncio + uvloop | — | Latency requirement |
| Data Frames | Polars | 1.x | Sub-ms vectorized ops |
| HTTP Client | httpx | 0.27+ | Async HTTP/2 |
| WebSocket | websockets | 13+ | Alpaca streams |
| LLM Client | litellm | 1.x | Unified API for all providers |
| Testing | pytest + hypothesis | — | 1,783 tests, property-based |
| Broker | Alpaca (alpaca-py) | 0.30+ | Paper trading |
| Database | SQLite (D109 ETL) | — | 6 tables, 3 materialized views |
| Config | pydantic-settings | 2.x | Type-safe config |
| Scheduling | Task Scheduler + PowerShell | — | Market phase transitions |
| Monitoring | Prometheus + HTTP server | — | Metrics, heartbeat, snapshots |

---

## LLM Infrastructure

| Tier | Model | Usage | Fallback Chain |
|---|---|---|---|
| Tier 1 | Qwen3.5-397B | News Agent, ManipulationClassifier | → DeepSeek-V3.1 → Llama-3.3-70B |
| Tier 2 | Qwen3-235B | Fundamental Agent | → MiniMax-M2.5 → Llama-3.3-70B |
| Deterministic | Zero LLM | Risk Agent, Technical Agent | N/A (0% failure rate) |
| Emergency | All LLMs down | Deterministic-only mode (D107) | Technical + Risk + RVOL only |

**Confidence Deflation**: 0.70x multiplier on LLM confidence scores only. Deterministic agents bypass deflation (duck-typed, not BaseAgent subclasses).

---

## Key Subsystem Details

### Exit Intelligence (D106+D109+D110)

The exit system has three layers that operate concurrently:

1. **13-Signal Composite Exit Intelligence** — Weighted composite of momentum, volume, price, and distribution signals. Threshold 0.40 → EXIT, 0.15 → TIGHTEN. Includes DistributionDetector (D106: 7 weighted signals, <1ms).

2. **6-Strategy Parallel Exit Engine** (D109+D110) — Independent strategies in any-of architecture. Any single strategy can fire EXIT or TIGHTEN independently. Currently freeze-safe (compute+log only).

3. **ContagionNetwork** (D110) — Cross-position signal propagation. When one position fires an exit signal, correlated positions receive TIGHTEN warnings with 10-minute decay.

### Portfolio Risk Management (D100+D110)

- **Sector concentration**: Max 2 positions per sector
- **Portfolio heat**: Max 5% total risk across all positions
- **Catalyst concentration** (D110): Max 2 positions with same catalyst type
- **Daily loss limit**: -10% circuit breaker with exponential backoff (D108)

### Tiered Kelly Criterion Position Sizing (D115)

4-tier conviction framework scaling risk based on signal confluence:

- **Tier 1 Standard** (1% risk, 15% max): Default for 80%+ of trades
- **Tier 2 High Conviction** (2% risk, 25% max): MFCS≥0.60, 3+ directional agents, proven catalyst, RVOL≥5x
- **Tier 3 Exceptional** (4% risk, 35% max): MFCS≥0.80, top-tier catalyst, gap 10-30%, float≤20M, VIX<20
- **Tier 4 Statistical Outlier** (5% risk, 40% max): MFCS≥0.90, confirmed catalyst, win rate≥45%, heat<3%

Safety: max 2 Tier 3+/day, sequential lockout after Tier 3+ stop, TradeResultTracker for rolling win rate (JSONL-backed). Currently in shadow mode (`KELLY_ENABLED=false`).

### Analytical Infrastructure (D109)

- **Backtest null hypotheses**: `--null-time` (random timing), `--null-filter` (buy everything), `--anti-signal` (accepted vs rejected)
- **MFE/MAE tracking**: Per-trade maximum favorable/adverse excursion
- **SQLite ETL**: Post-session JSONL → SQLite (6 tables, 3 materialized views)
- **Experiment replay**: 22 parameter variants per evaluation at zero LLM cost
