# 248 — Phase 0 + raw-tape Deep-Sets screen: the architecture bet is comprehensively FALSIFIED. The tape improves AUC but DESTROYS realized return. Pivot to data.

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: doc 247's cost-ordered protocol — Phase 0 (free referee) then, on Pierce's call ("take the raw-tape shot"), the Phase-2 Deep-Sets mean-pool screen (KILL CRITERION 2a).

## TL;DR
Two cheap experiments (one afternoon, ~1 GPU-hour total) **comprehensively close the architecture question**: no model class extracts a profitable cross-regime top-slice from the 9:50 microstructure (Phase 0), and the **raw un-aggregated tape — the one input doc 245 hadn't tested — improves rocket RANKING (AUC up in all 3 regimes) while making the traded slice CONFIDENTLY WORSE** (Deep-Sets screen). The bottleneck is the **information available at 9:50**, which is *shared* between rockets and their lookalike faders — not the model, not the aggregation. **The only un-falsified levers are DATA** (free-float, catalyst/news content). No live change.

## Phase 0 — the overfit-proof referee + separability probe (LORO, top-5% realized EOD return)
| regime | base | GBM | **TabPFN (referee)** | kNN | kNN_AUC | oracle |
|---|---|---|---|---|---|---|
| 2024 | 1.9% | −1.7% | **−6.1%** `[−10,−1.5]` | −5.2% | 0.656 | **+41%** |
| 2025 | 2.0% | +1.5% | **−6.1%** `[−9.5,−2.1]` | −1.7% | 0.724 | **+42%** |
| 2026 | 1.8% | +10.2% | **−2.6%** | +0.1% | 0.766 | **+40%** |
- **The frozen, overfit-proof TabPFN-v2 referee is the WORST in every regime, including 2026.** Its weights never train, so it cannot memorize the 30 2026 rockets — and it loses everywhere. **Capacity was never the constraint**; the GBM's 2026 "edge" is largely an artifact of its class-balancing (and its LORO 2026 CI straddles 0).
- **Separability exists (kNN_AUC 0.66-0.77) but does NOT convert** (kNN top-slice negative). Rockets are rankable above *random* faders, not above the faders that look most like rockets.
- **Oracle +40% every regime:** the rockets are real and huge — just **ex-ante unreachable from these 25 features.** The pre-registered escalation condition (TabPFN *less* negative) was NOT met.

## The raw-tape Deep-Sets screen — KILL CRITERION 2a met, emphatically
A learned per-trade transform + masked **mean-pool** over the un-aggregated 09:30-09:50 trades (per-day-normalized channels: signed_size_frac, log_price/vwap, dt_frac, is_block, is_odd_lot, frac_elapsed — 120.7M trades across 10,254 ticker-days), fused with the 25 context features, 5-seed ensemble, GPU, LORO:
| regime | GBM | GBM AUC | DeepSets | **DeepSets AUC** | DS−GBM money |
|---|---|---|---|---|---|
| 2024 | −1.7% | 0.691 | −7.7% | **0.700** | −6.0% |
| 2025 | +1.5% | 0.739 | −5.2% | **0.748** | −6.8% |
| 2026 | +10.2% | 0.835 | +0.1% | **0.905** | −10.1% |
| **pooled 24+25** | **−0.5%** | | **−7.1%** | | **paired −6.6%, CI [−10.0, −2.6]** |

- **The purest AUC≠P&L result in the project:** the raw tape *improves rocket ranking in every regime* (2026 AUC 0.835 → **0.905**) yet **destroys the top-slice realized return** (paired −6.6%, CI entirely negative).
- **Mechanism (now confirmed, not hypothesized):** the tape signature that best identifies rockets — violent block-print bursts + trade intensity — is *also* the signature of the most violent **faders** (gap-and-crap). The **top-5% slice is exactly where the lookalike faders concentrate**, and their −30%+ crashes dominate the mean. Getting *better* at "rocket-like" makes the traded slice *worse*. This is the fader-lookalike trap Phase 0 flagged, demonstrated directly on the raw tape.
- **Verdict:** DeepSets does not beat the GBM on the pooled 2024+2025 block — it is CI-separated *worse*. Per the pre-registered kill criterion, **the full Set Transformer will not rescue it.** The raw asynchronous tape carries no transportable rocket *return* signal the aggregates missed; what it adds (ranking) is anti-correlated with the traded-slice return.

## Unified verdict — ex-ante rocket selection from 9:50 micro+tape does not generalize
Across the whole arc the falsification is now comprehensive and consistent:
- **Structure (235), catalyst-type (240), L2/true-OFI (244), shares-outstanding float (243)** — no cross-regime edge.
- **25 aggregated features, every model class (boosting / in-context Bayesian / instance kNN)** — Phase 0: no profitable cross-regime top-slice; the overfit-proof referee is *worst*.
- **Raw un-aggregated tape (Deep-Sets)** — improves AUC, worsens money; KILL.
The bottleneck is **the information observable at 9:50** — price/volume/tape structure is *shared* between rockets and the faders that most resemble them. A better model or a richer encoder of the *same* observation window cannot separate what is not separable in that window. The right tail is **ex-ante random *conditional on what's visible at 9:50*.**

## The only un-falsified levers are DATA (not architecture)
Per doc 246's register — genuinely NEW information the 9:50 window lacks:
1. **True FREE-float** (Finnhub `floatShares`): the squeeze *constraint* (locked-up insider shares excluded) — doc 243 only killed shares-outstanding. The supply side a rocket needs is invisible in price/tape.
2. **Catalyst / news CONTENT** (Polygon `/v2/reference/news` + a working LLM tagger): *why this stock* — catalyst novelty + dilution risk (toxic-lender/offering → sell-the-news). doc 240 only killed catalyst-*type homogeneity*, never extracted content.
If both also fail the cross-regime money gate, the honest conclusion is that the right tail is not ex-ante selectable on this universe at the 9:50 decision point, and the program ships the GBM as a within-regime ranker + the doc-246 forward shadow only.

## Status
**No live change.** The architecture bet was closed for ~1 afternoon + ~1 GPU-hour — the cost-ordered protocol worked exactly as designed (the free Phase 0 + the cheap mean-pool screen gated the GPU-week that the full Set Transformer would have cost). AUC is retired as a decision metric — doc 248 is its tombstone (AUC ↑, money ↓, simultaneously, cross-regime).
**Basis**: `scripts/rocket_phase0_diagnostic_doc247.py`, `scripts/build_rocket_trade_cache_doc248.py` (120.7M trades cached), `scripts/rocket_deepsets_screen_doc248.py`. **Predecessors**: 247 (the protocol), 245 (the falsified baseline), 246 (the data-lever register). **Memory**: [[bet3-rocket-detection]], [[trades-parquet-tzbug]].
