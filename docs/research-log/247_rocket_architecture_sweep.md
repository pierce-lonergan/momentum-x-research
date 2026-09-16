# 247 — SOTA architecture sweep for cross-regime rocket detection: the honest verdict + a cost-ordered decision protocol

**Author**: Claude Opus 4.8 (8-agent workflow: 6 dimension researchers → lead-architect synthesis → adversarial feasibility/EV)
**Date**: 2026-06-03
**Mandate**: Pierce — "deep architectural research sweep: identify/adapt/benchmark SOTA ML architectures that could rival or beat the GBM on the rocket extreme-tail problem; ignore data-scarcity priors; evaluate purely on structural capacity." 8 Opus agents, 717K tokens, 146 tool-uses.

## Verdict up front (the honest, evidence-grounded answer)
**~65-70% probability: nothing ships, and that is the correct result.** The binding cross-regime test this sweep was meant to motivate **has already been run** (doc 245: CPCV *and* leave-one-regime-out, tape included) and **FAILED** the all-3-regime gate. The regime-dependence is most likely a **DATA truth, not a capacity limit** — the marginal buyer of low-float gappers changed between 2024-25 and 2026, and with **216 rockets** (2024=69, 2025=117, **2026=30**) the bottleneck is generalizable *signal*, not model capacity. A higher-capacity model on the same 25 features can only **fit 2026 harder** (AUC already 0.83 there; top-1% = ~16 names total — the textbook overfit magnet).

**The one genuine ~25-30% shot:** doc 245 falsified the **11 hand-aggregated tick scalars, NOT the raw asynchronous tape.** A permutation-invariant encoder over the *un-aggregated* opening-trade SET is the **only proposal in the entire six-dimension sweep that ingests information doc 245 did not already falsify.** Even its best realistic outcome justifies a forward *paper* shadow of the neural selector — not capital.

**Therefore the deliverable is not "pick a transformer" — it is a decision protocol that spends compute in strict ascending cost order, front-loaded with a free experiment that can close the whole question in an afternoon.** AUC is retired entirely as a decision metric (the 0.65→0.83-by-year AUC climb *is* the trap: ranking improves while the traded slice loses money).

## Ranked architectures — the real-mechanism-vs-fits-2026 split
Ranked by **expected information per GPU-hour on the binding metric** (cross-regime top-slice realized return), not paper pedigree.

| # | Architecture | EV | Role | Why |
|---|---|---|---|---|
| 1 | **TabPFN v2** (frozen in-context Bayesian) | **High** | **Overfit-proof REFEREE** | Weights never train → *cannot* memorize the 30 2026 rockets a 300-tree GBM can. Under LORO: if its 2024/25 top-slice is *less* negative than the GBM's → capacity-overfit existed + a calibrated edge survives; if *equally* negative → "tail is ex-ante random outside-regime" hardens to near-definitive. Retires or escalates the program in **minutes**. (Prior `ml_v6_*tabpfn*` work was on the *continuation* label — rocket-binary is new.) |
| 2 | **Set Transformer / Deep-Sets** over the RAW opening-trade set | **Medium** (highest ceiling) | **The one genuine shot** | The 11 tick aggregates are *lossy moments*; doc 245 falsified the moments, not the tape. A permutation-invariant encoder over ~1k-40k trades can learn joint size×timing×print-type structure (clustered block-absorption bursts) the moments discard. Honest odds: ~25-30% it rescues the 2024+2025 block, ~60% it overfits 2026, ~10% inconclusive. **A 2026-only result is a FALSIFICATION, not a success.** |
| 3 | Mamba/S6 over the raw tick stream | Medium− | Variant of #2 | Same un-falsified input, but *order-sensitive* where #2 is order-invariant. For "who is accumulating," permutation-invariance is the better bias + harder to overfit. Linear complexity is *feasibility, not edge*. Build only if #2 hits the tick-length wall. |
| 4 | **SAM/ASAM + ASL + day-grouped listwise loss** | High (among loss levers) | **Multiplier**, neural-only | The only loss/opt lever *aimed at* regime-robustness (flat minima suppress the simplicity-bias shortcut riding a 2026-only feature). ASL (γ⁺=0 hard rule, γ⁻=4) fixes the 2% base rate; LambdaRank(group=session_date, k=5) aligns the objective with "pick top-1 of today's gappers." Converts existing signal to dollars; **cannot conjure 2024 signal.** |
| — | FT-T / SAINT / TabNet / TabM / TabR / PatchTST / iTransformer | **Low** | **Logically barred** | All eat the **same 25 flattened features doc 245 already falsified** → can only re-rank a falsified space. (PatchTST/iTransformer near-degenerate: only ~4 ex-ante 5-min bars at 9:50. TabNet worst: hard-sparsemax starves weak tail features.) |
| — | Payoff-weighted / soft-Sharpe PnL loss | **Low** | **OVERFIT TRAP** | Optimizes dollars that *exist only in 2026* → pooled training learns "patterns that paid in 2026." Only as a winsorized light final fine-tune, judged on LORO. |
| — | MoE regime-routing | **Low** | Skip | Presupposes ex-ante regime ID (the thing you can't do); at 30 rockets the router memorizes regime identity — the 2026-overfit failure mode *in architecture form*. |

## The one recommended architecture (if Phase 0+1 show life): `RocketSetNet`
A **≤350k-param** per-day-normalized **Deep-Sets→Set-Transformer** tape encoder, fused with the 25 macro/aggregate features, SSL-pretrained on all 10,552 unlabeled days then **frozen** with only a tiny head fine-tuned on the 216 positives, trained with an **asymmetric + day-grouped listwise loss under ASAM**, seed-ensembled ≥10.
- **Input (per ticker-day):** SET branch = up to 2048 importance-sampled trades (keep ALL `size≥5000` blocks), 8 channels each — **per-day-normalized** signed_size_frac (÷ cum $vol), log price/vwap, dt_frac (÷ session), is_block, is_odd_lot, frac_session_elapsed, cum_$/adv20, venue embedding. CONTEXT branch = the 25 aggregates (rank-Gaussian, train-fold only). Empty tape → degrades gracefully to context-only.
- **Why a SET, not the reports' HAN/twin-tower:** those have *more* params and *more* ways to memorize 30 2026 rockets, for a *worse* inductive bias (order-sensitivity when the signal is distributional). A set encoder is the minimal architecture capturing "who is accumulating" while being maximally hard to overfit.
- **Loss:** `ASL(γ⁺=0, γ⁻=4, m=0.05) + 0.5·listwise_topk(query=session_date, k=5, graded by winsorized eod)`. **γ⁺=0 is a hard rule at 2% prevalence — never down-weight a rocket gradient.**
- **Optimizer:** ASAM (ρ tuned on LORO, not pooled CV) wrapping AdamW; dropout 0.3-0.4; SWA; seed-ensemble (variance at 216 positives is the dominant error term). **Early-stop on the smoothed top-decile realized-return surrogate on 2024+2025 inner-val — never on AUC or raw top-1% $.**

## Pre-registered benchmark blueprint (lock before any model run)
- **CPCV:** atom = ticker-day; **8 contiguous, regime-stratified, time-ordered groups** (each year tiled into sub-blocks so every test combo spans ≥2 regimes → ~20-30 rockets/group, no zero-rocket fold). k=2 test groups → C(8,2)=28 splits, 7 OOS paths **treated as a fragility diagnostic, NOT 7 i.i.d. samples** (they share ≥4/6 train groups). **Purge** any train day whose trailing-20d `adv20` window intersects a test date (the only true cross-day leak — intraday labels don't cross sessions); **embargo = 21 trading days** (not the harness's current 1). **KEEP `ENTRY=3`** (= 9:50, verified on 10,552 ticker-days — the dimension report's "off-by-one" was FALSE).
- **Binding metric + PASS RULE (frozen):** primary = top-5% mean realized EOD-from-9:50 return, **per regime, day-block BCa bootstrap (10k reps, resample whole days)**. Top-1% = directional tie-break only (too thin for a CI). PASSES iff: (1) top-5% lower-95%-CI **>0 in ALL 3 regimes**; (2) **paired** (candidate−LightGBM, same-day) lower-CI **>0 in 2024 AND 2025**; (3) lift CI excludes 1 all three; (4) no 2026 regression. **RESCUE tier** (given n): positive all 3, lower-CI>0 on the **pooled 2024+2025 block**, CI-separated paired margin there.
- **Exact LightGBM floor to beat** (intentionally slightly under-fit; *expected* to fail 2024/25 — that's the premise): `n_estimators=2000, lr=0.02, num_leaves=15, max_depth=4, min_child_samples=100, min_split_gain=0.01, reg_alpha=1.0, reg_lambda=5.0, feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, max_bin=127`, `scale_pos_weight∈{1,7,49}` tuned on **LORO $-metric not AUC**, early_stopping=100, isotonic-calibrated, 10 seeds.
- **Campaign multiple-testing:** White Reality Check / Hansen SPA (`arch.bootstrap.SPA`) over all architectures vs GBM on the *same* day-block resamples; effective-K via clustering of rank-correlations (near-identical rankings count as ~1 trial); deflate the winner. Ship only if it clears the pass rule **AND** survives SPA, in 2024 and 2025.

## Phased week-long plan with explicit KILL CRITERIA (inverted: free experiment first)
**Phase 0 — the logically-prior FREE diagnostic (½ day, no GPU) — DO FIRST.**
1. **Oracle + Bayes-error ceiling** + **kNN rocket-purity** on the 25-D matrix, per regime (are 2024/25 rockets separable from faders *at all*?).
2. **TabPFN-v2 referee** (reuse `tabpfn_shadow_runner.py`) in the doc241 CPCV+money harness on the rocket label, under LORO.
3. **Order-flow-confirmation proxy** on existing aggregates: does `large_print_ratio` high ∧ `odd_lot_ratio` low ∧ `tick_ofi_late`>0 give a positive top-slice in all 3 regimes?
> **KILL 0 (closes the entire arc):** if 2024/25 kNN-purity ≈ base-rate AND TabPFN's 2024/25 top-slice is as negative as the GBM's AND the proxy is flat/negative → the tail is ex-ante random in those regimes *in this representation*; no architecture can fix it from the 25 features. Ship the GBM (within-regime ranker) + the doc-246 forward shadow only. **This is the most probable outcome and a first-class negative.**

**Phase 1 — loss/optimizer on the existing tree pipeline (1 day, ~0 compute) — only if Phase 0 shows life.** LightGBM `objective='lambdarank'` (group=session_date) + ASL via custom `fobj`, **every hyperparam tuned on LORO not pooled CV** (pooled tuning systematically selects 2026-overfit configs).
> **KILL 1:** if LORO-tuned ranking+ASL doesn't move the 2024+2025 combined block directionally-positive + CI-separated from plain GBM → the loss wasn't the bottleneck; a neural model won't rescue it either. Ship the shadow.

**Phase 2 — `RocketSetNet` (3-4 days) — only if Phase 0 AND 1 pass.** Build per-day-normalized raw-trade-set tensors (reuse `build_xregime_tick_features_doc245.compute_tick_feats` per-trade; **ET from raw `sip_timestamp`, never `ts_et`**). **3-seed/4-fold Deep-Sets-mean-pool screen FIRST.**
> **KILL 2:** (a) mean-pool screen ≤ GBM on 2024+2025 → kill before the full run; (b) full run **positive in 2026 only → that is the falsification, not a success**; (c) 2024 profitability flips sign across the 10 seeds → seed-specific quirk → kill.

**Phase 3 — campaign verdict (½ day):** SPA vs GBM at clustered effective-K on the 2024+2025 endpoints; deflate; ship only if it clears the pass rule *and* survives.

**Budget:** Phase 0 (½d) + Phase 1 (1d) gate ~80% of the probability mass **before any GPU-week.**

## The single cheapest falsifier (afternoon, no GPU)
On the existing 25-feature matrix, per regime: compute **kNN rocket-purity** of the top-scored neighborhood AND run the **TabPFN referee under LORO**. If 2024/25 purity ≈ 2% base-rate (the live baseline already shows **2024 top-1% rocket-precision = 0%** — zero rockets in the slice) AND TabPFN's 2024/25 top-slice = the GBM's → rockets and faders are interleaved in this space, capacity was never the constraint, and **no encoder can separate what is not separable from these inputs.** Redirect the week to the only un-falsified feature classes (true free-float, catalyst/news content — doc 246's register), which are **DATA, not architecture.**

## Corrections to the dimension reports (for the record)
1. **The "ENTRY off-by-one bug" is FALSE.** `ENTRY=3` = positional row index → minute_idx 20 → **9:50** (verified on 10,552 ticker-days). Changing it to 4 would shift entry to 9:55 and silently corrupt every label/feature. **Do not change it.**
2. **The cross-regime test is not un-run.** Dimensions 1-3 framed "build the encoder, then run the per-regime money test" as the novel contribution; doc 245 already ran it (CPCV + LORO) and it failed. The contribution is the *decision protocol*.
3. **Counts:** 216 rockets (not 202); 2026 = 30 (top-1% ≈ 16 names — under-stressed how overfit-prone 2026 is).

## Recommendation
**Run Phase 0 now.** It is free, needs no new infra (TabPFN 7.1.1 + the harness exist), and is decision-defining: it either retires the architectural bet (most likely) or escalates it with evidence. I do **not** recommend any GPU-week until Phase 0 + Phase 1 both show cross-regime life. **No live change.**

**Basis**: workflow `wf_2442c6ef-ce9` (8 agents). **Predecessors**: 245 (the falsified baseline this must beat), 246 (forward shadow + untested-lever register), 241/242. **Memory**: [[bet3-rocket-detection]], [[trades-parquet-tzbug]].
