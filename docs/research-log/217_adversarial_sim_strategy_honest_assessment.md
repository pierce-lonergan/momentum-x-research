# 217 — The adversarial market sim: honest assessment + how it fits

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "build a system that simulates the market and BATTLES our process to
make us lose as much as possible, while we try to profit anyway. Is it worth it, how does it
fit, accelerate, compound the edge, dig into our edge like a tick."

This is a strategy decision, not a coding task. My honest assessment follows.

---

## 0. TL;DR
- **Yes — but as a ROBUSTNESS (execution-breaking) tool, NOT an edge-DISCOVERY tool.**
- **~70% of it already exists in `mx-arena/`** (scenario injection, failure injector, sim
  exchange, adverse-selection sampler, synthetic candidates) — research-only, unwired.
- **Build it against our EXECUTION/RISK plumbing now** (deterministic, can't be overfit).
- **Do NOT build a synthetic-distribution adversary to find/optimize SELECTION edges yet** —
  we'd overfit to a market we invented (the exact trap the stress tests just exposed).
- The accelerant isn't more synthetic data; it's a TIGHTER MEASUREMENT LOOP on REAL data.

## 1. The warning the stress tests just gave us (why this matters)
docs 213-215 ran our quantitative claims through no-cap + CI + multi-regime. Result: **every
"edge" evaporated.** MFCS-anti-predictive = cap artifact. Fade-short +9.47% = failed wide.
Continuation edge = never tested on our data. Marketable fill +2-3% = really +0.7%. The one
durable truth: **selection buys VARIANCE, not expectancy; the money is in EXECUTION + TIMING.**

Implication for an adversarial sim: **a synthetic market is a data-generating model. If we
optimize our SELECTION against it, we will "find" edges against our own invented
distribution — exactly as real as the +9.47% fade-short was (i.e., not).** We are
demonstrably good at fooling ourselves with plausible numbers; a synthetic adversary is a
machine for manufacturing more of them, faster. That is compounding CONFIDENCE IN FAKE
EDGES, the opposite of compounding a real one.

Deeper: **we have not yet confirmed a real selection edge to compound.** An adversarial sim
HARDENS an edge you've confirmed; it cannot FIND one you haven't. You can't compound what
you haven't measured.

## 2. What the adversary IS genuinely good for (its real job)
Not discovery — ROBUSTNESS of the layer we just pivoted to and that is DETERMINISTIC
(unfakeable): execution, risk, recon. The adversary's win condition = make our PLUMBING fail:
- carry a naked position overnight (the cancel-settle race, doc 216 — fixed ONE path; the
  adversary finds the rest),
- book PHANTOM P&L (force a close-403 storm — `failure_injector.py` ALREADY models Alpaca
  40310000 / held_for_orders),
- get squeezed/halted into max loss (`scenario.py` ALREADY injects flash_crash/halt/squeeze),
- blow the 3% drawdown guardrail, trip circuit breakers into garbage states, strand ghosts,
- exploit the EOD-close timing window, the stop re-arm race, the tranche 403-war.
This tests "does our machinery survive an adversarial broker + tape" — pure robustness,
can't be overfit because the machinery is logic, not a fitted signal. **THIS is buildable now
and directly compounds the execution-hardening (216) already underway.**

## 3. What already exists in mx-arena (the 70%)
- `scenario.py` — synthetic minute bars + inject flash_crash / trading_halt / short_squeeze.
- `failure_injector.py` — broker-error injection (403/40310000 held_for_orders, 422, 5xx) —
  the EXACT shapes that caused our phantoms/naked carries.
- `exchange.py` / `sim_alpaca_client.py` — a SimExchange + a sim broker client (drop-in for
  the real AlpacaDataClient).
- `adverse_selection_sampler.py` — empirical adverse-drift sampler (post-fill the price moves
  against you) — the realistic "market hunts your fill" mechanism, calibrated not parametric.
- `synthetic_candidates.py` — generate candidates from bar data (universe multiplier).
- `regime.py`, `walk_forward.py`, `calibration/` (spread models v0.1/v2/v3).
**Gap**: it's never wired to drive our REAL bridge/executor/exit/recon code; it's a separate
research sandbox. The high-value build = a thin harness that runs our REAL execution stack
against the sim broker + the failure injector + the scenario library, scored by "how much did
the adversary make us lose / how many invariants did it break."

## 4. The recommended architecture: the ADVERSARIAL EXECUTION HARNESS (build now)
```
  ScenarioGenerator + FailureInjector  ──drives──▶  sim_alpaca_client (SimExchange)
                                                          ▲
                                                          │ (drop-in for AlpacaDataClient)
                              OUR REAL CODE: bridge.attempt_close_with_status_check,
                              exit_intelligence, eod_recon, position_manager, the D-codes
                                                          │
                                          INVARIANT CHECKER (the adversary's scoreboard):
                                            - never naked (qty!=0 -> protective stop exists)
                                            - never a phantom (P&L booked only on confirmed fill)
                                            - never carry unintended overnight
                                            - drawdown guardrail holds
                                            - recon delta == 0 at EOD
                              Adversary WINS if it breaks an invariant or maximizes our loss.
```
This is a property-based / chaos test of execution, with an optimizer (or just a scenario
sweep) searching for the worst broker+tape sequence. It would have caught the cancel-settle
race (216), the phantom class, the overnight carry — BEFORE they cost real (paper) money.
The "battle" framing is exactly right HERE: adversary maximizes invariant-breaks, we harden
until it can't.

## 5. What NOT to build yet (and when to)
- **A synthetic-distribution SELECTION adversary** (generate fake gap-ups matching our
  filter/news distribution, optimize our picker against them): DEFER until the real-data
  scorecard confirms a genuine selection edge (~2-4 weeks of observe data). Building it now
  optimizes against an invented market = overfitting. When we DO build it, the FINAL judge
  must always be held-out REAL data, never the synthetic — the synthetic only proposes
  hypotheses; reality disposes.
- The honest accelerant for selection is the TIGHTER REAL-DATA LOOP we already have running
  (188 observe features + 213 fixed tools + the auto-scorecard), not synthetic data.

## 6. On "work around the lock / dig like a tick"
The instinct (relentless, deep, fast) is right; the method needs care. The discipline
(observe-first, no-cap+CI, ship-only-if-beats-baseline) is the ONLY reason we haven't
compounded fake edges into real losses — it IS the host the tick must not kill. Dig deeper
*faster* by tightening the measurement loop (more signals logged, faster grading, the
adversarial EXECUTION harness), NOT by loosening the gate. The fastest path to a compounding
edge is ruthless honesty about which edges are real — which is what the last few docs bought us.

## 7. Recommendation (concrete)
1. **NOW**: build the adversarial EXECUTION harness (§4) on the existing mx-arena pieces —
   wire `failure_injector` + `scenario` + `sim_alpaca_client` to drive our REAL
   `attempt_close_with_status_check` / exit / recon, with an invariant checker. Highest ROI,
   can't be overfit, extends doc 216. (Proposed doc 218.)
2. **CONTINUE**: the real-data scorecard/observe loop accrues the selection evidence.
3. **LATER (gated on #2 confirming an edge)**: the synthetic-distribution selection adversary,
   judged on held-out real data.

I'm strongly in favor of #1 and would start there. I'd push back on jumping to the
synthetic-selection adversary until we've earned it with real data — not because it's hard,
but because it would actively mislead us right now, and we've just spent days proving how
easily we mislead ourselves.

## Appendix
- mx-arena modules cited: scenario.py, failure_injector.py, exchange.py, sim_alpaca_client.py,
  adverse_selection_sampler.py, synthetic_candidates.py, regime.py, walk_forward.py.
- This doc + changelog. No code yet — a strategy decision for Pierce.
