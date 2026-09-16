# D200: Arena Elevation + 7 Profitability Experiments

## Context

57.7 percentage point gap between backtest (64.8% win rate) and live (7.1%). The arena previously only tested scanner filters (5% of decision surface). D200 expands it to measure the full pipeline and runs 7 experiments to identify root causes.

## Arena Elevation: Decision Quality Arena

**File**: `src/arena/decision_quality.py` + `scripts/run_decision_arena.py`

Measures what the Selection Arena never did:
- Per-agent accuracy (when agent says BULL, does stock actually win?)
- MFCS calibration (when MFCS=0.7, does stock win 70%?)
- Catalyst quality (win rate by catalyst type)
- Debate impact (does debate improve or degrade verdicts?)

## Experiment Results

### E4: Catalyst Confirmation Gate — HIGHEST IMPACT

**70% of all BUY verdicts had NO confirmed catalyst.** These account for $243K in losses with 0% win rate. Stocks WITH confirmed catalyst have 7% win rate.

**Action**: Set `UNIVERSE_REQUIRE_CATALYST=true` in `.env`

### E5: Anti-Signal / Null Hypothesis

| Variant | Trades | Win% | Avg Return |
|---|---|---|---|
| Buy Everything (no filter) | 196 | 43% | -0.74% |
| Random Signals | 12 | 43% | -0.52% |
| News Agent Only | 93 | 71% | +7.00% |
| Technical Agent Only | 92 | 73% | +7.83% |

**Single agents outperform the full 6-agent pipeline.** The multi-agent consensus is diluting strong signals from news and technical agents.

### E3: Regime-Conditional Strategy

| Regime | Win% | Avg Return | Trade? |
|---|---|---|---|
| VIX < 15 | 48% | +2.5% | YES |
| VIX 15-20 | 42% | -2.4% | NO |
| VIX 20-30 | 18% | -9.1% | NO |
| Monday | 57% | +3.7% | YES |
| Thursday | 59% | +4.7% | YES |
| Tuesday | 30% | -3.7% | NO |
| Friday | 33% | -6.5% | NO |

**Trade only when VIX < 15 on Mon/Thu** for highest expected value.

### E2: Entry Timing Optimization

Best entry: **9:35 ET** (+3.54% avg P&L, 65% win rate). Entering at 9:30 drops to +0.33% and 48% win rate. After 10:15, returns go negative.

### E7: Time-of-Day Exit Curve

Gap-ups peak late (52% of stocks peak between 1:00-4:00 PM). Holding to EOD is justified. A 30-minute hold captures only +0.40% vs +13% at EOD.

### E6: Kelly Recalibration

Kelly fraction = -12.5. Kelly correctly says "don't bet" given 7% win rate. Higher MFCS does NOT predict better outcomes (0% win rate across all MFCS buckets). **Kelly is not the problem — signal quality is.**

### E1: LLM Decision Replay (via Decision Quality Arena)

- Debate engine: 0% win rate with debate, 3.9% without. **Debate is harmful.**
- MFCS [0.4-0.6] bucket: 0% win rate, avg P&L -$1,383
- No MFCS level predicts winning trades

## Files

| File | Purpose |
|---|---|
| `src/arena/decision_quality.py` | Decision Quality Arena core |
| `src/arena/regime_analyzer.py` | Regime partitioning engine |
| `scripts/run_decision_arena.py` | Decision arena CLI |
| `scripts/experiment_catalyst_gate.py` | E4: Catalyst gate |
| `scripts/experiment_null_signal.py` | E5: Null/anti-signal |
| `scripts/experiment_regime.py` | E3: Regime analysis |
| `scripts/experiment_entry_timing.py` | E2: Entry timing |
| `scripts/experiment_exit_timing.py` | E7: Exit timing |
| `scripts/experiment_kelly.py` | E6: Kelly audit |
| `config/settings.py` | +require_catalyst field |
| `main.py` | +D200-E4 catalyst gate in eval loop |
| `tests/unit/test_d200_decision_quality.py` | 17 arena tests |
| `tests/unit/test_d200_experiments.py` | 10 experiment tests |
