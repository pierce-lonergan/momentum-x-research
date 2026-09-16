# mx-arena: Honest Assessment & Path to Full Optimization Engine

**Date**: March 27, 2026 (D131)
**Status**: Execution simulator (7/10) with clear path to strategy optimizer (9.5/10)

---

## What Was Built vs What Was Designed

The architecture spec described a system that runs `main.py` unmodified against a local
exchange server. What was actually built is meaningfully different.

**What's implemented:** A replay engine that takes BUY signals from the trade journal and
runs them through a matching engine with real historical bars. It answers: "given that the
bot decided to buy RMSG at $0.57, what would have happened with different stop distances?"

**What's not implemented yet:** Running `main.py` end-to-end against the arena's HTTP
server. The current system doesn't exercise the scanner, the agent pipeline, the MFCS
scoring, the consensus gates, the spread filter, or the exit intelligence. It takes the
journal's decisions as given and only simulates execution.

**What this means:** You can optimize execution parameters (stops, position sizing, risk per
trade) but you cannot optimize signal parameters (gap momentum threshold, MFCS threshold,
deflation factor, agent weights) because those decisions aren't being re-made by the
simulator. This is the gap between a 7/10 system and a 9.5/10 system.

---

## Fidelity Assessment

### What the simulator gets right (high fidelity)

The matching engine's NBBO-based fill logic with reconstructed bid-ask spreads is faithful
to Alpaca paper trading. Stop triggers on bar.low, limit fills on price crossing, 10%
partial fill probability all match documented behavior. The OTO bracket lifecycle (parent
fill activating held child) mirrors the D100 production flow.

The spread model's price-tiered + time-of-day architecture captures the most important
structural feature: wide at open (when the bot enters), tight midday, wide at close.

The March 26 validation (JBLU stopped at $4.37, RMSG survived to +12.3% EOD) demonstrates
realistic position outcomes.

**Estimated fidelity: ~60% for execution questions (stops, sizing, P&L given an entry).**

### What the simulator gets wrong or omits (fidelity gaps)

**Gap 1: No decision re-evaluation.** Every parameter sweep is conditioned on the entries
the current configuration selected. Optimizing stop distance while holding entry decisions
fixed creates systematic bias. A different MFCS threshold produces different entries, which
might have completely different optimal stop distances.

**Gap 2: No news/LLM agent simulation.** News endpoint returns empty. In end-to-end
replay, news agent returns NEUTRAL on everything, fundamental agent returns
D94_NO_DATA_SKIP. Only the deterministic technical agent produces directional signals.
Parameters optimized in this environment learn for "a system where only the technical
agent works" rather than the real multi-agent system.

**Gap 3: No pre-market simulation.** Phase 1 scanning (04:30-09:20) depends on Alpaca's
most-active and movers endpoints, which return different results at different pre-market
times. The screener stubs return the journal watchlist. In a real replay the scanner might
build a different watchlist from different pre-market data.

**Estimated fidelity: ~20% for signal-level questions (which stocks to trade, when to
enter, how to score).**

---

## The Stop Loss Sweep Finding Deserves Scrutiny

March 26 sweep found 2% stops produce the "best" result (-$0.96 total P&L). But:

- At 2% stops, win rate is 17% (1/6 winners)
- At 8% stops, win rate is 50% (3/6 winners)
- The 2% result is "best" because losers lose less, not because winners win more

On a stock gapping 50% with 100x RVOL, a 2% stop is 4 basis points of the gap. Most gap-up
days have a 1-3% opening pullback. D122 gap-day widening (half the gap as floor) was
designed to prevent this.

**Correct interpretation:** This single day's winners had smooth entries. Running the same
sweep across 10-20 days would likely show 4-6% stops performing best on average. The
multi-date sweep capability is the antidote to single-day conclusions.

---

## Five Changes Ranked by P&L Impact per Implementation Effort

### 1. Run main.py end-to-end against the arena server

Highest leverage improvement. Exercises every decision point: scanner, agents, MFCS,
consensus gates, spread filter, exit intelligence.

Sub-problems to solve:
- **Time simulation 04:30-16:00 ET**: Phase 0/1 happen pre-market and determine the day
- **News API replay**: Capture actual news responses from production sessions (logged in
  journals), replay them for the same ticker-date
- **LLM handling**: Mock from journal (fast, deterministic, can't test prompt changes) or
  call real APIs (slow, expensive, tests real pipeline). Use mocked for parameter sweeps,
  real for prompt experiments.

### 2. Decision replay mode

The critical missing capability. When sweeping `gap_momentum_score_threshold` from 0.5 to
2.0, the system should re-run the deterministic agents with each threshold and produce
different BUY/NO_TRADE decisions per candidate. The matching engine then simulates only
candidates that passed.

Implementation: Load watchlist + bar data. For each candidate, run deterministic agents
(technical + risk) with sweep parameter. Compute MFCS with mocked LLM signals (from
journal if available, NEUTRAL if not). Apply consensus gates. Produce BUY set per config.
Simulate those entries.

Transforms simulator from "what if we change stops on the same trades" to "what if we
change which trades we take" -- where the real profit factor lives.

### 3. Walk-forward cross-validation

Current sweep runs all combos across all dates and picks the best. This is textbook
overfitting. Walk-forward: optimize on days 1-10, test on 11-15, then optimize on 1-15,
test on 16-20. Each test window never seen during optimization.

If train PF=2.0 and test PF=0.8, parameters are overfit. If both ~1.4, they're robust.
The difference between "looks good on March 26" and "generalizes to unseen days."

### 4. Empirical null curve for AlphaDecayOracle

The oracle's effectiveness depends on the null curve (expected return of average gap-up
stock at each minute after open). The `--null-time` backtest data from D122 contains exactly
this. Extract per-minute-bucket average return, fit a curve, inject into both production
oracle and simulator oracle.

Matters for simulator because the oracle is 1 of 4 active exit strategies. Miscalibrated
oracle means simulated exits happen at wrong times and parameter optimization learns wrong
exit lessons.

### 5. Multi-stock portfolio simulation

Current simulator processes each BUY independently. But the bot has portfolio constraints:
max 8 positions, per-position allocation, catalyst concentration, contagion network. On
March 25, 8+ stocks were winners but the bot couldn't enter all 8.

Simulate Phase 2 evaluation loop with shared portfolio state: enter MKDW first (highest
score), try FEED but check limits, try CVV but same sector. Order matters because
sequential evaluation delay means later candidates enter at worse prices.

Only way to answer "what would total portfolio P&L have been?" rather than "what would
each individual trade have returned?"

---

## Implementation Priority

```
Priority 1: Full bot wiring (makes simulator test real decisions)
    |
    v
Priority 2: Decision replay mode (enables signal parameter optimization)
    |
    v
Priority 3: Walk-forward validation (prevents overfitting)
    |
    v
Priority 4: Multi-stock portfolio simulation (captures sequential bottleneck)
    |
    v
Priority 5: Empirical null curve (calibrates exit timing)
```

With priorities 1-4 implemented, mx-arena becomes the mechanism by which we find the
parameter configuration that maximizes realized profit -- not in theory, but through
replaying actual code against actual market data at scale.
