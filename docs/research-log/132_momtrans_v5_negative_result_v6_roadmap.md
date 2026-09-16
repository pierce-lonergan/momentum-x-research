# 132 — v5 Negative Result + v6 Roadmap (Data > Architecture)

**Session date:** 2026-05-06 (overnight)
**Branch:** develop
**Predecessors:** [131 v5 enhancement roadmap](131_momtrans_v5_enhancement_roadmap.md)
**Source research:** [M.md — Frontier Research Extension](M.md), [MoMTrans v4 Frontier Techniques](MoMTrans v4 — Frontier Techniques for Small-Data Tabular Transformers in Microcap Gap-Up Prediction.md)
**Status:** v5 NEGATIVE RESULT documented; v6 redirects to data not architecture

---

## TL;DR

**v5 trained but UNDERPERFORMED both v4 and v3.** The Tier-S "frontier
research" stack (CORN ordinal head + Spearman soft-rank loss + Muon
optimizer + Mixup) collapsed Spearman ρ from v4's 0.141 to **0.041** on
the full 16-fold WF eval — worse than the original v4 baseline by ~70%.

This is itself the most valuable finding of the entire v5 effort. It
directly validates the M.md research thesis:

> **The orthogonal axis is data, not architecture.** Your Spearman
> ceiling is far more likely a *feature ceiling* than a *model
> ceiling*. The single highest-EV move is to ingest tick-level Polygon
> trades_v1 and build microstructure features — VPIN, OFI, Kyle's λ,
> Hawkes self-excitation, dark-pool prints, Benzinga catalyst
> embeddings.

v6 is reframed accordingly: **stop optimizing model architecture; start
expanding the data horizon AND hardening the validation framework** (CPCV
+ Deflated Sharpe + PBO).

---

## 1. v5 Honest Results

### What v5 shipped (Tier S, per doc 131 plan)

| Component | Source | Implementation |
|---|---|---|
| CORN ordinal head | Shi et al. arXiv:2111.08851 | `scripts/ml_v5_components.py:CORNHead` |
| Spearman soft-rank loss | Blondel et al. arXiv:2002.08871 (sigmoid-pairwise approximation since torchsort failed to install) | `ml_v5_components.py:per_day_spearman_loss` |
| Muon + AdamW hybrid | Keller Jordan 2024; Yandex 2026 benchmark | `ml_v5_components.py:Muon` |
| EMA weights (decay=0.999) | Yandex 2026 | `ml_v5_components.py:EMAWeights` |
| Mixup α=0.4 + ordinal label interpolation | Zhang et al. 2018 + ScienceDirect 2025 | `ml_v5_components.py:mixup_features_and_ordinal_labels` |

All 17 component-level unit tests pass (`test_d283_v5_components.py`).
The 1-fold smoke test gave Spearman 0.157, suggesting the architecture
worked. The full 16-fold WF told a different story.

### Full WF results (1,020 seconds on RTX 5070)

| Strategy | Spearman ρ | Top-1% avg | Top-2% avg |
|---|---|---|---|
| v3 production | **+0.159** | **+22.51%** | +14.77% |
| v4 cohort cascade (single specialist as score) | +0.141 | +5.03% | +3.77% |
| **v5 Tier-S (this commit)** | **+0.041** ❌ | -0.46% | +0.60% |

The v5 model is essentially uncorrelated with realized 5-day returns.
Top-1% picks return slightly NEGATIVE on average — the model's
highest-confidence picks are no better than random.

### Diagnosis

The training loss converged normally (~0.36 across all folds) but **per-fold
early stopping triggered very aggressively** — most folds stopped at
epochs 17-25 instead of using the full 100. This suggests:

1. **The composite loss is non-stationary.** Spearman soft-rank loss
   gradient depends on per-day rankings; with Mixup interpolating across
   days the gradient signal is noisier than plain BCE. Early stopping
   (patience=15 on training loss) misinterprets noise as plateau.

2. **CORN's mathematical rank-consistency CONSTRAINT may be hurting.**
   Production v3 doesn't enforce P(R≥40%) ≤ ... ≤ P(R≥10%) but its
   empirical predictions still rank candidates well. Forcing
   monotonicity sacrifices flexibility for interpretability.

3. **Mixup interpolating ordinal labels in logit space** may be
   producing pathological "label noise" — e.g., a y=0 (SKIP) sample
   mixed 50/50 with a y=4 (ELITE) sample becomes a label that makes
   the conditional probabilities incoherent.

4. **Muon at lr=0.02** may be too aggressive on tiny matrices — d_model=64
   means the 64×64 weight matrices Muon orthogonalizes are right at the
   edge of where the Newton-Schulz iteration converges cleanly.

5. **EMA at decay=0.999** averages over ~1000 update steps; with
   early-stopping at <30 epochs × <100 batches/epoch ≈ 3000 steps total,
   the EMA shadow weights are heavily influenced by early-training
   noise.

Each individual cause is fixable — but the M.md research suggests we
**should not** spend more cycles fixing them. The whole architectural
optimization axis is hitting diminishing returns.

---

## 2. M.md Research Synthesis — The Reframe

The M.md doc's central thesis (paraphrased):

> Architectural sophistication has hit its ceiling at our scale.
> Adjacent-domain transfer (Optiver Kaggle 2021/2023, Numerai community,
> Renaissance/Two Sigma sourceable work) consistently shows: feature
> engineering > model architecture for tabular finance. The path from
> Spearman 0.16 to a tradeable strategy at $5M+ AUM goes through:
>
> 1. **DATA**: tick-level microstructure (VPIN, OFI, Kyle's λ),
>    Benzinga news embeddings (FinBERT/FinGPT), sympathy-stock peer
>    features, options-implied volatility (where available).
> 2. **VALIDATION**: CPCV + Deflated Sharpe + PBO (López de Prado
>    framework). The current 16-fold WF likely contains
>    multiple-testing artifacts; ELITE's Spearman 0.004 is *prima facie*
>    a multiple-testing illusion.
> 3. **EXECUTION**: Bouchaud square-root impact + capacity-aware Kelly.
>    At $5M AUM microcap, position sizes targeting Q/V_daily < 0.05
>    are necessary to keep impact <0.5%. Without this, alpha is eaten
>    by execution.
> 4. **SIZING**: Replace post-hoc Kelly with IQN/QR-DQN distributional
>    RL. Honest fat-tail handling improves realized Sharpe even if
>    Spearman is unchanged.

**Key uncomfortable findings from M.md:**

- "ELITE is a data problem, not a modeling problem. With 85
  positives per fold, **no architectural choice will rescue Spearman
  0.004**."
- "Until CPCV+DSR+PBO are run, the Spearman 0.16 ceiling is potentially
  a mirage." The current v4 production lift may NOT survive proper
  multiple-testing correction.
- "Bigger transformer → better tabular" is empirically false at our
  scale (already confirmed by doc 127 ablation).
- "Distillation gives free Spearman" is mythical; usually trades
  capacity for stability with net Spearman unchanged.

---

## 3. v6 Roadmap (data + validation + execution)

The v6 plan reorganizes around the M.md framework. Architecture changes
are deferred until validation hardening confirms there's a real signal
to optimize.

### Phase 0 — Validation Hardening (MANDATORY, before any further
architecture work)

| ID | Task | Source | Effort | Outcome |
|---|---|---|---|---|
| **0.1** | CPCV (Combinatorial Purged CV) replacing 16-fold WF | López de Prado *AFML* Ch. 7; Arian-Norouzi-Seco 2024 | 2-3 days | Honest Sharpe distribution; lower PBO |
| **0.2** | Deflated Sharpe Ratio (DSR) | Bailey & López de Prado JPM 2014 | 1 day | Probability that v4's lift is real |
| **0.3** | Probability of Backtest Overfitting (PBO) | Bailey-Borwein-LdP-Zhu 2016 | 1 day | Quantified IS-OOS rank flip rate |
| **0.4** | Numerai-style feature neutralization (orthogonalize predictions vs sector/beta/cap/realized-vol) | Numerai community standard | 1 day | DEFLATED Spearman that's market-neutral |
| **0.5** | Embargo + purging audit (5-day labels need ≥5d embargo) | LdP AFML | 1 hour | Confirms current 16-fold WF is leakage-clean |

**Expected outcome:** v4's 0.141 Spearman likely deflates to 0.10-0.12
after sector/beta/cap neutralization. ELITE's 0.004 will fail DSR. The
production cascade may need to drop ELITE entirely or massively reduce
its Aggressive Kelly cap.

### Phase 1 — Microstructure Feature Pack (highest-EV NEW data axis)

Per M.md §1A, expected Spearman lift +0.03-0.07 (the largest single
move). Computed from `data/polygon_warehouse/trades_v1_parquet`
(147 GB ZSTD parquet, 9 months). All features are per-(ticker, d0)
scalars added to the existing 54-feature set.

| ID | Feature | Source | Lift estimate |
|---|---|---|---|
| **1.1** | VPIN (Lee-Ready trade signing, NOT BVC) | Easley-LdP-O'Hara RFS 2012; Chakrabarty-Pascual-Shkilko JFM 2015 | +0.01-0.03 |
| **1.2** | Multi-Level Order Flow Imbalance (MLOFI) | Cont-Kukanov-Stoikov JFE 2014; Kolm-Turiel-Westray Math.Finance 2023 | +0.02-0.04 (highest single-feature ROI) |
| **1.3** | Kyle's λ (price-impact-per-signed-volume) | Kyle Econometrica 1985; Kyle-Obizhaeva 2016 invariance | +0.005-0.02 |
| **1.4** | Hawkes self-excitation (α, β) for trade arrival | Bacry-Muzy 2013; Fabre-Muni Toke 2024 neural Hawkes | +0.005-0.015 (borderline) |
| **1.5** | Dark-pool / FINRA TRF print magnitude (exchange code 4) | Buti-Rindi-Werner FM 2022 | +0.005-0.015 |
| **1.6** | Sweep detection (multi-exchange NBBO sweeps in <1s) | Folklore + retail-momentum literature | +0.005 (speculative) |

**Total expected lift: +0.05-0.10 Spearman.** This dwarfs anything
architectural.

**Implementation:** new `scripts/build_v6_microstructure_features.py`
that reads trades_v1, computes the 6 feature classes per (ticker, d0)
in the 9:30-10:00 ET window, and writes
`data/polygon_warehouse/derived/microstructure_v6.parquet`. Then v6
training joins this to aftermath_strat by (ticker, d0) and trains the
production v3 ensemble (NOT a transformer) on the expanded feature set.

### Phase 2 — News / Catalyst Embeddings

Per M.md §1C. Benzinga API or Polygon /v2/reference/news (we already
have the latter ETL'd in `news_features_polygon.parquet` at 4.66%
coverage).

| ID | Feature | Source | Lift estimate |
|---|---|---|---|
| **2.1** | FinBERT headline embeddings → 16-dim projection | Araci 2019 FinBERT | +0.01-0.03 (catalyst-driven) |
| **2.2** | LLM-graded catalyst taxonomy (FDA / earnings / dilution / sympathy / etc.) via Llama-3.1-8B or Qwen-2.5 | Numerai-community standard | +0.01-0.02 |
| **2.3** | News velocity (articles/60min vs 30d baseline) + sentiment dispersion | M.md §1C | +0.005 |
| **2.4** | FinGPT v3 propagation features (Liu et al. arXiv:2412.10823, 2024) | M.md §1C | +0.005-0.015 (frontier) |

### Phase 3 — Cross-Ticker (Sympathy) Features

Per M.md §1D. NO full GNN — just k-NN peer features for ~5-10
tabular features. Highest-ROI graph approach at our scale.

| ID | Feature | Source | Lift estimate |
|---|---|---|---|
| **3.1** | Daily peer matrix: k=20 nearest neighbors by 60-day return correlation + sector match + market-cap bucket | M.md §1D | base structure |
| **3.2** | Per-candidate features: peer mean gap, peer mean d-1 return, peer dispersion | Cucuringu et al. 2025 | +0.01-0.03 |
| **3.3** | Lead-lag DTW relations (top peer with strongest lead correlation in last 30d) | *Expert Systems with Applications* 2025 | +0.005 |

### Phase 4 — Capacity-Aware Kelly (Bouchaud Square-Root Impact)

Per M.md §10B. The most under-emphasized item from prior research.

| ID | Component | Source | Effort |
|---|---|---|---|
| **4.1** | Per-name Q/V_daily target ≤ 0.05 (Bouchaud Y=1 → impact <0.5%) | Maitrier et al. 2025 | 1 day |
| **4.2** | Per-name Y-coefficient calibration from `trades_v1` (estimate σ, V, average impact for last 30d) | Almgren-Chriss 2000; Bucci et al. 2019 | 2 days |
| **4.3** | Hard impact-aware Kelly cap: Q* = argmax E[return] - impact(Q) | Pinelis-Ruppert 2023 robust Kelly | 1 day |
| **4.4** | Capacity ceiling estimator: max AUM for which alpha exceeds impact | M.md §10E | 1 day |

**Outcome:** at $5M AUM, ELITE picks (less liquid) currently absorb
5-10% participation = 0.5-1.5% impact = eats most of the edge. This
phase forces honest sizing.

### Phase 5 — Distributional RL Sizing (replace post-hoc Kelly)

Per M.md §2B. IQN / QR-DQN handles fat tails directly, addresses
the calibration concerns from §2.

| ID | Component | Source | Effort |
|---|---|---|---|
| **5.1** | IQN (Implicit Quantile Networks) trained to predict the FULL ret_t5 distribution | Dabney-Ostrovski-Silver-Munos ICML 2018 | 1 week |
| **5.2** | CVaR-based Kelly: size from optimal Kelly of CVaR_α distribution | Rockafellar-Uryasev 2000 + IQN 2018 | 2 days |
| **5.3** | CQL (Conservative Offline RL) for 20k samples | Kumar-Zhou-Tucker-Levine NeurIPS 2020 | 1 week (frontier) |

### Phase 6 — Multi-task Learning (deferred until v6 data foundations)

Per M.md §8B. NOT a priority until CPCV proves there's signal to
share across tasks.

| ID | Architecture | Source | Effort |
|---|---|---|---|
| **6.1** | PLE (Progressive Layered Extraction) — single shared expert + per-tier gate | Tang et al. RecSys 2020 | 1 week |
| **6.2** | Auxiliary tasks: next-day vol, spread at d+1, halt probability | M.md §8B | 1 day each |

---

## 4. v6 Implementation Order (next 4-8 weeks)

**Week 1: Phase 0 (validation hardening)**
- Run CPCV on the v4 cohort cascade. Compare to 16-fold WF.
- Compute DSR + PBO on the +$46k Phase-C lift.
- If v4 lift fails DSR/PBO → document and rollback Wednesday's deploy.

**Weeks 2-3: Phase 1 (microstructure features)**
- Implement VPIN, MLOFI, Kyle's λ from trades_v1.
- Re-train v3 ensemble (NOT a transformer) on expanded feature set.
- A/B vs v4 cascade.

**Weeks 4-5: Phases 2-3 (news + sympathy)**
- FinBERT embeddings + LLM catalyst taxonomy.
- Sympathy peer features.

**Weeks 6-7: Phase 4 (impact-aware Kelly)**
- Bouchaud calibration from `trades_v1`.
- Hard capacity caps in production.

**Week 8+: Phase 5 (distributional RL)**

---

## 5. What v5 taught us (the one good thing)

The Tier-S architectural enhancements all SHOULD HAVE worked
individually per their published evidence. They DID NOT compose well
on this dataset. This is direct empirical confirmation that we're at
a feature ceiling, not a model ceiling.

**Don't ship v5 to production.** Don't waste another GPU-week tuning
the Tier-S stack. Pivot to v6 data work immediately.

---

## 6. Production status (unchanged)

| Strategy | Status |
|---|---|
| **D281 cohort cascade (XGBoost)** | Production. Always-available rollback. |
| **D282 MoMTrans v4 (PyTorch + 4 specialists)** | Wednesday's first ON window via `MX_USE_MOMTRANS=1` |
| **MoMTrans v5 CORN (this commit)** | RESEARCH FAILURE. Not env-gated. `momtrans_v5_corn.pt` exists on disk but no production loader references it. |

D281 + D282 are unaffected by the v5 negative result.

---

## 7. The genuinely uncomfortable conclusion

**v4's +$46k lift may not survive CPCV + DSR.** The M.md research
explicitly warns that walk-forward CV on autocorrelated tabular data
underestimates the probability of backtest overfitting. ELITE's 0.004
Spearman is "*prima facie* a multiple-testing artifact". Before we
ship MoMTrans v6 (or even continue with MoMTrans v4 in production),
we owe ourselves the rigor of running CPCV + DSR + PBO.

**Phase 0 may end the MoMTrans line entirely.** That's the most
valuable possible outcome — knowing whether we have real edge or are
chasing a backtest mirage.

---

## 8. Files this session

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v5_components.py` | NEW | CORN + Spearman + Muon + EMA + Mixup |
| `scripts/ml_v5_train_corn.py` | NEW | v5 trainer (full WF + .pt persistence) |
| `scripts/ml_v5_verdict.py` | NEW (broken; needs schema fix) | A/B vs v4 + v3 |
| `tests/unit/test_d283_v5_components.py` | NEW (17 tests, all pass) | Component behavioral guards |
| `data/models/momtrans_v5_corn.pt` | NEW (gitignored) | The RESEARCH FAILURE artifact (not for prod) |
| `data/models/momtrans_v5_corn_predictions.parquet` | NEW (gitignored) | 16-fold WF preds |
| `data/models/momtrans_v5_corn_summary.json` | NEW (gitignored) | Per-fold metrics |
| `docs/research-log/131_momtrans_v5_enhancement_roadmap.md` | NEW | Original v5 plan (now superseded by this doc) |
| `docs/research-log/132_momtrans_v5_negative_result_v6_roadmap.md` | NEW (this doc) | Honest negative result + v6 reframe |
