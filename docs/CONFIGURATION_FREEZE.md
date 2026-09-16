# Configuration Freeze — D107

**Declared**: March 13, 2026 (D107)
**Freeze Start**: Day 17 (first trading day after D107 deployment)
**Freeze End**: Day 31 (15 trading days later)
**Purpose**: Collect sufficient data on a stable configuration to answer: *Does the system have an edge?*

---

## Why This Freeze Exists

Between D100 and D106, the system received **28 parameter changes in 14 days**. No single configuration has been run for more than 2-3 trading sessions. This means:

1. **Every "fix" was applied against a backdrop of other changes**, making it impossible to attribute improvements to specific changes.
2. **The fundamental question — does the system have positive expected value? — remains unanswered** because the system was never observed long enough to generate statistically meaningful data.
3. **The development velocity itself became a risk.** The experiment framework (D102) was designed to collect data, but the parameters it was measuring changed faster than data could accumulate.

This freeze is the foundation for every subsequent optimization. Without it, the roadmap is guesswork.

---

## Frozen Parameter Snapshot

All parameters below are locked for the freeze period. These reflect the D106 WS3 configuration.

### Scanner Thresholds
| Parameter | Value | Source |
|-----------|-------|--------|
| `rvol_premarket_min` | 2.0 | D101 |
| `rvol_intraday_min` | 3.0 | D101 |
| `absolute_volume_override` | 500,000 | D105 |
| `gap_pct_min` | 0.05 (5%) | Original |
| `gap_pct_explosive` | 0.20 (20%) | Original |
| `atr_ratio_min` | 1.5 | Original |
| `price_min` | $3.00 | D101 |
| `price_max` | $50.00 | D105 |
| `price_min_high_volume` | $0.50 | D105 |
| `price_override_dollar_vol` | $10,000,000 | D105 |
| `price_override_rvol` | 5.0 | D105 |
| `min_dollar_volume` | $5,000,000 | D101 |

### Scoring Weights
| Parameter | Value | Source |
|-----------|-------|--------|
| `catalyst_news` | 0.35 | Original |
| `technical` | 0.30 | Original |
| `volume_rvol` | 0.20 | Original |
| `float_structure` | 0.15 | Original |
| `institutional` | 0.00 (disabled) | D100 |
| `deep_search` | 0.00 (disabled) | D100 |
| `risk_aversion_lambda` | 0.15 | Original |
| `mfcs_buy_threshold` | 0.25 | D101 |
| `confidence_deflation_factor` | 0.70 | D106 |
| `vix_block_threshold` | 35.0 | D101 |
| `vix_reduce_threshold` | 25.0 | D101 |
| `vix_shock_threshold_pct` | 15.0 | D104 |
| `spy_halt_threshold_pct` | -1.0 | D104 |
| `min_directional_agents` | 2 | D101 |

### Execution
| Parameter | Value | Source |
|-----------|-------|--------|
| `max_positions` | 8 | Competition |
| `max_position_pct` | 0.15 (15%) | Competition |
| `risk_per_trade_pct` | 0.01 (1%) | D101 |
| `stop_loss_pct` | 0.055 (fallback) | D94 |
| `initial_stop_atr_multiplier` | 2.0 | D100 |
| `initial_stop_floor_pct` | 0.04 (4%) | D100 |
| `max_entry_spread_pct` | 0.01 (1%) | D101 |
| `daily_loss_limit_pct` | 0.10 (10%) | Competition |

### Exit Intelligence
| Parameter | Value | Source |
|-----------|-------|--------|
| `exit_tighten_threshold` | 0.15 | D106 |
| `exit_threshold` | 0.40 | D106 |
| `trailing_stop_activation_pct` | 0.02 (2%) | D63 |
| `trailing_stop_atr_multiplier` | 2.0 | D63 |
| `trailing_stop_fallback_pct` | 0.05 (5%) | D100 |
| `chandelier_atr_multiplier` | 4.0 | D89 |

### Parallel Exit Strategies (D109+D110 — Freeze-Safe Log-Only)
| Parameter | Value | Source |
|-----------|-------|--------|
| `parallel_strategies_active` | False | D109 (compute+log only during freeze) |
| `catalyst_half_life_table` | 7 catalyst types (8-180 min) | D110 |
| `alpha_oracle_null_curve` | [(5,3.0)...(60,0.0)] | D110 |
| `contagion_decay_minutes` | 10.0 | D110 |
| `contagion_threshold` | 0.3 | D110 |

### Portfolio Risk (D110 Addition)
| Parameter | Value | Source |
|-----------|-------|--------|
| `max_same_catalyst_type` | 2 | D110 |

### Debate (Disabled)
| Parameter | Value | Source |
|-----------|-------|--------|
| `max_debate_attempts` | 0 | D100 |

---

## Exception Process

During the freeze period, the ONLY permitted code changes are:

1. **Crash fixes**: System fails to start, unhandled exception, or data corruption.
2. **D107 observability additions**: Signal history logging, reliability tracking, orphan reconciliation, heartbeat webhook — these add diagnostic capability without changing trading behavior.

**Explicitly forbidden**:
- Changing any parameter in the snapshot above
- Adding new agents or modifying agent prompts
- Adjusting scoring weights, thresholds, or deflation factors
- Modifying exit intelligence signal weights
- Changing stop-loss or position sizing logic

Each exception requires:
- Written justification (why it cannot wait until post-freeze)
- Confirmation that no trading parameters are affected
- Logged in this document under "Freeze Exceptions" section

---

## Data Collection Checklist (Daily)

During each trading session, the following data should be collected:

- [ ] **Signal history JSONL** (D107 WS1): `data/signal_history/signal_log_{DATE}.jsonl` — all 13 exit signal values per position per cycle
- [ ] **Session report** (existing): Trade count, P&L, entries, exits, rejections
- [ ] **Experiment journal** (D102): `data/experiments/experiment_journal_{DATE}.jsonl` — 22 variant data points per evaluation
- [ ] **Trade journal** (existing): Full entry/exit details with signal scores
- [ ] **Reliability score** (D107 WS3): Accessible via `/status` endpoint
- [ ] **Heartbeat webhook** (D107 WS4): External liveness monitoring confirms system ran

---

## Backtest to Run During Freeze

**Entry-delay sweep** (requires zero live data, run once):
```bash
for N in 0 1 2 5 10 15 30; do
    python scripts/backtest.py --entry-delay $N --days 90 --csv results_delay_${N}.csv
done
```

This directly answers whether late entry is the primary profitability blocker. If expectancy degrades sharply with delay, the fast-path and cache bypass (D106 WS1) are critical. If expectancy is flat across delays, the problem is elsewhere.

---

## Post-Freeze Analysis Plan

After 15 trading days, perform the following analysis before making ANY parameter changes:

### 1. Signal Efficacy Report
For each of the 13 exit signals, using signal history JSONL:
- What percentage of cycles does the signal exceed 0.1?
- What is the mean/max/p95 value across all cycles?
- Does the signal correlate with subsequent adverse price movement?
- **Kill any signal that never exceeds 0.1 or shows zero correlation.**

### 2. System Performance
- Win rate (target: >25% to justify continued development)
- Per-trade expectancy ($)
- Maximum drawdown
- Reliability score (target: >80%)

### 3. Statistical Requirements for Parameter Changes
- **Minimum N-trades**: 5 x N_parameters = 140 trades before parameter optimization is statistically meaningful
- **Bonferroni correction**: With 22 experiment variants, significance level = 0.05/22 = 0.0023 per variant
- **Practical implication**: During the 15-day freeze, expect 15-45 trades. This is sufficient for directional signals (does the system make money?) but NOT for parameter optimization.

### 4. Kill Threshold
If after the freeze period:
- Win rate < 15% over 20+ trades → **HALT all development**. Re-evaluate fundamental strategy viability.
- Reliability score < 50% → Focus exclusively on ops/infrastructure before any signal work.

---

## Freeze Exceptions Log

| Date | Change | Justification | Trading Parameters Affected? |
|------|--------|---------------|------------------------------|
| D108 (Mar 13) | Stop resubmission 3x retry with exponential backoff | Safety: reduces P(unprotected position) from P(fail) to P(fail)³ | **No** — retry logic only, no parameter change |
| D108 (Mar 13) | State file .bak rotation + post-merge validation | Safety: crash recovery, prevents corrupt state from causing trades | **No** — persistence layer only |
| D108 (Mar 13) | Circuit breaker exponential backoff (cap 300s) | Safety: prevents aggressive probing after repeated failures | **No** — backoff timing only |
| D108 (Mar 13) | Metric disk snapshots (200-file rotation) | Observability: preserves cross-session diagnostics | **No** — monitoring only |
| D109 (Mar 14) | Backtest analytical extensions (--null-time, --null-filter, --anti-signal) | Analytics: validates edge existence, no production impact | **No** — offline scripts only |
| D109 (Mar 14) | SQLite post-session ETL (6 tables, 3 views) | Analytics: structured query capability for trade data | **No** — post-session analysis only |
| D109 (Mar 14) | MFE/MAE tracking per trade | Analytics: "are stops too tight?" data collection | **No** — logging only |
| D109 (Mar 14) | Metric snapshot retention 50→200 | Observability: more cross-session diagnostic data | **No** — retention count only |
| D109 (Mar 14) | ParallelExitEngine (4 strategies) | Exit intelligence: compute+log only (`parallel_strategies_active=False`) | **No** — freeze-safe, log-only mode |
| D110 (Mar 15) | CatalystHalfLifeStrategy | Exit intelligence: compute+log only, 5th parallel strategy | **No** — freeze-safe, log-only mode |
| D110 (Mar 15) | AlphaDecayOracle v1 | Exit intelligence: compute+log only, 6th parallel strategy | **No** — freeze-safe, log-only mode |
| D110 (Mar 15) | ContagionNetwork | Exit intelligence: cross-position TIGHTEN propagation, log-only | **No** — freeze-safe, log-only mode |
| D110 (Mar 15) | Catalyst concentration limit (max 2 same type) | Portfolio risk: adds check at entry time | **Yes** — new entry gate. However, this strengthens risk controls (blocks entries, never allows additional ones). Conservative direction only. |
| D111 (Mar 15) | D110 config params wired to ParallelExitEngine | Bug fix: half-life table, null curve, contagion params were defined but never forwarded to engine | **No** — engine was using hardcoded defaults anyway (log-only mode) |
| D111 (Mar 15) | ExecutionBridge spread filter re-enabled | Bug fix: D101 spread filter was dead (missing client/settings refs) | **No** — restores existing safety check that was broken |
| D111 (Mar 15) | Phantom stop on full fill blocked | Bug fix: remaining=0 caused stop resubmit for qty=1 (would open short) | **No** — prevents erroneous order submission |
| D111 (Mar 15) | Stop ratchet-UP invariant enforced | Bug fix: documented but not enforced at assignment point | **No** — prevents stop from moving DOWN (safety) |
| D111 (Mar 15) | DST-aware RVOL volume profile | Bug fix: hardcoded 14:30 UTC wrong during EDT (Mar-Nov) | **No** — corrects existing calculation |
| D111 (Mar 15) | Alpha Oracle minutes_since_open fixed | Bug fix: was using hold time, now uses market time since 9:30 ET | **No** — corrects log-only calculation |
| D111 (Mar 15) | ManagedPosition D110 fields + bridge wiring | Bug fix: catalyst_type/sector/gap_pct always defaulted to unknown | **No** — data wiring for log-only strategies |
| D112 (Mar 15) | Adaptive Compute Router (3-tier evaluation routing) | Latency: routes 40-70% of candidates to instant-reject or deterministic-only path | **No** — changes evaluation SPEED, not trading decisions. MFCS threshold, stops, sizing all unchanged. Analogous to existing GEX hard filter. |
| D113 (Mar 16) | Heartbeat pulse calls in Phase 1/2/main loop | Bug fix: heartbeat timeout during long evaluations caused system crash at 05:30 ET | **No** — prevents crash, no parameter change |
| D114 (Mar 16) | News agent CORPORATE_UPDATE/SECTOR_CATALYST recognition | Signal broadening: minor catalysts cap at BULL/0.65 for gap-and-go trading | **Yes** — broadens catalyst recognition. Conservative: only adds BULL/0.65 cap (weaker than existing catalysts), addresses Day 16 BMNR miss (+12%). |
| D115 (Mar 16) | Tiered Kelly Criterion position sizing (4-tier, shadow mode) | Sizing: classifies conviction tier and logs, but uses Tier 1 values (`enabled=False`) | **No** — shadow mode only. Sizing math unchanged until `KELLY_ENABLED=true`. Same pattern as D106 parallel strategies and D112 router. |

---

## Freeze Conclusion — D122 (March 21, 2026)

### Status: ENDED

The configuration freeze is formally concluded as of D122. The freeze did not achieve its stated goal.

### What Happened

- **Declared**: D107 (March 13, 2026), intended to last 15 trading days (Day 17-31)
- **Actual stable-config trading days**: ~5 (Days 17-21)
- **Freeze exceptions logged**: 25 changes across D108-D115
- **Trading parameter changes during "freeze"**: At least 2 (D110 catalyst concentration limit, D114 news agent broadening)

### Why It Failed

1. **"Log-only" became a loophole.** The exception process allowed any feature that claimed "compute+log only" or "conservative direction." This produced 6 new parallel exit strategies, a 3-tier compute router, a Kelly sizing framework, and catalyst concentration limits — all during a supposed freeze. The system changed more during the freeze than in the 3 days before it.

2. **Exception gates were too soft.** Every exception used the same justification pattern: "no trading parameters affected" or "strengthens risk controls." But adding entry gates (catalyst concentration), broadening signal recognition (news agent), and changing evaluation routing (adaptive compute) all alter system behavior, even if individual parameter values didn't change.

3. **The fundamental question remains unanswered.** "Does the system have positive expected value?" requires stable-configuration data with sufficient trades. We got ~5 days of partially-stable operation with zero systematic exits. The data is insufficient for statistical conclusions.

### Lessons for Future Freezes

- A freeze with 25 exceptions is not a freeze — it's feature development with extra bureaucracy
- "Log-only mode" is feature development, not observability
- Hard gates: zero code changes except crash fixes (system literally won't start)
- If you need to add features, end the freeze honestly and start a new phase

### What's Next: D122 Diagnostics-Driven Development

Instead of another freeze, D122 adopts a **diagnostics-first** approach:
1. Run the entry-delay sweep and null hypothesis tests (defined in this document, never executed)
2. Analyze 10 days of experiment journal data (collected but never queried)
3. Analyze 3 days of parallel strategy signal logs
4. Let the diagnostic results determine which code changes are warranted
5. Monday trading provides the real data — more valuable than any further code sweep
