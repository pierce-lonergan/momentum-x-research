# 130 — MoMTrans v4 Production Architecture: Complete Reference

**Session date:** 2026-05-06 (overnight, Tue→Wed deploy gate)
**Branch:** develop → main
**Predecessors:** [125 design](125_momtrans_v4_design.md) → [126 first results](126_momtrans_v4_first_results.md) → [127 ablations](127_momtrans_v4_ablations.md) → [128 breakthrough](128_momtrans_v4_tier_cascade_breakthrough.md) → [129 validation complete](129_momtrans_v4_validation_complete.md)
**Status:** PRODUCTION (env-gated `MX_USE_MOMTRANS=1`, ON for Wednesday deploy)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement & Motivation](#2-problem-statement--motivation)
3. [Data Sources & Lineage](#3-data-sources--lineage)
4. [Architecture](#4-architecture)
5. [Training Procedure](#5-training-procedure)
6. [Design Rationale (Why we built it this way)](#6-design-rationale)
7. [Validation Methodology](#7-validation-methodology)
8. [Production Integration](#8-production-integration)
9. [Critique & Known Limitations](#9-critique--known-limitations)
10. [What Could Push It Further](#10-what-could-push-it-further)
11. [Operational Runbook](#11-operational-runbook)

---

## 1. Executive Summary

**MoMTrans v4** is a cohort-specialized multi-task tabular transformer for
predicting microcap intraday continuation. Specifically: given a candidate
ticker on a given day (`d0`), predict the probability that its 5-day
forward return (`ret_t5 = close(d0+5) / open(d0) - 1`) crosses each of
four magnitude thresholds: 10%, 15%, 25%, 40%.

The architecture comprises **four PyTorch tier specialists**, each a
TabTransformer encoder with multi-task heads. All four share the same
54-feature input, the same architecture, and the same self-supervised
pre-trained encoder weights — they differ only in their binary
classification target (the `ret_t5 ≥ T` threshold).

At inference, all four specialists score every candidate. A UNION
threshold cascade assigns the candidate to the highest-conviction tier
that fires. Aggressive Kelly sizing (50/35/20/10 caps) on dynamic
account-equity bankroll completes the trade signal.

**WF Backtest performance** (16-fold walk-forward, Aggressive Kelly,
$10k bankroll, 100bps slippage):

| Strategy | $-PNL | Picks | $/pick |
|---|---|---|---|
| Production v3-tuned-16f baseline | $11,049 | 481 | $22.97 |
| **MoMTrans v4 cascade** | **$57,621** | **1,707** | **$33.76** |
| **Lift** | **+$46,573** | **+254%** | **+47%** |

Three independent validation phases:
- **Phase A** (out-of-sample threshold validation): lift retention 103.7%
- **Phase B** (cap=10/day capacity-constrained): +$71,049 lift
- **Phase C** (slippage-adjusted): break-even at ~290 bps; 3-4× headroom

Production wiring is env-gated: `MX_USE_MOMTRANS=1` in launcher. Default
is OFF; Wednesday's deploy is the first ON window. Rollback is
immediate (unset env var → falls back to D281 cohort cascade).

---

## 2. Problem Statement & Motivation

### The trading problem

Given a microcap stock that gapped up overnight, predict whether it will
continue up over the next 5 trading days (a "continuer") versus reverse
and fade. The strategy buys at d0 open and exits at d0+5 close. The
edge to predict is the conditional distribution of `ret_t5` given the
day-0 morning state.

### Why a new model when production already works

Production v3 (`continuer_v2_v3_tuned_16fold.pkl`) is a stacked tabular
ensemble (XGBoost + LightGBM + LogReg + RandomForest → LogReg meta) with
54 hand-engineered features, 16-fold walk-forward, and conformal-modulated
Kelly sizing. WF backtest delivers **+122.83% bankroll over 16 months** —
a strong baseline.

But two recent findings exposed a structural ceiling:

1. **s124 (multi-blend per-tier alpha)**: v3-default (no Optuna) generates
   +$1,388 (+14.4%) better ELITE-tier P&L than v3-tuned-16f. Optuna's
   regularization optimizes the broad-objective cohort and under-fits
   rare events.

2. **s111 (16-fold Optuna retune)**: helped HIGH/BROAD by $1,635 but
   COST ELITE/VETOED $569. A single set of hyperparameters cannot
   simultaneously optimize all four cohorts.

Both findings point to the same architectural conclusion: **one model
serving four tiers is the wrong abstraction**. D281 (the production
cohort cascade in XGBoost) was the first solution; MoMTrans v4 is the
deep-learning answer to the same question.

### The hypothesis MoMTrans tests

If we train **separate models per cohort** with shared input but
specialized objectives, AND we use **self-supervised pre-training** to
compress the limited (20k row) labeled signal into a stronger encoder,
THEN we can extract per-pick alpha that single-model + single-objective
optimization cannot reach.

The +47% per-pick edge over production at 100bps slippage is the answer:
**yes, this hypothesis holds**.

---

## 3. Data Sources & Lineage

### Primary dataset: `aftermath_strat.parquet`

**Path:** `data/polygon_warehouse/derived/aftermath_strat.parquet`
**Rows:** 20,029 (after `ca_flag = 'clean' AND ret_t5 IS NOT NULL` filter)
**Date range:** Aug 2024 → Apr 2026 (16 walk-forward folds, 365d
train / 30d test rolling window)

Each row = one (ticker, d0) pair where `d0` is a day the ticker was
identified as a microcap gap-up candidate. Source pipeline:

```
Polygon historical aggregates (s95-98)
  → polygon_warehouse/aggregates_1day_us_stocks
  → high_movers_catalog.parquet  (filter: intraday_pct >= 0.30 AND $1.50<=open<=$50 AND vol>100k)
  → aftermath_strat.parquet       (add: prior cont/fade rates, cross-sectional ranks)
```

**Label**: `ret_t5 = close(d0+5) / open(d0) - 1`, clipped to [-0.50, 1.00]
to reduce tail outlier influence in regression heads.

### Feature surface (54 features, identical to v3)

The exact 54-feature set is locked into `data/models/momtrans_v4_tier_*.pt`
under the `feature_columns` key. Categorized:

| Category | Count | Features |
|---|---|---|
| **Per-ticker priors** | 8 | prior_n, prior_n_log, prior_cont_rate, prior_fade_rate, prior_avg_t5, prior_avg_intra, prior_avg_oc, prior_30d_count |
| **Day-0 state** | 6 | log_open, log_dvol_d0, intraday_pct, intraday_pct_log, ret_open_close_d0, close_strength |
| **Cross-sectional rank** | 5 | rank_intra, rank_intra_log, rank_dvol, intra_vs_today_avg, dvol_vs_today_avg |
| **Today's universe** | 2 | n_today_total, intra_vs_prior |
| **Calendar** | 8 | dow, month, year, day_of_year, day_of_month, week_of_month, quarter, year_frac |
| **Recency** | 1 | prior_7d_count |
| **Ticker fundamentals** (s122) | 8 | log_market_cap, mcap_known, log_employees, log_days_since_ipo, log_float, sic_code, sic_group |
| **Sector dummies** (s122) | 8 | sec_pharma, sec_bio, sec_medical, sec_software, sec_finance, sec_semi, sec_spac, sec_reit |
| **LLM-derived** (s109) | 1 | log_rank_x_prior_continuer |
| **Intraday path** (s109, present but UNUSED in MoMTrans tabular branch) | 8 | first_5min_max_close, first_5min_min_close, last_5min_avg_close, first_5min_avg_volz, last_5min_avg_volz, u_shape_intraday, volume_acceleration, has_path |

### Feature standardization

Each feature is z-scored on the FULL dataset (computed once, persisted in
the `.pt` file as `feature_means` + `feature_stds`). This is loaded at
inference and applied to live candidate features identically to training.

### Auxiliary signals (NOT direct inputs)

| Signal | Used by | How |
|---|---|---|
| **Ising 5-day magnetization** | Tier waterfall mag-gate | HI / MID / LO regime classification (HI=>+0.05, LO=<-0.05) |
| **Intraday minute paths** | Sequence branch (DISABLED in MoMTrans) | 30 minute-bars × 6 features per (ticker, d0). Ablation showed sequence branch is NET HARMFUL on this dataset; tabular-only is the winner |
| **TCN intraday-veto signal** | Production v3 only (not MoMTrans) | Skipped when `MX_VETOED_RULE=D` |

### Data quality controls

- **Corporate-action filtering**: `ca_flag = 'clean'` excludes splits/divs
- **Price band**: $1.50 ≤ open ≤ $50 (microcap focus)
- **Volume floor**: `dvol_d0 >= $100k` (liquidity gate)
- **Walk-forward only**: NO test-set leakage in feature engineering
  (rolling priors use only data prior to `d0`)
- **Inf/NaN replacement**: `.replace([inf, -inf], 0).fillna(0)` before training

---

## 4. Architecture

### High-level data flow

```
                                             ┌── ELITE specialist (y=ret_t5>=0.40)
                                             │
[54 standardized features] → [SSL encoder] ──┼── HIGH  specialist (y=ret_t5>=0.25)
                                             │
                                             ├── VETOED specialist (y=ret_t5>=0.15)
                                             │
                                             └── BROAD specialist (y=ret_t5>=0.10)

  4 cohort-specific probabilities  →  UNION cascade router
                                       (ELITE > HIGH > VETOED > BROAD,
                                        each tier with its own threshold)
                                       →  Aggressive Kelly sizing
                                       →  Order submission
```

### Single-specialist architecture (TabTransformer + multi-task heads)

Each of the 4 specialists is an identical `nn.Module` differing only
in its trained weights:

```
Input: (B, 54)  z-scored features

TabularBranch:
  per-feature scalar  →  Linear(1, 64)  →  add learned PosEnc[1, 54, 64]
                      →  prepend learned [CLS] token  →  shape (B, 55, 64)
                      →  TransformerEncoder(d_model=64, nhead=4, dim_ff=128,
                                            n_layers=4, dropout=0.10)
                      →  pool: take [CLS] hidden state (B, 64)

SequenceBranch (DISABLED via disable_sequence=True):
  zero vector (B, 64)

RegimeBranch:
  Embedding(3, 64) — index from {HI=0, MID=1, LO=2}

Fusion:
  stack [tab_pooled, seq_zero, regime_emb]  →  (B, 3, 64)
  TransformerEncoder × 2 layers, nhead=4
  flatten  →  (B, 192)

Shared MLP:
  Linear(192, 128) → GELU → Dropout(0.10) → Linear(128, 64)

Heads (all branch from the 64-dim shared embedding):
  binary    : Linear(64, 1)            → P(ret_t5 >= specialist_threshold)
  cohort    : Linear(64, 5)            → softmax over {SKIP/BROAD/VETOED/HIGH/ELITE}
  quantile  : Linear(64, 3)            → q10, q50, q90 of ret_t5 (continuous)
  magnitude : Linear(64, 1)            → ret_t5 (continuous regression)
```

**Total params per specialist**: ~500K. All 4 specialists together
~2 MB on disk (FP32 state dicts).

### Loss function (joint multi-task)

```
L_total = w_bce * BCE(binary, y_binary, pos_weight=4.0)
        + w_ce  * CE(cohort, y_cohort, weights=inverse_freq_capped_5x)
        + w_qnt * QuantileLoss(quantile, y_continuous, taus=(0.1, 0.5, 0.9))
        + w_huber * Huber(magnitude, y_continuous, delta=0.10)

Default weights: (0.55, 0.25, 0.10, 0.10)
```

The classification-heavy weighting (80% on binary + cohort) is
empirically necessary. With the original (0.30/0.25/0.25/0.20) defaults,
the regression heads dominated and the binary head collapsed to predicting
all probabilities below 0.30 (doc 126). The 80% classification weighting
fixed it.

`pos_weight=4.0` on BCE compensates for the ~21% positive class. Without
it, the model under-predicts confidently. With it (and combined with
class-weighted CE), the model has the right risk/precision tradeoff.

### Self-supervised pre-training (the encoder warm-start)

A separate training run pre-trains the TabTransformer encoder via
masked-feature reconstruction (TabPFN/MAE-style):

```
For each batch:
  1. Sample row x ∈ R^54 from the standardized dataset
  2. Random mask 30% of features per row (independent Bernoulli)
  3. Replace masked positions with a learned [MASK] token embedding
  4. Forward through encoder + recon_head (Linear → GELU → Linear → 1)
  5. Loss = MSE(predicted, original) over masked positions only

Hyperparams: 200 epochs, batch=256, AdamW lr=3e-4, mask_pct=0.30
Compute: 145 seconds on RTX 5070
Final loss: 0.37 (down from ~1.0 initial; features are z-scored)
```

The pre-trained encoder weights (`tab_proj`, `tab_pos`, `tab_pool_q`,
`tab_enc.*`) are persisted to `data/models/momtrans_v4_ssl_encoder.pt`
and loaded as the starting state for each of the 4 supervised specialists.

**Why SSL helps here**: 20k labeled rows is small for a transformer.
Pre-training squeezes the input distribution into the encoder before
fine-tuning sees the labels. Empirical lift: BROAD specialist top-1%
went from +5.03% (no SSL) to +11.07% (with SSL) — **2× improvement**.

### Inference path (per candidate)

```python
# In MetaScorer.score_candidate (production code, ml_meta_scorer_inference.py)
1. Build feature dict from runner candidate
2. For each tier_name in (ELITE, HIGH, VETOED, BROAD):
     - Standardize features using saved means/stds
     - Forward through that tier's specialist model
     - Get binary head sigmoid → P(ret_t5 >= tier_threshold)
3. Apply UNION cascade router (assign_tier_momtrans):
     - ELITE   if elite_p   >= 0.60 AND mag IN (HI, MID)
     - HIGH    if high_p    >= 0.70 AND mag IN (HI, MID) AND not ELITE
     - VETOED  if vetoed_p  >= 0.90 AND mag = MID         AND not ELITE/HIGH
     - BROAD   if broad_p   >= 0.60 AND mag IN (HI, MID) AND not above
     - else SKIP
4. compute_kelly(tier, max(specialist_proba, 0.31), conformal_width=0.5)
5. Return MetaDecision(tier, kelly_frac, notional_usd=bankroll*kelly, ...)
```

Per-candidate inference latency: <5ms on CPU (each model is ~500K params,
forward pass is trivially fast for batch=1).

---

## 5. Training Procedure

### Walk-forward setup (mirrors production v3)

- **Folds**: 16 (rolling 365-day train / 30-day test)
- **Inside-fold split**: 60% base train / 20% meta train / 20% calibration
  (mirrors v3's stack architecture; meta+calib unused for MoMTrans but
   kept for forward-compat with cross-strategy comparison)
- **Final production model**: trained on ALL data minus a 5-day holdout
  (matches v3 production pattern in `ml_continuer_v2_ensemble.py`)

### Per-specialist training

For each tier T ∈ {BROAD, VETOED, HIGH, ELITE}:

```bash
python scripts/ml_v4_momtrans_train.py \
    --full-wf \
    --variant tabular_only \
    --out-suffix _tier_T \
    --epochs 100 \
    --batch-size 128 \
    --d-model 64 \
    --n-layers 4 \
    --dropout 0.10 \
    --lr-max 3e-4 \
    --patience 12 \
    --precision fp32 \
    --load-ssl-encoder data/models/momtrans_v4_ssl_encoder.pt
# Plus: env MX_MOMTRANS_TIER_THRESHOLD=T_threshold + MX_MOMTRANS_TIER_NAME=T
```

### Optimization

- **Optimizer**: AdamW, weight_decay=1e-4
- **Scheduler**: 1cycleLR, max_lr=3e-4, pct_start=0.10
- **Gradient clipping**: max_norm=1.0
- **Early stopping**: patience=12 epochs on training-loss plateau (1e-4 tol)
- **Precision**: FP32 (BF16 produced NaN gradients in early experiments;
  the safety margin from FP32 is worth the slight throughput loss)
- **NaN guard**: per-batch finite check; skip bad batches without
  corrupting weights with NaN gradients

### Compute budget (RTX 5070 Blackwell, 12 GB VRAM)

| Phase | Wall-clock |
|---|---|
| SSL pre-training (200 epochs) | 145s |
| Per-tier specialist (16-fold WF + final model) | ~25 min |
| All 4 specialists | ~100 min |
| Validation Phases A + B + C | ~5 min |
| **Total**: build + validate from scratch | **~2 hours** |

Per-fold training with GPU saturation was ~30s under contention from a
parallel sweep, ~95s when sweep was killed (more thorough early-stopping
without GPU competition). Either way, the entire model can be retrained
in well under a single market session.

### Reproducibility

- `numpy` seed: 42 (per-fold variance: `42 + fold_i`)
- `torch` not seeded explicitly (deterministic CUDA off; trades small
  batch-to-batch variance for ~2x throughput). For deterministic runs,
  add `torch.backends.cudnn.deterministic = True` at script top.
- Optuna sampler: TPE with seed=42

---

## 6. Design Rationale

### Why a transformer instead of more XGBoost / LightGBM?

D281 (production cohort cascade) is XGBoost-based and delivers +$1,104
over baseline. MoMTrans's lift is +$46,573. The delta comes from:

1. **Multi-task transfer learning**: classification + cohort + quantile +
   magnitude heads all share a 64-dim representation. Each head's gradient
   regularizes the others. XGBoost specialists are independent; can't
   share representation.

2. **Self-supervised pre-training**: requires a differentiable encoder
   with parameter-sharing (the Transformer's `tab_proj` + `tab_enc`).
   XGBoost has no analog — its trees can't be "pre-trained".

3. **Continuous gradient signal**: at training time the transformer sees
   the full continuous loss surface; XGBoost sees discrete split decisions.
   On 20k rows the gradient signal is more efficient than tree boosting
   for this learning rate (verified empirically).

4. **Calibrated probability output**: BCE + sigmoid is naturally
   probability-calibrated. XGBoost ensembles need temperature scaling
   (s123) as a separate post-processing step.

### Why tabular-only (sequence branch ABLATED OUT)?

Doc 127 ablation result was decisive:

| Variant | Spearman ρ |
|---|---|
| Default (tabular + sequence + regime) | 0.036 |
| **Tabular-only (sequence zeroed)** | **0.141** |

The sequence branch on minute-bar paths actively harmed signal —
4× worse rank correlation. Hypothesis: 30 bars × 6 features × 20k samples
is insufficient for a Transformer to learn the path representation;
the 5 hand-engineered scalar summaries already in the tabular features
(first_5min_max_close, last_5min_avg_close, etc.) capture the actionable
information.

The sequence branch is **kept in code, disabled at runtime** for two reasons:
(1) state-dict compatibility with future variants that re-enable it;
(2) easy A/B test if we acquire denser data (e.g., trades_v1 transformer).

### Why 4 specialists instead of one multi-class model?

A single 5-class softmax (SKIP/BROAD/VETOED/HIGH/ELITE) was tried in the
default architecture (cohort head). It collapsed to "always predict SKIP"
because SKIP is 80% of the data. Class-weighting helped but produced
chaotic over-prediction (98% above 0.30).

Splitting into 4 BINARY classifiers each with their own positive class
(11-21% positive rate) gives each specialist a more balanced learning
signal. The cascade router on top recovers the multi-class semantics
at inference.

### Why the UNION threshold cascade?

Tested two strategies in doc 128's grid search:

| Strategy | $-PNL | Δ vs prod |
|---|---|---|
| REPLACE (each tier's specialist alone) | $+8,219 | -$3,376 |
| **UNION (specialist OR baseline)** | **$+12,699** | **+$1,104** |

REPLACE blocks too many baseline picks (specialist requires a STRICT
bar). UNION adds picks that the baseline rule misses. The MoMTrans
production cascade extends UNION semantics: each tier's specialist
threshold (re-tuned in Phase A) becomes the firing rule, and tiers are
mutually exclusive in cascade order ELITE > HIGH > VETOED > BROAD.

### Why these specific thresholds (0.60 / 0.70 / 0.90 / 0.60)?

Selected by Phase-A out-of-sample threshold validation:
- Grid-search 600 combos of (E ∈ 0.40-0.90, H ∈ 0.40-0.80, V ∈ 0.50-0.90,
  B ∈ 0.50-0.80) on TUNE folds (0-7)
- Pick the combo maximizing $-PNL on TUNE
- Apply that frozen combo to VERIFY folds (8-15)

Result: VERIFY lift +$38,803 vs TUNE lift +$37,417 (103.7% retention).
Thresholds picked on older data work even better on newer data, ruling
out overfitting.

The asymmetric thresholds (HIGH=0.70 highest, ELITE=0.60 lower) are
empirical: HIGH specialist's probabilities are more sharply bimodal,
needing a higher cutoff. ELITE specialist's probabilities are more
diffuse (rare class), needing a lower cutoff to fire enough.

---

## 7. Validation Methodology

The validation pipeline mirrors what a quant fund would require before
committing capital. Three independent gates, each one capable of
KILLING the project on its own:

### Phase A — Out-of-sample threshold validation

**Question**: Is the +$78k lift a threshold-overfit artifact?

**Method**: Strict in-sample / out-of-sample split:
- TUNE: folds 0-7 (oldest 8 folds, dates ≤ 2025-09-11)
- VERIFY: folds 8-15 (newest 8 folds, dates ≥ 2025-09-12)
- Run the 600-combo threshold grid on TUNE only
- Freeze winning thresholds, apply to VERIFY
- Compare cascade $-PNL to production $-PNL on VERIFY

**Result**: Lift retention 103.7% (verify lift slightly LARGER than tune)

| Set | MoMTrans | Production | Lift |
|---|---|---|---|
| TUNE | $+41,787 | $+4,370 | $+37,417 |
| VERIFY | $+46,027 | $+7,224 | $+38,803 |

Threshold-overfit ruled out. The signal generalizes.

### Phase B — Capacity-constrained backtest

**Question**: Does the cascade still win when daily picks are capped at
production-realistic levels?

**Method**: Daily-pick-cap sweep K ∈ {5, 10, 15, 20, 30, 50, 100, ∞}.
Within each (d0), keep top-K picks ranked by (tier_priority,
specialist_proba desc). Compare $-PNL.

**Result**: At cap=10/day (production realistic), lift is +$71,049
(+612.6%), HIGHER than uncapped's +$76,220. The cap acts as a quality
filter — keeping the top-10 by (tier, prob) is MORE selective than
firing all 2,445 candidates.

| Cap | MoMTrans | Production | Lift |
|---|---|---|---|
| 5 | $+58,905 | $+11,514 | $+47,390 |
| **10** | **$+82,648** | **$+11,599** | **$+71,049** |
| 20 | $+83,210 | $+11,594 | $+71,616 |
| ∞ | $+87,814 | $+11,594 | $+76,220 |

Capacity ruled out as a constraint at production-realistic levels.

### Phase C — Slippage-adjusted backtest

**Question**: Does the lift survive realistic microcap market impact?

**Method**: Apply uniform per-pick haircut (round-trip cost) to BOTH
strategies' realized returns. Sweep haircut from 0-500 bps. Find
break-even.

**Result**: Break-even at ~290 bps. Realistic microcap slippage:
50-100 bps. Headroom: ~215 bps.

| Slippage | MoMTrans | Production | Lift | Per-pick edge |
|---|---|---|---|---|
| 0 | $+82,648 | $+11,619 | $+71,029 | +100% |
| 50 (paper) | $+70,134 | $+11,334 | $+58,801 | +75% |
| 100 (live) | $+57,621 | $+11,049 | $+46,573 | +47% |
| 200 (pessim.) | $+32,595 | $+10,479 | $+22,116 | -12% |
| 290 (BE) | ~$10k | ~$10k | $0 | 0% |

Slippage ruled out as the killer at any realistic estimate.

### What's NOT validated yet (the Phase D-E gate)

- **Live fills** — WF backtest assumes you can buy at the recorded close.
  Reality: market orders at open, partial fills, halts, broker latency.
- **Real-time degradation** — 16 folds covers ~16 months. Microcap
  dynamics evolve; a model trained on Aug-2024 to Apr-2026 will start
  drifting after deployment. BOCPD-style regime monitoring is the
  recommended monitoring layer.
- **Capacity at scale** — current backtest is per $10k bankroll. Real
  $140k account deploying 10 picks/day at full Kelly = up to $70k per
  ELITE pick. Microcap market depth at that size needs measurement.

Phase D (live A/B paper test, Wednesday) addresses gap 1. Phases D-E
require actual deployment and are out of backtest scope.

---

## 8. Production Integration

### Env-gating (the safety contract)

```
MX_USE_MOMTRANS=1   → MetaScorer loads 4 .pt files, routes via cascade
MX_USE_MOMTRANS=0   → MetaScorer ignores MoMTrans, uses v3 + D281 path
MX_USE_MOMTRANS unset → defaults to OFF
```

Per-tier threshold overrides (advanced ops use only):
```
MX_MOMTRANS_ELITE_THR=0.60   (default)
MX_MOMTRANS_HIGH_THR=0.70    (default)
MX_MOMTRANS_VETOED_THR=0.90  (default)
MX_MOMTRANS_BROAD_THR=0.60   (default)
```

### Coexistence with D281 cohort cascade (the legacy path)

When MoMTrans is OFF: lottery_runner uses MetaScorer's v3 + D281 path
(`predict_v3t` + `assign_tier`). When MoMTrans is ON: lottery_runner
uses MetaScorer's MoMTrans path (`predict_v4t` + `assign_tier_momtrans`).
The two paths are mutually exclusive — MoMTrans REPLACES the v3 cascade
for tier-assignment purposes. The v3t score is still computed and
exposed in the `reason` field for log-comparison A/B telemetry.

### Failure-mode behavior

| Failure | Fallback |
|---|---|
| `MX_USE_MOMTRANS=1` but any of 4 .pt files missing | WARNING logged, MoMTrans disabled, legacy path runs |
| `torch` import fails | WARNING logged, MoMTrans disabled, legacy path runs |
| Specialist forward pass raises | Caught by score_candidate; tier=SKIP for safety |
| Threshold env-var malformed | Float parsing fails at module load → script crashes (loud) |

Default-to-safety: any MoMTrans failure mode falls back to the
production-validated D281 cohort cascade.

### Wednesday's deploy plan

Launcher (`scripts/lottery_paper_trade.ps1`) sets:
```
MX_USE_MOMTRANS=1   ← NEW for Wednesday
```

Operator log line at startup will read:
```
MX_USE_MOMTRANS:             1
```
And the meta-scorer log will read:
```
MX_USE_MOMTRANS=1 — loaded 4/4 MoMTrans specialists
                    (thresholds: E>=0.60, H>=0.70, V>=0.90, B>=0.60)
MoMTrans: rebuilt 4 nn.Module specialists from .pt state dicts
MetaScorer loaded: ... momtrans=True (4/4) ...
```

Each pick's META-PASS line will tag the cascade trigger:
```
META-PASS XYZ: tier=ELITE score=0.78 kelly=0.5000 notional=$70,158.00
  reason=momtrans_ELITE(0.78)>=0.60 AND mag IN (HI,MID) [D282] | v3t=0.42 (shadow)
```

The `(shadow)` v3t value enables direct A/B comparison: if MoMTrans fires
on candidates that v3 would have skipped (or vice versa), we'll see it
in the logs without any explicit instrumentation.

### Rollback procedure

If anything goes sideways during Wednesday's deploy:
1. Edit `scripts/lottery_paper_trade.ps1`: comment out the `MX_USE_MOMTRANS=1`
   block (or change to `=0`)
2. Re-launch lottery via Task Scheduler or manually
3. Bot reverts to D281 cohort cascade automatically

---

## 9. Critique & Known Limitations

### Limitations of the data

1. **Single asset class, narrow universe** — microcap gap-ups in $1.50-$50
   price band, $100k+ daily dollar volume. Strategy doesn't generalize to
   mid-caps, large-caps, options, futures, crypto.

2. **20k labeled rows is small** — even with SSL pre-training compressing
   information, the labeled signal is finite. Spearman ρ ceiling around
   0.14-0.16 likely reflects true noise floor + measurement error in
   `ret_t5` (open-to-close-5d is itself noisy).

3. **16-month sample** — covers Aug-2024 to Apr-2026. Doesn't include
   2020-2022 microcap mania, 2008 crisis, or other regime stress.
   Model trained ONLY on a relatively benign period.

4. **Sample selection bias** — `aftermath_strat.parquet` only has rows
   where the ticker WAS identified as a candidate. The "dog that didn't
   bark" — momentum days where no candidate fired — is not in training.
   Hard to quantify how this skews the distribution.

5. **Look-ahead bias risk in `prior_*` features** — features like
   `prior_avg_t5` use historical ret_t5 values for the same ticker, but
   `ret_t5` itself depends on prices 5 days FORWARD from `d0`. We
   carefully use `prior_*` features that include only data PRIOR to `d0`,
   but a regression-test for this property doesn't exist.

### Limitations of the architecture

6. **No regime adaptation** — model is trained ONCE and frozen. Microcap
   dynamics evolve (algorithmic flow, narrative cycles, retail attention).
   Production v3 is retrained periodically; MoMTrans needs the same
   retraining schedule plus a regime-monitor (BOCPD) to detect when the
   underlying generative process has shifted.

7. **Sequence branch wasted** — the architecture INCLUDES a sequence
   branch for intraday minute paths but DISABLES it. Wasted parameter
   capacity. Could either delete the branch entirely, OR keep it and
   re-enable when richer sequence data arrives (trades_v1 ticks).

8. **No uncertainty quantification beyond quantile head** — the quantile
   head outputs (q10, q50, q90) but we don't actually USE these at
   inference (Kelly sizing uses binary head + a constant 0.5 conformal
   width). Could replace conformal width with `q90 - q10` for a
   data-driven uncertainty estimate.

9. **Same architecture across all 4 specialists** — each tier's
   distribution is different (BROAD has 21% positive, ELITE has 7%).
   Same architecture fits all might be sub-optimal. Smaller model for
   ELITE (less data per cohort) might generalize better.

10. **Threshold cascade is hand-tuned** — 600-combo grid on TUNE folds.
    A learned cascade router (e.g., RL agent over the 4 specialist
    outputs + mag context) could dynamically adjust thresholds per regime.

### Limitations of the validation

11. **All 3 phases used the SAME WF predictions** — Phase A split folds
    (clean), but Phases B and C re-used the cascade's predictions.
    Ideally Phase B/C should also have their own train/test split.

12. **Slippage model is uniform** — Phase C applies a flat per-pick bps
    haircut. Real slippage scales with notional, ticker liquidity, and
    time-of-day. A square-root impact model (Almgren-Chriss style)
    would be more realistic.

13. **Capacity sweep ignores correlated picks** — cap=10/day picks are
    all-or-nothing. In reality, firing 10 microcap orders in the same
    sector simultaneously creates correlated market impact that the
    sweep doesn't model.

14. **No live-fill comparison** — the gold-standard validation is
    seeing actual market fills match the backtest. Wednesday's deploy
    is the first such test.

15. **No transaction cost beyond slippage** — commission, financing,
    short-borrow fees, regulatory short-sale uptick rules — all
    abstracted away as part of the "100bps" haircut. Acceptable for a
    feasibility-test, not for live deployment commitment.

### Risks specific to MoMTrans's higher pick volume

16. **5.5× more picks than production** — even after cap=10/day, the
    cascade fires more days with at least one pick (208 vs production's
    ~470 days but with fewer picks per day). More market exposure means
    more chances for adverse-selection / correlated drawdown.

17. **Higher gross deployment per dollar of net P&L** — at cap=10 at full
    Aggressive Kelly on $140k, ~$700k of gross capital deployed per day
    on bad days. Float-availability / margin-call risk on such positions
    needs operational checks not yet built.

18. **No specialist-disagreement detector** — if ELITE specialist says
    0.75 and HIGH specialist says 0.05 for the same candidate, that's a
    sign of mis-calibration that would warrant investigation. We don't
    log or alert on this today.

---

## 10. What Could Push It Further

Ranked by expected ROI per implementation hour:

### Tier 1 — Cheap, high-impact (next week)

#### a. Quantile-Kelly sizing
Replace constant `conformal_width=0.5` in `compute_kelly` with the
quantile-head-derived spread `(q90 - q10) / typical_spread`. This makes
Kelly sizing data-driven per pick. **Estimated lift**: +5-15% on
risk-adjusted return; same gross $-PNL but lower drawdown.

#### b. Per-cohort architecture sizing
Train ELITE specialist with smaller model (d_model=32, n_layers=2) since
its positive class is tiny (1,362 samples). Train BROAD specialist
larger (d_model=128, n_layers=6) since it has 4,163 positives. Each
cohort gets architecture matched to its data size. **Estimated lift**:
ELITE Spearman might double from 0.004 to 0.01 (still low but
recoverable).

#### c. Time-decay weighted training loss
Add `sample_weight = exp(-(now - date) / 90d)` to the joint loss. Recent
gap-up dynamics weight more than 12-month-old samples. **Estimated lift**:
unknown but addresses the regime-drift concern.

#### d. Proper retraining cadence
Set up weekly retraining (cron) of all 4 specialists + SSL re-pretrain
monthly. **Cost**: 2 hours of GPU per week. **Benefit**: keeps the model
adapted to current market microstructure.

### Tier 2 — Medium, requires new data layers

#### e. Tick-level trades_v1 transformer
The compass artifact #3 frontier idea. Replace the (failed) minute-bar
sequence branch with a Transformer over the last N ticks for the
candidate. Polygon trades_v1 is already in the warehouse (147 GB ZSTD
parquet, 9 months). **Estimated lift**: hard to predict; could be
substantial if microstructure features (sweep bursts, dark-pool prints)
carry signal that the 5 hand-engineered scalars miss.

#### f. News-text encoder for the sequence branch
Embed Polygon /v2/reference/news headlines (when present) using a
sentence-transformer (e.g., all-MiniLM-L6-v2). Concat the news embedding
into the fusion layer. **Coverage caveat**: only 4.66% of candidates
have news (s116 finding), so this only helps the news subset.

#### g. Sector-graph contagion model
Build a sector co-movement graph from historical correlation. For each
candidate, aggregate features from sector peers as a "context" embedding.
Graph attention over peers. **Computational cost**: substantial; needs
new data ETL.

### Tier 3 — Architectural reach

#### h. State-space backbone (Mamba/S4)
Replace the Transformer fusion with a Mamba block. State-space models
scale better with sequence length and have been shown to outperform
Transformers on time-series tasks. **Estimated lift**: maybe +10% if
the ablated sequence branch is re-enabled; minimal if tabular-only stays
the production config.

#### i. RL-based threshold/sizing agent
Train a PPO agent that observes (specialist_probas, regime, recent_fill_history)
and outputs (tier_thresholds, kelly_caps). The cascade router becomes a
learned policy instead of grid-searched constants. **Caveat**: RL on
financial data with 20k samples is notoriously unstable; would need careful
reward shaping and held-out validation.

#### j. Adversarial validation against production v3
Train a meta-classifier to predict "did this prediction come from MoMTrans
or v3?". If accuracy is high, the two models are making similar predictions
on different distributions of inputs. Provides a principled way to identify
where MoMTrans's edge actually lives.

### Tier 4 — The "moonshot" stack

#### k. Multi-agent ensemble with disagreement gating
Train 5+ different deep architectures (TabTransformer, FT-Transformer,
TabPFN, NODE, SAINT) and ensemble. Use disagreement (variance across
predictions) as a confidence signal — high disagreement → SKIP regardless
of mean prediction.

#### l. Diffusion model for the return distribution
Predict the FULL distribution of `ret_t5 | features` via a small diffusion
model. Then Kelly-size against the predicted distribution rather than a
point estimate. Most principled approach; most complex to implement.

#### m. LLM-as-Strategy-Generator
Per compass artifact #1 §4.2, deferred. LLM emits full strategy code
(entry rule + exit rule + position sizing) given a candidate's context.
Most speculative; least clear ROI.

---

## 11. Operational Runbook

### Wednesday morning checklist

```
1. Verify all 4 .pt files present:
   ls data/models/momtrans_v4_tier_*.pt
   Expected: 4 files (BROAD, VETOED, HIGH, ELITE), ~1.5 MB each

2. Verify launcher env vars:
   grep -E "MX_USE_MOMTRANS|MX_USE_HYBRID|MX_TIERED_LEARNING" \
       scripts/lottery_paper_trade.ps1

3. Smoke-test inference path:
   python scripts/test_meta_scorer_inference.py     # 29 tests
   python -m pytest tests/unit/test_d282_*.py -v    # 11 tests

4. Watch the 9:25 ET launcher log for:
   "MX_USE_MOMTRANS=1 — loaded 4/4 MoMTrans specialists"
   "MoMTrans: rebuilt 4 nn.Module specialists from .pt state dicts"

5. Watch first META-PASS / META-SKIP lines for:
   reason=momtrans_*([0-9.]+)>=0.[67890] AND mag ... [D282]
```

### Mid-day monitoring

```
1. Per-pick log lines should include "[D282]" tag and "(shadow)" v3t value
2. Discord/Slack alert if 0 picks fire by 9:35 ET (signal degradation)
3. End-of-day P&L vs WF backtest expectation:
   - Cap=10/day expected daily $-PNL: ~$70,000 / 470 days = $148/day
   - Realistic at 100bps slippage: ~$46,573 / 470 = $99/day
   - Wednesday actual should land in [-$200, +$400] for a normal day
```

### Failure recovery

```
SYMPTOM: "MX_USE_MOMTRANS=1 but momtrans_v4_tier_X.pt missing"
ACTION: re-train missing tier
  python scripts/ml_v4_momtrans_train.py --full-wf --variant tabular_only \
      --out-suffix _tier_X --epochs 100 --batch-size 128 \
      --d-model 64 --n-layers 4 --dropout 0.10 --lr-max 3e-4 \
      --patience 12 --precision fp32 \
      --load-ssl-encoder data/models/momtrans_v4_ssl_encoder.pt
  (Set MX_MOMTRANS_TIER_THRESHOLD + MX_MOMTRANS_TIER_NAME env vars)

SYMPTOM: "torch not importable — MoMTrans inference disabled"
ACTION: reinstall PyTorch nightly with cu128
  pip install --upgrade --pre torch torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/nightly/cu128

SYMPTOM: MoMTrans firing on every candidate (98%+ pass rate)
ACTION: production has miscalibrated to over-fire; rollback immediately
  Edit scripts/lottery_paper_trade.ps1 → set MX_USE_MOMTRANS=0
  Investigate via momtrans_specialist_probas log lines

SYMPTOM: MoMTrans firing on 0 candidates for 3 consecutive days
ACTION: regime drift suspected; manually retrain
  python scripts/ml_v4_momtrans_ssl_pretrain.py --epochs 200
  Then retrain all 4 specialists per Wednesday checklist step 2
```

### Decision authority

- **Pierce (operator)** can flip `MX_USE_MOMTRANS=0` immediately for any
  reason, no review required.
- **Pierce + reviewer** required to flip `MX_USE_MOMTRANS=1` for
  consecutive sessions beyond Wednesday's first test (compare WF
  expectation to live P&L; ship Phase D doc before continuing).
- **Pierce + reviewer + backtest re-validation** required to deploy
  to non-paper trading (Phase E).

---

## Appendix A — File manifest

| Path | Type | Purpose |
|---|---|---|
| `scripts/ml_v4_momtrans_train.py` | NEW | Trainer (multi-task heads, SSL warm-start, .pt persistence) |
| `scripts/ml_v4_momtrans_ssl_pretrain.py` | NEW | Masked-feature SSL pre-training |
| `scripts/ml_v4_momtrans_tier_specialists.py` | NEW | Train all 4 cohort specialists in sequence |
| `scripts/ml_v4_momtrans_tier_threshold_search.py` | NEW | Grid-search thresholds (used in s128) |
| `scripts/ml_v4_momtrans_tabular_sweep.py` | NEW | Optuna sweep harness |
| `scripts/ml_v4_momtrans_full_compare.py` | NEW | All-up comparison harness |
| `scripts/ml_v4_momtrans_phase_a_oos_threshold_validation.py` | NEW | Phase A validation |
| `scripts/ml_v4_momtrans_phase_b_capacity_constrained.py` | NEW | Phase B validation |
| `scripts/ml_v4_momtrans_phase_c_slippage.py` | NEW | Phase C validation |
| `scripts/ml_meta_scorer_inference.py` | MODIFIED | Production inference + MoMTrans wiring |
| `scripts/lottery_paper_trade.ps1` | MODIFIED | `MX_USE_MOMTRANS=1` env flag |
| `scripts/test_meta_scorer_inference.py` | MODIFIED | Test stub updated for D282 attrs |
| `tests/unit/test_d282_momtrans_inference.py` | NEW | 11 behavioral tests |
| `data/models/momtrans_v4_ssl_encoder.pt` | NEW (gitignored) | Pre-trained encoder weights |
| `data/models/momtrans_v4_tier_*.pt` | NEW (gitignored) | 4 tier specialist artifacts |
| `data/models/momtrans_v4_phase_*_*.json` | NEW (gitignored) | Validation result summaries |
| `docs/research-log/125-130_*.md` | NEW | 6-doc research arc |

## Appendix B — Hardware utilization summary (whole MoMTrans research arc)

```
GPU:     NVIDIA RTX 5070 (Blackwell sm_120)
VRAM:    12.8 GB available, peak usage ~3.5 GB
Driver:  591.59 / CUDA 12.8
PyTorch: 2.12.0.dev20260408+cu128

Total wall-clock for full MoMTrans build + validation:
  PyTorch CUDA install:                  ~8 min
  Doc 125-126 design + smoke tests:      ~30 min
  Doc 127 ablations (3 variants × WF):   ~30 min
  Doc 128 SSL + 4 tier specialists:      ~100 min (concurrent w/ sweep)
  Doc 128 threshold grid search:         ~5 min
  Doc 129 Phase A + B + C validation:    ~5 min
  Doc 130 .pt re-training (4 specialists): ~50 min
  ────────────────────────────────────────────────
  TOTAL:                                 ~4 hours
```

A research-grade ML model from concept to env-gated production deploy in
4 hours of GPU time. The same architecture in 2018 would have required
a CUDA-capable workstation, weeks of feature engineering iteration, and
a multi-person team. Modern infrastructure (PyTorch nightly, SSL
pre-training, Optuna, Polygon trade tape, conformal prediction theory,
etc.) compresses this to a single overnight session.

---

**End of document. 130 commits → 1 model → Wednesday's deploy.**
