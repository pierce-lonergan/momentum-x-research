# 303 — The LETF Kill, and the Amendment Nobody Applied

**Date:** 2026-09-22
**Status:** LETF close-window rebalance-flow harvest is **VOID**. Draft 302 is withdrawn.
**Trial 34 was not consumed. The registry bar stays at 33 trials.**
**Method:** the directive's cost kill was executed. It did not fire. The family died anyway,
on an adjudication that has been on disk since 2026-07-29.

---

## 0. The answer

The instruction was to run a zero-cost kill on LETF: measure the true all-in round-trip cost at
the 15:50 ET close window, and if it exceeds the ceiling, declare the family dead on arithmetic.

**The cost was measured. It does not kill the family.** All-in round trip on the primary tier is
**0.575 bps** including the SEC/TAF/CAT schedule — under a quarter of the threshold that would
have fired.

**The family is dead regardless**, and it was already dead before this session. Doc 298's M4
re-score adjudicated LETF at the 5 bps/day target on **2026-07-29**, concluded **STAYS CLOSED**,
and recommended a specific ledger amendment naming the binding ground. *The amendment was never
applied.* Two months later the unamended ledger row was read, its stated ground was found to have
evaporated under the target change, and the family was revived in doc 299 — on exactly the ground
M4 had already dismissed as the non-binding one.

Three separate things in this session turned out to be the same error: **a summary line read
without the adjudication that follows it.** It killed the LETF revival, it killed the SS0014
candidate, and it produced the double-counted cost threshold in Draft 302.

---

## 1. Deliverable 1 — the NBBO close-window cost table

Fresh measurement, this session: `scripts/true_nbbo_cost.py`, 9 ETFs × 20 sessions × 7 ET clock
instants, **576 true-NBBO observations**, read-only GETs, no order path.
Artifact: `data/research/doc298/letf_kill_1550.json`.

Round trip = one full quoted spread. Fees = **+0.21 bps** (SEC + TAF + CAT), per the directive.

| ticker | n | median RT (bps) | p75 | CI95 (session-clustered) | depth @ touch | **all-in (+fees)** |
|---|---|---|---|---|---|---|
| **SPY** | 28 | **0.261** | 0.389 | [0.258, 0.263] | $12.3M | **0.471** |
| **QQQ** | 83 | **0.282** | 0.419 | [0.279, 0.417] | $8.5M | **0.492** |
| **IWM** | 117 | **0.348** | 0.669 | [0.342, 0.350] | $6.9M | **0.558** |
| **DIA** | 133 | **0.570** | 0.763 | [0.562, 0.579] | $2.1M | **0.780** |
| SOXX | 40 | 1.261 | 2.099 | [1.043, 1.798] | $2.1M | 1.471 |
| XLF | 14 | 1.737 | 1.750 | [1.718, 1.750] | $74.8M | 1.947 |
| XBI | 15 | 2.492 | 3.056 | [1.944, 3.056] | $3.1M | 2.702 |
| GDX | 132 | 1.064 | 2.072 | [1.047, 1.929] | $2.0M | 1.274 |
| TLT | 14 | 1.223 | 1.232 | [1.216, 1.232] | $47.3M | 1.433 |
| **primary tier, equal-wt** | | **0.365** | | | | **0.575** |
| **all 9, pooled** | 576 | **1.064** | | | $6.9M | **1.274** |

**By clock time, pooled across all 9:**

| 09:45 | 10:30 | 11:30 | 13:00 | 14:30 | 15:30 | **15:50** |
|---|---|---|---|---|---|---|
| 0.939 | 0.679 | 0.674 | 0.570 | 0.578 | 0.559 | **0.862** |

Three things worth keeping:

1. **This independently replicates doc 298.** That measurement was 1,192 quotes over 13 proxy ETFs
   × 23 sessions spanning 2018-12-17 → 2026-07-27, and returned a blended **1.068 bps**. This one,
   a different ticker set over 20 sessions in 2026-08/09, returns **1.064 bps**. Two samples seven
   years apart in span agreeing to 0.004 bps is the strongest cost number the program holds.
2. **The close window is not the expensive window for liquid ETFs.** 15:50 sits at 0.862 bps
   against a 09:45 open of 0.939. It is modestly wider than the 15:30 trough (0.559) and still
   *cheaper* than the open. The standing `TARGET.md §2b` figure of **6.42 bps at 15:50** is a
   cross-tier median dominated by low-priced names and overstates this universe by **~7×**.
   Doc 298 flagged this and asked for `TARGET.md §2b` to be tiered; that is done in item 3.
3. **The close is not the expensive window at any tier — and the open is.** The 15:50 figure above
   prompted a full tiered rebuild of the intraday cost curve, since `TARGET.md` §2b carried a
   pooled claim that trading near the close costs ~4× mid-afternoon. **3,471 true-NBBO observations
   across all five tiers × 20 sessions × 7 instants say it does not, anywhere:**

   | tier | 09:45 | 10:30 | 11:30 | 13:00 | 14:30 | 15:30 | **15:50** | close vs 14:30 |
   |---|---|---|---|---|---|---|---|---|
   | index ETFs (9) | 0.939 | 0.679 | 0.674 | 0.570 | 0.578 | 0.559 | **0.862** | 1.49× |
   | mega caps | 2.371 | 1.741 | 1.516 | 1.225 | 1.200 | 0.944 | **0.902** | **0.75×** |
   | large caps | 5.519 | 2.806 | 2.364 | 1.958 | 1.838 | 1.847 | **1.821** | 0.99× |
   | mid-liquid | 7.550 | 7.302 | 7.345 | 7.260 | 7.321 | 7.307 | **7.372** | 1.01× |
   | low-priced | 22.346 | 21.884 | 21.164 | 22.701 | 22.080 | 22.247 | **22.858** | 1.04× |

   > ⚠ **CORRECTED by doc 305.** This table's probe truncated multi-symbol quote requests at 1,000 and never
   > paged. The index-ETF row therefore dropped later-alphabet tickers at busy moments; SPY survived in 3 of
   > 19 sessions, which is the "uneven n" noted below. Re-measured one symbol per request: index-ETF 15:30
   > **0.945** (was 0.559), 15:50 **1.010** (was 0.862), close premium **1.01×** (was 1.49×). Per-ticker
   > medians and the primary-tier all-in cost (0.575 → **0.576**) are unchanged, and so is the LETF verdict.
   > The stock tiers moved by a few percent.

   **No tier shows a 4× close premium; the maximum is 1.49× and mega caps are *cheaper* into the
   close.** Large caps run **5.519 bps at the open against 1.821 at 15:50 — the open is 3.0× the
   close.** Re-pooled, the curve is 1.84 bps at 14:30 vs 1.82 at 15:50, a ratio of **0.99× against
   the claimed 3.96×**. The old figure is most consistent with **uneven per-instant tier coverage**
   in a 613-observation pooled sample — drop a few liquid quotes at one clock time and the pooled
   median jumps a whole tier. `TARGET.md` §2b is corrected and tiered as of this document.

4. **Depth is not the binding constraint.** Median displayed size at the touch is **$6.9M** against
   a $190K account. The "small size" assumption behind using quoted spread as the cost is not an
   assumption here by any stretch.

⚠ **One honest caveat on this table.** Per-ticker `n` is very uneven (SPY 28, DIA 133), so the
pooled 1.064 is not equally weighted across names. The primary-tier equal-weight figure of 0.365
is the one to quote for a SPY/QQQ/IWM/DIA basket; the pooled number is dragged up by the wider
secondary names. Neither reading changes any verdict below, because both are far under threshold.

---

## 2. The kill test, corrected — and why it does not fire

The directive's thresholds were **c > 1.2642** (pessimistic) and **c > 1.8120** (optimistic).
Those came from Draft 302, and Draft 302 got them wrong. This is my error and it is worth stating
precisely, because the shape of it recurs twice more below.

Draft 302 line 146 asked whether the published ceiling was gross or net of cost, and answered:

> *"**no surviving source states it.** Pre-declared conservative default: the 1.2642–1.8120 bps
> ceiling is treated as **GROSS**."*

**A surviving source does state it.** `data/research/doc298/m4_letf_addendum.json` names the field
literally — `ceiling_R2_2pct.net_bps_unit_gross: 1.201` — and
`tier1_letf_identification.md:271` carries a table whose column header is **"net of cost."** The
ceiling is **net**. The arithmetic reproduces exactly:

```
R²=2%:  gross = 1.065 × 15.06 × √0.02 = 2.2682 bps  −  cost 1.068  =  1.2002   (source: 1.201) ✓
R²=3%:  gross = 1.065 × 15.06 × √0.03 = 2.7780 bps  −  cost 1.068  =  1.7100   (source: 1.711) ✓
```

So comparing a freshly measured cost against 1.2642–1.8120 **subtracts cost twice.** Run as
written, the test would have killed the family on a double-count.

**The corrected test compares measured `c` against the GROSS ceiling:**

| traded set | measured all-in `c` | vs 2.2682 (R²=2%) | vs 2.7780 (R²=3%) | verdict |
|---|---|---|---|---|
| primary tier only | **0.575** | 25% of it | 21% | **kill does not fire** |
| all 9 @ 15:50 | **1.072** | 47% | 39% | **kill does not fire** |
| all 9, pooled | **1.274** | 56% | 46% | **kill does not fire** |

Net ceiling after the measured cost, as a share of the 5 bps/day bar at the permitted 50% gross:

| traded set | net @ R²=2% | net @ R²=3% | % of bar |
|---|---|---|---|
| primary tier only | 1.693 bps | 2.203 bps | **16.9% – 22.0%** |
| all 9, pooled | 0.994 bps | 1.504 bps | **9.9% – 15.0%** |

**LETF survives the cost test.** On the primary tier it survives it comfortably. The one detail
the directive got right and Draft 302 did not carry: adding the +0.21 bps regulatory schedule to
the *full nine-name* set pushes the pessimistic end to **9.9%**, fractionally under the ≥10% build
filter. On the four-name primary tier it clears at 16.9%.

None of which matters, because of §3.

---

## 3. Deliverable 2 — the LETF verdict: **VOID**, on arithmetic, decided two months ago

### 3a. What M4 actually adjudicated

`data/research/doc298/M4_ledger_rescore_5bps.md` §2a, dated **2026-07-29**, took LETF apart into
two grounds that the ledger row had welded into one sentence:

* **Ground A (a filter):** ceiling 6.0–8.6% of requirement vs the doc-293 **≥10% build filter**.
* **Ground B (an arithmetic shortfall):** the R² of day-demeaned close-window return variance the
  family must explain to carry the bar at the only permitted deployment.

Its verdict: **"Ground A dies; Ground B survives and is, as the brief suspected, the real
objection."** And then, explicitly:

> **Recommended ledger amendment (verdict unchanged, ground corrected):** replace *"Ceiling
> 6.0–8.6% of requirement vs the ≥10% filter"* with *"needs R² ≥ 47.6% … against published
> estimates of 2–3% — a 16–24× shortfall …"*

**That amendment was never applied.** `docs/ATTEMPTS_LEDGER.md:30` still reads
*"Ceiling 6.0–8.6% of requirement vs the ≥10% filter"* — the ground M4 killed.

Doc 299 then read that row, correctly observed that halving the target doubles the percentage to
12.0–17.2% and clears the ≥10% filter, and filed **"Revived (conditionally)."** The revival is
sound *given the row*. The row was known to be wrong for two months.

Doc 300 caught the smell without finding the cause — it flagged the revival as resting on an
under-determined 2× rescale and marked the "clears the ≥10% filter" claim **conditional**. That
caveat was right, and it was pointing at the wrong thing: the rescale is fine, the *ground* is
obsolete.

### 3b. Ground B, with the denominators matched

M4's prose states the shortfall as *"11.67 required against a ceiling of 3.10–4.42"* and adds that
even at R²=10%, *"the family's ceiling Sharpe is 10.34 against 11.67 required."*

**Those pairs mix two different denominators.** From `m4_letf_addendum.json`: 11.67 is the
requirement on the single-name residual sd (15.06); 3.10–4.42 and 10.34 are ceilings on the
portfolio sd (6.149), whose matching requirement is **25.82**. Matched:

| R² | gross | net | Sharpe (resid sd) | vs req 11.67 | Sharpe (portfolio sd) | vs req 25.82 |
|---|---|---|---|---|---|---|
| **2%** (published) | 2.268 | 1.200 | 1.27 | **9.2× short** | 3.10 | **8.3× short** |
| **3%** (published) | 2.778 | 1.710 | 1.80 | **6.5× short** | 4.41 | **5.8× short** |
| 10% (5× published) | 5.072 | 4.004 | 4.22 | **2.8× short** | 10.34 | **2.5× short** |
| 47.58% (required) | 11.063 | 9.995 | 10.54 | 1.1× | 25.80 | 1.0× ✓ |

The bottom row closes the loop: at R²=47.58% the portfolio-denominator Sharpe lands on 25.80
against a requirement of 25.82, confirming 47.58% is the right number.

**M4's mismatch flatters the family.** Its "10.34 against 11.67" reads as a 1.13× near-miss; the
matched comparison is **2.5–2.8× short**. At the published R² of 2–3% the family is **5.8–9.2×
short**, not the 2.6–3.8× M4's prose implies. *The kill is stronger than the document that issued
it claims.*

And the ceiling assumes a **perfect** predictor of the flow component. There is no plausible R² at
which this family carries the bar at permitted deployment.

### 3c. The verdict

**VOID. Draft 302 is withdrawn. Trial 34 is not consumed. The registry stays at 33 trials and
the operative bar is unchanged at 1.153.**

Closure class: **`REFUTED_BY_ARITHMETIC`** — *not* `REFUTED_BY_COST`. Cost was measured, twice,
and does not kill it. Filing this as a cost refutation would put a false statement about the market
into the archive; the family fails against *our* deployment constraint, which is a fact about us.
Required keystones: `lower_requirement` or `raise_deployment`.

**On the one legitimate test that remains.** M4 noted that clearing the ≥10% filter authorises, under
doc-297 law 2, *"one registered $0 kill test of the single quantity R², and nothing else."* That
test is not worth registering. Its best possible outcome — R² at the top of the published range —
still leaves the family 5.8× short of the bar, and registering it would raise the multiplicity bar
for all 33 other families to buy an answer that cannot change the verdict. That is the planner's
**NEGATIVE-SUM** verdict by definition. Declined.

---

## 4. Deliverable 3 — pipeline: no admissible candidate, and why

The directive named three sources for a candidate with net edge > 10 bps/ticket. All three were
worked. **None yields one**, and in two cases the reason is the §0 pattern again.

### 4a. SS0014 (H-LOCAL) — the near-miss was already dissolved

The directive reads: *"local beat global (Δ=+0.136) and failed by exactly one permutation draw
(p=0.00995 vs α=0.005). Re-evaluate with sufficient permutation trials."*

The permutation count was **B=200**, so p≈0.00995 is ~2/201 and more draws would genuinely sharpen
it. But the sentence immediately after the one quoted, in doc 290:24, is:

> *"**The verification fleet then dissolved the near-miss entirely (artifact=TRUE, HIGH
> confidence): the Δ was measured against a WEAK baseline.** … the identical GBM on
> rank-transformed y scores **0.288–0.307 across seeds — indistinguishable from kNN's 0.31–0.33**.
> The true local-beyond-global lift is **~0.00–0.03**. … H-LOCAL is refuted at the effect-size
> level, not just the gate level."*

Re-running permutations would permute a Δ of ~0.00–0.03, not +0.136. The +0.136 does not exist.
**Re-testing it would re-commit doc 290's own headline lesson — always rank-calibrate the
baseline — against the document that established it.** Not a candidate.

### 4b. SS0006 (catalyst/news) — the margin is real, and it is behind a tombstoned door

> ⚠ **CORRECTED by doc 304 — this section's conclusion is wrong.** The short leg is **not** behind the
> doc-284 tombstone. That tombstone was measured on the **gapper** BUY ledger, and doc 284:18 and doc
> 297:274-275 scope it to that universe. Only **34 of 1,447** stage-B tickers overlap its borrow data,
> and **80.7%** of eligible stage-B names are easy-to-borrow at Alpaca (paper flags, indicative).
> Doc 260 *asserted* a borrow wall and never measured one. This section applied a universe-specific
> result to a different universe: the same summary-without-adjudication error §6 describes, made
> here by me. SS0006 stays `UNDERPOWERED`, not `STRUCTURALLY_UNAVAILABLE`. Its binding problems are
> power, an anchor look-ahead (universe median f10 −1.74% → −0.40% under a knowable anchor), and the
> OOS failure against the reaction baseline. The text below is left as written.

Here the directive's premise holds up: the effect is positive in both samples and the sole failure
mode was sample size. But the cited cell is a **long-short median**, and doc 260:16 decomposes it:

| leg | in-sample f10 | OOS f10 | doc 260's own read |
|---|---|---|---|
| **long** top tranche | **−0.62%** (2025: −2.2%) | **flat** | *"does NOT clear cost — the capturable long leg is dead"* |
| **short** bottom tranche | **−3.44%** | **−3.5%** | *"the skill is here (borrow-constrained on these names)"* |

The entire margin sits in the short leg — ~350 bps/ticket, comfortably over the 15 bps filter — and
that leg is behind the wall doc 284 tombstoned on a **full-census borrow measurement**: net
**−1.205%/ticket, CI [−1.571, −0.822]**, with doc 281's borrow attempts **0-for-94**. The
executable leg is the one measured at −0.62% to flat, before cost.

There is a second, independent problem: the OOS run **failed its own pre-registered
`beats-reaction` gate**. The edge over a cheap numeric baseline is not robust out of sample.

So SS0006 is `STRUCTURALLY_UNAVAILABLE` on the leg that carries the margin, and fails its own gate
on the leg that doesn't. Not a candidate. The fader-detection *replicates* cleanly both times, and
that is worth keeping as a fact about the market — it is just not bankable in this account.

### 4c. The anomaly mine — no margins attached

`python scripts/epistemics.py anomalies` returns **6 unexplained observations** (SS0010, SS0012,
SS0014, SS0019, SS0020, SS0022). **None carries a gross-margin figure**, because anomalies are
recorded as unexplained *observations*, not as sized effects. The instruction to "pull the top
candidate with gross margin > 15 bps" cannot be satisfied from this command, and attaching a margin
to any of them would mean computing one and calling it recovered — the exact move doc 301 banned.

The two with genuine forward content remain SS0012 (attention conserved but decoupled from price)
and SS0014's *surviving* finding (volatility scale predictable at ρ≈0.30, direction not) — both
already classified durable, positive, and **unmonetisable in this account**.

**Deliverable 3 is therefore returned unfilled, with reasons, rather than filled with a candidate
that would not survive its own first gate.** The pipeline is empty. That is the finding.

---

## 5. Deliverable 4 — `risk_aversion_lambda`: settled by arithmetic, not by a re-run

### 5a. The 509-scenario corpus does not exist

Step 4 asked for a re-run of the 509 LLM Arena scenarios. **`data/llm_arena/` contains one empty
`results/` directory.** There is no `index.json` and no `scenarios_*.json`; `data/scenarios/`
holds a different and smaller corpus (`gap_scenarios.json`, **196** records). The 509 scenarios are
not on disk. `run_llm_arena.py seed` can rebuild a corpus via `AutoLabeler`, but what it produces
would be a *new* corpus, not the one that generated the cited figures — so it could not validate
them, only replace them.

### 5b. The decomposition does not need the corpus

Step 4 asked: *"if zero resolution (Recall=1.0, Precision=0.5 at 50% base rate), discard the 0.541
calibration penalty."* **That condition can be checked in closed form from the three numbers
themselves, and it holds.**

At a 50% base rate over N samples, R=1.000 means every actual positive was predicted positive, so
TP = N/2. P=0.500 then forces FP = TP = N/2 — which is *every actual negative*. The classifier
predicted positive on **every single case**.

A constant forecast has **resolution ≡ 0** by definition (resolution is the variance of the
conditional forecast about the base rate, and a forecast that never varies has none). So the
Murphy decomposition of the reported 0.541 is **entirely reliability + uncertainty, with zero
discriminative content.** The penalty weight is calibrating a predictor that distinguishes nothing.

Combined with the provenance problem already annotated in `config/settings.py` — the figures
`CE=0.541 / F1=0.667 / P=0.500 / R=1.000` appear **only in that source comment**, with no artifact
anywhere on disk — the conclusion is: **`risk_aversion_lambda = 0.25` has no evidential basis.**
Step 4.3's own rule says discard it.

### 5c. Why I am not changing it, and what to do instead

**`λ` must not be lowered before the quarantine in Step 4.4 is in place.** `mfcs = weighted_sum −
(λ × risk_score)` at `src/core/scoring.py:207` — lowering λ *raises* scores, admits more candidates,
and **increases deployment**. The book it would deploy into is measured at **−2.041%/ticket**,
negative in 2024, 2025 and 2026 separately. Removing a handbrake that rests on nothing is correct in
isolation and is the wrong first move on a negative edge: it scales the loss.

**The correct order is Step 4.4 first, then λ.** Quarantine live order submission on families whose
measured net per-ticket return is negative; only then is λ a question about candidate ranking rather
than about size on a losing book.

⚠ **Related and unaddressed:** `.env` carries a silently-authorised **40% deployment**. That should
be reconciled against the quarantine before either change lands.

**Step 4.4 is a production change on a live account and is Pierce's to authorise.** This session is
read-only against production; the quarantine is specified, not applied.

---

## 6. What actually went wrong, three times

| # | surface | the summary that was read | the adjudication that followed it |
|---|---|---|---|
| 1 | LETF revival (doc 299) | ledger row: *"Ceiling 6.0–8.6% vs the ≥10% filter"* | M4 §2a, 2026-07-29: that ground dies; **Ground B is the real one** — and M4 asked for the row to be rewritten |
| 2 | SS0014 candidate (this directive) | *"failed by exactly one permutation draw"* | the very next clause: **the fleet dissolved the Δ as a weak-baseline artifact** |
| 3 | cost threshold (Draft 302) | *"ceiling 6.0–8.6% of 21.07"* | `m4_letf_addendum.json` field name: **`net_bps_unit_gross`** — it was already net |

All three are the same failure, and it is not a reasoning failure. Each conclusion follows validly
from what was read. It is a **retrieval** failure: the corpus is 303 documents deep, its ledger is a
one-line-per-family summary, and **the summary and the adjudication live in different files with no
link between them.**

Doc 288's lesson was "a clean audit of the wrong property." This one is narrower and more
mechanical: **a ledger row is a pointer, not a verdict, and a recommended amendment that is never
applied is a landmine with a two-month fuse.**

### Method rules this adds

1. **A recommended ledger amendment is a work item, not a note.** M4's amendment sat unapplied for
   two months and directly caused a revival. Any document that recommends a ledger change must
   either make it or file it. *(Applied below.)*
2. **Before reviving any family, read the most recent document that adjudicated it — not the ledger
   row.** The row states the *original* ground, which is precisely the thing a re-score changes.
3. **When a quantity's gross/net status is unclear, grep the artifact field names before declaring a
   conservative default.** `net_bps_unit_gross` answered in one grep what Draft 302 declared
   unknowable.
4. **Check denominators match before quoting a ratio of two Sharpes.** M4's own kill was understated
   by ~2× for this reason.

---

## 7. Applied this session

* **Ledger row amended** to M4's recommended text, 2 months late, with the matched-denominator
  correction from §3b folded in.
* **Draft 302 voided.** `302_letf_prereg_DRAFT.md` retained with a VOID header — it is the record of
  a correctly-executed design against an obsolete ground, and the §2 gross/net error is instructive.
* **Trial 34 not registered.** Registry: **33 trials.** Operative bar unchanged at **1.153**.
* **`letf_kill_1550.json`** — 576 fresh NBBO observations, the program's second independent
  close-window cost measurement.

## 8. Open

1. ~~`TARGET.md §2b` still says 6.42 bps at 15:50.~~ **DONE this session.** Rebuilt tiered from
   3,471 observations (§1.3); the ~4× close premium is retracted as wrong in both magnitude and
   direction. **Scope correction to my own claim above:** I wrote that the stale figure "may be
   silently killing other close-window designs." A grep of the corpus shows **only Draft 302 ever
   cited it** as a blocking consideration — and that draft is now void. So the exposure was
   **prospective, not realised**: no surviving family was filtered on it. The fix still matters,
   because the filter was live and the next close-window design would have hit it.
2. **Step 4.4 quarantine** — specified, not applied. Requires Pierce.
3. **`.env` 40% deployment** — reconcile against the quarantine.
4. The doc-250 bootstrap and the `n_obs` gap on eleven records (doc 301 §5) remain open and
   untouched by this session.
5. **The pipeline is empty.** No candidate cleared 10 bps/ticket net. The honest read of §4 is that
   the program's three nominated sources of a next candidate are exhausted, and finding one is now
   the binding problem — not testing one.
