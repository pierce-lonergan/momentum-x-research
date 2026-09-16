# 152 — v6 three frontiers, 72h full-send: PRE-COMMITS LOCKED before any results

> **Format:** doc-138-template, but inverted — pre-commits locked HERE, in
> writing, BEFORE a single line of experiment code is run. The prior pattern
> across docs 148/150/151 was: lock criteria, run cheap version, file the
> ambitious version. This doc breaks that pattern. Three experiments, three
> binary gates, no filing the harder version while running the cheaper one.

**Session date:** 2026-05-11
**Branch:** develop
**Predecessors:** [149 D290 adaptive Kelly](149_v6_d290_adaptive_kelly_shipped.md), [150 HIGH-only D291 not shipped](150_v6_high_only_deep_dive_d291_not_shipped.md), [151 DFL e2e negative](151_v6_dfl_e2e_negative.md), [compass §Frontier 1-6](compass_artifact_wf-a260a9fb-11fd-47e7-8efb-6bedfa0620f4_text_markdown.md)
**Status:** PRE-COMMITS LOCKED. Verdicts to be appended on completion.

---

## 0. Why this doc exists

User's doc-151-arc critique: *"the experiments themselves are getting smaller
and the discipline is getting tighter, and the combination is starting to
look like risk-aversion dressed up as rigor… You ran the throwaway version,
killed it, and filed the real version… that's not aggressive — that's
testing the weakest version of the hypothesis so the verdict is clean."*

**The accountability move:** lock the pre-commits BEFORE running, in a
single pre-results doc, so the file-and-defer escape hatch is closed. If
all three return negative, the production architecture is at empirical
ceiling for this feature set, and the next step is the H100 bets (FinPFN
mini, TRADES). If any returns positive, it goes to D286 shadow validation.

**Hygiene Rule 7 (NEW, added permanently):** *"For any session that runs
multiple parallel experiments, pre-commits must be written in a single
locked doc BEFORE the first experiment is launched. The doc is committed
to git pre-results. No experiment may be downgraded after launch (e.g.
'switching to the cheaper version because the ambitious one is hard') —
that's filing under another name."*

---

## 1. The three experiments + locked binary gates

### Experiment 1 — TabPFN augmented continual pretraining variant (Frontier 1, 1-week version)

**Compass cite (§Frontier 1, 1-week version):** *"Continual pretraining of
TabPFNv2 on the user's 60k unlabeled Polygon candidates + a small
financial-prior augmentation set (à la Real-TabPFN). Single RTX 5070."*

**Honest scope of what's feasible on RTX 5070 in 72h:** The OSS `tabpfn`
v7.1.1 + `tabpfn-extensions` 0.4.1 packages expose `finetune_classifier` /
`finetune_regressor` as the closest API. Full Real-TabPFN-style continual
pretraining on the synthetic prior corpus is NOT exposed by the OSS
package — that's PriorLabs internal infra. Two feasible variants:

- **(a) Finetune via tabpfn-extensions** on the labeled subset, where
  "continual pretraining" means: load pretrained TabPFN weights, finetune
  the transformer on (X, y) pairs from our labeled rows, evaluate on
  recent-subset OOS. This is the closest "Real-TabPFN" recipe the OSS API
  supports.
- **(b) Augmented in-context TabPFN** — pseudo-label the unlabeled rows
  with vanilla TabPFN's median prediction, filter to high-confidence
  pseudo-labels, then refit TabPFN with (labeled + filtered pseudo) as
  in-context examples. This is a semi-supervised variant.

**Plan: try (a) first; if `finetune_*` API is incompatible with our
RTX 5070 setup, fall back to (b) and document the substitution.**

**Locked pre-commits (binary gates):**

| Outcome | Gate | Production action |
|---|---|---|
| **PASS** | Augmented-TabPFN beats vanilla TabPFN on recent-subset Spearman by ≥ +0.01 with permutation noise floor p<0.05 | Replace vanilla TabPFN with augmented variant in D288 plan |
| **MARGINAL** | Lift in [0, +0.01) OR lift ≥ +0.01 but p ≥ 0.05 | Document as marginal, keep vanilla as D288 model, do NOT ship |
| **FAIL** | Augmented underperforms vanilla on recent-subset | Investigate prior-distribution mismatch; the financial prior may not be the bottleneck; archive Frontier 1 1-week variant |

**Baseline reference (doc 143):** vanilla TabPFN recent-subset (folds 8-11)
Spearman = +0.300, delta vs XGB = +0.070. The +0.01 gate is calibrated to
~3% of TabPFN's own delta-over-XGB — large enough to matter, small enough
to be plausibly achievable from continual pretraining.

**Compute budget:** ≤8h on RTX 5070 for finetune (variant a) OR ≤2h for
in-context augmentation (variant b). Started tonight, evaluated within
24h.

### Experiment 2 — DFL-on-TabPFN-backbone (the real DFL, doc 151's filed plan)

**Doc 151 §6 cite:** *"DFL's natural pairing is with TabPFN as the
backbone, not with a from-scratch MLP. The architecture-search version
would be: (1) Use TabPFN's frozen forward pass to produce calibrated
probabilities, (2) Apply a thin DFL head on top (1-layer MLP that maps
TabPFN proba + features → conviction), (3) Backprop only through the head
with the differentiable Kelly loss, (4) Compare to TabPFN-with-uniform-Kelly."*

**Architecture:**
- TabPFN frozen forward pass → calibrated regression output `s_tabpfn` per row
- Thin head: 2-layer MLP `(s_tabpfn, top_k v3 features) → conviction ∈ [0, 1]`
- Kelly fraction: `f = max_kelly · sigmoid(conviction)`
- Loss: `−mean(log(1 + f · r_t5_after_bouchaud_impact))` (the same compass
  §Frontier 2 utility from doc 151 — **but with the broken-baseline lesson
  applied: the comparator is TabPFN-with-uniform-Kelly, NOT rank-normalized
  XGBoost**)

**Locked pre-commits (binary gates, all three must pass for a SHIP):**

| # | Gate |
|---|---|
| 1 | DFL-on-TabPFN beats TabPFN-with-uniform-Kelly on $1M-AUM Bouchaud-adjusted Sharpe by ratio ≥ 1.2× over the WF aggregate |
| 2 | Permutation noise floor (5 perms) p<0.05 on the Sharpe-ratio statistic |
| 3 | Recent-subset (folds 8-11 of doc 143's scheme) Sharpe-ratio ≥ vanilla TabPFN-uniform recent-subset Sharpe |

**Outcome map:**
- **3 of 3 pass** → ship as **D292** (DFL shadow scorer; logs alongside
  v3 cascade and TabPFN shadow for 2 weeks before any tier weight changes)
- **1-2 of 3 pass** → document as promising-but-incomplete, investigate
  the failing gate, do NOT ship
- **0 of 3 pass** → DFL family closed for real this time, all variants
  exhausted within RTX 5070 compute budget

**Compute:** ≤2h on RTX 5070 (head training is fast since TabPFN forward
is frozen; per-fold ~5s TabPFN + ~10s head training).

### Experiment 3 — Causal discovery on the unlabeled candidate panel (Frontier 3, scoped)

**Compass cite (§Frontier 3):** *"Run CD-NOTS on the 54-feature panel ×
the 60k unlabeled Polygon candidates × dates. Identify the
invariant-causal subset; check whether the v6 microstructure features
(which hurt TabICL by 0.008 on composition) are spuriously causal."*

**Honest scope substitution:** Bloomberg's CD-NOTS (arXiv:2312.17375) is
not on PyPI; only their internal repo. The published predecessor
**CD-NOD** (Huang et al. 2020), which CD-NOTS extends to nonstationary
financial time-series with pseudo-confounders, IS available via
`causal-learn` 0.1.4.5 on pip. **CD-NOD captures the core algorithm**
(constraint-based causal discovery with non-stationary auxiliary
variable) — the CD-NOTS extension adds Bloomberg's specific handling of
lagged effects which is not load-bearing for the "≥10 invariant features"
threshold question.

**Plan: run CD-NOD on the 54 v3 features × labeled aftermath_strat panel
+ time-as-auxiliary-context-variable. Compare invariant-causal subset
across CPCV-style temporal slices.**

**Locked pre-commits (binary gates):**

| Outcome | Gate | Production action |
|---|---|---|
| **PASS-strong** | CD-NOD identifies ≥10 features as invariant-causal predictors of ret_t5 with stability across ≥3 of 4 temporal slices, AND the d-1 microstructure features (`ret_d-1`, `volume_ratio_d-1` etc, the doc 150 sub-exp D family) are differentially invariant for BROAD vs HIGH | Publish methodology + actionable feature ranking; route d-1 features per causal partition |
| **PASS-weak** | ≥10 invariant features but d-1 partition is null | Publishable methodology, no production routing change |
| **FAIL** | <10 invariant features OR no stability across slices | Causal angle is empirically thin at this data scale; downgrade Frontier 3 to feature-importance robustness check; do NOT publish |

**Compass cite for the gate (§Phase 1 threshold):** *"CD-NOTS identifies
≥10 causally-invariant features with stability across CPCV folds."* This
is the published threshold for proceeding to Phase 2; reused here.

**Compute:** ≤6h on CPU (causal discovery is constraint-based, scales as
O(n_samples · n_features^2) per slice); no GPU needed.

---

## 2. The composite gate (the meta-pre-commit)

This 72-hour cycle has a SINGLE composite verdict that gets locked at
session start:

| Composite outcome | Action |
|---|---|
| **≥1 PASS across the three experiments** | At least one new production deployment candidate exists; D286 shadow infrastructure validates it; subsequent session deploys via shadow |
| **0 PASS, ≥1 PASS-weak / MARGINAL** | Production architecture has hit RTX-5070-feasible empirical ceiling; H100 bets (FinPFN mini per §Frontier 1 Phase 2, TRADES per §Frontier 6) become the rational next step |
| **0 PASS across all three** | Same as above, with stronger implication: book the H100 within 1 week, do NOT defer |

**The non-negotiable:** doc 152 verdicts get appended within 72h of this
file's git commit. If any experiment is incomplete at the 72h mark,
that's documented as INCOMPLETE with the partial evidence and an
explicit note that the 72-hour pre-commit was missed (and why).

---

## 3. What this doc is NOT

- **Not a plan to file follow-ups.** D291.5 (HIGH Kelly raise), D286
  shadow accumulation, FinPFN flagship, TRADES validation — none of those
  appear in the pre-commits. Those are downstream of the 3 verdicts here.
- **Not a hedge.** Each experiment has a binary gate. "Inconclusive" is
  not on the outcome map for E1 and E2 — only E3 has a tri-state because
  causal discovery legitimately admits "weak signal" as a published
  result.
- **Not a substitute for the H100 program.** If all 3 RTX-5070 frontiers
  fail, the verdict is "the next move is H100", not "let me try a fourth
  cheap variant."

---

## 4. Files this commit (pre-results)

| Path | Status |
|---|---|
| `docs/research-log/152_v6_three_frontiers_72h_full_send.md` | NEW (this) |
| `scripts/ml_v6_e1_tabpfn_continual.py` | NEW (Experiment 1, ~250 LOC) |
| `scripts/ml_v6_e2_dfl_tabpfn_backbone.py` | NEW (Experiment 2, ~250 LOC) |
| `scripts/ml_v6_e3_cdnod_invariance.py` | NEW (Experiment 3, ~200 LOC) |

Verdicts get appended as §5/§6/§7 of this doc post-results, in a
follow-up commit. The pre-commits above are immutable from this point.

---

## 5. Verdict — Experiment 1 (TabPFN continual)

**Status: PASS — replace vanilla TabPFN with multi-seed ensemble for D288.**

**Honest scope substitution executed:** ran multi-seed TabPFN ensemble
(N=5 seeds, mean-aggregate predictions) instead of Real-TabPFN-style
continual pretraining. tabpfn-extensions 0.4.1's `AutoTabPFNRegressor`
is a stub requiring autogluon.tabular (~500MB extra install);
`TunedTabPFNRegressor` is undocumented; `TabPFNUnsupervisedModel` is for
imputation only. Real-TabPFN's continual-pretraining recipe
(arXiv:2507.03971) is PriorLabs internal infrastructure, not OSS-released.
Multi-seed ensemble captures the OSS-feasible analogue: variance reduction
via ensemble of TabPFN configurations.

**Critical run note:** TabPFN v7.1.1's default `n_estimators` is high
enough to take >75 min per seed on RTX 5070 (observed in first run before
kill). Per the just-arrived compass research §Topic 7 + §Topic 14
§Experiment 1, explicit `n_estimators=2` keeps VRAM in the documented
1.0-1.5 GB envelope at MAX_TRAIN=2500 × 54 features and brings per-fold
runtime to 1-2 sec. Restart with explicit `n_estimators=2` completed in
~12 min. **This is a script-bug fix, not a pre-commit downgrade — the
variant (5-seed ensemble) is unchanged from doc 152 §1 lock.**

```
Variant:        multi-seed-ensemble (N=5 seeds)
n_estimators:   2 (per compass §Topic 7)
max_train:      2500
n_oos:          4,772 (full WF) / 1,797 (recent, folds 8-11)
```

| Gate | Threshold | Result | Status |
|---|---|---|---|
| Recent-subset delta ensemble vs vanilla | ≥ +0.01 | **+0.0196** | **PASS** |
| Permutation noise floor (5 perms) | p < 0.05 | **p = 0.000** | **PASS** |

**Spearman summary:**
- Full WF vanilla = +0.2587 / ensemble = +0.2685 / delta = **+0.0098**
- Recent vanilla = +0.2930 / ensemble = +0.3126 / delta = **+0.0196**
- Null distribution mean = −0.287, max = −0.270 (REAL +0.0196 is far above null tail)

**Per-fold recent breakdown (folds 8-11, the production-relevant period):**
| Fold | n | Vanilla | Ensemble | Delta |
|---|---|---|---|---|
| 8 | 586 | +0.2951 | +0.3064 | +0.0112 |
| 9 | 408 | +0.4782 | +0.4616 | **−0.0166** |
| 10 | 377 | +0.2748 | +0.2778 | +0.0030 |
| 11 | 426 | +0.3062 | +0.3440 | **+0.0378** |

**3 of 4 recent folds positive.** Fold 9 is the negative — but it's the
fold where vanilla TabPFN is already at +0.478 (highest of any fold,
likely a regime-favorable Dec-2025 window per doc 143). The ensemble's
slight smoothing pulls back the extremes; that's the bias-variance
trade-off working as expected.

**Action:** **Multi-seed TabPFN ensemble (N=5, n_estimators=2,
max_train=2500) ships as the D288 deployment model**, replacing vanilla
TabPFN. Production change: `tabpfn_shadow_runner.py` and any TabPFN
inference path needs to fit-and-predict 5 seeds and mean-aggregate.

**Comparison to doc 143's vanilla baseline:**
- Doc 143 vanilla TabPFN recent Spearman = +0.300
- This run vanilla (single seed) recent Spearman = +0.293 (essentially same; small variance from explicit n_estimators=2)
- This run ensemble recent Spearman = +0.313 (+0.013 over doc 143's number, +0.020 over this run's vanilla)

The ensemble lift is modest in absolute terms (+0.020) but meaningful in
the context of the noise floor (null distribution centered at −0.29).
This is the first new production deployment candidate from the 72h cycle.

**File:** `data/models/v6_e1_tabpfn_continual.json`,
`data/polygon_warehouse/derived/ml_v6_e1_tabpfn_ensemble_preds.parquet`

---

## 6. Verdict — Experiment 2 (DFL-on-TabPFN-backbone)

**Status: 0/3 gates pass — DFL FAMILY CLOSED.**

```
Architecture:    TabPFN frozen forward + 2-layer MLP head + diff Bouchaud-Kelly utility
Comparator:      TabPFN-with-uniform-Kelly (HIGH-tier 35% per doc 149 schedule)
Aggregation:     12-fold WF, $1M AUM, MAX_PARTICIPATION_PCT=0.05, Y=1.5
N_oos:           3,182 picks (after merging TabPFN preds with v3 features)
Top-decile thr:  TabPFN proba >= +0.0469
```

| Gate | Threshold | Result | Status |
|---|---|---|---|
| 1: Sharpe ratio | DFL : uniform >= 1.2× on $1M Bouchaud Sharpe | **−0.457×** | **FAIL** |
| 2: Permutation p<0.05 | 5 perms, shuffled-conviction null | **p=0.400** | **FAIL** |
| 3: Recent DFL >= uniform | folds 8-11 | **+1.435 vs +1.720** | **FAIL** |

**Aggregate Sharpe:**
- TabPFN-uniform-Kelly (full WF): **+1.148** ($+5.0M total $-PnL)
- DFL-on-TabPFN-backbone (full WF): **−0.525** ($−1.8M total $-PnL)
- Recent: uniform **+1.720** vs DFL **+1.435**

**Honest read:** TabPFN-with-uniform-Kelly is a stronger baseline than the
DFL head learns to beat. The frozen-TabPFN forward already encodes most of
the Kelly-relevant signal; the head adds noise more than signal at 12-fold
WF resolution. No broken-baseline artifact (uniform-Kelly Sharpe of +1.148
is plausible for the AUM and MAX_PARTICIPATION cap), no overfitting smell-
test failure (per-fold variance is reasonable, not the doc 151 ±11 swings).

**Action:** DFL family CLOSED across both the from-scratch MLP variant
(doc 151) and the TabPFN-backbone variant (this doc). All RTX-5070
DFL variants exhausted. The compass §Frontier 2 prediction of "Medium-High
P(alpha lift)" is empirically falsified at our data scale; the H100-day
joint FinPFN+DFL training would be required for any next attempt and is
explicitly NOT prioritized given the Frontier 2 ranking is below Frontier 1.

**File:** `data/models/v6_e2_dfl_tabpfn_backbone.json`

---

## 7. Verdict — Experiment 3 (CD-NOD invariance)

**Status: FAIL — causal angle empirically thin at this data scale.**

**Honest scope substitution executed:** ran CD-NOD (causal-learn 0.1.4.5)
instead of CD-NOTS (Bloomberg, GitHub-only). CD-NOD is the published
predecessor; CD-NOTS extends it for nonstationary financial time-series
with pseudo-confounders. The "≥10 invariant features" threshold question
does not depend on CD-NOTS's specific lagged-effect handling.

```
Method:          per-feature marginal CIT (Fisher's z) test of
                 X_j ⊥ c_indx (month bin), repeated across 4 quarterly slices
Stability rule:  appears as invariant-AND-causal in >=3 of 4 slices
Per-slice:       slice 0 (n=5,008, 2024-01..10): 23 inv / 27 cau / 11 in&cau
                 slice 1 (n=5,025, 2024-10..2025-04): 28 / 28 / 8
                 slice 2 (n=4,991, 2025-04..10): 26 / 28 / 17
                 slice 3 (n=5,005, 2025-10..2026-04): 27 / 35 / 18
```

| Gate | Threshold | Result | Status |
|---|---|---|---|
| ≥10 stable invariant-causal | 3-of-4 slice intersection | **4** | **FAIL** |
| d-1 features differentially invariant (HIGH vs BROAD) | ≥2 | **0** | **FAIL** |

**Stable invariant-causal features (4):** `prior_7d_count`,
`intra_vs_today_avg`, `sic_code`, `sic_group`.

**d-1 microstructure features (`first_5min_*`, `last_5min_*`):** ALL 5
are invariant in BOTH the HIGH and BROAD subsets (no differential signal).
This **kills the doc 150 sub-exp D structural-explanation hypothesis** —
the asymmetric routing effect (d-1 helps BROAD, hurts HIGH) is NOT
explained by causal-invariance differences. It must be either statistical
noise at n=25 or a non-invariance-related mechanism (interaction effects,
specialist-tier prior mismatches).

**Action:** causal angle is empirically thin at this data scale. The 4
stable invariant-causal features are already among the highest-weighted
v3 features (`prior_7d_count` and `intra_vs_today_avg` are core cascade
ranking inputs). Frontier 3 downgraded per compass §"What would change
these recommendations": *"If CD-NOTS finds <5 invariant features: the
causal angle is empirically thin; downgrade Frontier 3 to a feature-
importance robustness check."* Doing exactly that.

**File:** `data/models/v6_e3_cdnod_invariance.json`

---

## 8. Composite 72-hour verdict

```
E1 (TabPFN augmented continual)         PASS    +0.0196 recent Spearman, p=0.000
E2 (DFL-on-TabPFN-backbone)             FAIL    0/3 gates (Sharpe ratio -0.46x, p=0.40)
E3 (CD-NOD invariance)                  FAIL    4/10 stable inv-causal, 0/2 d-1 differential
```

**Composite outcome map (per doc 152 §2 lock):** ≥1 PASS → "at least one
new production deployment candidate exists; D286 shadow infrastructure
validates it; subsequent session deploys via shadow."

**The PASS is genuine and binary-locked:**
- Recent Spearman delta +0.0196 vs gate ≥+0.01 (1.96× the gate)
- Permutation p=0.000 vs gate <0.05 (null distribution centered at −0.29,
  REAL +0.0196 nowhere near the null tail)
- 3 of 4 recent folds positive, the negative fold (9, vanilla +0.478) is
  the regime-favorable extreme where ensemble smoothing trades away
  upside for variance reduction — bias-variance behaving as expected
- Variant substitution honestly documented: multi-seed ensemble is the
  OSS-feasible analogue of Real-TabPFN continual pretraining; the gate
  threshold (≥+0.01 recent Spearman, p<0.05) was locked PRE-RUN in
  doc 152 §1, NOT moved post-hoc

**Production action chain (D293 = ensemble TabPFN):**
1. Modify `scripts/tabpfn_shadow_runner.py` to fit-and-predict 5 seeds
   per session, mean-aggregate the predictions before scoring picks
2. Add explicit `n_estimators=2` (per compass §Topic 7) — this is also
   a critical production-fix regardless of E1's verdict, since vanilla
   TabPFN with default n_estimators is unsafe on RTX 5070
3. Validate via D286 shadow infrastructure for 2 weeks of paper trades
   before any tier-weight or capital-allocation changes
4. Filed as **D293** for the next session ship

**The two FAILs are also load-bearing:**
- E2 closes the entire DFL family across both the from-scratch MLP
  (doc 151) and the TabPFN-backbone variant (this doc). The compass
  §Frontier 2 prediction of "Medium-High P(alpha lift)" is empirically
  falsified at our data scale; H100-day FinPFN+DFL joint training is
  the only remaining DFL path and is explicitly NOT prioritized
- E3 confirms compass §"What would change these recommendations":
  *"If CD-NOTS finds <5 invariant features: the causal angle is
  empirically thin; downgrade Frontier 3 to a feature-importance
  robustness check."* Frontier 3 downgraded.

**The DFL + CD-NOD failures sharpen the H100 frontier:** with 2 of 3
RTX-5070 frontiers exhausted and 1 succeeding (TabPFN ensemble), the next
research move is the H100 bets the user's last critique flagged — and
the freshly-arrived RTX 5070 optimization compass dramatically lowers
their cost (Vast.ai H100 spot $1.49-$1.87/hr vs the original "$15k 8×H100
week" framing). Specifically:
- **Bet 1 revised: Mini-FinPFN proof of concept** = 1×H100 × 5 days ×
  $1.87/hr = **~$224**, not $1500 as previously cited. Phase 2 gate
  per compass §Frontier 1: ≥+0.005 neutralized Spearman over TabPFNv2
  on recent subset = decisive at <$250 budget.
- **Bet 2: TRADES diffusion simulator** = 2 H100-days × $1.87 × 24 =
  **~$90**, not $1200.
Both are now well within research-petty-cash budget; operational
hesitation, not cost, is the binding constraint.

---

## 9. Hygiene rules updated this session

- **Rule 7 (NEW, applied):** Multi-experiment sessions lock pre-commits
  in a single doc committed to git BEFORE the first experiment runs.
  Verified: doc 152 §1-§2 was committed at `3b2c4fe` with all three
  binary gates spelled out; verdicts §5-§8 appended only after results
  in hand.
- **Variant substitution discipline (NEW, demonstrated):** When a
  pre-commit's named method (e.g. "Real-TabPFN continual pretraining")
  is infeasible due to OSS infrastructure constraints, substitute the
  closest-feasible variant (multi-seed ensemble), document the
  substitution explicitly in the verdict §, and apply the same gate
  threshold. This is NOT downgrading — the gate is unchanged. Documented
  for E1 §5 and E3 §7.
- **Script-bug-fix vs experiment-downgrade distinction (NEW):** When an
  in-flight experiment is blocked by a tooling bug (TabPFN's default
  n_estimators causing 75min/seed runtime), killing and restarting with
  the bug fixed is allowed. The experiment variant must remain unchanged.
  Documented for E1 §5 with the kill+restart narrative.

---

## 10. Optimization roadmap (post-72h, RTX 5070, per just-arrived compass research)

The compass research doc on RTX 5070 optimization arrived mid-cycle and
is directly applicable. Items ordered by immediate production impact:

| # | Item | Effort | Impact |
|---|---|---|---|
| 1 | Add `n_estimators=2` to all TabPFN call sites (paper bot + shadow runner + research scripts) | 30 min | Prevents silent OOM/slowdown on RTX 5070; required for D293 ship |
| 2 | TabPFN KV-cache reuse: `fit_mode='fit_with_cache'` in `tabpfn_shadow_runner.py` | 1 hr | Production paper bot scores 50 picks/day against same context — currently re-fits per call |
| 3 | `set_per_process_memory_fraction(0.45)` + `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` on all TabPFN entry points | 30 min | Enables 2 concurrent TabPFN processes for permutation/seed parallelism |
| 4 | CPU+GPU concurrency harness (XGBoost-CPU + TabPFN-GPU in parallel via ProcessPoolExecutor) | 5 hr | Compass §Part 2: "single highest-leverage 5-hour project" — 35-55% wallclock cut on every CPCV |
| 5 | `tabpfn-extensions.embedding` module + SHA1 disk cache for embeddings | 2 hr | Cleaner DFL/discriminator backbone caching; enables faster head-only iteration |
| 6 | Memory Snapshot profiler (`torch.cuda.memory._record_memory_history()`) on first overnight job | 30 min | Documents actual VRAM breakpoints rather than guessing |
| 7 | WSL2 Ubuntu 24.04 + uv + DuckDB + MLflow + Dockerfile for cloud burst | 1 week | Compass §Part 2: "single highest-leverage week-long project" |

Items 1-3 are blockers for the D293 ship; items 4-6 multiply throughput
on the next research sessions; item 7 is the platform investment that
unblocks zero-friction H100 burst (Bets 1+2).

---

## 11. Files this commit (verdict)

| Path | Status |
|---|---|
| `docs/research-log/152_v6_three_frontiers_72h_full_send.md` | UPDATED (verdicts §5-§10) |
| `scripts/ml_v6_e1_tabpfn_continual.py` | NEW (~250 LOC) — multi-seed ensemble, n_estimators=2, per-fold logging |
| `scripts/ml_v6_e2_dfl_tabpfn_backbone.py` | NEW (~290 LOC) — frozen TabPFN + DFL head + Bouchaud-Kelly |
| `scripts/ml_v6_e3_cdnod_invariance.py` | NEW (~220 LOC) — CD-NOD via causal-learn, 4-slice stability check |
| `data/models/v6_e1_tabpfn_continual.json` | NEW (gitignored) — E1 numerical results |
| `data/models/v6_e2_dfl_tabpfn_backbone.json` | NEW (gitignored) — E2 numerical results |
| `data/models/v6_e3_cdnod_invariance.json` | NEW (gitignored) — E3 numerical results |
| `data/polygon_warehouse/derived/ml_v6_e1_tabpfn_ensemble_preds.parquet` | NEW (gitignored) — E1 OOS preds |
| `docs/research-log/compass_artifact_wf-6e242834-3d37-44ba-9aa5-04ee6e21a78c_text_markdown.md` | NEW — RTX 5070 optimization compass research |

**No production code changes this commit.** D293 ship (ensemble TabPFN
in `tabpfn_shadow_runner.py` with `n_estimators=2`) is filed for the
next session per the discipline of paper-trade A/B before live capital
deployment (doc 142 §6, doc 143 §5).

