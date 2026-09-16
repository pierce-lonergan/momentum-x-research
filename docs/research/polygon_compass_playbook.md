# MOMENTUM-X × Polygon.io: Deep Tactics & Hierarchical Temporal Modeling Playbook

## TL;DR
- **You are leaving ~80% of your $199/mo subscription on the table.** The single biggest unlock is Flat Files (S3) — bulk-download `day_aggs_v1`, `minute_aggs_v1`, `trades_v1`, and `quotes_v1` to backfill 600+ days × full US universe (~12 TB total, ~4 TB just for trades), then layer the WebSocket SIP feed (~100% of consolidated tape vs. Alpaca's IEX-only ~2% on free tier) and the news/Insights endpoints for catalyst gating. With Stocks Advanced you have unlimited REST calls and a single concurrent WS connection per cluster.
- **For microcap gap-up momentum, the Polygon-specific edges that matter most are**: (1) tick-level `trades_v1` for sweep/print-tape forensics with full condition codes; (2) NBBO `quotes_v1` for true spread/depth at sub-second resolution rather than 1-min bar reconstruction; (3) the Universal Snapshot (250-symbol cap) and gainers/losers endpoints for premarket gap detection and intraday rotation; (4) `/v3/reference/tickers` + ticker-types (CS/ADRC/WARRANT/UNIT/RIGHT/PFD) and splits/dividends for survivorship- and corporate-action-clean universe filters; (5) the Benzinga-powered news API with per-ticker sentiment Insights for catalyst quality scoring.
- **For dynamic, hierarchical temporal modeling, the most defensible direction is a clockwork/dilated tick→second→minute→hour→day pyramid** — Mamba/S4 selective state-space backbones for the dense intraday levels, a BOCPD or HSMM regime header on top, and HTM-style streaming anomaly detection running parallel as a non-stationary "smoke alarm." Frontier ideas (diffusion planners, active inference, stigmergic agent meshes) are credible research directions but **none have a clean published track record on US microcap gap-and-go** — speculate boldly, but treat them as bet-small / instrument-only experiments inside `mx-arena` until proven.

---

## Key Findings

| Capability | Polygon.io Stocks Advanced ($199/mo) | Practical impact for MOMENTUM-X |
|---|---|---|
| Flat Files (S3) | Included; daily/minute/trades/quotes from full SIP, 20+ yr history | Replaces 88-day × 30-ticker replay with 20-yr × full universe; ~12 TB if you take everything |
| WebSocket | 1 concurrent connection / cluster; T, Q, A (per-sec), AM (per-min), LULD, FMV (Business only) | True SIP coverage vs. Alpaca free-tier IEX (~2% of consolidated volume) |
| REST | Effectively unlimited request rate on Advanced (soft-capped by infra, no documented hard limit) | Lets you parallelize universe scans; concurrency 8–32 is typical sweet spot |
| News + Insights | Included; per-article ticker-level sentiment + reasoning | Catalyst gating signal; Benzinga partnership delivers structured headlines |
| Reference | tickers, types, exchanges, conditions, splits, dividends, **short interest**, **short volume**, **free float**, financials, SEC filings | Universe construction, corporate-action denoising, microcap mcap filtering |
| Snapshots | gainers/losers (top 20), all-tickers (~10,000+ symbols), Universal (max 250) | Premarket gap-up scanner + intraday rotation feed |
| Server-side technicals | SMA, EMA, RSI, MACD | Convenient but slow vs. local compute on Parquet |
| Options | Not included on Stocks Advanced — needs separate Options plan | GEX/sweep ideas below require an additional subscription |

The user's audit (5/30 endpoints, 0 WS, 0 news, 0 flat files) means **every section of Part A below is a live unlock**.

---

## PART A — Polygon-Specific Deep Tactics (~70%)

### 1. Flat Files (S3) — the single biggest unlock

#### S3 mechanics
- **Endpoint:** `https://files.polygon.io` (S3-compatible, **not** AWS S3 itself — region/SigV4 quirks apply).
- **Bucket:** `flatfiles`
- **Auth:** Separate Access Key ID + Secret Access Key from `polygon.io/dashboard/flat-files` (distinct from your REST API key — common gotcha).
- **Top-level prefixes:** `us_stocks_sip/`, `us_options_opra/`, `us_indices/`, `global_forex/`, `global_crypto/`. For MOMENTUM-X you only care about `us_stocks_sip/`.
- **Layout:** `us_stocks_sip/{dataset}_v1/{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz` where `{dataset}` ∈ `{day_aggs, minute_aggs, trades, quotes}`.
- **Availability cadence:** Each day's files appear ~11:00 AM ET the *following* trading day. So your incremental pipeline runs at noon ET daily, not at the close.

#### File formats / schemas (verified from cited Polygon docs)

```text
day_aggs_v1 / minute_aggs_v1 (gzip CSV, header row):
ticker, volume, open, close, high, low, window_start, transactions
# window_start is Unix nanoseconds (UTC). 
# minute_aggs_v1 covers premarket + RTH + after-hours.

trades_v1 (gzip CSV):
ticker, conditions, correction, exchange, id, participant_timestamp,
price, sequence_number, sip_timestamp, size, tape, trf_id, trf_timestamp
# conditions: comma-separated list of integer condition codes (lookup via /v3/reference/conditions).
# exchange: integer (lookup via /v3/reference/exchanges). 4 = FINRA TRF (dark pool flag).
# tape: 1=NYSE (A), 2=AMEX/ARCA (B), 3=Nasdaq (C).
# Timestamps in nanoseconds.

quotes_v1 (gzip CSV):
ticker, ask_exchange, ask_price, ask_size, bid_exchange, bid_price, bid_size,
conditions, indicators, participant_timestamp, sequence_number, sip_timestamp, tape
# Quote sizes are in round lots (multiply by 100 for shares).
```

#### Practical performance & cost
Public production references put the *full* `us_stocks_sip/` corpus at **~12 TB** as of 2024–25; trades alone ~4 TB for 22 years; quotes are roughly 2× trades. For your 600-day target × full universe:
- `day_aggs_v1`: **a few hundred MB total** (each file ~150–250 KB compressed).
- `minute_aggs_v1`: **~30–50 GB total** (each file ~50–80 MB compressed). This is the workhorse.
- `trades_v1`: **~300–500 GB for 600 days** (each file ~500 MB–1 GB compressed).
- `quotes_v1`: **~600 GB–1 TB for 600 days**. Only download if you actually need NBBO history; otherwise reconstruct on demand from REST `/v3/quotes`.

**Client recommendations (real-world consensus):**
- `aws s3 cp/sync --endpoint-url https://files.polygon.io` is the most reliable for very large files (trades/quotes). Multiple users report **rclone hangs/checksum issues on multi-GB trades files**.
- `mc` (MinIO client) is great for ad-hoc preview (`mc cat … | gzcat | head`).
- `boto3` works but is slow single-threaded; pair with `concurrent.futures.ThreadPoolExecutor(max_workers=8–16)` per-file.
- Public throughput reports: tens of MB/s per stream; aggregate **300–800 Mbps** on a well-peered VM is realistic. The 22-yr full sync takes 6–24 hours typical.

```python
# Reference downloader skeleton (boto3, parallel by date):
import boto3, botocore, os, concurrent.futures as cf
from datetime import date, timedelta

s3 = boto3.session.Session(
    aws_access_key_id=os.environ["POLYGON_S3_KEY"],
    aws_secret_access_key=os.environ["POLYGON_S3_SECRET"],
).client("s3", endpoint_url="https://files.polygon.io",
         config=botocore.client.Config(signature_version="s3v4",
                                       max_pool_connections=64))

def fetch(d, dataset="minute_aggs_v1"):
    key = f"us_stocks_sip/{dataset}/{d.year}/{d.month:02d}/{d.isoformat()}.csv.gz"
    out = f"data/{dataset}/{d.year}/{key.split('/')[-1]}"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if not os.path.exists(out):
        s3.download_file("flatfiles", key, out)
    return out

dates = [date(2023,1,1)+timedelta(days=i) for i in range(900)]
with cf.ThreadPoolExecutor(16) as ex:
    list(ex.map(fetch, dates))
```

#### Conversion to Parquet (the only sane long-term layout)
CSV.gz is fine for ingest, miserable for repeated queries. Convert to **Hive-partitioned Parquet by `(year, ticker)` for minute aggs and `(year, month, ticker)` for trades**, with **ZSTD** compression and ~256 MB row groups (DuckDB's documented sweet spot is 100 MB–10 GB; Polars OOMs on 140 GB monolithic files but handles partitions cleanly).

```python
import polars as pl, duckdb
# One day, all tickers → partitioned parquet shard
df = pl.read_csv("data/minute_aggs_v1/2024/2024-03-04.csv.gz")
df = df.with_columns([
    pl.from_epoch("window_start", time_unit="ns").alias("ts_utc"),
    pl.col("window_start").alias("ns"),
]).drop("window_start")
df.write_parquet("warehouse/minute_aggs/year=2024/month=03/day=04.parquet",
                 compression="zstd", row_group_size=200_000)

# Or do it in DuckDB in one shot, partitioned:
duckdb.sql("""
COPY (SELECT *, year(to_timestamp(window_start/1e9)) AS year
        FROM read_csv_auto('data/minute_aggs_v1/**/*.csv.gz'))
TO 'warehouse/minute_aggs' (FORMAT PARQUET, PARTITION_BY (year, ticker),
                            COMPRESSION ZSTD, OVERWRITE_OR_IGNORE);
""")
```

For `mx-arena`'s replay engine the killer pattern is **DuckDB over Hive-partitioned Parquet on local SSD** — predicate pushdown lets you pull a single ticker-day in <100 ms even from a 30 GB minute corpus, and the `httpfs` extension lets you keep the warehouse on S3/MinIO if you outgrow the laptop.

#### Incremental updates
A minimal cron at 12:30 ET:
1. List `us_stocks_sip/{dataset}_v1/{YYYY}/{MM}/` and diff against your local manifest.
2. Download only missing keys.
3. Append-write to the matching partition (DuckDB `COPY ... APPEND TRUE`).
4. On the **first business day after the 15th and last day of the month** (FINRA short interest schedule), also pull the new short-interest snapshot via REST (no flat file).

#### Gotchas & landmines
- **Timestamps are nanoseconds in UTC.** Convert with `pl.from_epoch(col, "ns").dt.convert_time_zone("America/New_York")` before any session logic. Gap detection that uses naive UTC will be 4–5 hours off.
- **Late corrections.** Polygon includes corrected trades but the `correction` column on `trades_v1` is non-zero for them. For replay fidelity you want to *exclude* corrections (because they weren't visible in real time) — this is what `zipline-polygon-bundle` does in `trades_to_custom_aggs`.
- **TRF / dark prints aren't sequenced with exchange trades** — they print on a delay. If you build aggregates from `trades_v1` and don't filter `exchange == 4` (FINRA TRF) or condition codes that mark dark prints, your bars will look "real" but won't match what a live tape consumer would have seen.
- **OPRA vs SIP**: Stocks Advanced gives you the SIP feed (CTA + UTP); options data lives at `us_options_opra/` and is *not* included — separate Options subscription required.
- **Premarket coverage**: minute aggs include 4:00 AM–9:30 AM if eligible trades occurred. Many microcaps have *no* premarket prints, so absence of a bar ≠ "halted" — check `trades_v1` to disambiguate.
- **Adjusted vs. unadjusted**: flat files are **unadjusted**. Splits and reverse splits will create artificial "gaps" in your minute series unless you adjust on the fly using `/v3/reference/splits`.
- **Reverse-split contamination is huge in microcaps.** Polygon dividends are not adjusted for splits either. Build your own adjustment factor stack from `/v3/reference/splits` and apply it at query time, not at write time (so you can re-run without re-downloading).

### 2. WebSocket SIP feed

#### Coverage you're paying for
Polygon ingests directly from CTA (Tape A/B), UTP (Tape C), all 19 major exchanges, FINRA TRF facilities, and OTC. **Effectively 100% of consolidated US equities tape**, vs. Alpaca's free-tier IEX feed which is ~2% of total volume. For microcaps this matters disproportionately because microcap volume is concentrated on the venues IEX *doesn't* see (NSDQ, ARCA, EDGX, dark pools).

#### Channel taxonomy (stocks cluster, `wss://socket.polygon.io/stocks`)
| Channel | Meaning | Use |
|---|---|---|
| `T.{sym}` | Trades | Print-tape, sweep detection, true VWAP |
| `Q.{sym}` | NBBO Quotes | Spread, microprice, depth |
| `A.{sym}` | Per-second OHLCV aggregates | The "bar" your existing 1-min pipeline should subscribe to a level finer |
| `AM.{sym}` | Per-minute OHLCV aggregates | Drop-in replacement for your current 1-min pipeline |
| `LULD.{sym}` | Limit-up/limit-down events | **Critical for microcap halts** |
| `FMV.{sym}` | Fair Market Value | Business plan only — N/A for you |
| `*` wildcard | All symbols | Allowed, but expect tens of thousands of msgs/sec |

#### Connection management — the realities
- Stocks Advanced allows **one concurrent connection per cluster**; opening a second drops the first. If you need to fan out, run multiple processes each with their own API key (Polygon allows multiple keys).
- Polygon's published guidance is unambiguous: **the #1 cause of disconnects is consumer-side backpressure**. Their server-side buffer fills, then they kill your connection. Their explicit recommendation: "read packets off the network immediately, put them into a queue, parse on a separate thread."
- Python single-threaded asyncio works for a few hundred symbols but will collapse on `*`. Two production patterns:
  1. **Producer/consumer with bounded queue** — `websockets` lib has built-in backpressure on the StreamReader; default 1 MB / 32-frame queue. Use `connect(max_size=2**24, max_queue=2**14)` for full-tape consumption.
  2. **Offload to Go/Rust sidecar** — multiple Polygon community reports of single-threaded Python failing at full universe scale; a tiny Go process that writes raw frames to Redis Streams or a UDP socket is a common fix.
- The official `polygon-api-client` (now branded `massive`) has built-in reconnect on the async client and a `max_reconnects` parameter; it does **not** persist subscription state across reconnects — *you* have to re-subscribe on `on_open`. This is the most common silent-failure mode I'd verify in your code.

```python
# Resilient skeleton (websockets lib, raw, since you'll likely outgrow the SDK)
import asyncio, json, websockets

SUBS = ["T.*", "Q.*", "AM.*", "LULD.*"]   # full-tape

async def run(api_key, queue):
    backoff = 1
    while True:
        try:
            async with websockets.connect(
                "wss://socket.polygon.io/stocks",
                max_size=2**24, max_queue=2**14, ping_interval=20
            ) as ws:
                await ws.send(json.dumps({"action":"auth","params":api_key}))
                await ws.send(json.dumps({"action":"subscribe","params":",".join(SUBS)}))
                backoff = 1
                async for raw in ws:
                    queue.put_nowait(raw)   # never block the socket
        except Exception as e:
            await asyncio.sleep(min(backoff, 30)); backoff *= 2
```

#### Decoded condition codes — the goldmine for microcap forensics
Pull `/v3/reference/conditions?asset_class=stocks` once and cache. Trade conditions you want hot in memory:

| Code(s) | Meaning | MOMENTUM-X use |
|---|---|---|
| `12` "Form T" / pre-market trades | Reported outside RTH | Premarket gap formation |
| `15` "Intermarket Sweep" (ISO) | The trade was an ISO | **Gap continuation signal** — institutional aggression |
| `38` "Odd Lot" | <100 shares | Filter out retail noise; common in microcaps |
| `7` "Average Price Trade" | Not last-sale eligible | Don't include in VWAP |
| `6` "Cash Sale" / `13` "Sold Out of Sequence" | Late prints | Exclude from intraday momentum logic |
| `52` "Contingent Trade" | Multi-leg block | Often signals institutional repositioning |

Polygon publishes `update_high_low`, `update_open_close`, `update_volume` flags per condition — **use these, don't hard-code your own** because the SIP rules change yearly.

The famous trick: **a burst of ISO-flagged prints sweeping the offer is a high-conviction continuation signal in microcap gap-ups**. ISOs are exempt from Reg NMS Rule 611 trade-through protection (cited NASDAQ + FINRA documentation), so an institution sending them is explicitly choosing speed over price-improvement — it's a tell.

#### Latency
Polygon does not publish microbenchmarks, but their infra is co-located in Equinix NY4 with the SIPs. Realistic round-trip from "trade prints on Nasdaq" → "your callback fires" is **single-digit to low-tens of milliseconds** on a co-lo'd VM, **30–80 ms** from a typical AWS us-east-1 instance, **150–300 ms** from a residential connection. Vs. Alpaca IEX free, you save the ~10 ms of SIP consolidation but more importantly you actually *see* the trade — most microcap prints never hit IEX.

### 3. News API + Insights

#### Schema (`/v3/reference/news`)
```json
{
  "id": "...", "publisher": {"name", "homepage_url", "logo_url"},
  "title": "...", "author": "...", "published_utc": "RFC3339",
  "article_url": "...", "tickers": ["AAPL","MSFT"],
  "image_url": "...", "description": "...", "keywords": [...],
  "insights": [
     {"ticker":"AAPL", "sentiment":"positive|neutral|negative",
      "sentiment_reasoning":"<2-3 sentence LLM-generated rationale>"}
  ]
}
```

The **Insights field is LLM-generated** — Polygon does not publicly disclose the model but the output is consistent with a mid-tier instruction-tuned LLM (GPT-4-class or Claude-class), not a fine-tuned financial sentiment classifier. Practical implications:
- **It can be wrong on sarcasm, irony, and complex earnings beats-with-bad-guidance** ("Beat top line, lowered guidance" → often labeled positive).
- The `sentiment_reasoning` text is more useful than the label itself — feed it as a feature.
- Latency from publisher push to API availability is **typically <60 seconds for Benzinga/PR Newswire/BusinessWire**, **2–10 minutes for Motley Fool / Seeking Alpha / Reuters aggregations**.

#### Publisher coverage (observed in practice)
Confirmed publishers in Polygon's news feed: Benzinga (now a paid partner with a separate dedicated endpoint at `/v3/partners/benzinga/news`), BusinessWire, PR Newswire, GlobeNewswire, Motley Fool, Seeking Alpha, Zacks, MarketWatch, Reuters re-syndications. **SEC EDGAR 8-K coverage is partial and delayed** — Polygon does have a separate SEC filings API (`POLYGON_IO_LIST_FILINGS`) that is more reliable for primary-source filings.

#### Where Polygon News sits vs. competitors
- **Latency:** ~equivalent to Benzinga Pro for headlines from Benzinga (since it's the same source), 30s–2m slower than Bloomberg/Refinitiv, ~equal to Ravenpack on PR-wire content.
- **Quality:** Insights sentiment is **not** Ravenpack-class. Ravenpack uses entity-resolved, event-typed, novelty-scored, relevance-scored, sentiment-scored output. Polygon gives you a single 3-class label per ticker. Treat Polygon's labels as a *prior*, not a feature in production.
- **Coverage gaps:** No Twitter/X, no Reddit/Stocktwits, no Discord — you need a separate vendor (Tie / Social Market Analytics / RavenPack alt-data) for these. **For microcap pump dynamics this is a real gap** — early Stocktwits velocity is one of the strongest leading indicators for microcap squeezes.

#### Catalyst-gating features for momentum
For a gap-up at the open, computed against the prior 24h news window for that ticker:
1. `n_articles_24h` — raw count
2. `n_unique_publishers_24h` — quality proxy (one publisher × 50 syndications ≠ 50 catalysts)
3. `pct_positive`, `pct_negative` — Insights label distribution
4. `weighted_sentiment` — sum over articles of `tier_weight(publisher) × insight_score`. Manually weight (BusinessWire/PR Newswire = 1.0 for primary-source releases; Benzinga = 0.7 for secondary; Motley Fool = 0.2 for opinion).
5. `time_to_first_article_after_close` — late-night PR (8–10 PM ET) is the canonical microcap pump pattern
6. `co_mention_count` — how many other tickers in the same articles (a "5-stock sympathy roundup" article is much weaker signal than a single-ticker 8-K announcement).

### 4. Snapshot endpoints

- **`/v2/snapshot/locale/us/markets/stocks/gainers`** and `/losers` — top 20, recomputed continuously. Cleared at midnight ET, repopulates from ~4 AM ET as premarket prints arrive. **This is your premarket gap-up scanner with one HTTP call.** Latency from market data to API is sub-second in practice.
- **`/v2/snapshot/locale/us/markets/stocks/tickers`** (the "all tickers" snapshot) — returns last trade, last quote, today's bar, prev day's bar for **every** active equity. Payload is large (multi-MB JSON, ~10k tickers). Polygon's docs note this is "dependent on tech stack" but it works on Advanced. Poll it every 5–15 seconds, not faster — it's not actually a cheap call server-side.
- **`/v3/snapshot` (Universal)** — multi-asset, **max 250 symbols per call**, but supports `ticker.gte/lte` lexicographic ranges so you can shard your watchlist. Use this for your 30→300 ticker focused-watch tier.

**MOMENTUM-X premarket pipeline pattern:**
```
04:30 ET  → /v2/snapshot/.../gainers (top 20)  →  union with watchlist  
04:30 ET  → /v2/snapshot/.../tickers           →  filter pct_change > +5%, vol > 50k, price 0.5–20
          → dedupe via /v3/reference/tickers (filter type = CS or ADRC; drop ETF, WARRANT, RIGHT, UNIT)
          → for each candidate, pull /v3/reference/splits (last 60d) to flag reverse-split risk
          → /v3/reference/news?ticker=X&published_utc.gte=yesterday for catalyst
09:25 ET  → snapshot all candidates one more time → final gap-list to mx-arena
```

### 5. Options on small-caps

**Reality check:** Stocks Advanced **does not include options data**. Options Starter ($29) gets you 5-yr history; Options Advanced ($199) gets full real-time WS. So this is an **incremental $29–$199/mo question**.

For microcaps ($50M–$2B mcap), realistic options coverage:
- Names with weekly options chains: roughly the top ~600 most liquid names. Most microcaps have **only monthlies, only at-the-money strikes, often with 0 OI on Tuesday/Wednesday**.
- Names with no listed options at all: probably 30–50% of the $50M–$500M mcap universe.
- IV computation: Polygon publishes Greeks/IV on `/v3/snapshot/options/{underlying}` using a Black-Scholes pricer with the closing risk-free rate; **stale and unreliable below ~5 contracts/day volume**. For squeezable microcaps the IV print at 9:31 AM is often the prior day's closing IV mechanically rolled forward, not a real market value.
- **GEX computation** is technically possible per the formulas widely cited (`call_gex = γ × OI × 100 × S² × 0.01`; `put_gex = -1 ×` same), but the dealer-positioning assumption that makes GEX work for SPX/SPY breaks down on microcaps where dealers may not be the dominant counterparty. **Don't rely on GEX for microcap gap trades**; it's a mid/large-cap mechanic.
- **Sweep detection in options** uses similar condition codes to equities (intermarket sweep flag); this *can* work as a "smart money" signal on names with active chains.

Recommendation: skip options data for now. The $199 → $398/mo doubling is not justified by microcap gap-up alpha. Revisit only if you broaden into mid-cap squeeze plays where chains are deep.

### 6. Reference data goldmines (often forgotten)

| Endpoint | Stealth value |
|---|---|
| `/v3/reference/dividends` | Distinguish "ex-div drop" from "pump fade" — never short a -3% open if it's just ex-div |
| `/v3/reference/splits` | **Reverse-split flag is your microcap fraud filter.** Reverse splits frequently precede S-1/S-3 dilutive offerings. A reverse split in last 90 days + gap-up = high-probability fade |
| `/v3/reference/conditions` | Already discussed — full taxonomy for sweep/odd-lot/dark-pool detection |
| `/v3/reference/tickers/types` | CS, ADRC, ADRP, ADRR, ADRW, GDR, NYRS, UNIT, RIGHT, PFD, FUND, SP, WARRANT, INDEX, ETF, ETN, OS, BOND, BASKET, AGEN, EQLK, LT, OTHER. **For microcap gap-up you almost certainly want only `CS` + `ADRC`** — UNITs, RIGHTs, WARRANTs all behave totally differently |
| `/v3/reference/exchanges` | Map exchange IDs → ARCA/BATS/EDGA/EDGX/IEX/NSDQ/NYSE; routing analysis (lots of NYSE → ARCA cross-exchange ISOs at the open is a tell) |
| `/v3/reference/short-interest` (`POLYGON_IO_GET_STOCKS_V1_SHORT_INTEREST`) | FINRA bi-monthly short interest, ~4-day reporting lag — useful for *days-to-cover* baseline but NOT real-time |
| `/v3/reference/short-volume` (`POLYGON_IO_GET_STOCKS_V1_SHORT_VOLUME`) | Daily short volume from FINRA (T+1) — **leading indicator for squeeze setups** |
| `/v3/reference/free-float` (`POLYGON_IO_GET_STOCKS_FREE_FLOAT`) | Free float estimate — divide gap-day volume by float for the "rotation ratio" that microcap traders obsess over |
| `/v1/indicators/{sma\|ema\|rsi\|macd}` | Server-side technicals. **Don't use these in production** — they cost you a network round-trip per ticker per timeframe. Compute locally on your Parquet warehouse with `polars` or `pandas-ta`. They're fine for one-off research |

### 7. Bulk-pull strategies & rate limits

Polygon's documentation describes Stocks Advanced as "unlimited API calls" with no published per-second hard limit. In practice:
- Multiple community reports of pushing **100k+ requests/hour without throttling**.
- Polygon imposes **per-connection backpressure** rather than per-request quotas — if you hit them with 256 concurrent connections you'll see slowdowns/503s on individual calls but no ban.
- **Optimal concurrency for bulk REST pulls is typically 16–32 workers**. More than that hits diminishing returns and increases tail-latency variance.
- For non-trade/quote bulk historical, **always prefer flat files**. REST `/v2/aggs` for full universe × 600 days is theoretically possible but practically silly — that's ~6M requests vs. 600 file downloads.
- **Cache reference data hard**: tickers, types, exchanges, conditions, splits change daily at most. A 24h TTL cache + a forced-refresh on ticker-not-found should be your default.

```python
# Decision tree
if dataset == "historical bulk":  use flat files (S3)
elif dataset == "real-time stream":  use WebSocket (T, Q, A, AM, LULD)
elif dataset == "ad-hoc query / single-symbol / paginated":  use REST
elif dataset == "static reference":  REST + 24h disk cache
```

---

## PART B — Dynamic, Hierarchical Temporal Modeling (~30%)

The user has explicitly invited speculation. The most underrated insight here is: **microcap gap-up momentum is dominantly a non-stationary, regime-switched, event-driven process — so the right architectures are those that handle non-stationarity natively, not those that maximize fit on a stationary backtest**.

### 1. Multi-scale temporal hierarchy — the tick→daily ladder

A defensible architectural sketch:

```
                 ┌──────────────────────────────────────────┐
                 │  Daily/Multi-day regime header           │
                 │  • BOCPD (Bayesian Online Changepoint)   │
                 │  • Hamilton 2-state HMM on overnight returns  
                 │  • Output: P(continuation regime), expected run length
                 └────────────────────┬─────────────────────┘
                                      ▼ conditioning vector
                 ┌──────────────────────────────────────────┐
                 │  Hourly / 15-min Mamba/S4 backbone       │
                 │  • Inputs: minute aggs + sentiment delta │
                 │  • 4–8 hr context window                 │
                 └────────────────────┬─────────────────────┘
                                      ▼ context vector
                 ┌──────────────────────────────────────────┐
                 │  Minute / second Dilated TCN or Mamba    │
                 │  • Inputs: WS A/AM bars + microstructure │
                 │  • Output: 1-/5-min directional + IV     │
                 └────────────────────┬─────────────────────┘
                                      ▼
                 ┌──────────────────────────────────────────┐
                 │  Tick layer — order-flow features        │
                 │  • Sweep counter, ISO-burst rate, OFI    │
                 │  • Bid-side vs offer-side print ratio    │
                 │  • Streamed via T./Q. WS                 │
                 └──────────────────────────────────────────┘
```

**Why this shape:**
- The **Clockwork RNN** (Koutník et al. 2014, arXiv:1402.3511) and **Dilated RNN** (Chang et al. 2017, arXiv:1710.02224) both prove the same point: forcing different layers to update at different *clock rates* both reduces parameters and improves long-horizon learning. A microcap gap-up has signal at every scale — the daily catalyst, the premarket print pace, the open auction imbalance, the first-5-min flush — and giving each its own clock is the natural inductive bias.
- **Mamba / S4** state-space models (Gu & Dao, 2023, arXiv:2312.00752) have linear-time complexity in sequence length, which is the only viable option for tick-level histories. Recent work (CryptoMamba, Shi et al. 2025, arXiv:2501.01010; FinMamba, Chen et al. 2025; CMDMamba, Frontiers in AI 2025) demonstrates Mamba beating Transformer baselines on financial sequences with much lower compute. **Caveat:** These are univariate/few-asset benchmarks; nobody has published Mamba results on microcap gap-up specifically.
- **Transformers (Informer, Autoformer, FEDformer)** have a complicated track record in finance. Zeng et al. (2022, arXiv:2205.13504, "Are Transformers Effective for Time Series Forecasting?") show a simple linear baseline (DLinear) beating most Transformer variants on standard TS benchmarks. So treat Informer/Autoformer with skepticism on noisy financial series — they're useful for the hourly/daily layer where seasonality matters, less useful intraday.

### 2. Regime-switching / dynamic systems layer

For the daily/multi-day layer specifically:
- **BOCPD** (Adams & MacKay 2007; financial application: Tsaknaki/Lillo/Mazzarisi 2023, arXiv:2307.02375 — order-flow regime detection on NASDAQ data) maintains a posterior over "run length since last regime change." It gives you a calibrated probability rather than a binary signal, which is far more useful for position sizing.
- **HMM (2–4 state)** on overnight log-return / overnight-volume z-score has been studied since Hamilton (1989) — works as a sanity check.
- **HSMM (Hidden Semi-Markov)** improves on HMM by allowing non-geometric state dwell times — more realistic for "trending" vs "chop" regimes that don't decay exponentially.
- **Self-organizing maps (Kohonen)** for clustering market states into a 2D regime atlas — old-school but still useful for *interpretability*.
- **Echo State Networks / Reservoir Computing** (Jaeger 2001; financial reviews: arXiv:2211.00363, arXiv:2509.04422) train only the readout layer, making them dramatically faster than backprop-through-time RNNs and, critically, **online-trainable** — they fit naturally with your replay-engine + live-streaming dual-mode architecture.

### 3. Hierarchical Temporal Memory (HTM)

Numenta's HTM (Hawkins 2004; Cui et al. 2016, arXiv:1607.02480) is *built* for streaming, non-stationary anomaly detection. The honest summary:
- HTM is an **excellent anomaly detector** on univariate streams — it competes with state-of-the-art on the Numenta Anomaly Benchmark and learns continuously without train/test cycles.
- HTM is a **mediocre point forecaster**. Most published successes (e.g., Ribeiro et al. 2021, MDPI Electronics) are on stock-index trend prediction, not directional alpha.
- Multivariate HTM is an open research area (see the 2025 arXiv:2504.18599 hybrid HTM+SPRT paper, Grid HTM arXiv:2205.15407).

**Right way to use HTM in MOMENTUM-X:** Run an HTM column per ticker on the 1-min volume + return stream as a **non-stationary anomaly siren**, parallel to your main model. When HTM anomaly score spikes, that's a "this stock is doing something it has never done before" signal — useful as a *gate* on your existing strategy, not a replacement for it.

### 4. Frontier ideas (speculation explicitly invited)

These are research bets, not engineering recipes.

- **Diffusion planners for trade decision generation.** Janner et al.'s Diffuser, Black et al.'s DDPO (arXiv:2305.13301), and concurrent work (DARL: arXiv:2510.07099 for portfolio under stress) frame trade-plan generation as *iterative denoising from noise to a coherent state-action trajectory*. Conceptually beautiful for momentum trading because the action space is discrete-ish ("enter, scale, exit, stop") and the optimal trajectory is highly path-dependent. **Practical caveat:** every published diffusion-RL trading paper trains on synthetic or augmented data because the real-data sample efficiency is brutal. Worth experimenting with on your 600-day replay corpus, but expect 6–12 months of work to get past baseline DQN/PPO.

- **Iterative refinement / wave-based learning.** Multi-pass inference where the same network re-reads its own output is a 2024–2026 trend (Chain-of-Thought, Self-Refine, Universal Transformer style). For trading, you'd run the multi-scale stack 3–5 times, each pass conditioned on the previous output. Plausibly improves calibration; almost certainly improves robustness on regime breaks. No known trading paper yet — open territory.

- **Mycelial / mesh-network agent architectures.** This is a metaphor more than a model. The closest published analog is multi-agent reinforcement learning with implicit coordination (arXiv:2601.00324 "Multiagent RL for Liquidity Games"). The case for it: a microcap gap is a *coordination problem* — many small bets across many tickers, each agent specializing in a sub-regime. Genetic populations of small strategies that share a "gene pool" of features and parameters is the most concrete instantiation; your replay engine is well-suited to it.

- **Stigmergic algorithms with pheromone trails.** Real history here: Dorigo's Ant Colony Optimization (1992) was applied to portfolio selection in the 2000s (Engelbrecht and others — see the Springer 2004 chapter "Stocks' Trading System Based on PSO"). The interesting modern angle: write "pheromone" features onto your features warehouse keyed by `(ticker, regime, setup_pattern)` whenever a strategy succeeds; future strategies use that pheromone as a prior. Fundamentally a Bayesian belief-state pooled across strategies — a tractable engineering target.

- **Genetic strategy populations.** Well-trodden (Allen & Karjalainen 1999 onward). The honest take: GAs *find* alpha but rarely *generalize* it; they overfit ferociously on small samples. With 600 days × full universe you have enough data to make this less catastrophic.

- **Active inference (Friston).** The free-energy principle (Parr/Pezzulo/Friston 2022, MIT Press) reframes decision-making as minimizing expected free energy = balancing accuracy and complexity of a generative world-model. For trading, the seductive feature is that *exploration is built in* (epistemic value) — a trader naturally tries new strategies when uncertain. **There is no published quant-trading deployment of active inference that I'm aware of** beyond toy bandit examples. Worth reading; not yet ready to ship.

### 5. What predicts gap continuation vs. fade — empirical guidance

This is where the rubber meets your strategy. From the literature:

- **Lou, Polk, Skouras (2019, JFE)** "A Tug of War" — momentum profits accrue overnight, *not* intraday. Cross-period reversals are robust: a high overnight return tends to be followed by a low intraday return. This is the **single most important academic result for gap-and-go traders** because it argues that the average gap-up *fades*. The strategy edge has to come from selecting the subset of gaps that *don't* fade.
- **Akbas et al. (2022, JFE), Bogousslavsky (2021, JFE)** — the fade is driven by retail-attention-fueled opening pressure and intraday arbitrageur correction. So gap-ups with **abnormal retail attention features** (Stocktwits velocity, Google Trends spike) are *more* likely to fade. This is counterintuitive but strongly empirically supported.
- **Berkman, Koch, Tuttle, Zhang (2012, JFQA)** — "the hidden cost of buying at the open." Same finding from a different angle.
- **Recent (2025) arXiv:2507.04481 "Does Overnight News Explain Overnight Returns?"** — overnight news content explains a substantial fraction of overnight return autocorrelation. **Polygon's news API is exactly the data source needed to operationalize this**.
- **Volume × Float ratio** — folklore but well-supported in microcap practitioner literature (e.g., the cited TraderLion / Highstrike pieces). A gap-up with first-hour volume > 2× full-float is a *much* higher-probability continuation than one with 0.2× float, regardless of catalyst quality.
- **Catalyst type** — FDA approvals, earnings beats with raised guidance, contract awards continue more reliably than secondary offerings, sympathy moves, and "AI hype" mentions.
- **Day of week** (cited Shareplanner / SPY-QQQ analysis) — Monday gaps are most likely to fade; Thursday/Friday gaps continue more reliably. Weak effect on individual microcaps but real on indices.

**Robust lookback windows** (folklore, but supported by my reading):
- Tick layer: 30 sec – 5 min
- Minute layer: 30 min – 4 hours
- Hourly: prior 5 sessions  
- Daily: prior 20 sessions (the standard "RVol" lookback)
- Multi-day: 60–90 sessions (long enough to capture regime, short enough to stay relevant)

Above 90 sessions for microcaps you start including pre-pump/post-pump regimes that aren't comparable.

---

## Caveats

1. **The Polygon → Massive rebrand (Oct 30, 2025).** Polygon.io rebranded to Massive.com. `api.polygon.io` continues to work; new SDK defaults to `api.massive.com`. Functionally identical, but documentation URLs are now a mix of `polygon.io/docs` and `massive.com/docs` — link rot is a real risk on older blog posts.

2. **All concrete file size and throughput numbers above are practitioner-reported, not guaranteed.** Polygon does not publish official size or latency SLAs. Test your specific pipeline before committing infra.

3. **"Unlimited" REST on Stocks Advanced is contractual but practically subject to fair-use.** I am not aware of users being banned for high request volumes, but the documentation does not promise SLAs.

4. **The Insights/sentiment field is LLM-generated and unaudited.** Treat its labels as a noisy prior. Do not deploy a pure-sentiment trading strategy on it.

5. **Microcap gap-up is one of the most adversarial environments in equities.** Pump-and-dump operators specifically design their tape patterns to fool momentum systems. Anything you build needs to be tested against known historical pump-and-dumps (BBBY 2022, AMC 2021, GME 2021, every Chinese microcap IPO 2023–2024) and against known coordinated short-squeeze attacks. Survivorship bias in microcap data is severe — Polygon delisted-ticker metadata is acknowledged as spotty (cited Yolo Trading review).

6. **None of the frontier ideas (diffusion planners, active inference, stigmergic agent meshes) have a published track record on microcap gap-up specifically.** They are research bets. The user explicitly invited speculation; I am explicit about marking it as such.

7. **Latency claims for the WebSocket SIP feed are estimates from typical Polygon community reports, not from a published benchmark.** Run your own latency tests with timestamped echoes before relying on absolute numbers for any latency-sensitive logic.

8. **Mamba/S4 results in finance are early.** CMDMamba, FinMamba, CryptoMamba (all 2024–2025) show promising results on standard benchmarks but are **not yet validated on noisy, sparse-event, microcap-specific data**. Treat them as architectures-to-experiment-with, not architectures-to-deploy.

9. **The Lou/Polk/Skouras overnight-momentum result is on monthly anomaly portfolios across the full equity universe** — its applicability to single-name microcap gap trades is suggestive, not guaranteed. Verify on your own data before basing strategy on it.

10. **Options data is not in your $199 plan.** Anything in §5 of Part A requires an additional subscription. GEX is a large/mid-cap mechanic; don't pay for options data on the GEX argument alone for a microcap strategy.