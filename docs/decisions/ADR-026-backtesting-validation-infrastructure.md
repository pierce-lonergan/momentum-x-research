# ADR-026: Backtesting Validation Infrastructure (S028)

**Status**: Accepted
**Date**: 2026-02-08
**Session**: S028

## Context

Phase 3 of the advancement plan requires proving or disproving that the system
has an edge by running historical scenarios through the full agent pipeline.
This ADR covers the infrastructure built to support that validation.

The prior session (S027) completed data pipeline wiring so agents receive real
data. This session builds the tooling to:
1. Collect historical gap scenarios from Alpaca
2. Run them through the agent pipeline
3. Measure agent calibration
4. Feed results into CPCV for acceptance gating

## Decisions

### D1: ScenarioBuilder

- **File**: `src/data/scenario_builder.py` (NEW, ~350 lines)
- `GapScenario` dataclass: ticker, date, OHLCV, prev_close, prev_volume +
  computed gap_pct, rvol, intraday_return, outcome (WIN/LOSS)
- `ScenarioDatabase`: collection with summary stats, save/load JSON, to_backtest_arrays()
- `ScenarioBuilder`: async scanner fetches daily bars from Alpaca for ~50
  momentum-prone tickers, identifies gap days >= threshold
- `MOMENTUM_UNIVERSE`: curated list of biotech, meme, EV, AI, crypto, China-ADR,
  cannabis, fintech tickers known for explosive gap behavior
- **Tests**: 17 tests in `tests/unit/test_scenario_builder.py`

### D2: ScenarioAgentRecorder

- **File**: `src/data/scenario_recorder.py` (NEW, ~360 lines)
- `ScenarioResult`: verdict_action, mfcs_score, agent_signals, correctness
- `RecordingReport`: session summary with buy/no_trade counts, accuracy, save/load
- `ScenarioAgentRecorder`: converts scenarios to CandidateStock (pre-open data
  only, correct Literal types), optionally fetches historical technical data and
  news, runs through `Orchestrator.evaluate_candidate()`
- `results_to_backtest_arrays()`: converts results to (signals, returns) for CPCV
- **Tests**: 14 tests in `tests/unit/test_scenario_recorder.py`

### D3: AgentCalibration

- **File**: `src/analysis/agent_calibration.py` (NEW, ~260 lines)
- Per-agent calibration: confidence bins, buy accuracy, Spearman rank
  correlation between confidence and return, mean absolute calibration error
- `CalibrationSuite`: aggregates per-agent reports with overall accuracy
- Custom `_spearman_correlation()` and `_rank_array()` (no scipy dependency)
- **Tests**: 15 tests in `tests/unit/test_agent_calibration.py`

### D4: CLI Commands

- **File**: `main.py`
- `build-scenarios`: fetches historical daily bars from Alpaca, builds
  ScenarioDatabase, saves to `data/scenarios/gap_scenarios.json`
  - Flags: `--lookback` (days), `--min-gap` (pct threshold)
- `record-scenarios`: loads scenario DB, runs through full agent pipeline,
  saves recording report + backtest arrays
  - Flags: `--max-scenarios` (cap for testing)
- `backtest`: now auto-detects `data/scenarios/backtest_data.json` and loads
  real scenario data before falling back to synthetic

### D5: Backtest Integration

- `cmd_backtest` priority order:
  1. Scenario-based (if `data/scenarios/backtest_data.json` exists)
  2. Multi-ticker historical (if `--tickers` provided)
  3. Single-ticker historical (if `--ticker` provided)
  4. Synthetic (default fallback)

## Validation Workflow

```
# Step 1: Build scenario database (~5 min, needs Alpaca API keys)
python -m main build-scenarios --lookback 540 --min-gap 0.10

# Step 2: Record agent responses (~30-60 min, needs LLM API keys)
python -m main record-scenarios --max-scenarios 50

# Step 3: Run CPCV backtest with real data
python -m main backtest

# Decision gate: PBO < 0.10 AND DSR > 0.95
# If REJECTED → redesign before Phase 4
# If ACCEPTED → proceed to paper trading
```

## Impact

| Component | Before S028 | After S028 |
|-----------|------------|------------|
| Scenario DB | Not implemented | ScenarioBuilder + 50 ticker universe |
| Agent Recording | CachedAgentWrapper existed but unused | Full ScenarioAgentRecorder pipeline |
| Calibration | Not measured | Per-agent calibration bins + correlation |
| Backtest Data | Synthetic only | Auto-loads scenario data when available |
| CLI | 5 commands | 7 commands (+ build-scenarios, record-scenarios) |

## S029 Fixes

### D6: litellm Provider Compatibility

- **Problem**: Together AI rejects `response_format={"type": "json_object"}` for
  Qwen 2.5 and DeepSeek R1 models → all agents fail with `UnsupportedParamsError`
- **Fix**: Set `litellm.drop_params = True` in `src/agents/base.py` and
  `src/agents/debate_engine.py`. LiteLLM silently drops unsupported params.
- **Safety**: `_extract_json()` already handles DeepSeek R1 `<think>` blocks
  and markdown fences, so JSON parsing works without `response_format`.

### D8: load_dotenv for litellm API Key Discovery

- **Problem**: `AuthenticationError: TOGETHER_AI_API_KEY environment variable not set`.
  pydantic-settings reads `.env` into Settings fields but does NOT export unmatched
  keys (like `TOGETHER_AI_API_KEY`) to `os.environ`. litellm discovers API keys
  via `os.environ`, not via Settings objects.
- **Fix**: Added `load_dotenv()` in `main.py` before `Settings()` initialization.
  Uses `python-dotenv` (already a dependency) to populate `os.environ` from `.env`.
  `override=False` ensures shell-exported vars take precedence.

### D7: Scenario Data Quality Filter

- **Problem**: `avg_gap_pct: 267.4%` from build-scenarios — extreme penny stock
  gaps (FFIE, MULN) with 1000%+ moves skew statistics.
- **Fix**: Added `max_gap_pct` parameter (default 5.0 = 500%) to
  `ScenarioBuilder.build()` and `_scan_ticker()`. Filters stock-split artifacts
  and extreme anomalies.
- **CLI**: Added `--max-gap` argument to `main.py`.

### D9: Orchestrator _last_agent_signals Storage

- **Problem**: `ScenarioAgentRecorder._record_single()` checks
  `hasattr(orchestrator, "_last_agent_signals")` to capture agent signals per scenario,
  but Orchestrator never stored this attribute. Result: `agent_signals: {}` in all
  recording reports — agent data completely lost.
- **Fix**: Added `_last_agent_signals: list[AgentSignal] = []` to `Orchestrator.__init__()`
  and `self._last_agent_signals = agent_signals` after `_dispatch_agents()` returns in
  `_evaluate_candidate_inner()`.

### D10: ScenarioRecorder mfcs Field Name Mismatch

- **Problem**: `ScenarioRecorder._record_single()` used
  `getattr(verdict, "mfcs_score", 0.0)` but `TradeVerdict.mfcs` is the actual field name.
  Result: `mfcs_score: 0.0` in all recording reports even when MFCS was computed correctly.
- **Fix**: Changed to `getattr(verdict, "mfcs", 0.0)`.

### D11: Robust _extract_json for LLM Response Parsing

- **Problem**: `_extract_json()` threw `json.JSONDecodeError` when LLM responses
  contained free text around JSON, incomplete `<think>` blocks without closing tags,
  or markdown fences not at the start of the text. All such exceptions hit the
  BaseAgent error handler, silently returning NEUTRAL fallback signals.
- **Fix**: Rewrote `_extract_json()` with:
  1. Regex-based markdown fence extraction (handles fences anywhere in text)
  2. Incomplete `<think>` block handling (finds JSON after opening tag)
  3. JSON regex fallback when direct `json.loads()` fails
  4. Warning-level logging when JSON extraction fails (was silent)
- **Impact**: Both `src/agents/base.py` and `src/agents/debate_engine.py` updated.

### D12: Agent Signal Debug Logging

- **Problem**: Agent pipeline failures were completely silent — the error handler
  caught all exceptions and returned NEUTRAL signals without logging what the LLM
  actually returned or what the parsed JSON contained.
- **Fix**: Added 3 log points in `BaseAgent.analyze()`:
  1. `DEBUG`: Raw LLM response content (first 500 chars)
  2. `DEBUG`: Parsed JSON keys and signal/confidence values
  3. `INFO`: Final signal direction, confidence, and reasoning summary

### D13: Historical News Fetch — `as_of` Parameter

- **Problem**: `NewsClient.get_news_for_ticker()` had no `as_of` parameter. The recorder
  passed `as_of=scenario.date` which raised `TypeError` (caught silently at DEBUG level).
  News always returned empty for historical scenarios.
- **Root cause**: Both `_fetch_alpaca_news()` and `_fetch_finnhub_news()` hardcoded
  `datetime.now()` as the reference time. No mechanism to specify a historical date.
- **Fix**: Added `as_of: str | None = None` parameter to `get_news_for_ticker()`,
  `_fetch_alpaca_news()`, and `_fetch_finnhub_news()`. When set, fetches news from
  `(as_of - lookback_hours)` to `(as_of + 1 day)`. Alpaca `end` param added.
- **Impact**: Historical scenarios now get actual news from around the gap date.

### D14: Historical Technical Data — Daily Bars with Start/End

- **Problem**: `_fetch_technical_data()` requested 1-min bars with `end` but no `start`.
  The `iex` feed may not have 1-min historical bars for all tickers. Even with `sip`,
  fetching 200 1-min bars without a `start` date returns bars from an arbitrary range.
- **Fix**: Switched to daily bars (`timeframe="1Day"`) with explicit `start` (90 days
  before scenario date) and `end` (scenario date). Daily bars are reliably available
  for all tickers on both `iex` and `sip` feeds. Provides enough data for RSI(14),
  MACD(26), Bollinger(20), and support/resistance calculations.
- **Impact**: Technical agent now receives real price history and computed indicators.

### D15: Data Fetch Error Logging Upgrade

- **Problem**: `_fetch_news()` and `_fetch_technical_data()` logged failures at `DEBUG`
  level, making data pipeline failures completely invisible in normal operation.
- **Fix**: Upgraded to `WARNING` level. Added success logging at `DEBUG` level with
  counts of bars and news items retrieved.

## S031 Fixes

### D16: Deterministic RVOL Score for volume_rvol Weight Category

- **Problem**: `_default_weights()` allocates 20% weight to `volume_rvol`, but no agent
  maps to this category. The 5 analytical agents cover `catalyst_news`, `technical`,
  `float_structure`, `institutional`, and `deep_search` — but `volume_rvol` has no
  corresponding agent. Result: 20% of MFCS scoring capacity was always 0.0.
- **Fix**: Added deterministic RVOL score computation in `compute_mfcs()` directly from
  `candidate.rvol`. Uses empirical thresholds:
  - RVOL < 1.0 → 0.0 (below average volume)
  - RVOL 1-2 → 0.2-0.4 (mildly elevated, linear interpolation)
  - RVOL 2-4 → 0.4-0.7 (strong demand signal)
  - RVOL > 4 → 0.7-1.0 (explosive demand, capped at 1.0)
- **Rationale**: RVOL is a pure quantitative signal from the scanner — no LLM needed.
  Only fills `volume_rvol` if no agent signal already occupies that category (future-proof).
- **Impact**: GME (rvol=4.31) component contribution went from 0.0 to 0.143.

### D17: Risk Agent NEUTRAL Fallback Produced Near-Maximum Penalty

- **Problem**: When the risk agent returns a base `AgentSignal` instead of `RiskSignal`
  (e.g., from error handler fallback), the scoring formula was:
  `risk_score = 1.0 - signal_to_score(signal)`.
  For NEUTRAL/0.35: `score = 0.4 × 0.35 = 0.14` → `risk_score = 0.86` →
  `penalty = λ × 0.86 = 0.258`. This near-maximum penalty wiped out almost all
  weighted sum, making MFCS ≈ 0 for most scenarios.
- **Root cause**: The inversion formula `1.0 - (direction × confidence)` doesn't align
  with `RiskSignal.risk_score` semantics. A NEUTRAL risk signal with low confidence
  should mean "no strong risk detected, uncertain" (moderate ~0.5 risk), not 0.86 risk.
- **Fix**: Replaced with direct direction-to-risk mapping aligned with `RiskSignal`
  defaults:
  - STRONG_BEAR → 0.9 (high risk)
  - BEAR → 0.7 (elevated)
  - NEUTRAL → 0.5 (moderate, matches `RiskSignal.risk_score` default)
  - BULL → 0.3 (low risk)
  - STRONG_BULL → 0.1 (minimal risk)
- **Impact**: NEUTRAL/0.35 risk penalty drops from 0.258 to 0.150. GME MFCS goes from
  0.012 to 0.200 (with RVOL fix). Scores are now mathematically coherent and the
  risk penalty scales proportionally to actual risk assessment.

### D18: Configurable BUY Threshold (`mfcs_buy_threshold`)

- **Problem**: `_build_trade_verdict` hardcoded `scored.mfcs > 0.5` for BUY action.
  With current data coverage (3/6 agents have real data), max achievable MFCS is ~0.20.
  The system produced 0 BUY verdicts across all scenarios, making CPCV backtesting
  meaningless (no signal variance to analyze).
- **Root cause**: The 0.5 threshold was designed for a fully-operational system with all
  6 agents providing high-quality signals. With partial data coverage, the MFCS range
  is compressed to [0.0, ~0.20].
- **Fix**: Added `mfcs_buy_threshold` field to `ScoringWeights` in settings.py
  (default 0.10 for Phase 3). Orchestrator reads `self._settings.scoring.mfcs_buy_threshold`
  instead of hardcoded 0.5. Production target: raise to 0.5 once all agents have real data.
- **Impact**: GME (MFCS=0.126) now produces a BUY verdict with debate. Other scenarios
  with lower MFCS still get HOLD.

### D19: Phase 3 Debate Threshold Calibration

- **Problem**: `mfcs_debate_threshold` was 0.6, unreachable with current MFCS range.
  The debate engine was never exercised during backtesting, leaving a critical pipeline
  component untested in production conditions.
- **Fix**: Lowered default `mfcs_debate_threshold` from 0.6 to 0.15 in settings.py.
  This allows candidates with strong technical + RVOL signals to trigger debate.
  Production target: raise back to 0.6 once all agents have real data.
- **Note**: Both thresholds are env-configurable via `DEBATE_MFCS_DEBATE_THRESHOLD`
  and `SCORE_MFCS_BUY_THRESHOLD`.

### D20: Null Safety — Debate Engine Result Check

- **Problem**: `_evaluate_candidate_inner()` accessed `debate_result.verdict` without
  null-checking after `run_debate()`. If the debate engine returns `None` (error,
  timeout, mock), the orchestrator crashed with `AttributeError: 'NoneType' object
  has no attribute 'verdict'`. Previously hidden because debate threshold (0.6) was
  never reached.
- **Fix**: Added `if debate_result is not None:` guard around all debate result
  access. When debate returns None, logs a warning and proceeds without debate
  (falls through to risk veto check and trade verdict).
- **Impact**: Fixes crash in orchestrator_context test, also makes the system resilient
  to debate engine failures in production.

## S032 Fixes — CPCV Pipeline Unblock

### Root Cause Analysis

The 10-scenario recording run (S031) showed 1 BUY, 70% accuracy, avg_mfcs=0.027.
Running `backtest` produced `PBO=1.000, DSR=0.500, REJECTED` — but this was due to
3 compounding bugs, not a failing strategy:
1. All 15 CPCV folds marked contaminated (model-ID format mismatch)
2. News agent returned NEUTRAL for 60% of scenarios (timezone bug)
3. Backtest date range defaulted to 2025, but scenarios span 2023-2024

### D21: News Timezone Bug — UTC vs Eastern Time

- **Problem**: `_fetch_alpaca_news()` and `_fetch_finnhub_news()` created reference
  timestamps as `T09:30:00+00:00` (9:30 UTC). US market open is 9:30 AM ET, which
  equals 14:30 UTC (winter) or 13:30 UTC (summer). The 48-hour lookback window was
  shifted 5 hours early, missing pre-market news catalysts.
- **Evidence**: 6/10 scenarios had `news_agent: NEUTRAL/0.0, reasoning: ""`. PDD got
  news (earnings), but GME, CLSK, MRNA, MULN got nothing despite known catalysts.
- **File**: `src/data/news_client.py`
- **Fix**: Used `zoneinfo.ZoneInfo("America/New_York")` for DST-aware ET conversion
  in both `_fetch_alpaca_news()` and `_fetch_finnhub_news()`:
  ```python
  from zoneinfo import ZoneInfo
  _et = ZoneInfo("America/New_York")
  _y, _m, _d = (int(x) for x in as_of.split("-"))
  ref_time = datetime(_y, _m, _d, 9, 30, tzinfo=_et)
  ```
- **Verified**: Winter 2023-12-04 → 14:30 UTC, Summer 2024-06-15 → 13:30 UTC.
- **Impact**: News agent (30% MFCS weight) will receive actual historical news →
  higher MFCS → more BUY verdicts with real catalyst data.

### D22: Model-ID Lookup Fails for Provider-Prefixed IDs (ROOT CAUSE)

- **Problem**: `KnowledgeCutoffRegistry.get_cutoff("deepseek-ai/DeepSeek-R1")` returned
  `None`. Normalized form is `"deepseek-ai/deepseek-r1"`, but registry key is
  `"deepseek-r1"`. Neither prefix-match direction succeeds. When `get_cutoff()` returns
  `None`, `check_contamination()` returns `is_contaminated=True` with reason "Unknown
  model". ALL 15 CPCV folds were marked contaminated → PBO defaults to 1.0 →
  guaranteed REJECTED.
- **File**: `src/core/llm_leakage.py`
- **Fix**: Added provider-prefix stripping after existing prefix-match loop:
  ```python
  # Strip provider prefix: "deepseek-ai/DeepSeek-R1" → "deepseek-r1"
  if "/" in normalized:
      short_name = normalized.split("/")[-1]
      if short_name in self._cutoffs:
          return self._cutoffs[short_name]
      for key, cutoff in self._cutoffs.items():
          if short_name.startswith(key) or key.startswith(short_name):
              return cutoff
  ```
  Also added Together AI Qwen model entries to `_DEFAULT_CUTOFFS`:
  `qwen2.5-7b-instruct-turbo`, `qwen2.5-coder-32b-instruct`, etc.
- **Tests**: 3 new tests: `test_provider_prefixed_deepseek`, `test_provider_prefixed_qwen`,
  `test_provider_prefixed_qwen_coder`.
- **Impact**: Folds now checked against actual cutoff dates (DeepSeek R1: 2024-07-31)
  instead of auto-flagged as "unknown model". Scenarios after cutoff+buffer will be clean.

### D23: Dates Array in backtest_data.json + Real Backtest Date Range

- **Problem**: `backtest_data.json` had only `signals` and `returns` arrays, no dates.
  `cmd_backtest()` created `HistoricalBacktestSimulator` with default dates 2025-01-01
  to 2025-12-31, but actual scenarios span 2023-2024. The contamination detector linearly
  maps fold indices to this wrong date range, causing incorrect fold assignments.
- **Files modified (3)**:
  1. `src/data/scenario_recorder.py` — `results_to_backtest_arrays()` now returns
     3-tuple `(signals, returns, dates)` where each date is `result.scenario.date`
  2. `main.py` — `cmd_record_scenarios()` writes `dates` array to backtest_data.json
  3. `main.py` — `cmd_backtest()` reads `dates`, computes min/max, passes real
     `backtest_start`/`backtest_end` to `HistoricalBacktestSimulator`. Also
     restructured to create simulator AFTER data loading, consolidated synthetic fallback.
- **Tests**: 3 tests in `TestResultsToBacktestArrays` updated from 2-tuple to 3-tuple
  unpacking, with assertions on the dates array.
- **Impact**: Contamination detector uses real scenario dates. Scenarios after model
  cutoff+buffer (DeepSeek: 2024-08-30) produce clean folds → real PBO computation.

### D24: Cutoff-Aware Scenario Selection

- **Problem**: `scenarios[:max_scenarios]` takes the FIRST N scenarios from a
  chronologically-sorted database (oldest first). With `--max-scenarios 50`, all
  50 selected scenarios (2023-11-28 to 2024-04-09) predate DeepSeek R1's cutoff+buffer
  (2024-08-30). This is **correct contamination detection** — the model was trained
  on this data. Result: ALL 15 CPCV folds contaminated → PBO=1.000 (default).
- **Key insight**: PBO needs BOTH contaminated and clean folds to be meaningful.
  It measures whether in-sample performance generalizes out-of-sample. Zero clean
  folds means zero signal — PBO defaults to 1.0 without any actual analysis.
- **File**: `src/data/scenario_recorder.py`
- **Fix**: Added `select_scenarios_for_backtest()` function that:
  1. Looks up model cutoff via `KnowledgeCutoffRegistry`
  2. Partitions scenarios into contaminated (date ≤ buffer_end) and clean (date > buffer_end)
  3. Allocates ≥30% of slots to clean scenarios
  4. Takes **latest** contaminated + **earliest** clean (clusters around boundary)
  5. Re-sorts chronologically for correct fold construction
  Updated `ScenarioAgentRecorder.record()` to accept `model_id` parameter.
- **File**: `main.py` — passes `settings.models.tier1_model` to `recorder.record()`,
  added cutoff diagnostic logging.
- **Tests**: 8 new tests: no-cap, fewer-than-cap, spans-boundary, all-contaminated,
  all-clean, unknown-model, chronological-order, provider-prefixed-model.
- **Impact**: With 196 scenarios spanning 2023-11-28 to 2026-02-04, selecting 50
  will include ~35 contaminated + ~15 clean → 4-8 clean CPCV folds → real PBO/DSR.

### D25: Risk Veto → Advisory Mode (S033)

- **Problem**: Risk agent VETO has absolute power (orchestrator line 348), killing 62%
  of scenarios before MFCS is even evaluated. The LLM hallucinates veto conditions
  from sparse data (e.g., "bid-ask > 3%" with no actual spread data).
- **Fix**: Added configurable `risk_veto_mode` setting:
  - `"HARD"` (production): Risk VETO → immediate NO_TRADE
  - `"ADVISORY"` (Phase 3 default): Risk VETO logged, but MFCS score decides
- **Files**: `config/settings.py` (new field), `src/core/orchestrator.py` (mode check)
- **Tests**: 2 new tests — advisory mode passes through, hard mode blocks

### D26: Weight Redistribution for Absent Agents (S033)

- **Problem**: 4/6 agents (news=92%, institutional=100%, deep_search=100%,
  fundamental=88%) return empty NEUTRAL defaults. Their 60% combined weight
  contributes phantom 0.20 scores, diluting real signals from technical + RVOL.
- **Fix**: In `compute_mfcs()`:
  1. Skip NEUTRAL signals with empty reasoning (default fallback = no data)
  2. Redistribute absent agent weight proportionally to active agents
  3. `weight_boost = 1.0 / active_weight` when `active_weight < 1.0`
- **File**: `src/core/scoring.py`
- **Tests**: 5 new tests — empty excluded, real NEUTRAL included, boost increases
  score, all-active no boost, lower lambda reduces penalty
- **Impact**: With only technical (0.20) + RVOL (0.20) active, boost=2.5×.
  MFCS jumps from ~0.05 to ~0.50+ for scenarios with real technical signals.

### D27: Lower Risk Aversion Lambda (S033)

- **Problem**: λ=0.3 risk penalty can subtract up to 0.30 from MFCS. With partial
  data coverage, this is too harsh — zeroes out most scores.
- **Fix**: Default `risk_aversion_lambda=0.15` for Phase 3 (production target: 0.30)
- **File**: `config/settings.py`

### D28: Evidence-Based Risk Agent Prompt (S033)

- **Problem**: Risk agent prompt says "MANDATORY VETO CONDITIONS" without requiring
  evidence from provided data. The LLM infers conditions: "FFIE is $0.04 therefore
  bid-ask > 3%". These are hallucinated vetoes.
- **Fix**: Updated system_prompt to require data evidence for each veto condition:
  "ONLY trigger if EVIDENCED IN PROVIDED DATA". Added: "Absence of data is NOT
  evidence of risk."
- **File**: `src/agents/risk_agent.py`

### D29: Configurable Debate Divergence + Smart Override (S033b)

- **Problem**: Hardcoded `if divergence < 0.3: verdict = "NO_TRADE"` in debate_engine.py
  kills ALL debate trades with partial Phase 3 data. AMC had MFCS=0.329 but divergence=0.10
  → forced NO_TRADE. The `DebateConfig` already had threshold fields but `DebateEngine`
  never received them.
- **Fix**: Added `divergence_no_trade` and `divergence_full` params to `DebateEngine.__init__`.
  Wired from `DebateConfig` via orchestrator. Smart override: when judge says BUY but
  divergence is below threshold, allow QUARTER sizing instead of killing the trade.
  Lowered default `divergence_low_threshold` from 0.3 to 0.15 for Phase 3.
- **Files**: `src/agents/debate_engine.py`, `config/settings.py`, `src/core/orchestrator.py`

### D30: Debate Prompts Calibrated for Partial Data (S033b)

- **Problem**: Bear prompt "Be thorough and relentless" — with partial data every gap is
  ammunition. Judge weighs argument quantity not quality. This compresses divergence.
- **Fix**: Bear prompt: "Do NOT penalize the bull case for data that is unavailable."
  Judge prompt: "Weight arguments based on evidence QUALITY, not quantity."
  Added DATA QUALITY NOTE to debate context flagging agents with/without data.
- **File**: `src/agents/debate_engine.py`

### D31: LiteLLM Retry & Timeout Configuration (S033b)

- **Problem**: news_agent timed out at 361s (120s × ~3 retries). `litellm.num_retries`
  never explicitly set — LiteLLM default caused 3x wallclock.
- **Fix**: Set `litellm.num_retries = 1` in base.py and debate_engine.py. Added
  tier-specific timeout fields to ModelConfig (Tier 1: 120s, Tier 2: 60s). Wired
  timeouts into all agent constructors via orchestrator.
- **Files**: `src/agents/base.py`, `src/agents/debate_engine.py`, `config/settings.py`,
  `src/core/orchestrator.py`

### D32: Faster Model for Bull/Bear Debate (S033b)

- **Problem**: All 3 debate roles used Tier 1 (DeepSeek R1, ~30-60s). Bull/Bear don't
  need deep reasoning — they need fluent argument construction.
- **Fix**: Added `advocate_model` and `advocate_timeout` to DebateEngine. Bull/Bear use
  Tier 2 (Qwen) for ~3-5x speed. Judge stays on Tier 1 for reasoning depth.
- **Files**: `src/agents/debate_engine.py`, `src/core/orchestrator.py`

## Backtest Results (S033c — 50-Scenario Validation Run)

### Recording Results (D25-D32 applied)
- **50 scenarios** selected (35 contaminated + 15 clean, range: 2024-05-13 to 2024-11-06)
- **15 BUY verdicts** (30% BUY rate, up from 0/50 in D25-D28-only run)
- **68% overall accuracy** (buy_accuracy=60%, no_trade_accuracy=71.4%)
- **Pipeline: ~78 minutes** for 50 scenarios (~1.6 min/scenario avg)

### CPCV Backtest
- **PBO = 0.0000** (PASS, target < 0.10)
- **DSR = 0.9972** (PASS, target > 0.95)
- **Clean OOS Sharpe = 5.4692**
- **VERDICT: ACCEPTED**
- 15 paths (1 clean, 14 contaminated)
- Caveat: 1 clean fold has low statistical power — full 196 scenarios needed

### CPCV Run History
| Run | PBO | DSR | BUY | Notes |
|-----|-----|-----|-----|-------|
| 1 (S030) | 1.0 | 0.5 | ? | Unknown model → all folds contaminated |
| 2 (S031) | 1.0 | 0.5 | 1/10 | All pre-cutoff → contaminated |
| 3 (S032) | 0.0 | 0.5 | 5/50 | 1 fold, 0 trades in clean fold |
| 4 (S033) | 1.0 | 0.5 | 0/50 | D25-D28 only, debate killed all trades |
| 5 (S033c) | 0.0 | 0.997 | 15/50 | D25-D32, ACCEPTED (1 clean fold — misleading) |
| **6 (S033d)** | **1.0** | **0.0** | **73/196** | **D25-D36, REJECTED (3 clean folds — honest)** |

## S033c Observed Issues (Fixed in S033d)
- news_agent Pydantic error: `catalyst_specificity` receives whitespace `' '` from R1 → **FIXED by D33**
- Judge JSON parse failures: ~6% of debates return confidence=0.00, divergence=0.00 → **FIXED by D34**
- These reduce BUY yield by ~10-20% — fixing could improve results further

## S033d: BUY Yield + Portfolio P&L (D33-D36)

### D33: Defensive Literal Field Validators
- Added `_sanitize_literal()` helper: strip whitespace → case-insensitive match → prefix match → safe default
- Applied `@field_validator(mode="before")` on ALL 11 Literal fields across 6 Pydantic models
- Models protected: AgentSignal, NewsSignal, TechnicalSignal, RiskSignal, DebateResult, TradeVerdict
- Fixes the `catalyst_specificity=" "` bug and prevents future LLM output noise

### D34: Judge JSON Parse Failure Resilience
- Verdict-aware defaults: when verdict present but bull/bear_strength missing, estimate from verdict (BUY→0.7/0.3)
- Confidence guard: conf=0.0 + div=0.0 + no reasoning → forced NO_TRADE (prevents zero-confidence BUY)
- Added logging on all _extract_json() failure paths for debugging

### D35: Position-Size-Weighted Backtest + Portfolio Simulation
- Added `verdict_position_size_pct` to ScenarioResult
- Extended `results_to_backtest_arrays()` from 3-tuple to 4-tuple (added position_sizes)
- Added `RecordingReport.portfolio_summary()` method with full P&L simulation
- Backtest simulator uses position sizes to weight returns (FULL/HALF/QUARTER → proportional)

### D36: Enhanced Backtest Report Metrics
- Added portfolio fields to BacktestReport: final_equity, total_return_pct, max_drawdown_pct, profit_factor, win_rate_pct
- Portfolio simulation computed in backtest step 6 after CPCV
- All metrics exposed in to_dict() and logged in cmd_backtest

## Full 196-Scenario Run Results (S033d)

### Recording Results
- **196/196 recorded**, 0 failures
- **73 BUY verdicts (37%)**, 123 NO_TRADE
- Overall accuracy: 57.7%, buy_accuracy: 50.7%, no_trade_accuracy: 61.8%
- avg_mfcs: 0.193, avg_buy_confidence: 0.489
- Runtime: ~228 minutes (19:09→22:57), ~1.16 min/scenario

### Portfolio Simulation (D35/D36)
- Starting equity: $100,000
- **Final equity: $99,349.81 (-0.65%)**
- Win rate: 50.7% (37/73 trades)
- Avg win: $179.54 | Avg loss: -$202.59
- **Profit factor: 0.91** (losses exceed wins)
- Max drawdown: 3.94% ($4,020.82)

### CPCV Backtest
- **PBO = 1.0000 (FAIL)**
- **DSR = 0.0000 (FAIL)**
- **Clean OOS Sharpe = -1.4744** (negative — strategy loses money OOS)
- 15 paths: 3 clean, 12 contaminated
- **VERDICT: REJECTED**

### Key Insight
Run 5 (50 scenarios, 1 clean fold) was a **false positive ACCEPTED**. With only 1 clean fold containing very few trades, PBO=0.0 and high DSR were artifacts of insufficient statistical power, not a genuine edge. Run 6 (196 scenarios, 3 clean folds) reveals the truth: the strategy has **negative OOS returns** and the PBO gate correctly rejects it.

The rejection is **correct and expected**: 4/6 agents operate on empty data (confabulating from pre-training knowledge). Only the technical_agent and RVOL produce real signals, which alone cannot provide a trading edge. The infrastructure and validation framework work exactly as designed — the system honestly tells you "no edge detected."

### Path Forward
1. **Complete Phase 2 (Data Pipeline)**: Wire real news, technical indicators, and options data to agents
2. **Re-run Phase 3**: With agents receiving actual data, re-record scenarios and re-test
3. **Do NOT proceed to Phase 4 (Paper Trading)** until CPCV passes with real data

## Test Results (S033d)

- **702 passing**, 5 pre-existing failures (README tests, CLI analyze), 10 pre-existing errors (Grafana dashboard)
- All 56 model validator tests pass (D33 — new file)
- All 32 debate_engine tests pass (10 new for D34)
- All 27 scenario_recorder tests pass (5 new for D35/D36)
- All 30 llm_leakage tests pass
- All 14 scoring tests pass
- All 7 orchestrator_context tests pass
