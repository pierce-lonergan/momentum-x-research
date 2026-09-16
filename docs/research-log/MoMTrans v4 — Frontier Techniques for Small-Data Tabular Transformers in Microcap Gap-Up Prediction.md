# MoMTrans v4 — Frontier Techniques for Small-Data Tabular Transformers in Microcap Gap-Up Prediction

## TL;DR

- **The three highest-leverage bets are: (1) replace independent BCE heads with a CORN/CORAL-style ordinal cascade plus a Spearman-ρ surrogate (torchsort) listwise loss to directly optimize the metric you're measured on; (2) treat the 60k unlabeled Polygon candidates as a TabPFNv2-distillation source — use TabPFNv2 (Hollmann et al., Nature 2025) as a teacher to generate soft labels, then distill into MoMTrans, which simultaneously addresses the 20k label ceiling, the ELITE data-starvation problem, and provides a calibrated probability for Kelly sizing; (3) replace the 4 separate specialists with a single TabM-style parameter-efficient ensemble (Gorishniy et al., ICLR 2025) trained with Muon optimizer and EMA weights — the ICLR-2025/2026 tabular-MLP optimizer benchmark from Yandex shows Muon + EMA dominates AdamW on real-world drifting tabular benchmarks (TabReD), and TabM was the winning architecture in two recent Kaggle competitions.**

- **Several "obvious" frontier ideas are likely traps for this specific system: full DeepSeek-V3-style FP8 + MoE + MLA stack adds engineering overhead that won't pay off at ~500K params on a single Blackwell GPU; KAN architectures show no consistent advantage over MLPs on tabular and cost more compute; bigger transformers were already proven to overfit at 20k rows; raw OHLCV minute-bar sequence branches were empirically harmful and revisiting them via Mamba/JEPA is unlikely to break the 0.14–0.16 Spearman ceiling without new feature engineering.**

- **Genuine open research frontier for this exact problem: there is essentially no published work on (small-sample × strictly-nested ordinal labels × ranking-metric optimization × intraday microcap × single-GPU). The combination of CORN with NDCG@K surrogates under per-day groupings, plus conformal-prediction-with-adaptive-coverage (CQR/ACI 2024) for Kelly-quality probability calibration on regime shifts, plus pseudo-labeling on the 60k Polygon candidates with cluster-aware regularization (CAST 2024), is novel enough that careful empirical work would constitute publishable original research, not just application.**

---

## Key Findings (read this if nothing else)

1. **The "tabular-only beat the full architecture" observation matches the published literature.** TabM (ICLR 2025) and the Yandex 2026 optimizer benchmark explicitly find that for tabular data with hundreds of features and time-related drift (TabReD), parameter-efficient MLP ensembles plus Muon outperform attention-heavy architectures. Your sequence branch hurting is consistent with Grinsztajn et al. (2022) and the broader finding that quadratic-attention models do not earn their parameters on small tabular datasets.

2. **TabPFNv2 (Hollmann et al., Nature 637:319–326, 2025) is the single biggest external lever available.** It was specifically designed for the small-data regime (≤10K rows in v2; up to 50K in TabPFN-2.5 released Nov 2025). It can serve as: a teacher for distillation, a feature extractor (its embeddings can be frozen and reused), a few-shot baseline that already beats tuned XGBoost, and — per Hoo et al. 2025 (arXiv:2501.02945) — a strong forecaster when you reformulate intraday windows as tabular features. Crucially, TabPFNv2 is a Bayesian-style in-context learner so its predictive distribution is well-calibrated, which directly supports Kelly sizing.

3. **The ranking-metric mismatch is your biggest controllable problem.** You measure Spearman ρ and trade top-K daily picks but train BCE. Pobrotyn & Białobrzeski's NeuralNDCG (arXiv:2102.07831), Blondel et al.'s torchsort soft-rank Spearman surrogate, and the recent Stock Ranking Loss benchmark (arXiv:2510.14156, ACM CIKM 2025) — which directly evaluates pointwise/pairwise/listwise losses for transformer-based S&P 500 ranking — show consistent gains from listwise losses over BCE/MSE for top-K equity selection.

4. **Ordinal nested labels are being treated as four independent binary tasks. This wastes signal.** CORN (Shi et al., arXiv:2111.08851) and continuously-generalized ordinal logistic (Lu et al. 2022) provide rank-consistent multi-threshold heads with shared backbone — directly applicable to BROAD≥10% / VETOED≥15% / HIGH≥25% / ELITE≥40%. UNICORNN (OpenReview 2024) adds calibrated unimodal output. This single change should disproportionately help ELITE because it borrows statistical strength from BROAD/VETOED.

5. **The 60k unlabeled Polygon candidates are unused capital.** SCARF (Bahri et al., ICLR 2022) for contrastive pretraining, T-JEPA (Thimonier et al., NeurIPS-track ICLR 2025) for augmentation-free JEPA-style pretraining, plus CAST (Kim et al., arXiv:2310.06380) cluster-aware self-training and Curriculum Pseudo-Labeling (Kim et al., arXiv:2302.14013) all give documented gains on small-tabular semi-supervised problems.

6. **The compute headroom is real but trap-laden.** You have 8.5 GB of unused VRAM and 55–65% idle GPU time on a Blackwell sm_120. The right uses are: deep ensembles / SWAG (real Bayesian Kelly support), Sharpness-Aware Minimization (which is ~2× per-step but documented to help in low-data regimes), and Muon (slightly slower but consistently better on tabular per arXiv:2604.15297). The wrong uses are FP8 stunts and MoE — the DeepSeek-V3 paper (arXiv:2505.09343) explicitly justifies these for 671B-parameter models with cross-node communication issues that do not exist at your scale.

7. **Conformal prediction with adaptive coverage is the rigorous answer to "Kelly needs probability quality."** Adaptive Conformal Inference (Gibbs & Candès 2021, refined 2024), Adaptive Feature-OCP (arXiv:2511.15838, 2025), and ensemble-batch MIMO-CQR (Sousa et al., Neurocomputing 2024) provide *finite-sample* coverage under the non-exchangeable, regime-shifting conditions of microcap intraday data. This is significantly more honest than MC-Dropout or last-layer Laplace for actual position sizing.

---

## Theme 1 — Small-Data Tabular Deep Learning

### TabPFNv2 (Hollmann et al., Nature 637:319–326, 2025) — arXiv-mirrored at openreview/2502.17361

**Core idea.** In-context learner pretrained on millions of synthetic SCM-generated tabular tasks. At inference, the entire labeled training set is concatenated as the prompt. v2 supports regression and increased context; v2.5 (released 6 Nov 2025) extends to 50K rows × 2K features. Wikipedia/PriorLabs confirms commercial maturity.

**Empirical evidence on your regime.** Ye et al. (arXiv:2502.17361, "A Closer Look at TabPFN v2") show TabPFNv2 dominates on small/medium tabular across 300+ datasets, including beating tuned CatBoost. Hoo et al. (arXiv:2501.02945, 2025) explicitly demonstrate TabPFNv2 as a strong time-series forecaster when windows are encoded as tabular rows — directly relevant to your 5-day forward return setup. Liu & Ye 2025 and Thomas et al. 2024 (LoCalPFN, arXiv:2406.05207, NeurIPS 2024) show fine-tuning and retrieval-augmented adaptation work.

**Honest assessment for MoMTrans.** Almost certainly helps as a *teacher* and possibly as a *feature extractor*. You will not run it as your live model (TabPFNv2 inference cost grows with context and 20K rows is at its comfort edge), but distilling its soft-label predictions into MoMTrans's 500K-param TabTransformer is a near-pure win. **GPU memory:** TabPFNv2 inference at 20K context fits on 12 GB but is tight; consider offline batched inference. **Implementation complexity:** Low (PriorLabs Python package, drop-in sklearn API).

**Gap.** No published deployment in microcap intraday trading. Reproducibility critique: Zhang et al. (arXiv:2505.20003, May 2025) "TabPFN: One Model to Rule Them All?" provides Bayesian-inference-style critique and notes some published claims do not replicate cleanly on certain regression metrics.

### TabM (Gorishniy et al., ICLR 2025; arXiv:2410.24210)

**Core idea.** A single MLP that simulates an ensemble of MLPs via BatchEnsemble-style weight sharing — diverse predictions at MLP cost. ICLR 2025 paper, plus the TabReD benchmark companion which specifically targets *real-world tabular with time-related distribution drift* (the closest published analog to your microcap problem).

**Evidence.** Won the UM Kaggle competition and reached top-3/top-4/top-5 in CIBMTR, with one solution placing 25/3300+ using *only* TabM. Beats FT-Transformer, TabR, MLP+, and GBDTs on TabReD. Currently the paretofrontier for tabular DL in the drift-rich regime.

**Assessment.** Strongly recommended replacement for the 4 independent TabTransformer specialists. Use a single TabM trunk with multi-task ordinal heads (CORN). **VRAM:** under 1 GB. **Complexity:** medium (official `tabm` PyPI package).

### TabR (Gorishniy et al., ICLR 2024; arXiv:2307.14338)

Retrieval-augmented MLP with custom kNN-attention component. Useful for "find-similar-historical-setup" inference but limited by needing all training tickers in memory. Less relevant than TabM for your use case unless you build a setup-similarity retrieval system.

### ExcelFormer / Trompt / FT-Transformer

ExcelFormer (Chen et al., arXiv:2301.02819) introduced Semi-Permeable Attention to constrain feature-interaction pathways under data scarcity, claiming GBDT-beating results. Trompt (Chen et al., 2023; ICML 2023) uses prompt-style learnable per-feature embeddings. Both are reasonable, but the 2025 evidence (TabM, TabReD) suggests these attention-heavy models do *not* dominate on drifting real-world tabular — which matches your own empirical result.

### Modern benchmarks

- **TabReD** (Rubachev et al., 2024–2025) — the only public benchmark targeting *temporal distribution drift* in tabular ML. Conclusion: TabM and properly-tuned MLP-based models lead.
- **Gorishniy et al. 2025 optimizer benchmark** (arXiv:2604.15297) — 15 optimizers × 17 tabular datasets including TabReD; **Muon outperforms AdamW consistently** for tabular MLPs, and **AdamW + EMA** is a simpler alternative.
- **Holzmüller et al. 2024 "Better by Default"** — strong pre-tuned MLPs and boosted trees with sensible defaults match or beat most fancy methods.

### Synthetic data: TabDDPM (Kotelnikov et al., ICML 2023) and successors

TabDDPM applies discrete+continuous diffusion to tabular generation; outperforms CTGAN/TVAE on ML-utility while being privacy-superior to SMOTE. TabSyn (latent-space diffusion) is a 2024 follow-up. **Caveat:** privacy-leakage and out-of-distribution issues are documented (Cheng & Bahmani, arXiv:2510.16037, 2025). For your problem, synthesizing trades is risky because the distribution non-stationarity is exactly what you most need to *not* memorize. **Recommendation:** Use only for class-balancing the ELITE positives (~85 per fold), not for general data augmentation.

### Meta-learning / few-shot

STUNT (Nam et al., arXiv:2303.00918, ICLR 2024) generates self-supervised tasks from unlabeled tables for meta-learning — directly applicable to your 60k unlabeled candidates. LoCalPFN (Thomas et al., arXiv:2406.05207, NeurIPS 2024) is the SOTA retrieval+fine-tuning over TabPFN.

---

## Theme 2 — Ordinal / Nested Label Exploitation

### CORN (Shi et al., arXiv:2111.08851, 2021) and CORAL (Cao et al., 2019)

**Core idea.** CORAL imposes weight-sharing across K−1 binary heads to guarantee rank consistency (P[y>1] ≥ P[y>2] ≥ …). CORN drops the weight-sharing and uses chain-rule conditional probabilities — empirically *better* than CORAL while preserving rank consistency.

**Why it fits MoMTrans precisely.** Your BROAD/VETOED/HIGH/ELITE thresholds are mathematically nested: P(R≥40%) ≤ P(R≥25%) ≤ P(R≥15%) ≤ P(R≥10%). Treating them independently is *guaranteed* to produce inconsistent ELITE>VETOED predictions and waste statistical strength. Replacing 4 BCE heads with one CORN head is a near-trivial code change with `coral-pytorch`, and statistically should help ELITE most (~85 positives per fold borrows from ~thousands of BROAD positives).

**GPU/complexity.** Trivial.

### Continuously Generalized Ordinal Regression (Lu, Ferraro, Raff; arXiv:2202.07005, 2022)

Interpolates between parallel-hyperplane and class-specific-hyperplane ordinal regression — better sample efficiency. Recommended as a CORN extension.

### UNICORNN (OpenReview 2024)

Calibrated unimodal output via Optimal Transport divergence — addresses the well-known issue that ordinal classifiers often produce non-unimodal posteriors. Helpful if you need clean ELITE/HIGH cumulative probabilities for Kelly.

### Hierarchical / nested learning

- **Nested Learning** (Alemohammad et al., arXiv:2007.06402) — sequence of nested information bottlenecks for multi-granular tasks.
- **Hierarchy-preserving constraint loss** (USPTO patent 12423592, IBM 2024) — enforces P(coarse) ≥ P(fine) via max-pooling losses.

**Gap.** No published applied work on nested-ordinal financial returns. This is a small contribution opportunity.

### Beyond CORN (2024–2026)

The Deep Ordinal Regression Forests line (Zhu et al.), the SoDeep approach for sorting-based loss surrogates (Engilberge et al., CVPR 2019), and emerging optimal-transport-based ordinal losses are worth tracking but offer marginal gains over CORN for your scale.

---

## Theme 3 — Self-Supervised Pretraining for Tabular (post-MFR)

### SCARF (Bahri et al., ICLR 2022; arXiv:2106.15147)

Contrastive learning via random-feature corruption (replace each feature value with one drawn from its empirical marginal). On 69 OpenML-CC18 datasets, SCARF outperforms autoencoders and improves the semi-supervised setting — the regime closest to your 20k labeled / 60k unlabeled split.

### VIME (Yoon et al., NeurIPS 2020) and SubTab (Ucar et al., NeurIPS 2021)

VIME: mask-prediction + value-imputation pretext + consistency regularization on augmentations. SubTab: split features into overlapping subsets, reconstruct full input from each. Both predate SCARF and tend to underperform it on the OpenML-CC18 benchmark.

### ReConTab (NeurIPS 2023 TRL workshop)

Regularized contrastive representation learning for tabular — one of the strongest current contrastive options.

### T-JEPA (Thimonier et al., arXiv:2410.05016, ICLR 2025)

JEPA adapted to tabular — predicts latent representation of one feature subset from another, *no augmentations needed*. Reports consistent improvements over SCARF/VIME and matches GBDTs in many cases. **Strongly recommended** as a replacement for your 200-epoch masked-feature-reconstruction pretraining: it's algorithmically similar but uses latent-space prediction (avoiding the well-known degenerate-pixel problem of masked autoencoders).

### Empirical consensus 2024–2026

The wwweiwei/awesome-self-supervised-learning-for-tabular-data review and recent benchmarks (Rubachev et al. 2025) suggest contrastive (SCARF) ≥ JEPA-style ≥ masked autoencoder for downstream classification, but the gap to a well-tuned supervised baseline is modest. Pretraining helps most when you have a clean unlabeled corpus from a similar distribution — which you have.

### Cross-domain pretraining

- **XTab** (Zhu et al., ICML 2024) — cross-table pretraining; modest gains, big complexity cost.
- **TransTab** (Wang & Sun, NeurIPS 2022) — transferable tabular transformers.

**Honest assessment.** Given the 60k unlabeled Polygon candidates come from the *same distribution* as labeled rows, T-JEPA or SCARF pretraining is more promising than your current MFR. Expect 0.005–0.02 Spearman lift if it works. **Complexity:** medium.

---

## Theme 4 — Ranking-Aware Loss Functions

This is the area with the strongest expected ROI for MoMTrans because the strategy explicitly trades top-K daily picks.

### Differentiable sorting / soft-rank Spearman

**torchsort** (Blondel et al., "Fast Differentiable Sorting and Ranking," arXiv:2002.08871; teddykoker/torchsort) provides O(n log n) soft-rank via isotonic regression with PyTorch C++/CUDA kernels. The Numerai community has documented gains from optimizing Spearman ρ directly via torchsort. **This is the most directly aligned loss for your evaluation metric.**

```python
def spearman_loss(pred, target, regularization_strength=1.0):
    pred_rank = torchsort.soft_rank(pred, regularization_strength=regularization_strength)
    target_rank = torchsort.soft_rank(target)
    return -corr(pred_rank, target_rank)
```

### NeuralNDCG (Pobrotyn & Białobrzeski, arXiv:2102.07831)

NDCG approximation via NeuralSort — a differentiable sort. ApproxNDCG (Bruch et al.) uses sigmoid-based rank approximation. Both directly optimize NDCG@K which is well-aligned with "trade the top K picks per day." Use with per-day groupings.

### ListNet, ListMLE, SoDeep (Engilberge et al., CVPR 2019)

SoDeep trains a small auxiliary network to act as a differentiable sorter and reports state-of-the-art on cross-modal retrieval — applicable here as a ranking surrogate.

### Stock-ranking-specific 2024–2025 evidence

- **Kwiatkowski et al., "On Evaluating Loss Functions for Stock Ranking" (arXiv:2510.14156, ACM CIKM 2025)** — directly benchmarks pointwise/pairwise/listwise/weighted losses for transformer S&P 500 selection. **Margin-style pairwise and listwise losses dominate**, with Sharpe and risk-adjusted return improvements over MSE/BCE baselines.
- **NRBO@k** (Banik et al. 2021) — top-k-weighted ranking metric for stock prediction; list-wise loss + graph approach generates 0.281%–4.928% relative gain over top-stock strategy.

**Recommendation.** Use a **composite loss**: 0.5·BCE_CORN + 0.5·SpearmanSoftRank, with per-day groupings (group by date_id). Expect this to be the single highest-impact change after CORN.

**Gaps.** Per-day groupings with limited samples per day (microcap universe is small) introduce variance — needs careful batching strategy. ApproxNDCG@K with K matching your daily-pick count (e.g., K=3 or K=5) is empirically untested in microcap.

---

## Theme 5 — DeepSeek-Style Compute Efficiency at Small Scale

**Critical framing.** DeepSeek-V3's MLA, FP8, MoE, DualPipe, and Multi-Plane Network were specifically designed for 671B params on 2,048 H800s with cross-node communication bottlenecks. **Almost none of this transfers to your single-GPU 500K-param setting.** The "DeepSeek mindset" worth absorbing is *the philosophy* (instrument relentlessly, optimize the training process, find the bottleneck) — not the techniques.

### Muon (Keller Jordan et al., 2024; kellerjordan.github.io/posts/muon)

Orthogonalize updates via Newton-Schulz iteration on momentum matrices. Set NanoGPT speedrun records in 10/2024. Liu et al. ("Muon is Scalable for LLM Training," arXiv:2502.16982, 2025) extended to 16B-param Moonlight MoE with ~2× compute efficiency over AdamW. **The Yandex 2026 tabular optimizer benchmark (arXiv:2604.15297) finds Muon consistently outperforms AdamW for tabular MLPs.**

**Assessment for MoMTrans:** Direct fit. d_model=64, n_layers=4 means most weights are 2D matrices ≥64×64 — exactly Muon's sweet spot. **Implementation:** `pip install git+https://github.com/KellerJordan/Muon`; ~30 lines of code change. **GPU cost:** ~3% wallclock overhead. **Strongly recommended.**

### Schedule-Free AdamW (Defazio et al., NeurIPS 2024)

No schedule, single base learning rate, implicit weight averaging. Strong empirical performance on LLM training (Song et al. 2025, "Through the River…"). **For your 16-fold WF setup** where each fold needs its own LR schedule, schedule-free is operationally simpler. Combined with Muon's momentum it should compose.

### SOAP, Sophia, Lion

- **Sophia** (Liu et al., arXiv:2305.14342) — diagonal Hessian preconditioning. Liu et al. ICLR 2024 showed 2× speedup on GPT-2 medium/large but **multiple replications** (Zhao et al. 2024, arXiv:2509.02046 "Fantastic Pretraining Optimizers and Where to Find Them") find **no significant speedup over AdamW for models <0.5B**. Skip for your scale.
- **Lion** (Chen et al. 2023) — sign-momentum. Gains on vision Transformers but mixed on small models. Skip.
- **SOAP** (Vyas et al. 2025) — Shampoo-style with Adam in eigenbasis. Promising at scale but adds memory overhead — likely not worth it.

### FP8 / INT8 Training

DeepSeek-V3 FP8 mixed-precision (arXiv:2505.09343); FOG architecture for fully-FP8 GEMM (Hernández-Cano et al., arXiv:2505.20524, 2025); TWEO (arXiv:2511.23225, Nov 2025) addresses the extreme-outlier instability. Blackwell sm_120 has native FP8 support.

**Honest assessment for MoMTrans:** **Skip.** You're at 3.5 GB/12 GB VRAM. FP8 saves memory you don't need, and the engineering risk (outlier-induced training collapse) is substantial. BF16 mixed precision (your current setup) is the right answer at this scale.

### MoE in small-scale

**Skip.** All published evidence (DeepSeek-V3 itself acknowledges this) is that MoE earns its complexity above ~1B params with multiple-token-per-batch routing. At 500K params and one specialist per ordinal threshold, you have a more elegant solution available: TabM-style parameter-sharing ensembles or CORN multi-head, both of which give MoE-style specialization without router instability.

### Mixture of Recursions (Bae et al., arXiv:2507.10524, NeurIPS 2025)

Recursive transformer with token-level adaptive recursion depth + KV-sharing. Demonstrated 135M–1.7B params. **Probably overkill** for MoMTrans but conceptually interesting if you wanted a single model handling all 4 ordinal thresholds with adaptive depth — speculative.

### LoRA / DoRA / GaLore for the 4 specialists

DoRA (Liu et al., ICML 2024 Oral, arXiv:2402.09353) decomposes weights into magnitude+direction; consistently outperforms LoRA at low rank. **Use case:** train a shared TabM/TabTransformer trunk via SCARF/T-JEPA pretraining, then attach DoRA adapters per ordinal threshold. Adapter rank=4 fits all 4 specialists in <50 MB. **Recommended** as an alternative to your current 4-separate-models approach.

### Knowledge Distillation

The single most leveraged technique you should adopt:

- **TabDistill** (arXiv:2511.05704, Nov 2025) — explicitly for distilling transformers into tabular MLPs in few-shot regime; surpasses XGBoost and logistic regression with limited labels.
- **Hinton et al. 2015 dark-knowledge** with temperature τ=2–4 on TabPFNv2 logits → MoMTrans.
- **Co-distillation** / mutual learning (Anil et al. 2018) — train multiple students simultaneously and have them teach each other.
- **Born-Again Networks** (Furlanello et al. 2018) — same architecture self-distillation, surprisingly effective.

**Plan:** TabPFNv2 (and/or a tuned XGBoost/CatBoost ensemble) generates soft labels on both 20k labeled and 60k unlabeled Polygon rows. Distill into MoMTrans with mixed loss = α·CE_hard + (1−α)·KL_soft + β·SpearmanRank.

### Test-time compute scaling (Snell et al. 2024; s1 by Muennighoff et al., arXiv:2501.19393, 2025)

Sequential vs parallel scaling at inference. **Limited applicability** to tabular regression — there's no chain-of-thought to extend. Parallel scaling = MC ensembles, which you can already do. Skip.

### Flash Attention v3

Worth turning on for the TabTransformer's self-attention; PyTorch 2.12 nightly + CUDA 12.8 should support it natively. Marginal (~10–15%) speedup at your sequence length (54 features ≈ very short).

---

## Theme 6 — Uncertainty and Calibration (Kelly-quality probabilities)

**This is operationally critical.** Aggressive Kelly (50/35/20/10 caps) is unforgiving of miscalibrated probabilities — overestimating P[ELITE] by 5% can blow up the strategy.

### Conformal prediction with adaptive coverage

- **Adaptive Conformal Inference** (Gibbs & Candès 2021; refined Angelopoulos et al. 2024) — online updating of α to maintain coverage under shift.
- **CQR** — Conformalized Quantile Regression (Romano et al. 2019).
- **AEnbMIMOCQR** (Sousa et al., Neurocomputing 2024) — multi-step ahead, no data splitting, near-exact coverage even when not exchangeable. Direct fit for 5-day forward prediction.
- **Attention-based Feature OCP** (arXiv:2511.15838, 2025) — claims 88% interval-size reduction vs OCP. Recent and promising.
- **Neural Conformal Control** (Bhatnagar et al., AAAI 2025; arXiv:2412.18144).
- **Strongly Adaptive Online Conformal Prediction** (Bhatnagar et al., arXiv:2302.07869).

**Recommendation.** Wrap MoMTrans with split-conformal-with-adaptive-α at inference. Use the conformal prediction interval to *gate* Kelly sizing — only size up when interval is tight. This is a major robustness upgrade with small compute cost.

### Bayesian deep learning

- **Last-layer Laplace** (Daxberger et al. 2021) — extremely cheap post-hoc Bayesian.
- **MC Dropout** (Gal & Ghahramani 2016) — your current model probably does not use dropout heavily; adding it for MC uncertainty is cheap.
- **Deep Ensembles** (Lakshminarayanan et al. 2017) — gold standard but 4–10× compute. With your headroom: feasible.
- **SWAG** (Maddox et al. 2019) — Gaussian over SGD trajectory. Single training run. **Best ROI in this category.**

### Evidential Deep Learning (Amini et al. 2020, NeurIPS)

NIG-distribution outputs estimate aleatoric+epistemic uncertainty in one forward pass. **Caveat:** Bengs et al. and Jürgens et al. (arXiv:2402.09056, NeurIPS 2024 "Are Uncertainty Quantification Capabilities of Evidential Deep Learning a Mirage?") show learned EDL uncertainties are non-vanishing even with infinite data — use with caution. EDL for *aleatoric* uncertainty is fine; for *epistemic*, prefer ensembles or SWAG.

### Mixture Density Networks / Normalizing Flows / Diffusion

For full conditional return distribution P(R|x). Real Spline Flows (NSF, RealNVP) and conditional diffusion are overkill given you only need a few quantiles for Kelly. MDNs with K=3 mixture components are the cheap option. **Recommendation:** Skip unless full distribution is needed.

---

## Theme 7 — Knowledge Distillation (XGBoost / large-models → MoMTrans)

Already partially covered in Theme 5. Specific points:

- **Tree → Transformer distillation** is empirically tricky because trees produce hard step-function predictions. Use *temperature-softened probabilities* from a calibrated XGBoost ensemble, not raw leaf assignments. Frosst & Hinton 2017 ("Distilling a Neural Network Into a Soft Decision Tree") shows the *reverse* direction works; the forward direction (tree→nn) is in TabDistill (arXiv:2511.05704).
- **TabPFN as teacher** is novel and promising; Liu & Ye 2025 ("On Finetuning Tabular Foundation Models," arXiv:2506.08982) provides the systematic study of TabPFNv2 finetuning for downstream use.
- **Co-distillation** of the four ordinal heads (each teacher to each other) before final fine-tuning is an underexplored option.

---

## Theme 8 — Robustness and Distribution Shift

### Test-time adaptation

- **TENT** (Wang et al., ICLR 2021) — entropy-min on BN affines.
- **EATA** (Niu et al., ICML 2022; arXiv:2403.11491 v2 2024) — selective sample efficient + anti-forgetting.
- **AdapTable** (Kim et al., AAAI 2025; arXiv:2407.10784) — **the only TTA method designed specifically for tabular** with shift-aware uncertainty calibrator and label-distribution handler. Up to 16% improvement on HELOC.

**Assessment.** AdapTable is a near-direct fit for cross-fold WF testing. **Recommended** for your inference pipeline.

### Stochastic Weight Averaging family

- **SWA** (Izmailov et al. 2018) — average weights across last cycles of training.
- **SWAG** (Maddox et al. 2019) — Gaussian SWA for Bayesian uncertainty.
- **Adaptive SWA** (arXiv:2406.19092, 2024) — schedule-free averaging.
- **EMA of weights** — the Yandex 2026 optimizer benchmark explicitly highlights AdamW+EMA as a simple, consistent improvement on tabular.

**Strongly recommended.** Add EMA (decay=0.999) to your AdamW now; switch to AdamW+EMA or Muon+EMA. Almost zero implementation cost.

### Sharpness-Aware Minimization (Foret et al., ICLR 2021)

SAM/ASAM/Agnostic-SAM (arXiv:2406.07107). Doubles per-step compute but documented to help in low-data regimes. Your 9-min/16-fold budget can absorb a 2× → 18-min cost. **Worth A/B testing.**

### Group DRO / Bitrate-Constrained DRO

- **Group DRO** (Sagawa et al., arXiv:1911.08731) — minimize worst-group loss with strong L2 / early stopping required for overparameterized.
- **Bitrate-Constrained DRO** (Setlur et al., ICLR 2023) — doesn't need group annotations, matches Group DRO performance.

**Use case.** Define groups by market regime (e.g., VIX bucket, liquidity bucket, sector). Train MoMTrans with Group DRO to be robust to regime shifts. **Recommended.**

### Continual / online learning

- **Reset / Shrink-and-Perturb** (Ash & Adams 2020) — continual training across folds with periodic weight reset to avoid loss-of-plasticity.
- **Bayesian online change-point detection** (Adams & MacKay 2007; Saatçi et al.) — gate predictions when a regime change is detected.

---

## Theme 9 — Alternative Architectures Beyond Transformers

### Mamba / Mamba-2 (Gu & Dao 2023)

**MambaTab** (Ahamed & Cheng, arXiv:2401.08867, 2024 / PMC 2024) and **Mambular** (OpenReview ICLR 2025) — Mamba adapted to tabular. Both report competitive performance with transformer baselines.

**Honest assessment.** No clear win over TabM or properly-tuned MLP on TabReD. Skip.

### Kolmogorov-Arnold Networks (Liu et al., arXiv:2404.19756, 2024)

**KAN benchmark on tabular** (Poeta et al., arXiv:2406.14529, 2024): KAN matches/slightly beats MLP but with substantially higher compute cost. **TabKAN** (Springer 2025) — KAN encoding for numerical features in tabular. Mixed evidence; TabKANet shows competitive results.

**Honest assessment.** **Not worth it.** KAN's smooth-spline activations don't earn their compute cost on tabular per the published benchmarks.

### GNNs for cross-instance tabular learning

**Edge-updating GNNs** (ScienceDirect 2026) — model feature interactions as graph. Could help if you constructed a *ticker-similarity graph* (sector, market cap, beta) — speculative but interesting.

### Liquid Neural Networks

No published tabular evaluation. Skip.

---

## Theme 10 — Efficiency-Focused Training Tricks

### Mixup / Manifold Mixup (Zhang et al. 2018; Verma et al. 2019)

Documented gains on tabular per Mixup paper (Tables for UCI datasets). **Contrastive Mixup** (Darabi et al., arXiv:2108.12296) and **MixupE** (arXiv:2212.13381) extend to tabular. **Calibrated Mixup for Imbalanced Regression** (ScienceDirect 2025) reports 10–20% MAE reduction specifically for imbalanced tabular regression.

**Recommendation.** Add Mixup with α=0.2–0.5 for free regularization. **Crucially, this fits your imbalanced-positive setting** — calibrated Mixup is designed exactly for it.

### Active Learning

For label-efficient tier specialist training. Useful if you can curate which positives to label. Coresets (Mirzasoleiman et al.) and BatchBALD (Kirsch et al.) are SOTA. **Lower priority** given your hard 20k ceiling is structural, not budget-driven.

### Pseudo-labeling on 60k Polygon candidates

- **Curriculum Pseudo-Labeling** (Cascante-Bonilla et al. 2021; Kim et al. 2023, arXiv:2302.14013) — add easy pseudo-labels first.
- **CAST** (Kim et al., arXiv:2310.06380, 2024) — cluster-aware self-training with reliable confidence; specifically designed for tabular GBDT but applicable to NNs.
- **In-all-likelihoods** (Rodemann et al., arXiv:2303.01117) — multi-objective utility for pseudo-label selection.
- **Data-centric pseudo-labeling** (Seedat et al., arXiv:2406.13733, 2024).

**Recommendation.** Use CAST-style cluster-aware pseudo-labeling on the 60k candidates with TabPFNv2 as the confidence-providing teacher. Iterate with curriculum from easy to hard.

### Multi-token prediction (DeepSeek-V3)

For tabular: predict multiple ordinal thresholds simultaneously — basically what CORN does. Already covered.

### Layer-wise LR decay, stochastic depth

Standard tricks. Worth adding but marginal at 4 layers.

### Reset / Shrink-and-Perturb (Ash & Adams 2020)

Critical for 16-fold WF if you train sequentially across folds. Avoids loss-of-plasticity. **Recommended** if you currently train sequentially per fold.

---

## Synthesis and Prioritization

### Tier S — Implement immediately (highest expected impact × lowest implementation cost)

1. **CORN multi-threshold ordinal head** with shared TabM/TabTransformer trunk — replaces 4 independent specialists. Borrows statistical strength to ELITE. Effort: 1 day. Expected: +0.005–0.015 Spearman.
2. **Spearman soft-rank loss (torchsort)** + per-day groupings, composite with CORN BCE. Effort: 2–3 days. Expected: +0.005–0.015 Spearman.
3. **AdamW → Muon (or Muon + EMA-of-weights)**. Effort: 1 hour. Expected: +0.003–0.01 Spearman, free.
4. **TabPFNv2 distillation** — generate soft labels for 20k labeled + 60k unlabeled, distill into MoMTrans with KL temperature loss. Effort: 1 week. Expected: +0.01–0.02 Spearman, large impact on ELITE specifically.
5. **Mixup augmentation** (α=0.4) for free regularization. Effort: 1 hour. Expected: +0.002–0.008.

### Tier A — Implement after S validates (substantial impact, moderate cost)

6. **TabM architecture** replacing TabTransformer trunk. Effort: 1 week. Expected: +0.005–0.015.
7. **T-JEPA pretraining** replacing masked-feature reconstruction. Effort: 1 week. Expected: +0.005–0.015.
8. **Adaptive Conformal Inference** wrapper for Kelly-quality probabilities. Effort: 3–5 days. Expected: backtest Sharpe improvement, lower drawdown.
9. **Cluster-aware self-training (CAST)** on 60k unlabeled Polygon candidates. Effort: 1 week. Expected: +0.005–0.015.
10. **SAM** at 2× compute. Effort: 1 day. Expected: +0.003–0.01.

### Tier B — Promising but speculative

11. AdapTable test-time adaptation per-fold. Tabular-specific TTA novel for finance.
12. SWAG for Bayesian Kelly sizing.
13. DoRA adapters per ordinal threshold (instead of CORN, alternative architecture).
14. Group DRO over market-regime groups.

### Tier D — Likely traps; do not pursue

- **FP8 / INT8 training** — irrelevant at 500K params, 3.5 GB VRAM.
- **MoE routing** — wrong scale entirely.
- **Mamba / KAN** — no published advantage on tabular over MLP/TabM at your scale.
- **Sequence branches over minute bars** — already empirically failed; don't revisit without new feature engineering.
- **Larger d_model / n_layers** — already empirically overfits.
- **Test-time compute scaling (s1-style)** — no analog for tabular regression.
- **Sophia / Lion** — no significant edge over AdamW at your model size per recent replications.

### Synergy / Composability matrix

- **Compose well:** CORN + Spearman-soft-rank loss, Muon + EMA, TabPFN distillation + CAST self-training (TabPFN labels the 60k), T-JEPA pretraining + CORN downstream, SAM + Mixup, Conformal + SWAG.
- **Mutually exclusive / redundant:** CORN-multi-head ↔ DoRA-per-specialist (pick one architectural pattern); SAM ↔ Schedule-Free (both perturb optimization; A/B test); Deep Ensembles ↔ SWAG (both Bayesian; pick by compute).
- **Watch for interactions:** Mixup interacts with CORN — Mixup labels need to interpolate ordinal probabilities consistently (use linear interpolation in logit space). Conformal-on-distilled-model: ensure TabPFN soft labels are used for *training* but conformal calibrates against *true* labels.

### The five highest-leverage research bets (combine cutting-edge with constraints)

1. **TabPFNv2 → MoMTrans distillation pipeline using all 80k rows.** This single technique addresses the 20k label ceiling, the ELITE starvation, and provides a Bayesian-style calibrated teacher. No published deployment in microcap intraday — original work.
2. **CORN ordinal head + per-day Spearman soft-rank listwise loss.** Combines two existing 2021–2024 techniques in a way that is *exactly* matched to nested-threshold + top-K-trade structure. Likely publishable as an applied paper if it works.
3. **TabM trunk + DoRA adapters or CORN heads + Muon optimizer + EMA weights.** All four are 2024–2025 SOTA on tabular and compose without conflict; per Yandex 2026 benchmark, this combination is the new tabular-DL baseline.
4. **Adaptive Conformal Inference + SWAG for Kelly probability quality.** Genuinely rigorous coverage under the non-exchangeable, regime-shifting microcap world. The Kelly literature has not absorbed 2024 ACI advances.
5. **CAST cluster-aware self-training on 60k unlabeled candidates with TabPFNv2 confidence + curriculum schedule.** The intersection of "small labeled tabular finance" and "modern semi-supervised pseudo-labeling" is essentially unstudied.

### Genuinely open research questions where you'd be at the frontier

- **NDCG@K loss with K matching daily-pick count under per-day groupings, applied to nested-ordinal-label finance.** No published study.
- **Conformal Kelly:** how should ACI prediction-interval width *modulate* Kelly sizing in microcap? The conformal-prediction and Kelly-criterion literatures are largely disjoint.
- **Pseudo-label confidence calibration in non-stationary financial regimes.** CAST etc. assume stationary cluster structure; financial regimes violate this.
- **TabPFNv2-as-teacher distillation on time-evolving distributions.** TabPFNv2 was pretrained on synthetic IID priors; whether its ICL captures the quasi-stationary structure of microcap intraday is empirical.
- **Ordinal-specific calibration loss** (beyond UNICORNN's unimodality) for nested return thresholds: an ordinal version of focal/Brier loss tailored to ELITE-tail extreme imbalance. Open.
- **The 0.14–0.16 Spearman ceiling itself.** Is this a Bayes ceiling for the 54-feature universe (in which case feature engineering — adding tick trades, NBBO microstructure, news sentiment — is the only path forward) or a model-class ceiling (in which case TabPFN-class foundation models could break through)? Decisive experiment: train TabPFNv2 directly on the 54-feature × 20k-row data and measure its ceiling.

---

## Caveats

1. **Most empirical evidence cited is from non-financial domains.** Tabular benchmarks (TabReD, OpenML-CC18) include some industrial datasets but not microcap finance. Generalization is plausible but not guaranteed. The Stock Ranking Loss benchmark (arXiv:2510.14156) is the closest direct analog and supports listwise losses.
2. **TabPFNv2's behavior on financial drift is undertested.** Hoo et al. 2025 results are on standard time-series benchmarks (ETT, weather), not microcap intraday. **Pilot before committing.**
3. **Several papers cited are 2025–2026 preprints** (e.g., AFOCP arXiv:2511.15838, TWEO arXiv:2511.23225, Yandex optimizer benchmark arXiv:2604.15297). Replication risk. Treat as directional, not load-bearing.
4. **"DeepSeek-style efficiency" at 500K params is mostly philosophy, not technique transfer.** The actual DeepSeek-V3 innovations (MLA, FP8, MoE, DualPipe, NVLink-aware pipeline parallelism) target a fundamentally different operating regime. Adopt the *mindset* of training-process optimization; do not adopt the *techniques* mechanically.
5. **Backtest leakage risk.** Several techniques here (TabPFNv2 distillation, CAST self-training on Polygon candidates) require careful walk-forward hygiene to avoid leaking future labels into the soft-label generation step. Each technique needs its own WF-aware implementation.
6. **The 0.14–0.16 Spearman ceiling may be feature-driven, not model-driven.** If true, the highest-leverage move is adding the unused signals (tick-level trades, NBBO quotes, Benzinga sentiment, short interest, cross-ticker correlation) as *additional engineered features*, not improving the model. Most of the techniques in this report assume the 54-feature universe is fixed; that assumption may be the binding constraint.
7. **Kelly sizing magnifies calibration errors.** Any technique claimed to "improve probabilities" must be validated by a calibration test (reliability diagram, Murphy decomposition) under the actual WF protocol, not just by Spearman. A model with better Spearman but worse calibration can blow up Kelly even if pure-prediction metrics improve.
8. **No technique listed has a published microcap intraday gap-up trading deployment.** All applications to your problem are extrapolations from adjacent domains. The 16-fold walk-forward backtest with $46k baseline lift is your actual ground truth — anchor evaluation there.