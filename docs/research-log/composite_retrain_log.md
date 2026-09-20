
## 2026-04-17T22:24:14Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:25:13Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:25:13Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:25:14Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:25:53Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:25:54Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:25:54Z
- current_rows: 533 (base=497 + shards=36)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 533/407*1.5 rows)

## 2026-04-17T22:34:41Z
- current_rows: 536 (base=497 + shards=39)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 536/407*1.5 rows)

## 2026-04-17T22:34:41Z
- current_rows: 536 (base=497 + shards=39)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 536/407*1.5 rows)

## 2026-04-17T22:34:41Z
- current_rows: 536 (base=497 + shards=39)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 536/407*1.5 rows)

## 2026-04-18T16:40:30Z
- current_rows: 5781 (base=497 + shards=5284)
- last_train_rows: 407
- last_train_age_days: 1
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5781))

## 2026-04-18T16:43:34Z
- current_rows: 5781 (base=497 + shards=5284)
- last_train_rows: 407
- last_train_age_days: 1
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5781))

## 2026-04-18T16:55:13Z
- current_rows: 5781 (base=497 + shards=5284)
- last_train_rows: 407
- last_train_age_days: 1
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5781))

## 2026-04-18T16:55:25Z
- current_rows: 5781 (base=497 + shards=5284)
- last_train_rows: 407
- last_train_age_days: 1
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5781))

---

## 2026-04-18 ~12:55 EDT — v1 retrain attempted, REFUSED by stability gate

**Loader fix validated.** After modifying `src/production_arena/scenarios.py`
to union `features_labeled.jsonl` (base) with `data/backfill/labels_shards/`
under a base-wins dedup policy, `load_scenarios` reports:

```
5774 kept | labels: base=497 shards=5278 conflicts=6 |
skipped: date=0 ticker=0 no_bars=13 no_label=75 malformed=0
```

Candidate accounting: 5,774 + 13 + 75 = 5,862 (matches candidates.jsonl total).
Six conflicts went base-wins (shard rows discarded, canonical preserved).
v0's 497 training rows are untouched in v1's input — the v0↔v1 comparison
is honest.

**Training completed, stability gate fired, no artifacts written.**

```
FULL:     train_auc=0.5795  cv_auc=0.5729  overfit_gap=0.0066
PRESCORE: train_auc=0.5595  cv_auc=0.5544  overfit_gap=0.0051
STABILITY WARNING: new CV AUC 0.5729 differs from prior v0 CV AUC 0.7564
                   by -0.1835 (threshold ±0.05)
refusing to overwrite production model. Pass --i-know-what-im-doing to proceed.
```

Gap = -0.1835 is ~3.7× the ±0.05 stability threshold and ~1.8× the 0.10
"stop and investigate" threshold agreed with opus-1M in today's retrain-plan
discussion. **Override was NOT applied.** `composite_v0_*` artifacts on disk
remain from 2026-04-16 20:35 (unchanged).

**Explicable hypothesis (not yet verified — diagnostic pending):** v1's CV
AUC collapse is driven by compression of the `arena_buy_verdict` feature's
predictive power. v0's stratified win-rate gap was NO_TRADE 60.2% vs BUY
22.2% = +38.0pp. v1's is NO_TRADE 46.9% vs BUY 37.0% = +9.9pp — ~26% of
v0's separation. `arena_buy_verdict` had the largest absolute importance
in v0 (coefficient -0.86, ~4× the next largest), and v0's own metadata
documents: *"AUC is ARENA-VALIDATED — synthesized agent signals make this
optimistic vs live."* v0 was correct for its narrow-window slice where
cascade strongly anti-selected; on the 14× larger, more regime-diverse
dataset, the anti-selection signal is still present but much weaker.
The collapse is plausibly data-distribution, not code drift.

**Diagnostic to confirm (next step, not yet run):** retrain v1 code on v0's
exact 407-row slice. Expected outcome if hypothesis is correct: CV AUC
reproduces near 0.76, confirming the code didn't change the math. If CV
AUC drops to ~0.57 on the narrow slice too, the hypothesis is wrong and
the code has a regression we haven't spotted.

**Production status:** v0 remains the live composite model. No action on
live trading. Decision on v1 deferred until the diagnostic runs and the
result is in front of the decision-maker.

---

## 2026-04-18 ~13:03 EDT — Diagnostic v99 (v1 code on 407-row regime)

**Mechanism.** `data/backfill/labels_shards/` renamed to `.diag_bak/` so
`load_scenarios` falls through to base-only via the existing `.exists()`
check (no code change). Trained with:

```
python -m src.composite.train \
    --feature-stability-check --i-know-what-im-doing \
    --output-version v99 \
    --output-dir models/diagnostics \
    --min-rows 0
```

`--min-rows 0` is required because v0's 407-row training pre-dates the
1000-row guard added in D221; the diagnostic accepts the same noise level
v0 accepted, which is the point of a baseline reproduction. Loader
self-confirmed the rename worked: `load_scenarios: 497 kept | base=497
shards=0 conflicts=0`.

**Artifacts.** `models/diagnostics/composite_v99_*.pkl` and
`composite_v99_metadata.json`. **v99 is a diagnostic artifact, NOT a
production candidate. Do not promote.** The shell script's glob
(`models/composite_v*_metadata.json`) does not recurse into subdirectories,
so v99 will not be picked up as "latest" by future retrain runs.

**Headline numbers (CV AUC):**

| Model    | v0 (407) | v99 diag (497, v1 code) | Δ from v0 | v1 (5,774) | Δ from v0 |
|----------|----------|-------------------------|-----------|------------|-----------|
| FULL     | 0.7564   | **0.7339**              | -0.0225   | 0.5729     | -0.1835   |
| PRESCORE | 0.6612   | **0.6406**              | -0.0206   | 0.5544     | -0.1068   |

v99 reproduces v0's AUC within 0.023 — well inside the ±0.05 stability
band that would have fired a warning. v1's collapse of -0.18 against v0
is **not** code drift.

**Per-fold AUCs (FULL model):**

```
v0:  [0.8007, 0.7313, 0.7883, 0.8031, 0.6586]   mean=0.7564 std=0.0619
v99: [0.7735, 0.7946, 0.6843, 0.7000, 0.7172]   mean=0.7339 std=~0.045
```

Different fold splits hit different rows (the +90 EST-fix rows reshuffle
CV folds), but the AUC distribution is in the same range and v99 is
slightly tighter (lower std), consistent with mildly more data.

**Feature importance comparison (FULL model coefficients):**

| feature                            | v0       | v99      | abs_drift |
|------------------------------------|----------|----------|-----------|
| arena_buy_verdict                  | -0.8586  | -0.7606  | 0.0981    |
| log1p_dollar_volume                | +0.6714  | +0.6850  | 0.0136    |
| log1p_premarket_volume             | -0.3581  | -0.4385  | 0.0803    |
| orb_range_pct                      | -0.2590  | -0.2863  | 0.0272    |
| day_to_premarket_volume_ratio      | +0.2518  | +0.0782  | 0.1737    |
| log_price                          | -0.1632  | -0.1808  | 0.0176    |
| gap_pct                            | -0.1663  | -0.0078  | 0.1585    |
| price_to_pre_market_high_ratio     | +0.1040  | +0.1244  | 0.0203    |

Feature set is identical (same 8 features in same order). The two dominant
features — `arena_buy_verdict` (largest absolute, both negative) and
`log1p_dollar_volume` (second-largest, both positive) — held their sign
and approximate magnitude. arena_buy_verdict drifted by 11% (-0.86 →
-0.76), well within noise for a 90-row data delta. The two features that
drifted noticeably (`day_to_premarket_volume_ratio` and `gap_pct`) were
small in v0 to begin with; small coefficients are noisy at small N.

**Verdict — hypothesis CONFIRMED.** v1's CV AUC collapse (-0.1835) is
data-driven, not code-driven. The feature engineering, training pipeline,
and dependency stack produce the same model on the same regime.

**Interpretation — TWO competing readings, both consistent with the data:**

1. **Overfit reading.** v0 was overfit to `arena_buy_verdict` on a narrow
   regime (Dec-Jan EDT-EST transition) where cascade strongly anti-selected
   winners (38pp gap). On the broader v1 distribution, the feature's "real"
   predictive power surfaces as 9.9pp gap, and v1's 0.57 AUC is the honest
   number. Implication: v2 should drop, regularize, or shrink
   arena_buy_verdict.

2. **Regime reading.** `arena_buy_verdict`'s predictive power is genuinely
   regime-dependent. In some regimes (the v0 slice) cascade is strongly
   anti-selective; in other regimes (parts of Feb-April) it is weakly so
   or even slightly correlated. Both v0 and v1 are "correct" within their
   distributions; v1's AUC is lower because it averages across regimes
   without conditioning. Implication: v2 should add a regime indicator
   and let arena_buy_verdict do its work where it's predictive.

These have **different downstream implications.** Do not lock in either
reading tonight. The data we have supports both equally; distinguishing
them requires either (a) walk-forward analysis stratified by regime, or
(b) a v2 experiment that drops arena_buy_verdict and measures
non-arena-feature predictive power on the 5,774-row dataset. Both are
Monday's work, not Saturday's.

**Production status (UNCHANGED).** v0 remains live. No retrain promoted
today. The stability gate did its job; the loader fix is shipped; the
collapse is now understood as data-driven; v99 diagnostic artifacts are
isolated under `models/diagnostics/` and tagged "do not promote." Saturday
is done.

## 2026-04-18T17:08:13Z
- current_rows: 5781 (base=497 + shards=5284)
- last_train_rows: 407
- last_train_age_days: 1
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5781))

## 2026-04-22T10:37:07Z
- current_rows: 5784 (base=497 + shards=5287)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5784))

## 2026-04-22T10:37:10Z
- current_rows: 5784 (base=497 + shards=5287)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5784))

## 2026-04-22T10:37:13Z
- current_rows: 5784 (base=497 + shards=5287)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5784))

## 2026-04-22T10:43:35Z
- current_rows: 5785 (base=497 + shards=5288)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5785))

## 2026-04-22T10:43:37Z
- current_rows: 5785 (base=497 + shards=5288)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5785))

## 2026-04-22T10:43:40Z
- current_rows: 5785 (base=497 + shards=5288)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5785))

## 2026-04-22T10:48:54Z
- current_rows: 5786 (base=497 + shards=5289)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5786))

## 2026-04-22T10:48:57Z
- current_rows: 5786 (base=497 + shards=5289)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5786))

## 2026-04-22T10:49:00Z
- current_rows: 5786 (base=497 + shards=5289)
- last_train_rows: 407
- last_train_age_days: 5
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5786))

## 2026-04-26T02:00:44Z
- current_rows: 5787 (base=497 + shards=5290)
- last_train_rows: 407
- last_train_age_days: 9
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5787))

## 2026-04-26T02:00:46Z
- current_rows: 5787 (base=497 + shards=5290)
- last_train_rows: 407
- last_train_age_days: 9
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5787))

## 2026-04-26T02:00:48Z
- current_rows: 5787 (base=497 + shards=5290)
- last_train_rows: 407
- last_train_age_days: 9
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5787))

## 2026-04-30T01:33:41Z
- current_rows: 5788 (base=497 + shards=5291)
- last_train_rows: 407
- last_train_age_days: 13
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5788))

## 2026-04-30T01:33:44Z
- current_rows: 5788 (base=497 + shards=5291)
- last_train_rows: 407
- last_train_age_days: 13
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5788))

## 2026-04-30T01:33:46Z
- current_rows: 5788 (base=497 + shards=5291)
- last_train_rows: 407
- last_train_age_days: 13
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5788))

## 2026-04-30T01:48:22Z
- current_rows: 5789 (base=497 + shards=5292)
- last_train_rows: 407
- last_train_age_days: 13
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5789))

## 2026-04-30T01:48:25Z
- current_rows: 5789 (base=497 + shards=5292)
- last_train_rows: 407
- last_train_age_days: 13
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5789))

## 2026-04-30T01:48:27Z
- current_rows: 5789 (base=497 + shards=5292)
- last_train_rows: 407
- last_train_age_days: 13
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5789))

## 2026-05-05T02:10:14Z
- current_rows: 5790 (base=497 + shards=5293)
- last_train_rows: 407
- last_train_age_days: 2
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5790))

## 2026-05-05T02:10:16Z
- current_rows: 5790 (base=497 + shards=5293)
- last_train_rows: 407
- last_train_age_days: 2
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5790))

## 2026-05-05T02:10:19Z
- current_rows: 5790 (base=497 + shards=5293)
- last_train_rows: 407
- last_train_age_days: 2
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5790))

## 2026-05-05T22:17:04Z
- current_rows: 5791 (base=497 + shards=5294)
- last_train_rows: 407
- last_train_age_days: 3
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5791))

## 2026-05-05T22:17:07Z
- current_rows: 5791 (base=497 + shards=5294)
- last_train_rows: 407
- last_train_age_days: 3
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5791))

## 2026-05-05T22:17:10Z
- current_rows: 5791 (base=497 + shards=5294)
- last_train_rows: 407
- last_train_age_days: 3
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5791))

## 2026-05-29T14:07:55Z
- current_rows: 5797 (base=497 + shards=5300)
- last_train_rows: 407
- last_train_age_days: 26
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5797))

## 2026-05-29T14:07:57Z
- current_rows: 5797 (base=497 + shards=5300)
- last_train_rows: 407
- last_train_age_days: 26
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5797))

## 2026-05-29T14:08:00Z
- current_rows: 5797 (base=497 + shards=5300)
- last_train_rows: 407
- last_train_age_days: 26
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5797))

## 2026-06-02T03:06:14Z
- current_rows: 5798 (base=497 + shards=5301)
- last_train_rows: 407
- last_train_age_days: 30
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5798))

## 2026-06-02T03:06:17Z
- current_rows: 5798 (base=497 + shards=5301)
- last_train_rows: 407
- last_train_age_days: 30
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5798))

## 2026-06-02T03:06:20Z
- current_rows: 5798 (base=497 + shards=5301)
- last_train_rows: 407
- last_train_age_days: 30
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5798))

## 2026-06-10T02:27:39Z
- current_rows: 5799 (base=497 + shards=5302)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5799))

## 2026-06-10T02:27:41Z
- current_rows: 5799 (base=497 + shards=5302)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5799))

## 2026-06-10T02:27:44Z
- current_rows: 5799 (base=497 + shards=5302)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5799))

## 2026-06-10T12:25:19Z
- current_rows: 5800 (base=497 + shards=5303)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5800))

## 2026-06-10T12:25:22Z
- current_rows: 5800 (base=497 + shards=5303)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5800))

## 2026-06-10T12:25:25Z
- current_rows: 5800 (base=497 + shards=5303)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5800))

## 2026-06-10T12:38:07Z
- current_rows: 5801 (base=497 + shards=5304)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5801))

## 2026-06-10T12:38:10Z
- current_rows: 5801 (base=497 + shards=5304)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5801))

## 2026-06-10T12:38:13Z
- current_rows: 5801 (base=497 + shards=5304)
- last_train_rows: 407
- last_train_age_days: 38
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5801))

## 2026-07-05T19:06:47Z
- current_rows: 5802 (base=497 + shards=5305)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5802))

## 2026-07-05T19:06:50Z
- current_rows: 5802 (base=497 + shards=5305)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5802))

## 2026-07-05T19:06:53Z
- current_rows: 5802 (base=497 + shards=5305)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5802))

## 2026-07-05T19:19:12Z
- current_rows: 5803 (base=497 + shards=5306)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5803))

## 2026-07-05T19:19:14Z
- current_rows: 5803 (base=497 + shards=5306)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5803))

## 2026-07-05T19:19:17Z
- current_rows: 5803 (base=497 + shards=5306)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5803))

## 2026-07-06T17:54:27Z
- current_rows: 5804 (base=497 + shards=5307)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5804))

## 2026-07-06T17:54:30Z
- current_rows: 5804 (base=497 + shards=5307)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5804))

## 2026-07-06T17:54:32Z
- current_rows: 5804 (base=497 + shards=5307)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5804))

## 2026-07-06T18:07:04Z
- current_rows: 5805 (base=497 + shards=5308)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5805))

## 2026-07-06T18:07:06Z
- current_rows: 5805 (base=497 + shards=5308)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5805))

## 2026-07-06T18:07:09Z
- current_rows: 5805 (base=497 + shards=5308)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5805))

## 2026-07-06T18:17:44Z
- current_rows: 5806 (base=497 + shards=5309)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5806))

## 2026-07-06T18:17:47Z
- current_rows: 5806 (base=497 + shards=5309)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5806))

## 2026-07-06T18:17:49Z
- current_rows: 5806 (base=497 + shards=5309)
- last_train_rows: 407
- last_train_age_days: 64
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5806))

## 2026-09-15T23:04:40Z
- current_rows: 5807 (base=497 + shards=5310)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5807))

## 2026-09-15T23:04:42Z
- current_rows: 5807 (base=497 + shards=5310)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5807))

## 2026-09-15T23:04:44Z
- current_rows: 5807 (base=497 + shards=5310)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5807))

## 2026-09-15T23:14:12Z
- current_rows: 5808 (base=497 + shards=5311)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5808))

## 2026-09-15T23:14:15Z
- current_rows: 5808 (base=497 + shards=5311)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5808))

## 2026-09-15T23:14:17Z
- current_rows: 5808 (base=497 + shards=5311)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5808))

## 2026-09-15T23:26:50Z
- current_rows: 5809 (base=497 + shards=5312)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5809))

## 2026-09-15T23:26:52Z
- current_rows: 5809 (base=497 + shards=5312)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5809))

## 2026-09-15T23:26:54Z
- current_rows: 5809 (base=497 + shards=5312)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5809))

## 2026-09-15T23:45:00Z
- current_rows: 5812 (base=497 + shards=5315)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5812))

## 2026-09-15T23:45:02Z
- current_rows: 5812 (base=497 + shards=5315)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5812))

## 2026-09-15T23:45:04Z
- current_rows: 5812 (base=497 + shards=5315)
- last_train_rows: 407
- last_train_age_days: 136
- latest_meta: models/composite_v0_metadata.json
- decision: **RETRAIN** (row count grew >50% (last: 407, current: 5812))

## 2026-09-16T00:14:06Z
- current_rows: 1 (base=0 + shards=1)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 1/407*1.5 rows)

## 2026-09-16T00:14:06Z
- current_rows: 1 (base=0 + shards=1)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 1/407*1.5 rows)

## 2026-09-16T00:14:07Z
- current_rows: 1 (base=0 + shards=1)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 1/407*1.5 rows)

## 2026-09-16T00:19:07Z
- current_rows: 2 (base=0 + shards=2)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 2/407*1.5 rows)

## 2026-09-16T00:19:07Z
- current_rows: 2 (base=0 + shards=2)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 2/407*1.5 rows)

## 2026-09-16T00:19:08Z
- current_rows: 2 (base=0 + shards=2)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 2/407*1.5 rows)

## 2026-09-16T00:41:46Z
- current_rows: 3 (base=0 + shards=3)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 3/407*1.5 rows)

## 2026-09-16T00:41:47Z
- current_rows: 3 (base=0 + shards=3)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 3/407*1.5 rows)

## 2026-09-16T00:41:47Z
- current_rows: 3 (base=0 + shards=3)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 3/407*1.5 rows)

## 2026-09-16T00:51:47Z
- current_rows: 5 (base=0 + shards=5)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 5/407*1.5 rows)

## 2026-09-16T00:51:47Z
- current_rows: 5 (base=0 + shards=5)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 5/407*1.5 rows)

## 2026-09-16T00:51:47Z
- current_rows: 5 (base=0 + shards=5)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 5/407*1.5 rows)

## 2026-09-16T00:52:32Z
- current_rows: 5 (base=0 + shards=5)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 5/407*1.5 rows)

## 2026-09-16T00:52:32Z
- current_rows: 5 (base=0 + shards=5)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 5/407*1.5 rows)

## 2026-09-16T00:52:32Z
- current_rows: 5 (base=0 + shards=5)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 5/407*1.5 rows)

## 2026-09-16T01:01:03Z
- current_rows: 6 (base=0 + shards=6)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 6/407*1.5 rows)

## 2026-09-16T01:01:04Z
- current_rows: 6 (base=0 + shards=6)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 6/407*1.5 rows)

## 2026-09-16T01:01:04Z
- current_rows: 6 (base=0 + shards=6)
- last_train_rows: 407
- last_train_age_days: 0
- latest_meta: models/composite_v0_metadata.json
- decision: skip (no triggers met; 0d old, 6/407*1.5 rows)
