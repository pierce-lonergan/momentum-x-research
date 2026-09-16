# Alternative Data Pipeline: Free Tier Architecture

**Version**: D148 (March 28, 2026)
**Goal**: Predict which gap-up stocks produce outlier bar-1 spikes (+3%)
**Constraint**: Zero cost. Free tier APIs only. 15-min delay acceptable for training data.

---

## 1. What We Actually Need (vs What's Available)

The bar-1 exit strategy is outlier-driven: 5 of 79 trades produce 85% of P&L.
All 5 outliers are $3-$5 stocks with extremely low float (EEIQ: 1.48M outstanding).
The hypothesis: **low float + high RVOL + social buzz = supply constraint = bigger spike.**

We need features collected BEFORE market open (during Phase 1, 04:30-09:25 ET)
that correlate with bar-1 return > 3%. After 200+ enriched trades, train a model.

### Feature Priority (Based on Research + Our Data)

| Feature | Why It Matters | Free Source | Status |
|---------|---------------|-------------|--------|
| **Float size** | Low float = supply constraint = bigger spikes | Finnhub profile2, yfinance | **LIVE** |
| **Shares outstanding** | Context for float | Finnhub profile2 | **LIVE** |
| **Market cap** | Micro-cap = retail-driven = more volatile | Finnhub profile2 | **LIVE** |
| **Industry** | Sector context | Finnhub profile2 | **LIVE** |
| **Pre-market vol acceleration** | Building demand into open | Bar data computation | **BUILT** |
| **Short interest %** | Squeeze potential | yfinance `ticker.info` | **TO BUILD** |
| **Days to cover** | Squeeze urgency | yfinance `ticker.info` | **TO BUILD** |
| **Reddit mention velocity** | Retail attention acceleration | PRAW (Reddit API) | **TO BUILD** |
| **News sentiment** | Catalyst identification | Finnhub news sentiment | **TO BUILD** |
| **Options put/call ratio** | Smart money positioning | Alpaca options, yfinance | **BUILT** |

---

## 2. Free Sources Evaluated (Honest Assessment)

### Tier 1: Already Working

| Source | Endpoint | Rate Limit | What We Get |
|--------|----------|-----------|-------------|
| **Finnhub** | `/stock/profile2` | 60/min | Float, outstanding, market cap, industry |
| **Alpaca** | Options provider | 200/min | Call/put OI near strike |
| **Bar data** | Local computation | Unlimited | Pre-market volume acceleration |

### Tier 2: Build This Week (Free, Reliable)

| Source | Method | Rate Limit | What We Get |
|--------|--------|-----------|-------------|
| **yfinance** | `ticker.info` | ~2000/hr | Short interest %, days to cover, float (backup) |
| **Finnhub** | `/company-news` | 60/min | News headlines (free tier confirmed) |
| **Reddit PRAW** | `subreddit.new()` | 60-100/min | Mention velocity from r/wallstreetbets, r/pennystocks |

### Tier 3: Premium Only (Skip for Now)

| Source | Why Skip |
|--------|----------|
| Finnhub social-sentiment | 403 on free tier (tested) |
| Finnhub short-interest | 403 on free tier (tested) |
| Twitter/X | $200/month minimum |
| StockTwits API | Closed to new registrations since 2021 |
| Alpha Vantage | Only 25 calls/day (useless for scanning) |

---

## 3. Implementation Plan

### Phase A: yfinance Short Interest (Today, 1 Hour)

Add yfinance as a data source in `src/data/enrichment.py`. Single call per
ticker provides: sharesShort, shortRatio, shortPercentOfFloat,
sharesShortPriorMonth, floatShares (backup for Finnhub).

**Rate budget:** 15 candidates x 1 call = 15 calls per scan cycle. At
~2000/hr yfinance tolerance, this is negligible.

**Staleness:** Short interest is 7-14 days old (FINRA bi-monthly reporting).
Acceptable — we're looking for structural squeeze setups, not daily changes.

**Implementation:**
```python
import yfinance as yf
ticker = yf.Ticker("EEIQ")
info = ticker.info
short_pct = info.get("shortPercentOfFloat", 0)  # e.g., 0.15 = 15%
days_to_cover = info.get("shortRatio", 0)
float_shares = info.get("floatShares", 0)  # Backup for Finnhub
```

### Phase B: Finnhub News Sentiment (Today, 1 Hour)

Use Finnhub `/company-news` endpoint (free tier, confirmed working).
Fetch recent news for each candidate. Count headlines. Score via
simple keyword matching (FDA, earnings, contract, upgrade = bullish).

**Rate budget:** 15 candidates x 1 call = 15/min. Well under 60/min limit.

**Implementation:**
```python
# Finnhub /company-news is free tier
resp = await client.get(
    "https://finnhub.io/api/v1/company-news",
    params={
        "symbol": ticker,
        "from": yesterday,
        "to": today,
        "token": FINNHUB_KEY,
    },
)
# Returns list of {headline, summary, source, datetime, ...}
# Count recent headlines as proxy for catalyst presence
```

### Phase C: Reddit Mention Velocity (This Week, 2 Hours)

Use PRAW to monitor r/wallstreetbets and r/pennystocks. Count ticker
mentions in recent posts. Compute velocity (mentions per hour).

**Rate budget:** 60-100 calls/min via PRAW. Need ~20 calls per scan
(fetch recent posts, search for ticker patterns). Well within limits.

**Requires:** Reddit API credentials (free, OAuth app registration).

**Implementation:**
```python
import praw
reddit = praw.Reddit(client_id=..., client_secret=..., user_agent=...)
wsb = reddit.subreddit("wallstreetbets")
# Count mentions of $EEIQ in recent posts
recent = list(wsb.new(limit=100))
mentions = sum(1 for post in recent if "EEIQ" in post.title.upper())
```

### Phase D: FinBERT Sentiment Scoring (This Month, 3 Hours)

Run Finnhub news headlines through FinBERT (free, local, HuggingFace).
Produces sentiment score (-1 to +1) per headline. Average across
headlines for composite catalyst sentiment.

**Requires:** `pip install transformers torch` (~2GB model download once).
Runs locally, no API cost. ~0.5s per headline on CPU.

---

## 4. Rate Limit Safety Architecture

### Per-Scan Budget (15 candidates, every 5 minutes)

| Source | Calls/Scan | Calls/Hour | Free Limit/Hour | Headroom |
|--------|-----------|-----------|-----------------|----------|
| Finnhub profile2 | 15 | 180 | 3,600 | 20x |
| Finnhub news | 15 | 180 | 3,600 | 20x |
| yfinance | 15 | 180 | ~2,000 | 11x |
| PRAW | 5 | 60 | 6,000 | 100x |
| **Total** | **50** | **600** | — | **Safe** |

### Safety Nets

1. **Per-source throttle:** 100ms between Finnhub calls (already implemented)
2. **429 backoff:** On any 429, wait 60s then halve request rate for 5 min
3. **Graceful degradation:** Missing data returns None, never blocks
4. **Daily budget tracking:** Log total calls per source per day
5. **Kill switch:** If any source returns 5 consecutive 429s, disable for 1 hour

---

## 5. Data Storage

All enrichment data stored in two places:

1. **Journal entry** (per-trade): enrichment fields added to CandidateContext
   and carried through to trade results for ML training.

2. **Enrichment cache** (per-date): `data/enrichment/YYYY-MM-DD.json`
   stores full enrichment for all candidates scanned that day.
   Enables retroactive analysis without re-fetching.

---

## 6. The ML Training Path

After 200+ enriched trades (2-3 months at 4 trades/day):

**Features:** float_shares, shares_outstanding, market_cap, industry,
short_interest_pct, days_to_cover, reddit_velocity, news_headline_count,
news_sentiment_avg, options_pc_ratio, gap_pct, rvol, premarket_vol_accel

**Target:** bar_1_return > 0.03 (binary: outlier or not)

**Model:** Logistic regression first (interpretable, 13 features on 200
samples is fine). Then XGBoost if more data accumulates.

**Validation:** Walk-forward (always). If precision > 20% at recall > 50%
(identifies 2.5 of 5 outliers while flagging 12 candidates), deploy as
candidate ranking signal. Enter top-ranked candidates first.

**Expected impact:** If outlier frequency triples from 6.3% to 15-20%
on top-ranked candidates, P&L roughly triples. This is the single
largest remaining lever in the entire system.
