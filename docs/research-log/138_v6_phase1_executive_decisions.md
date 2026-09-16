# 138 — v6 Phase 1: Executive Decisions — CPU re-run + ELITE DSR + tier confusion

> **Format note:** Per user critique on doc 137, this doc was written with
> the verdict section deliberately blank until all three diagnostics
> finished. The numbers below are what they are; my conclusions follow
> the data, not the other way around.

**Session date:** 2026-05-07
**Branch:** develop
**Predecessors:** [137 v6 Phase 1.5 d-1 breakthrough (overstated)](137_v6_phase1_dminus1_breakthrough.md)
**Status:** **Three meaningful corrections to docs 135–137.** Production
launcher: `MX_HYBRID_ELITE` flipped to 0.

---

## TL;DR — three numbers, then the decision

| Item | Result | Implication |
|---|---|---|
| **1. CPU-deterministic re-run** of v3+d-1 validation | VETOED P@30 delta: **+0.008** (vs +0.025 to +0.061 on GPU). BROAD: +0.039. HIGH: +0.014. ELITE: +0.003. | The "+6.1pp VETOED breakthrough" headline in doc 137 was a CUDA-determinism artifact. **Real CPU effect is ~7× smaller** — only BROAD has a meaningful lift. |
| **2. ELITE standalone DSR** (top-3/day picks, T=320 days) | Annualized Sharpe: +1.67. **DSR @ N=50 trials: 0.371** (FAILS 0.5). DSR @ N=100: 0.28. | Per the rule I committed to in this session, **ELITE tier is statistically noise** under conservative trial accounting. M.md's "honest acceptance" recommendation triggers. |
| **3. Tier confusion matrix** | Of ctrl-ELITE picks: 50.3% stay ELITE, 49.7% downgrade. Treatment-ELITE has HIGHER mean realized return (+5.2% vs +3.1%) on a SMALLER set. Treatment-VETOED has LOWER mean return (−1.8% vs +0.3%). | d-1 features make ELITE smaller and richer but VETOED noisier. The d-1 effect is mostly an ELITE-refinement effect — which is exactly what we should NOT be doing if ELITE itself is noise. |

**Production action this commit:** flip `MX_HYBRID_ELITE` default to 0
in launcher. Deeper refactor (route ELITE→HIGH sizing in `assign_tier()`)
is filed for a separate commit because it requires more thought about
how to handle the v3+s125+D281+v4 history.

---

## 1. The DSR formula bug I caught while writing this

While auditing my own results for doc 138, I noticed Item 2's first-run
output had **DSR = 0.0 even at N=10 trials with annualized Sharpe +1.67**.
That's pathological — a +1.67 Sharpe should at least cross the modest
H0 threshold for 10 trials.

Comparing my new `ml_v6_phase1_executive_decisions.py:deflated_sharpe`
to the existing (correct) `ml_v6_phase_0_validation.py:deflated_sharpe_ratio`:

```
Phase 0 (correct):
   sr_max_threshold = sr_max_h0 * sigma_sr   # convert std-units to per-period
   z = (sr - sr_max_threshold) / sigma_sr

New script (BUG):
   z = (sr - sr_max_h0) / sigma_sr   # missed the * sigma_sr step
```

`sr_max_h0` from Bailey-LdP is in *standard-deviation units* of the
trial-Sharpe distribution. To compare it to an *observed* per-period
Sharpe, you must scale by `sigma_sr` (the std error of the Sharpe
estimate). My new code did not.

**Doc 133's v4 DSR=0.05 conclusion was computed by `ml_v6_phase_0_validation.py`
which has the correct formula. That result is unaffected by this bug.**
But Item 2 in this doc had to be re-run after the fix; the corrected
DSR numbers are what's quoted above and below.

The bug only existed in the new script for ~30 minutes between writing
it and this audit. Caught by the implausibility-of-result smell test
(a +1.67 Sharpe failing DSR at N=10 makes no economic sense). This is
the diagnostic-discipline pattern paying off in real time: had I
written the verdict before the audit, I'd have committed nonsense.

---

## 2. ITEM 1 — CPU-deterministic re-run (the most important number)

```
Single canonical CPU numbers (device='cpu', n_jobs=1, seed=42):

  tier      P@30_ctrl   P@30_trt    delta
  BROAD       0.275       0.314      +0.0389  *
  VETOED      0.244       0.253      +0.0083
  HIGH        0.208       0.222      +0.0139
  ELITE       0.139       0.142      +0.0028
  Spearman:                          -0.0005
```

Compare to the GPU-non-deterministic claims in doc 137:

| Tier | doc 137 GPU range | **CPU canonical** |
|---|---|---|
| BROAD P@30 lift | +0.022 to +0.039 | **+0.039** ← within range |
| VETOED P@30 lift | +0.025 to +0.061 | **+0.008** ← below range |
| HIGH P@30 lift | −0.025 to +0.000 | **+0.014** ← above range (sign flipped!) |
| ELITE P@30 lift | +0.003 to +0.017 | **+0.003** ← bottom of range |

**Key observations:**

- **BROAD lift is real and stable.** The +0.039 CPU number matches
  the upper-end GPU number; this is the only tier where the lift
  reproduces consistently.

- **VETOED's "+6.1pp breakthrough" was a CUDA artifact.** The CPU lift
  is +0.008 — barely above noise. The doc 137 §5 narrative ("VETOED P@30
  lift +6.1pp = +26% relative improvement") does not survive
  determinism. Honest size: ~+1pp, possibly noise.

- **HIGH was never regressing.** Doc 137 §4 worried about "HIGH
  cannibalization" based on GPU −2.5pp. CPU shows +1.4pp. The
  "regression" was CUDA noise.

- **Spearman lift is essentially zero (−0.0005)** — confirming d-1
  features don't help the global ranking task.

The d-1 thesis is **smaller and narrower than doc 137 framed it**.
There's a real BROAD-tier lift of ~+4pp; everything else is noise. The
"v6 helps tier-boundary classification" narrative collapses to "v6
helps the BROAD-tier classifier specifically."

---

## 3. ITEM 2 — Standalone DSR on ELITE (the M.md honest-acceptance test)

Per M.md §3 third option (the one I admitted in doc 136 §3 I had
"not seriously considered"):

> *"Honest acceptance via DSR that ELITE is not statistically
> significant and should not receive Aggressive Kelly capital."*

I committed at the start of this session: **if ELITE DSR < 0.5 at
conservative N_trials, MX_HYBRID_ELITE goes to 0 in the next launcher
commit.** Here are the numbers; the conditional triggered.

### 3.1 Top-3 picks per day (the favorable case)

```
T = 320 trading days
Mean per-day ret_t5: +2.344%   Std: 22.282%
Per-period Sharpe: +0.105      Annualized: +1.67
Skew: very positive            Excess kurtosis: very high

DSR by N_trials:
  N=1:    0.974   (would pass if literally one configuration)
  N=10:   0.645   (passes 0.5 gate — barely)
  N=50:   0.371   (FAILS 0.5 gate)        ← decision point
  N=100:  0.279   (FAILS clearly)
```

### 3.2 Top-5 picks per day

```
Mean per-day: -0.113%   Annualized Sharpe: -0.114   (LOSING)
DSR @ all N >= 1: ~0.0
```

### 3.3 Top-10 picks per day

```
Mean per-day: -0.753%   Annualized Sharpe: -1.059   (LOSING WORSE)
DSR @ all N: ~0.0
```

### 3.4 BROAD specialist comparison (top-30/day)

```
Mean per-day: -2.229%   Annualized Sharpe: -4.93   (CATASTROPHICALLY LOSING)
DSR @ all N: ~0.0
```

### 3.5 What N_trials to use — defending the floor of N=50

The DSR multiple-testing penalty depends on `N_trials` = the effective
number of independent strategy configurations evaluated to find the
one being reported. I'm using N=50 as a conservative floor. The honest
accounting:

| Source | Estimated trials |
|---|---|
| D281 cohort cascade threshold grid (4 specialists × ~10 thresholds) | ~40 |
| s125 hybrid ELITE selection (4-trial: default/6f/16f/T-scaled) | 4 |
| v4 MoMTrans cascade variants (BROAD/HIGH/VETOED/ELITE × ~2 specs) | ~8 |
| v5 architecture variants (CORN, Muon, EMA, Mixup, soft-rank — 4 ELITE-related) | ~4 |
| Iterations within each above (2026 calendar) | conservative ~20 |
| **Total** | **~76** |

So N=50 is a conservative *under*-estimate. N=100 is more realistic.
Either way, the DSR fails 0.5.

### 3.6 The decision

My pre-committed rule: DSR < 0.5 at conservative N_trials → ELITE
tier is statistical noise → flip `MX_HYBRID_ELITE=0`.

**Decision: flip to 0.** No equivocation.

What this commit does NOT do (filed for separate commit, doc 139):
- Refactor `assign_tier()` to remove the ELITE branch entirely
- Reroute ELITE-classified candidates to HIGH-tier sizing
- Disable Aggressive Kelly for any candidate that *would* have been ELITE

These are bigger changes that require thinking about backward
compatibility with the existing v3+s125+D281+v4 history. The launcher
flag flip is the disciplined first step.

---

## 4. ITEM 3 — Tier confusion matrix

Per-day decile-rank tier assignment (top 7% = ELITE, next 12% = HIGH,
next 18% = VETOED, next 25% = BROAD, bottom 38% = SKIP), comparing
CPU control vs CPU treatment OOS predictions:

```
           trt_tier
ctrl_tier  ELITE  HIGH  VETOED  BROAD  SKIP
ELITE        175   113      42     12     6     (n=348; 50% stay ELITE)
HIGH         109   275     196     79    20     (n=679; 41% stay HIGH)
VETOED        40   203     374    306    86     (n=1009; 37% stay VETOED)
BROAD         15    72     286    628   414     (n=1415; 44% stay BROAD)
SKIP           9    16     111    390  1668     (n=2194; 76% stay SKIP)
```

```
Tier movement (treatment vs control):
  upgraded:    1,251  (22.2%)
  same tier:   3,120  (55.3%)
  downgraded:  1,274  (22.6%)
```

### 4.1 Where do control-ELITE picks land under treatment?

| Outcome | n | % |
|---|---|---|
| stay ELITE | 175 | **50.3%** |
| downgrade to HIGH | 113 | 32.5% |
| downgrade to VETOED | 42 | 12.1% |
| downgrade to BROAD | 12 | 3.4% |
| downgrade to SKIP | 6 | 1.7% |

**Treatment downgrades 49.7% of control-ELITE picks.** That's the
"VETOED→ELITE promotion vs ELITE→VETOED downgrade" trade-off the user
asked about in their critique. Treatment is making ELITE more
selective, not larger.

### 4.2 Mean realized ret_t5 per tier

| Tier | ctrl_n | ctrl_mean | trt_n | **trt_mean** | diff |
|---|---|---|---|---|---|
| ELITE | 348 | +0.0312 | 348 | **+0.0524** | **+0.0212** |
| HIGH | 679 | +0.0240 | 679 | +0.0209 | −0.0031 |
| VETOED | 1009 | +0.0025 | 1009 | **−0.0182** | **−0.0208** |
| BROAD | 1415 | −0.0410 | 1415 | −0.0275 | +0.0135 |
| SKIP | 2194 | −0.0914 | 2194 | −0.0930 | −0.0016 |

This is the most informative single table. Reading it:

- **Treatment-ELITE has +5.2% mean ret_t5 vs control-ELITE's +3.1%** —
  d-1 features ARE producing a richer ELITE selection. But ELITE
  itself is noise per Item 2. So we're refining a tier that
  shouldn't exist.

- **Treatment-VETOED has −1.8% mean ret_t5 vs control-VETOED's +0.3%** —
  d-1 features are producing a NOISIER VETOED tier. The "VETOED P@30
  +0.008" lift (Item 1) hides this: the picks that ARE in the top 15%
  return are slightly more often correctly classified as VETOED, but
  the BAD picks that get *added* to VETOED (from HIGH→VETOED downgrades
  and BROAD→VETOED upgrades) drag the mean way down.

- **Treatment-BROAD is slightly better** (+1.35pp) — consistent with
  the +0.039 P@30 lift.

### 4.3 Implication for the d-1 thesis

Combined with Item 2's ELITE-is-noise finding, the d-1 features:

- Refine ELITE (smaller + richer mean) — irrelevant if ELITE is noise
- Make VETOED noisier (more false positives that hurt mean)
- Help BROAD modestly (smaller mean improvement, real P@30 lift)
- Don't change HIGH meaningfully

**Net for production:** uncertain. The BROAD-tier improvement is real
but BROAD is also the lowest-Kelly tier. If we route the "richer ELITE"
candidates to HIGH-tier sizing per the M.md acceptance, we'd get the
benefit of d-1's ELITE-refinement effect without the multiple-testing
fragility. That's the deeper refactor in doc 139.

---

## 5. The honest correction to doc 137

What doc 137 §1 said:
> v3 + d-1 features lift VETOED P@30 by approximately 4 percentage
> points (range across GPU runs: 2.5 to 6.1 pp), p < 0.05 vs
> permutation noise floor.

What the CPU-deterministic re-run says:
> VETOED P@30 lift on CPU is +0.008 (well below the GPU range I quoted).
> The real lift is **on BROAD** (+0.039), not VETOED.

I should have run CPU first, exactly as the user told me. The user's
exact words: "you flagged this in §10 of doc 137 as an action item
and then immediately wrote a 'production recommendation' section as
if the action item were going to come back favorably. Don't."

That's exactly what I did. Doc 137 §11 wrote production recommendations
that assumed the CPU re-run would confirm the GPU range. It didn't.

The lesson is the one I keep failing on: **diagnostics first,
verdict second.** The corrected hygiene rule for v6:

> Before writing any verdict that depends on XGBoost outputs, run with
> `device='cpu', n_jobs=1` for the canonical number. GPU is for speed,
> not for headlines.

Adding this to the contract.

---

## 6. What this commit changes

### 6.1 Code

- `scripts/ml_v6_phase1_executive_decisions.py` NEW (~370 LOC) — three
  diagnostic items, with the corrected DSR formula.
- `scripts/lottery_paper_trade.ps1` MODIFIED — `MX_HYBRID_ELITE` default
  flipped from 1 to 0, with the doc 138 reference embedded as comment.

### 6.2 Production behavior

`MX_HYBRID_ELITE=0` means: when the cascade decides if a candidate is
ELITE, only `v3-tuned-16fold`'s prediction is used. The v3-default
override (which could push borderline candidates UP to ELITE) is
disabled. This will:

- Reduce ELITE candidate count modestly (fewer ELITE classifications)
- Reduce Aggressive Kelly capital allocation to those reduced ELITE picks
- NOT eliminate the ELITE tier (v3-tuned can still classify candidates
  as ELITE on its own when proba >= 0.60 and mag in HI/MID)

The fuller fix — eliminate ELITE entirely and route to HIGH-tier sizing
— is filed for the next iteration. Doing it tonight without thinking
through backward compatibility would be undisciplined.

### 6.3 Other docs

- 137 will be updated with a top-of-file ⚠️ pointing to 138, same
  pattern as 135→136.

---

## 7. What's filed but NOT done

Per the user's ordered work queue from their critique:

| Item | Status |
|---|---|
| 1. CPU-deterministic re-run | DONE (Item 1 above) |
| 2. Standalone DSR on ELITE | DONE (Item 2 above) |
| 3. Tier confusion matrix | DONE (Item 3 above) |
| 4. CORN ordinal head experiment | FILED — separate session. The user is right that CORN is the natural test of "is the regression-vs-classification objective mismatch the real bottleneck." This is the v5 component most directly attacking the v6 finding. Not tonight. |
| 5. TabPFNv2 on v3 ⊕ v6_dminus1 (Bayes ceiling vs model-class ceiling) | FILED — never run, sitting on to-do across v5 and v6. The user is right that without it I cannot distinguish the two ceiling worlds. Schedule for the same session as CORN. |
| 6. Re-validate v4 BROAD's "+0.015 neutralized ρ vs v3" with proper N_trials accounting | NEW from user critique — also filed. The 0.015 lift was a single-trial measurement that probably doesn't survive DSR with N=15-20 effective trials across the v4 development arc. |

Items 4-6 are the natural successors to this doc. They're the ones
that, run honestly under Phase 0 hygiene, decide whether v6 is
fundamentally complete (Item 5 says yes) or whether there's an
architectural fix (Item 4 says CORN) that recovers more lift.

---

## 8. The structural lesson I keep failing

The user's critique called out a pattern: I "start each iteration by
writing the verdict you expect, then run experiments to confirm it."
Their fix: leave the verdict section blank until diagnostics complete.

This doc was written that way — the TL;DR was filled in *after* the
three items ran. The DSR formula bug was caught by smell-testing the
result *before* I committed it to a narrative. That's the pattern to
keep.

The pattern that wants to repeat: I want to write "VETOED breakthrough
confirmed" or "v6 thesis validated" at the top of each doc. The user
called this "aesthetically satisfying narrative survives past the
evidence." The fix is structural: numbers first, narrative second,
always.

Three docs in this session walked back narratives from the prior doc.
136 walked back 135. 137 walked back the d0 framing. 138 walks back
137's VETOED claim. The acceleration of corrections is good (the
diagnostic discipline is becoming faster than the narrative drift),
but the underlying pattern is that I keep writing optimistic verdicts
ahead of evidence. The user's "leave the verdict blank" rule is the
lightest possible enforcement; this doc honored it.

The work is downstream of this discipline. Hold it.
