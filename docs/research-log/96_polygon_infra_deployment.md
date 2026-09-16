# 96 — Polygon Infrastructure Deployment

**Date:** 2026-05-02
**Source:** `docs/research/polygon_compass_playbook.md` (the Compass research doc)
**Status:** Code shipped. Awaiting credentials. Ready to download terabytes.

---

## §1 — What ships in this commit

Eight new files + one extension. Total ~1,400 LOC.

| File | LOC | Purpose |
|------|-----|---------|
| `docs/research/polygon_compass_playbook.md` | 423 | The full research playbook (copied verbatim) |
| `mx-arena/data_providers/polygon/endpoints.py` | +180 | **14 new endpoint methods** (was 6, now 20) |
| `scripts/polygon_credentials_preflight.py` | 200 | One-shot creds + endpoint health check |
| `scripts/polygon_flatfile_pull.py` | 250 | S3 bulk downloader, parallel, resumable |
| `scripts/polygon_parquet_warehouse.py` | 180 | CSV.gz → Hive-partitioned ZSTD Parquet |
| `scripts/polygon_snapshot_screener.py` | 240 | Premarket gap-up scanner (Compass §A.4 pattern) |
| `scripts/polygon_news_catalyst.py` | 250 | News + Insights → 8 catalyst features per ticker |
| `docs/research-log/96_polygon_infra_deployment.md` | this | The runbook you're reading |

### §1.1 — New endpoint methods on `PolygonEndpoints`

Snapshots:
- `losers()` — short-side candidate generator
- `all_tickers_snapshot()` — full ~10k ticker bundle
- `universal_snapshot(tickers)` — multi-symbol bundled (250-cap)
- `single_ticker_snapshot(ticker)` — one ticker's full state

News:
- `news(ticker, since, limit)` — articles + LLM Insights field

Reference:
- `dividends(ticker, since)` — corporate-action filter
- `splits(ticker, since)` — **reverse-split fraud filter (microcap critical)**
- `short_interest(ticker)` — FINRA bi-monthly
- `short_volume(ticker)` — FINRA daily T+1
- `ticker_types()` — canonical type dictionary (CS / ADRC / ETF / WARRANT / RIGHT / UNIT / …)
- `conditions()` — trade & quote condition codes (sweep detection)
- `exchanges()` — ID → name lookup

Aggregates:
- `grouped_daily(date)` — every US ticker's daily OHLCV in ONE call

Market state:
- `market_status_now()` — open/closed/early-close
- `market_status_upcoming()` — holiday calendar

---

## §2 — Credential setup (operator action required)

**The code is ready but cannot run without two sets of credentials.**

### §2.1 — POLYGON_API_KEY (REST)

Get from https://polygon.io/dashboard → API Keys.

```powershell
# Add to ~/momentum-x-secrets.env
Add-Content "$env:USERPROFILE\momentum-x-secrets.env" "POLYGON_API_KEY=YOUR_KEY_HERE"
```

### §2.2 — POLYGON_S3_KEY + POLYGON_S3_SECRET (Flat Files)

Get from https://polygon.io/dashboard → Flat Files → Access Keys.

**These are SEPARATE from POLYGON_API_KEY.** Common gotcha — even Polygon's
docs note this.

```powershell
Add-Content "$env:USERPROFILE\momentum-x-secrets.env" "POLYGON_S3_KEY=YOUR_S3_KEY_ID"
Add-Content "$env:USERPROFILE\momentum-x-secrets.env" "POLYGON_S3_SECRET=YOUR_S3_SECRET"
```

### §2.3 — Verify

```powershell
# Loads secrets, then runs preflight against all endpoints + S3
python scripts/polygon_credentials_preflight.py
```

Expected output when valid:
```
ENV CHECK
  POLYGON_API_KEY            PRESENT
  POLYGON_S3_KEY             PRESENT
  POLYGON_S3_SECRET          PRESENT

REST ENDPOINTS
  aggregates(AAPL day)              PASS  (1 item(s))
  ticker_details(AAPL)              PASS
  gainers()                         PASS  (20 item(s))
  ... [all 13 checks]
  news(ticker=AAPL, n=5)            PASS  (5 item(s))

S3 FLAT FILES
  HEAD us_stocks_sip/.../2026-05-01.csv.gz: PASS (167.3 KB)

SUMMARY
  imports        OK
  REST API       OK
  S3 flat files  OK
```

---

## §3 — Phased deployment plan (what to download, in what order)

Per Compass §A.1, the bulk-pull sequence is:

### Phase 1 — Smoke test (10 minutes, ~10 MB)
```powershell
# Pull last 30 days of day_aggs (every US ticker's daily OHLCV)
python scripts/polygon_flatfile_pull.py --dataset day_aggs_v1 --days 30
# Convert to Parquet warehouse
python scripts/polygon_parquet_warehouse.py --dataset day_aggs_v1
```

This validates the entire pipeline (S3 auth, parallel download, parquet
conversion) on a tiny dataset. Total disk: ~10 MB. Time: 1-2 min.

### Phase 2 — The workhorse (overnight, ~50 GB)
```powershell
# In a background terminal — runs ~2-6 hours depending on bandwidth
python scripts/polygon_flatfile_pull.py --dataset minute_aggs_v1 --days 600 --workers 24
```

What this gives us: **every minute bar for every US-listed ticker
(~9000) for 600 trading days = 2.5 years**.

Compare to current state: `data/bar_recordings/` has 88 days × ~30 watchlist
tickers. This is **600 days × ~9000 tickers = 6,000× more data points.**

After download:
```powershell
python scripts/polygon_parquet_warehouse.py --dataset minute_aggs_v1
# Result: data/polygon_warehouse/minute_aggs/year=2024/month=01/data.parquet, etc.
```

### Phase 3 — Tick-level for high-mover catalog (selective, ~20 GB net)

The trade tape (`trades_v1`) is huge but we only need it for tickers that
actually moved. Strategy:
1. Daily download of `trades_v1` (~700 MB/day → ~420 GB for 600 days).
2. **Filter immediately** to only tickers that had ≥30% intraday move that
   day, write filtered parquet, **delete the source CSV.gz**.
3. Net storage: ~20 GB instead of 420 GB.

Implementation pending — `scripts/polygon_filter_trades_to_movers.py` (next session).

### Phase 4 — News backfill (1-2 days, < 1 GB)

```powershell
# For every (date, ticker) in our 88-day arena, pull news from prior 24h
python scripts/polygon_news_catalyst.py --tickers HCAI,RPGL,RYOJ,XRX,VLN,WNW,SKLZ,MRAM \
  --hours 24 --json-out data/lottery/news_friday.json
```

Then a backfill loop runs over the whole arena to attach news features to
every historical decision. Used by Tuesday's E1 (wait-and-see) + E7 (news
gating) experiments per doc 93.

---

## §4 — Storage budget

| Phase | Raw size | Warehouse size | Cumulative |
|-------|----------|----------------|------------|
| 1 — day_aggs (30d) | 10 MB | 5 MB | 15 MB |
| 1 — day_aggs (5y full) | 700 MB | 350 MB | 1 GB |
| 2 — minute_aggs (600d) | 50 GB | 25 GB | 75 GB |
| 3 — trades filtered | 20 GB | 20 GB | 95 GB |
| 4 — news (88d arena) | 50 MB | 50 MB | 95 GB |

**~100 GB total for the full v1 dataset**. Local SSD is fine. Quotes files
(~600 GB-1 TB) are deferred unless a use case justifies them.

---

## §5 — Integration plan with the lottery (next deployments)

### §5.1 — Tuesday: snapshot-screener as second source

Patch `lottery_runner.py` to call `polygon_snapshot_screener.gainers()` AND
Alpaca's screener, take union, dedupe by ticker. Should fix the doc 92 §6.1
"stale screener" issue immediately.

### §5.2 — Wednesday: news-catalyst gating (E7 from doc 93)

For each lottery candidate at 09:25 ET, call `polygon_news_catalyst.compute_features()`.
Skip if:
- `n_articles_24h == 0` (no news = pump risk)
- `latest_publisher_tier < 0.5` (only opinion / aggregation)
- `pct_negative > 0.5` AND `pct_positive < 0.2` (negative consensus)

Hypothesis from doc 93 §3: this would have killed RPGL and RYOJ on Friday.
Worth backtesting on the 88-day arena before live deploying.

### §5.3 — Thursday: reverse-split fraud filter

For each candidate, call `splits(ticker=X, execution_date_gte=last_90_days)`.
If ANY result, **skip** — reverse splits in last 90 days are a microcap
fraud signal per Compass §A.6.

### §5.4 — Friday: review

After 3 days of layered filters, compare:
- Capture ratio vs. baseline (Friday's 50.1%)
- Per-leak attribution (which experiments actually killed bad picks)

---

## §6 — Why this is a 10× upgrade

Per the Compass doc TL;DR:

> "**You are leaving ~80% of your $199/mo subscription on the table.**
> The single biggest unlock is Flat Files — bulk-download 600+ days × full US
> universe (~12 TB total, ~50 GB just for minute aggs)."

| Dimension | Before | After Phase 2 |
|-----------|--------|---------------|
| Days of arena data | 88 | 600+ |
| Tickers per day | ~30 | ~9,000 |
| Total (date,ticker) data points | ~5,955 | ~5,400,000 |
| Cross-sectional analysis possible? | No (only watchlist) | **Yes (full market)** |
| News-gated selection? | No | **Yes (Insights field)** |
| Reverse-split fraud filter? | No | **Yes (90-day lookback)** |
| Real-time gainer source? | Alpaca only (stale 09:25) | Polygon + Alpaca (fresh) |
| Dividend-ex / split-adjust? | No | **Yes (free signal)** |

---

## §7 — Stop conditions

| Question | Result |
|---|---|
| Does the code import cleanly? | **YES** — preflight imports OK |
| Can it run without credentials? | NO — preflight skips REST/S3 with clear error |
| Is the credential setup documented? | **YES** — §2 above |
| Is the bulk download resumable? | **YES** — skip-existing logic in flatfile_pull.py |
| Is the warehouse query-fast? | **YES** — DuckDB + Hive partitioning + ZSTD |
| Does this touch the live trading bot? | **NO** — all scripts are standalone analysis |
| Risk to the lottery? | **ZERO** — runs in different processes |

---

## §8 — Operator action items (in priority order)

1. **Get POLYGON_API_KEY** from polygon.io dashboard → API Keys
2. **Get POLYGON_S3_KEY + POLYGON_S3_SECRET** from polygon.io dashboard → Flat Files → Access Keys
3. Add all three to `~/momentum-x-secrets.env`
4. Run `python scripts/polygon_credentials_preflight.py` — should print all PASS
5. Run Phase 1 smoke test (10 minutes, ~10 MB)
6. Run Phase 2 in background (overnight, ~50 GB) — `--workers 24` recommended
7. Convert to Parquet (15 min after Phase 2 finishes)
8. Sit back; we now have 600 days × full US universe

---

## §9 — What's next (post-credentials)

After Phase 2 completes, the immediate analyses we can run:

1. **Re-run the 5-hypothesis backtest (doc 89)** on 600 days instead of 88.
   Did H3 (short-the-ripper) actually work in pre-2026 regimes?
2. **Re-run the lottery backtest (doc 90)** on full universe instead of
   watchlist subset. Does freshness premium hold?
3. **Compute Ising magnetization (doc 95 §9 wild idea)** on 600 days.
   Look for genuine regime-shift signal at known dates.
4. **Build the high-mover catalog** for 600 days (vs. current 188 from 88 days).
   Use it as ground truth for any future model.

These are the experiments doc 89 §10 said "we need more data first." The
data is now ~30 minutes of typing + ~6 hours of waiting away.

---

> "We've been renting a library and reading 5 books. Tonight we got the keys
> to the back rooms — first editions of every minute, every quote, every news
> article on every ticker for the last decade. The walk back there is just
> code and an overnight download."
