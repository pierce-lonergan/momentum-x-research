# 131 — MoMTrans v5 Enhancement Roadmap

**Session date:** 2026-05-06 (overnight, post-Wed-deploy prep)
**Branch:** develop
**Predecessors:** [130 v4 architecture complete](130_momtrans_v4_architecture_complete.md)
**Source research:** [MoMTrans v4 — Frontier Techniques for Small-Data Tabular Transformers](MoMTrans v4 — Frontier Techniques for Small-Data Tabular Transformers in Microcap Gap-Up Prediction.md)
**Status:** RESEARCH PLAN — implementations begin this session

---

## TL;DR

The frontier-techniques research identifies **5 high-leverage enhancements** for our 500K-param TabTransformer cascade. v4 ships at +47% per-pick edge over production v3 (+$46,573 lift @ 100bps slippage); v5 targets **+0.025-0.040 Spearman ρ on top of v4's 0.141** by stacking:

1. **CORN ordinal head** (replace 4 independent BCE heads) — borrows ELITE statistical strength from BROAD (84× more positive class)
2. **Spearman soft-rank loss** (torchsort) — directly optimizes the ranking metric we trade on
3. **Muon optimizer + EMA weights** — Yandex 2026 benchmark shows consistent gain over AdamW on tabular drift
4. **Mixup augmentation** — calibrated for imbalanced regression; free regularization
5. **TabPFNv2 distillation** — Hollmann 2025 in-context learner as teacher for soft-label distillation

The roadmap is staged so each Tier S item can ship independently, with Tier A items (TabM trunk, T-JEPA pretraining, ACI conformal Kelly, SAM) layered on after Tier S validates.

**Key reality check vs research doc:** the doc cited "60k unlabeled Polygon candidates" but our actual unlabeled pool is essentially zero — `aftermath_strat.parquet` has 20,029 labeled rows; the catalogs have 20,795-20,886 rows that are nearly identical sets. CAST-style self-training on a separate unlabeled corpus is out of scope. TabPFN-as-teacher still applies via self-distillation on the 20k labeled set.

---

## 1. The 5 Tier S items (this session + this week)

### S1. CORN ordinal head (Shi et al., arXiv:2111.08851)

**Problem.** v4 trains 4 independent binary specialists, each on a different
return threshold. The architecture WASTES the nested-label structure:
P(R≥40%) ≤ P(R≥25%) ≤ P(R≥15%) ≤ P(R≥10%) is a mathematical identity that
the 4 separate models can violate.

**The fix.** Single TabTransformer trunk with one CORN head outputting K-1
chained conditional probabilities. P(R≥10%) is shared across all 4 ordinal
levels; the model only learns the conditional refinement P(R≥40% | R≥25%),
P(R≥25% | R≥15%), etc. This guarantees rank consistency AND borrows ELITE's
85 positives/fold from BROAD's 4,163.

**Implementation.** `coral-pytorch` package or custom (~50 LOC). The
binary head `Linear(64, 1)` becomes `Linear(64, K-1) = Linear(64, 3)`
followed by chained sigmoid logic. Loss is sum of K-1 BCE terms with
mask: BCE_k is only non-zero for samples that reach level k.

**Expected lift.** +0.005-0.015 Spearman per the research doc.
Disproportionate help on ELITE.

**Cost.** 1 day implementation + 30 min training time.

### S2. Spearman soft-rank loss (Blondel et al., torchsort)

**Problem.** v4 trains BCE on binary labels. The strategy trades top-K
daily picks ranked by predicted probability. Spearman ρ is the metric
we measure. **Training on BCE optimizes the wrong objective.**

**The fix.** Add a Spearman soft-rank loss term using `torchsort`:

```python
import torchsort
def spearman_loss(pred, target, regularization_strength=1.0):
    pred_rank = torchsort.soft_rank(pred, regularization_strength=regularization_strength)
    target_rank = torchsort.soft_rank(target)
    return -corr(pred_rank, target_rank)
```

Per-day groupings (group by `d0`) so ranking is over the daily candidate
cohort — directly aligned with the strategy's "pick top-K today" semantics.

**Expected lift.** +0.005-0.015 Spearman. Stock Ranking Loss benchmark
(arXiv:2510.14156, ACM CIKM 2025) directly evaluates pointwise/pairwise/
listwise losses for transformer S&P 500 selection: listwise wins.

**Cost.** 2-3 days (need `pip install torchsort`, batch-by-day data
loader, validate gradient flow with the new loss).

### S3. Muon optimizer + EMA weights (Yandex 2026 benchmark)

**Problem.** v4 uses AdamW. Yandex's 2026 tabular optimizer benchmark
(arXiv:2604.15297, 15 optimizers × 17 datasets including TabReD) shows
**Muon consistently outperforms AdamW for tabular MLPs**.

**The fix.** Replace `AdamW` with `Muon` for 2D matrix params, AdamW
for 1D bias/LN params (Muon is matrix-only). Add EMA-of-weights with
decay=0.999 for free distribution-shift robustness.

**Implementation.** `pip install git+https://github.com/KellerJordan/Muon`,
~30 LOC swap. d_model=64, n_layers=4 → most weights are 64×64 matrices,
exactly Muon's sweet spot.

**Expected lift.** +0.003-0.01 Spearman. Free wallclock cost (~3% overhead).

**Cost.** 1 hour.

### S4. Mixup augmentation (Zhang et al. 2018; Calibrated Mixup 2025)

**Problem.** v4 has no augmentation. With ~85 ELITE positives/fold,
the rare class is severely under-sampled.

**The fix.** Mixup with α=0.4 in feature space + linear-interpolation
of CORN ordinal labels in logit space. Calibrated Mixup for Imbalanced
Regression (ScienceDirect 2025) reports 10-20% MAE reduction
specifically for our regime.

**Expected lift.** +0.002-0.008 Spearman.

**Cost.** 1 hour.

### S5. TabPFNv2 distillation (Hollmann et al., Nature 637:319, 2025)

**Problem.** v4 has 500K params trained from scratch on 20k rows.
TabPFNv2 was pretrained on millions of synthetic SCM tasks and
specifically targets the small-data regime. v2.5 (Nov 2025) extends
to 50K rows × 2K features.

**The fix.** Use TabPFNv2 as a TEACHER:
1. Run TabPFNv2 inference over the full 20k labeled set (in-context
   prediction; TabPFNv2 needs ~12 GB VRAM at this context, fits
   tight on RTX 5070).
2. Save TabPFNv2's soft probability outputs as "dark-knowledge labels".
3. Train MoMTrans v5 with mixed loss:
   `L = α·CE_hard + (1-α)·KL_soft + β·SpearmanRank`
   with temperature τ=2-4 on the soft labels.

**Expected lift.** +0.01-0.02 Spearman. Per Hoo et al. 2025 (arXiv:2501.02945),
TabPFNv2 is a strong time-series forecaster when windows are encoded as
tabular rows — directly relevant to our 5-day-forward setup.

**Cost.** 1 week. Requires careful walk-forward hygiene to avoid
leaking future labels into TabPFNv2's in-context prompt.

---

## 2. Tier A items (after Tier S validates, weeks 2-4)

### A1. TabM trunk (Gorishniy et al., ICLR 2025)

Replace 4 separate TabTransformer specialists with single TabM trunk
+ multi-task ordinal heads. Won UM Kaggle, top-3/4/5 in CIBMTR. TabM
beats FT-Transformer, TabR, MLP+, GBDTs on TabReD specifically (the
benchmark closest to our regime).

**Cost.** 1 week. **Expected.** +0.005-0.015.

### A2. T-JEPA pretraining (Thimonier et al., arXiv:2410.05016, ICLR 2025)

Replace our 200-epoch masked-feature reconstruction with JEPA-style
predict-latent-from-feature-subset pretraining. No augmentations
needed. Reports consistent improvements over SCARF/VIME.

**Cost.** 1 week. **Expected.** +0.005-0.015.

### A3. Adaptive Conformal Inference (Gibbs & Candès 2021; refined 2024)

The Kelly criterion is brutally unforgiving of probability miscalibration.
Wrap MoMTrans with split-conformal-with-adaptive-α at inference. Use the
prediction-interval width to GATE Kelly sizing — only size up when interval
is tight. AEnbMIMOCQR (Sousa et al., Neurocomputing 2024) provides finite-sample
coverage even when not exchangeable.

**Cost.** 3-5 days. **Expected.** Sharpe improvement, lower drawdown.

### A4. SAM at 2× compute (Foret et al., ICLR 2021)

Sharpness-Aware Minimization doubles per-step cost. Documented to help
in low-data regimes. Our 9-min/16-fold budget can absorb 2× → 18 min.

**Cost.** 1 day. **Expected.** +0.003-0.01.

### A5. DoRA adapters (Liu et al., ICML 2024)

Train shared TabM trunk via T-JEPA, attach DoRA adapters per ordinal
threshold (rank=4 fits all 4 in <50 MB). Alternative architecture if
CORN doesn't get sufficient lift.

**Cost.** 1 week. **Expected.** matches CORN +0.005-0.015 but easier
to compose with future per-tier specializations.

---

## 3. Tier D — TRAPS (the research doc explicitly calls these out)

These look attractive but have published negative evidence at our scale:

| Trap | Why it fails for us |
|---|---|
| FP8/INT8 training | We're at 3.5/12 GB VRAM; FP8 saves memory we don't need + adds outlier-collapse risk |
| MoE routing | DeepSeek-V3 explicitly justifies MoE for 671B params with cross-node bottlenecks; irrelevant at 500K |
| Mamba/KAN | TabReD benchmark + KAN-on-tabular benchmark (2024) show no consistent advantage over TabM/MLP |
| Sequence branches over minute bars | Already empirically failed in doc 127 ablation (ρ 0.036 vs 0.141) |
| Larger d_model / n_layers | doc 127 ablation: bigger model was the WORST result |
| s1-style test-time compute | No analog for tabular regression |
| Sophia / Lion | No significant edge over AdamW at <0.5B params per Zhao et al. 2024 |

---

## 4. The 5 highest-leverage research bets (per the source doc)

If we wanted to publish original work, the source research doc identifies
these as **genuinely novel** intersections in the literature:

1. **TabPFNv2 → MoMTrans distillation pipeline.** No published microcap
   intraday deployment.
2. **CORN ordinal head + per-day Spearman soft-rank listwise loss.**
   Combines 2021-2024 techniques in a way exactly matched to our
   nested-threshold + top-K-trade structure.
3. **TabM trunk + DoRA adapters + Muon + EMA.** All four are 2024-2025
   SOTA on tabular per the Yandex 2026 benchmark. Compose without conflict.
4. **Adaptive Conformal Inference + SWAG for Kelly probability quality.**
   Genuinely rigorous coverage under non-exchangeable regime shifts. The
   conformal-prediction and Kelly-criterion literatures are largely
   disjoint.
5. **CAST self-training (NOT applicable here — we don't have the 60k
   unlabeled pool).**

---

## 5. The 0.14-0.16 Spearman ceiling — feature vs model question

The research doc raises the **most important strategic question**: is the
0.14-0.16 Spearman ceiling we observe in v4 a:

- **Bayes ceiling for the 54-feature universe** → no model can break it;
  feature engineering is the only path forward
- **Model-class ceiling** → TabPFNv2-class foundation models could break
  through

**Decisive experiment.** Train TabPFNv2 directly on the 54-feature × 20k-row
data (no distillation, no MoMTrans). Measure its WF Spearman. If ≥0.18,
it's a model-class ceiling and we should chase TabPFN-class architectures.
If ~0.14-0.16, it's a Bayes ceiling and we need NEW FEATURES (tick trades,
NBBO microstructure, news embeddings, sector co-movement).

**This experiment runs as part of S5 (TabPFN distillation).**

---

## 6. Implementation order (this session vs follow-on)

### Tonight (this session)

- ☐ **S3** Muon + EMA optimizer (1 hour, free)
- ☐ **S4** Mixup augmentation (1 hour, free)
- ☐ **S1** CORN ordinal head (1 day, big win on ELITE)
- ☐ Train v5-stage1 (Muon + EMA + Mixup + CORN) and validate vs v4 baseline
- ☐ Document results

Target tonight: confirm S1+S3+S4 stack. Expected lift: +0.010-0.030
Spearman over v4's 0.141.

### This week

- ☐ **S2** Spearman soft-rank loss (2-3 days)
- ☐ **S5** TabPFNv2 distillation (1 week — also resolves the
  feature-vs-model ceiling question)

### Next week (Tier A)

- ☐ A1-A5 in priority order, validating each against v5 baseline.

---

## 7. Production safety contract

ALL v5 work runs in research mode. The launcher's `MX_USE_MOMTRANS=1`
loads the v4 .pt files. v5 will have its own `.pt` artifact set
(e.g., `momtrans_v5_corn.pt`) and its own env flag (`MX_USE_MOMTRANS_V5=1`,
default OFF). Same Phase-A/B/C validation gates as v4 before any
production rollout.

The v4 production deployment (Wednesday 9:25 ET) is unaffected.

---

## 8. Files this enhancement effort will produce

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_v4_momtrans_train.py` | MODIFIED | + Muon, EMA, Mixup, CORN as opt-in flags |
| `scripts/ml_v5_train_corn.py` | NEW | Top-level entry point for v5 training |
| `scripts/ml_v5_corn_head.py` | NEW | CORN head + chained-conditional logic |
| `scripts/ml_v5_spearman_loss.py` | NEW | Per-day grouped Spearman loss with torchsort |
| `scripts/ml_v5_tabpfn_distill.py` | NEW (S5) | TabPFNv2 teacher → MoMTrans student |
| `scripts/ml_v5_validate.py` | NEW | Phase A/B/C re-runs on v5 vs v4 |
| `tests/unit/test_d283_corn_head.py` | NEW | CORN head behavioral tests |
| `tests/unit/test_d284_spearman_loss.py` | NEW | Spearman loss regression test |
| `data/models/momtrans_v5_*.pt` | NEW (gitignored) | v5 specialist artifacts |
| `docs/research-log/131_momtrans_v5_enhancement_roadmap.md` | NEW (this doc) | this |

---

## 9. Decision authority for v5 production rollout

Same as v4: env-gated default OFF, Phase A/B/C must pass on v5 before
any production rollout. v5 ships behind `MX_USE_MOMTRANS_V5=1` (parallel
to v4's `MX_USE_MOMTRANS=1` — not replacing). The two can run side-by-side
for direct A/B comparison via the existing `(shadow)` v3t-style telemetry.

D281 cohort cascade remains the ALWAYS-AVAILABLE rollback path (pure
XGBoost, no PyTorch dependency).
