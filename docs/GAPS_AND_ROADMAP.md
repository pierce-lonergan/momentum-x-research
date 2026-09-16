# Momentum-X: Comprehensive Gap Analysis & Path to Extreme Profitability

> **NOTE (2026-04-06)**: This document was written at D120/D121. The system is now at **D210**. Key gaps listed below (Faller Risk, SEC prefetch, short interest, sentiment velocity, tiered universe, Kelly sizing, arena infrastructure, session data collection) have been addressed in D160–D210. See README.md for the current system state.

**Date**: March 13, 2026 (Day 15)
**Last Updated**: March 21, 2026 (D121: 13 cross-system bug fixes, D120: Strategy Arena evolved profiles, D119 simulation arena, D118 catalyst profiler)
**Account**: $139,449.15 (started ~$100,000)
**System Version**: D120 (latest: strategy simulation arena with 22 profiles, Elo tournament ranking, 4 unlocked strategy dimensions, entry catalyst profiler, tiered Kelly position sizing, news catalyst broadening, heartbeat timeout fix, adaptive compute router, 8-bug code sweep, catalyst half-life, alpha decay oracle, contagion network, catalyst concentration limits)
**Test Suite**: 1,939 tests passing, 0 failures

---

## 1. Executive Summary

Momentum-X has a **sound architectural foundation** — modular agent pipeline, Polars-based EMC scanner, deterministic risk gates, multi-layer exit system, comprehensive observability. The system correctly identifies explosive momentum candidates and has sophisticated infrastructure for scoring, debate, and execution.

However, after 15 trading days, the system has **zero profitable systematic exits**. Every gain came from Day 1 manual-era trades or accidental overnight drift. The path to extreme profitability requires fixing three fundamental bottlenecks:

1. **Entry Timing** — The system finds the right stocks but enters too late
2. **Signal Calibration** — MFCS thresholds and position sizing aren't tuned to signal quality
3. **Exit Execution** — Exit intelligence has 12 signals but none have triggered a real exit

This document provides a comprehensive gap analysis across all system components, ranked by expected Sharpe ratio impact, with a concrete roadmap for each tier.

---

## 2. System Health Scorecard (Post-D110)

| Component | Health | Notes |
|-----------|--------|-------|
| EMC Scanner | **Healthy** | D105: Tiered price floor, absolute vol override, $50 ceiling |
| Pre-Market Research | **Healthy** | Phase 0 caching (news, SEC, technicals) working |
| Bipolar MFCS Scoring | **Healthy** | D101: Full bipolar [-1.0, +1.0] |
| Deterministic Risk Agent | **Healthy** | D101: Zero LLM, microsecond latency |
| Deterministic Technical Agent | **Healthy** | D101: RSI/MACD/BB/EMA/ATR, 0% failure |
| News Agent (LLM) | **Healthy** | Qwen3.5-397B, **D106: 0.70x deflation** (was 0.6x), calibrated prompts, **D114: CORPORATE_UPDATE/SECTOR_CATALYST recognition for gap-and-go** |
| Fundamental Agent (LLM) | **Healthy** | Qwen3-235B, float/dilution analysis |
| Circuit Breaker | **Healthy** | -10% daily loss limit, **D108: exponential backoff, system health gate** |
| Session State Recovery | **Healthy** | Atomic JSON writes, crash recovery, **D108: .bak rotation + fallback, diff logging, post-merge validation** |
| Stop-Loss Resubmission | **Healthy** | Two-phase cancel/resubmit, **D108: 3x retry with exponential backoff** |
| Scheduling & Heartbeat | **Healthy** | Task Scheduler + PowerShell launcher, **D113: pulse calls in Phase 1/2/main loop to prevent heartbeat timeout** |
| Trade Journal | **Healthy** | JSONL per-evaluation, full provenance, **D108: reasoning cap 500→1500** |
| Experiment Framework | **Healthy** | D102: 22→23 variant parameter replay (D106: +deflate_0.75) |
| D103 Post-Trade Bugfixes | **Healthy** | 5 defense layers against NVTS-type losses |
| D104 Macro Regime | **Healthy** | VIX shock + SPY halt + ATR fallback + confidence decay |
| D105 Filter Enhancements | **Healthy** | Sub-$3 admission, absolute vol, $50 ceiling |
| D106 WS1 Entry Timing | **Healthy** | 5-agent fast-path + smart cache bypass (~45s saved per candidate) |
| D106 WS1 VWAP Gate | **Healthy** | Returns None (no opinion) when WebSocket disconnected; skips 9:30-9:35 ET |
| Exit Intelligence | **Improved** | D106: threshold 0.40, tighten 0.15, +distribution_detector; **D109: Parallel Exit Strategy Engine** (4 independent strategies, any-of architecture, freeze-safe logging) |
| Manipulation Detection | **NEW** | D106 WS2+WS3: ManipulationClassifier (Tier 1), DistributionDetector, PROMO_LATE→NO_TRADE, PROMO_EARLY→half/tight |
| Fast-Path Entry | **Improved** | D106: 5-agent scoring (was 3), smart cache bypass at market open |
| Backtest Analytical Extensions | **NEW** | D109: --null-time, --null-filter, --anti-signal, MFE/MAE tracking |
| SQLite Storage Layer | **NEW** | D109: Post-session ETL (JSONL→SQLite), 6 tables, 3 materialized views |
| Metric Snapshot Retention | **Improved** | D109: 50→200 snapshots, cross-session diagnostics |
| Validation Clamp Counter | **NEW** | D109: `mx_state_validation_clamps_total` Prometheus counter |
| Parallel Exit Strategies | **Improved** | D109: VelocityEngine, PullbackClassifier, VolumeExhaustion, GratitudeExit; **D110: +CatalystHalfLife, +AlphaDecayOracle v1** — 6 strategies, any-of architecture, compute+log during freeze |
| Contagion Network | **NEW** | D110: Cross-position signal propagation — when position A deteriorates, correlated positions B/C/D get early TIGHTEN warning. 10-min decay, configurable threshold |
| Catalyst Concentration | **NEW** | D110: Max 2 positions with same catalyst type at entry time. Complements existing sector concentration limits |
| Tiered Kelly Sizing | **NEW** | D115: 4-tier conviction framework (1-5% risk), shadow mode (enabled=False), TradeResultTracker for rolling win rate |
| Entry Catalyst Profiler | **NEW** | D118: Per-trade catalyst durability profiling. Adjusts half-life, ATR multiplier, gratitude decay, and risk scale based on catalyst type (EPHEMERAL/SHORT_LIVED/MULTI_HOUR/PERMANENT_REVALUATION) |
| Strategy Simulation Arena | **NEW** | D119+D120: 22-profile tournament across 5 historical sessions (1,155 matchups). Two-layer replay (entry re-evaluation + exit simulation). Elo ranking. `ablation_sizing` (mfcs_scaling_denom=0.35) crowned champion (Elo 1450, +$2,508). See `docs/research/STRATEGY_ARENA_FINDINGS.md` |
| **mx-arena Exchange Simulator** | **NEW** | D129-D131: Full Alpaca exchange simulator. Runs unmodified production code against historical bars at 180K ticks/sec. Matching engine with OTO/bracket orders, 10% partial fills, NBBO spread model. REST API (14 endpoints) + WebSocket (JSON + msgpack). Parameter sweep engine: 8 configs in 3.6s. Historical validation of Mar 26 BUY signals against real 1-min bars. See `docs/architecture/MX_ARENA.md` |
| Paper Trading Log | **NEW** | D114: Day-to-day observation and decision log (`docs/PAPER_TRADING_LOG.md`) |
| Debate Engine | **Disabled** | D100: max_debates=0 (0% conversion rate) |
| Institutional Agent | **Disabled** | Weight=0.00, no options flow data |
| Deep Search Agent | **Disabled** | Weight=0.00 |
| Prompt Arena | **Disabled** | Elo system complete but not wired to production (Elo math reused by Strategy Arena) |

---

## 3. Tier 1: Critical Gaps (Highest Impact)

### 3.1 Entry Timing (Est: +1.0-1.5 Sharpe) — **PARTIALLY FIXED D106 WS1**

**The Problem**: The system identifies correct stocks but enters 30-180 minutes after the optimal window. Days 11-14 showed all positions stopped out within ~15 minutes — they entered after the move peaked.

**Root Cause**: Full 4-agent evaluation pipeline takes 30-100s per candidate. By the time MFCS is computed and consensus gates pass, the opening momentum has dissipated.

**Evidence**:
- Day 5: 1.7% capture rate (entered 60+ min late)
- Day 6: 0.3% capture rate (entered 177 min late)
- Days 11-14: 0% capture rate (all stop-outs)
- Fast-path exists (D85) but was marked "degraded" — reconciliation with full eval adds complexity

**D106 WS1 Fixes Applied**:
1. ✅ **5-agent fast-path scoring**: FastPathScorer extended from 3 LLM agents (50% MFCS weight) to 5 agents by adding DeterministicTechnical + DeterministicRisk (<1ms each). Technical gracefully degrades pre-market (NEUTRAL/0.0). Risk uses candidate data from Phase 0.
2. ✅ **Smart cache bypass at market open**: At 9:30, if premarket MFCS cache is valid (price stable <3%, cache fresh <15 min), skip full LLM re-dispatch. Reuse cached LLM signals + re-run only deterministic agents with real market data. Saves ~45s per candidate.
3. ✅ **VWAP gate skip for 9:30-9:35 ET**: VWAP is undefined at market open. Gate now skipped for first 5 minutes.
4. ✅ **VWAP None fallback**: `_get_vwap()` returns `None` (no opinion) instead of `price*0.98` when WebSocket disconnected. Old fallback auto-rejected entries ALL DAY on disconnect.

**Remaining Entry Timing Improvements**:
- Decouple fast-path from full eval (if fast-path fills at 9:30, don't re-evaluate)
- Cap post-open eval latency to 15s with pre-market fallback
- Consider fast-path-only mode for highest-conviction candidates

**Files**: `src/execution/fast_path.py`, `src/core/orchestrator.py`, `config/settings.py`, `main.py`

---

### 3.2 Position Sizing vs. Signal Quality (Est: +0.5-0.8 Sharpe) — **FIXED D115**

**The Problem**: All trades risk exactly 1% of equity regardless of MFCS score. A marginal signal (MFCS 0.26) is sized identically to a strong signal (MFCS 0.60+).

**D115 Fix: Tiered Kelly Criterion Position Sizing**

4-tier conviction framework scaling risk 1-5% based on signal confluence:

| Tier | Name | Risk/Trade | Max Position | Requirements |
|------|------|-----------|-------------|-------------|
| 1 | Standard | 1% | 15% | Default (80%+ of trades) |
| 2 | High Conviction | 2% | 25% | MFCS≥0.60, 3+ directional agents, proven catalyst, RVOL≥5x, R:R≥2.5 |
| 3 | Exceptional | 4% | 35% | MFCS≥0.80, FDA/earnings/M&A, gap 10-30%, float≤20M, VIX<20 |
| 4 | Statistical Outlier | 5% | 40% | MFCS≥0.90, confirmed catalyst, win rate≥45%, R:R≥3.0, heat<3% |

Safety guardrails: max 2 Tier 3+ per day, sequential lockout after Tier 3+ stop, budget auto-downgrade.

Currently in **shadow mode** (`KELLY_ENABLED=false`): classifies and logs tier but uses Tier 1 values for actual sizing. Activation after 5+ observation sessions.

**Files**: `src/core/kelly_tier.py`, `src/execution/trade_result_tracker.py`, `config/settings.py`, `src/core/orchestrator.py`, `src/execution/alpaca_executor.py`, `src/execution/bridge.py`

---

### 3.3 Exit Intelligence Calibration (Est: +0.3-0.5 Sharpe) — **PARTIALLY FIXED D106 WS1**

**The Problem**: Exit intelligence has 12 sophisticated signals but the composite threshold (0.60) had **never been reached** on a live position. Zero exits triggered in 15 days.

**Evidence**:
- PROFITABILITY_ASSESSMENT: "Smart exits (D78): 0 (exit intelligence never triggered an exit)"
- Each of 12 signals contributes ~0.08 to composite (1.0 / 12 signals)
- Reaching 0.60 requires 7-8 signals at maximum simultaneously — extremely unlikely

**D106 WS1 Fixes Applied**:
1. ✅ **Exit threshold lowered from 0.60 to 0.40**: Now achievable with 5 signals at high intensity (was 7-8)
2. ✅ **Tighten threshold lowered from 0.20 to 0.15**: TIGHTEN stop behavior triggers earlier

**D109 Phase 4 Fixes Applied**:
1. ✅ **Parallel Exit Strategy Engine**: 4 independent strategies (VelocityEngine, PullbackClassifier, VolumeExhaustion, GratitudeExit) in any-of architecture — any single strategy can fire independently
2. ✅ **Freeze-safe logging**: Strategies compute + log via JSONL but don't change trading behavior during freeze (`parallel_strategies_active=False`)
3. ✅ **Relative thresholds**: PullbackClassifier scales with advance size (30%/50% retracement), not fixed percentages
4. ✅ **Time-decaying R-multiple**: GratitudeExit captures profits at 3.0R→0.75R floor over 30 minutes
5. ✅ **Per-position state tracking**: PullbackClassifier maintains state machine per ticker with auto-pruning

**Remaining Exit Intelligence Improvements**:
- Add signal weighting: volume_fade and vwap_deterioration are more predictive — weight them 2x
- Add urgent exit signals: RVOL collapse, bid-side dominance
- **Activate parallel strategies** post-freeze (`parallel_strategies_active=True`)
- Backtest exit thresholds: Run D102 variants with exit_threshold sweep
- Innovation 4: MFE-Calibrated Windows (requires empirical MFE distribution from freeze data)
- Innovation 5: Stay Score (architectural replacement, needs threshold calibration)
- Innovation 6: Intraday ATR Stop (changes stop computation — post-freeze; intraday ATR logged now)

**Files**: `src/execution/exit_intelligence.py`, `src/execution/exit_strategies.py`, `config/settings.py`

---

### 3.4 ATR Stop Calibration (Est: +0.3-0.6 Sharpe)

**The Problem**: Initial stop at 2.0x ATR_14d may be too tight for gapping small-caps. Chandelier exit at 4.0x ATR may be too loose.

**Evidence**:
- Days 11-14: All positions stopped out within 15 minutes
- A stock with 2% ATR_14d gapping +8% has intraday volatility far exceeding the 14-day average
- 2.0x ATR = 4% stop on a stock routinely moving 3-5% intraday = whipsaw
- Chandelier at 4.0x ATR allows positions to give back nearly all gains

**Fix Strategy**:
1. **Use intraday ATR for entry-day stops**: Compute 5-min ATR from first 30 minutes, not 14-day daily ATR
2. **D102 parameter sweep**: Run ATR multiplier variants [1.5, 2.0, 2.5, 3.0, 3.5] with D102 framework
3. **Gap-adjusted stops**: For stocks gapping > 10%, widen initial stop by 1.5x (gap implies higher intraday vol)
4. **Tighten Chandelier from 4.0x to 2.5-3.0x**: Institutional standard is 1.5-2.0x; 4.0x gives back too much

**Files**: `config/settings.py`, `src/execution/exit_intelligence.py`

---

## 4. Tier 2: High-Value Improvements

### 4.1 Confidence Deflation Refinement (Est: +0.3-0.5 Sharpe) — **PARTIALLY FIXED D106 WS1**

**The Problem**: The 0.6x deflation multiplier was too aggressive, compressing the entire LLM scoring range into [0, 0.6]. An LLM confidence of 0.80 became 0.48, which was below the buy threshold for most setups.

**D106 WS1 Findings & Fixes**:
- ✅ **Deflation does NOT apply to deterministic agents**: DeterministicRiskAgent and DeterministicTechnicalAgent are duck-typed and bypass `BaseAgent.analyze()`. Only LLM agents (News, Fundamental, Institutional, DeepSearch) are deflated. This was confirmed via code exploration — no code change needed.
- ✅ **Deflation raised from 0.6x to 0.70x**: At 0.6x, confidence 0.80→0.48 compressed scoring range too much. At 0.70x, confidence 0.80→0.56 — more appropriate.
- ✅ **Experiment variant added**: `deflate_0.75` added to deflation sweep for counterfactual comparison. Full sweep: [0.4, 0.5, 0.6, 0.7, 0.75, 0.8].

**Remaining Deflation Improvements**:
- Per-agent differentiation (News 0.6x vs Fundamental 0.8x) — needs trade data
- Platt scaling calibration to replace crude multiplier (needs 100+ trades)

**Files**: `config/settings.py`, `src/agents/base.py`, `data/experiments/experiments.yaml`

---

### 4.2 Consensus Gate Relaxation (Est: +0.2-0.3 Sharpe)

**The Problem**: Current gate requires 2+ non-NEUTRAL directional agents. When one LLM agent fails (returning NEUTRAL with empty reasoning, ~15-20% of the time), only deterministic agents provide signals. If only Technical is directional, the gate blocks even strong setups.

**Fix**: Change requirement from "2 directional agents" to "1 directional agent + RVOL > 3.0x" — ensures volume confirmation without requiring LLM consensus.

**Files**: `src/core/orchestrator.py`, `config/settings.py`

---

### 4.3 Spread Filter Recalibration (Est: +0.2-0.4 Sharpe)

**The Problem**: Max 1% bid-ask spread rejects many small-cap momentum stocks that are our target universe. A $2 stock with $0.03 spread = 1.5% — auto-rejected despite being perfectly tradeable.

**D105 Evidence**: BIAF at $1.07 had 0.4% spread with 170M volume. Sub-$3 stocks with massive volume have tight spreads.

**Fix**: Dynamic spread filter:
- Price > $10: max spread 0.5%
- Price $3-$10: max spread 1.0%
- Price $0.50-$3 (high-vol override): max spread 2.0%

**Files**: `config/settings.py`, `src/execution/bridge.py`

---

### 4.4 Sector Concentration Enforcement (Est: +0.2-0.4 Sharpe)

**The Problem**: `PortfolioRiskManager` with `max_sector_positions = 2` exists in code but may not be called before order submission. If 3 biotech positions all halt simultaneously, portfolio takes concentrated -15% hit.

**Fix**: Add explicit `portfolio_risk.check_entry(ticker, sector)` call in execution bridge before order submission.

**Files**: `src/execution/bridge.py`, `src/execution/portfolio_risk.py`

---

### 4.5 RVOL Exhaustion Hard Gate (Est: +0.2-0.3 Sharpe)

**The Problem**: RVOL > 5.0 is flagged as exhaustion risk in candidate model but not enforced as a hard gate when combined with overbought indicators.

**Evidence**: TECHNICAL_DEEP_DIVE notes RVOL > 5.0 as "exhaustion risk" but the RVOL scoring function gives max score (0.95-1.0) at RVOL > 4.0 — rewarding exactly the condition that signals blow-off tops.

**Fix**: If RVOL > 5.0 AND RSI(9) > 75 AND no news catalyst → VETO entry (retail FOMO blow-off). Already partially handled by gap quality classifier but needs explicit gate in scoring.

**Files**: `src/agents/deterministic_technical.py`, `src/core/scoring.py`

---

## 5. Tier 3: Medium-Value Improvements

### 5.1 VIX/Macro Regime Enforcement Verification

Verify that VIX checks in settings.py are actually called in the orchestrator evaluation loop. D104 added VIX shock and SPY halt thresholds but the enforcement path needs audit.

### 5.2 Tranche Target Recalibration

Current tranches (+5%, +10%, +20%) are designed for swing trading. Intraday momentum on small-caps suggests +3%, +6%, +10% would capture more exits.

### 5.3 20-Minute Momentum Exit Tuning

The +1R requirement within 20 minutes may be too aggressive. Small-caps often consolidate 10-15 min after initial move then ramp again. Consider 30-minute window or +0.5R threshold.

### 5.4 State Recovery Robustness — **DONE D107+D108**

- ✅ D107: Orphaned order reconciliation (cancel untracked orders at startup)
- ✅ D108: State file backup rotation (.bak) with fallback load on corruption
- ✅ D108: Post-merge sanity assertions (clamp impossible values, warn on suspicious state)
- ✅ D108: Post-recovery connectivity probe (verify broker link before trading)
- ✅ D108: State diff logging (DEBUG-level field change tracking)

### 5.5 Pre-Mortem Signal Logging — **PARTIALLY DONE D107**

- ✅ D107: SignalHistoryLogger (13-signal JSONL per cycle)
- Remaining: Log individual exit intelligence signal scores on every position check

---

## 6. Tier 4: Data-Dependent Optimizations (30-100+ Trades Required)

### 6.1 Platt Scaling Calibration (100+ trades)
Fit sigmoid calibration curve to predicted confidence vs. realized accuracy. Replaces the crude 0.6x deflation with empirically-calibrated confidence.

### 6.2 MWU Weight Optimization (30+ trades)
Use Multiplicative Weights Update to dynamically adjust agent weights based on rolling accuracy. Currently fixed at 0.35/0.30/0.20/0.15.

### 6.3 Shapley Attribution (50+ trades)
Decompose realized P&L to per-agent contributions via Monte Carlo Shapley values. Infrastructure complete in `src/analysis/shapley.py`.

### 6.4 Walk-Forward Optimization
Extend backtest harness with rolling out-of-sample validation using CPCV. Infrastructure exists in `src/core/backtester.py`.

### 6.5 Prompt Arena Activation
Wire Elo-based prompt tournament into production. Select highest-Elo prompt variant per agent per trade.

---

## 7. Test Coverage Gaps

### Current: 1,737 tests (93% of modules covered)

**Strong Coverage**:
- Exit Intelligence: 163 tests + 68 parallel exit strategy tests (excellent — D109+D110)
- Market Calendar: 35 tests
- Technical Indicators: 35 tests
- D103-D105 enhancements: 56+ new tests
- D109 analytical infrastructure: 104+ tests (backtest, ETL, MFE/MAE, metrics)
- D110 exit innovations: 27 tests (catalyst half-life, contagion, oracle)
- D112 Adaptive Compute Router: 30 tests (tier routing, pump detection, catalyst override, metrics)

**Critical Gaps**:
| Module | LOC | Direct Tests | Priority |
|--------|-----|-------------|----------|
| Orchestrator (orchestrator.py) | 1,613 | 7 (indirect) | HIGH |
| WebSocket Client | 546 | 0 | MEDIUM |
| Position Manager | 529 | 0 direct | MEDIUM |
| Monitoring/Metrics | 434 | 0 | LOW |
| Deterministic Technical | 371 | 5 | MEDIUM |
| Agent Base Class | 321 | 0 direct | LOW |
| Backtest Simulator | 309 | 0 | MEDIUM |

**Missing Test Categories**:
- Performance/load tests: None (no latency benchmarks)
- Stress tests: None (no high-volume scenarios)
- End-to-end fast-path tests: Minimal

---

## 8. Configuration Parameter Summary (D105 Current)

### Scanner Thresholds
| Parameter | Value | Source |
|-----------|-------|--------|
| gap_pct_min | 5% | MOMENTUM_LOGIC.md |
| rvol_premarket_min | 2.0x | MOMENTUM_LOGIC.md |
| price_min | $3.00 | D101 (standard path) |
| price_min_high_volume | $0.50 | D105 (sub-$3 override) |
| price_max | $50.00 | D105 (raised from $20) |
| price_override_dollar_vol | $10M | D105 |
| price_override_rvol | 5.0x | D105 |
| absolute_volume_override | 500K | D105 |
| min_dollar_volume | $5M | D101 |

### Scoring
| Parameter | Value | Source |
|-----------|-------|--------|
| MFCS buy threshold | 0.25 | D101 bipolar |
| Confidence deflation | **0.70x** | **D106 WS1** (was 0.6x) |
| News weight | 0.35 | D101 |
| Technical weight | 0.30 | D101 |
| RVOL weight | 0.20 | D101 |
| Fundamental weight | 0.15 | D101 |
| Risk lambda | 0.15 | Production |
| Exit threshold | **0.40** | **D106 WS1** (was 0.60) |
| Exit tighten threshold | **0.15** | **D106 WS1** (was 0.20) |

### Execution
| Parameter | Value | Source |
|-----------|-------|--------|
| Max positions | 8 | Competition mode |
| Max position % | 15% | Competition mode |
| Risk per trade | 1% | D101 fixed-risk |
| Initial stop (ATR mult) | 2.0x | D100 |
| Initial stop (floor) | 4% | D100 |
| Chandelier (ATR mult) | 4.0x | D89 |
| Daily loss limit | -10% | Competition mode |
| Stale entry cutoff | 10:30 AM | D97 |

### Macro Regime (D104)
| Parameter | Value | Source |
|-----------|-------|--------|
| VIX block | > 35 | D101 |
| VIX reduce | > 25 | D101 |
| VIX shock delta | > 15% | D104 |
| SPY halt | < -1.0% | D104 |
| Confidence decay (bear) | 0.85x | D104 |

---

## 9. Roadmap: Path to Extreme Profitability

### Phase A: Immediate Fixes (Days 16-20) — **D106 WS1 COMPLETE**
**Goal**: First profitable systematic exits

1. ✅ Lower exit intelligence threshold (0.60 → **0.40**) — **DONE D106 WS1**
2. ✅ Lower exit tighten threshold (0.20 → **0.15**) — **DONE D106 WS1**
3. ✅ Remove VWAP gate for first 5 minutes post-open — **DONE D106 WS1**
4. ✅ Fix VWAP fallback (None instead of price*0.98) — **DONE D106 WS1**
5. ✅ Raise confidence deflation (0.6x → **0.70x**) — **DONE D106 WS1**
6. ✅ Pre-compute full **5-agent** MFCS at 9:20 AM — **DONE D106 WS1**
7. ✅ Smart cache bypass at market open (~45s saved) — **DONE D106 WS1**
8. Add exit signal logging for live positions
9. Widen ATR stop multiplier for gapping stocks (2.0x → 2.5x on gap > 10%)
10. Implement MFCS-scaled position sizing

### Phase A.5: Manipulation Detection — **DONE D106 WS2+WS3**
**Goal**: Detect market manipulation and strategize exits

1. ✅ ManipulationPhase + ManipulationSignal models — **DONE D106 WS2**
2. ✅ ManipulationClassifier agent (Tier 1 Qwen3.5-397B) — **DONE D106 WS2**
3. ✅ build_manipulation_filing_summary() in SEC client — **DONE D106 WS2**
4. ✅ Parameter modification layer (PROMO_LATE→NO_TRADE, PROMO_EARLY→half/tight/aggressive) — **DONE D106 WS2**
5. ✅ Agent registration + replay/recording support — **DONE D106 WS2**
6. ✅ DistributionDetector (7 weighted deterministic signals, <1ms) — **DONE D106 WS3**
7. ✅ Exit intelligence integration (distribution_detector 0.15 wt, dead signals zeroed) — **DONE D106 WS3**
8. ✅ ManagedPosition manipulation_phase + hard_exit_time fields — **DONE D106 WS3**

### Phase A.7: Analytical Infrastructure — **DONE D109**
**Goal**: Tools to validate whether the system has edge

1. ✅ Metric snapshot retention 50→200 (cross-session diagnostics) — **DONE D109**
2. ✅ Validation clamp counter `mx_state_validation_clamps_total` — **DONE D109**
3. ✅ Backtest `--null-time` (random entry timing null hypothesis, Welch's t-test) — **DONE D109**
4. ✅ Backtest `--null-filter` (buy-everything null, tests MFCS filtering value) — **DONE D109**
5. ✅ Backtest `--anti-signal` (accepted vs rejected candidates, both long-only) — **DONE D109**
6. ✅ MFE/MAE tracking per trade (Exit Autopsy Phase 1: "are stops too tight?") — **DONE D109**
7. ✅ SQLite post-session ETL (`scripts/etl_sqlite.py`, 6 tables, 3 views) — **DONE D109**
8. ✅ Idempotent JSONL→SQLite loading with `_etl_loaded_files` tracking — **DONE D109**

### Phase A.9: Parallel Exit Strategies — **DONE D109 Phase 4**
**Goal**: Independent exit strategies that fire without consensus

1. ✅ VelocityEngine (3-min rolling velocity, 4 time-based phases) — **DONE D109**
2. ✅ PullbackClassifier (stateful state machine, relative thresholds) — **DONE D109**
3. ✅ VolumeExhaustionStrategy (entry-bar volume ratio) — **DONE D109**
4. ✅ GratitudeExitStrategy (time-decaying R-multiple, 0.75R floor) — **DONE D109**
5. ✅ ParallelExitEngine orchestrator (any-of, per-ticker state, auto-prune) — **DONE D109**
6. ✅ Freeze-safe logging (compute+log, no trading impact) — **DONE D109**
7. ✅ Nested JSONL signal history extension — **DONE D109**
8. ⬚ **Post-freeze**: Activate `parallel_strategies_active=True` for any-of override

### Phase A.10: Exit Intelligence Innovations — **DONE D110**
**Goal**: Information-side exit timing, cross-position correlation, alpha measurement

1. ✅ CatalystHalfLifeStrategy — per-catalyst shelf life with velocity gate — **DONE D110**
2. ✅ AlphaDecayOracle v1 — observed vs null return, single aggregate curve — **DONE D110**
3. ✅ ContagionNetwork — cross-position TIGHTEN propagation, 10-min decay — **DONE D110**
4. ✅ Catalyst concentration limit — max 2 same catalyst_type at entry — **DONE D110**
5. ✅ ParallelExitEngine extended to 6 strategies (was 4) — **DONE D110**
6. ⬚ **Post-freeze**: Activate `parallel_strategies_active=True` for any-of override
7. ⬚ **Deferred**: Adversarial Self-Play (premature — can't harden parameters that produce zero exits)
8. ⬚ **Deferred**: Liquidity Topology (exit timing > stop placement; noisy on sub-$3)

### Phase A.11: Adaptive Compute Router — **DONE D112**
**Goal**: Reduce per-cycle evaluation latency by routing obvious candidates to instant/deterministic paths

1. ✅ 3-tier routing: INSTANT_REJECT (<1ms), DETERMINISTIC_ONLY (<100ms), FULL_PIPELINE (15-30s) — **DONE D112**
2. ✅ Tier 1 hard filters: RVOL, price, float, gap, 424B5 dilution, corporate actions, pump pattern — **DONE D112**
3. ✅ Pump pattern detection: >30% gap on sub-$3 → INSTANT_REJECT unless strong cached catalyst (FDA/earnings) — **DONE D112**
4. ✅ Tier 2 reuses D107 deterministic-only path — no new evaluation code paths — **DONE D112**
5. ✅ Prometheus metrics + journal logging for post-session routing analysis — **DONE D112**
6. ⬚ **Post-freeze**: Tune tier thresholds based on accumulated routing logs (did any Tier 1/2 rejects deserve full eval?)

### Phase A.12: Reliability & Signal Broadening — **DONE D113+D114**
**Goal**: System stability and broader catalyst recognition

1. ✅ Heartbeat pulse calls in Phase 1, Phase 2, and main loop — prevents timeout crash during long evaluations — **DONE D113**
2. ✅ CORPORATE_UPDATE and SECTOR_CATALYST catalyst types added to news agent — **DONE D114**
3. ✅ NEUTRAL override softened for gap-and-go trading (minor catalysts cap at BULL/0.65) — **DONE D114**
4. ✅ Paper trading day-to-day decision log (`docs/PAPER_TRADING_LOG.md`) — **DONE D114**

### Phase A.13: Tiered Kelly Criterion Position Sizing — **DONE D115**
**Goal**: Scale risk allocation by conviction level — size up on high-conviction setups

1. ✅ KellyTierClassifier (4-tier: Standard/High Conviction/Exceptional/Statistical Outlier) — **DONE D115**
2. ✅ TradeResultTracker (JSONL-backed rolling win rate for tier classification) — **DONE D115**
3. ✅ KellyTierConfig (30+ configurable parameters, `enabled=False` shadow mode default) — **DONE D115**
4. ✅ Orchestrator integration (classify before sizing, log tier in journal) — **DONE D115**
5. ✅ Executor integration (per-trade risk_per_trade_pct from Kelly tier) — **DONE D115**
6. ✅ Bridge integration (record TradeResult on close for win rate tracking) — **DONE D115**
7. ✅ Safety guardrails (max 2 Tier 3+/day, sequential lockout, budget downgrade) — **DONE D115**
8. ⬚ **Post-observation**: Activate `KELLY_ENABLED=true` after 5+ shadow-mode sessions

### Phase B: Entry Optimization (Days 20-30)
**Goal**: Capture opening momentum

1. ✅ Pre-compute full 5-agent MFCS at 9:20 AM — **DONE D106 WS1**
2. ✅ Adaptive Compute Router — 40-70% candidates instant/deterministic — **DONE D112**
3. Decouple fast-path from full eval
4. Cap post-open LLM calls to 10s with pre-market fallback
5. Dynamic spread filter based on price tier
6. Sector concentration enforcement

> **🚦 Phase A → B Gate**: At least **1 tranche exit** has fired in live trading (proving exit intelligence works end-to-end). Without a single systematic exit, optimizing entry timing is premature.

### Phase C: Calibration (Days 30-50, 30+ trades)
**Goal**: Data-driven parameter optimization

1. Analyze D102 experiment journal data
2. Run ATR multiplier sweep analysis
3. Calibrate exit intelligence thresholds from real signal distributions
4. Begin MWU weight optimization
5. Confidence deflation per-agent differentiation

> **🚦 Phase B → C Gate**: Win rate **> 20%** over **20+ trades**. If the system can't win 1 in 5 trades with optimized entries, parameter calibration won't help — the fundamental thesis needs re-examination.

### Phase D: Advanced Optimization (Days 50-100, 100+ trades)
**Goal**: Systematic alpha generation

1. Platt scaling calibration (replace 0.6x with empirical curve)
2. Shapley attribution → agent weight rebalancing
3. Prompt Arena activation (Elo-based variant selection)
4. Walk-forward optimization with CPCV
5. Re-evaluate debate engine with calibrated thresholds

> **🚦 Phase C → D Gate**: **Positive expectancy** ($ per trade) over a rolling 30-trade window. Advanced optimization requires a system that makes money on average — otherwise we're optimizing noise.

### Phase E: Scale (Days 100+)
**Goal**: Compound returns

1. Transition from paper to live (after PBO < 0.10 gate)
2. Multi-strategy allocation (momentum + mean-reversion)
3. Cross-asset expansion (options overlay, sector hedging)
4. Automated parameter drift detection
5. Real-time Shapley rebalancing

> **🚦 Phase D → E Gate**: ALL paper trading graduation criteria met (see Section 12). Live capital deployment requires every single graduation criterion to be satisfied — no exceptions.

### ⛔ Kill Threshold
If **win rate < 15%** after **50 trades**, **HALT all development**. Re-evaluate fundamental strategy viability before any further code changes. At that point, the question is not "how do we fix the system?" but "is momentum gap-up trading the right strategy?"

---

## 10. Key Metrics to Track

### Daily Dashboard
- **Win Rate**: Target > 55% (currently 0% systematic)
- **Average Winner / Average Loser**: Target > 2.0 (no data yet)
- **Profit Factor**: Target > 1.5 (backtest: 1.50)
- **Max Drawdown**: Target < 10% of peak
- **Sharpe Ratio**: Target > 2.0 annualized
- **Trades Per Day**: Target 2-5 (currently 0-6, highly variable)

### Signal Quality
- **MFCS Distribution**: Mean, std, % above threshold
- **Agent Failure Rate**: Target < 5% per LLM agent
- **Exit Intelligence Composite**: Distribution of scores on live positions
- **Fast-Path Fill Rate**: % of pre-market orders that execute

### Execution Quality
- **Slippage**: Target < 0.5% average
- **Entry Delay**: Minutes from first scan to order submission
- **Stop-Out Rate**: Target < 30% of trades
- **Tranche Exit Rate**: Target > 40% hit T1

---

## 11. Risk Assessment (Quantified — D107)

| Risk | Probability | Impact | Mitigation | Residual Risk |
|------|-------------|--------|------------|---------------|
| **Overfitting** (28 tunable params, <30 trades) | HIGH (60%) | -30% Sharpe | D107 config freeze, N-trade thresholds before optimization | MEDIUM — min 140 trades (5×params) required before parameter optimization is statistically meaningful |
| **Regime change** (momentum crash) | MEDIUM (30%) | -50% equity | VIX filter (block >35, reduce >25), VIX shock circuit breaker, SPY halt detection | LOW |
| **Liquidity vacuum** (gap against position) | LOW (10%) | -15% per position | ATR-based stops, 8-position limit, $5M daily dollar-volume gate | LOW |
| **LLM API outage** | MEDIUM (30%) | 0 trades that day | DeterministicTechnical + DeterministicRisk provide baseline; D107 deterministic-only mode | LOW |
| **Strategy crowding** | LOW (10%) | -20% capacity | Small-cap focus, early entry timing, pre-market evaluation | LOW |
| **Survivorship bias in scanner** | MEDIUM (30%) | Inflated backtested expectations | Live-only evaluation, D107 freeze data collection, D102 experiment journal | MEDIUM |
| **Untracked position from crash** | LOW (10%) | -100% of that position | D64 crash recovery, D107 orphan reconciliation, D108 post-merge validation + state backup | LOW |

### Effective Degrees of Freedom
- **28 tunable parameters × <20 trades = deeply overfit**
- Minimum N for meaningful parameter optimization: **5 × N_params = 140 trades**
- With Bonferroni correction across 22 experiment variants: significance level = **0.05/22 = 0.0023** per variant
- **Practical implication**: During the 15-day configuration freeze, expect 15-45 trades. This is sufficient for directional signals (does the system make money?) but NOT for parameter optimization.

### Strategy Capacity
- $140K equity, 8 positions at 15% each = **$21K per position**
- Sub-$50 stocks with $5M daily dollar-volume = **<0.5% market impact**
- Capacity ceiling: **~$500K** before market impact becomes a concern
- Beyond $500K: need to raise dollar-volume gate or widen price range

---

## 12. Paper Trading Graduation Criteria (D107)

**All of the following must be true** before live trading is even discussed:

| # | Criterion | Target | Measurement |
|---|-----------|--------|-------------|
| 1 | System reliability | > 90% | ≥9/10 trading days with ≥1 executed trade (D107 reliability score) |
| 2 | Win rate | > 30% | Over 50+ systematic trades (trades with full pipeline evaluation) |
| 3 | Per-trade expectancy | > $0 | Positive $ per trade over rolling 30-trade window |
| 4 | Tranche exits completed | ≥ 10 | At least 10 tranche exit triggers confirmed in trade journal |
| 5 | Exit intelligence activity | ≥ 5 triggers | At least 5 TIGHTEN or EXIT recommendations confirmed in signal logs |
| 6 | Backtest robustness | PBO < 0.10, DSR > 0.95 | From CPCV walk-forward backtest (Phase D) |
| 7 | Configuration freeze completed | 15 trading days | No parameter changes during freeze period (see `docs/CONFIGURATION_FREEZE.md`) |

**Rationale**: These criteria exist because "FIXED" ≠ "Validated". The system has had 28 root causes identified and patched, but none have been validated with statistically meaningful trade data. The graduation criteria ensure that the system has been observed, not just deployed.

---

*This document should be updated after each significant system change or every 5 trading days.*
*Last updated: D110 — catalyst half-life, alpha decay oracle, contagion network, catalyst concentration limits.*
