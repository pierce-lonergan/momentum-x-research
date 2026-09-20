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
cannot-be-retro-scored count rises **11 → 12**. `closed_on` is blank everywhere, which
also removes the only machine-readable field that could have settled the target-era
question in §3. `seed_stepping_stones.py` now carries the rule explicitly.

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

Also unresolved, and flagged here because doc 299 acts on it: **the LETF revival's 2×
requirement rescale is under-determined.** It assumes the closure was evaluated against a
0.1%/day target, inferred from 21.07 ≈ 2 × 10 bps. `TARGET.md` records a target that moved
twice in the relevant period. The *procurement conclusion* survives either way because it
is driven by the bar rather than the requirement, but the "clears the ≥10% build filter"
claim is conditional.

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

1. Re-run the audit's verification pass — 79.6% of findings are unadjudicated.
2. Resolve the `_plausibility` dead-code claim: if every wrong-signed record is also market
   evidence, the guard doc 299 cites never executes on real data.
3. Settle which target era the LETF closure belongs to, which makes the §3 build-filter
   claim unconditional or kills it.
4. Backfill real effect sizes and intervals for the 12 inadmissible records, from the
   documents — now the only honest way to raise that count.
