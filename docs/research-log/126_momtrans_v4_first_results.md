# 126 — MoMTrans v4 first-run results: scaffold WORKS, signal WEAK

**Session date:** 2026-05-06 (overnight after Tue paper-deploy prep)
**Branch:** develop
**Predecessors:** [125 design doc](125_momtrans_v4_design.md)
**Status:** RESEARCH — does NOT affect production

---

## TL;DR

The MoMTrans v4 scaffold works end-to-end:
- PyTorch nightly with CUDA 12.8 installed (Blackwell sm_120 support)
- Training runs at **32.8s/fold on RTX 5070** (16-fold WF in ~9 min)
- All 4 multi-task heads produce predictions, no NaN, loss descends
- Verdict + sweep harnesses ready for iteration

But the **first-run signal is far below production v3-tuned-16f**:

| Metric | MoMTrans v4 (first run) | Production v3-tuned-16f |
|---|---|---|
| Top-1% picks avg ret_t5 | +6.66% (52% win) | **+22.51% (62% win)** |
| Top-2% picks avg ret_t5 | +5.11% (49% win) | **+14.77% (54% win)** |
| Top-5% picks avg ret_t5 | +2.64% (44% win) | **+7.38% (48% win)** |
| Spearman ρ vs y_reg | 0.036 | **0.159** |

The model HAS positive rank discrimination (top-1% beats the population
mean of -3.5%) but extracts ~3× less signal than v3. **No SHIP verdict
without significant additional iteration.**

---

## What worked

1. **End-to-end GPU pipeline** — Blackwell sm_120 + PyTorch 2.12 nightly
   trains 16-fold WF in 9 min wall-clock at 32.8s/fold (vs ~80 min
   estimate). Plenty of compute headroom for sweeps.

2. **Multi-task scaffold** — All 4 heads (binary, cohort, quantile,
   magnitude) produce sane outputs. Joint loss descends to ~1.0 after
   100 epochs across all folds.

3. **Sequence ingestion** — `intraday_paths_30min.parquet` (505,582 rows)
   loaded into per-(ticker, d0) sequences with 98.97% coverage. Sequence
   features (open_rel, high_rel, low_rel, close_rel, vol_z, log_trans)
   feed into the time-transformer branch.

4. **Class imbalance handling** — pos_weight=4.0 on BCE + inverse-freq
   weights on cohort CE shifted predictions out of the always-low regime
   the v1 attempt collapsed to.

## What's wrong (the diagnostic)

### Multi-task interference

First training run (default weights 0.30/0.25/0.25/0.20):
- Binary head max prob = 0.45 → no picks above 0.30 threshold
- Cohort head 100% predicting SKIP (the dominant 80% class)
- Magnitude head: predicts mean -0.07 (matches population, no discrimination)
- Quantile head: q90 = +0.29 (vs realized +0.50, underestimates upside)

Second training run (rebalanced weights 0.55/0.25/0.10/0.10 + class weights):
- Binary head fires on 98% of rows at threshold 0.30 — over-predicting
- Cohort head fires ELITE on 9% of rows (vs 0.06% reality) — over-predicting

Pattern: classification heads either **collapse to dominant class** OR
**over-predict everything**. The middle ground requires more training,
larger model, or different regularization.

### Likely causes

1. **Insufficient capacity** — d_model=64, n_layers=4 may be too small
   for 54 features × 30 sequence positions. Try d_model=128.

2. **Insufficient training time** — 100 epochs with early-stopping at
   patience=15 means many folds stop at 30-50 epochs. Increase patience
   or remove early stopping.

3. **Sequence head adds noise without signal** — minute-bar paths may
   not have strong predictive content beyond what v3's scalar summaries
   already capture. Test: train tabular-only ablation.

4. **Multi-task loss balance** — Quantile + Huber heads may be teaching
   the binary head bad lessons. Test: classification-only training.

5. **No self-supervised pre-training** — 20k samples is small for a
   transformer. Pre-train an encoder via masked feature reconstruction
   on the same data, then fine-tune for the 4 tasks.

6. **No exponential time-decay weighting** — recent gap-up dynamics
   should weight more than 12-month-old samples. Easy to add.

---

## Iteration roadmap (this week)

### Phase A — Diagnostic ablations (1-2 hrs each)

```bash
# A1: tabular-only (does the sequence branch help at all?)
python scripts/ml_v4_momtrans_train.py --full-wf --epochs 100 \
    --d-model 128 --n-layers 4
# (Manually disable seq_branch in build_model if needed)

# A2: classification-only (drop quantile + magnitude heads)
# Edit joint_loss to set weights=(0.70, 0.30, 0.0, 0.0)

# A3: bigger model, longer training
python scripts/ml_v4_momtrans_train.py --full-wf --epochs 300 \
    --d-model 128 --n-layers 6 --dropout 0.15 --patience 30
```

### Phase B — Self-supervised pre-training

Train a masked-feature autoencoder on the 20k rows for 200 epochs
(no labels). Then fine-tune the encoder for the 4 supervised tasks.
This is the standard fix for "small dataset + transformer" problems.
Estimated: +30-50% rank correlation if it works.

### Phase C — Optuna sweep (24-48 hours)

```bash
python scripts/ml_v4_momtrans_sweep.py --n-trials 32 --hours 24
```

Search over (d_model, n_layers, dropout, lr_max, batch_size). Each
trial is now ~9 min on GPU, so 32 trials = ~5 hours, leaves headroom
for re-runs.

### Phase D — Architectural variants (next week)

- Mamba state-space model for sequence branch (compass artifact #3)
- Hierarchical multi-scale (5min + 30min + daily features stacked)
- Set transformer for ranking-mode (predict the top-K of today's cohort)

---

## NOT shipping anywhere near production

The cohort cascade (D281) ships in tomorrow's deploy and stays the
production architecture. MoMTrans is a candidate replacement that
needs to clear the SHIP gate (+$500 + no -30% regression) before it
gets an env-gated production path.

First-run gap to baseline: ~15pp on top-1% avg ret_t5. Closing that
takes work but the GPU + scaffold + sweep harness are all in place
to do it iteratively.

## Files

- `scripts/ml_v4_momtrans_train.py` — trainer (modified: rebalanced loss)
- `scripts/ml_v4_momtrans_verdict.py` — comparison vs v3 baseline
- `scripts/ml_v4_momtrans_sweep.py` — Optuna sweep harness
- `data/models/momtrans_v4.pt` (gitignored) — best-fold artifact
- `data/models/momtrans_v4_predictions.parquet` (gitignored) — WF preds
- `data/models/momtrans_v4_summary.json` (gitignored) — per-fold metrics

## Hardware confirmed working

```
GPU:           NVIDIA GeForce RTX 5070 (Blackwell sm_120)
VRAM:          12.82 GB total, ~3 GB used during training
Driver/CUDA:   591.59 / CUDA 12.8 (PyTorch nightly cu128)
torch:         2.12.0.dev20260408+cu128
arch_list:     ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']
Wall-clock:    525.6s for 16-fold WF (32.8s/fold avg)
GPU util:      40-45% (room to grow with bigger batches or models)
GPU temp:      57°C (well within thermal headroom)
```
