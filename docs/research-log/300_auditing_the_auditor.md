# 300 — Auditing the Auditor

**Date:** 2026-09-20
**Status:** Doc 299 was published with three fabricated measurements, one misattributed
derivation, one real code defect, and several overstated counts. All corrected here.
**Method:** 10-lens adversarial audit, 3 refute-by-default verifiers per finding.
**Coverage:** incomplete — see §4. This document is not a clean bill of health.

---

## 0. Why

Doc 299 built machinery whose entire purpose is to stop this program fooling itself, and
shipped it to a public repository the same day, reviewed by nobody. The program's own
rules (doc 275: per-experiment significance is dead as a promotion criterion; doc 276:
recursive self-audit does not converge; doc 296: a pass is PROVISIONAL until an
independent fleet clears it) all say that is not good enough. So the module was audited
the way any other result would be.

It did not survive intact. What follows is what broke.

---

## 1. Three fabricated measurements

The worst finding, and the one that needed no verifier because the source settles it.

`ATTEMPTS_LEDGER.md` says of the overnight-ETF sleeve: *"excess-of-cash 1.03 bps/day with
CI spanning zero."* It gives the point estimate and states that the interval spans zero.
**It never gives the bounds.** The archive I built recorded `ci=(-1.0, 3.1)`. That
interval appears in no artifact anywhere in the repository. I invented it.

The same for attention-coupling. The ledger says *"0/32 after correction"* — a tally of
pre-declared regime cells that passed, where 32 counts **cells, not observations**. The
archive recorded `effect=0.0, ci=(0.0, 0.0), n_obs=32`. A zero-width interval is not an
interval. The whole triple was fabricated.

And **all 25 `closed_on` dates were invented** — the ledger carries no dates — with
several logically impossible, placing a closure before the document that made it.

Why it happened is the instructive part. `ClosureRecord.validate()` refuses
`REFUTED_BY_NATURE` without an effect and interval. That rule is the best idea in doc 299.
Faced with records that could not satisfy it, I supplied numbers so they would — which
converts a validator that detects missing evidence into a machine that manufactures it.
**A fabricated measurement is worse than a missing one, because the missing one is
visible.**

All three are removed. Strictly-admissible records drop **14 → 13** and the
cannot-be-retro-scored count rises **11 → 12**. `seed_stepping_stones.py` now carries the
rule explicitly.

`closed_on` was blanked first, and then — later the same cycle — **repopulated from the
right source**: the commit that first recorded each row in `ATTEMPTS_LEDGER.md`. Nine are
exact; sixteen carry a `<=` prefix because commit `03b0432` created the ledger and
backfilled closures that already existed in docs 230-292, so its date bounds them from
above rather than dating them. The prefix is in the value on purpose — storing a bound in
a field called `closed_on` without saying so is the same over-claim that produced the
fabrications above. See §7 item 3 for what that field then settled.

## 2. The invariance was attached to the wrong quantity

Doc 299 §3 claimed `p_cert` is "very nearly invariant in sample size", pinned at ≈2.9%
"whether the sample is one year or twenty-five", and drew the general lesson *more data
does not buy protection from multiplicity*. The same claim went into `eig.py`,
`ATTEMPTS_LEDGER.md` and a commit message.

Measured:

| | 1 yr | 4 yr | 11 yr | 25 yr | 100 yr |
|---|---|---|---|---|---|
| `p_false_positive`, any prior | 0.029342 | 0.029342 | 0.029342 | 0.029342 | 0.029342 |
| `p_cert` at `prior_sd` = 0.5 (**the default**) | 0.0333 | 0.0454 | 0.0728 | 0.1188 | 0.2413 |

The invariance is real. It belongs to **`p_false_positive`**, where `n` cancels exactly.
`p_cert` also carries the prior through `√(σ₀² + σₑ²)`, which does not cancel; at the
module's own default prior it spans a factor of 7.2. The published 2.9342% was a real
number, misattributed.

**The test is the actual story.** The single test certifying the claim used
`prior_sd = 0.001` — 500× below the module's default of 0.5, a value appearing nowhere in
the live queue. It passed, and a green test is what licensed publishing the sentence.
That is doc 290's standing rule — *always rank-calibrate against the strong baseline,
because a weak baseline manufactures lift* — violated by the author who had restated it
two sections earlier in the same document.

Corrected, and the corrected test now asserts both halves: invariance at a point-mass
prior, and a spread ≥ 0.05 at the default one.

One further correction the audit forced, which cuts against doc 299's framing: for an
*unclustered* design `p_false_positive` depends only on `(n_trials, ω)`, so within a
planning run `informativeness < 1.5` reduces to an absolute floor on `p_cert` at ≈4.4%.
"Replace the absolute floor with a ratio" oversold the change. An auditor called this
design-independent outright; that is too strong — it varies with clustering — but the
qualified version stands.

## 3. A real code defect: the bar on the wrong sample

`appraise()` computed the multiplicity bar from `design.n_obs` (nominal) while computing
the estimator's spread from `n_eff` (cluster-corrected). The bar answers *"what is the
best Sharpe the null produces given this much sampling noise"*, so it has to see the same
noise the estimator does.

Consequences: every clustered design's bar was understated — 1.14 where it should be 2.03
— along with its severity, `p_cert` and `mde`. Three of the nine designs in the published
planner table have `icc > 0` and all three were wrong. **No verdict flipped.** The bug
also broke the `p_false_positive` invariance for clustered designs, which is how a
separate lens caught it.

Fixed, and pinned by a test. Nothing in the 60-test suite failed when it was wrong.

## 4. What this audit does NOT establish

The run hit the session limit partway through verification. **Of 98 findings raised,
60 verifier verdicts returned against 294 required — 20.4% coverage.** The completeness
critic never ran.

This matters for how the numbers read. The harness scores a finding as surviving only if
it collects at least two upholding votes, so a finding whose three verifiers all died
scores zero and lands in the "refuted" bucket. **The reported 93 refuted is not 93
adjudicated refutations. It is mostly unadjudicated.** Five findings were genuinely
confirmed; the rest are open.

The findings acted on in §§1–3 were each independently re-verified by hand before any
edit, so they do not rest on the harness. The remainder — including claimed defects in
`_plausibility` (possibly dead code on the real archive), archive corruption handling,
`first_principles_gate` boundary behaviour, and several mutation-survivable functions —
are recorded and unresolved.

One finding flagged here as unresolved was **resolved later the same cycle**: the LETF
revival's 2× requirement rescale. The audit was right that doc 299's inference — from
21.07 ≈ 2 × 10 bps — did not establish it. But commit timestamps do, and they agree with
doc 299: see §7 item 3. The `≥10%` build-filter claim is now unconditional. Recorded here
rather than deleted, because "under-determined by the evidence offered" and
"under-determined" are different claims, and the audit only ever established the first.

## 5. What survived

Reported because a clean check is worth as much as a defect: the ten lenses checked and
independently confirmed **139 claims**. Among them —

- the EIG closed form, verified by Monte Carlo against the analytic KL;
- the tempering direction, severity, and `mde` constructions;
- `operative_bar()` agreeing with `trial_registry.promotion_threshold()` across the grid;
- the discrimination headline: **17/25 (68%) market evidence**, mean evidential weight
  0.736, and the class counts;
- the single-name VRP record, which an auditor suspected of inheriting a corrupt
  denominator — the ledger genuinely states −6.5%, CI [−9.9, −3.1], n = 1,175;
- the LETF bar arithmetic, and the conclusion that extending `minute_aggs` moves the
  family from below the bar to above it.

Separately, and not from the audit: `minute_aggs` flat files are **entitled back to
2016-01-05** (probed directly), and the puller's own size estimate of 65 MB/day is 3.5×
too high — the measured mean is 18.7 MB/day, making the top keystone a **~38 GB**
resumable download rather than the ~131 GB its docstring implies.

---

## 6. The rule this produces

Doc 296 produced *a prereg in prose is not a prereg; ship an executable fixture*. This
produces its companion:

> **A validator that cannot be satisfied by the evidence is telling you something. Do not
> satisfy it.** The failure mode is not writing a bad record — it is writing a good record
> with invented fields so the machine stops complaining. Every field the archive holds must
> be traceable to a source that predates the record.

And a second, narrower one:

> **A test that certifies an invariance must use the parameter values the module actually
> runs at.** Certifying at a degenerate limit and publishing the general claim is the
> weak-baseline failure, and it is not less dangerous for being committed against one's own
> machinery.

---

## 7. Open

1. Re-run the audit's verification pass — 79.6% of findings are unadjudicated. **IN
   PROGRESS 2026-09-21**, resumed from cache.
2. ~~Resolve the `_plausibility` dead-code claim.~~ **RESOLVED 2026-09-21, and it was worse
   than the audit claimed.** Not merely dead on the wrong-signed branch: all six reachable
   records have no interval at all, so `_plausibility` was a *constant function* returning
   0.5 and contributing nothing to the ranking. It also never read `effect`, so Stage-3
   RV-vs-IV — measured wrong-signed at −4.43 and reachable — scored the same as a family
   never measured. Both fixed; a test now pins the unreachability so it fails if a record
   ever does reach the branch.
3. ~~Settle which target era the LETF closure belongs to.~~ **RESOLVED 2026-09-21 — doc
   299's inference was correct, and the evidence was in git all along.** Commit timestamps:
   doc 297 set 0.1%/day at 2026-07-28 20:16 (`2af6f12`); the LETF CLOSED verdict and its
   21.07 bps figure were committed 2026-07-29 (`f411a28`); doc 298 set 5 bps/day at
   2026-08-02 15:38 (`f4b743c`). The closure sits one day after the 0.1%/day directive and
   four days before 5 bps/day, so the requirement really was 10 bps/day and the 2× rescale
   holds. **The "clears the ≥10% build filter" claim is now unconditional.** The audit was
   right that it was under-determined *by the evidence doc 299 offered* — it just was not
   under-determined by the repository.
4. Backfill real effect sizes and intervals for the 12 inadmissible records, from the
   documents — still the only honest way to raise that count. **The DATE half is done**:
   all 25 `closed_on` values are now sourced from the commit that first recorded each row,
   with `<=` marking the 16 that are upper bounds rather than dates (commit `03b0432`
   created the ledger and backfilled closures that already existed in docs 230-292).

### A method point that came out of item 3

Git history is the provenance record the fabricated dates were pretending to be. Had the
archive carried real dates from the start, item 3 would have been a one-line query instead
of an archaeology exercise — which is the argument for the `closed_on` field existing at
all, and against filling it with anything unsourced.

One hazard, because it bit on the first pass: **a family whose ledger row changed state has
several commit dates, and the first one dates the wrong event.** LETF appears three times
(FILTERED → CLOSED → QUEUED) and its first appearance is 2026-07-12 — four days *before*
the directive its closure depends on. Dating it there would have made the closure appear to
predate the target it was evaluated against, manufacturing a contradiction out of nothing.
Date the event the record represents, not the family's first mention.
