# Phase 4 — Threshold Calibration Report

> ## ⚠️ SUPERSEDED — see Appendix B at the bottom for the corrected findings
>
> **The "+16.19% INVERTED strategy" headline below was inflated by ~6 percentage points
> due to ORB lookahead AND failed a true holdout test. The realistic strategy at honest
> entry timing is +9.86% in-sample / +8.24% on holdout — substantially smaller, with
> n=32 / n=7 sample sizes that warrant shadow validation before any live promotion.**
>
> **The original analysis is preserved below for audit-trail purposes. Do NOT cite the
> +16.19% number. Cite Appendix B.**
>
> Correction performed in Phase 4.6 diagnostics after user pushback. The lesson: CV
> didn't catch the artifact because the lookahead was structural to the universe
> definition (NT-excl-ORB used post-9:35 ORB info while entry was at 9:31), so all 5
> folds shared the same artifact. A true 20% holdout caught it.

---

**Sweep grid:** 6 (max_float) × 1 (min_price held at $0.50) × 5 (vwap) × 6 (mfcs) × 6 (composite) = **1,080 configurations**
**Scenarios per config:** 407 labeled rows from `data/backfill/features_labeled.jsonl`
**Total trades simulated:** 1,080 × 407 = 439,560 per mode
**Modes:** STANDARD (composite filter on cascade BUYs) + INVERTED (composite filter on cascade NT-excl-ORB)
**Runtime:** 100s for both sweeps + CV (single-process)
**Output JSON:** `data/arena_runs/phase4_sweep_results.json`

## ~~TL;DR — three numbers~~ ⚠️ SUPERSEDED — see Appendix B

| Strategy | Best CV avg PnL | CV trades / 79 days | CV stability (folds) |
|----------|------------------|---------------------|----------------------|
| STANDARD (cascade BUYs, composite ≥ 0.40) | **−0.48%** | 17 | wild: +9.5%, −2.8%, −6.6%, −1.2%, −0.8% |
| ~~INVERTED (cascade NT-excl-ORB, composite ≥ 0)~~ | ~~**+16.19%**~~ ⚠️ INFLATED | ~~155~~ | ~~rock-solid: every fold +12% to +20%~~ |
| Best STANDARD (3.0% VWAP, composite 0.40) | +17.59% IS only | 175 IS | (overfit — ignore) |

~~**The headline:** the inverted strategy holds up across all 5 cross-validation folds. The standard strategy's Phase 3 +0.73% finding was IS-overfit and collapses to **−0.48% out-of-sample**.~~

**Corrected headline:** The STANDARD strategy collapses to −0.48% CV (genuine result, kept). The INVERTED strategy's +16.19% required ORB lookahead and failed a true holdout test. The honest realistic-entry version is +9.86% IS / +8.24% holdout on tiny samples — see Appendix B.

## Why the inverted strategy works

Phase 4.0 diagnostic established that the pre-open cascade (D112 router, D101 consensus, D124 alignment, VWAP, MFCS) systematically rejects candidates with median MFE +22.8% and median close +10.2%, while accepting candidates with median MFE +8.1% and median close −7.5%. The mechanism is that the cascade's pre-open rejection criteria correlate with pump-and-dump signals (small float, mixed agent signals, choppy VWAP relationship) — **not** with catalyst quality.

Trading the inversion (everything the cascade rejected EXCLUDING the ORB-held subset) gives you the high-MFE, low-MAE universe.

## CV results — per-fold detail

### STANDARD (cascade BUYs, composite filter)

| Fold | Chosen threshold | Test n | Test WR | Test avg PnL |
|------|------------------|--------|---------|--------------|
| 1 | 0.40 | 3 | 100.0% | +9.47% |
| 2 | 0.40 | 3 | 33.3% | −2.79% |
| 3 | 0.40 | 3 | 0.0% | −6.58% |
| 4 | 0.35 | 5 | 60.0% | −1.20% |
| 5 | 0.40 | 3 | 66.7% | −0.84% |
| **Aggregate** | **0.39 avg** | **17** | **52.9%** | **−0.48%** |

The CV picks composite ≥ 0.40 nearly every fold (the IS optimum) but realized EV bounces from +9.5% to −6.6%. Three of five folds are negative. With only 3-5 test trades per fold, a single bad pick dominates. This is the price of cascade-anti-selection: even after composite filtering, the underlying universe is poor.

### INVERTED (cascade NT-excl-ORB, composite filter)

| Fold | Chosen threshold | Test n | Test WR | Test avg PnL |
|------|------------------|--------|---------|--------------|
| 1 | 0.00 | 32 | 84.4% | +15.95% |
| 2 | 0.00 | 35 | 91.4% | +16.03% |
| 3 | 0.00 | 31 | 83.9% | +17.40% |
| 4 | 0.00 | 31 | 90.3% | +12.37% |
| 5 | 0.00 | 26 | 88.5% | +19.81% |
| **Aggregate** | **0.00** | **155** | **87.7%** | **+16.19%** |

Every fold above +12% with WR 84-91%. CV consistently picks threshold 0.00, meaning composite filtering doesn't improve the inverted set — the set is already high-quality.

## Heatmap: STANDARD `max_float × composite_threshold` (avg PnL %)

`vwap_bias_threshold_pct=2.0`, `mfcs_buy_threshold=0.25` held constant; cell = mean across grid combos with that pair.

| max_float \\ composite | 0.0 | 0.20 | 0.25 | 0.30 | 0.35 | **0.40** |
|------------------------|-----|------|------|------|------|----------|
| 200M | −9.67% | −4.95% | −3.63% | −3.01% | −0.19% | **+0.78%** |
| 500M | −9.67% | −4.95% | −3.63% | −3.01% | −0.19% | **+0.78%** |
| 1B | −9.67% | −4.95% | −3.63% | −3.01% | −0.19% | **+0.78%** |
| 2B (Phase 1 default) | −9.67% | −4.95% | −3.63% | −3.01% | −0.19% | **+0.78%** |
| 5B | −9.67% | −4.95% | −3.63% | −3.01% | −0.19% | **+0.78%** |
| no-gate | −9.67% | −4.95% | −3.63% | −3.01% | −0.19% | **+0.78%** |

**Reading:** every row identical. The composite escape hatch (Phase 1) is doing the work — once it bypasses the float check, the float threshold is irrelevant. ONLY composite_threshold matters.

## Heatmap: INVERTED `vwap × composite_threshold` (avg PnL %)

| vwap \\ composite | 0.0 | 0.20 | 0.25 | 0.30 | 0.35 | 0.40 |
|--------------------|-----|------|------|------|------|------|
| 0.5% | +14.43% | +14.30% | +14.30% | +14.18% | +14.05% | +14.53% |
| 1.0% | +14.93% | +14.79% | +14.79% | +14.68% | +14.55% | +15.07% |
| 2.0% (Phase 1 default) | +16.19% | +16.05% | +16.05% | +15.96% | +15.85% | +16.44% |
| 3.0% | **+17.50%** | +17.36% | +17.36% | +17.15% | +16.94% | **+17.59%** |
| off | +16.19% | +16.05% | +16.05% | +15.96% | +15.85% | +16.44% |

**Reading:** vwap=3.0% is best, but the differences across vwap are small (~3pp). Composite threshold barely affects the result. The strategy is robust to threshold choice — a sign it's a real signal, not p-hacked.

## Recommendation for Phase 5 shadow mode

1. **Log composite + cascade decision per-candidate** (already wired via `--log-composite-score`).
2. **Add a SHADOW INVERTED column:** for every candidate the cascade rejected (NOT at the ORB gate), record what the inverted strategy WOULD have done.
3. **Tomorrow's session is the test.** If the inverted strategy continues to produce 1-3 high-MFE setups per day in live data with synthesized vs. real LLM signals diverging, we have a real edge.

**Do NOT promote the inverted strategy to live trading without at least 5 sessions of shadow data.** The CV result is encouraging, but synthesized agent signals have known bias (they're deterministic functions of pre-market features); real LLM noise may shift the picture.

## Phase 4.4 — second-tier audit

### HUBC scanner-vs-router floor inconsistency

- Scanner: `price_min = $2.00` (`config/settings.py` D219 Phase 1)
- Router: `instant_reject_min_price = $0.50` (`config/settings.py:1338`)
- Apr 16 evidence: HUBC at $0.16 reached the router and was rejected at $0.50 floor — the scanner should have rejected it at $2.00 first.

**Resolution: log finding, no code change.** The composite's `log_price` feature downranks sub-$1 stocks regardless of where the structural floor lands. A $0.16 stock has `log_price = −1.83`, and the model coefficient on `log_price` (from `06_composite_v0_training.md`) will move the composite score below threshold. Defensive structural floors are still useful but the day's bottleneck is the cascade, not the price floor.

### D170 observation window audit

April 16 journal: 0 D170-related rejections out of 85 entries. The observation window only runs after MFCS passes — today no candidates reached that point. **D170 will become relevant once trades start passing MFCS** (post-Phase 1 deployment). Worth re-auditing after tomorrow's session.

## What Phase 5 must measure

1. **Live composite_score on every candidate** — establishes the shadow training set for Phase 6 V1 model retraining with REAL agent outputs (not synthesized).
2. **Cascade decision** — for divergence-budget tracking against the arena's CV results.
3. **The "would have inverted" counterfactual** — flag whenever the cascade rejects a candidate at a non-ORB gate. If, over 5 sessions, those candidates close green at >70% rate, the inverted strategy is real and we ship the cascade replacement (per `04_structural_redesign.md`).

## Caveats acknowledged

- All numbers above are arena-validated. Synthesized agent signals are deterministic; real LLM noise will reduce signal strength. Live AUC and EV will likely be lower than what's reported here.
- The inverted strategy's +16.19% CV avg PnL assumes you can fill at the labeled `entry_price` (9:31 ET open) — real fills have spread + slippage.
- The arena uses close-of-session as exit. Phase 6 may extend to T+15 or trail-stop modeling, which could further improve EV.
- 79 trading days is one regime. The inverted strategy may degrade in different volatility / catalyst regimes. Continued shadow-mode validation across regimes is necessary before live promotion.

## Files generated

- `scripts/d220_phase4_sweep.py` — sweep + CV harness (~250 lines)
- `data/arena_runs/phase4_sweep_results.json` — 1,080 IS combos × 2 modes + CV detail (~1MB JSON)
- This document.

---

# APPENDIX B — Phase 4.6 corrected findings (the honest version)

## Why the +16.19% number was wrong

The user pushed back on the inverted-strategy result before Phase 5 wired anything live, citing four specific concerns: (1) entry price assumption, (2) ORB lookahead, (3) composite-filter null result, (4) MFE/MAE 5:1 ratio that doesn't fit small-cap reality. A 30-minute diagnostic pass run via `scripts/d220_phase4_diagnostics.py` confirmed concerns (2) and (5: holdout overfitting), partially (3), and refuted (1) and (4).

### Diagnostic 1 — entry price (entry assumption was FINE)

| Entry method | n | mean PnL | median PnL | WR |
|--------------|---|----------|------------|----|
| Labeled 9:31 open | 155 | +16.19% | +10.23% | 87.7% |
| First-2-min VWAP | 155 | +15.91% | +9.61% | 85.2% |

Slippage between labeled and realistic VWAP entry: median −0.01%, mean +0.31% (slightly favorable). The entry-price assumption is NOT the source of the inflation.

### Diagnostic 2 — ORB lookahead was the dominant artifact (CONFIRMED)

The "inverted" set is defined as "NO_TRADE excluding ORB-rejections" — but the ORB rejection requires post-9:35 minute bar information. If we make decisions at 9:31 (before ORB info is available), we cannot exclude ORB-rejected candidates from the universe.

| Universe | n | WR | mean PnL |
|----------|---|----|---------:|
| Inverted set (NT-excl-ORB, with ORB lookahead) | 155 | 87.7% | **+16.19%** |
| ALL NT (the only realistic 9:31 universe) | 236 | 60.2% | **+5.87%** |

The +10pp difference (+16.19% vs +5.87%) is entirely attributable to lookahead. The CV didn't catch this because all 5 folds shared the same lookahead structure.

### Diagnostic 5 — true 20% holdout (a 6pp haircut on top of the lookahead)

A 20% random holdout (82 rows) was set aside, never touched by the IS sweep or CV.

| Stratum | n | WR | mean PnL |
|---------|---|----|---------:|
| HOLDOUT inverted (labeled entry, with ORB lookahead) | 32 | 81.2% | **+10.33%** |
| HOLDOUT inverted (realistic VWAP entry, with ORB lookahead) | 32 | 71.9% | **+10.22%** |
| HOLDOUT ALL NT (realistic universe, no lookahead) | 50 | 52.0% | **+0.62%** |

Even with ORB lookahead, the holdout drops the mean from +16.19% to +10.22% — confirming additional sweep-tuning overfit on top of the lookahead artifact.

### Diagnostic 6 — the ONLY honest version of the strategy

Decision time: **9:36 ET** (after 9:35 close is observable).
ORB filter: candidate must have broken its 5-min opening-range high by 9:35.
Entry: VWAP of 9:36-9:37.
Exit: session close (matches arena's exit logic).

| Universe | n | WR | mean PnL | median PnL |
|----------|---|----|---------:|-----------:|
| ALL NT, ORB-broken-by-9:35 (full set) | **32** | 65.6% | **+9.86%** | +7.07% |
| NT-excl-ORB, ORB-broken-by-9:35 (post-hoc subset) | 30 | 70.0% | +11.33% | +8.00% |
| HOLDOUT same strategy | **7** | 71.4% | **+8.24%** | n/a (small) |

**Honest deployable strategy: ~32 trades over 79 days (~150/year), 65-72% WR, +8% to +10% per trade.** Sample sizes (n=32 IS / n=7 holdout) are too small for a confident point estimate; the realistic per-trade EV range is somewhere between +2% and +10%, with a wide CI.

## What stays true from the original Phase 4 report

- **The STANDARD strategy at composite≥0.40 is genuinely overfit.** Phase 3's +0.73% IS collapses to −0.48% CV. CV did its job there.
- **The structural gates (max_float, vwap, mfcs) don't drive the result.** Heatmaps showed that swapping any of them within reasonable ranges moved the result <2pp.
- **The composite score IS predictive.** Spearman +0.367 between composite_full and realized PnL on cascade-BUYs holds.
- **The cascade-anti-selection diagnostic finding is real but smaller.** The pre-open cascade rejects candidates with MEDIAN MFE +22.8% vs BUY's +8.1%. The mechanism is correct (cascade selects pump-prone setups). But the LIVE deployable strategy needs a 9:36 decision time, not the 9:31 framing the original report used.

## What Phase 5 ships under the corrected framing

1. **Composite shadow** — unchanged. Per-candidate composite_score logged at evaluation time. Logs to `data/shadow/shadow_<date>.jsonl` with `kind=composite_shadow`. Independent kill switch `SHADOW_SCORING_ENABLED`.
2. **Inverted shadow** — paranoid schema with hard-enforced timing invariants (decision_timestamp ≥ 9:36, orb_break_timestamp < decision_timestamp, simulated_entry_basis from fixed enum). Logs to same JSONL with `kind=inverted_shadow`. Independent kill switch `SHADOW_INVERTED_ENABLED`.
3. **Production never reads shadow fields.** Static AST guard at `tests/static_analysis/test_shadow_isolation.py` enforces this. The shadow JSONL is a write-only side channel.
4. **No live promotion until 5+ shadow sessions confirm** the +8-10% per-trade range survives real-LLM noise + realistic fills. Phase 5 ships only the telemetry; tomorrow morning is the first measurement.

## Lessons for the research process

1. **CV is necessary but not sufficient.** When the artifact is in the universe definition (lookahead structural to how candidates were filtered), all CV folds share the artifact. **Always pair CV with a true holdout** that's set aside before any tuning.
2. **"Free money" hypotheses warrant the strictest possible scrutiny.** The +16.19% / 87% WR profile was the user's signal that something was wrong. A real edge in this universe looks like 55% WR / +2% per trade. When a result is too good, it's the measurement, not the market.
3. **Visible corrections beat quiet retractions.** Future-Pierce reading this doc in three months needs to see the +16.19% claim alongside the SUPERSEDED marker, not a cleaned-up version that erases the mistake. Research integrity runs on the trail of things you walked back.

## Files (Phase 4.6 additions)

- `scripts/d220_phase4_diagnostics.py` — the diagnostic harness (5+1 checks)
- This appendix
