# 127 — MoMTrans v4 ablations: tabular-only is the surprise winner

**Session date:** 2026-05-06 (early AM, after Tue paper-deploy)
**Branch:** develop
**Predecessors:** [125 design doc](125_momtrans_v4_design.md), [126 first results](126_momtrans_v4_first_results.md)
**Status:** RESEARCH — production unchanged

---

## TL;DR

Ran 3 ablations sequentially on RTX 5070 (~30 min wall-clock total).
Each is a full 16-fold WF training of the MoMTrans v4 architecture
with one component changed. The results FUNDAMENTALLY redirect where
v4 should go next.

| Variant | top-1% avg | Spearman ρ vs y_reg |
|---|---|---|
| v3-tuned-16f (PROD baseline) | +22.51% (62% win) | **+0.1586** |
| MoMTrans default | +6.66% (52% win) | +0.0357 |
| **MoMTrans tabular-only** ⭐ | +5.03% (43% win) | **+0.1407** |
| MoMTrans cls-only | +7.60% (49% win) | +0.0164 |
| MoMTrans bigger | -6.13% (36% win) | +0.0194 |

The tabular-only variant **4× the rank correlation** of the default
(0.036 → 0.141), reaching **88% of production v3's ρ**. The
sequence branch was ADDING NOISE, not signal.

---

## Three concrete answers

### 1. The sequence branch is dead weight (and worse — actively harmful)

Default Spearman ρ = 0.036. Tabular-only = 0.141. Removing the
time-transformer **4×'d** the rank correlation. The minute-bar paths
(open_rel/high_rel/low_rel/close_rel/vol_z/log_trans for 30 minutes)
do NOT add discriminative signal beyond what v3's 5 scalar summaries
already capture.

This contradicts the s109 design intuition that "compressing 30 bars
into 5 scalars throws information away." Apparently the right
information IS in the 5 scalars (first_5min_max_close,
last_5min_avg_close, vol_z, etc.); the rest is noise that confuses
a transformer with limited training data.

**Implication:** future v4 iterations should drop the sequence branch
entirely OR replace it with a richer per-(ticker, d0) representation
(e.g., tick-level trades_v1, sector contagion graph, news embeddings).

### 2. More capacity overfits — small data is the bottleneck

The "bigger" variant (d_model=128, n_layers=6 vs default 64/4) was
**the worst** of all 4 runs: top-1% avg = -6.13%. With only 20k
training rows, doubling parameters causes overfitting.

**Implication:** the model is already at or above optimal capacity
for this dataset size. Lifts must come from BETTER USE OF EXISTING
DATA (self-supervised pre-training, time-decay weighting, careful
multi-task balance) — not bigger models.

### 3. Multi-task vs cls-only: tradeoff between top-1% and ranking

| Metric | Multi-task default | cls-only |
|---|---|---|
| top-1% avg | +6.66% | **+7.60%** |
| top-5% avg | **+2.74%** | -0.96% |
| top-10% avg | -0.69% | **-1.95%** |
| Spearman ρ | **+0.0357** | +0.0164 |

cls-only spikes higher at the very tail (the model is more
confident about its top picks) but falls apart for the broader
ranking. Multi-task default is more balanced.

**Implication:** quantile + magnitude regression heads ARE pulling
weight on global ranking but adding noise to the top-1% precision.
The cohort cascade D281 already proves cohort-specialized models
beat single-objective at the tail; v4 might benefit from a
cls-only head on top of a tabular-only backbone, with tier-specific
inference thresholds (echoing D281's UNION strategy).

---

## What this means for next steps

The previous plan (overnight Optuna sweep) needs to be REDIRECTED.
Sweeping over (d_model, n_layers, dropout) on the default config is
mostly useless when:
- the sequence branch is harmful → drop it
- more capacity overfits → don't sweep up
- the regression heads are mixed bag → revisit task balance

### Updated phase plan

**Phase 1 (next session):** Tabular-only Optuna sweep
- Fix the architecture to tabular-only
- Sweep over (d_model ∈ {32, 48, 64}, n_layers ∈ {2, 3, 4},
  dropout, lr_max, task_weights simplex)
- Smaller models, careful regularization
- Target: close Spearman gap from 0.141 → 0.16+ (matching production)

**Phase 2:** Self-supervised pre-training
- Mask 30% of tabular features, train an encoder via reconstruction
- Use unlabeled rows (could expand beyond aftermath_strat to
  the full 60k+ Polygon-screener candidates)
- Then fine-tune for the 4 supervised tasks

**Phase 3:** Tier-specific tabular models (the cohort-cascade analog)
- Train 4 tabular-only MoMTrans models, each on a different
  cohort threshold (echoing D281)
- Each gets its own Optuna params (the s124+s111 finding suggests
  this matters)

**Phase 4 (deferred):** Architectural alternatives to the sequence branch
- Tick-level trades_v1 transformer (compass artifact #3 frontier)
- Sector-graph contagion model
- News-text encoder (Polygon /v2 articles into embeddings)

The s109/s121/s124 progression suggests rank correlation 0.16 is
near a "data ceiling" for this 54-feature universe. Closing the
last 12% of MoMTrans's gap to production may need either MORE DATA
or MORE FUNDAMENTALLY DIFFERENT FEATURES, not just better
architectures.

---

## Hardware utilization (this session)

```
3 ablations × 16-fold WF each on RTX 5070 (Blackwell sm_120):
  tabular_only:  ~6 min  (sequence branch zeroed → faster)
  cls_only:      ~6 min  (same arch, different loss)
  bigger:       ~12 min  (2x params)
TOTAL: ~30 min wall-clock for all 3

GPU peak:  2.6 GB VRAM, 42% util, 42°C
Headroom:  9 GB VRAM unused; could run 2-3 ablations in parallel next time
```

---

## NOT shipping anywhere near production

D281 cohort cascade (live for tomorrow) remains the production
architecture. MoMTrans v4 gap to production is now better understood
but still material. The next iteration is targeted at closing that
gap, not deploying.

## Files

- `scripts/ml_v4_momtrans_train.py` — added `--variant` + `--out-suffix` flags
- `scripts/ml_v4_momtrans_compare_ablations.py` — NEW (~80 LOC)
- `data/models/momtrans_v4_{tabular_only,cls_only,bigger}_predictions.parquet` — gitignored
- `data/models/momtrans_v4_ablation_comparison.json` — gitignored
- `docs/research-log/127_momtrans_v4_ablations.md` — this doc
