# Experiment 1A/1B — Overfit vs Regime resolution

**Date:** 2026-04-19 (Sunday) — runs Monday morning
**Routes:** v2 architecture for the next quarter. Whichever reading wins
determines whether to drop/regularize `arena_buy_verdict`, build regime
MoE, or iterate on regime featurization.
**Result destination:** `docs/research-log/09_monday_overfit_regime_results.md`

---

## Why this experiment

Yesterday's v1 retrain on 5,774 rows produced CV AUC 0.5729, a -0.1835
collapse vs v0's 0.7564 on 407 rows. The v99 diagnostic (v1 code on the
407-row slice) reproduced 0.7339, confirming the collapse is data-driven,
not code-driven. Two readings remain consistent with the data:

- **Overfit reading:** v0 was overfit to `arena_buy_verdict` on a narrow
  Dec-Jan regime where cascade strongly anti-selected (38pp stratified
  gap). v1's 0.57 is the honest broad-distribution number. v2 should
  drop or regularize the arena feature.
- **Regime reading:** `arena_buy_verdict`'s predictive power is genuinely
  regime-dependent. Both v0 and v1 are "correct" within their slices;
  v1's lower AUC is the cost of averaging across regimes without
  conditioning. v2 should add regime conditioning (MoE) and let the
  feature do its work where it's predictive.

Both readings predict downstream architecture. They are mutually
exclusive, distinguishable from data already on disk, and the answer
routes ~3 months of v2 work. This is the highest-leverage experiment
available today.

---

## Experiment 1A — Regime-stratified walk-forward CV on v1

### Method

1. **Regime bucketing.** Three options, ranked by tractability:

   **Default (option 3, no external data):** Per-date cross-sectional
   aggregate features computed from existing training rows:
     - `cs_mean_gap_pct` — mean of gap_pct across all candidates that day
     - `cs_std_dollar_volume` — std-dev of log dollar_volume that day
     - `cs_count_candidates` — number of qualifying candidates that day
     - `cs_mean_orb_range` — mean of orb_range_pct that day

   Cluster dates into 3 regime buckets via k-means on the standardized
   4-vector. Three buckets is the right granularity for ~81 trading
   days — each bucket gets ~27 days × ~70 candidates = ~1,900 rows,
   sufficient for 5-fold CV on 8 features.

   **Fallback (option 2, with VIX backfill):** Pull historical daily
   VIX from yfinance for each date in training. Cluster by VIX quartile
   × calendar quarter. Adds ~30 minutes of backfill but more
   interpretable buckets. Use this if option 3 produces uninformative
   clusters (one bucket dominates, or per-bucket AUCs are within
   ±0.02 of each other).

   **Last resort (option 1):** Calendar bucket — Dec-Jan vs Feb-Mar vs
   Apr. Coarse but simple.

2. **Per-regime CV.** For each regime cluster:
   - Filter training rows to that regime's date set.
   - Train v1 composite (same code, same 8 features) with 5-fold
     stratified CV on `close_win`.
   - Report per-fold AUC, mean ± std, plus arena_buy_verdict coefficient
     and stratified BUY/NO_TRADE win-rate gap.

3. **Repetition for variance estimation.** 3 seeded runs per regime
   (seeds 42, 17, 31). The ±0.012 single-run standard error noted in
   the prior conversation makes one-shot per-regime numbers unreliable
   at 5-pp resolution.

4. **Bootstrap CI on the per-regime AUC delta vs v1-global.** 5,000
   bootstrap resamples of the per-regime test predictions to get a
   CI on (per-regime AUC) - (global v1 AUC of 0.5729).

### Decision matrix

| Outcome | Reading | Action |
|---|---|---|
| All 3 regime AUCs within ±0.02 of 0.57 (CIs overlap) | **Overfit reading wins.** v0's signal was a narrow-slice artifact. | Drop arena_buy_verdict or shrink heavily in v2. Skip MoE; build de-overfit baseline. |
| One regime AUC ≥ 0.70, another ≤ 0.55 (CIs disjoint) | **Regime reading wins.** Feature is regime-dependent. | Build MoE: per-regime experts + soft gating. Move MoE from v2 experiment #4 to critical path. |
| Mixed: AUCs spread but CIs overlap, no clean split | **Indistinguishable with current regime definitions.** | Iterate on regime featurization (try option 2 with VIX, or k=4 clusters), do not ship v2 architecture decision yet. |

---

## Experiment 1B — `arena_buy_verdict` ablation

### Method

1. **Drop arena_buy_verdict from the feature set.** v1 trains on 7
   features instead of 8 (the other 7: gap_pct, log1p_premarket_volume,
   log1p_dollar_volume, log_price, price_to_pre_market_high_ratio,
   orb_range_pct, day_to_premarket_volume_ratio).

2. **Retrain on full 5,774 rows** with 5-fold stratified CV.

3. **3 seeded runs** (seeds 42, 17, 31).

4. **Bootstrap CI on the AUC delta** between v1-with-arena (0.5729) and
   v1-without-arena.

### Decision matrix

| Outcome | Interpretation | Action |
|---|---|---|
| v1-without-arena CV AUC ≥ 0.56 (CI overlap with 0.57) | Arena feature contributes ≤ 0.01 AUC at scale. Effectively noise. | Drop in v2. Saves cascade cost without losing predictive power. |
| v1-without-arena CV AUC 0.52-0.55 (CI disjoint from 0.57) | Arena feature carries 0.02-0.05 AUC at scale, even at compressed effect size. | Keep in v2 but surface it through MoE per 1A's regime reading; do not rely on it as the primary signal. |
| v1-without-arena CV AUC < 0.52 | Arena was carrying ~0.05+ AUC; dropping it cripples the model. | Cascade is doing real work despite anti-selection. Architecture choice becomes "how to invert + amplify" not "drop." |

---

## How 1A and 1B compose

The two experiments answer orthogonal questions:

- **1A asks:** is the model's average AUC across regimes hiding per-regime
  AUC variance?
- **1B asks:** how much of v1's AUC comes from the cascade feature
  specifically?

The four-way joint outcome:

|  | 1B: arena drop hurts | 1B: arena drop ~no effect |
|---|---|---|
| **1A: regimes diverge** | Arena is regime-dependent and dominant. Build MoE with arena per-regime; v2 critical path. | Regimes diverge but not because of arena. Some other feature is regime-dependent. Investigate which. |
| **1A: regimes converge** | Arena dominant globally. Either keep it as primary (and accept the optimism floor) or rebuild around different features. | Arena is noise globally. Drop it. Build v2 on the other 7 features + microstructure (when persistence accumulates). |

Most likely outcome (my prior): **1A regimes converge + 1B arena drop ~no
effect.** This says v0's 0.7564 was overfit, the broad signal at scale
is ~0.55-0.58, and the system needs new features (microstructure,
distillation) to push past 0.60. That's a 6-month research direction,
not a 2-week architecture change.

Second-most-likely: **1A regimes diverge + 1B arena drop hurts.** This
says regime conditioning is the lever and arena is the regime-dependent
feature. v2 critical path becomes MoE.

Other two outcomes are lower-prior but not negligible.

---

## Pre-execution checklist

Run before kicking off Monday morning:

- [ ] `git status` clean on develop
- [ ] `python -m pytest tests/ -q` all passing
- [ ] `models/composite_v0_*.pkl` present and unchanged from Apr 16
- [ ] `data/backfill/labels_shards/` has 80 shards (per audit)
- [ ] `python scripts/maybe_retrain_composite.sh --check-only` reports
      `current_rows: 5781`
- [ ] If using option 2 fallback: `python scripts/backfill_vix.py
      --start 2025-12-11 --end 2026-04-14` completes without error

If any check fails, stop and resolve before running 1A or 1B. Do not
proceed under "I'll fix that after."

---

## Execution scaffold

```
# 1A: regime-stratified walk-forward
python scripts/experiment_1a_regime_cv.py \
    --regime-method synthetic_cs \
    --n-clusters 3 \
    --seeds 42,17,31 \
    --bootstrap-n 5000 \
    --output docs/research-log/09_monday_overfit_regime_results.md

# 1B: arena_buy_verdict ablation
python scripts/experiment_1b_arena_ablation.py \
    --seeds 42,17,31 \
    --bootstrap-n 5000 \
    --append-to docs/research-log/09_monday_overfit_regime_results.md
```

Both scripts will be written Sunday night or Monday morning before
execution. Skeleton implementations need:

- `experiment_1a_regime_cv.py`: ~150 LOC. Uses
  `src.production_arena.scenarios.load_scenarios`, sklearn `KMeans`
  for regime clustering, sklearn `StratifiedKFold` for CV, np.random
  bootstrap for CI.
- `experiment_1b_arena_ablation.py`: ~80 LOC. Mostly identical to
  the existing composite training code with a feature-list parameter.

Both write a markdown table directly into the results doc — no separate
JSON intermediate. Tables compose cleanly with the 1B-appended-to-1A
flow.

---

## Decision tree (Tuesday morning)

After both experiments complete and the results doc lands:

1. **Read 1A's per-regime AUCs and per-regime arena coefficient.** Look
   for divergence, not just point estimates. CIs overlapping at zero is
   "no signal."

2. **Read 1B's AUC delta and CI.** Look for whether the CI excludes 0.

3. **Cross-reference against the four-way joint outcome table above.**
   Pick the cell that fits the data.

4. **Decision:**
   - "Overfit reading wins": Tuesday morning's work is starting on
     phantom-journal IPW (experiment #2 in the v2 ladder). Goal: get
     an honest broad-regime AUC ≥ 0.62 within 4 weeks.
   - "Regime reading wins": Tuesday morning's work is starting on
     regime-MoE prototype. Goal: per-regime AUC ≥ 0.65 mean within
     4 weeks.
   - "Indistinguishable": Tuesday is regime-feature iteration (option
     2 with VIX, k=4 clusters). Defer architecture decision by 1 week.

Do not commit to a v2 architecture change before this decision tree
runs. The temptation will be strong because the experiments take hours
and the routing question feels resolved before the data lands.

---

## What this experiment does NOT answer

- **Whether the system makes money.** AUC is necessary but not
  sufficient. A 0.65 AUC model with poor calibration on the
  high-confidence tail can still lose money.
- **Whether the cascade should die.** Even if 1B says drop the arena
  feature, the cascade may still be valuable as a feature distiller
  (per the v2 thesis). 1A/1B routes the COMPOSITE architecture, not the
  cascade architecture.
- **Whether v0 should be retired.** v0 stays in production as the
  current-best paper-trading model regardless of 1A/1B's outcome. The
  decision to retire v0 happens only when v2 has demonstrated live
  PnL > v0's, not when its CV AUC is higher.
