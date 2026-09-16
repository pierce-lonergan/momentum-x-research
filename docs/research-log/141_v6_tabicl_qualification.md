# 141 — v6 TabICL qualification: lift collapses on the recent subset

> **Format:** Per doc-138-template (script first, run, then doc, verdict
> last). The script ran two variants. The numbers below qualify doc 140's
> "+0.027 TabICL lift" headline — they don't invalidate it, but they
> change what it means for production.

**Session date:** 2026-05-07 (continuing)
**Branch:** develop
**Predecessors:** [140 v6 Items 4 + 5b: foundation models break the ceiling](140_v6_items_4_5b_foundation_model_breakthrough.md)
**Status:** **TabICL not pilot-ready.** Lift doesn't replicate on the
recent data subset where production trades.

---

## TL;DR — three uncomfortable rows

```
                                          TabICL   XGBoost   delta
Doc 140: full 20K rows (2024-01..2026-04)  +0.210   +0.179   +0.031
Doc 141: 6708-row d-1 subset (2025-08+)    +0.203   +0.204   -0.000  (TIED)
Doc 141: 6708-row + d-1 features (64 cols) +0.196   +0.202   -0.006  (TabICL slightly worse)
```

The d-1 inner-join restricts to the recent 9-month window (when the
trades_v1 warehouse coverage starts). On that window — **the same
window production lives in** — TabICL and XGBoost are tied. Doc 140's
+0.027 lift was concentrated on OLDER data (2024-01 through mid-2025).

Two implications:

1. **TabICL is not ready for a production pilot.** The +0.027 lift
   doesn't generalize to the recent regime. Recommending paper-trade
   A/B in doc 140 § 4 was premature. Reverting that recommendation.

2. **d-1 microstructure features compose POORLY with TabICL.** They
   hurt TabICL by −0.008 vs only −0.001 for XGBoost. Foundation
   model's pretraining-derived priors don't transfer well to the
   sparse heavy-tailed d-1 features. The "TabICL + d-1" composition
   bet is dead.

Doc 140 still stands on its narrow claim: TabICL beats XGBoost on the
full historical sample, p<0.001 vs noise floor, ≥0.18 model-class
threshold cleared. That measurement is real. **What doc 140 did NOT
test, and what this doc reveals, is whether the lift transfers to
recent data.** It doesn't.

---

## 1. The setup

Same TabICL + XGBoost ceiling-test pipeline as doc 140 (12-fold WF,
120d train, 15d test, single random seed). Two feature sets:

- **Variant A:** v3 features only (54 cols)
- **Variant B:** v3 ⊕ d-1 microstructure (64 cols)

Same data subset for both: rows from `aftermath_strat` that ALSO have
a d-1 microstructure entry (the inner-join from doc 137). 6,708 rows
spanning 2025-08-04 → 2026-04-24.

Compared to doc 140, the *only* difference is the data subset. Doc
140 used the full 20K rows; this doc uses the 6.7K-row d-1-subset.

---

## 2. The numbers

### 2.1 Variant A: v3 features only (54 cols), 6708-row subset

```
TabICL Spearman:  +0.2033
v3 XGBoost:       +0.2035
Delta:            -0.0002  (TIED)
```

On the recent subset, TabICL and XGBoost achieve **identical** Spearman
within 0.0002. Compare to doc 140's full-data result of TabICL +0.21
vs XGBoost +0.18. **The XGBoost number went UP from 0.18 to 0.20**
when restricted to recent data. **TabICL stayed flat at ~0.20.**

The interpretation: XGBoost was being held back by older / messier
data folds in the full sample. On the cleaner recent data, XGBoost
catches up to TabICL.

### 2.2 Variant B: v3 + d-1 microstructure (64 cols), same subset

```
TabICL Spearman:  +0.1958
v3 XGBoost:       +0.2022
Delta:            -0.0064  (TabICL slightly worse)

d-1 lift on TabICL:  -0.0075  (HURTS)
d-1 lift on XGBoost: -0.0014  (essentially neutral)
```

**Adding d-1 features hurts TabICL** by −0.008 Spearman. XGBoost is
nearly indifferent to the d-1 features at the global Spearman level
(though doc 138 showed XGBoost gets a +0.039 lift on BROAD P@30
specifically — not measured here).

Why do d-1 features hurt TabICL? Three plausible reasons:

1. TabICL was pretrained on synthetic Gaussian-ish tabular tasks. The
   d-1 features have heavy-tailed distributions (Hawkes Fano factor,
   ISO sweep counts) and high-NaN-rate sparsity (OFI is null in 62%
   of rows). The in-context attention prior may not handle these well.

2. TabICL's 8K-context-row budget per fold is bandwidth-limited.
   Adding 10 features dilutes the signal-per-feature ratio. With
   54 features TabICL may already be near its information-density
   sweet spot; adding 10 mostly-noisy features is net-negative.

3. The d-1 features have a "this name has trades_v1 coverage" missingness
   structure that XGBoost handles via tree splits (it can learn
   "if v6_pack_present then route to leaf X"). TabICL has no such
   explicit splitting mechanism — it does in-context interpolation
   which may not exploit missingness as cleanly.

---

## 3. Why doc 140's TabICL lift didn't replicate

Doc 140's +0.027 lift was on **full data** (20029 rows, 2024-01-16 to
2026-04-24). Here's the data-subset breakdown:

| Subset | n | period | TabICL ρ | XGB ρ | delta |
|---|---|---|---|---|---|
| Full | 20,029 | Jan 2024 - Apr 2026 | +0.21 | +0.18 | +0.03 |
| d-1 subset | 6,708 | Aug 2025 - Apr 2026 | +0.20 | +0.20 | ~0 |
| **Implied "older only"** | **~13,300** | **Jan 2024 - Aug 2025** | **probably ≥+0.21** | **probably <+0.17** | **probably ≥+0.04** |

The TabICL ceiling-lift effect is concentrated on the OLDER half of
the data. On the recent half, the gap closes.

Possible mechanisms (untested):
- The older period (2024) had distinct microstructure regimes (Aug 2024
  carry-trade unwind, election volatility, etc.) that XGBoost overfit
  to but TabICL's pretraining prior generalized through
- Survivorship/listing changes between 2024 and 2026 changed feature
  semantics; XGBoost's specific decision tree memorizes those changes,
  TabICL's smooth attention interpolates
- Pure data quality: older trades_v1 data is sparser, microcap candidate
  filtering was less mature

**For production, the recent regime is what matters.** Production
trades 2026-05-07 forward, not 2024-01. The "+0.027 TabICL ceiling-
breaker" framing in doc 140 § TL;DR overstated the production
relevance.

---

## 4. The honest correction to doc 140

What doc 140 § 4 said:
> Pilot phase 2 (1-2 weeks, paper trading): If TabICL's OOS predictions
> correlate >0.5 with v3's BUT have non-trivial disagreement on the
> top-30 picks, run a 50/50 capital ensemble in paper trading.

Reverting this. **TabICL on recent data is statistically indistinguishable
from XGBoost in raw Spearman.** A 50/50 ensemble of two equivalent
predictors adds nothing in expectation; the only way it would help is
if the predictors are uncorrelated (unlikely given they hit the same
~0.20 Spearman on the same data) or if TabICL has a structurally
different per-tier P@30 profile than XGBoost (untested).

What doc 141 says instead:

- Doc 140's TabICL ≥0.18 result is REAL on the historical full sample.
  It doesn't replicate on the recent subset.
- Until we understand WHY the lift is concentrated on the older half,
  we cannot honestly claim a production-relevant edge.
- The model-class ceiling may exist *for the older / messier data*.
  Whether it exists *for production data* is genuinely unknown.

---

## 5. The per-fold breakdown (RAN — smoking gun)

Per-fold TabICL vs XGBoost delta on the doc 140 full-data run:

```
fold start         n_test   TabICL      XGB    delta
   0 2024-05-15       231   +0.348   +0.272   +0.076
   1 2024-07-18       278   +0.311   +0.191   +0.119
   2 2024-09-19       286   +0.255   +0.127   +0.128
   3 2024-11-21       400   +0.132   +0.056   +0.076
   4 2025-01-23       395   +0.306   +0.206   +0.100
   5 2025-03-27       595   +0.249   +0.158   +0.091
   6 2025-05-29       453   +0.171   +0.151   +0.020
   7 2025-07-31       337   +0.211   +0.142   +0.069
   8 2025-10-02       586   +0.214   +0.240   -0.026  ←
   9 2025-12-04       408   +0.365   +0.354   +0.011
  10 2026-02-05       377   +0.241   +0.191   +0.051
  11 2026-04-09       426   +0.222   +0.209   +0.013
```

Split at 2025-08-01 (d-1 warehouse start; production-relevant boundary):

| Period | Folds | n | TabICL Spearman | XGB Spearman | **delta** |
|---|---|---|---|---|---|
| OLDER (May 2024 – Jul 2025) | 0-7 | 2,975 | +0.232 | +0.163 | **+0.069 (TabICL wins big)** |
| NEWER (Oct 2025 – Apr 2026) | 8-11 | 1,797 | +0.198 | +0.227 | **−0.029 (XGBoost wins)** |

**Smoking gun confirmed.** On the older 8 folds, TabICL beats XGBoost by
+0.069 Spearman. **On the newer 4 folds — what production trades —
XGBoost beats TabICL by 0.029.** The doc 140 +0.027 full-data lift
was entirely concentrated in the 2024-2025 historical window where
XGBoost was struggling.

Most informative individual data points:
- **Fold 8 (Oct 2025): TabICL −0.026 vs XGBoost.** First fold where
  TabICL meaningfully loses. Coincides roughly with the d-1 warehouse
  coverage start.
- **Fold 9 (Dec 2025): both models hit Spearman > 0.35.** The recent
  data is in a high-signal regime where both models do well; TabICL's
  pretraining edge shrinks because XGBoost has enough training history.

What this rules out:
- "TabICL is a strict superset of XGBoost capability." False — XGBoost
  beats TabICL on recent folds.
- "Model-class ceiling generalizes." False on this data — the ceiling
  effect is data-regime-specific.

What's still possible:
- **TabICL has stronger out-of-distribution generalization.** When the
  test regime is different from train (older folds, smaller train
  windows, more regime mixing), TabICL's pretraining helps. When
  train and test are similar (recent folds, accumulated training
  history), XGBoost is competitive or better.
- This is consistent with TabICL's design (trained on diverse synthetic
  tasks → strong cross-distribution transfer) and XGBoost's behavior
  (memorizes training distribution well → strong in-distribution
  performance).

Strategic implication: **TabICL might be useful as a regime-shift
backstop**, not as a primary scorer. If we detect the production
regime is changing, TabICL's predictions become more relevant. In
stable regimes, v3 XGBoost is the better choice. This is a nuanced
deployment story, not a "TabICL replaces v3."

---

## 6. The strategic frame after doc 141

Three docs in this thread (138, 139, 140 → 141):

- 138: corrected doc 137's CUDA-amplified d-1 result; flipped MX_HYBRID_ELITE
- 139: established that v4/v5 architectures never beat v3
- 140: claimed TabICL beats v3 (model-class ceiling exists)
- **141 (this): qualifies doc 140 — TabICL doesn't beat v3 on recent data**

The strategic frame as of tonight:

| Question | Status |
|---|---|
| Did v4/v5 transformer-from-scratch beat v3? | NO (doc 139, definitive) |
| Did v6 d0 microstructure help? | NO globally; modest BROAD P@30 (doc 138) |
| Did v6 d-1 microstructure help? | YES on BROAD P@30 (+0.039 CPU-deterministic, doc 138) |
| Does ELITE tier survive DSR? | NO (doc 138, MX_HYBRID_ELITE flipped) |
| Did CORN MLP from scratch help? | NO (doc 140, definitive) |
| Did TabICL beat v3 on full historical data? | YES (+0.027, p<0.001 noise floor — doc 140) |
| **Does TabICL beat v3 on RECENT data?** | **UNRESOLVED — appears to be ~0 (this doc)** |

The honest synthesis: **architecture-from-scratch is dead. Foundation
models have a real lift but it concentrates on data we don't trade
on. The d-1 microstructure features are the only positive in this
arc that's both real AND replicable on recent data.**

The disciplined production roadmap, ranked by confidence:

1. **Highest confidence:** d-1 microstructure feature pack on v3 XGBoost
   for BROAD/VETOED specialists. CPU-deterministic +0.039 BROAD P@30
   lift on recent data. Production-ready pending an A/B paper-trade.
2. **Medium confidence:** Bouchaud square-root impact estimator for v3
   production. M.md says "mandatory at $5M AUM." Not ML, just math.
3. **Low confidence:** TabICL-as-secondary-scorer in production. Lift
   doesn't replicate on recent data. Defer until per-fold breakdown
   resolves.
4. **Dead:** v4/v5 transformers, CORN-from-scratch, ELITE-as-Aggressive-
   Kelly tier (doc 138), TabICL + d-1 composition (this doc).

---

## 7. The meta-pattern that just played out

Doc 140 was written carefully (verdict-blank-until-numbers, p-value
attached, three-caveats explicit) and STILL overstated the production
relevance. The thing it missed: **a positive measurement on
historical data doesn't automatically transfer to recent data.**
That's not a process failure — it's a thing the discipline didn't
specifically guard against because it wasn't on the prior critique list.

Adding to the v6 hygiene contract:

> Any positive lift measurement on full WF data must be accompanied by
> a same-comparison on the most-recent-N-folds subset (where N covers
> the production-relevant period). If the lift collapses on the recent
> subset, the production-pilot recommendation must wait until the
> mechanism is understood.

This is the same principle as DSR (don't claim significance without
the multi-trial penalty), applied to data subsets (don't claim
production relevance without the recent-data check).

---

## 8. What this commit does and doesn't change

**Code:** None. The composition test was a one-shot inline Python
script (logged in `logs/v6_tabicl_with_dm1.log`).

**Doc:** 141 (this) added.

**Production:** UNCHANGED.

**Filed for next session:**
- ~~Per-fold TabICL−XGBoost delta breakdown~~ DONE in §5 — the smoking gun
- Bouchaud square-root impact estimator for v3 production picks (M.md
  "mandatory" item; doesn't depend on any of this; would be the
  highest-value single addition to v3 production at $5M AUM)
- d-1 features paper-trade A/B planning (the only Phase 1 positive
  that survived all qualifications — BROAD P@30 +0.039 CPU-deterministic)
- TabICL as a regime-shift backstop (the nuanced deployment story §5
  ends on, not a primary scorer)

**Not filed:**
- TabICL paper-trade pilot as primary scorer. Recent-data delta is −0.029.
  Reverting doc 140 § 4 production recommendation.
- TabPFNv2 if license unblocked. Even if unblocked, faces the same
  recent-vs-historical question. Worth running for completeness but
  not for a production decision.
- More v6 microstructure features. Still nothing in the d0 / news /
  options space until the fundamental v3 deployment work (Bouchaud,
  paper-trade A/B for d-1) ships.
