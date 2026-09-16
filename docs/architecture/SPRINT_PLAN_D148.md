# Sprint Plan: D148+ — Taking mx-arena to the Next Level

**Date**: March 28, 2026
**Current State**: Bar-1 exit deployed (+$5.11, +299%). 100% at T+60s. Live Monday.
**Core Constraint**: 79 trades, outlier-driven (top 5 = 85%), median = breakeven.

---

## Honest Assessment: What Actually Breaks the Ceiling

The arena has exhausted exit optimization on 79 trades. D147 confirmed: no entry
relaxation, sizing tier, or stress variant beats the +$5.11 baseline. The remaining
levers are NOT in the arena's current capabilities — they're in:

1. **More data** (live trades accumulating daily)
2. **Predicting outliers** (which 5 of 15 candidates will spike?)
3. **Expanding the universe** (same strategy on new markets)
4. **Capital scaling** (linear P&L growth, zero strategy change)

These are NOT parameter optimizations. They're structural expansions.

---

## Sprint 18: Nightly Auto-Researcher (This Week)

**What:** Build `arena/auto_researcher.py` — a nightly script that:
1. Loads yesterday's trades from journal
2. Adds to historical dataset
3. Re-runs walk-forward on current bar-1 strategy
4. Computes rolling 40-trade outlier frequency
5. Checks overfit ratio and drift
6. Generates a one-page daily report

**Why:** Captures 80% of the Darwin-Gödel value at 10% of the cost.
The human stays in the loop for deployment decisions. The AI handles
the analytical grunt work.

**Cost:** 200-300 lines of Python. 1-2 days.
**Expected value:** Continuous monitoring without manual effort.
Early detection of parameter drift or outlier frequency changes.

**NOT building yet:** Full autonomous hypothesis generation. The moments
where human judgment was essential (Finding 2 as artifact, 2.0x overfit
boundary, capture decomposition question) can't be automated safely
with 79 trades. After 500+ trades, revisit.

---

## Sprint 19: Alternative Data Collection (Start Immediately)

**What:** Start collecting enrichment features for every candidate:
- **Short interest %** of float (Fintel/SEC — 2-week delayed)
- **Float size** (shares outstanding, free float — financial APIs)
- **Social mention velocity** (StockTwits/Reddit — acceleration in last 30 min pre-market)
- **Pre-market volume acceleration** (volume per 30-min bucket 04:00-09:30)
- **Options open interest** at strikes near gap price (CBOE/Tradier)

**Why:** After 200+ trades (2-3 months), these features enable a predictive
model for outlier bar-1 spikes. If the model achieves >20% precision at
50% recall, outlier frequency triples from 6.3% to ~15-20% on filtered
candidates. That triples P&L because outliers ARE the P&L.

**Cost:** 2 hours to set up collection scripts. $0 ongoing (free APIs).
**Expected value:** The dataset that enables the breakthrough. Worthless
today, potentially transformative in 3 months.

**Implementation:**
- Add fields to journal entries: short_interest_pct, float_shares,
  social_velocity_30m, premarket_vol_acceleration, options_oi_ratio
- New script: `scripts/enrich_candidates.py` — runs during Phase 1
- Store enrichment in premarket cache alongside existing fields

---

## Sprint 20: Universe Expansion Test (This Month)

**What:** Run the arena's MFE analysis on a DIFFERENT asset class:
large-cap earnings gap stocks.

**Test:** Download 90 days of 1-minute bars for 50 stocks that gapped >3%
on earnings (AAPL, TSLA, NVDA, etc. post-earnings). Compute:
- Does MFE peak at bar 1?
- What's the MFE distribution (25th/50th/75th)?
- Does the bar-1 exit produce positive P&L?

**Why:** If the bar-1 spike pattern holds on large-caps, the strategy ports
to a universe where position sizes are 10-50x larger. A +$0.065/trade edge
on a $50,000 position = $32.50/trade instead of $0.65/trade.

**Cost:** 1 day (data download + arena analysis).
**Expected value:** Validation of whether the strategy generalizes.
If yes, this is the single largest P&L expansion available.

---

## Sprint 21: Fill Model Calibration (After 40 Live Trades)

**What:** Compare arena spread model predictions to actual Alpaca fill prices.

**Implementation:**
1. After 40 live trades, download order history from Alpaca API
2. For each trade: compute arena's predicted fill price (spread model)
3. Compare to actual fill price
4. Compute per-price-tier bias

**Why:** Every arena simulation assumes the spread model is accurate.
If it systematically overstates fills by 15 bps on $3-$5 stocks, every
finding is slightly off. Calibration makes all future analysis more
accurate.

**Cost:** 2 hours. Requires Alpaca order history API access.
**Expected value:** +5-10 bps accuracy improvement across all simulations.

---

## Sprint 22: Nightly Report with AI Interpretation (After Sprint 18)

**What:** Extend auto_researcher.py with an Opus 4.6 API call that
interprets the nightly results and writes a narrative report.

**Input to AI:** Walk-forward results, drift detection, outlier frequency,
any anomalies in today's trades, the enrichment features from Sprint 19.

**Output:** A one-page report saved to `docs/daily_research/YYYY-MM-DD.md`:
- "Strategy performing within CI. No action recommended."
- OR "ALERT: Outlier frequency 2.1% over last 30 trades (expected 6.3%).
  Consider fallback to 50% bar-1 exit."
- OR "New pattern detected: stocks with social velocity >500/hr have
  12% outlier rate vs 4% baseline. Consider as ranking signal."

**Cost:** 300 lines + ~$0.10/day API cost.
**Expected value:** Continuous intelligence without manual analysis.

---

## Sprint 23: IB Migration Planning (When Account Reaches $500K)

**What:** Plan migration from Alpaca to Interactive Brokers.

**Benefits:**
- Level 2 data (pre-open order imbalance → outlier prediction)
- Smart order routing (12 bps better fills)
- Lower latency (~10ms vs ~200ms)
- Better execution reporting (fill model calibration)

**Cost:** 1 week to port execution layer. $0.005/share commission
(offset by better fills).

**Gate:** Account must reach $500K for the economics to work.
At $139K, the commission cost exceeds the fill quality improvement.

---

## Sprint 24: ML Outlier Predictor (After 200+ Trades)

**What:** Train a logistic regression to predict outlier bar-1 spikes
from the alternative data features collected in Sprint 19.

**Features:** short_interest_pct, float_size, social_velocity,
premarket_vol_acceleration, options_oi_ratio, gap_pct, rvol, price_tier

**Target:** bar_1_return > 3% (outlier classification)

**Validation:** Walk-forward with 60/40 train/test split on 200+ trades.
If precision > 20% at recall > 50% (identifies 2.5 of 5 outliers while
flagging 12 candidates), deploy as candidate ranking signal.

**Gate:** Need 200+ trades with enrichment data. At 4 trades/day, 5 days/
week trading = ~50 days = 2.5 months after Sprint 19 data collection starts.

**Expected value:** If outlier frequency triples from 6.3% to 15% on
top-ranked candidates, P&L roughly triples.

---

## Sprint 25: Full Darwin-Gödel Machine (After 500+ Trades)

**What:** Autonomous research system that generates hypotheses, writes
arena tests, runs experiments, and proposes deployments.

**Gate:** 500+ live trades (6+ months). Daily reports from Sprint 22
must show AI interpretations are consistently correct (>70% accuracy
on directional calls). Walk-forward CIs must be <1 order of magnitude.

**Why wait:** With 79 trades, an autonomous system would churn through
thousands of variants on the same data, finding increasingly marginal
improvements that don't generalize. The safety cost of autonomous code
generation in a financial system exceeds the benefit until the dataset
supports reliable validation.

**Build sequence:**
1. Orchestration layer (API call + subprocess + result parsing)
2. Prompt engineering (encode all findings, validation gates, NOT REC list)
3. Safety layer (human approval gate, API budget limits, sandbox)
4. Evolutionary framework (population, mutation, selection)

---

## The Honest Priority Ranking

| Priority | Sprint | Effort | Gate | Expected Impact |
|----------|--------|--------|------|-----------------|
| **1** | **Live trading Monday** | Done | — | Validates everything |
| **2** | **19: Alt data collection** | 2 hours | — | Enables ML in 3 months |
| **3** | **18: Nightly auto-researcher** | 2 days | — | Continuous monitoring |
| **4** | **20: Universe expansion test** | 1 day | — | 10-50x position size if generalizes |
| 5 | 21: Fill calibration | 2 hours | 40 trades | Arena accuracy |
| 6 | 22: AI daily report | 1 day | Sprint 18 | Automated intelligence |
| 7 | 23: IB migration | 1 week | $500K acct | Better fills + Level 2 |
| 8 | 24: ML predictor | 1 week | 200 trades | 3x outlier frequency |
| 9 | 25: Darwin-Gödel | 1 week | 500 trades | Full autonomy |

---

## What Doesn't Move the Needle (Parked)

- **More exit optimization** — Exhausted. Bar-1 100% is optimal on 79 trades.
- **Parameter sweeps** — 3.43x overfit on MFCS, 2.0x on bar-1. Done.
- **Regime conditioning** — Zero difference. Universal strategy.
- **Trailing stop variants** — Remainder is -88%. All trails fail.
- **Quantum algorithms** — No application to this problem at this scale.
- **RL exit agent** — 79 trades = 3 orders of magnitude too few.
- **Sub-second execution** — Gated by capital ($500K+ for co-location).
- **Options/pairs/shorts** — Wrong universe for $3-$5 small-caps.

---

## The Meta-Insight

The arena's greatest value isn't finding the optimal strategy (though it did).
It's building the infrastructure that makes the NEXT optimization cycle faster.

Sprint 18 (auto-researcher) uses that infrastructure to monitor continuously.
Sprint 19 (alt data) builds the NEXT dataset for the NEXT model.
Sprint 20 (universe expansion) tests whether the finding TRANSFERS.
Sprint 24 (ML predictor) is the NEXT research breakthrough, built on data
that doesn't exist yet but will exist if we start collecting today.

Each sprint builds on the previous one. The system compounds.

**Start Sprint 19 (alt data collection) TODAY.** It costs 2 hours and
every day of delay is a day of missing data that can't be recovered.
The ML predictor in 3 months depends on starting collection now.
