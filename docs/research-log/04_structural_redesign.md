# Structural Redesign — Replace the Cascade with a Calibrated Composite

The immediate fixes in `03_immediate_fixes.md` will produce trades tomorrow. They are tactical. This document is strategic: **the cascade architecture itself is the disease, not any single threshold.** If we don't fix the architecture, we'll be back here in two weeks with a new bottleneck gate.

## The cascade math

13 binary gates in series with average pass rate `p` produce compound survival `p^13`. Plot:

| Per-gate pass rate | Compound survival | Trades per 100 candidates |
|--------------------|-------------------|---------------------------|
| 95% | 51% | 51 |
| 90% | 25% | 25 |
| 85% | 12% | 12 |
| 80% | 5.5% | 5–6 |
| 75% | 2.4% | 2–3 |
| 70% | 1.0% | 1 |
| **Today (1 gate at 0%)** | **0%** | **0** |

Even with a 90% pass rate per gate (which is generous — many of our gates are 80–85%), 13 gates compound to 25%. From 24 daily candidates, we'd take 6 trades. That sounds good — until you remember that **each individual gate is a *correlated* signal of low quality**, so the arithmetic above overstates the real survival.

In practice, gates in our cascade are *correlated*: a stock that fails the float gate often also fails the price gate, the catalyst gate, etc. So the failure rate isn't just multiplicative across the gates — failures cluster on specific stocks. That's actually *worse* for end-to-end survival because the same "good" candidates pass everything and the "marginal" ones get killed by some combination of 3-4 gates each.

## Why this architecture happened

The system grew organically. Every gate was added in response to a specific real-world failure:

- D101 consensus — added after the system bought stocks that all 5 LLM agents marked NEUTRAL
- D124 alignment — added after a bullish-bearish split led to losing trades
- D112 router — added as a *compute optimization* but creep-included structural rules
- VWAP bias — added after buying stocks that were already breaking down
- D170 observation — added to avoid front-running fake breakouts
- ORB confirmation — added in D219 based on the Zarattini research

Each gate is defensible in isolation. Each gate killed real losers in its day. The problem is **none has ever been removed**. New gates are *added* on top of old ones rather than *replacing* them. The result is monotonic gate accumulation.

## The target architecture: one continuous score

The replacement is straightforward in concept:

> **One MFCS-style continuous score. One BUY threshold. One ORB confirmation gate. Done.**

The 13 binary gates collapse into:

1. **EMC scanner** — keep as is. This is the universe filter. Gap≥5%, dolvol≥$2M, RVOL≥3, price≥$2. Produces ~20-50 candidates per scan.

2. **One composite probability score** — replaces D112 router instant_reject, D101 consensus, D124 alignment, VWAP bias, all the routing tiers. The score is a calibrated estimate of `P(close return > 0 | features)` trained on the 79-day backfill data we generated yesterday.

3. **MFCS buy threshold** — keep as is at 0.25 (or whatever the calibration shows is the optimal precision/recall trade-off).

4. **ORB confirmation** — keep as the *only* hard structural gate after scoring. Yesterday's backfill: ORB-broken 53% WR vs ORB-held 11% WR. This +41.6pp edge is the largest signal we have. Worth a hard gate.

5. **Position sizing** — Kelly tier per current logic. Adjusted by the score's calibration confidence.

That's 5 stages, only 2 of which are binary gates (EMC scanner + ORB). Compound survival from EMC-pass to BUY is governed by the score distribution, not by multiplicative gate dilution.

## What goes in the composite score

Train a single model — start with logistic regression for interpretability, upgrade to LambdaMART per the user's roadmap once we have 1000+ training rows — predicting `P(close > 0 | features)` from:

| Feature class | Examples (from the 407-row backfill) |
|---------------|----------------------------------------|
| Pre-market structural | gap_pct, premarket_volume, dolvol, price, float_shares, market_cap |
| Catalyst quality | news_agent's catalyst type (FDA/clinical/M&A/earnings/contract/none), confidence |
| Manipulation risk | risk_agent's score, dilution flags (424B5), short interest if available |
| Technical | RVOL, ATR, distance to prior day high/low, sector context |
| Calendar | day-of-week, day-of-month, earnings season flag |

The first version is honest: take the existing agent outputs and fit a logistic regression to backfill outcomes. That gives you a single score per candidate that:
- Is interpretable (you can read off feature weights)
- Is calibrated (output is a probability, comparable across candidates)
- Replaces 6+ binary gates (D112 instant_reject, D101 consensus, D124 alignment, VWAP bias, MFCS thresholding, deterministic_strong_pass)

## Migration path

Don't rip out the gates immediately. Run the new score in **shadow mode** for 5 sessions:

1. Compute the composite score for every candidate that reaches it (currently ~5 per session).
2. Log the score alongside the actual MFCS and the actual gate-cascade verdict.
3. After 5 sessions, compare:
   - Where the new score and the old cascade *agree* (both BUY or both NO_TRADE) — confidence builder.
   - Where they *disagree* — investigate each case manually. These are the calibration opportunities.
4. Once disagreement is understood, flip the new score to live and remove the bypassed gates.

This is the same shadow-mode approach we used for D219 Phase 4 (catalyst quality classification). It worked there. It will work here.

## What about the other gates?

The "other gates" (stale gap detection, exhaustion, GEX, dilution flag, corporate action) become **soft penalties on the composite score**, not hard rejections. A stale gap subtracts X from the score; an exhaustion classification subtracts Y; etc. The threshold (0.25) does the actual rejection. A stock with 4 small penalties may still score above threshold; a stock with one catastrophic penalty (dilution) drops far enough to fail.

This preserves the *information* each gate carries while removing the *multiplicative dilution* problem.

## Why this works

Three reasons:

1. **Continuous beats binary.** A binary gate at 80% pass loses ~20% information per gate even when the *signal* it carries is weak. A continuous penalty loses information only proportional to the penalty's magnitude.

2. **Calibration beats heuristics.** The current thresholds are heuristics from individual incidents. Calibration on 407 labeled outcomes gives you the *empirically optimal* trade-off between precision and recall.

3. **One score is testable.** Sweeping a single threshold over historical data is straightforward — produces an ROC curve, an optimal operating point, and an honest expected-value estimate. Sweeping 13 thresholds is intractable; their interactions are too high-dimensional to characterize.

## The "one weird trick" alternative

If the redesign feels too ambitious for the next 7 days, there's a simpler substitute that captures most of the value:

> **Treat any candidate with MFCS ≥ 0.50 as a guaranteed BUY (subject only to ORB confirmation). Bypass D112, D101, D124, VWAP, and all routing tiers.**

This is essentially "MFCS ≥ 0.50 is the score" with the existing scoring system. Today, the 5 MFCS=0.821 candidates would have been buys. Yesterday's MFCS=0.539 max would have been a no-trade — which matches our backfill finding that average MFCS quality was lower before D219 Phase 2.

This is a **3-line code change** (one in adaptive_router.py, one in orchestrator.py to add the high-MFCS bypass, one in the VWAP gate to skip when MFCS is high). It's not the "right" architecture but it would unblock trading immediately while we build the proper composite score.

I'd actually recommend doing this in addition to the immediate fixes in `03_immediate_fixes.md` — they're mutually reinforcing.

## Connection to the production arena

The structural redesign requires the production arena from `02_arena_critique.md`. You can't sweep a composite score's threshold without an arena that simulates the full pipeline against historical data. Build the arena first; train the composite second; ship to production third.

Estimated build time:
- Production arena: 2–3 days (mostly wiring `evaluate_candidate()` to a Scenario class)
- Composite score (logistic regression on backfill): 1 day
- Shadow mode integration: 1 day
- 5 sessions of shadow data: 1 trading week
- Live cutover: 1 day

Total: 2 weeks from start to live composite score. The immediate fixes from `03_immediate_fixes.md` cover the gap.

## What I'd rip out completely

When the composite score is live, these gates can be deleted (not just bypassed):

- D112 router `instant_reject_*` rules (fold into composite as soft penalties)
- D101 VWAP bias (fold in as a feature)
- D124 alignment (fold in as a feature)
- D101 consensus minimum (it's already conditional on MFCS — just trust MFCS)
- The `deterministic_strong_pass` and `deterministic_clear_reject` short-circuits in D112 (these are exactly the kind of hard threshold the composite replaces)

That's roughly 600 lines of logic that gets simpler. The cognitive overhead of the system drops substantially. New developers (or new model versions) can understand it in one read.

## Summary

The cascade architecture has been the cause of every "why aren't we trading" investigation for at least the last 4 weeks. Each one ends with "fix one gate, the next one becomes the bottleneck." The cascade is a hydra. The only way to win is to stop playing whack-a-mole and replace the architecture.

The immediate fixes in `03_immediate_fixes.md` buy us tomorrow's trades. The structural redesign in this document buys us a sustainable system.
