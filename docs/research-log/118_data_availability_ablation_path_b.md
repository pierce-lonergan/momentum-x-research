# 118 — Data-availability ablation + Path B v4 plan

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [117 v4 retrain HOLDS v3 - sparse features dilute](117_v4_holdback_sparse_features_dilute.md)

---

## TL;DR — major finding: v3 LOSES on the rows microstructure covers

1. **trades_v1 80-day download DONE** (64 trading days, 185 GB raw across
   Jan 26 → April 24). Conversion of remaining 43 days running in
   background.

2. **🚨 Data-availability ablation: v3 underperforms by -7.80pp on
   microstructure-1 rows.**
   - Baseline (all 12,192 OOS rows, P≥0.30): **+6.18%** on 839 picks
   - Microstructure-1 slice (recent regime): **-1.62%** on 30 picks
   - **Microstructure-0 slice (older data)**: **+6.47%** on 809 picks
   - The 21 days that have microstructure data (Jan 26 - Feb 10 +
     April 15-29) are a HARDER REGIME for v3, exactly where extra
     features could help.

3. **Path B (data-routed v4) is the right design.** Train v4 ONLY on
   `has_microstructure=1` rows; at inference, route to v4 if
   has_microstructure else v3. Avoids the dilution pathology that
   killed s117's v4 retrain.

---

## 1. The data-availability ablation

`scripts/ml_data_availability_ablation.py` (NEW, ~150 LOC).

### What we did
Joined v3-tuned-16fold OOS predictions to microstructure + news data
availability indicators. Stratified P≥0.30 picks by data presence.

### Results (P≥0.30, 12,192 OOS rows)

| Slice | Total rows | Picks | Pick% | avg | win% |
|---|---|---|---|---|---|
| ALL ROWS (baseline) | 12,192 | 839 | 6.9% | **+6.18%** | 47.2% |
| **has_microstructure=1** | **543** | **30** | 5.5% | **-1.62%** ❌ | 50.0% |
| has_microstructure=0 | 11,649 | 809 | 6.9% | +6.47% | 47.1% |
| has_news=1 (≥1 article) | 190 | 17 | 8.9% | +6.02% | 52.9% |
| has_news=0 BUT in fetch window | 3,890 | 304 | 7.8% | +6.73% | 48.7% |
| micro=1 AND news=1 | 24 | 3 | 12.5% | **+7.78%** | 66.7% |
| micro=1 AND news=0 | 519 | 27 | 5.2% | -2.67% | 48.1% |

### P≥0.50 (high-conviction)

| Slice | Total | Picks | avg | win% |
|---|---|---|---|---|
| ALL ROWS | 12,192 | 57 | +22.86% | 64.9% |
| has_microstructure=1 | 543 | **0** | — | — |
| has_news=1 | 190 | **0** | — | — |
| has_news=0 BUT in fetch window | 3,890 | 15 | +20.74% | 73.3% |

### Critical finding

**On the 21-day microstructure window, v3 produces ZERO P≥0.50 picks.**
The model is uncertain on those rows (broad-tier picks only). Combined
with the negative average, this means:

- v3 was trained mostly on 2024-2025 data
- The 2026-Q1 regime (which is when microstructure data covers) is
  different from training distribution
- v3 generalizes poorly to this new regime
- Microstructure features could plausibly help — IF a model is trained
  to use them on these specific rows

### Why this matters for Path B

Session 117's v4 lost because it was trained on ALL rows (mostly without
microstructure). A v4 trained ONLY on micro=1 rows would:
- See exclusively the harder regime
- Have full microstructure feature coverage (no NaN dominance)
- Be a SPECIALIST for the recent regime, not a generalist

At inference: route to v4 if has_microstructure, else v3.

---

## 2. Path B implementation plan

### Current data inventory (commit time)
- 21 trading days of microstructure (Jan 26 - Feb 10 + April 15-29)
- 543 (ticker, d0) rows = 2.7% of aftermath
- 64 trading days downloaded (185 GB raw); 43 more in conversion
- After full conversion: ~85 days = ~2,000-2,500 rows = ~10-12% coverage

### Two-stage rollout

**Stage 1 (today, with current 543 rows):**
- Build `ml_v4_routed.py` — trains v4 on `has_microstructure=1` rows only
- Evaluate WF: WF predictions on the 543 micro=1 rows
- Compare to v3 on those same 543 rows
- Verdict: ship Path B if v4-routed beats v3 on this slice

**Stage 2 (when conversion completes, ~2,000+ rows):**
- Re-train v4-routed on full data
- Bigger sample → less overfitting risk
- More confident verdict

### What gets shipped

```python
# Inference-time routing in ml_meta_scorer_inference.py
def predict_v4_routed(features, micro_data, scorer_v3, scorer_v4):
    if features.get("has_microstructure"):
        return scorer_v4.predict(features, micro_data)
    return scorer_v3.predict(features)
```

Production model bundle: `continuer_v3_tuned_16fold.pkl` (existing) +
`continuer_v4_routed.pkl` (NEW). MetaScorer.load_default() loads both;
score_candidate() checks data availability and routes.

---

## 3. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_data_availability_ablation.py` | NEW | ~150 |
| `data/models/data_availability_ablation.json` | NEW (gitignored) | small |
| `docs/research-log/118_data_availability_ablation_path_b.md` | NEW (this doc) | this |

---

## 4. Validated edge stack (post-118, unchanged for production)

```
Meta-scorer 16-fold AGGRESSIVE       +122.83% bankroll WF (~92% APY)
  Sharpe 3.15, Calmar 31.24, max DD -2.81%

For Monday: production v3-tuned-16fold ships.
Post-Monday: Path B v4 (data-routed) attempted on full conversion.
If Path B wins: route micro=1 rows to v4, others to v3.
```

---

## 5. Background jobs status

- ✅ trades_v1 80-day download: DONE (64 days, 185 GB raw)
- 🟡 trades_v1 Feb-Apr convert (43 days): running, ETA ~60-90 min
- ✅ News v2 backfill: DONE (4,080 rows / 190 with news / 4.66%)

---

## 6. Next-session priorities

1. **Monday: paper-deploy** with v3-tuned-16fold + aggressive Kelly.
2. **Wait for trades_v1 Feb-Apr conversion to complete** (~60-90 min).
3. **Rebuild microstructure features** at full coverage (~85 days).
4. **Implement Path B v4** (data-routed): train on micro=1 rows only.
5. **Compare v4-routed vs v3 on the micro=1 slice**; if v4 wins by
   measurable margin, ship the routed inference path.
6. **Strip TCN + swap VETOED to rule D** post-stable Monday.
