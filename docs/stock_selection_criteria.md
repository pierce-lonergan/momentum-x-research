# How MOMENTUM-X Selects Stocks

**Last updated:** 2026-04-14
**Purpose:** Complete reference for every filter, gate, and scoring criterion in the stock selection pipeline.

---

## Overview

MOMENTUM-X scans thousands of stocks every morning before the market opens, looking for a very specific pattern: **stocks that gapped up overnight on real news, with confirmed volume, that are likely to keep running after the opening bell.**

The system uses a 5-stage funnel. Each stage filters out stocks that don't meet increasingly strict criteria. By the time a stock reaches a BUY decision, it has passed **16+ independent quality checks.**

```
Stage 1: Universe Discovery      ~70 stocks from Alpaca screeners
    |
Stage 2: EMC Filter              ~8-20 candidates (gap, price, volume filters)
    |
Stage 3: GEX Enrichment          ~8-20 candidates (options market data added)
    |
Stage 4: AI Evaluation           ~5-10 scored by 6 AI agents (MFCS score)
    |
Stage 5: Gate Sequence            0-3 BUY verdicts (16 sequential safety checks)
    |
Result: Only the highest-quality momentum trades execute.
```

---

## Stage 1: Universe Discovery

### Where do the stocks come from?

Every 60 seconds during pre-market (4:30 AM - 9:30 AM ET), the system queries two Alpaca screeners:

| Source | What it finds | Count |
|--------|--------------|-------|
| **Most Active** | Stocks with the highest trading volume in pre-market | Up to 50 |
| **Top Gainers** (D117) | Stocks with the biggest percentage price gains | Up to 20 |

The two lists are merged and deduplicated, producing ~60-70 unique tickers to evaluate. This catches both high-volume institutional favorites AND small-cap rockets that haven't built volume yet but are making big percentage moves.

---

## Stage 2: EMC Filter (Explosive Momentum Candidates)

This is the primary filter. It asks: **"Is this stock showing the explosive gap-up pattern that precedes momentum runs?"**

### The 7 Core Criteria

Every stock must pass ALL of these simultaneously:

| Criterion | Threshold | Why |
|-----------|-----------|-----|
| **Gap Up** | **+5% to +50%** | The stock must have jumped at least 5% from yesterday's close. Below 5% isn't momentum. Above 50% is usually a promotional pump. |
| **Relative Volume (RVOL)** | **2.0x normal** or 500K+ shares | Volume must be at least DOUBLE the stock's average. This confirms real buyers, not just a thin market gap. |
| **Price** | **$1.50 - $50.00** | Below $1.50 is too illiquid/risky. Above $50 doesn't gap enough for momentum plays. |
| **Dollar Volume** | **$2 million+** | Must be trading enough dollar value to enter and exit without moving the market. |
| **Float** | **Under 20 million shares** | Low float = limited supply. When demand hits a low-float stock, it moves faster and further. |
| **Not a Corporate Action** | No splits/reverse-splits | Stocks that "gap" due to a stock split aren't real momentum — the price change is artificial. |
| **Not Exhaustion** | Not a multi-day pump fading | If the stock already ran 40% yesterday and is gapping again with declining volume, it's likely exhausting, not accelerating. |

### Smart Override Rules

The system has intelligence to catch unusual situations:

| Override | When It Triggers | What Happens |
|----------|-----------------|--------------|
| **Mega Dollar Volume** | Dollar volume > $10 million | ALL price floors bypassed — if enough money is flowing, the stock qualifies regardless of share price |
| **Extreme RVOL** | Volume > 30x normal | Price floor drops to $0.50 — extreme volume signals something real is happening |
| **High-Volume Low-Price** | Volume > $2M AND RVOL > 5x | Price floor drops to $0.50 — dual confirmation of interest |

### The CATALYST Tier (Mid-Cap Stocks)

Alongside the standard momentum filter, the system also watches for mid-cap catalyst plays:

| Criterion | Threshold | Why |
|-----------|-----------|-----|
| **Market Cap** | $1 billion - $50 billion | Institutional-quality companies |
| **Gap** | +3% (lower bar) | Mid-caps don't gap 20%+ often, but 3%+ on a catalyst is significant |
| **RVOL** | 1.5x normal | Lower bar — large-cap baselines are already active |
| **Dollar Volume** | $10 million+ | Must have institutional liquidity |
| **Price** | $5 - $200 | Mid-cap price range |

**Result:** 8-20 candidates pass the EMC filter on a typical day.

---

## Stage 3: GEX Enrichment (Gamma Exposure)

Each candidate gets enriched with **options market data** from the Gamma Exposure (GEX) calculation.

### What is GEX?

GEX measures the hedging pressure from options market makers (dealers). When dealers are "long gamma," they automatically sell into rallies and buy into dips — which **suppresses** momentum. When they're "short gamma," they amplify moves.

| GEX Regime | What It Means | Effect on Momentum |
|------------|---------------|-------------------|
| **Acceleration** (GEX < -0.01) | Dealers amplify price moves | Favorable for momentum trades |
| **Neutral** (-0.01 to +0.05) | No structural bias | Neutral |
| **Suppression** (GEX > +0.05) | Dealers dampen price moves | Headwind for momentum |

**Hard rejection:** GEX normalized > 2.0 (extreme suppression). In practice, this almost never triggers — most small-cap stocks have minimal options activity.

---

## Stage 4: AI Evaluation (MFCS Scoring)

Candidates that pass the EMC filter enter the AI evaluation pipeline. **Six specialized AI agents** analyze each stock simultaneously:

### The 6 AI Agents

| Agent | Weight | What It Analyzes | Example Signal |
|-------|--------|-----------------|----------------|
| **News Agent** | **55%** | Headlines, press releases, catalyst credibility. Uses FinBERT sentiment + LLM reasoning. | "FDA approval announced" → BULL |
| **Volume/RVOL Agent** | **25%** | Relative volume, pre-market activity, demand-supply imbalance | RVOL 50x → BULL |
| **Float Structure Agent** | **15%** | Share float, insider ownership, dilution risk from SEC filings | Float 2M shares → BULL |
| **Technical Agent** | **5%** | Price patterns, RSI, MACD, Bollinger Bands, support/resistance | RSI 65, breakout → NEUTRAL |
| **Risk Agent** | **Penalty (25%)** | Spread, liquidity, bankruptcy risk, volatility | Wide spread → CAUTION |
| **Manipulation Classifier** | **Gate** | Pump-and-dump detection, promotional patterns | Promotional → BLOCK |

### How the MFCS Score Works

**MFCS = Multi-Factor Composite Score** — a number from -1.0 to +1.0.

```
MFCS = (News × 55%) + (Volume × 25%) + (Float × 15%) + (Technical × 5%) - (Risk × 25%)
```

Each agent returns a signal:
- **STRONG_BULL** (+1.0) — very confident this stock will run
- **BULL** (+0.5) — positive outlook
- **NEUTRAL** (0.0) — no opinion / insufficient data
- **BEAR** (-0.5) — negative outlook
- **STRONG_BEAR** (-1.0) — very confident this will fail

The signals are multiplied by the agent's confidence level (0-100%) and weighted.

**Example:**
```
News: BULL × 85% confidence = +0.425 × 55% weight = +0.234
RVOL: BULL × 90% confidence = +0.450 × 25% weight = +0.113
Float: NEUTRAL × 50% confidence = 0.000 × 15% weight = 0.000
Tech:  NEUTRAL × 30% confidence = 0.000 × 5% weight  = 0.000
Risk:  CAUTION × 40% score = penalty of -0.100 × 25%  = -0.025

MFCS = 0.234 + 0.113 + 0.000 + 0.000 - 0.025 = 0.322
```

**Buy Threshold: MFCS >= 0.25**

A score of 0.322 passes. The stock moves to the gate sequence.

### Why News is 55% of the Score

From D203 analysis of 115 historical BUY trades:
- News agent BULL signal → **16.7% win rate** (best predictor)
- Technical agent BULL signal → **0% win rate** (anti-predictor!)
- Volume/RVOL → strong structural signal, reliable
- Float structure → meaningful but not actionable alone

The news agent is the only agent where a bullish signal actually predicts winning trades. That's why it gets the majority weight.

---

## Stage 5: Gate Sequence (16 Safety Checks)

Even after scoring MFCS >= 0.25, a stock must pass **16 additional safety checks** before the system buys it. These gates prevent the system from trading into dangerous situations.

### Market Safety Gates

| Gate | What It Checks | Threshold | Action on Fail |
|------|---------------|-----------|----------------|
| **VIX Panic** | Is the overall market in crisis? | VIX >= 35 | Block ALL entries |
| **VIX Block** | Is volatility too high for small-caps? | VIX >= 20 | Block MOMENTUM entries (CATALYST may proceed) |
| **VIX Size Reduction** | Is volatility elevated? | VIX >= 15 | Cut position size in half |
| **SPY Crash** | Is the broad market crashing? | SPY down > 2.5% | Block ALL entries |

### Signal Quality Gates

| Gate | What It Checks | Threshold | Action on Fail |
|------|---------------|-----------|----------------|
| **Consensus Alignment** (D124) | Do the agents agree? | Need strict bullish majority | NO_TRADE — agents disagree |
| **Consensus Gate** (D101) | Did ANY agent give a directional signal? | At least 1 BULL or BEAR | NO_TRADE — all NEUTRAL means no signal |
| **Catalyst Confirmation** (D200) | Is there a real catalyst? | catalyst_type != UNKNOWN | NO_TRADE — no identified reason for the gap |
| **News Confidence** (D204) | Is the news agent confident? | News = BULL with confidence >= 30% | NO_TRADE — news too weak |

### Risk Management Gates

| Gate | What It Checks | Threshold | Action on Fail |
|------|---------------|-----------|----------------|
| **Faller Risk** (D160) | Is this stock likely to fade? | Faller score < 0.60 | Reduce size or NO_TRADE |
| **Observation Window** (D170) | Has the price confirmed the move? | 3-15 min observation period | Defer entry until confirmed |
| **Session Regime** (D198) | Are we on a losing streak today? | Daily loss < 10% of equity | NO_TRADE — protect remaining capital |
| **Max Positions** | Too many open positions? | Max 3 simultaneous positions | NO_TRADE — portfolio full |
| **Circuit Breaker** | Has the system hit its daily loss limit? | Daily drawdown < 10% | HALT all trading |

### Execution Quality Gates

| Gate | What It Checks | Threshold | Action on Fail |
|------|---------------|-----------|----------------|
| **Spread Filter** (D101) | Is the bid-ask spread too wide? | Spread < 1% (or 3% on high conviction) | NO_TRADE — entry cost too high |
| **Duplicate Check** | Are we already in this stock? | No existing position | Skip — already holding |
| **Stop-Out Guard** (D94b) | Did we just get stopped out of this? | Not stopped out this session | Skip — prevent revenge trading |

---

## What Happens After a BUY

If a stock passes ALL 16 gates, the system:

1. **Submits a limit buy order** at the current ask price
2. **Sets an automatic stop-loss** at 5.5% below entry (protects against large drops)
3. **Sets profit targets** at +3%, +8%, and +10% (tranche exits to lock in gains)
4. **Monitors continuously** via 6 exit strategies throughout the day
5. **Closes all positions** by 4:00 PM (no overnight risk)

### The 6 Exit Strategies

| Strategy | What It Detects | Action |
|----------|----------------|--------|
| **Trailing Stop** | Price drops from peak | Lock in gains — stop follows price up, never down |
| **Volume Exhaustion** | Buying volume drying up | Exit before the crowd leaves |
| **VWAP Deterioration** | Price drops below day's average | Momentum broken — institutional sellers appearing |
| **Alpha Decay** | Returns decaying vs expected curve | Stock running out of steam |
| **Catalyst Half-Life** | Time since catalyst announcement | Catalyst impact fading — time to exit |
| **Early Profit Take** | Quick +0.5% in first 2 minutes | Grab the easy money on initial spike |

---

## Understanding Today's Results

### Reading the Watchlist

When you see a watchlist post on Discord, each stock shows:

- **Gap %** — How much the stock jumped overnight. Higher = stronger initial move.
- **RVOL** — How much more volume than normal. 10x = ten times usual trading. 100x+ = explosive interest.
- **Momentum Bar** — Visual indicator combining gap and volume. `████████░░` = strong momentum.

### Why Stocks Get Rejected

The most common reasons a watchlisted stock doesn't become a trade:

| Reason | What It Means | How Often |
|--------|---------------|-----------|
| **"Consensus alignment: 1 bullish vs 1 bearish"** | The AI agents disagree — some say buy, some say don't. Without clear consensus, we don't trade. | ~35% of rejections |
| **"Router: RVOL 0.8x < 1.0x minimum"** | Volume is actually BELOW normal despite the gap. No real buyers — probably a thin market gap. | ~30% of rejections |
| **"Consensus gate: 0 directional agents"** | All AI agents returned NEUTRAL — nobody has an opinion. Usually means no news catalyst was found. | ~25% of rejections |
| **"MFCS 0.217 < 0.25 threshold"** | The composite score was close but not high enough. Marginal conviction isn't enough to risk capital. | ~10% of rejections |

### Zero Trades = System Working Correctly

A day with zero trades does NOT mean the system is broken. It means the system evaluated every available opportunity and determined that none met its quality standards. **The system is designed to be selective** — it would rather miss a good trade than take a bad one.

The edge in momentum trading comes from discipline: buying only the highest-quality setups and avoiding everything else. Most stocks that gap up will fade back down within hours. The system's job is to find the ones that won't.

---

## Threshold Summary Table

| Filter | Parameter | Value | Stage |
|--------|-----------|-------|-------|
| Gap minimum | gap_pct_min | 5% | EMC |
| Gap maximum | gap_pct_max | 50% | EMC |
| Price floor | price_min | $1.50 | EMC |
| Price ceiling | price_max | $50.00 | EMC |
| Relative volume | rvol_premarket_min | 2.0x | EMC |
| Dollar volume | min_dollar_volume | $2 million | EMC |
| Float cap | float_max_shares | 20 million shares | EMC |
| MFCS buy threshold | mfcs_buy_threshold | 0.25 | AI Scoring |
| News agent weight | catalyst_news | 55% | AI Scoring |
| Risk penalty | risk_aversion_lambda | 25% | AI Scoring |
| VIX block (small-cap) | vix_block_threshold | 20.0 | Market Safety |
| VIX panic (all) | vix_panic_threshold | 35.0 | Market Safety |
| Max positions | max_positions | 3 | Portfolio |
| Spread limit | max_entry_spread_pct | 1% | Execution |
| Stop-loss | stop_loss_pct | 5.5% | Risk |
| Daily loss limit | daily_loss_limit_pct | 10% | Circuit Breaker |
