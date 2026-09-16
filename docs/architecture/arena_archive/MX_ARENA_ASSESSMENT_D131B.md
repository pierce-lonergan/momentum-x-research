# mx-arena: Second Assessment — Post-Documentation Update

**Date**: March 27, 2026 (D131, post-doc update)
**Score**: 7.0/10 (unchanged — code didn't change, documentation did)

---

## What Actually Changed

The implementation is identical. Same 74 tests, same 14 REST endpoints, same matching
engine, same data engine. Every technical subsystem score is unchanged.

What changed is the document's relationship to reality. Three specific improvements:

1. Opening paragraph now says "it does NOT yet run main.py end-to-end" rather than
   implying it does. Fidelity estimate (60% execution, 20% signal) in the header.
   Prevents the most dangerous simulator failure: trusting results for questions it
   can't answer.

2. Parameter table split into "currently sweepable" and "requiring decision replay"
   is the clearest articulation of actual capabilities. Before, someone might sweep
   mfcs_buy_threshold and get meaningless results with no warning.

3. Stop loss sweep caveat is honest in a way backtesting documentation rarely is.
   Calling out that 2% "wins" only because losers lose less, that single-day results
   don't generalize, and D122 gap widening would override the finding anyway.

---

## Why Score Stays at 7.0

Better documentation doesn't change what the tool can do. Core limitation: the simulator
optimizes execution on decisions the bot already made. It can't tell you:
- Whether gap_momentum_score_threshold=0.7 would have caught QNTM on Mar 25
- Whether lowering mfcs_buy_threshold from 0.15 to 0.10 produces profitable marginal entries
- Whether confidence_deflation_factor=0.75 outperforms 0.70 on real candidates

These determine profitability. The simulator can't answer any of them yet.

---

## Three Things That Matter Most for Profitability

### 1. Missing Data (Not a Missing Feature)

Validated on 2 days (Mar 26 with 7 trades, Mar 27 with 0). The stop loss sweep finding
is based on 7 trades on 1 day. No parameter conclusion from 7 trades has statistical
meaning. Before implementing any roadmap item: download 90 days of bars for every watchlist
ticker. This takes hours and produces the foundation for everything else.

### 2. Decision Replay > Full Bot Wiring

Full bot wiring requires pre-market simulation, news API replay, LLM mocking — three hard
sub-problems. Decision replay only needs deterministic agents + MFCS formula + mocked LLM
signals from journals. Much smaller scope, answers the most valuable question: "which
parameter changes would have changed which trades we took?"

Implementation: For each day, load watchlist. For each candidate, run
deterministic_technical.evaluate_signal() and deterministic_risk.evaluate_signal() with
sweep param. Mock LLM signals from journal. Compute MFCS. Apply consensus gate. Output
BUY set per config. Feed into matching engine. No HTTP server, WebSocket, or pre-market
simulation needed.

### 3. Statistical Framework for Multi-Date Sweeps

Current sweep ranks by P&L/win rate/profit factor. Across 50 days, need statistical
significance. Bootstrap CI: resample per-trade P&L 1000x, compute 95% CI on profit
factor. Rank by CI lower bound, not point estimate. Config with PF 1.5 CI [1.1, 2.0]
beats PF 1.8 CI [0.9, 3.2] because its CI excludes unprofitable territory.

---

## What March 26 Data Actually Reveals

Three winners (RMSG +12.3%, PAYS +5.4%, OLPX +0.5%). Two stopped out (JBLU, SRPU).
Two held to EOD at loss (SRPT -5.6%, UGRO -14.3%).

**Pattern:** Winners go up from entry and never look back. Losers start declining within
minutes. The difference between profitability and loss isn't stop distance — it's which
stocks you enter. RMSG/PAYS/OLPX were good trades at any reasonable stop. SRPT/UGRO/JBLU/
SRPU were bad trades at any stop. Optimizing stops treats the symptom. Optimizing candidate
selection treats the cause. This is precisely why decision replay matters more than
execution optimization.

---

## Specific Technical Recommendations

1. **OTO regression test**: Replay exact D100 OTO-to-standalone flow. Submit OTO buy +
   stop, fill buy, cancel stop, submit standalone stop, submit 3 tranche sells. Verify
   state at each step. This is production's most complex order flow.

2. **Spread model calibration**: Download 5 days of actual Alpaca quote data for 10
   symbols. Compare simulated spread vs actual bid-ask at 1-min resolution. If simulated
   spread understates reality, simulator is optimistic about entries.

3. **ANNA scenario**: Replay exact D124 pattern — enter $14.28, stop $13.11, immediate
   drop to $12.01. If simulator reproduces this correctly, scenario library captures a
   real failure mode.

4. **Bootstrap CI on sweep results**: Before Optuna, implement 20-line bootstrap. Resample
   per-trade P&L 1000x, compute 95% CI on profit factor. Rank by CI lower bound.
   Immediately tells you if sweep results are signal or noise.
