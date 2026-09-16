# mx-arena: Final Assessment — Honest 8.5/10

**Date**: March 28, 2026 (Post-D137)
**Previous self-assessment**: 9.5/10
**Corrected assessment**: 8.5/10

---

## Where the 8.5 Is Earned

**Matching engine + OTO lifecycle** (earned): The 8-test OTO lifecycle suite validates
the exact D100 flow production uses. If this passes, execution simulation matches reality.

**Statistical framework** (earned): Bootstrap CIs, walk-forward CV, regime classification,
drift alerting. The 3.43x overfit finding alone justifies the framework. Institutional-grade.

**Golden-file regression tests** (earned): 3 anchor dates with locked outputs. Any code
change that shifts fill/spread/exit logic immediately fails a test. Would have caught the
D133 spread timezone bug on first run.

**The -99.3% P&L delta** (earned): Proves the simulator catches real bugs. The story from
+$16.94 phantom to +$0.12 honest is the most important number in the project.

## Where the Score Is Generous

**Decision replay** (7.5, called 9.5): Only implements gap momentum mode (1 of 50+ signal
types from the real technical agent). On stocks that don't trigger gap momentum, uses the
journal's original signal unchanged. Can't test non-momentum technical logic changes.

**Exit intelligence** (8.0, called 9.0): All 4 strategies ported from production but NOT
calibrated. Alpha oracle uses hand-drawn null curve. Velocity thresholds are hardcoded.
2-strategy agreement threshold based on 355 signals from live trading at different times.
Strategies work in isolation but calibration relative to each other is unvalidated.

**55% signal fidelity** (honest): 45% of signal-level decisions are wrong or missing.
Can't test prompt changes, news agent behavior, Phase 1 scanning, or WebSocket VWAP.
Decision replay claims to enable signal optimization but 55% fidelity means signal
optimization results should be treated with significant skepticism.

## What the Walk-Forward Result Actually Means

Train PF=4.40 CI [0.84, 20.50], test PF=1.28 CI [0.06, 7.00].

**The CIs are enormous.** Train CI includes unprofitable territory (PF < 1.0). Test CI
includes catastrophic loss (PF=0.06). With 28 train trades and 21 test trades, the results
are essentially noise. Walk-forward needs 100+ trades (50+ per window) for meaningful CIs.

**The 3.43x overfit ratio is unreliable** when both numerator and denominator have CIs
spanning two orders of magnitude. The honest conclusion: not enough data yet.

**The framework is correct.** The data isn't sufficient. Need 30+ dates with 100+ trades.

## The 6 Innovations That Find Real Money

### 1. Evaluation Ordering (highest leverage, lowest effort)

The sequential evaluation bottleneck costs unmeasured alpha. Simulate every candidate
ordering (momentum desc, gap desc, RVOL desc, spread asc, random) across 75 dates with
evaluation_delay_bars modeling 30s per LLM candidate. If ordering delta is 30%+ of P&L,
the correct response is to re-architect fast-path for parallel entry.

### 2. Time-Phased Stops (addresses biggest P&L leak)

Phase 1 (0-5 min): Ultra-tight 1-2% stop. Not momentum if no move in 5 min.
Phase 2 (5-15 min): Full ATR stop. Demonstrated momentum, give room.
Phase 3 (15+ min): Trailing chandelier. Protect gains.

Sweep phase boundaries and per-phase distances across 75 dates.

### 3. LLM Agent Dollar Value (determines architecture)

Run decision replay in 4 configs: (a) all agents, (b) news only, (c) technical only,
(d) deterministic only. If (d) produces 90% of P&L at zero latency cost, LLM agents
are net-negative. Innovation: invert pipeline — enter on deterministic, adjust on LLM
post-entry.

### 4. Failure-Mode-Conditioned Optimization

Cluster losing trades by price path shape (gap-and-fade, phantom gap, pump-dump, slow
bleed). Find archetype-specific defensive params. Build real-time classifier on first
3-5 bars. Apply conditional defense. Tests whether adaptive risk management outperforms
static.

### 5. Capture Ratio Optimization

Optimize actual_pnl / MFE instead of total P&L. Decompose into entry capture, hold
capture, exit capture. Each has different parameter levers. Focuses optimization on
exit timing (highest leverage) rather than entry selection (already good).

### 6. Synthetic Regime Stress Testing

Inject regime shocks into real historical days: 3x spreads at T+30, 10% volume at T+45,
same-sector candidates. Find parameters that don't catastrophically fail. Pareto frontier
between average performance and worst-case performance.

## The Meta-Innovation: Continuous Learning Loop

Each live day -> new arena data -> re-run walk-forward -> drift detection -> auto
re-optimize if CI drops -> validate improvement via bootstrap -> recommend deployment.
Track recommendation accuracy over 50+ cycles. If >70% acceptance accuracy, the
optimizer is trustworthy.

## Immediate Actions

1. Run decision replay on all 75 dates (not just 17 with journals) to get 200+ trades
2. Compare simulated fills to actual Alpaca fills on historical trades (calibration)
3. Run evaluation ordering study (Innovation 1) — sweep is a config change, not code
4. Run LLM value measurement (Innovation 3) — 4 configs, same dates, compare P&L
