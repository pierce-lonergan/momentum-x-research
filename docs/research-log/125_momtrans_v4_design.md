# 125 — MoMTrans v4: GPU multi-task transformer (overnight/week training)

**Session date:** 2026-05-05 (Tuesday late EOD)
**Branch:** develop
**Predecessors:** [124 multi-blend + per-tier alpha](124_multi_blend_per_tier_alpha_v3_default_elite.md), D281 cohort cascade

---

## TL;DR

A GPU-native multi-task transformer designed to be trained overnight on the
RTX 5070 (12 GB VRAM, sm_120) with **NO production deployment** until fully
validated against v3-tuned-16f via the same 16-fold WF $-PNL gate that
shipped D281.

The architecture targets 6 of the 10 weaknesses identified in the deep-dive:

| Weakness (deep-dive) | MoMTrans component |
|---|---|
| W1: one model serves 4 tiers | Multi-class cohort head |
| W2: binary y discards magnitude | Quantile + Huber regression heads |
| W3: single conformal threshold | Quantile head → principled prediction bounds |
| W4: same features for every tier | Multi-task shared backbone |
| W6: modest ensemble diversity | Sequence-aware deep model (vs all-tabular ensemble) |
| (Bonus) Compass artifact #3 frontier | Hierarchical sequence path (minute paths -> embeddings) |

This is the **first deep-learning model on tick-derived sequences** in the
codebase. TCN attempts (s108/110/111) failed because they were retro-fit
into the existing veto framework; MoMTrans builds the veto framework
around the deep model from day 1.

---

## 1. Data assets we use

| Asset | Coverage | Use in MoMTrans |
|---|---|---|
| `aftermath_strat.parquet` | 20,029 rows | label source (ret_t5), tabular features |
| `intraday_paths_30min.parquet` | **505,582 rows / 3,413 tickers** | minute-bar sequence input (THE MAJOR UNLOCK) |
| `ticker_details.parquet` | 14,857/20,029 with mcap | sector dummies, cap, float (already in 54-feat set) |
| `ising_daily.parquet` | full | regime conditioning embedding (HI/MID/LO mag) |

**Critical observation:** the intraday paths dataset has ~25 bars per
(ticker, d0). Each bar = 6 features. Production v3 compresses this to 5
scalar summaries: `first_5min_max_close`, `first_5min_min_close`,
`last_5min_avg_close`, `first_5min_avg_volz`, `last_5min_avg_volz`,
`u_shape_intraday`, `volume_acceleration`, `has_path`. A sequence model
gets the whole 30×6 matrix per candidate.

---

## 2. Architecture

### Input

```
TabularBranch    : (B, 54)      — the existing v3 features
SequenceBranch   : (B, 30, 6)   — 30-min OHLCV+vol_z+log_trans path
RegimeBranch     : (B, 3)       — one-hot {HI, MID, LO} mag_5d
```

### TabularBranch (TabTransformer-style)

```
[B, 54] → FeatureEmbedding [B, 54, 64]
        → 4 × TransformerEncoder(d_model=64, nhead=4, dim_ff=128)
        → AttentionPool → [B, 128]
```

Each feature gets its own learned embedding (continuous → quantile-binned to
32 bins → embedding lookup). Self-attention across features lets the model
learn which features matter for each candidate.

### SequenceBranch (Time Transformer)

```
[B, 30, 6] → InputProjection (Linear 6 → 64) + sinusoidal PosEnc
           → 4 × TransformerEncoder(d_model=64, nhead=4, dim_ff=128)
           → CLSToken pool → [B, 128]
```

A learned [CLS] token attends to all 30 time-positions; its final hidden
state pools the entire sequence. Standard ViT-style pooling, no exotic
state-space machinery (Mamba is overkill for n=30).

### RegimeBranch

```
[B, 3] → Embedding(3, 16) → [B, 16]
```

### Cross-Attention Fusion

```
TabularEmbedding  : [B, 1, 128]
SequenceEmbedding : [B, 1, 128]
RegimeEmbedding   : [B, 1, 16]  → padded to 128

Concat → [B, 3, 128]
Cross-Attention(d_model=128, nhead=8) — each modality attends to others
Pool → [B, 128]
```

The cross-attention lets tabular features query "which minute mattered for
my prediction" — interpretable + principled.

### Multi-Task Heads (4 simultaneous tasks)

```
Shared MLP : [B, 128] → ReLU → Dropout(0.1) → [B, 64]

Head 1 (binary):     [B, 64] → Linear(64 → 1) → Sigmoid → P(ret_t5 ≥ 0.10)
Head 2 (cohort):     [B, 64] → Linear(64 → 4) → Softmax → P(BROAD/VETOED/HIGH/ELITE)
Head 3 (quantile):   [B, 64] → Linear(64 → 3) → (q10, q50, q90) of ret_t5
Head 4 (magnitude):  [B, 64] → Linear(64 → 1) → ret_t5 (Huber loss)
```

### Joint Loss

```
L_total = 0.30 * BCE(head1, y_binary)
        + 0.25 * CrossEntropy(head2, y_cohort)
        + 0.25 * QuantileLoss(head3, y_continuous, [0.1, 0.5, 0.9])
        + 0.20 * Huber(head4, y_continuous)
```

The weights are starting points; can tune via outer Optuna sweep. Each task
provides regularization to the others (multi-task transfer).

---

## 3. Training plan

### Phase 1 — Smoke test (NOW, 30 min)

Goal: prove the trainer runs end-to-end on a single fold without crashing.
Output: 1 trained model, qualitative WF print on the latest fold.

```bash
python scripts/ml_v4_momtrans_train.py --smoke
```

### Phase 2 — Single full WF run (overnight, ~4-6 hours)

Goal: get the first apples-to-apples comparison vs v3-tuned-16f baseline.
- 16-fold walk-forward identical to production
- Default hyperparameters
- BF16 mixed precision on GPU
- AdamW + 1cycle LR
- Early stopping per-fold

```bash
python scripts/ml_v4_momtrans_train.py --full-wf
```

### Phase 3 — Hyperparameter sweep (this week, ~24-48 hours)

Goal: find the configuration that beats v3-tuned-16f on $-PNL by ≥ +$500
with no tier regression > 30% (same SHIP gate as D281).

Sweep dimensions:
- `d_model` ∈ {32, 64, 128}
- `n_layers` ∈ {2, 4, 6}
- `dropout` ∈ {0.05, 0.10, 0.20}
- `task_weights` (4 dims simplex sample)
- `lr_max` ∈ {1e-3, 3e-4, 1e-4}
- `quantile_loss_weight_curriculum` (anneal vs constant)

Expected total: 32 trials × ~4 hr each = ~128 GPU-hours over the week.

```bash
python scripts/ml_v4_momtrans_sweep.py --n-trials 32 --hours 24
```

### Phase 4 — WF verdict + comparison vs production

Same gate as D281: SHIP if total $-PNL ≥ baseline + $500 AND no tier
regression > 30%. If verdict positive, ship behind `MX_USE_MOMTRANS=1`
env gate (default OFF, like all prior architectural experiments).

---

## 4. Why this is likely to work

The s124 finding (v3-default ELITE +$1,388 over v3-tuned-16f) and the
D281 cascade (+$1,104 from 4 cohort specialists) are both evidence that
**the production v3 model has unexploited residuals at specific cohort
boundaries**. A multi-task transformer with shared backbone can:

1. Learn cohort-specific representations *jointly* (vs D281's 4 separate
   models that share no weights and might overfit individually)
2. Use the full 30-bar sequence (vs v3's 5 scalar summaries)
3. Predict the *shape* of the return distribution (quantile head) rather
   than just whether it cleared a threshold

The pre-D281 ceiling was $11,594; D281 lifted it to $12,699. MoMTrans
target: $14,000+ (≈+10% over D281 cascade).

## 5. Why this might NOT work (the failure modes to monitor)

1. **Overfitting** — 20k rows is small for a transformer. Mitigation:
   small model (~500K params), heavy dropout, 16-fold WF.
2. **Train/eval distribution shift** — gap-up dynamics evolve. Mitigation:
   exponential time-decay weighting in loss (recent rows weighted more).
3. **Multi-task interference** — heads can fight each other if loss
   weights are wrong. Mitigation: GradNorm or PCGrad if needed.
4. **GPU memory fragmentation** — RTX 5070 has 12 GB but Windows reserves
   ~2 GB. Mitigation: batch_size=128 max, gradient checkpointing.
5. **Sequence model degeneration** — TCN failed (s108/110/111) because
   the model collapsed to "always predict 0" via focal loss. Mitigation:
   plain BCE on the binary head + multi-task regularization.

If after Phase 2 the WF $-PNL is BELOW production baseline, we have
strong evidence that "more capacity" doesn't help on this dataset and
should pivot. The grid-search and threshold-tuning of D281 are still
the highest-ROI deployable wins.

---

## 6. NOT shipping to production

This entire effort is **research-mode**. The cohort cascade (D281) is
the production architecture. MoMTrans is evaluated against it as a
candidate replacement. Even if MoMTrans wins WF, it ships **env-gated**
behind `MX_USE_MOMTRANS=1` (default OFF) — same pattern as every prior
architectural experiment.

Concrete safeguards:
- Output `data/models/momtrans_v4.pt` (gitignored); production loaders
  do not look for it
- `scripts/ml_meta_scorer_inference.py` will NOT load MoMTrans unless
  `MX_USE_MOMTRANS=1` and the .pt exists
- Lottery launcher does NOT set the env var
- All testing happens via dedicated `scripts/ml_v4_momtrans_verdict.py`
  outside the production inference path
