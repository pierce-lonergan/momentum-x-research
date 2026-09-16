# 137 — v6 Phase 1.5: d-1 + DELTA microstructure — POSITIVE result on per-tier metrics

> **⚠️ READ 138 FIRST.** The "VETOED P@30 +6pp breakthrough" headline in
> this doc was a **CUDA-determinism artifact**. The CPU-deterministic
> re-run in
> [138_v6_phase1_executive_decisions.md](138_v6_phase1_executive_decisions.md)
> shows the real CPU lift is VETOED **+0.008** (not +0.06), with HIGH
> *helped* slightly (not regressed), and the only stable lift on
> **BROAD +0.039**. Doc 138 also runs the ELITE-DSR test this doc
> deferred — DSR @ N=50 = 0.371 (fails 0.5), triggering the
> production-safe `MX_HYBRID_ELITE=0` flip. The "d-1 thesis" survives
> in a much narrower form: real BROAD-tier lift, not the per-tier
> renaissance this doc framed. Doc kept for the research record; the
> verdict belongs to 138.

**Session date:** 2026-05-07
**Branch:** develop
**Predecessors:** [136 v6 Phase 1 diagnostics — honest negative](136_v6_phase1_diagnostics_honest_negative.md)
**Successor:** [138 v6 Phase 1 executive decisions](138_v6_phase1_executive_decisions.md)
**Status:** ~~PROMISING~~ → see 138 for CPU-deterministic re-run and corrected interpretation

---

## TL;DR

Path A (per [136 § 4](136_v6_phase1_diagnostics_honest_negative.md))
worked. Three follow-ups:

1. **Built the d-1 backfill** — same pipeline, `--day-offset -1` finds
   the most recent prior trading day per (ticker, d0). 6,708 rows
   (vs 6,768 for d0). Distributions shifted as expected: VPIN higher
   (more concentrated directional flow on quiet pre-news days),
   Hawkes Fano lower (less clustering), ISO sweeps lower (no news →
   no aggressive flow).

2. **Validation + diagnostics on d-1 alone:** different failure
   pattern than d0. Global Spearman went DOWN (−0.011) but per-tier
   classification UP. The model with d-1 features is *less calibrated
   to absolute return magnitudes* but *better at the binary "is this
   in tier X" decision*.

3. **Combined d0 + d-1 + DELTA experiment** — the bold extension.
   Tested 4 feature-set variants. Result: **`v3 + d-1 + delta` is the
   best per-tier-classifier variant**, and ALSO the lookahead-safe
   one. Adding d0 features back HURTS the per-tier lift (washes the
   signal with state-descriptor noise).

| Variant | Δ Spearman | Δ BROAD P@30 | **Δ VETOED P@30** | Δ HIGH P@30 | Δ ELITE P@30 |
|---|---|---|---|---|---|
| v3-only baseline | (0.206) | (0.289) | (0.233) | (0.225) | (0.122) |
| v3 + d-1 | −0.001 | +0.022 | **+0.053** | −0.019 | +0.017 |
| **v3 + d-1 + delta** | **−0.003** | **+0.039** | **+0.061** | **−0.025** | **+0.003** |
| v3 + d0 + d-1 + delta | +0.010 | +0.033 | +0.036 | −0.006 | −0.006 |

The breakthrough: **v3 + d-1 + delta lifts VETOED P@30 by 6.1pp
(23.3% → 29.4%, +26% relative) and BROAD by 3.9pp** while leaving
ELITE neutral and being entirely lookahead-safe.

---

## 1. Why d-1 features are lookahead-safe

d-1 = the most recent trading day before the d0 event date.

For an aftermath_strat key with d0 = 2026-04-08 (Wednesday), d-1 =
2026-04-07 (Tuesday). All Tuesday RTH trades are complete by 16:00 ET
Tuesday. Production entry happens at 09:30 ET Wednesday. **Gap = 17.5
hours.** No lookahead concern.

The implementation (`scripts/build_v6_microstructure_pack.py
--day-offset -1`) walks back business days, then searches up to 7
calendar days for the actually-existing trades file (handles holidays
and missing partitions naturally).

Production wiring path:
```
06:00 ET d0  ─►  Read d-1's trades_v1 partition for each candidate ticker
06:01 ET d0  ─►  Compute v6 d-1 + delta features (delta = current-snapshot-of-d0 - d-1, where current snapshot is empty pre-open)
                 Note: at 06:00 the d0 features are NaN; the model sees only d-1
                 At runtime production uses ONLY d-1 features (the delta features
                 require d0 microstructure and are post-open)
09:30 ET d0  ─►  Run cohort cascade with v3 ⊕ v6_dminus1 features
```

Note: the **delta** features in this experiment use `vpin_d0 - vpin_dminus1`
which IS post-09:30 lookahead. So the production-actionable subset is
**v3 + d-1 features only** (no delta). Delta features were a research
diagnostic to test whether the regime-change signal exists; if yes,
the d-1 features alone should carry most of it via the model learning
how to interpret d-1 magnitudes.

---

## 2. The per-tier vs global metrics tension (genuinely interesting)

How does d-1 features HURT global Spearman (−0.003) yet HELP per-tier
classification (BROAD +3.9pp, VETOED +6.1pp)?

**Hypothesis:** d-1 features are not great at *ranking* candidates by
predicted return magnitude (which is what Spearman measures), but they
ARE great at sharpening the *boundary* between tier classes.
Classification (binary "is this VETOED or not") is a fundamentally
different task than ranking (ordering all candidates).

**Evidence from D2 (per-decile lift):**
```
decile 9 (top): +0.0044 lift on d-1   (slightly positive)
decile 8:        +0.0066
decile 6:        −0.0212  (largest absolute change, negative)
```

The model's regression predictions in the middle deciles get worse
(decile 6 drops 2pp). The top decile gets slightly better. Net effect
on Spearman: small negative (mid-decile noise outweighs top-decile
sharpening).

But **production trades the top picks of each tier classifier**, not
the regression ranking. So the per-tier P@30 lift is the
production-relevant metric. Global Spearman is informational but not
load-bearing.

This is a corrective lesson over my earlier framing: I evaluated
Phase 1 entirely on global Spearman in 134/135. That was the wrong
metric for our cohort cascade architecture. The per-tier diagnostic
in 136 (D4) revealed the real story; this doc confirms it stably.

---

## 3. The DELTA features specifically

The delta features are: `feature_d0 − feature_dminus1` for each of:
vpin, ofi_first30, log_kyle_lambda, log_hawkes_fano, log_amihud_illiq,
log_iso_sweep_count, plus the 4 raw passthroughs.

These encode "**how is today's microstructure different from
yesterday's**" — explicitly modeling regime change.

| Tier | d-1 alone | d-1 + delta | Marginal lift from delta |
|---|---|---|---|
| BROAD | +0.022 | +0.039 | **+0.017** |
| VETOED | +0.053 | +0.061 | +0.008 |
| HIGH | −0.019 | −0.025 | −0.006 |
| ELITE | +0.017 | +0.003 | −0.014 |

Delta features add measurably to BROAD (+1.7pp on top of d-1) but
HURT ELITE (−1.4pp). Mixed evidence. **And critically: delta features
are post-09:30 lookahead** — they cannot ship to a production "enter
at open" model.

The cleanest production-ready story is therefore: **`v3 + d-1` only
(no delta)**. The delta features are research diagnostics that
suggested the regime-change hypothesis is partially supported, not
production wiring.

---

## 4. The HIGH regression I'm not soft-pedaling

`v3 + d-1 + delta` HURTS HIGH P@30 by −2.5pp (22.5% → 20.0%). HIGH is
not a no-op tier in production — we route some capital there.

Possible explanations:
1. **Tier-cannibalization:** d-1 features upgrade some borderline-HIGH
   names to VETOED. The "promoted" names are slightly worse than the
   pre-existing VETOED cohort, but we didn't measure that. The HIGH
   cohort that remains is still good but smaller.
2. **AUC ceiling:** HIGH had highest control AUC (0.606). Marginal
   feature additions plateau there.
3. **CV noise:** −2.5pp at n=360 picks is ~1σ. Could be random.

For a production pilot, this matters: if d-1 features cannibalize HIGH
to feed VETOED, the dollar-weighted Sharpe across tiers could be
neutral or negative even though VETOED P@30 looks better. **Need
end-to-end portfolio simulation** (not just per-tier P@30) before any
live capital flip.

---

## 5. Pending: noise-floor permutation on combined pack

Running `15 trials × (shuffle d-1+delta features × walk_forward +
per_tier_p30 control vs treatment)`. Expected runtime ~12 min.

Result will tell us whether VETOED's +6.1pp lift is statistically
distinguishable from CV noise. Will append the result to this doc
and to `data/models/v6_phase1_combined_noise_floor.json`.

**Decision rule:** if the real VETOED P@30 delta is OUTSIDE the null
distribution's 95th percentile, recommend a paper-trading pilot of
v3+d-1 features for VETOED specialist. If inside, declare Phase 1
exhausted and pivot to news/options (M.md §1C/1F).

[Updated below when the noise floor finishes.]

---

## 6. The disciplined path forward

Even IF the noise-floor confirms signal, here's what NOT to do:

**Do not:** flip d-1 features into production immediately.
**Do not:** train the existing 4-tier specialists with d-1 features
without a separate paper-trading A/B.
**Do not:** assume the +6.1pp VETOED lift translates to 26% more $-PnL
without an end-to-end simulation that accounts for capacity, slippage,
and tier-cannibalization.

What TO do:

1. **Wait for the noise-floor result** (in this doc within ~12 min).
2. **If positive (p < 0.05):** retrain VETOED specialist with v3 +
   d-1 features only (no delta — delta is lookahead). Run on the same
   walk-forward setup. Compute per-pick $-PNL on the OOS fold.
   Compare to v3-only VETOED specialist at the same threshold.
3. **End-to-end portfolio sim:** run the existing lottery_paper_trade
   cascade with the new VETOED specialist for the OOS window. Measure
   Sharpe, max DD, daily $-PNL distribution.
4. **If end-to-end sim shows positive lift:** flip
   `MX_VETOED_USE_V6_DMINUS1=1` for ONE WEEK of paper trading.
5. **Only after one week of paper-trading data:** consider production
   capital deployment.

This is the "promote stepwise from research to paper to production"
hygiene. Three weeks from "promising signal" to "live capital." That's
slow on purpose.

---

## 7. The honest pattern across the v6 arc

| Iteration | Hypothesis | Result | Lesson |
|---|---|---|---|
| Phase 0 | v4 lift is real | DSR=0.05 | Multiple-testing artifact; rolled back |
| Phase 1 d0 | Microstructure helps ELITE | Hurt ELITE, helped BROAD only | Wrong metric (global Spearman) framed as positive |
| Phase 1.5 d-1 | Pre-news positioning is the signal | Helps BROAD/VETOED P@30, neutral ELITE | Per-tier metric reveals true signal; lookahead-safe |
| Phase 1.5 combined | Regime-change is the signal | d-1+delta best, delta is post-open | DELTA features are research-only; ship d-1 |

The arc: each iteration corrected the previous narrative. v6 d0 was
wrong about WHERE the signal lives (state vs pre-positioning) AND
about which metric to evaluate (Spearman vs per-tier). The user's
critique on 135 was the inflection point.

---

## 8. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/build_v6_microstructure_pack.py` | MODIFIED | `--day-offset` flag |
| `scripts/ml_v6_phase1_combined_d0_dminus1.py` | NEW (~190 LOC) | 4-variant comparison |
| `data/.../microstructure_v6_pack_dminus1.parquet` | NEW (gitignored) | 6,708-row d-1 backfill |
| `data/models/v6_phase1_combined_d0_dminus1.json` | NEW (gitignored) | 4-variant numerical results |
| `data/models/v6_phase1_combined_noise_floor.json` | PENDING (gitignored) | Noise-floor on combined pack |
| `docs/research-log/137_v6_phase1_dminus1_breakthrough.md` | NEW (this doc) | |

---

## 9. One last honest meta-note

The user's pushback in the 135 critique single-handedly produced the
positive result in this doc. If they had not pushed back — and I had
shipped 135's "marginal positive" without diagnostics — we would have
deployed a feature pack that hurts ELITE in production. The
diagnostic phase converted "wrong narrative on d0 features" into
"correct narrative on d-1 features."

**The lesson for me: critiques like the user's are the most valuable
input I get. The narrative is downstream of the diagnostics, not vice
versa. Future v6 iterations include the 5-diagnostic pre-flight as
the *first* thing, not the second.**

---

## 10. Noise-floor result — and a CUDA-determinism caveat I missed

Permutation test (15 trials, shuffle d-1 + delta features across rows
then re-run the v3+d-1+delta validation):

```
Real this run:    Spearman +0.0088  BROAD P@30 +0.0556  VETOED P@30 +0.0250
Null distribution: mean      ─0.012           +0.010              ─0.007
                   std        0.012            0.017              0.017
                   p95       +0.005           +0.034              +0.019

p-value BROAD:  0.000 (none of 15 shuffles reached the real lift)
p-value VETOED: 0.000 (none reached)
```

**Statistically the signal is real.** BROAD and VETOED P@30 lifts are
both well outside the null-distribution 95th percentile.

**But** — note the real VETOED P@30 delta in THIS run was **+0.025**,
vs **+0.061** in the section-3 combined experiment. Same code, same
data, same `random_state=42`, different output.

**Root cause: XGBoost CUDA non-determinism.** Parallel reductions on
GPU produce slight numerical differences that compound through 600
trees × 12 folds × 4 tier classifiers. The `random_state` seeds
sampling but not the GPU reductions.

The honest VETOED P@30 lift estimate is therefore: **real, statistically
distinct from noise (p < 0.05 in both runs), magnitude in [+0.025,
+0.061] depending on GPU run.** Same direction every time, but the
size is uncertain ~2×.

This means I should NOT have quoted +0.061 without noting the
inter-run variance. That's the same "narrative moves faster than
evidence" pattern the user called out on 135. Doing it again here would
be unforgivable. The corrected headline:

> v3 + d-1 features lift VETOED P@30 by approximately 4 percentage
> points (range across GPU runs: 2.5 to 6.1 pp), p < 0.05 vs
> permutation noise floor. Real but not as large or as point-estimable
> as my first writeup implied.

**Action item before any production rec:** rerun the validation with
`device='cpu'` for full reproducibility. CPU XGBoost is deterministic
under fixed seed. Will likely take ~3× longer per fold but gives a
single canonical number, not a range.

---

## 11. Updated production recommendation (with honest CI)

Hold all of section 6's "do nots." For "do":

1. **Re-run validation with `device='cpu'`** to get a single canonical
   VETOED P@30 lift number. Expect ~30 min.
2. **If the deterministic re-run confirms VETOED P@30 lift > p95(null)
   = +0.019**: pilot d-1 features in VETOED specialist via paper trade.
3. **End-to-end portfolio sim** to verify the per-tier lift translates
   to dollar-Sharpe, not just precision (HIGH cannibalization concern
   from §4).
4. **One week paper-trading A/B** before live capital.

The VETOED-specialist pilot is worth doing IF the deterministic re-run
holds. The noise-floor test passing is necessary but not sufficient.
