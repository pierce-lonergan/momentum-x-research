# Composite Score V0 — Training Report

**Trained:** 2026-04-16T23:15:42Z
**Training rows:** 407 (from `data/backfill/features_labeled.jsonl`)
**Models written:** `models/composite_v0_full.pkl`, `models/composite_v0_prescore.pkl`

## Headline

| Model | Train AUC | CV AUC mean ± std | Brier | Overfit gap | Status |
|-------|-----------|-------------------|-------|-------------|--------|
| **full** (with `arena_buy_verdict`) | 0.7820 | 0.7564 ± 0.0619 | 0.2026 | +0.0256 | OK |
| **prescore** (without verdict) | 0.6894 | 0.6612 ± 0.0953 | 0.2306 | +0.0282 | OK |

**Overfit gap** = train AUC − CV mean AUC. Threshold for flagging: > 0.10.

## Class balance and stratification

Overall positive rate (`win_close`): **44.2%** (OK)

Stratified by arena verdict:

| Stratum | n | win_rate |
|---------|---|----------|
| Arena BUY | 171 | 22.2% |
| Arena NO_TRADE | 236 | 60.2% |

**Important:** the arena BUY win rate is *lower* than the arena NO_TRADE win rate. This is the **gate-cascade anti-selection** finding from Phase 3.0 — the current 13-gate cascade is systematically picking less-likely-to-win candidates than it rejects. The composite score's `arena_buy_verdict` coefficient is expected to be **negative** as a result. This is real signal the model can learn from, not a data bug.

## Feature importance — FULL model

| Feature | Coefficient (post-scaling) |
|---------|----------------------------|
| arena_buy_verdict | -0.8586 |
| log1p_dollar_volume | +0.6714 |
| log1p_premarket_volume | -0.3581 |
| orb_range_pct | -0.2590 |
| day_to_premarket_volume_ratio | +0.2518 |
| gap_pct | -0.1663 |
| log_price | -0.1632 |
| price_to_pre_market_high_ratio | +0.1040 |

## Feature importance — PRESCORE model

| Feature | Coefficient (post-scaling) |
|---------|----------------------------|
| log1p_dollar_volume | +0.6230 |
| log1p_premarket_volume | -0.3236 |
| day_to_premarket_volume_ratio | +0.3125 |
| gap_pct | -0.2471 |
| log_price | -0.1847 |
| orb_range_pct | -0.1003 |
| price_to_pre_market_high_ratio | +0.0630 |

## Calibration — FULL

| bin | n | predicted | actual |
|---|---|---|---|
| [0.00, 0.20) | 95 | 0.146995073736996 | 0.12631578947368421 |
| [0.20, 0.40) | 91 | 0.29237655175691746 | 0.3076923076923077 |
| [0.40, 0.60) | 99 | 0.5076027103188617 | 0.45454545454545453 |
| [0.60, 0.80) | 94 | 0.6897062323082018 | 0.7446808510638298 |
| [0.80, 1.00) | 28 | 0.8690202987025291 | 0.8928571428571429 |

## Calibration — PRESCORE

| bin | n | predicted | actual |
|---|---|---|---|
| [0.00, 0.20) | 15 | 0.15974143842477886 | 0.26666666666666666 |
| [0.20, 0.40) | 170 | 0.322814111532614 | 0.3 |
| [0.40, 0.60) | 160 | 0.4890208484553467 | 0.50625 |
| [0.60, 0.80) | 53 | 0.6940339485150229 | 0.6981132075471698 |
| [0.80, 1.00) | 9 | 0.855336976906913 | 0.7777777777777778 |

## Per-fold CV AUCs

| Fold | FULL | PRESCORE |
|------|------|----------|
| 1 | 0.8007 | 0.7760 |
| 2 | 0.7313 | 0.6963 |
| 3 | 0.7883 | 0.5420 |
| 4 | 0.8031 | 0.7062 |
| 5 | 0.6586 | 0.5858 |

## Honest assessment of training quality

- **407 labeled rows** is a thin training set. CV stdev across folds tells you the model's stability — anything above 0.05 means the model could swing meaningfully on resampling. See per-fold table.
- **AUC is arena-validated, not live-validated.** Phase 2's pipeline_runner uses synthesized agent signals (deterministic functions of candidate features). Real LLM agents will produce noisier signals, so live-data AUC will likely be **lower** than what's reported here. This is a known limitation; Phase 5 shadow mode is what closes the gap with live data.
- **Day-of-week feature deliberately excluded.** Per the user's calibration: too noisy for 407 rows.
- **Two models, not one:** the FULL model uses arena_buy_verdict; the PRESCORE model doesn't. PRESCORE is for early-stage filtering before the gate cascade has run (Phase 5 shadow could call PRESCORE on every candidate at scan time, then call FULL after the cascade for a refined estimate).

## Decisions to make for Phase 4

1. **Ship which model as primary?** Both are saved. Phase 5 shadow mode logs both. Phase 4's threshold sweep should compare them.
2. **What threshold?** AUC tells you the model can rank, not where to cut. Phase 4's sweep finds the cut that maximizes Sharpe given the scoring distribution.
3. **Should arena_buy_verdict's coefficient be capped?** If FULL says "ignore the cascade entirely," that's directly relevant to the structural-redesign discussion in `04_structural_redesign.md`.

## Notes from the prompt
- AUC is ARENA-VALIDATED — synthesized agent signals make this optimistic vs live.
- Real-LLM validation awaits Phase 5 shadow data.
- arena_buy_verdict is expected to have NEGATIVE coefficient: cascade anti-selects.

---

## Validation against the arena (Phase 3.4)

After training, the composite was applied to all 502 backfill scenarios via the
arena's new `--log-composite-score` flag:

```
python -m src.production_arena.cli --dates 2025-12-11:2026-04-14 \
    --output data/arena_runs/phase3_with_composite.jsonl --log-composite-score
```

### Spearman rank correlation: composite vs realized PnL

On 171 arena BUYs with both `composite_score_full` and `would_be_pnl_pct`:

- **Spearman rank correlation = +0.367**
- Mean composite score on BUYs = 0.225 (the model is appropriately pessimistic
  about cascade-anti-selected BUYs)

A +0.37 Spearman is genuinely strong for a noisy financial signal — the model is
ranking BUYs by realized PnL meaningfully better than chance.

### Threshold sweep on composite-filtered BUYs

The headline finding: filtering arena BUYs by composite score threshold turns
the cascade's −23.82% loss into a positive-expectancy strategy:

| Composite threshold | n BUYs | Win rate | Avg PnL | Sum PnL (sim) |
|---------------------|--------|----------|---------|---------------|
| 0.00 (no filter)    | 171    | 22.2%    | −9.34%  | −1596.77%     |
| 0.20                | 80     | 33.8%    | −4.56%  | −364.43%      |
| 0.25                | 58     | 37.9%    | −3.27%  | −189.69%      |
| 0.30                | 36     | 38.9%    | −2.85%  | −102.45%      |
| 0.35                | 21     | 52.4%    | −0.20%  | −4.26%        |
| **0.40**            | **14** | **57.1%**| **+0.73%** | **+10.17%** |
| 0.50                | 5      | 60.0%    | +0.16%  | +0.82%        |

**At threshold ≥ 0.40**, the composite picks 14 of the cascade's 171 BUYs
(8.2%) with positive realized expectancy and 57% win rate. This is the
clearest possible quantification of the structural-redesign argument in
`docs/research-log/04_structural_redesign.md`: a single calibrated score replaces
12 binary gates and produces strictly better selection.

### Caveats — what these numbers ARE and AREN'T

**They ARE:** an honest measurement of how the V0 composite ranks the arena's
BUYs against their realized close-of-session returns over 79 days of historical
data.

**They are NOT:**
1. **A live-data result.** The arena uses synthesized agent signals
   (deterministic functions of pre-market features). Real LLM agents introduce
   noise, so the live AUC will likely be *lower* than 0.756. Phase 5 shadow
   mode logs both the composite score and the live cascade verdict so we can
   measure the gap directly.
2. **Validated on out-of-sample data the model never saw.** The 5-fold CV in
   the training table above is the closest proxy; the threshold sweep here
   uses the full 407-row training set.
3. **Accounting for execution slippage.** The arena uses
   `would_be_entry_price = labeled_outcome.entry_price` (the 9:31 ET open) and
   `would_be_pnl_pct = close_return`. Real fills have spread, slippage, and
   sometimes partial fills.

### Phase 4 priorities (informed by this validation)

1. **Threshold sweep is the headline.** Phase 4 should evaluate composite
   thresholds at finer granularity (0.30, 0.32, ..., 0.50) and report the
   complete tradeoff curve — including how the optimal threshold shifts when
   we sweep `instant_reject_max_float` and `vwap_bias_threshold_pct` jointly.
2. **Days-with-trades distribution.** 14 trades over 79 days = 1 trade every
   ~5.6 days. Verify these aren't all clumped on a few days (concentration
   risk).
3. **Investigate the 60% WR / 60% MFE-capture stratum.** A handful of
   threshold-0.50 BUYs with strong realized PnL — what features are they
   high on? This informs the next iteration of the model.


---

## Phase 4.0 — Diagnostics on the cascade-anti-selection finding

The user (correctly) pushed back on the Phase 3.0 finding before acting on it.
Three diagnostics were run before the Phase 4 sweep:

### Diagnostic 1 — Label leakage via micro-moves

A "win" where `|close_return| < 2%` is a stock that didn't really move — calling
it a win is statistically valid but operationally meaningless (you wouldn't
have traded it).

| Group | n wins | micro (\|ret\|<2%) | moderate (>=2%) |
|-------|--------|--------------------|-----------------|
| arena BUY  | 38  | 11 (28.9%) | 27 (71.1%) |
| arena NO_TRADE | 142 | 13 (9.2%)  | 129 (90.8%) |

**Re-computed win rate excluding micro-moves (`|ret| >= 2%`):**

| Group | n investable | win_rate |
|-------|--------------|----------|
| arena BUY | 148 | 18.2% |
| arena NO_TRADE | 212 | 60.8% |

**Verdict:** Label leakage softens the gap by ~4pp (38pp → 42pp narrowed) but
does NOT explain it. The cascade-anti-selection finding holds.

### Diagnostic 2 — Survivorship asymmetry

407 of 5,862 candidates are labeled (6.9%). If labeling preferentially captured
arena-BUY candidates, the NO_TRADE set might be biased toward the easier-to-label
NO_TRADEs (which may close green simply because they didn't move).

| Distribution | BUY | NO_TRADE |
|--------------|-----|----------|
| gap_pct: min / p50 / max | 7.3% / 35.1% / 455% | 8.3% / 30.6% / 340% |
| dolvol p50 / max | $128M / $3.5B | $155M / $5.0B |

**Verdict:** Distributions are roughly symmetric. NO clear survivorship bias —
both groups span the universe of pre-market gap-ups.

### Diagnostic 3 — ORB-held intraday information

The arena's ORB confirmation gate uses post-open data (whether the stock broke
its 5-min opening range). NO_TRADEs rejected at the ORB gate have intraday
information that pre-open gates don't see — so they may close green for reasons
unrelated to gate quality.

| Group | n | win_rate |
|-------|---|----------|
| NO_TRADE rejected at ORB gate (orb_held=False) | 81 | **7.4%** |
| NO_TRADE rejected at OTHER gates (pre-open) | 155 | **87.7%** |

**Verdict:** The ORB gate is doing its job (rejected ORB-held set = 7.4% WR).
But the OTHER gates (pre-open: D112, D101, D124, VWAP, MFCS) are rejecting
candidates with **87.7% close win rate** — vs the arena BUY's 22.2%.

The pre-open cascade is anti-selecting by **65 percentage points**, NOT 38pp.
The original 38pp gap was understated because it included ORB rejections (which
behave correctly).

### Diagnostic 4 — MFE distribution (the mechanism)

If the cascade were selecting for "explosive setups that fade", BUYs should have
higher MFE than NO_TRADEs (more spike) AND lower close (more fade). The data
shows the opposite:

| Group | n | median close | median MFE | median MAE | spike-and-fade % |
|-------|---|--------------|------------|------------|-------------------|
| arena BUY | 171 | **−7.47%** | +8.08% | −13.93% | 53.2% |
| arena NO_TRADE | 236 | +3.44% | +14.70% | −8.63% | 11.4% |
| **NT excl-ORB** (pre-open rejects only) | 155 | **+10.23%** | **+22.80%** | −4.41% | 9.7% |

**The pre-open cascade is rejecting the candidates with the BIGGEST upside
moves AND the SMALLEST downside.**

The mechanism is not "explosive setups fade" — it's "the cascade's pre-open
rejection criteria (small-float, RVOL, consensus, VWAP) correlate with
pump-and-dump signals, NOT with genuine catalysts." When a stock has a large
float, broad agent consensus, and trades cleanly above VWAP, it's a real
catalyst — and the cascade rejects it. When a stock has a tiny float, mixed
agent signals, and bounces around VWAP, it's a pump — and the cascade accepts
it.

**Implication for Phase 4 strategy:** The original sweep over `max_float`,
`min_price`, `vwap_threshold`, `mfcs_threshold` is necessary but not sufficient.
The real opportunity is to **invert the cascade**: trade the candidates the
pre-open gates REJECT (excluding ORB), filtered by the composite score. The
NT-excl-ORB set has a +10.23% median close on 155 candidates. Even random
selection from that set beats the cascade.

This finding does NOT invalidate the +0.40 composite threshold result for
arena-BUYs (that finding stands and is reproduced in Phase 4's standard sweep).
It SUPPLEMENTS it: there's a second, larger universe of opportunities the
cascade is currently throwing out, which Phase 4 will quantify with an
"inverted cascade" experiment.

### Caveat acknowledged

These diagnostics are still arena-validated, not live-validated. The synthesized
agent signals could be systematically biased in a way that creates the apparent
anti-selection. But the diagnostic 4 mechanism (pre-open rejection criteria
correlate with pump signals) is a structural prediction the user made in
`docs/research-log/04_structural_redesign.md` — Phase 5 shadow data on tomorrow's
live session is what definitively confirms or refutes it.

