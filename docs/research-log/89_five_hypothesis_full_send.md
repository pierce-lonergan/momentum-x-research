# 89 — Five-Hypothesis Full-Send: Two Inversions, One Walk-Forward Failure

**Date:** 2026-04-29
**Branch:** develop
**Operator instruction (verbatim):**
> "Test all four §7 hypotheses in parallel as cheap backtests. Late-day
> entry, buy-weakness-on-VWAP-reclaim, short-the-ripper, MAGNA-N intersection.
> ~1.5 sessions. Risk: four parallel experiments on a universe we now know
> is hostile may produce four negative results. Reward: if even one inverts,
> we have an actual strategy. then test the most expensive hypothesis we
> can. if there are 84% movers in every watch list then there is absolutely
> a way to find how to identify it."

**Headline (one paragraph):** Two of the five hypotheses inverted to
positive expectancy in-sample (H1 long-late-day-13:00-top-3 = **+0.45%/trade,
+50% compound**; H3 short-the-morning-ripper-top-1 = **+2.82%/trade,
+168% compound**). A regime-overlay filter (F5 top-5 dollar volume in bottom
tercile) lifted H3 to **+7.02%/trade, +224% compound**. The combined
regime-switched portfolio shows in-sample Sharpe 4.35 annualized. **But
walk-forward validation killed it**: training the F5 cutoff on Dec 11 –
Feb 20 and applying it to Feb 23 – Apr 30 produced **−4.3%/trade, 38% win
rate, −33% compound** out-of-sample. The edge is real in-sample but
regime-dependent, not structural. Recommendation: do NOT deploy; the
information value of this work is the sharpened next-step queue, not a
production strategy.

---

## §1 — The five hypotheses (per doc 88 §7)

| ID | Hypothesis | Implementation cost |
|----|-----------|---------------------|
| H1 | Late-day entry sweep (11:30 / 12:00 / 13:00 / 13:30 / 14:00 / 14:30 ET) × top-K (1, 2, 3, 5) of first-30-min momentum, +20%/-10%/EOD exit | Low — modify entry time |
| H2 | Buy on VWAP reclaim after ≥10% pullback from morning high (which itself was ≥20% from open), +20%/-10%/EOD | Medium — anchored VWAP path tracking |
| H3 | Short top-K of first-30-min momentum at 10:00 ET, +30%/-15% exit (sweep K, min_first_30, target/stop) | Low — flip sign |
| H4 | First-30-min top-K AMONG candidates with MAGNA-N score ≥2, ETF-excluded | Medium — fundamentals lookup per ticker |
| H5 | Consolidation breakout: ≥20% morning push → 60-min base ≤10% range → break above morning high | Medium — pattern detection |

Files: `scripts/backtest_5_hypotheses.py`, `scripts/backtest_winners_deep_dive.py`,
`scripts/backtest_winners_robustness.py`, `scripts/backtest_regime_overlay.py`,
`scripts/backtest_final_strategy_v1.py`.

---

## §2 — First-pass results across all five

Single config per hypothesis, +20%/-10% exit, 88 trading days:

| ID | Strategy | n | Avg/trade | Win | Target | Stop | Compound |
|----|----------|---|-----------|-----|--------|------|----------|
| H1 | entry 11:30, top-3 | 264 | −0.02% | 44.3% | 11.4% | 28.8% | −68.9% |
| H1 | entry 12:00, top-3 | 264 | −0.70% | 41.3% | 9.5% | 29.9% | −94.2% |
| **H1** | **entry 13:00, top-3** | **264** | **+0.45%** | **47.0%** | **7.2%** | **16.3%** | **+49.8%** |
| H2 | VWAP reclaim morn≥20% pull≥10% | 196 | −0.10% | 37.8% | 21.4% | 45.9% | −77.4% |
| **H3** | **short top-3 min30 +20%/−10%** | **57** | **+1.87%** | **45.6%** | **29.8%** | **50.9%** | **+77.3%** |
| H4 | MAGNA-N≥2 top-5 +20%/−10% | 349 | −0.59% | 41.0% | 10.3% | 33.2% | −97.3% |
| H5 | consolidation breakout +20%/−10% | 20 | −0.73% | 50.0% | 0.0% | 15.0% | −16.9% |

Two strategies inverted positive: **H1 at 13:00** (long late-day-breakout)
and **H3 short** (fade the morning pump).

Notable second-tier findings:
- H1 is **highly entry-time-sensitive** — only 13:00 works; 12:00, 11:30,
  13:30, 14:00, 14:30 all lose. Narrow edge → red flag for overfitting.
- H4 (MAGNA-N intersection) loses worse than naive — fundamentals are
  not an intraday-timing edge. Catalysts may matter for which stocks
  appear in the watchlist, but not for "buy at 10:00 / sell at +20%".
- H5 only triggered 20 setups; relaxing filters needed before drawing
  any conclusion.

---

## §3 — Deep-dive sweep on H1 + H3

### §3.1 — H1 sweep (entry time × top-K)

Top 5 by compound:
| Strategy | n | Avg | Win | Compound |
|----------|---|-----|-----|----------|
| **H1_entry_1300_top3** | **264** | **+0.45%** | **47.0%** | **+49.8%** |
| H1_entry_1330_top3 | 264 | +0.12% | 47.7% | −35.0% |
| H1_entry_1300_top1 | 88 | −0.21% | 39.8% | −39.2% |
| H1_entry_1330_top2 | 176 | −0.06% | 47.2% | −48.2% |
| H1_entry_1300_top2 | 176 | −0.09% | 43.2% | −50.2% |

Only one config is meaningfully positive. **15 of 16 configs lose money.**
This pattern is consistent with statistical noise winning a parameter
search rather than a structural edge.

### §3.2 — H3 sweep (top-K × min_first_30 × target/stop)

Top 5 by compound:
| Strategy | n | Avg | Win | Compound |
|----------|---|-----|-----|----------|
| **H3_short_top1_min20_tgt30_stp15** | **67** | **+2.82%** | **56.7%** | **+168.2%** |
| H3_short_top1_min30_tgt30_stp15 | 43 | +3.74% | 55.8% | +163.5% |
| H3_short_top2_min30_tgt30_stp15 | 57 | +2.70% | 52.6% | +104.5% |
| H3_short_top3_min30_tgt30_stp15 | 57 | +2.70% | 52.6% | +104.5% |
| H3_short_top1_min30_tgt10_stp5 | 43 | +1.81% | 46.5% | +93.3% |

H3 is **MUCH more robust to parameter choice than H1**. Multiple configs
all show positive expectancy, and the +30%/−15% target/stop is
structurally optimal (asymmetric pay-off matches the asymmetric outcome
distribution of failed pumps: pumps fail HARD when they fail).

### §3.3 — Combined market-neutral (best H1 + best H3)

Daily portfolio: 3 long-late picks averaged + 1 short-ripper pick.

| Metric | Value |
|--------|-------|
| n_days | 88 |
| daily_mean | +0.621% |
| daily_std | 5.65% |
| Sharpe (annualized) | **1.75** |
| compound | +50.6% |
| win_day_rate | 52.3% |
| max drawdown | −39.4% |

This is the headline number from first-pass deep dive. **It does not
survive walk-forward** (§5).

---

## §4 — Robustness (R1–R4)

### §4.1 — R1: drop CRCA from both winners

CRCA was the +984% accidental hold from PROMPT_10. Could the headline
edge be just one outlier carry trade?

| Strategy | Full | Without CRCA |
|----------|------|--------------|
| H1 (long 13:00 top-3) | +0.45%/trade | +0.45%/trade (CRCA 0 appearances) |
| H3 (short top-1 min20) | +2.82%/trade | +2.82%/trade (CRCA 0 appearances) |

CRCA never made the top-3 of first-30-min momentum (its first-30 was only
+34%), so it never appears in either strategy. **The H1/H3 edge is
independent of the CRCA outlier.** Good — eliminates one obvious
overfitting concern.

### §4.2 — R2: universe-size effect

H1 by univ-size quartile:
| Univ size bin | n_trades | Avg n_tickers | Avg PnL |
|---------------|----------|---------------|---------|
| 0 (smallest) | 66 | 18 | +0.88% |
| 1 | 69 | 41 | +0.65% |
| 2 | 63 | 58 | +0.33% |
| 3 (largest) | 66 | 154 | −0.06% |

H3 by univ-size quartile:
| Bin | n_trades | Avg n_tickers | Avg PnL | Win |
|-----|----------|---------------|---------|-----|
| 0 | 17 | 19 | +0.67% | 53% |
| 1 | 17 | 41 | +1.98% | 53% |
| 2 | 16 | 59 | **+7.02%** | **69%** |
| 3 | 17 | 163 | +1.85% | 53% |

H3 has a clear sweet spot at universe size ~58 tickers. Below: too few
candidates. Above: too many marginal candidates.

### §4.3 — R4: monthly P&L profile

**H1 (long 13:00, top-3) monthly:**
| Month | n | Mean | Win | Compound |
|-------|---|------|-----|----------|
| 2025-12 | 39 | +1.17% | 46% | **+35.9%** |
| 2026-01 | 54 | +0.33% | 46% | +1.7% |
| 2026-02 | 54 | +1.05% | 57% | **+55.4%** |
| 2026-03 | 66 | −1.01% | 39% | **−58.1%** |
| 2026-04 | 51 | +1.30% | 47% | **+66.5%** |

H1 had a single bad month (March), bookended by good months. Not a
clean degradation — could be regime variance.

**H3 (short top-1, min20, +30%/−15%) monthly:**
| Month | n | Mean | Win | Compound |
|-------|---|------|-----|----------|
| 2025-12 | 11 | −0.15% | 45% | −16.9% |
| 2026-01 | 14 | **+5.71%** | **71%** | **+84.4%** |
| 2026-02 | 12 | **+9.09%** | **67%** | **+136.4%** |
| 2026-03 | 19 | +0.04% | 53% | −17.3% |
| 2026-04 | 11 | +0.08% | 45% | −10.4% |

**H3's edge is concentrated in Jan-Feb 2026 and disappears in Mar-Apr.**
This is the regime-shift signal.

---

## §5 — Regime overlay: F5 dollar-volume filter (the headline finding)

We tested whether intraday observable features at 10:00 ET could
distinguish "fadable pumps" from "real pumps" within H3. Five features
tried; **F5 (avg dollar volume of top-5 first-30-min movers) was the
clean winner**:

| F5 tercile | n_trades | Avg/trade | Win | Compound |
|------------|----------|-----------|-----|----------|
| **lo** ($4M–$22M) | **21** | **+7.02%** | **66.7%** | **+224.1%** |
| mid ($22M–$50M) | 26 | +4.64% | 61.5% | +130.0% |
| **hi** ($50M+) | **20** | **−3.96%** | **40.0%** | **−64.0%** |

**Reading**: low-dollar-volume pumps (small float, retail-only HFT
amplification) are fade-able. High-dollar-volume pumps (institutional
participation, real flow) are not — they often keep running.

This matches market-microstructure intuition: high-DV moves have actual
buyer interest, so shorting them is fighting the trend; low-DV moves
are HFT/wholesaler artifacts that revert when the order flow stops.

### §5.1 — F5 trade-by-trade audit (the 21 lo-tercile trades)

| Date | Ticker | first_30 | exit_reason | pnl |
|------|--------|----------|-------------|-----|
| 2025-12-15 | AMCI | +33% | stop | −15.0% |
| 2025-12-19 | JLHL | +49% | target | +30.0% |
| 2025-12-23 | ASTI | +42% | target | +30.0% |
| 2025-12-29 | GVH | +40% | stop | −15.0% |
| 2026-01-26 | ABOS | +25% | eod | +6.1% |
| 2026-01-27 | PHGE | +35% | eod | +4.8% |
| 2026-01-29 | SLGB | +23% | time_stop | +8.2% |
| 2026-01-30 | GITS | +74% | target | +30.0% |
| 2026-02-05 | KELYB | +82% | target | +30.0% |
| 2026-02-06 | BOXL | +28% | target | +30.0% |
| 2026-02-11 | HTLM | +77% | time_stop | +16.5% |
| 2026-02-18 | JELD | +26% | time_stop | +12.9% |
| 2026-02-19 | MLEC | +45% | time_stop | +13.6% |
| 2026-02-25 | NVTX | +30% | stop | −15.0% |
| 2026-03-04 | NPT | +37% | time_stop | −6.8% |
| 2026-03-11 | CODX | +21% | time_stop | +1.7% |
| 2026-03-25 | RZLT | +26% | time_stop | +1.7% |
| 2026-04-08 | MGRT | +27% | stop | −15.0% |
| 2026-04-10 | FGL | +29% | time_stop | −1.7% |
| 2026-04-17 | RMSG | +34% | time_stop | +15.4% |
| 2026-04-28 | SBLX | +33% | stop | −15.0% |

**Pattern**: 7 target hits all in Dec-Feb. From March onward: 0 target
hits, mostly small-positive time-stops or stop-outs. **The favorable
regime ended in late February.**

### §5.2 — Bootstrap CI on the 21-trade F5=lo bucket

5,000 iterations:
- mean_observed: +7.02%
- 95% CI: [+1.04%, +12.82%]
- share of bootstrap iterations with positive mean: 97.2%

The IN-SAMPLE positive mean is statistically significant. **But
significance ≠ persistence.**

---

## §6 — Walk-forward validation (the killer)

Train period: 2025-12-11 → 2026-02-20 (44 dates).
Test period: 2026-02-23 → 2026-04-30 (44 dates).

Procedure: compute the F5 bottom-tercile cutoff using ONLY train period
data; apply the same dollar-volume threshold ($21.9M) to the test period;
trade H3 short on test-period dates passing the cutoff.

| Window | n_trades | Avg/trade | Win | Target | Stop | Compound |
|--------|----------|-----------|-----|--------|------|----------|
| **In-sample (train)** | **11** | **+13.08%** | **81.8%** | **36.4%** | **18.2%** | **+243%** |
| **Out-of-sample (test)** | **8** | **−4.33%** | **37.5%** | **0.0%** | **37.5%** | **−33%** |

**The signal does not generalize forward.** The F5 cutoff that selected
the +13%/trade in-sample bucket selected the −4.3%/trade out-of-sample
bucket. Whatever made low-dollar-volume pumps fade in Dec-Feb 2026
stopped working in Mar-Apr 2026.

This is the **definitive** result. Any version of "deploy this strategy
live" would be a bet that the Dec-Feb regime returns. We have no
forward evidence it will.

---

## §7 — Why the regime change?

Four candidate explanations, none directly testable with current data:

1. **Pump cycle exhaustion.** Jan-Feb 2026 had an unusually heavy wave
   of small-cap retail-driven pumps (consistent with the AI penny-stock
   mania reported in the press). Once that cycle exhausted, fadable
   pumps decreased and the strategy lost its substrate.

2. **HFT / market-maker adaptation.** PFOF wholesalers may have improved
   their fade execution, capturing more of the morning-pump-fade for
   themselves and leaving less for shortable through Alpaca.

3. **Watchlist composition shift.** The bot's filter changed: April
   watchlists average 14 tickers vs December's 30+. Smaller universe →
   less low-dollar-volume noise → fewer setups that match the filter.

4. **Macro regime.** A broader risk-on/risk-off shift could have
   changed retail behavior in micro-cap names.

Without a way to A/B-test against an extended dataset (we don't have
watchlist construction for dates outside `data/bar_recordings/`), we
can't disambiguate these.

---

## §8 — What survives, what doesn't

### §8.1 — Survives the analysis

- **Watchlist quality** (per doc 88): 84% of days have a ≥30% mover. The
  selection layer is producing the right kind of candidate.
- **Short-side has structural appeal**: even the parameter-naive H3
  config produced positive in-sample results across many configurations.
  The asymmetry (capped −15% loss vs uncapped pump-fade) matches the
  underlying distribution.
- **F5 dollar-volume filter is informationally meaningful**: at minimum
  it cleanly separates fadable from non-fadable pumps in-sample. May
  generalize to a future similar regime.
- **Late-day breakout (H1) is plausible but fragile**: only one entry
  time worked. Likely overfit.

### §8.2 — Does not survive

- **Walk-forward**. Most important finding.
- **The Sharpe-1.75 / Sharpe-4.35 in-sample numbers**. They're real
  observations but not deployable.
- **The combined market-neutral**. Pretty headline, no forward edge.

### §8.3 — Doesn't apply (yet)

- **MAGNA-N intersection** (H4): negative. May be wrong for intraday
  timing, may still be right for "which catalyst is real" — different
  question than what this analysis tested.
- **VWAP reclaim** (H2): -0.1%/trade — basically random. Pattern is
  too common; needs additional discriminator.
- **Consolidation breakout** (H5): too few setups (20). Need to relax
  filters and re-test.

---

## §9 — Updated next-step queue, ranked by EV

In priority order for what to test next, given the new evidence:

1. **Extend the data substrate** [HIGHEST EV]. The 88-day sample is
   below statistical-significance threshold for a regime-conditional
   strategy. We need either:
     - More forward time (continue paper trading + recording for 3+
       months), OR
     - Backward extension via Polygon backfill + reconstructed historical
       watchlists. The backfill ticker universe (100 tickers) is too
       small; we'd need to reconstruct watchlists for arbitrary historical
       dates using the bot's filter.
   Without more data, every backtest from here is overfitting noise.

2. **Forward paper-trade H3 with regime overlay** [MEDIUM EV, LOW COST].
   Run the F5-filtered H3 short strategy on paper from now forward,
   instrumented. After 30+ trades of forward data, we'll know if the
   regime returned. Net cost: zero P&L if shorts; daily +/-15%/+30%
   bounded if implemented. Could co-exist with halt on the long side.

3. **Pump-regime classifier work** [MEDIUM EV, MEDIUM COST]. Build a
   real-time daily classifier: given the first 30 minutes of watchlist
   data, predict P(today is a fade-able-pump regime). Train on the
   Dec-Feb labels (where H3 worked) vs Mar-Apr labels (where it didn't).
   If the classifier has out-of-fold AUC > 0.7, we have a signal.

4. **Investigate H5 with relaxed filters** [LOW-MEDIUM EV, LOW COST].
   Only 20 trades triggered with the original filter (push≥20%, range≤10%).
   Try push≥10% / range≤15% / window 30-min — see if the consolidation
   breakout has an edge with more setups.

5. **Test "buy weakness on extended runner"** [LOW EV, LOW COST]. The
   inverse of H2: instead of VWAP reclaim, look for the second push
   in a stock that already moved 50%+ — buy the second-leg breakout.
   Historically this is a Kullamägi-pattern winner.

6. **Operator decision: stay halted** [SAFEST]. The existing strategy
   is documented as a net loser. The new findings do NOT yet justify a
   restart. Stay halted, run #2 in parallel as a paper experiment, and
   revisit after collecting forward data.

7. **Refrain from deploying H1 + H3 live** [DO NOT]. Walk-forward
   failed. There is no evidence that the in-sample edge persists.

---

## §10 — Stop conditions check

| Condition | Result |
|---|---|
| Did at least one hypothesis invert positive? | **YES** — H1 13:00, H3 short |
| Did the inversion survive parameter sweep? | H3 yes (multiple configs +); H1 no (1 of 16) |
| Did it survive time-split robustness? | **NO** — both winners are first-half-loaded |
| Did the regime filter (F5) survive walk-forward? | **NO** — train +13%/test −4.3% |
| Is there a deployable strategy from this work? | **NO** |
| Is there a sharpened next-step queue? | **YES** — see §9 |

---

## §11 — Files written

| Path | Rows | Purpose |
|------|------|---------|
| `scripts/backtest_5_hypotheses.py` | — | Unified harness, 5 hypotheses |
| `scripts/backtest_winners_deep_dive.py` | — | H1×H3 sweep + combined |
| `scripts/backtest_winners_robustness.py` | — | Time-split, monthly, CRCA check |
| `scripts/backtest_regime_overlay.py` | — | F5 + 4 features tercile analysis |
| `scripts/backtest_final_strategy_v1.py` | — | Walk-forward + bootstrap on F5=lo |
| `data/audits/h1_late_day_trades.parquet` | 792 | H1 entry-sweep trades |
| `data/audits/h2_vwap_reclaim_trades.parquet` | 196 | H2 trades |
| `data/audits/h3_short_ripper_trades.parquet` | 57 | H3 trades |
| `data/audits/h4_magna_intersection_trades.parquet` | 349 | H4 trades |
| `data/audits/h5_consolidation_breakout_trades.parquet` | 20 | H5 trades |
| `data/audits/h_5_summary.json` | — | Comparative table across hypotheses |
| `data/audits/winners_deep_dive_summary.json` | — | Sweep results |
| `data/audits/winners_robustness_summary.json` | — | Monthly + universe-size + CRCA |
| `data/audits/regime_features.parquet` | 88 | Daily regime features |
| `data/audits/regime_overlay_summary.json` | — | Tercile analysis per feature |
| `data/audits/final_strategy_v1_summary.json` | — | F5 walk-forward + bootstrap |

---

## §12 — One-paragraph honest answer to the operator

> "Yes, two of the five hypotheses inverted positive in-sample, and a
> regime filter on dollar volume identified a +7%/trade, 67% win-rate
> short setup. No, the strategy does not survive walk-forward —
> training on Dec-Feb and testing on Mar-Apr produces a 38% win rate
> and −33% compound. The data is consistent with a Jan-Feb 2026 pump
> regime that ended in late February and has not returned. The work
> has narrowed the search but not produced a deployable strategy. The
> highest-EV next step is extending the data substrate or running H3
> forward as instrumented paper to see whether the regime returns. I
> recommend NOT restarting live trading on the basis of this analysis."
