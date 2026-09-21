# 299 — The Epistemic Architecture

**Date:** 2026-09-20
**Status:** BUILT. One retro-validation hit, one procurement item, one finding that
strengthens the no-edge thesis rather than weakening it.
**Code:** `src/epistemics/` (closure, archive, eig, retro), `scripts/epistemics.py`,
`scripts/seed_stepping_stones.py`, `tests/unit/test_epistemics.py` (64 tests)
**Data:** `data/research/stepping_stones.jsonl` (25 records),
`data/research/design_queue.json` (9 designs, priors declared)

---

## 0. Why this document exists

The program has good machinery for judging a result and almost none for choosing a
question. `trial_registry.py` prices a trial after it runs. Pre-registration freezes a
spec before it runs. The mandatory adversarial fleet attacks it once it has run. But
nothing has ever answered "should we run this at all, and what does running it cost
everything else?" — and nothing has answered "we just built X; what did we give up on
*because* we didn't have X?"

Both gaps were filled here, by adopting three ideas from outside the program: Peircean
abduction as bounded search over an explicit hypothesis space, Popperian severity as
formalised by Mayo, and generalised Bayesian optimal experimental design. A fourth idea
— an archive of stepping stones, where a failed experiment is retained with the specific
missing piece that caused it to fail — turned out to be the one that paid.

The first thing the architecture did was tell me I was wrong about what it would find.

---

## 1. The discrimination problem, applied to our own graveyard

`ATTEMPTS_LEDGER.md` records its closures in a column called "verdict". Read closely,
that column conflates two statements with nothing in common:

> **Short door** — net negative after borrow.

A measurement of the world. Borrow costs more than the effect is worth. Nothing we build
changes it.

> **Single-name RV-vs-IV @30d** — unblinded pass VOID, runner evaluated h=1 where the
> prereg froze h=21.

A measurement of *us*. The hypothesis was never actually asked.

Both were filed as closures. Only the first is evidence about markets. `src/epistemics/
closure.py` makes the distinction machine-checkable with eight classes, three of which
are evidence about the market (`REFUTED_BY_NATURE`, `REFUTED_BY_COST`,
`STRUCTURALLY_UNAVAILABLE`) and five of which are evidence about us. The taxonomy has no
"failed" category, and it enforces two rules with teeth:

* a closure that is a statement about us **must name the keystone it was missing**, or it
  is filed as `ABANDONED` — honestly, rather than dressed up as a refutation;
* `REFUTED_BY_NATURE` **requires a recorded effect and interval**, because it is the only
  class that is evidence the edge does not exist.

### What I expected, and what the measurement said

I expected the graveyard to be mostly instrument limitations. The framing that motivated
this work says a discard pile contains a thin seam of suppressed signal, and I went in
expecting to find that "35 families closed, zero certified edges" was really "we never
managed to ask most of these properly."

It is not. The ledger has **35 rows** — 22 CLOSED, 4 FILTERED, 5 OPEN, 4 QUEUED — but
only **33 distinct families**, because the LETF close-window family appears three times as
its state changed. The archive holds the 22 closures plus 3 of the 4 filtered, the fourth
being LETF's superseded BLOCKED-AT-$0 state. Hence **25**. The open and queued families
have no closure to type yet. Transcribing all 25:

> **CORRECTION (doc 300).** This originally said "35 families". The ledger tracks 35
> *rows*; 33 distinct families. The same 33 also happens to be the registered-trial count,
> which is what made 33 and 35 look interchangeable across the repository. They are not.

| closure class | n | evidence about |
|---|---|---|
| `REFUTED_BY_NATURE` | 14 | the market |
| `REFUTED_BY_COST` | 2 | the market |
| `STRUCTURALLY_UNAVAILABLE` | 1 | the market |
| `REFUTED_BY_ARITHMETIC` | 5 | us |
| `VOIDED_BY_DEFECT` | 1 | us |
| `ABANDONED` | 2 | us |

**17 of 25 closures (68%) are measurements of the market.** Mean evidential weight 0.736.
Only 6 name a keystone and can be re-opened mechanically, and 5 of those 6 revive on the
same thing — a re-scoped requirement, which is Pierce's decision and not a build.

This is the opposite of what an enthusiastic reading of the framework predicts, and it
**strengthens the no-edge thesis**. The program's negative results are not an artifact
of poor instruments. They are mostly measurements, and they mostly point the wrong way.

### The honest caveat, which is also a finding

Only **12 of 25** records are strictly admissible. Thirteen claim `REFUTED_BY_NATURE`
without a recorded effect size or interval. That does not mean those families were not
measured — the measurements are in the documents. It means **the ledger does not carry
them**, so none of those thirteen can be retro-scored quantitatively without re-reading
prose.

> **CORRECTION (doc 300).** This originally read 14 and eleven. The audit found that two
> records had been made admissible with numbers that exist in no artifact anywhere in the
> repository: the overnight-ETF sleeve carried an interval of (−1.0, 3.1) where the
> ledger says only "CI spanning zero", and attention-coupling carried
> `effect=0.0, ci=(0.0, 0.0), n_obs=32` where the ledger says "0/32 after correction" — a
> tally of pre-declared regime cells, not an effect size, and 32 counts *cells* rather
> than observations. Both were invented to clear the very admissibility rule this section
> is about. They are removed; the count is now 13/12. **All 25 `closed_on` dates were
> also invented** — the ledger carries no dates — and are now blank.

Every future closure should be written with its measurement attached; the cost of doing so
at burial time is a minute, and the cost of recovering it later is an afternoon per family.

---

## 2. The retro-validation engine, and the one thing it found

`src/epistemics/retro.py` takes the capabilities the program currently has and returns
the archived families whose stated blocker no longer holds. The program has done this
twice by hand — the day_aggs Q1 rebuild (doc 278) that unblocked V-RACE from doc 273 and
triggered the contamination sweep in doc 279, and the point-in-time shares-outstanding
unblock (doc 295 → 298) that exhumed the LETF family. Both paid. Neither was found by
searching; both were remembered, by luck.

Two hard guards, because a program that re-opens closed families whenever a data feed
arrives will re-derive the same losses with more decimal places:

1. **Closures that are evidence about the market do not revive on capability.** A
   keystone arriving cannot undo a measurement. Re-opening one requires an explicit,
   argued market-structure claim, which is a decision for Pierce and not for a script.
2. **A tight, wrong-signed interval scores near zero regardless of which keystone
   arrived.** The gapper universe measured −2.041%/ticket with a day-blocked CI of
   [−2.823, −1.226] across three separate years. No instrument upgrade makes that a
   candidate again, and `_plausibility()` is written so that it cannot.

> **CORRECTION (doc 300).** Guard 2 is real code but, on the current archive, **it never
> executes**. Every record carrying a wrong-signed interval is also a market-evidence
> closure, so guard 1 excludes it first — and both such records name no keystone, so they
> are skipped regardless. Two independent exclusions fire before `_plausibility` is
> consulted, and all six records that *do* reach it have no interval at all, which made
> `_plausibility` a constant function returning 0.5. Citing it as what protects the
> archive was wrong: guard 1 is doing that work alone. The audit also found the function
> never read `effect`, so a family with a measured wrong-signed point estimate (Stage-3
> RV-vs-IV, −4.43) scored the same as one never measured. Both fixed; a test now pins the
> unreachability so that it fails if a record ever does reach the branch.

### The hit: LETF close-window rebalance-flow harvest

Running the engine against what the program now has surfaced five families revivable on
`lower_requirement`. Four are re-derivations. One is not.

The LETF family was closed in doc 298 on arithmetic: at 50% gross it needed a mean of
**21.07 bps** in a 15-minute window whose measured sd is **15.06 bps** — annualised
Sharpe 22.2 — and its validated ceiling was **6.0–8.6% of requirement**, under the ≥10%
build filter. That closure was evaluated against the then-standing 0.1%/day target.

The standing target is now **5 bps/day**. The requirement halves; the effect does not
change. So:

| gate | at 0.1%/day (as closed) | at 5 bps/day (current) |
|---|---|---|
| ceiling as % of requirement | 6.0–8.6% → **fails** ≥10% filter | 12.0–17.2% → **clears** |
| ceiling-implied annualised Sharpe | 1.33–1.91 | 1.33–1.91 (unchanged) |

The family clears the build filter it was killed by. The binding constraint has moved —
and the new one is not arithmetic.

### Where it now fails, and the procurement item that follows

The operative multiplicity bar at 34 registered trials depends only on the trial count
and the sample length. A 15-minute close window can only be measured on minute bars, and
**`minute_aggs` covers 2024-01-16 → 2026-09-17 — 666 sessions, counted from the
warehouse itself** — where the daily warehouse was extended to 2016–2026 this cycle.

| intraday sample | sessions | operative bar @34 trials | ceiling 1.91 clears? |
|---|---|---|---|
| `minute_aggs`, **measured on disk** | **666** | **2.319** | **no** |
| extended to 2020 | 1,510 | 1.540 | yes, at the optimistic ceiling |
| extended to 2016 (**measured: 2,690**) | 2,690 | 1.154 | yes, at both ends |

So the answer is not "LETF is revived". It is:

> **The LETF close-window family is now blocked on one identified, bounded piece of
> work: extending `minute_aggs` from 2024 back to 2016, matching the daily warehouse
> that already exists.** Without it the family cannot clear the bar at any plausible
> effect size. With it, its validated ceiling sits above the bar.

Nobody finds that by remembering. It requires jointly noticing that the target changed,
that the build filter therefore flipped, that the daily warehouse was extended but the
minute warehouse was not, and that this family needs minute bars. Four facts, three
documents, two repositories. That is what the archive is for.

> **CORRECTION (doc 300).** The session counts above were originally 650 and 2,772 —
> a round estimate and a nominal 11×252. Counted from the warehouse with DuckDB, the
> figures are 666 (2024-01-16 → 2026-09-17, so there is also an unnoticed two-week hole at
> the start of 2024) and ~2,690 after a 2016 backfill. The bars move from 2.347/1.137 to
> 2.319/1.154. **No sign changes: the ceiling sits below the bar now and above it after.**

> **RESOLVED (2026-09-21).** The paragraph below flagged the 2× rescale as
> under-determined. It is now determined, from commit timestamps rather than from the
> 21.07 ≈ 2 × 10 inference: doc 297 set 0.1%/day at 2026-07-28 20:16 (`2af6f12`), the LETF
> CLOSED verdict and its 21.07 bps figure were committed 2026-07-29 (`f411a28`), and doc
> 298 set 5 bps/day at 2026-08-02 15:38 (`f4b743c`). The closure belongs to the 0.1%/day
> era. **The requirement halves and the ≥10%-filter claim is unconditional.** The caveat
> below is kept rather than deleted, because how it was resolved is the useful part.

**A load-bearing assumption that the audit would not let stand.** The table above halves
the requirement on the premise that the LETF closure was evaluated against a 0.1%/day
target, inferred from 21.07 ≈ 2 × 10 bps. That inference is **under-determined**: 21.07 is
equally consistent with other deployment assumptions, and `TARGET.md` records a target
that moved twice during the period the closure was made. If the closure was in fact made
against the 0.5%/day target of docs 292–293, the ratio is 10× rather than 2× and every
figure in the build-filter row changes. **The conclusion "extend `minute_aggs`" survives
either way** — it is driven by the bar, not the requirement — but the "clears the ≥10%
filter" claim does not, and should be treated as conditional until someone confirms which
target era that closure belongs to. The archive's `closed_on` field would have settled it,
which is precisely why inventing those dates was harmful.

Three further caveats, stated rather than buried. The 6.0–8.6% figure is a **ceiling**, not a
measured mean with an interval, so 1.91 is an upper bound on the deliverable Sharpe. The
15.06 bps window sd was measured on the 2024–2026 sample and LETF rebalance mechanics
were not stationary over 2016–2026. And the family also needs point-in-time shares
outstanding *back to 2016*, which doc 298 confirmed is served but not how far back.
**All three are prerequisite checks, not results.** This is a pre-registration to write,
not a finding to act on.

The first real use of the archive also caught a defect in the archive: my LETF record
originally named only `lower_requirement`, omitting the intraday-data keystone. The
record was wrong in exactly the way the module exists to prevent. Corrected in the
seeder and regenerated.

---

## 3. Pricing an experiment before it runs

`src/epistemics/eig.py` computes, for a design specified before any data is touched:

* **expected information gain**, closed-form under a Gaussian conjugate model —
  `EIG = ½·ln(1 + ω·σ₀²/σₑ²)` nats;
* **severity** — the probability the test would have failed had the hypothesis been
  false. Popper via Mayo, as a number. An experiment that cannot fail cannot corroborate;
* **the multiplicity toll** — the amount by which registering this trial raises the bar
  for every other hypothesis in the queue.

The toll is the part standard experimental design does not model, and in a program gated
on a deflated Sharpe it is the dominant cost. Running a trial does not merely spend time;
it permanently raises the threshold everything else must clear. At 34 trials the toll of
one more is +0.0081 Sharpe on a 2.08-year sample (524 sessions, the length used elsewhere
in this document) and +0.0035 on 11 years (2,772). Small per trial, and
the reason the bar is 2.6 today. **A trial with negligible information gain is not free
and not harmless. It is a tax on every hypothesis still in the queue.**

Two design decisions worth recording because both came from getting it wrong first.

**The likelihood is tempered.** Classical EIG assumes the model is right; for market
data it is not. Following generalised Bayesian OED, the likelihood carries an epistemic
learning rate `ω ∈ (0,1]` that inflates the effective observation variance by `1/ω`. The
default is `ω = 0.25` — a governance choice, not a measurement, set pessimistically on
the program's own record of results that did not survive retest (docs 277, 296). `ω = 1`
is the optimistic bound and the gap between the two is the honest uncertainty band.

**A ceremonial test cannot be detected with an absolute threshold, and I tried.** My
first implementation flagged a design as worthless if its certification probability was
below 1% and its information gain negligible. Writing the test for it showed the flag
never fires, and the reason is a real invariance — but I attached it to the wrong
quantity, and published it that way.

> **CORRECTION (doc 300, 2026-09-20).** This section originally asserted that `p_cert`
> is "very nearly invariant in sample size", pinned at ≈2.9% "whether the sample is one
> year or twenty-five", and drew from that the general lesson "more data does not buy
> protection from multiplicity." **That is false**, and the same claim propagated into
> `eig.py`, `ATTEMPTS_LEDGER.md` and a commit message. The paragraphs below are the
> corrected version.

The invariance is real and it belongs to **`p_false_positive`** — the rate at which a
design certifies *nothing*. Because the bar and the estimator's spread carry the same
standard error, `bar/σₑ = √ω·(E[max]/se + 1.6449)` exactly, with `n` cancelling, so:

| | 1 yr | 4 yr | 11 yr | 25 yr | 100 yr |
|---|---|---|---|---|---|
| `p_false_positive`, any prior | 0.029342 | 0.029342 | 0.029342 | 0.029342 | 0.029342 |
| `p_cert` at `prior_sd`=0.001 | 0.0293 | 0.0293 | 0.0293 | 0.0293 | 0.0293 |
| `p_cert` at `prior_sd`=0.5 **(the default)** | **0.0333** | **0.0454** | **0.0728** | **0.1188** | **0.2413** |

`p_cert` is invariant only as `prior_sd → 0`, because it also carries the prior through
`√(σ₀² + σₑ²)`, which does not cancel. At this module's own default prior it spans a
factor of **7.2**. The published 2.9342% figure is real — it is just the false-positive
rate, not the certification probability.

**How the error survived, which is the part worth recording.** The single test certifying
the claim used `prior_sd = 0.001` — 500× below the module's default of 0.5, and a value
appearing nowhere in the live queue. It passed, and a green test is what licensed
publishing the sentence. That is precisely doc 290's rule — *a weak baseline manufactures
lift* — committed against the program's own machinery by the person who wrote the rule
down two sections earlier. The corrected test now asserts both halves: invariance at a
point-mass prior, and a spread of ≥0.05 at the default one.

The corrected lesson is narrower and still worth having: **more data does not reduce the
rate at which a null design certifies.** It does lower the bar — which is exactly why the
LETF calculation above turns on sample length — and those two facts are consistent, not
contradictory: the bar and the noise fall together, so their ratio, and with it the
false-positive rate, is fixed.

The fix to the flag stands: replace the absolute floor with a ratio, `informativeness =
p_cert / p_false_positive`. A design whose pass is as likely under the null as under the
prior proves nothing whichever way it lands. This is doc 290's rule applied to the design
rather than to a model, with the null as the baseline.

One caveat that the original section overstated away. For an *unclustered* design
`p_false_positive` depends only on `(n_trials, ω)`, so within a single planning run
`informativeness < 1.5` reduces to an absolute floor on `p_cert` at `1.5 × p_fp` ≈ 4.4%.
It is not a floor across runs, and not one for clustered designs — but the difference
from the threshold it replaced is smaller than "replace the floor with a ratio" implies.

### Running it on the actual queue

`data/research/design_queue.json` holds the program's live and queued designs with their
priors declared and committed — including, for each, why that prior and not another. At
33 registered trials, ω=0.25:

| design | EIG (nats) | lift | bar | verdict |
|---|---|---|---|---|
| `letf_..._if_minute_extended` | **0.337** | **14.42** | 1.15 | **ADMISSIBLE** |
| `sevp_event_vol_carry` | 0.144 | 4.85 | 2.29 | ADMISSIBLE |
| `anomaly_vol_scale_monetisation` | 0.135 | 2.31 | 2.03 | ADMISSIBLE |
| `letf_close_window_retest` | 0.107 | 5.64 | 2.32 | ADMISSIBLE |
| `overnight_etf_excess_of_cash` | 0.079 | 1.89 | 1.14 | ADMISSIBLE |
| `vol_score_risk_shaping` | 0.037 | 1.17 | 2.03 | CEREMONIAL |
| `kalshi_zero_capital_shadow` | 0.024 | 1.47 | 4.23 | CEREMONIAL |
| `rv_forward_shadow_ledger` | 0.007 | 1.15 | 7.72 | CEREMONIAL |
| `rocket_gate_forward_ledger` | 0.002 | 1.01 | 10.92 | CEREMONIAL |

> **CORRECTIONS (doc 300).** This table has moved three times and every move is a
> correction rather than a re-tune, so all three are recorded:
> 1. Three rows had understated bars because `appraise()` computed the bar on the
>    *nominal* sample while computing the estimator's spread on the *effective* one. Fixed;
>    the three affected rows are the three with `icc > 0`. No verdict flipped.
> 2. `n_obs` for the two LETF rows was 650 and 2,772 — an estimate and a nominal 11×252.
>    They are now 666 and 2,690, **measured** from the warehouse. The document had already
>    corrected these in prose while `design_queue.json` still held the old values, so
>    `epistemics.py plan` — the command this section tells you to run — printed the numbers
>    the same document called wrong. Caught by the doc-300 completeness critic.
> 3. `letf_..._if_minute_extended` was **INFEASIBLE** when this was written, blocked on
>    `intraday_tape_pre_2020`. **That keystone landed 2026-09-21** and the design is now
>    ADMISSIBLE and top-ranked. The counterfactual the row existed to price became real.

Two things fall out, and the second is unwelcome.

**The most informative experiment available to the program was the one it could not
run — and now it can.** The LETF re-test on the full minute sample carries 0.337 nats,
2.3× the next best, at a lift of 14.42 against a next-best of 5.64. When this section was
written it was infeasible for exactly one reason: `minute_aggs` stopped at 2024. The
planner, which knows nothing about §2, independently priced the same procurement item as
the highest-value action on the board — and on 2026-09-21 that item was acquired
(2,691 sessions, 3.94 billion bars, 0 download errors). It is now the top-ranked
admissible design. Nothing has been *measured* yet; what changed is that it can be.

**Four armed or queued collectors cannot produce a result that clears the promotion bar.**
`rocket_gate_forward_ledger` (n=30, bar 10.92, lift 1.01) and `rv_forward_shadow_ledger`
(n=60, bar 7.72, lift 1.15) are *currently collecting*. At those sample lengths the
annualised-Sharpe standard error is 2.90 and 2.05, so the deflated bar sits at ten and
eight Sharpe respectively, and a pass is essentially as likely under the null as under
the prior.

The necessary caveat: these collectors do not state their gates as Sharpe certifications.
The rocket-gate's declared criterion is "CI-lo > 0 plus agreement across halves at n≥30",
which is a weaker and perfectly coherent test. So the honest claim is not that they are
badly designed for their stated purpose. It is this:

> **An armed collector can pass its own stated gate and still not license deployment,
> because its gate is weaker than the program's promotion bar.** Doc 275 killed
> per-experiment p<0.05 as a promote criterion in prose. This prices the gap per
> collector, in advance.

That does not mean stopping them — they are nearly free, and a *negative* result from a
cheap collector is still worth having, which is what the `UNDISCRIMINATING` verdict is
for as distinct from `CEREMONIAL`. It means no result from any of them may be described
as a pass without also stating the bar it did not clear.

### Anti-mode-collapse

`diversity_bonus()` rewards a design in an under-represented mechanism class and
penalises one that crowds an already-dominant family.

**And here the measurement contradicted my framing for the second time.** I wrote this
term expecting to show the program's search had collapsed into one neighbourhood. It has
not. The 25 archived closures spread across 10 mechanism classes at entropy **2.068 nats
against a uniform maximum of 2.303 — 89.8% of maximum**, with the largest class (per-name
price/momentum) at only 8 of 25.

The thing I was conflating it with is real but different. Doc 290 and doc 291 both found
that *generic* features carried whatever signal was present and that momentum-x's own
vocabulary contributed ≈0. That is concentration in **feature** space, inside the families
that were tested. It is not concentration in **hypothesis** space, and the entropy term
does not measure it. Two distinct failure modes with similar-sounding names; only the
first one is what this guards.

So the term stays, as a guard on future selection rather than a diagnosis of past
selection. One caveat on the number itself: entropy depends on how finely the classes are
cut, and the assignment in `data/research/design_queue.json` is a hand-made judgement — a
coarser taxonomy would show more concentration. The histogram ships with the number for
exactly that reason.

---

## 4. The first-principles gate

Between "revived" and "registered as a trial" sits `first_principles_gate()`, checking
constraints no amount of modelling argues around: cost admissibility against true NBBO
plus regulatory fees (never a Roll estimate — measured 0.56×–3.5× off and non-positive
on 29.5% of ticker-days), executability at this broker, capacity against ADV, and the
doc-293 ≥10%-of-requirement filter. Any argument left unsupplied is reported as
`UNCHECKED` rather than silently passing, so a partial gate never reads as a full one.

Its purpose is narrow and mercenary: stop a revived hypothesis consuming a registered
trial — and therefore raising the bar for everything else — when it is already dead on
arithmetic.

---

## 5. Method rules this adds

1. **Every closure names its class and, if it is a statement about us, its keystone.**
   A closure that cannot name one is an abandonment and is filed as `ABANDONED`.
2. **`REFUTED_BY_NATURE` requires a recorded effect and interval.** Without them the
   honest class is `UNDERPOWERED`. Twelve historical records fail this and are marked.
3. **Anomalies are recorded at burial time.** A family dies on its primary endpoint
   while leaving behind a result nobody asked for — doc 290's predictable volatility
   scale, doc 289's conserved-but-decoupled attention, doc 297's T-bill double-count.
   These are the program's least crowded source of hypotheses, because by construction
   nobody was looking for them. `scripts/epistemics.py anomalies` lists all seven
   currently recorded.
4. **Retro-validate on every capability landing.** Not from memory. When a feed, a
   sample extension, or a target re-scope lands, run `epistemics.py revive`.
5. **Price a trial before registering it.** A design that is `CEREMONIAL` or
   `UNDISCRIMINATING` must not be registered; it cannot teach and it raises the bar.
6. **Market-evidence closures do not revive on capability.** Only on an argued
   market-structure change, and that argument is Pierce's to make.

---

## 6. What this does not do

It does not manufacture an edge. Under the doc-297 identity — `return ≡ deployment ×
turns × net-per-ticket` — better epistemics operate on none of the three terms. What
they do is stop the program spending trials on questions that cannot answer, and make
sure that when a capability lands, the questions it unblocks are found by a query rather
than by luck.

The honest summary of the exercise: I built it expecting to find that the graveyard was
full of questions we had asked badly, and found instead that 68% of it is measurement.
The architecture's first real output was to make the program's negative result *harder*
to dismiss, and to convert one arithmetic closure into a single, bounded, checkable
procurement item. That is a smaller claim than the framing promised and a more useful one.

---

## 7. Open, in order

1. **Extend `minute_aggs` 2024→2016** to match the daily warehouse. It is the
   **highest-EIG action the planner can see** (0.344 nats, lift 14.67, against a next-best
   of 0.144 / 5.55) and the sole blocker on the one family the engine revived. Verify
   point-in-time shares-outstanding depth at the same time; doc 298 confirmed the feed is
   served but not how far back.

   > **CORRECTION (doc 300).** This originally called it "the top item in the keystone
   > census". It is not. The census ranks by *families blocked*, and
   > `epistemics.py keystones` puts `intraday_tape_pre_2020` at **1 family, fifth of
   > eight**; the top row is `lower_requirement` at 5. Two different rankings —
   > families-blocked and expected-information-gain — were conflated, and the procurement
   > recommendation was framed on the wrong one. The recommendation itself survives,
   > because EIG is the ranking that should drive it, but the stated reason was wrong.

   **DONE 2026-09-21.** 2,691 sessions / 2016-01-04→2026-09-17 / 3.94 billion minute bars.
   The bar moved 2.319 → 1.153, against the 1.137 this document predicted at an assumed
   2,772 sessions.
2. **Backfill effect sizes and intervals** onto the thirteen non-admissible records, from
   the documents. Until then those closures cannot be retro-scored.
3. **Pre-register the LETF re-test** if and only if (1) succeeds, with an executable
   prereg fixture per doc 296 and a declared prior.
4. **Wire `epistemics.py revive` into the nightly capability check** so a landing feed
   triggers the query automatically.
5. **State the bar alongside any collector result.** Four armed or queued collectors
   have gates weaker than the promotion bar; none of their outcomes may be reported as a
   pass without the bar it did not clear.
6. **Pierce:** the five `lower_requirement` revivals are live only because the target
   moved to 5 bps/day. If the target moves again, re-run the query — in either direction.
