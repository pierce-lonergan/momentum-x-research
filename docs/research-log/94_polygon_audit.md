# 94 — Polygon Audit: We're Renting a Library and Reading Five Books

**Date:** 2026-05-02
**Plan:** Stocks Advanced ($199/mo) — unlimited REST calls, real-time SIP, 10y
history, NBBO ticks, options access, websocket streams, flat files (S3 bulk).
**Current usage:** 5 of ~30 REST endpoints, 0 websocket streams, 0 options
data, 0 news, 0 flat files.
**Estimated value extracted:** ~15% of subscription. The rest is actual
information sitting on a shelf.

---

## §1 — Current footprint (audited from code)

### §1.1 — Endpoints touched

| Endpoint | Use case | Code path | Cache TTL |
|----------|----------|-----------|-----------|
| `/v2/aggs/ticker/{T}/range/{n}/{tf}/{from}/{to}` | minute & daily bars | `polygon_backfill_all.py`, `unified_bar_source.py` | forever (historical immutable) |
| `/v3/quotes/{T}` | NBBO ticks | `polygon_backfill_all.py` (tick validation only) | forever |
| `/v3/trades/{T}` | trade prints | `polygon_backfill_all.py` (tick validation only) | forever |
| `/v3/reference/tickers/{T}` | name, type, market_cap, share class | `extract_prod_qty_truth.py` | 1 day |
| `/vX/reference/financials` | quarterly fundamentals (MAGNA-N) | `polygon_backfill_all.py`, `classify_carry_trades_magna_n.py` | forever |
| `/v2/snapshot/` | (configured in TTL map but **no code path actually calls it**) | — | 0 |

**Cached storage**: 1.4 GB (171M minute bars + 1.2G ETF minute bars + 78M tick
data + 7.6M fundamentals).

### §1.2 — Wrapper architecture

`mx-arena/data_providers/polygon/`:
- `client.py` (307 LOC): httpx async + retry/429 backoff + file cache + cursor
  pagination. Solid foundation.
- `endpoints.py`: 5 typed wrappers — `aggregates`, `quotes`, `trades`,
  `ticker_details`, `financials`. **No wrappers for the other 25+ endpoints.**
- `models.py`: dataclasses for Bar, Quote, Trade, TickerRef, FinancialReport.

`scripts/polygon_backfill_all.py` (437 LOC): bulk orchestrator with 4 corpora
(`tick_validation`, `etf_baseline`, `equity_universe`, `fundamentals`).
Resumable via parquet progress files. Exists ONLY for backfill — never invoked
during live trading.

### §1.3 — What's NOT in the codebase at all

- No `wss://socket.polygon.io/...` connection anywhere
- No `/v3/reference/options/contracts` (options chains)
- No `/v3/snapshot/options/{underlying}` (options Greeks)
- No `/v3/reference/news` (news API)
- No `/v1/indicators/*` (server-side technicals)
- No `/v3/snapshot/locale/us/markets/stocks/{direction}` (gainers/losers)
- No `/v2/market/short-interest/{T}` (FINRA short interest)
- No `/v2/aggs/grouped/locale/us/market/stocks/{date}` (whole-market daily)
- No flat-file S3 downloads (`https://files.polygon.io`)
- No `/v3/reference/dividends` or `/v3/reference/splits` (corp actions)
- No `/v3/reference/exchanges`, `/v3/reference/conditions`
- No `/v1/marketstatus/now` (market status)
- No `/v3/reference/tickers/types` (ticker classification)

---

## §2 — Polygon's full capability surface (what $199/mo buys)

I'll group by API surface; the bold rows are what we should care about.

### §2.1 — Reference data (slow-changing universe)

| Endpoint | Returns | Why we should care |
|----------|---------|---------------------|
| `/v3/reference/tickers` (list) | every ticker, type, market, currency | Universe construction; filter by type before scanning |
| **`/v3/reference/tickers/types`** | enumerated types (CS, ADR, ETF, ETN, ETV, ETS, FUND, WARRANT, RIGHT, UNIT, BOND, SP, BASKET, …) | **Cleanly classify what's in our watchlist — the leveraged-ETF audit (R3 in doc 90) had false positives because we filtered by name; types is canonical** |
| **`/v3/reference/tickers/{T}`** | name, mcap, share class, locale, sic, …, **branding, homepage_url** | Canonical metadata; what we already use for fundamentals lookup |
| **`/v3/reference/dividends`** | ex-date, declaration, cash amount | Catalyst detection (ex-div drops are not pump fades) |
| **`/v3/reference/splits`** | execution date, ratio | Reverse-split detection (4-for-1 reverse = bankruptcy candidate, NOT a pump) |
| **`/v3/reference/conditions`** | trade & quote condition code dictionary | We have raw codes in the tick data — without this we can't filter regular vs odd-lot vs intermarket sweep |
| `/v3/reference/exchanges` | exchange codes + names | Identify exchange routing patterns |
| **`/v3/reference/options/contracts`** | options chain (strike, expiry, OI) | **Critical for GEX, IV, options-sniffing flow** |

### §2.2 — Aggregates / bars (we use this)

| Endpoint | Returns | Notes |
|----------|---------|-------|
| `/v2/aggs/ticker/{T}/range/{n}/{tf}/{from}/{to}` | OHLCV+VWAP at any tf | What we use |
| **`/v2/aggs/grouped/locale/us/market/stocks/{date}`** | EVERY US ticker's daily OHLCV in one call | **Bulk daily snapshot — 9000 tickers in one request. Better than 9000 separate calls.** |
| **`/v2/aggs/ticker/{T}/prev`** | previous trading day OHLCV | One-shot for "is today's open above yesterday's close" |

### §2.3 — Trades & quotes (we use this for tick validation)

| Endpoint | Returns | Notes |
|----------|---------|-------|
| `/v3/trades/{T}` | every trade print with conditions, exchange | What we use, but we don't decode conditions |
| `/v3/quotes/{T}` | every NBBO update with bid/ask sizes | What we use, but we don't compute spread metrics in real time |
| **`/v3/trades/{T}` (real-time)** | same data live via REST polling | **Could feed our scanner without a websocket dep** |

### §2.4 — Snapshots (none used)

| Endpoint | Returns | Why this matters NOW |
|----------|---------|----------------------|
| **`/v3/snapshot/locale/us/markets/stocks/gainers`** | top 50 gainers (live) | **Direct alternative to Alpaca's stale screener — see doc 92 §6.1** |
| **`/v3/snapshot/locale/us/markets/stocks/losers`** | top 50 losers | **Short-side candidate generation (E11 in doc 93)** |
| **`/v3/snapshot/locale/us/markets/stocks/tickers`** | full snapshot of all tickers | **One call, every ticker's last trade + bid/ask + day stats** |
| `/v3/snapshot/locale/us/markets/stocks/tickers/{T}` | one ticker's full state | Per-ticker quick check |
| **`/v3/snapshot/options/{underlying}`** | full options chain + Greeks | **Sweep detection, IV regime, gamma walls** |
| `/v2/snapshot/locale/us/markets/stocks/direction/{dir}` (legacy) | older equivalent of gainers/losers | Use v3 |

### §2.5 — Server-side technicals (none used)

Polygon computes these for free on their compute, returns aligned to bars:

| Endpoint | Indicator | Avoided by computing locally? |
|----------|-----------|--------------------------------|
| `/v1/indicators/sma/{T}` | simple moving average | Trivial locally |
| `/v1/indicators/ema/{T}` | exponential MA | Trivial locally |
| `/v1/indicators/rsi/{T}` | RSI | Saves CPU + standardizes definition across team |
| `/v1/indicators/macd/{T}` | MACD with signal | Same |

These are convenience — saves us writing TA libs. Marginal value.

### §2.6 — News (none used) ⭐

| Endpoint | Returns | Why we should care |
|----------|---------|---------------------|
| **`/v3/reference/news`** | articles, publisher, publish-time, ticker-tags, **insights (sentiment + reasoning per article)** | **Direct catalyst signal. RPGL/RYOJ failed Friday probably because no real news. Can gate selection on "ticker has 8-K-grade news in last 4h"** |

The `insights` field is the killer feature: Polygon runs LLM scoring on each
article and returns `{sentiment: positive/negative/neutral, sentiment_reasoning: "..."}`
per ticker per article. Free signal we're paying for.

### §2.7 — Short interest (none used)

| Endpoint | Returns | Why |
|----------|---------|-----|
| **`/v2/market/short-interest/{T}`** | bi-monthly FINRA short interest | Squeeze candidate scoring; high SI + gap-up = potential explosion |
| `/v3/short-volume/{T}` | daily short-volume / total volume | Real-time short pressure |

### §2.8 — Options (none used) ⭐⭐

The biggest entirely-unused capability:

| Endpoint | Returns | Why |
|----------|---------|-----|
| **`/v3/snapshot/options/{T}`** | full chain + last trade + IV + Greeks per contract | **Gamma-wall detection, dealer hedging proxy, IV term structure** |
| `/v3/reference/options/contracts` | every contract for an underlying | Strike grid construction |
| `/v3/trades/options/{T}` | options trade prints | Sweep detection |
| `/v2/aggs/ticker/O:{contract}/range/...` | aggregate bars per contract | Historical IV / OI |

Options data on small-caps is THIN but real. Where it does flow, it's
operationally informative — large block calls below ATM = institutional
short hedge unwind = upward pressure on underlying.

### §2.9 — Market status / corporate actions

| Endpoint | Returns | Why |
|----------|---------|-----|
| `/v1/marketstatus/now` | open/closed + early close flag | Skip lottery on early-close days |
| `/v1/marketstatus/upcoming` | holiday calendar | Pre-fetch into the scheduler |

### §2.10 — Flat files (S3 bulk) ⭐⭐⭐

`https://files.polygon.io/flatfiles/us_stocks_sip/` — the secret weapon.

For every trading day, Polygon publishes:
- `day_aggs_v1/{YYYY}/{MM}/{date}.csv.gz` — every ticker's daily OHLCV
- `minute_aggs_v1/{YYYY}/{MM}/{date}.csv.gz` — every ticker's 1-min bars
- `trades_v1/{YYYY}/{MM}/{date}.csv.gz` — every trade for the day
- `quotes_v1/{YYYY}/{MM}/{date}.csv.gz` — every NBBO update for the day

**Daily file sizes** (approximate from public docs):
- Daily aggs: ~3 MB
- Minute aggs: ~150 MB
- Trades: ~5–15 GB
- Quotes: ~30–60 GB

**Why this matters:** REST pagination for "every ticker's minute bars on 2026-05-01"
takes ~9000 calls × 30s each = 75 hours and pummels rate limits. The flat file is
ONE 150 MB download and gives the same data.

**For our 88-day backtest universe, this is the bulk-pull cheat code.**

### §2.11 — Websocket streams (none used) ⭐

We use Alpaca's websocket. Polygon's offers:

| Channel | What it pushes | Why we'd want it |
|---------|----------------|-------------------|
| `T.{T}` | per-trade prints (live SIP) | Tick-by-tick price (lower-latency than 1-min bars) |
| `Q.{T}` | per-quote NBBO updates | Real-time spread monitoring (E5 in doc 93) |
| `A.{T}` | per-second aggregates | 1-second bars for finer entry timing |
| `AM.{T}` | per-minute aggregates | What we already build from REST polling |
| `XQ.{T}` | crypto quotes | (out of scope) |
| **`TA.{T}`** | trade aggregates with conditions | **Decoded condition codes in real time — separate sweep from regular order** |

Polygon's SIP feed includes `ARCA, BATS, EDGA, EDGX, IEX, NSDQ, NYSE, ...`.
Alpaca's free feed is IEX-only (about 2-3% of total volume). **Polygon's SIP
gives us 100% of US equity flow vs Alpaca's ~2%.**

---

## §3 — Gap analysis: ranked by impact on signal/gate/slippage/arena

| # | Gap | What it would unlock | Effort | Value |
|---|-----|---------------------|--------|-------|
| 1 | **Snapshot gainers/losers** (REST polling 09:25 ET) | Replace Alpaca's stale screener (the doc 92 §6.1 issue). Confirmed-fresh universe. | 4 hours | **HIGH** |
| 2 | **News API + insights field** | Gate selection on "ticker has 8-K-grade news in last 4h." Would have killed RPGL/RYOJ. | 1 day | **HIGH** |
| 3 | **Flat files (S3 bulk)** for missing historical context | Backfill 2-3 years of complete-universe minute aggs for arena replay. Currently we only have 88 days of partial. Order-of-magnitude bigger arena. | 2 days code + days of download | **HIGH** |
| 4 | **Reference/tickers/types** | Canonical type classification (was hand-rolled with false positives in R3) | 2 hours | MEDIUM |
| 5 | **Reference/dividends + splits** | Distinguish "real catalyst gap" from "ex-div drop" or "reverse-split bankruptcy" | half day | MEDIUM |
| 6 | **Conditions decoding** | Filter regular vs odd-lot vs sweep in our existing tick data. Better slippage attribution. | half day | MEDIUM |
| 7 | **Options snapshots** (small-caps that have chains) | Gamma-wall detection; dealer hedging position proxy. Most of our universe is too small for this to matter day-to-day. | 1 day | LOW-MEDIUM (universe-dependent) |
| 8 | **Polygon websocket SIP feed** | Tick-by-tick from full SIP (vs Alpaca's IEX-only ~2%). Real-time spread + true VWAP. | 2 days | MEDIUM-HIGH |
| 9 | **Short interest + short volume** | Squeeze-candidate scoring | 4 hours | MEDIUM |
| 10 | **Market status calendar** | Skip early-close days, halve trail width on volatile days | 1 hour | LOW |
| 11 | **Server-side technicals (RSI/MACD)** | Convenience only | 2 hours | LOW |
| 12 | **Aggs grouped (whole market daily)** | Replace 9000-call universe scan with 1 call | 2 hours | MEDIUM |

---

## §4 — Concrete bulk-pull plan (next 1–2 weeks)

The user said: *"I'm open to writing a script and downloading terabytes over multiple days if it meaningfully improves arena or model quality."* Here's how I'd budget that.

### §4.1 — Phase 1: Snapshot gainers/losers (immediate, half day)

**Goal**: replace Alpaca screener with Polygon snapshot.

```python
# scripts/polygon_snapshot_gainers.py
async def fetch_gainers(client, limit=50):
    resp = await client.get("/v3/snapshot/locale/us/markets/stocks/gainers")
    return resp.data.get("tickers", [])[:limit]
```

Wire into `lottery_runner.py` as a second source — pull both Alpaca + Polygon,
deduplicate, take union ranked by % change. Validates the fix from doc 92 §6.1
without needing to wait 30 days.

### §4.2 — Phase 2: News API integration (1 day)

**Goal**: gate selection on real catalysts.

```python
# scripts/polygon_news_lookup.py
async def fetch_recent_news(client, ticker, hours=4):
    since_iso = (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    resp = await client.get("/v3/reference/news", {
        "ticker": ticker,
        "published_utc.gte": since_iso,
        "limit": 10,
    })
    return resp.results
```

Tag each lottery candidate with:
- `n_articles_4h`: count of news mentions
- `latest_sentiment`: from Polygon's `insights[]` field
- `has_8K_grade`: boolean (publisher in {SEC, BusinessWire, PR Newswire})

Monday-EOD analysis: for our 8 picks, how many had `n_articles_4h > 0`?
Hypothesis: HCAI/MRAM/XRX did, RPGL/RYOJ didn't.

### §4.3 — Phase 3: Flat-file bulk pull (2 weeks of overnight downloads)

**Goal**: 2-3 years of complete-universe minute aggs for arena.

```python
# scripts/polygon_flatfile_pull.py
import boto3  # Polygon uses S3-compatible storage
import os

s3 = boto3.client(
    "s3",
    endpoint_url="https://files.polygon.io",
    aws_access_key_id=POLYGON_S3_KEY_ID,
    aws_secret_access_key=POLYGON_S3_SECRET,
)

for day in trading_days(start="2024-01-01", end="2026-04-30"):
    key = f"us_stocks_sip/minute_aggs_v1/{day:%Y/%m/%d}.csv.gz"
    s3.download_file("flatfiles", key, f"data/polygon_flatfiles/{key}")
```

**Storage budget**: 600 trading days × 150 MB = **90 GB**. Plenty of room
on a typical SSD.

**Time budget**: at home-broadband 50 MB/s, ~30 min per file = 300 hours
over a month of overnight runs. Or run it on a cloud VM and rsync down.

**What this unlocks**:
- Arena replay against EVERY ticker (not just 88 days × ~30 watchlist tickers)
- Universe-level signals: cross-sectional momentum, sector rotation, market
  breadth — currently invisible because we only see watchlist
- True walk-forward validation with 600 days of out-of-sample post-strategy-design
- Counterfactuals: "if we'd run the lottery 2 years ago, what would it have done?"

### §4.4 — Phase 4: Tick-level data for top movers (selective bulk)

**Goal**: full quotes + trades for every ticker that had ≥30% intraday move
in the last 2 years (the high-mover catalog).

REST is too slow. Flat files are overkill (we don't need every tick of every
boring stock). Compromise: pull `quotes_v1/{date}.csv.gz` ONCE per day (60 GB
× 600 days = **36 TB**), filter to high-mover tickers in-place using polars,
write per-(date,ticker) parquets, delete the source.

**Net storage**: ~20 GB (filtered).
**Net time**: 600 days × 60 GB / 50 MB/s = 200 hours of download + filter.

**What this unlocks**:
- Order-flow imbalance signal at tick level (real version of E5 spread gate)
- Sweep detection (decoded condition codes)
- Internalized vs lit-market routing patterns (PFOF leakage analysis)
- True microstructure for the EVENTS we actually care about

### §4.5 — Phase 5: Options chains for borderline-largecap names

**Goal**: identify when a "small-cap" actually has tradeable options.

For every ticker that appears in our watchlist with mcap > $500M, fetch:
```
/v3/snapshot/options/{T}
```

Most tickers will return empty (no chain). For the ones with chains, store:
- ATM IV
- 30d IV term structure
- Total OI by strike (gamma-wall map)

**Storage budget**: ~5 MB per ticker per day; expect 50-100 tickers/day = 0.5 GB/day.

**What this unlocks**:
- Identify dealer-hedging-driven moves (gamma walls)
- Cross-asset signal: when options IV spikes WITHOUT spot moving, something is brewing
- Adds an entirely new selection axis ("only buy gappers WITH IV expansion")

---

## §5 — Where the audit lands

| Question | Answer |
|----------|--------|
| Are we using our $199/mo well? | NO — 5 of ~30 endpoints, 0 websocket, 0 options, 0 news, 0 flat files |
| What's the highest-impact gap? | News API + Snapshot gainers + Flat files (in order) |
| Would bulk pulling improve arena? | YES dramatically — current arena is 88 days × ~30 tickers; flat files give 600+ days × full universe |
| Should we cancel and downgrade to Starter? | NO — but only because we can fix the under-utilization in 1-2 weeks of code |
| If we did all of §4, what's the one-line summary? | "We'd shift from 'a strategy that trades a thin slice' to 'an arena that knows the entire US equity market for 2.5 years.'" |

---

## §6 — Implementation sequence (recommended)

| Week | Action | Outcome |
|------|--------|---------|
| W1 Mon | Add `/v3/snapshot/.../gainers` to lottery as second source | Replaces stale Alpaca screener (doc 92 §6.1) |
| W1 Tue | Add `/v3/reference/news` lookup for each candidate | Tag with sentiment + article count |
| W1 Wed | Wire news tag into selection (E7 from doc 93) | Selection improvement |
| W1 Thu | Add `/v3/reference/dividends + /v3/reference/splits` | Catalyst-context tagging |
| W1 Fri | Backtest doc 90 with news tag | "How would E7 have changed the lottery's 88-day P&L?" |
| W2 | Set up flat-files S3 credentials + pull 1 month as proof of concept | Arena infrastructure milestone |
| W3-W4 | Background overnight pulls of 2.5 years minute aggs | Universe-scale arena |
| W5+ | Tick-level for high-mover catalog | Microstructure depth |

---

## §7 — Risks & open questions

1. **Polygon S3 credentials**: separate from REST API key. Need to request via
   Polygon dashboard. Plan supports it (Stocks Advanced includes flat files).
2. **Storage growth**: 100GB → 300GB → 1TB+ if we go deep. SSD or external?
3. **Cache invalidation**: our current `data/polygon_cache/` mixes endpoints.
   With more endpoints we'll want sharded structure.
4. **Rate limits during bulk pulls**: Stocks Advanced has no documented hard
   cap but we should still pace at ~10 req/s to be polite.
5. **News API cost**: included in Stocks Advanced or extra? Verify.
6. **Options data quality on small-caps**: many lottery candidates won't have
   chains. Worth checking before building elaborate gamma-wall logic.

---

> "We're paying for a library and only reading the books on the front shelf.
> The back rooms have first editions of every minute, every quote, every
> news article on every ticker for the last decade. Walking back there is
> just code."
