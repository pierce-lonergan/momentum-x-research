# 136 — v6 Phase 1 DIAGNOSTICS: honest negative result + redesign plan

**Session date:** 2026-05-07
**Branch:** develop
**Predecessors:** [134 v6 Phase 1 microstructure pack](134_v6_phase1_microstructure_pack.md), [135 v6 Phase 1 marginal validation](135_v6_phase1_validation_marginal.md)
**Status:** v6 pack as currently built is **NOT shippable**. Doc 135's
positive framing was wrong. New direction below.

---

## TL;DR

User pushback on doc 135 was correct on every substantive point. I ran
the diagnostic phase they recommended (plus a lookahead audit they
implied). All five diagnostics return findings that **invalidate** the
"marginal positive" verdict in 135.

| Diagnostic | Finding | Implication |
|---|---|---|
| **D5 lookahead** | Every v6 feature uses post-09:30 ET trades on d0 | Cannot feed an "enter at open" decision (lookahead). Production-wiring path is BLOCKED unless we move to d-1 features. |
| **D2 per-decile lift** | Largest absolute lift in **decile 5** (+0.030); decile 9 (top picks we trade) lift is −0.0006 | M.md "top-tail / ELITE thesis" is REJECTED. v6 acts on the middle of the distribution. |
| **D3 liquidity ablation** | OFI alone contributes only +0.0043 of the +0.022 total lift; the binary `has_first_30_trades` adds nothing on top of the rest of the pack | OFI is mostly redundant. The real lift comes from VPIN/Kyle/Hawkes/Amihud/ISO. User's specific OFI-as-liquidity-gate concern is partially confirmed. |
| **D4 per-tier eval** | BROAD P@30: 0.281 → 0.303 (**+0.022**) | v6 helps BROAD ranking measurably. |
| | ELITE P@30: 0.136 → **0.128** (−0.008) | v6 *HURTS* ELITE precision — opposite of M.md prediction. Production-relevant tier is hurt, not helped. |
| **D1 noise floor (20 perm)** | Real Spearman delta +0.0238; null distribution mean −0.0023, std 0.0070, p95 +0.0094; **p-value = 0.000** | The global signal IS real — well outside the CV-noise band. |

**Production decision:** v6 microstructure pack as currently built does
NOT ship. The cross-sectional signal IS statistically real (D1
permutation test: p=0.000) but it's lift in *the wrong part of the
distribution* and on *the wrong tier* for our production cascade.

**Synthesis after all 5 diagnostics:** v6 d0 microstructure features
are a real *state-descriptor* signal (current informed-trader regime,
current illiquidity, current trade clustering) but a poor *selection*
signal for the rare super-continuers we trade in ELITE. The ELITE
tier needs FORWARD-LOOKING information (e.g., d-1 informed-trader
positioning, news content, options gamma), not CURRENT-STATE
information about d0's trading regime.

---

## 1. What the user got right that I missed

I want to enumerate this explicitly because it's how I should have
written 135 in the first place.

### 1.1 "89% CPCV ≠ marginal pass; it's a fail"

I framed 89% / 90% as "marginal pass on the spirit, fail on the letter."
That was post-hoc gate-relaxation. The 90% gate exists precisely to
catch the failure mode where average metrics are good but tail-stability
is not. I should have written "FAIL" without softening.

### 1.2 OFI-as-liquidity-gate concern

OFI is NaN when no trades happen in [09:30, 10:00). I filled NaN with
0. Result: the model literally cannot distinguish "balanced flow" from
"no flow." 38% non-null coverage means 62% of OFI=0 is "no data," not
"balanced."

D3 confirms the user's instinct: removing OFI from the v6 pack drops
the lift from +0.0216 to +0.0173 — only ~20% of the lift was OFI.
And replacing OFI with binary `has_first_30_trades` makes things WORSE
(+0.0082), suggesting the binary is correlated with other v6 features
(specifically: VPIN/Kyle/Hawkes/Amihud are all NaN when no trades).
The "real" v6 signal is in the non-OFI features.

### 1.3 Two runs gave opposite-sign CPCV deltas

In 135 I waved this away ("Run 1 was small sample"). On re-inspection,
**Run 1's CPCV results were mathematically degenerate.** With only 4
OOS folds and `k_per_subset=4, replace=False`, every "random subset"
was identical (12, 13, 14, 15). 200 samples = 200 copies of one number.
The 100% frac>0 was structurally trivial, the +1.37 mean Sharpe was
a single observation reported as a distribution. That entire row of
the result table in 135 was uninformative.

This is worse than the user said. I should add a guard to the CPCV
function: `assert n_folds_available > k_per_subset` or it returns an
explicit error.

### 1.4 "Intellectual pace > evidence pace"

Diagnosing this is uncomfortable. I went from research doc to merge-to-
main in one session. The merge was technically safe (nothing wired in)
but the *narrative* I committed claimed Phase 1 had a confirmed verdict.
It didn't. The verdict was "promising on one fold scheme, ambiguous on
another, with internal CPCV results that were degenerate."

The right behavior would have been: ship the code + tests + backfill
as a research artifact, do the diagnostic phase BEFORE the validation
narrative, then write the verdict. Instead I wrote the verdict from
two weak measurements.

---

## 2. What I added that the user didn't push on

### 2.1 Lookahead audit (D5) — the deepest issue

Every v6 feature observes trades that happen AT OR AFTER 09:30 ET on d0:

```
vpin_d0:            full RTH d0
ofi_first30_d0:     [09:30, 10:00) ET d0
kyle_lambda_d0:     full RTH d0 5-min bars
hawkes_fano_d0:     full RTH d0
amihud_illiq_d0:    full RTH d0 5-min bars
iso_sweep_count_d0: full RTH d0
```

**Production entry happens at the 09:30:00 open or seconds after.** All
of these features are unavailable at decision time for an "enter at
open" model. They are *available* for:
- Hold-vs-exit decisions made at 09:35 or later
- Position-size adjustment after the open
- Research-evaluation of ret_t5 (which we did in 135 — that's not
  lookahead, just retrospective ranking)

But our production cascade decides "trade or skip" at the open. Even
if the v6 pack helped at all (it doesn't help the top tail per D2),
we couldn't wire it into the cohort cascade as currently designed.

This is a more fundamental problem than 135 acknowledged.

### 2.2 The CPCV degenerate-subset bug

Logged in `ml_v6_phase1_validation.py` for fix in next iteration:

```python
# Add guard:
if len(folds) < k_per_subset + 2:
    return {"error": f"CPCV needs >{k_per_subset+1} folds, got {len(folds)}"}
```

Without this guard, any run with too-few-OOS-folds silently produces
fake CPCV stats. Run 1 of doc 135 hit exactly this case.

---

## 3. The boldest honest interpretation

Two M.md quotes I should have weighed harder when designing the pack:

1. *"Microstructure features... expected lift on Spearman 0.03–0.07.
   This is the largest single move and dominates everything else."*
   — predicted lift band 0.03–0.07. We measured **+0.0157** on
   neutralized ρ. That's at the BOTTOM of M.md's predicted band.
   We're not exceeding M.md's prior; we're underperforming it.

2. *"ELITE is a data problem, not a modeling problem... With 85
   positives per fold, no architectural choice will rescue Spearman
   0.004; you need (a) more positives via lower threshold + ordinal
   CORN/CORAL, (b) auxiliary tasks providing inductive transfer (PLE),
   **or (c) honest acceptance via DSR that ELITE is not statistically
   significant and should not receive Aggressive Kelly capital.**"*

The third option — honest acceptance — is one I have not seriously
considered. D4 shows v6 features actively *hurt* ELITE precision.
That's not a bug to fix with more features; it might be evidence
that ELITE-tier predictions cannot be improved within this universe
and capital allocation to ELITE should be deprecated rather than
amplified.

This is uncomfortable because the s125 hybrid ELITE selector is
currently in production (`MX_HYBRID_ELITE=1` is the only flag we
left ON in 133's rollback). If ELITE is a noise tier, even the
hybrid selector is selecting on noise.

I'm not advocating disabling ELITE today. I am advocating that the
next experiment include a **null-DSR test on v3-tuned-16f's ELITE
predictions specifically.** If ELITE's standalone DSR is < 0.5
under N_trials = (number of ELITE configurations we've tried this
year, which is large), we should consider deprecating ELITE entirely.

---

## 4. Two redesign paths for Phase 1.5

### Path A: d-1 microstructure (the lookahead-safe redesign)

Recompute the entire v6 pack but use **prior-trading-day (d-1)**
trades instead of d0 trades. This tests M.md's implicit "informed
traders position BEFORE the news" thesis.

| Feature | d0 version (current, lookahead) | d-1 version (proposed, lookahead-safe) |
|---|---|---|
| vpin | informed flow on news day | informed flow on day before news |
| ofi_first30 | opening-window flow on news day | opening-window flow day before |
| kyle_lambda | liquidity fragility on news day | liquidity fragility day before |
| hawkes_fano | trade clustering on news day | trade clustering day before |
| amihud_illiq | illiquidity on news day | illiquidity day before |
| iso_sweep_count | aggressive flow on news day | aggressive flow day before |

The d-1 versions are observable at 16:00 ET on d-1, which is well
before the 09:30 ET d0 entry. **No lookahead.**

Builder change is one line (`day=d_minus_1`); validation rerun is the
same harness. Total turnaround: ~30 min backfill + 5 min validation +
30 min diagnostics. **This is the cheapest single experiment that
could rescue Phase 1.**

If d-1 microstructure helps ELITE specifically (where d0
microstructure didn't), we have a genuine forward-looking signal and
the v6 thesis is rescued.

If d-1 microstructure does NOT help ELITE either, we have strong
evidence that microstructure features are NOT the ELITE-rescue M.md
predicted, and we should pivot to news/options/sympathy features.

### Path B: skip microstructure, go to news/options

Per M.md §1C and §1F:
- FinBERT catalyst embeddings: M.md predicted lift 0.01–0.03 on
  Spearman, "especially valuable on ELITE where idiosyncratic
  catalyst quality dominates"
- Options-implied features (P/C ratio, IV skew, max pain, GEX):
  M.md predicted lift 0.01–0.03 on ELITE/HIGH tier

Both target ELITE explicitly, both are lookahead-safe (FinBERT on
news pre-09:30; options snapshot from prior close), both feed into
the existing tabular pipeline.

**Cost:** FinBERT requires news data we have partial coverage of
(`build_news_features_polygon.py` exists). Options requires a new
data source we don't yet have.

### Recommended: Path A first (cheap, decisive), then Path B if A fails

---

## 5. What stays committed vs gets reverted

**Stays:**
- `scripts/v6_microstructure_pack.py` — feature functions are correct,
  unit tests pass; useful for d-1 redesign and as a reference impl.
- `scripts/build_v6_microstructure_pack.py` — pipeline is correct.
- `scripts/ml_v6_phase1_validation.py` — validation harness, with a
  CPCV guard fix (filed as a follow-up).
- `scripts/ml_v6_phase1_diagnostics.py` — this is the artifact of this
  doc; reusable for any future feature-pack evaluation.
- `tests/unit/test_v6_microstructure_pack.py` — 24 unit tests.
- `data/.../microstructure_v6_pack.parquet` — backfill cached for
  Path A reuse (d-1 version will write to a different parquet).

**Updates:**
- 134 doc — add a note pointing forward to 135 + 136 for the verdict.
- 135 doc — add a top-of-file ⚠️ pointing to 136 for the corrected
  interpretation (do not delete; the wrong-narrative + the correction
  are both part of the research record).

**Production launcher:** UNCHANGED. v6 was never wired in.
`MX_USE_MOMTRANS=0`, `MX_TIERED_LEARNING=0`, `MX_HYBRID_ELITE=1`.

---

## 6. The bigger lesson for me

The pattern across v5 (negative) → Phase 0 (positive: caught a real
multiple-testing artifact) → Phase 1 (claimed marginal-positive, was
actually marginal-negative under diagnostics) is:

**My validation discipline is good. My narrative discipline is not.**

When the numbers come back ambiguous, I lean toward writing them up
as positive-with-caveats rather than negative-with-caveats. The Phase 0
result was unambiguous (DSR 0.05 vs 1.0 = clear), so I called it
correctly. The v5 result was unambiguous in the wrong direction
(Spearman 0.041 vs 0.141), so I called THAT correctly too. But the
Phase 1 result was ambiguous, and I picked the optimistic frame.

The fix: before writing any verdict, run the diagnostic phase — null
floor + per-decile + ablation + per-tier — and let those determine
the narrative. If a 30-line "diagnostics first, narrative second"
checklist had been part of the Phase 0 hygiene rule, doc 135 would
have been written as 136 from the start.

I'm adding this to the v6 hygiene contract:

> Every v6 feature pack evaluation MUST include the 5 diagnostics
> from `scripts/ml_v6_phase1_diagnostics.py` BEFORE any verdict is
> written. The narrative is downstream of the diagnostics.

---

## 7. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v6_phase1_diagnostics.py` | NEW (~370 LOC) | All 5 diagnostics; reusable harness |
| `data/models/v6_phase1_diagnostics.json` | NEW (gitignored) | Numerical results from D2-D5 |
| `data/models/v6_phase1_d1_noise_floor.json` | NEW pending (gitignored) | D1 noise floor (running in background) |
| `docs/research-log/136_v6_phase1_diagnostics_honest_negative.md` | NEW (this doc) | Corrected interpretation; Phase 1.5 plan |

---

## 8. Decision matrix going forward

```
IF D1 noise floor shows real-delta > p95(null):
  GOTO Path A (d-1 microstructure) — invest one more day
ELSE:
  Phase 1 is fully closed. The v6 microstructure pack does not work.
  SKIP TO M.md §1C (FinBERT catalysts) or §1F (options-implied)
  Both target ELITE specifically per M.md.
```

The honest research arc:
- v3 (Spearman ~0.16) — production
- v4 cohort cascade — looked +$46k but was multiple-testing artifact (Phase 0)
- v5 architecture — went BACKWARDS (rho 0.04)
- v6 Phase 1 microstructure — does NOT help ELITE (this doc)
- v6 Phase 1.5 — d-1 microstructure OR pivot to news/options
