# 117 — v4 retrain HOLDS v3: sparse features dilute signal

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [116 parallel news + v4 retrain-and-compare pipeline](116_news_parallel_v4_compare_pipeline.md)

---

## TL;DR — major negative finding (with verdict pipeline working as designed)

1. **trades_v1 Jan-Feb conversion completed** (12 days, 12k partitions
   each). Combined with s113's 9 April days, microstructure features
   rebuilt: **543 (ticker, d0) rows = 2.7% coverage** (up from 191).

2. **News backfill v2 nearly complete**: 4,000 / 4,080 keys (98%) at
   2.4/s. **187 with news (4.7% coverage)** — consistent with literature.

3. **🚨 v4 retrain (v3 + microstructure + news) HOLDS v3** with **-$4,232
   regression** ($+8,050 vs v3 $+12,283 = -34%). The retrain-and-compare
   verdict pipeline correctly rejected the model.

4. **Lesson: sparse features dilute signal.** Adding 23 new features
   (11 microstructure + 12 news) where 95-97% of rows have NaN/0 hurts the
   tree-ensemble more than the small signal in the 3-5% with-data subset
   helps. This is a known pathology — sparse-feature-with-indicator
   patterns need MUCH higher coverage to be useful.

5. **Production stays on v3-tuned-16fold for Monday deploy.** All
   $122.83% bankroll WF expectations remain valid.

---

## 1. The v4 retrain numbers

### Full v3-tuned-16fold vs v4 comparison (16-fold WF, $10k bankroll, Aggressive Kelly)

| Tier | v3 n | v3 avg | v3 $ | v4 n | v4 avg | v4 $ | Δ $ |
|---|---|---|---|---|---|---|---|
| ELITE | 7 | +58.79% | +$176 | 6 | +24.69% | +$106 | **-$70** |
| HIGH | 27 | +23.45% | +$680 | 26 | +30.24% | +$461 | **-$219** |
| VETOED | 87 | +5.36% | +$2,190 | 72 | +7.28% | +$1,277 | **-$913** |
| BROAD | 367 | +8.54% | +$9,237 | 350 | +4.30% | +$6,206 | **-$3,031** |
| **TOTAL** | | | **+$12,283** | | | **+$8,050** | **-$4,232** |

### Per-tier raw stats from v4 ensemble run

| Tier | n | avg | win% | Sharpe |
|---|---|---|---|---|
| v4 P≥0.30 | 779 | +5.06% | 46.0% | 2.04 |
| v4 P≥0.50 | 56 | +21.37% | 62.5% | 2.20 |
| v4 P≥0.60 | 9 | **+23.93%** ← vs v3's +47.53% | **77.8%** | 1.60 |

The P≥0.60 ELITE-equivalent dropped from +47.53% / 90% win → +23.93% / 78% win.

### Why v4 lost

Hypothesis: of 543 microstructure rows, only 2.7% of training examples have
non-NaN features. Of 4,000 news rows, only ~187 have any articles (4.7%).
The trees end up using the `has_microstructure` and `has_news` indicators
as strong split features, which:

- Encodes a regime/recency proxy (recent rows have data, old rows don't)
- Causes the model to learn "this is recent → behave differently" rather
  than "this signal is informative"
- Splits the existing well-calibrated v3 leaves into noisier sub-leaves

Confirmed by per-fold breakdown — v4 fold 9 (Oct 2025) is -5.00% (similar
to v3) but fold 14 (Mar 2026) is +17.25% which is HIGHER than v3 — the
model learned regime patterns that don't generalize.

### Why this isn't a feature-engineering bug

The features themselves are correctly extracted (real dark_pool_pct,
real news sentiment from Polygon Insights). The issue is statistical:
**a feature that is 0/NaN for 95% of rows is mostly an indicator of
absence, not a useful signal.**

---

## 2. Path forward (when to revisit v4)

The retrain-and-compare verdict pipeline did its job — caught a regression
that could have shipped silently. Two paths to actually improve via v4:

### Path A: Wait for full data
- Complete trades_v1 download (240 GB total, currently at ~110 GB / 46%)
- Convert all to parquet
- Build microstructure for full ~89 days = ~5,000 rows = ~25% coverage
- Re-run v4 retrain — at 25%+ coverage, indicator features become less
  dominant and signal can emerge

### Path B: Smarter feature engineering
- Train v4 on ONLY the rows where `has_microstructure=1` (forces model
  to use microstructure features instead of using the indicator)
- Use the v4-restricted predictions for those rows; fall back to v3 for
  the rest
- This is closer to the "expert systems" pattern: route based on data
  availability, not learn the routing

### Path C: Drop sparse features entirely
- Stick with v3-tuned-16fold (current production)
- Microstructure / news features are kept for SLICING analysis (correlate
  with P&L outcomes) but not as model inputs

For Monday: **Path C** by default (keep v3). Decide between A/B over
next week as more data lands.

---

## 3. Files shipped this session

| Path | Status | Note |
|---|---|---|
| `data/polygon_warehouse/trades_v1_parquet/year=2026/month=01/` | NEW (gitignored) | 5 days × 12k partitions |
| `data/polygon_warehouse/trades_v1_parquet/year=2026/month=02/` | NEW (gitignored) | 7 days × 12k partitions |
| `data/polygon_warehouse/derived/microstructure_features.parquet` | rebuilt (gitignored) | 543 rows |
| `data/polygon_warehouse/derived/news_features_polygon_180d.parquet` | growing (gitignored) | 4,000 rows so far |
| `data/polygon_warehouse/derived/ml_v2_walkforward_predictions_v4.parquet` | NEW (gitignored) | 12k OOS preds |
| `data/models/continuer_v2_v4.pkl` | NEW (gitignored) | not promoted |
| `data/models/v4_lift_summary.json` | NEW (gitignored) | HOLD v3 verdict |
| `docs/research-log/117_v4_holdback_sparse_features_dilute.md` | NEW (this doc) | this |

No code changes — only data/model artifacts + this doc.

---

## 4. Validated edge stack (post-117, unchanged)

```
Meta-scorer 16-fold AGGRESSIVE       +122.83% bankroll WF (~92% APY)
  Sharpe 3.15, Calmar 31.24, max DD -2.81%
  TCN signal DEBUNKED (s114), VETOED-D candidate (s115): +13.37%/trade

v4 retrain attempt: HOLD v3 (sparse features dilute, -$4,232 regression).
  Pipeline did its job: caught the regression before promotion.

Production deploy Monday: v3-tuned-16fold (UNCHANGED).
```

---

## 5. Background jobs status (commit time)

- trades_v1 80-day download: ~110 GB / 240 GB (~46%, ETA 4-5 hr)
- News v2 backfill: ~4,000 / 4,080 (98%, ETA 5 min)
- trades_v1 Jan-Feb conversion: DONE (12 days complete)

---

## 6. The bigger lesson

Two consecutive negative findings (s114 TCN debunk + s117 v4 holdback)
**validate the rigor verifier discipline**. Both could have been silently
shipped to production without the validation infrastructure:

- s114: TCN was technically a "trained model" with 5-epoch loss curves
  showing convergence — but AUC = 0.49 on the held-out predictions
- s117: v4 had MORE features and MORE data — naive intuition says "more
  is better" — but the retrain-and-compare verdict caught the regression

**The infrastructure is the moat.** Rigor + walk-forward + verdict
gates are what allow a 1-developer team to compound improvements
without silently degrading.

---

## 7. Next-session priorities

1. **Monday: paper-deploy + monitor** with v3-tuned-16fold + aggressive Kelly.
2. **Wait for trades_v1 240GB to complete** (~4-5 hr remaining).
3. **Re-attempt v4 with full data** (25%+ microstructure coverage).
4. **Try Path B (model-routing)** if v4 fails again at higher coverage.
5. **Strip TCN + swap VETOED to rule D** (per s115) post-stable Monday.
