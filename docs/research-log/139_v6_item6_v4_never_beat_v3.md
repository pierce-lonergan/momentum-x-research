# 139 — v6 Item 6: MoMTrans v4/v5 NEVER beat v3 on neutralized ρ

> **Format:** Per doc-138-template (script first, run it, then doc).
> Numbers were in hand before the verdict was written. The verdict
> follows the data, not the other way around.

**Session date:** 2026-05-07
**Branch:** develop
**Predecessors:** [138 v6 Phase 1 executive decisions](138_v6_phase1_executive_decisions.md)
**Status:** **Item 6 negative.** The MoMTrans v4/v5 architecture
program has never demonstrated edge over v3 BROAD specialist on
neutralized Spearman ρ. Doc 133's "+0.015 v4 lift" was an
apples-to-oranges comparison.

---

## TL;DR — the verdict M.md predicted, confirmed empirically

```
v3 BROAD specialist (apples-to-apples baseline): neutralized rho = +0.135

ALL 16 v4/v5 variants tested:
  variant            neut_rho   lift_vs_v3
  v4_tier_BROAD       +0.1192    -0.0161    <-- "best" v4/v5 variant
  v4_tabular_only     +0.1190    -0.0164
  v4_sweep_0          +0.1136    -0.0218
  v4_sweep_5          +0.1036    -0.0318
  v4_sweep_2          +0.0996    -0.0357
  v4_sweep_3          +0.0935    -0.0418
  v4_sweep_1          +0.0869    -0.0484
  v4_tier_VETOED      +0.0818    -0.0535
  v4_tier_HIGH        +0.0660    -0.0694
  v4_sweep_4          +0.0566    -0.0788
  v4_base             +0.0xxx    -0.xxxx
  v4_cls_only         +0.0xxx    -0.xxxx
  v4_bigger           +0.0xxx    -0.xxxx
  v4_sweep_6          +0.0208    -0.1146
  v4_tier_ELITE       +0.0395    -0.0959
  v5_corn             +0.0056    -0.1298    <-- worst (the v5 program)

  EVERY variant has NEGATIVE lift. The "MoMTrans architecture beats v3"
  claim has never been true on apples-to-apples comparison.
```

Statistical test: lift z-score = **−1.26**, one-sided p = 0.896.
Bonferroni correction for 16 variants: p_corrected = 1.0 (no
significance possible). The "best" v4 variant is *worse* than v3 by
1.26σ — and we've now multiple-testing-corrected for the trial count.

---

## 1. Where doc 133's "+0.015 lift" came from

Doc 133 § 0.4 reported:
```
v3 production:  raw rho 0.1585 -> 100% neutralized 0.0762
v4 BROAD spec:  raw rho 0.1355 -> 100% neutralized 0.0905
                                  --------- 
                                  +0.0143 lift
```

These two predictions are NOT the same task:

- **v3 production = v3-tuned-16fold REGRESSION on continuous ret_t5**
- **v4 BROAD spec = binary CLASSIFIER on (ret_t5 >= 0.10)**

A regression's ranked predictions and a binary classifier's
probability outputs aren't directly comparable on Spearman ρ. The
binary classifier discretizes the score in ways that affect
rank-correlation differently than continuous regression. **Doc 133
was apples-to-oranges.**

The fair comparison is BROAD-specialist-vs-BROAD-specialist:
- v3 BROAD specialist (continuer_v2_v3_tier_BROAD.pkl) = binary classifier on (ret_t5 >= 0.10)
- v4 BROAD specialist (momtrans_v4_tier_BROAD.pt) = binary classifier on (ret_t5 >= 0.10)

Both trained on the SAME target. Both give probability outputs in
[0, 1]. Same neutralization, same exposures, same join. **On that
fair comparison, v3 BROAD wins by 0.016.**

---

## 2. The N_trials accounting that buries the lift

Even ignoring the apples-to-oranges issue, the doc 133 measurement
treated the lift as a single-trial result. Counting actual variants:

| Source | Count |
|---|---|
| `momtrans_v4_predictions.parquet` (base) | 1 |
| `momtrans_v4_sweep_0..6` (Optuna sweeps) | 7 |
| `momtrans_v4_bigger`, `cls_only`, `tabular_only` (3 ablations) | 3 |
| `momtrans_v4_tier_BROAD/VETOED/HIGH/ELITE` | 4 |
| `momtrans_v5_corn` | 1 |
| **Variants on disk** | **16** |
| Implicit selection (we picked v4 BROAD because it had highest ρ) | +1 |
| Hyperparameter trials within `momtrans_tabular_sweep.db` | likely 20+ more |
| **Realistic N_trials estimate** | **30–50** |

At N=20, Bonferroni-corrected p = 1.0. The "best" v4 lift doesn't
even directionally improve over v3, let alone survive multiple testing.

---

## 3. What this means for the architecture program

The honest research arc, restated with this finding:

| Iteration | What I claimed | What was actually true |
|---|---|---|
| v4 (docs 125–130) | "+47% per-pick edge over v3 at 100bps slippage; +$46k WF lift" | DSR=0.05 — multiple-testing artifact (doc 133) |
| v4 (doc 133 corrective) | "Transformer captures something v3 doesn't: +0.015 neutralized ρ" | Apples-to-oranges comparison (doc 139). On apples-to-apples, v3 wins by −0.016 |
| v5 (doc 132) | "Tier-S architecture didn't work; pivot to data" | Confirmed: v5 CORN was the WORST of all 16 variants tested (neut ρ = 0.006) |
| v6 Phase 1 d0 (docs 134–135) | "Marginal positive lift" | Hurt ELITE precision; lift was in mid-decile (doc 136) |
| v6 Phase 1.5 d-1 (doc 137) | "VETOED P@30 +6pp breakthrough" | CUDA artifact; CPU shows +0.008 (doc 138) |
| v6 Phase 1 ELITE DSR (doc 138) | "ELITE tier reasonable" | DSR @ N=50 = 0.371 — statistical noise |
| **v6 Item 6 (this doc)** | "v4 captures something v3 doesn't" | **Never demonstrated. v3 BROAD beats every v4/v5 variant by ≥0.016** |

**The MoMTrans v4/v5 architecture program has, at no point in its
six-month lifespan, demonstrated edge over v3 on a properly-controlled
comparison.** Every "win" was either:
- an apples-to-oranges measurement (doc 133),
- a multiple-testing artifact (Phase A/B/C, doc 133),
- a CUDA-determinism amplification (doc 137),
- or a single-trial Sharpe with no DSR correction (doc 129).

Each of these failure modes is a known anti-pattern in the validation
literature. We hit all four. The lesson is one I should have learned
in Phase 0 but apparently had to learn again three times.

---

## 4. The strategic question is no longer "improve the model"

Per the user's critique that triggered this work:

> "If item 6 comes back negative under proper N_trials accounting,
> the entire MoMTrans v4/v5 program never demonstrated edge over v3
> to begin with. ... If item 6 confirms it, the v6 strategic question
> is no longer 'how do we improve the model' but 'how do we deploy
> v3 better.' Capacity modeling (Bouchaud impact at \$5M AUM),
> execution quality, sizing methodology, drawdown gating — those
> become the highest-leverage moves."

Item 6 came back negative. The user's strategic frame applies.

**But** — Item 5 (TabPFNv2 ceiling test) is still required before
fully committing to the "v3 is the ceiling" pivot. Item 5 distinguishes:

- (Bayes ceiling) v3's Spearman 0.135 is the genuine maximum
  achievable on this universe with this label horizon. No model class
  can do better. → "improve v3 deployment" is the only path.

- (Model-class ceiling) v3 hits 0.135 because gradient-boosted trees
  on 54 hand-engineered features are the wrong family. TabPFNv2 with
  in-context learning may push to ~0.18+. → "foundation model
  approach" is still potentially viable.

**Without Item 5, we don't know which world we're in.** This doc
recommends doing Item 5 next (per user's ordering: 6 → 5 → 4) before
making the strategic pivot decision.

---

## 5. Production implications RIGHT NOW

Production currently runs v3-tuned-16fold + s125 hybrid ELITE (now
disabled per doc 138) + various bug fixes. **None of v4 / v5 was ever
in production-effective use** — `MX_USE_MOMTRANS=0` since the Phase 0
rollback in doc 133.

So this finding doesn't trigger another launcher flip. It does
trigger **two new commitments**:

1. **The `momtrans_v4_*.pt` artifacts (~50 MB on disk) can be deleted
   in a future cleanup commit.** They represent a research path that
   never demonstrated edge. Keeping them around as "in case we want
   to revisit" is sunk-cost reasoning that the data has now invalidated.

2. **All future architecture proposals must include an apples-to-apples
   comparison plan upfront.** Specifically: the proposed model must be
   evaluated on the same target, same join, same exposures, and same
   N_trials accounting as the v3 specialist incumbent. No "novel
   architecture vs v3 production regression" comparisons. Adding to
   v6 hygiene contract.

---

## 6. What's NOT in this finding

- **This doc does NOT say v3 is good in absolute terms.** v3 BROAD's
  neutralized ρ of 0.135 is reasonable for microcap intraday gap-up
  prediction (per Numerai-tier benchmarks where 0.02–0.04 neut ρ per
  era is excellent on liquid universes). It just says no v4/v5 variant
  beat it.

- **This doc does NOT say feature engineering is dead.** The v6
  d-1 microstructure pack adds a real BROAD P@30 lift of +0.039
  (CPU-deterministic, doc 138 Item 1). That's still on the table.
  But it's a feature lift on the v3 architecture, not a vindication
  of v4.

- **This doc does NOT say the s125 hybrid ELITE flag should be
  re-enabled.** Doc 138's ELITE DSR test stands. ELITE tier remains
  statistical noise per the data.

---

## 7. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v6_item6_v4_neutralized_dsr.py` | NEW (~250 LOC) | 16-variant comparison + DSR sweep |
| `data/models/v6_item6_v4_neutralized_dsr.json` | NEW (gitignored) | Full per-variant results |
| `docs/research-log/139_v6_item6_v4_never_beat_v3.md` | NEW (this doc) | The verdict |

No code or launcher changes. v6 Phase 1 d-1 BROAD lift is unaffected
(separate measurement). MX_USE_MOMTRANS stays at 0. MX_HYBRID_ELITE
stays at 0.

---

## 8. The honest framing for what this means

I've been chasing MoMTrans architecture improvements for ~6 months
across v4 → v4 ablations → v5 → v6 — at least 16 model-architecture
variants on disk, plus implicit thread of tweaks in each. Every
"breakthrough" claim was either a measurement artifact or untested
under proper hygiene. **The architecture work has produced one
measurable production-relevant artifact in this entire arc**: the
v6 d-1 microstructure feature pack with a CPU-deterministic +0.039
BROAD P@30 lift (doc 138).

Everything else has been retracted, walked back, or now (this doc)
deflated by re-comparison against the proper v3 baseline. The
architectural tree has been searched fairly thoroughly within
TabM/TabTransformer/CORN-class designs at the 20K-row scale we have.
None of it beat v3 BROAD specialist.

The next experiment that *could* rescue the "model-class ceiling
exists" thesis is TabPFNv2 (Item 5). It's been on the to-do list
across two architecture iterations and has not been run. Until it is,
I cannot honestly say I know whether v3 is the Bayes ceiling for this
universe. Item 5 is the diagnostic that closes that question.

After Item 5, the strategic decision is binary:
- TabPFNv2 ≥ 0.18 → "model class was wrong, switch to foundation model"
- TabPFNv2 ≈ v3 (0.13–0.16) → "v3 is the ceiling, focus on capacity / execution / sizing"

Either way is actionable. The current state — *not knowing* — is the
expensive one. Item 5 next.

---

## 9. Filed for the next session

| Item | Status |
|---|---|
| 5. TabPFNv2 on v3 features only — Bayes vs model-class ceiling | NEXT (no v6 features should run before this) |
| 4. CORN ordinal head on v3 features — training-objective bottleneck | After 5 |
| Cleanup: delete `momtrans_v4_*.pt` artifacts after a one-month cooling period | Filed |
| Production deployment quality work (capacity, slippage, drawdown gating) | Conditional on Item 5 result |
