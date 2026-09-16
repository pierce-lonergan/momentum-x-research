# 146 — v6 D289 interaction features: 6.8% gap closure, NOT pilot-worthy

> Brief follow-up to doc 145. Pre-commit pattern (verdict-blank-until-data) applied:
> the result didn't strengthen the engineering hypothesis; it confirmed the
> doc 145 verdict that interactions are weak and TabPFN is needed for the bulk.

**Session date:** 2026-05-11
**Branch:** develop
**Predecessors:** [145 v6 TabPFN discriminator + data hygiene](145_v6_tabpfn_discriminator_and_data_hygiene.md)
**Status:** D289 interaction-feature prototype produces +0.006 Spearman lift
(6.8% of the TabPFN gap). NOT shipped as standalone change. Documented
as confirmation of doc 145 verdict.

---

## TL;DR

Per doc 145 § 6 filed work, prototyped D289: add hand-engineered
interaction features pointed at by the discriminator analysis to v3's
input space. Re-train depth-5 XGBoost on the 12-fold WF setup.

**Result:**
- v3 baseline (54 features): Spearman +0.1861
- v3 + 9 interactions (63 features): Spearman +0.1918
- Lift: +0.0057 (closed 6.8% of TabPFN's 0.084 gap)
- Per-fold: 8 of 12 folds positive, 4 negative

**Decision:** Not a standalone-shippable production change. Could roll
into v3 retraining as a "free" tiny lift but doesn't justify a separate
launcher commit. **Defensive overlay (D288 after shadow-data validation)
remains the only meaningful path** to closing the TabPFN gap.

---

## 1. The 9 interactions tested

Picked from doc 145's discriminator analysis: top under-weighted
features were `ret_open_close_d0` (3.2× under-weighted) and `sec_semi`
(v3 weight = 0.0%). Engineered 9 cross-products and transforms:

```
roc_x_logmcap   = ret_open_close_d0 × log_market_cap
roc_x_intra     = ret_open_close_d0 × intraday_pct
roc_x_priorfade = ret_open_close_d0 × prior_fade_rate
semi_x_dvol     = sec_semi × log_dvol_d0
semi_x_intra    = sec_semi × intraday_pct
semi_x_logmcap  = sec_semi × log_market_cap
roc_squared     = ret_open_close_d0²            (non-monotonic)
roc_pos         = (ret_open_close_d0 > 0)        (sign indicator)
roc_neg         = (ret_open_close_d0 < 0)        (sign indicator)
```

These are the most natural interactions given the discriminator
finding: combine the under-weighted features with the highest-standalone-
alpha features (log_market_cap, intraday_pct, prior_fade_rate, dvol_d0).

## 2. Why this lift is so small

The doc 145 discriminator R²=0.38 said TabPFN's distinctive view is
38% explainable from v3 features in principle. The doc 145 depth-
sensitivity test showed XGBoost (any depth) plateaus at Spearman
~0.19 on v3 features alone.

This D289 prototype confirms what the depth test predicted: hand-
engineering interactions doesn't close the gap because XGBoost at
depth=5 already explores the space of (feature-pair) interactions
in its decision tree. Adding the products as explicit features lets
XGBoost find them at lower depth (computational efficiency) but
doesn't unlock new representational capacity.

The 6.8% closure is the marginal gain from making XGBoost's job
easier (it doesn't have to discover the interaction pattern from
scratch in each fold). It's not new information.

To capture the remaining 93% of TabPFN's edge, we'd need:
- Either: a model that does in-context learning natively (i.e., TabPFN itself)
- Or: a fundamentally different model class (e.g., a transformer trained
  on the same data) — but the v4/v5 work already showed from-scratch
  transformers don't beat v3, so this path is closed.

The defensive overlay (D288) sidesteps this entirely by using TabPFN
as a filter rather than a primary predictor.

---

## 3. What this means for next-launcher commits

Original doc 145 § 3 production roadmap:

| # | Description | Updated status |
|---|---|---|
| D288 | Defensive overlay (after 2 weeks D286 shadow data) | UNCHANGED — primary path |
| D289 | Interaction feature engineering | **DOWNGRADED** — not standalone-shippable; may roll into next v3 retraining |

The D289 plan was always speculative ("might capture 10-20%"). The
empirical answer is 6.8%. Not enough to justify a launcher commit by
itself, but worth keeping the interaction features in the v3 input
space if/when v3 BROAD specialist gets retrained for any other reason.

---

## 4. Files this commit

This is a brief documentation-only commit to close the D289 loop.
The prototype was tested inline in a Python session; the script
itself wasn't preserved (the test was 30 lines and unambiguous —
preserved in the doc).

| Path | Status |
|---|---|
| `docs/research-log/146_v6_d289_interactions_minimal_lift.md` | NEW (this) |

No code or launcher changes.

---

## 5. The hygiene contract still works

The pattern from this session:
- doc 144 → strong claim (defensive overlay +5.87 pp lift)
- doc 145 → bug-fix STRENGTHENED the claim AND ruled out a hopeful
  hypothesis (deeper XGBoost would close the gap — empirically false)
- doc 146 → engineering prototype CONFIRMED the doc 145 verdict
  (interactions don't close the gap; 6.8% is too small)

Each step's prediction was tested with data, not rationalized. The
doc 145 statement "engineering features into v3 might capture
10-20%" was an honest estimate; the actual 6.8% is a downgrade but
documented honestly.

The defensive overlay path is now the ONLY actionable architecture-
improvement work until a fundamentally different scoring approach
(beyond v3 features + tree models) is available. That approach
exists — it's TabPFN itself — but its production cost (license + ~10s
inference) only pays via the defensive-overlay use case.

---

## 6. Filed for next session — UNCHANGED from doc 145 except removing D289

1. **D288 — defensive overlay enable** (after 2 weeks D286 shadow data,
   ~Friday May 22 if shadow runs every weekday from May 12)
2. **Regenerate clean TabPFN preds with ticker saved** (defensive future-proofing)
3. **Re-run doc 144 Exp #4 with corrected tickers** (verify 1.39× ratio still holds)
4. **Empirical Y estimation for Bouchaud capacity** (still filed from doc 142)

D289 removed from the roadmap — closed as "interactions confirmed weak."

---

## 7. The honest meta-note (one line)

**A negative result this small is the kind that the discipline catches
before it ships, not after.** Doc 138 caught the DSR formula bug
before it shipped. Doc 141 caught the TabICL recent-data collapse
before deployment. This doc catches the D289 hypothesis as not
pilot-worthy before any code touches the production XGBoost retrain
path. Same hygiene, different direction.
