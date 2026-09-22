# 301 — Reading the Sources

**Date:** 2026-09-21
**Status:** The thirteen inadmissible records were read out of their own documents.
All thirteen survived an independent anti-fabrication check. Two records turned
out to claim the strongest closure class on measurements that cannot support it.
**The published 68% market-evidence headline is wrong. It is 60%.**
**Method:** one agent per record reading only its cited documents; each extraction then
attacked by a second agent that greps the whole repository for every reported number.

---

## 0. Why this was done manually

Doc 300 left one item genuinely open: thirteen records claimed `REFUTED_BY_NATURE` —
"measured with adequate power; the effect is absent or wrong-signed" — without carrying the
measurement that licenses it. The validator refused them, correctly.

The instruction was to do this as a one-pass manual extraction rather than build a parser,
and that was right. A parser would have to decide which cell is the primary endpoint, and
that decision is exactly where the judgement lives.

The risk was obvious. A day earlier the author of that archive was caught fabricating
measurements to satisfy that same validator. So the extraction ran under one rule, stated
at the top of every agent's prompt:

> **NOTHING IS INVENTED TO SATISFY THE VALIDATOR.** Your job is not to make these records
> pass. "Not found" is a correct and valuable answer. An invented number is the worst
> possible outcome — worse than returning nothing, because a missing number is visible and
> a fabricated one is not.

Every extraction was then passed to a verifier whose explicit job was to grep the whole
repository for each reported number and return `misattributed` if any value appeared only
in the extraction. The result: **12 `confirmed_verbatim` and 1 `not_found`** — where
`not_found` is the *correct* verdict for the one record whose measurement genuinely does not
exist. **Zero fabrications, zero misattributions.**

---

## 1. What the sources actually carry

| record | recovered, verbatim | source | supports the class? |
|---|---|---|---|
| SS0001 per-name direction | −0.04%, CI [−0.3, +0.2] | 250:8 | **yes** — effect *absent* |
| SS0002 exit-timing | −1.13%, CI [−1.70, −0.56] | 235:111 | **yes** |
| SS0004 rocket ex-ante | −4%, CI [−6.8, −1.1] | 242–248 | **yes** |
| SS0005 deep-set tape | −6.6pp, CI [−10, −2.6] | 248, 256–257 | **yes** |
| SS0006 catalyst/news | **+2.82%**, CI [−0.8, +5.1] | 260:16 | **NO** |
| SS0014 H-LOCAL | **+0.136**, no interval | 290:21 | **NO** |
| SS0008 multi-day dilution | −1.8%, no interval | 258 | partial |
| SS0010 exit-posture flip | −$1,582, CI [−8,958, +5,866], n=14 | 280 | partial |
| SS0012 cohort-relational | −0.019, no interval | 289 | partial |
| SS0016 short-tenor RV | −2.7%, no interval | 292 | partial |
| SS0003 loss-cap variants | +1.26, CI [−0.68, +3.19] — **secondary** | 236:120 | no |
| SS0007 coiled-catalyst | +1.0, CI [+0.23, +1.92] — **secondary** | 254:38 | no |
| SS0013 attention-coupling | **no effect exists** | 290 §5 | no |

### The two reclassifications

**SS0006, catalyst/news amplification — `REFUTED_BY_NATURE` → `UNDERPOWERED`.** The
recovered *primary* endpoint is positive in both samples. Doc 260:16, verbatim:

> `| LLM L-S median **f10** | +2.82% | **+1.35%** | positive but **halved**; n≈93/tranche → **CI crosses 0** (s7 CI[−0.8,+5.1]) |`

The ledger's verdict — "gate fails" — is accurate. The pre-registered gate did fail. But a
failed gate on a **positive**, zero-spanning estimate at n≈93 per tranche is absence of
evidence, not evidence of absence. That is precisely the mis-filing doc 275 made
acceptance-test power mandatory to prevent, and it had been sitting in the archive under
the strongest available label.

**SS0014, H-LOCAL micro-regime retrieval — `REFUTED_BY_NATURE` → `UNDERPOWERED`.** Worse,
in a way: the frozen gate metric came out **positive** — the local model *beat* the global
one — and the gate failed on the permutation p-value by a single draw. Doc 290:21:

> `**G1 (local beats global): FAIL — by exactly one permutation draw** (Δ=+0.136, p=0.00995 vs frozen α=0.005)`

Out-of-sample Spearman: global GBM 0.175 versus kNN 0.311. No interval stated anywhere.
Both of doc 290's original findings stand and are kept in the record — the apparent kNN
lift *was* a weak-baseline artifact, and roughly four neighbourhoods *do* lie on one smooth
surface. But a positive effect one permutation draw short of a frozen α is underpowered,
not refuted by nature. The closure may well be right on economic grounds; this measurement
does not license the strongest class.

### The third thing, which is a confirmation rather than a correction

**SS0013, attention-coupling.** The extraction independently confirms that the measurement
this class requires **does not exist in any cited document**. The primary endpoint is doc
290 §5's sixteen-cell pre-declared coupling test, and what it reports is a *tally* — "Zero
survive." of "16 pre-declared cells × 2 statistics = 32 tests" — plus a hedged directional
description whose tilde is in the source.

This is the record whose earlier version carried a fabricated `effect=0.0, ci=(0.0, 0.0),
n_obs=32`. An independent reader, told only to report what the document says, found that
the corpus contains no such thing. The fabrication invented exactly what is absent.

---

## 2. The headline number is wrong, and it moves against the program

Doc 299 published, and doc 300 re-confirmed, that **17 of 25 closures (68%) are
measurements of the market** — the figure that makes the no-edge thesis hard to dismiss as
an instrument artifact. With two records moved out of `REFUTED_BY_NATURE`:

| | published | corrected |
|---|---|---|
| market evidence | 17/25 (**68%**) | **15/25 (60%)** |
| evidence about us | 8/25 | **10/25** |
| mean evidential weight | 0.736 | **0.676** |
| mechanically revivable | 6 | **8** |
| strictly admissible | 12/25 | **14/25** |

Sixty percent is still a majority, and the thesis survives it. But it is eight points
weaker than what was published, and the correction came entirely from reading the sources
the archive already cited. Nobody had.

`n_obs_sufficient` is now the **second-most-blocking keystone** in the census, behind
`lower_requirement`. Neither reclassified record appears in the revival queue, because that
keystone is not available — they are revivable in principle and blocked in fact, which is
the correct state.

---

## 3. What the corpus systematically does not record

Of thirteen records, **six** now carry a point estimate with no interval, and **only one**
carries the full effect-plus-interval-plus-n triple the class demands. The binding
constraint is not the effect and not the interval — it is **`n_obs`**.

The documents state corpus sizes readily and cell sizes almost never. Doc 235 gives
"632,020 rows, 10,786 ticker-days" while its primary endpoint is a top-2 selection over a
pooled subset. Doc 250 gives 10,254 candidates while its quoted cell excludes 2026 and
covers only 2024+2025 candidates, a count no document states. (An extractor originally
justified the gap by pointing at incomplete path coverage; its verifier corrected that —
doc 288 audited exactly this and found coverage is 100%. The conclusion held, the stated
reason did not, and the verifier caught it. That is the phase earning its keep on something
other than fabrication.) Every extractor was warned about this trap specifically, and
every one of them left `n_obs` empty rather than attach a corpus to a subset.

So the honest state of the archive is no longer "inadmissible, measurements unknown." It is
**"inadmissible for a specific, documented reason: the cell n is not in the source."** That
is a much better place to be, and it yields a concrete next action — if those records are
to become admissible, the n has to come from re-running the analysis, not from reading.

**It also raises a question I am deliberately not answering by weakening the validator.**
One could argue that a day-blocked interval already encodes the effective n, so requiring
`n_obs` separately is stricter than the corpus can satisfy. Perhaps. But relaxing an
admissibility rule because records fail it is the exact move that produced the
fabrications, one document ago. The rule stays; the records stay inadmissible; the reason
is now written down.

---

## 3b. A defect in the corpus, not in the archive

The SS0001 verifier found something while checking the interval, and it is the most
consequential single thing in this document after the two reclassifications.

**Doc 250 claims day-blocked confidence intervals. The code does an i.i.d. bootstrap.**

Doc 250:50 describes the method as `day-block CIs`. But
`scripts/rocket_basket_exits_doc250.py:81` is:

```python
rng = np.random.default_rng(seed)
m = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(reps)]
```

— a flat i.i.d. resample of candidate rows with no session grouping, called at line 90 on
the pooled 2024+2025 row vector. Gapper candidates cluster heavily within sessions, which
is precisely *why* the document claims day-blocking. An i.i.d. bootstrap therefore
**understates** the interval. At doc 278's measured ~2.5× SE inflation the honest interval
is nearer **[−0.67, +0.58]** than the published **[−0.30, +0.20]**.

What this does and does not change:

* **The closure survives.** The interval brackets zero either way, and doc 250's own
  sentence adds that after "a realistic ~1% small-cap round-trip spread/slippage, the
  basket is clearly negative."
* **The adequate-power leg does not survive intact.** `REFUTED_BY_NATURE` asserts
  measurement with adequate power, and the power here is ~2.5× worse than the published
  interval implies.
* **It was not silently recomputed.** Changing a published number requires re-running the
  analysis against the warehouse, not editing a CI in an archive. The stated interval is
  carried with the caveat attached.

This is doc 296's prose-versus-code failure class — a specification that says one thing
while the runner does another — found in a document written before that rule existed. It
is worth noting that doc 288's re-audit examined this very script's denominator and
returned "**doc-250 EOD-hold denominator — CLEAN**", because it was checking *path
coverage* rather than *bootstrap structure*. A clean audit of the wrong property.

## 4. Method rules this adds

1. **An extraction phase must be paired with an adversarial verification phase that greps
   for every reported number.** 13/13 came back clean here, which is the point: the check
   is cheap and its value is that a fabrication cannot survive it.
2. **Distinguish "the gate failed" from "nature refused."** Two records conflated them, and
   both were filed under the strongest label the taxonomy offers. A failed gate on a
   positive, zero-spanning estimate is `UNDERPOWERED`.
3. **Report `is_primary_endpoint` on every extracted cell.** Three of thirteen recovered
   figures were secondary cells, and one of them (SS0007, +1.0 CI [+0.23, +1.92]) is
   positive and CI-excluding-zero — it would have looked like a contradiction of its own
   closure if reported as primary. The extractor flagged it; nothing else would have.
4. **Never compute an n.** Say "not stated."

---

## 5. Open

1. The `n_obs` gap on eleven records. Requires re-running analyses, not reading.
2. **The doc-250 bootstrap.** Re-run `rocket_basket_exits_doc250.py` with a session-blocked
   resample and publish the corrected interval. Until then SS0001's interval is known to be
   too narrow. Grep the rest of the corpus for the same pattern — any script claiming
   day-blocked intervals whose `boot_ci` resamples flat rows has the same defect, and doc
   288's audit would not have caught it because it checked coverage rather than structure.
3. SS0010's recovered cell (n=14, CI spanning ±$8–9k) does not match the ledger's
   "overnight robustly negative" claim. Either the extractor found a secondary cell or the
   ledger overstates. Not resolved here; flagged rather than guessed.
4. The LETF pre-registration, drafted separately.
