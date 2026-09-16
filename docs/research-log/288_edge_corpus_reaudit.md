# 288 — THE RE-AUDIT: did a bug ever fake (or bury) our edge? A 15-agent adversarial sweep of the whole corpus

**Author**: Claude (Fable 5, ultracode; 5 error-class hunters + per-finding adversarial skeptics + synthesis, wf_2250281a, 15 agents / ~1.14M tok) | **Date**: 2026-07-11 | **Class**: METHODOLOGY AUDIT + one disclosed magnitude correction + a systemic-trap CI guard | **Mandate (Pierce)**: "reanalyze the experiments… look for mistakes that could've conflated our conclusions/misrepresented our results, or bugs that produced incorrect results, and fix them."

> **The multi-month no-edge thesis survived the re-audit intact.** Five hunters swept the corpus for temporal leakage, selection/survivorship, multiplicity/overfit, cost/fill/denominator dishonesty, and outright money-code bugs; every filed finding was handed to an adversarial skeptic who tried to reproduce and refute it. Result: **1 confirmed defect that misstates a headline *number* (not a decision), 8 checked-and-clean, and 1 systemic trap worth hardening.** The only real error made a dead thing look *deader*, not alive. Net effect: the negatives are stronger, not weaker.

## §1 — The one confirmed defect (FIX-1): the short door is dead by ~0.6%, not ~1.2%

`scripts/_doc284_borrow_pricer.py:~360` computes each short ticket's net as `gross − day_borrow − FRICTION_PCT − OVERSHOOT_PCT`, subtracting the **0.90% stop-overshoot** (the cost of a stop *gapping through* the +8% cover on a squeeze) **flat from every fillable ticket**. But that cost is only incurred when a ticket is actually stopped out — and the measured stop-rate is only ~0.34. The correct expected charge is ≈ 0.90 × 0.34 ≈ 0.31%, so the pooled net was **overstated ~2×**:

| | reported (doc 284) | stop-weighted correction |
|---|---|---|
| net / ticket | −1.205% | **~−0.62%** |
| CI95 | [−1.571, −0.822] | ~[−0.99, −0.24] |

**The tombstone does not move.** Corrected net is still < 0 with a CI fully below zero → *SHORT DOOR TOMBSTONED* stands, and the +0.13%/day frontier is unchanged (the short leg contributes nothing either way; the pricer separately discloses its IBKR-APR borrow is an optimistic lower bound vs 1–3% specialist locates, which sustains the tombstone on its own).

**How it was handled — disclose, don't retro-tune.** `OVERSHOOT_PCT` is a *frozen doc-284 prereg parameter*, and the finding is MEDIUM-confidence (if 0.90% was intended as an already-whole-set-averaged drag, it is a labeling defect, not a math bug). Per the project's own doc-275/277 discipline (no post-hoc tuning of frozen parameters), I did **not** silently mutate the parameter and re-publish a "corrected" headline. Instead: a loud audit-disclosure comment now sits at the parameter and the deduction site, this doc records the corrected magnitude, and the [MEMORY doc-284 entry is annotated](../../). A clean stop-conditional re-run (charge overshoot only on `mfe≥cover_stop` tickets) is the documented follow-up — it moves the magnitude, not the decision.

## §2 — What was checked and is CLEAN (the negatives, strengthened)

Each was reproduced by an adversarial skeptic and found bug-absent, harmless, pre-registered, or provably non-moving — so the verdicts they support are now *more* trustworthy:

- **doc-250 EOD-hold denominator** — CLEAN. Path coverage is 100% in all three regime-years (the corpus is built from the same warehouse it reconstructs from); the `if p is None` branch is dead code. No-edge exit verdict unaffected.
- **doc-258 short-side survivorship** — CLEAN (mechanism real, effect null). The `c20 IS NOT NULL` survival filter moves the long-short spread < 1 point under four treatments (relax to c10; dark→last-close; dark→−100%) and flips no sign; the binding short constraint is borrow, not the pre-borrow spread.
- **doc-284 rocket-gate duplicate tickets** — CLEAN (frozen by design; prereg §2 declares "per-ticker arm instrument, not a book simulation"; the +$14,710 is explicitly "not money").
- **doc-280 Path-B multiplicity** — CLEAN (already quarantined; Path-B is the secondary ceiling, the primary session-blocked counterfactual drives NO-SHIP; doc-281 already retracted the ceiling dollars).
- **doc-242/244 tick-OFI on a UTC-mislabeled baseline** — CLEAN for verdict (the ts_et bug is real but doc-245 named + fixed it via sip_timestamp and re-derived correctly-windowed; the corrected lift *shrank*).
- **v6 microstructure pack ts_et windows** — CLEAN for verdict (real re-introduction, but impact confined to OFI ρ +0.003→+0.062; vpin/iso_sweep are order-bucketed and immune; the do-not-ship gate loss can't be closed by it).
- **doc-255 coiled ∩ rocket = ∅** — CLEAN (structural collection artifact; the load-bearing coiled verdict is the clean forward `fwd_hi10` test in doc-254).
- **doc-284 optional stopping (n∈[30,60])** — CLEAN (real rigor gap, but no verdict produced yet — live n=3, PENDING; a hard DEAD kill sits at n=60; add an alpha-spending boundary as an improvement).

## §3 — The systemic trap (the real prize): `trades_v1.ts_et`

The audit's highest-leverage finding is a *pattern*, not a single bug: **`trades_v1.ts_et` is a `timestamp[tz=UTC]` column storing the wrong instant (~+240 min off true `sip_timestamp`)**, so any wall-clock time-of-day windowing off `ts_et` silently selects ~05:30 pre-dawn instead of the 09:30 open. It broke doc-242/244, was diagnosed and fixed in doc-245, and then **got re-introduced in `v6_microstructure_pack.py`.** It has never flipped a live verdict — but only by luck (the affected features were weak or dominated by order-bucketed features immune to the bug).

**Hardening shipped:** `scripts/_doc288_tset_guard.py` classifies every `ts_et` wall-clock extraction in `scripts/` + `src/` — 50 raw hits reduce to **25 SUSPECT** (a file that neither uses `sip_timestamp` nor `tz_convert`) vs 18 safe. `tests/unit/test_tset_guard_doc288.py` **freezes the 25 as an allowlist and FAILS on any new occurrence** — so the next re-introduction can't land silently; the author must derive ET from `sip_timestamp` or justify an allowlist entry. (Auditing the 25 grandfathered sites — several of which read a correctly-labeled warehouse — is a documented follow-up; the guard stops the bleeding.)

## §4 — Honest ledger

1 verdict-moving defect (magnitude only, disclosed) · 4 real-but-non-moving artifacts · 3 not-a-bug (mechanism absent or pre-registered) · 1 refuted on reproduction. I did not inflate the count to look thorough. **No decision in the negative thesis — no long edge in price or information (250-261), short door dead net of borrow (284), no rocket edge (BET#3), +0.13%/day frontier (283) — is driven by a bug.** The re-audit's product is confidence: the wall the search keeps hitting is the market, not our code.
