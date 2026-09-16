# Strategy Arena: Tournament Findings & Deployment Recommendations

**Date**: March 21, 2026 (D120)
**System Version**: D120 (D119 arena infrastructure + D120 evolved profiles)
**Test Suite**: 1,939 tests passing

---

## 1. Executive Summary

The Strategy Simulation Arena (D119) replays historical paper trading sessions through different strategy configurations, ranks them via Elo tournament, and identifies which parameter combinations extract the most P&L. D120 extended the arena with 4 newly unlocked strategy dimensions and 10 additional profiles (6 evolved + 4 ablation) for a total of 22 profiles.

**Key finding**: The single highest-impact improvement is **MFCS scaling denominator reduction** (0.5 to 0.35), which increases position sizes on high-conviction trades. This alone produced the #1 Elo rating and +$139 more P&L than the previous champion.

**Deployment recommendation**: `aggressive_all` with `mfcs_scaling_denom=0.35` (the `ablation_sizing` profile).

---

## 2. Arena Architecture

### Two-Layer Replay

1. **Entry layer**: Recompute MFCS from stored agent signals with different weights/thresholds. Decides whether a trade would have been entered under a given configuration.
2. **Exit layer**: Replay entered trades through 1-min bars with different stop/target/trailing/exit-intelligence parameters. Computes realized P&L.

### Elo Tournament

Round-robin pairwise comparison across all sessions. Profile with higher P&L on a session wins (actual=1.0). Within 0.1% of each other = draw (actual=0.5). Elo ratings reuse `compute_expected_score()` and `update_elo()` from `src/agents/prompt_arena.py`.

### Fidelity Limitation

The journal stores agent VERDICTS (BULL/BEAR/NEUTRAL + confidence), not the raw features agents used. Re-weighting tests "what if we weighted the news agent's BULL signal more?" but CANNOT test "what if a different model analyzed this differently?" or discover trades the scanner rejected before agent evaluation.

---

## 3. Data Coverage

| Session | Date | Trades | Tickers with Bars |
|---------|------|--------|-------------------|
| 1 | 2026-02-24 | 6 | ALUR, EDSA, IOVA, LRMR, RIME, VIR |
| 2 | 2026-02-25 | 7 | CRCA, EDSA, IOVA, LRMR, NVTS, RXRX, XWEL |
| 3 | 2026-03-03 | 12 | EONR, MOBX, ONDS, RUBI, STAK, SVRN, TMDE, TPET, TURB |
| 4 | 2026-03-19 | 3 | AIM, ARTL, CHNR |
| 5 | 2026-03-20 | 2 | ANNA, MOBX |

**Total**: 5 sessions, 30 trade evaluations, 27 unique tickers with cached 1-min bars.

**Statistical caveat**: With only 5 sessions, Elo needs 30-50 matches for rough ordering. Trust TOP 2-3 and BOTTOM 2-3 rankings; mid-pack may be statistically indistinguishable. Victory is credible only if the profile outperforms in at least 4 of 5 sessions.

---

## 4. D119 Tournament Results (12 Original Profiles)

First tournament run with 12 profiles, 330 matchups:

| Rank | Profile | Elo | W/L/D | Total P&L | Key Trait |
|------|---------|-----|-------|-----------|-----------|
| 1 | `aggressive_all` | 1361 | 44/23/38 | +$2,369 | Low bar + high risk + wide targets + runner |
| 2 | `momentum_runner` | 1317 | 53/33/19 | +$4,332 | 30% runner, 12% trail |
| 3 | `wide_targets` | 1302 | 60/32/13 | +$6,453 | 10/20/40% tranches + 25% runner |
| 4 | `wide_exits` | 1242 | 50/33/22 | +$4,141 | exit=0.70, tighten=0.40, trail=0.06 |
| 5 | `technical_heavy` | 1175 | 36/27/42 | +$1,691 | technical weight=0.40 |
| 6 | `tight_exits` | 1131 | 33/53/19 | +$1,485 | exit=0.35, trail=0.02 |
| 7 | `low_bar_entry` | 1126 | 30/40/35 | +$933 | mfcs=0.15, lambda=0.10 |
| 8 | `tight_stops` | 1061 | 41/55/9 | +$1,551 | ATR 1.5x, cap 10% |
| 9 | `news_heavy` | 1054 | 11/57/37 | -$950 | catalyst_news=0.45 |
| 10 | `high_bar_entry` | 1047 | 18/43/44 | $0 | mfcs=0.35, 3+ directional |
| 11 | `conservative_all` | 1046 | 18/43/44 | $0 | High bar, low risk, no runner |
| 12 | `baseline` | 1031 | 24/58/23 | +$1,314 | Production defaults |

### Key Insights from D119

1. **Entry filtering is an edge**: `aggressive_all` won partly by REJECTING bad trades (MOBX on 2026-03-20) via its entry threshold configuration. Not entering a losing trade is as valuable as managing a winning one.

2. **Runner allocation matters**: All top 3 profiles have runners (25-30%). Baseline has runner_pct=0.0 and ranked last.

3. **Wide trailing beats tight**: `wide_exits` (trail=0.06) significantly outperformed `tight_exits` (trail=0.02). Tighter trails get stopped out by normal volatility.

4. **MFE leakage is the bottleneck**: Even the best strategy captures only ~60% of available MFE. ANNA had 12.5% MFE; best capture was 7.48%.

5. **Baseline is worst**: Production defaults (ranked 12th) are significantly worse than nearly every alternative configuration.

---

## 5. D120: Four Unlocked Dimensions

D120 identified 13 strategy dimensions that had NEVER been varied in any profile. The top 4 were implemented:

### 5.1 Trailing Stop Decoupling (Priority 1)

**Bug found**: In `scripts/replay_optimizer.py`, `trailing_activation_pct` was used as BOTH the activation threshold AND the trail distance. These are fundamentally different parameters — you want trailing to activate early (small gain) but trail wide (don't get shaken out).

**Fix**: Added `trailing_trail_distance_pct` to `SimConfig`. When `None`, falls back to `trailing_activation_pct` for backward compatibility.

**Result**: Surprisingly, decoupling HURT on this dataset. `ablation_trailing` (activate 3%, trail 8%) captured less on ANNA ($792 vs $1,030) because the wider 8% trail let the price pull back further before triggering. The current coupling was accidentally beneficial for this specific price action pattern.

### 5.2 Exit Signal Weight Overrides (Priority 2)

**Gap found**: `ExitSignalEngine` has 13 signals with fixed global weights. No profile had ever varied them.

**Fix**: Added `exit_signal_weight_overrides` to `SimConfig`. Changed `ExitSignalEngine.__init__` from replace to merge semantics (partial dict merged onto defaults).

**Result**: No impact in replay mode. The exit signal weights affect the production `ExitSignalEngine` composite score, but `simulate_trade()` uses its own trailing/tranche logic. This dimension would need the replay engine to fully integrate the exit intelligence scoring loop to show impact.

### 5.3 MFCS Scaling Denominator (Priority 3)

**Gap found**: `SimConfig.mfcs_scaling_denom` (controls position size responsiveness to MFCS score) existed but was never exposed through `StrategyProfile` or varied.

**Fix**: Added `mfcs_scaling_denom` to `StrategyProfile` and wired through `_FIELD_MAP`.

**Result**: **Highest-impact dimension.** Lowering from 0.5 to 0.35 increased position sizes on high-conviction trades. On ANNA: +$1,280 vs +$1,030 (24% more profit, same exit price). `ablation_sizing` ranked #1 overall (Elo 1450).

### 5.4 Tranche Stop Ratchet Ratio (Priority 4)

**Gap found**: After T1 fills, stop moves to `entry + (T1 - entry) * 0.25` — hardcoded. Higher ratio = more profit locked in.

**Fix**: Added `tranche_ratchet_ratio` to `SimConfig`. Default 0.25 preserves backward compatibility.

**Result**: No measurable impact on this dataset. Most profitable trades were captured by trailing stops, not tranche ratcheting. The dimension exists for future experimentation.

---

## 6. D120 Tournament Results (22 Profiles)

Full tournament with 22 profiles (12 original + 6 evolved + 4 ablation), 1,155 matchups:

| Rank | Profile | Elo | W/L/D | Total P&L | Type |
|------|---------|-----|-------|-----------|------|
| **1** | **`ablation_sizing`** | **1450** | 46/27/32 | **+$2,508** | **Ablation** |
| 2 | `ablation_signals` | 1415 | 44/23/38 | +$2,369 | Ablation |
| 3 | `aggressive_all` | 1412 | 44/23/38 | +$2,369 | Original |
| 4 | `ablation_ratchet` | 1401 | 44/23/38 | +$2,369 | Ablation |
| 5 | `ablation_trailing` | 1343 | 34/26/45 | +$1,927 | Ablation |
| 6 | `evolved_hunter` | 1336 | 34/26/45 | +$1,927 | Evolved |
| 7 | `evolved_hybrid` | 1330 | 40/26/39 | +$2,069 | Evolved |
| 8 | `evolved_holder_moderate` | 1218 | 52/33/20 | +$4,582 | Evolved |
| 9 | `evolved_holder` | 1216 | 52/33/20 | +$4,582 | Evolved |
| 10 | `momentum_runner` | 1214 | 53/33/19 | +$4,332 | Original |
| 11 | `wide_exits` | 1211 | 50/33/22 | +$4,141 | Original |
| 12 | `wide_targets` | 1173 | 60/32/13 | +$6,453 | Original |
| 13 | `evolved_sniper` | 1130 | 18/43/44 | $0 | Evolved |
| 14 | `evolved_conservative` | 1129 | 18/43/44 | $0 | Evolved |
| 15 | `conservative_all` | 1127 | 18/43/44 | $0 | Original |
| 16 | `technical_heavy` | 1115 | 36/27/42 | +$1,691 | Original |
| 17 | `high_bar_entry` | 1110 | 18/43/44 | $0 | Original |
| 18 | `low_bar_entry` | 1086 | 30/40/35 | +$933 | Original |
| 19 | `news_heavy` | 1059 | 11/57/37 | -$950 | Original |
| 20 | `tight_exits` | 1053 | 33/53/19 | +$1,485 | Original |
| 21 | `tight_stops` | 957 | 41/55/9 | +$1,551 | Original |
| 22 | `baseline` | 918 | 24/58/23 | +$1,314 | Original |

---

## 7. Ablation Analysis

Each ablation profile enables exactly ONE new dimension on the `aggressive_all` base:

| Dimension | Profile | Elo | vs aggressive_all (Elo 1412) | Finding |
|-----------|---------|-----|------------------------------|---------|
| MFCS scaling (0.35) | `ablation_sizing` | 1450 | **+38 Elo, +$139 P&L** | **Primary driver** |
| Exit signal weights | `ablation_signals` | 1415 | +3 Elo, tied P&L | No impact in replay |
| Tranche ratchet (0.40) | `ablation_ratchet` | 1401 | -11 Elo, tied P&L | No impact on this data |
| Trailing decouple (3%/8%) | `ablation_trailing` | 1343 | -69 Elo, -$442 P&L | **Hurt** — wider trail let profits slip |

### Interpretation

1. **MFCS scaling is the only dimension that improved results.** The formula `position_pct = 0.05 + 0.10 * (mfcs / denom)` becomes more responsive with denom=0.35 vs 0.50. High-conviction trades (MFCS > 0.30) get proportionally larger positions, amplifying winners.

2. **Trailing decoupling hurt because of the specific price action.** ANNA's move had a clean spike-and-pullback pattern. The tighter trail (trail=activation=0.06) caught the spike at $5.50. The wider trail (0.08) let it pull back to $5.38. This may not generalize — with more sessions, decoupling could outperform on choppy runners.

3. **Exit signal weights and ratchet ratio had no measurable effect** in the replay engine. The replay uses `simulate_trade()` which has its own trailing/tranche logic; the exit signal composite is not fully integrated into the replay path.

---

## 8. ANNA Counterfactual (Key Trade)

ANNA (2026-03-20): Entry $5.20, MFE 12.5% ($5.85)

| Profile | Exit Price | Reason | P&L | Hold | MFE Capture |
|---------|-----------|--------|-----|------|-------------|
| `ablation_sizing` | $5.50 | trailing_stop | **+$1,280** | 18m | 46% |
| `aggressive_all` | $5.50 | trailing_stop | +$1,030 | 18m | 46% |
| `momentum_runner` | $5.52 | trailing_stop | +$968 | 13m | 49% |
| `evolved_holder` | $5.54 | trailing_stop | +$993 | 14m | 52% |
| `evolved_hunter` | $5.38 | trailing_stop | +$792 | 20m | 28% |
| `wide_targets` | $5.46 | trailing_stop | +$696 | 9m | 40% |
| `baseline` | $5.21 | trailing_stop | +$34 | 4m | 2% |

**Why `ablation_sizing` won**: Same exit price as `aggressive_all` ($5.50), same exit reason (trailing_stop at 18 min). But 24% more profit because the lower MFCS scaling denom allocated a larger position to this high-conviction trade.

---

## 9. Deployment Decision

### Recommended: `aggressive_all` + `mfcs_scaling_denom=0.35`

This is the `ablation_sizing` profile — the tournament champion. The change is minimal (one parameter value) with the highest demonstrated lift:

| Parameter | Production Default | Deployed Value |
|-----------|-------------------|----------------|
| `mfcs_buy_threshold` | 0.25 | 0.15 |
| `risk_aversion_lambda` | 0.15 | 0.10 |
| `risk_per_trade_pct` | 0.01 | 0.02 |
| `max_position_pct` | 0.15 | 0.25 |
| `kelly_enabled` | True | True |
| `tier2_min_mfcs` | 0.25 | 0.20 |
| `tier2_risk_pct` | 0.02 | 0.03 |
| `tier2_max_position_pct` | 0.25 | 0.30 |
| `mfcs_scaling_denom` | 0.50 | **0.35** |
| `tranche_targets` | (0.05, 0.10, 0.20) | (0.08, 0.15, 0.30) |
| `exit_threshold` | 0.60 | 0.70 |
| `tighten_threshold` | 0.30 | 0.35 |
| `runner_pct` | 0.00 | 0.25 |
| `trailing_activation_pct` | 0.04 | 0.06 |

### Why NOT the other top contenders

- **`wide_targets`** (+$6,453 raw P&L): Highest raw P&L but more variance and lower Elo (1173). Riskier for single-session deployment.
- **`momentum_runner`** (+$4,332): Depends on runners materializing, which is session-dependent.
- **`evolved_hybrid`** (all 4 dimensions): Trailing decoupling component actively hurt. Uninterpretable if it wins because multiple dimensions interact.

---

## 10. Remaining Untested Dimensions

These 9 dimensions were identified but not yet implemented:

| # | Dimension | Location | Estimated Impact |
|---|-----------|----------|-----------------|
| 1 | Confidence floor | `src/core/scoring.py:66` | Low |
| 2 | RVOL curve shape | `src/core/scoring.py:155-165` | Low |
| 3 | Chandelier multiplier | `scripts/replay_optimizer.py` | Medium |
| 4 | Pullback/exhausted retracement | `src/execution/exit_strategies.py` | Medium |
| 5 | Gratitude decay/floor | `src/execution/exit_strategies.py` | Medium |
| 6 | 20-minute rule parameterization | Not yet parameterized | Medium |
| 7 | Kelly guardrails (min_price, min_gap) | `scripts/replay_optimizer.py:100-101` | Low |
| 8 | Adaptive runner trailing | Not yet implemented | Medium |
| 9 | Full exit intelligence integration in replay | `scripts/replay_optimizer.py:557` | High |

The highest remaining opportunity is #9: fully integrating the 13-signal exit intelligence scoring loop into `simulate_trade()` so that exit signal weight overrides actually affect replay outcomes.

---

## 11. Files & Infrastructure

| File | Purpose |
|------|---------|
| `src/arena/strategy_arena.py` | Core engine: StrategyProfile, StrategyArena (Elo), ArenaDataLoader, replay |
| `src/arena/__init__.py` | Package init |
| `scripts/run_arena.py` | CLI: 22 profiles, tournament orchestration, report output |
| `data/arena/arena_state.json` | Persisted Elo ratings and match history |
| `data/arena/results_*.json` | Detailed per-session results with timestamps |
| `tests/unit/test_strategy_arena.py` | 48 tests covering profiles, entry re-eval, Elo, counterfactuals |

### CLI Usage

```bash
python scripts/run_arena.py                                    # Full 22-profile tournament
python scripts/run_arena.py --profiles baseline,aggressive_all # Subset comparison
python scripts/run_arena.py --dates 2026-03-20                 # Single session
python scripts/run_arena.py --rankings                         # Show saved Elo
python scripts/run_arena.py --counterfactual ANNA              # What-if analysis
python scripts/run_arena.py --detail                           # Per-profile breakdown
```
