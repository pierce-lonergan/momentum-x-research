# 97 — Polygon Data Organization Spec

**Date:** 2026-05-02
**Status:** Phase 1 LIVE (day_aggs warehoused, sample queries <100 ms).
Phase 2 background-running (minute_aggs 600 days, PID 47728).
**Goal:** lay out a clean, queryable, future-proof data organization for
everything we pull from Polygon over the coming weeks/months.

---

## §1 — Top-level layout

```
data/
├── polygon_flatfiles/              # raw CSV.gz from S3 (immutable; archival)
│   ├── day_aggs_v1/{YYYY}/{date}.csv.gz
│   ├── minute_aggs_v1/{YYYY}/{date}.csv.gz
│   ├── trades_v1/{YYYY}/{date}.csv.gz       # selective (high-mover only)
│   └── quotes_v1/{YYYY}/{date}.csv.gz       # deferred (~600 GB-1 TB)
│
├── polygon_warehouse/              # query layer (Hive-partitioned Parquet, ZSTD)
│   ├── day_aggs/year={YYYY}/data.parquet
│   ├── minute_aggs/year={YYYY}/month={MM}/data.parquet
│   ├── trades/year={YYYY}/month={MM}/day={DD}/ticker={T}/data.parquet
│   ├── reference/snapshot_date={YYYY-MM-DD}/{tickers,types,exchanges,conditions}.parquet
│   ├── corporate_actions/year={YYYY}/{splits,dividends}.parquet
│   ├── short_data/year={YYYY}/month={MM}/{interest,volume}.parquet
│   └── news/year={YYYY}/month={MM}/data.parquet
│
├── polygon_snapshots/              # per-run live screener outputs (small JSONs)
│   └── snapshot_{YYYYMMDD_HHMMSS}.json
│
├── polygon_cache/                  # REST response cache (existing)
│   └── {endpoint_path}/{params_hash}.json
│
└── polygon_backfill/               # LEGACY — pre-warehouse pulls (do not delete; deprecated)
    ├── equity_minute_bars/
    ├── etf_minute_bars/
    ├── fundamentals/
    └── tick_data/
```

### §1.1 — Why three storage tiers

| Tier | Purpose | Format | Size | Refresh |
|------|---------|--------|------|---------|
| **flatfiles** | archival raw | CSV.gz | original | append-only, never modified |
| **warehouse** | query layer | Hive Parquet ZSTD | 50% smaller | rebuilt from flatfiles when needed |
| **cache** | REST hot path | JSON | small | TTL-based, evictable |

The flatfiles are the **source of truth**. The warehouse is rebuildable. If
Polygon corrects a historical day, we re-pull that day's CSV.gz, then rebuild
the affected warehouse partition.

---

## §2 — Hive partitioning rationale

Per Compass §A.1: DuckDB's predicate pushdown sweet spot is row groups
of 100 MB-10 GB. Polars OOMs on monolithic 140 GB files but handles
partitions cleanly.

### §2.1 — day_aggs (small)
```
day_aggs/year={YYYY}/data.parquet
```
- One file per year. ~12k tickers × 252 days = 3M rows. ~50 MB ZSTD.
- Predicate `WHERE year = 2024` narrows to a single file.

### §2.2 — minute_aggs (medium)
```
minute_aggs/year={YYYY}/month={MM}/data.parquet
```
- One file per month. ~12k tickers × 21 days × 390 RTH min = 100M rows. ~1 GB ZSTD.
- Predicate `WHERE year = 2024 AND month = 3` narrows to one file.
- For `WHERE ticker = 'AAPL'` queries, DuckDB scans all months but uses
  zone maps (statistics=true) to skip non-matching row groups.

### §2.3 — trades (large, selective)
```
trades/year={YYYY}/month={MM}/day={DD}/ticker={T}/data.parquet
```
- 4-deep partitioning because tick volumes vary 1000× by ticker.
- We do NOT bulk-store trades for all tickers. Only:
  - Tickers in the high-mover catalog (≥30% intraday move that day)
  - Tickers that were lottery picks (so we can replay slippage)
- Net: ~5 GB instead of ~400 GB.

### §2.4 — quotes (huge, deferred)
- Same shape as trades. **Not pulling until a clear use case justifies it.**
- Polygon estimate: ~600 GB - 1 TB for 600 days of quotes.

### §2.5 — reference (slowly changing)
```
reference/snapshot_date={YYYY-MM-DD}/{tickers,types,exchanges,conditions}.parquet
```
- Pulled weekly. Old snapshots kept for as-of historical queries.
- Each snapshot ~5 MB.

### §2.6 — corporate actions (event-driven)
```
corporate_actions/year={YYYY}/{splits,dividends}.parquet
```
- Pulled daily (incremental). Microcap fraud-filter signal.

### §2.7 — short_data (FINRA)
```
short_data/year={YYYY}/month={MM}/{interest,volume}.parquet
```
- Short interest: bi-monthly schedule (1st bday after 15th + last bday).
- Short volume: daily T+1.

### §2.8 — news (catalyst corpus)
```
news/year={YYYY}/month={MM}/data.parquet
```
- One file per month. Articles + insights + tickers list (exploded).
- Indexed on (ticker, published_utc) for fast catalyst lookup.

---

## §3 — Schema spec (canonical column names)

Every warehouse file must follow these conventions so cross-table joins
work without bespoke renames.

### §3.1 — All time series tables
- `ts_utc` : `Datetime[ns, UTC]` — original Polygon ns timestamp converted
- `ts_et`  : `Datetime[ns, America/New_York]` — derived
- `year`, `month`, `day` : Int — Hive partition columns from `ts_et`

### §3.2 — Aggs (day_aggs, minute_aggs)
- `ticker`       : Utf8
- `open`, `high`, `low`, `close` : Float64
- `volume`       : Float64 (some ETFs report fractional)
- `transactions` : Int64
- `vwap`         : Float64 (only minute_aggs)

### §3.3 — Trades
- `ticker`       : Utf8
- `id`           : Utf8
- `price`        : Float64
- `size`         : Int64
- `conditions`   : List[Int32] — looked up via `reference/conditions`
- `exchange`     : Int32 — looked up via `reference/exchanges`
- `tape`         : Int8 — 1=NYSE, 2=AMEX/ARCA, 3=Nasdaq
- `correction`   : Int8 — non-zero = corrected (exclude from replay)
- `trf_id`       : Int32 — non-null when this is a TRF/dark print
- `participant_timestamp` : Int64 (ns)
- `sip_timestamp`         : Int64 (ns)
- `sequence_number`       : Int64

### §3.4 — Quotes (when we pull)
- `ticker`       : Utf8
- `bid_price`, `ask_price` : Float64
- `bid_size`, `ask_size`   : Int64 — multiply by 100 for shares
- `bid_exchange`, `ask_exchange` : Int32
- `conditions`, `indicators` : List[Int32]
- `tape`         : Int8

### §3.5 — News
- `id`             : Utf8
- `published_utc`  : Datetime[ns, UTC]
- `title`          : Utf8
- `publisher_name` : Utf8
- `publisher_tier` : Float — 0.2 (opinion) → 1.0 (primary release)
- `tickers`        : List[Utf8] — all ticker tags
- `insights`       : List[Struct{ticker, sentiment, sentiment_reasoning}]
- One row per article. For per-ticker queries, explode `tickers`.

### §3.6 — Reference
- Each snapshot is a flat dict-of-arrays parquet. Column names mirror
  Polygon's API response exactly.

### §3.7 — Corporate actions
- splits: `ticker, execution_date, split_from, split_to, ratio`
- dividends: `ticker, ex_dividend_date, declaration_date, record_date,
              pay_date, cash_amount, dividend_type`

---

## §4 — Critical invariants (timestamps, adjustments, corrections)

Per Compass §A.1 (the landmines section):

1. **Timestamps are nanoseconds in UTC.** Conversion at write time:
   ```python
   pl.from_epoch("window_start", time_unit="ns").alias("ts_utc")
     .dt.convert_time_zone("America/New_York").alias("ts_et")
   ```
   Anything that uses naive UTC for session detection is **4-5 hours off**
   and silently wrong.

2. **Flat files are UNADJUSTED.** Splits and reverse splits create artificial
   "gaps" in minute series unless we adjust on the fly using
   `reference/splits`. **Adjust at QUERY TIME, not write time** — so we can
   re-run analyses with different adjustment policies without re-downloading.

3. **Corrections must be filtered for replay fidelity.** A trade with
   `correction != 0` was reissued after-the-fact. For replay (e.g. lottery
   slippage analysis), we EXCLUDE corrections because they weren't visible
   in real time. For accounting / clean OHLCV, we INCLUDE them.

4. **TRF / dark prints are not sequenced with exchange trades.** When
   building aggregates from `trades_v1`, filter `exchange == 4` (FINRA TRF)
   or rely on Polygon's official condition flags (`update_high_low`,
   `update_open_close`, `update_volume` per condition).

5. **Premarket coverage**: minute_aggs include 4:00 AM - 9:30 AM bars only
   if eligible trades occurred. **Absence of a bar ≠ halted.** Cross-check
   with `trades_v1` (or `reference/market-status`) to disambiguate.

6. **Reverse-split contamination is huge in microcaps.** A 10:1 reverse
   split makes the historical minute bars look like the price was 10x
   what it really was. Always apply split adjustment before any
   percentile or threshold gate.

---

## §5 — Common query patterns (DuckDB)

### §5.1 — Single ticker, single day
```sql
SELECT ts_et, open, high, low, close, volume
FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet',
                   hive_partitioning=true)
WHERE ticker = 'AAPL'
  AND year = 2024 AND month = 3
  AND CAST(ts_et AS DATE) = '2024-03-04'
ORDER BY ts_et
-- Expected: <50ms via predicate pushdown.
```

### §5.2 — Universe-wide top movers on a date
```sql
WITH d AS (
  SELECT ticker, MIN(open) AS open, MAX(high) AS high,
         MIN(low) AS low, ARG_MAX(close, ts_et) AS close,
         SUM(volume) AS volume
  FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet',
                    hive_partitioning=true)
  WHERE year = 2024 AND month = 3 AND CAST(ts_et AS DATE) = '2024-03-04'
  GROUP BY ticker
)
SELECT ticker, open, high, close, volume,
       (high - open) / open * 100 AS max_pct_intraday
FROM d
WHERE open BETWEEN 0.5 AND 50 AND volume > 100000
ORDER BY max_pct_intraday DESC
LIMIT 50;
```

### §5.3 — Cross-sectional breadth (regime detection)
```sql
SELECT CAST(ts_et AS DATE) AS d,
       SUM(CASE WHEN (close - open)/open >= 0.30 THEN 1 ELSE 0 END) AS n_huge_up,
       SUM(CASE WHEN (close - open)/open <= -0.30 THEN 1 ELSE 0 END) AS n_huge_down
FROM read_parquet('data/polygon_warehouse/day_aggs/**/*.parquet',
                  hive_partitioning=true)
WHERE volume > 50000
GROUP BY 1 ORDER BY 1;
-- Output is the Ising-magnetization regime detector from doc 95 §9.
```

### §5.4 — Joining with news (catalyst gate)
```sql
WITH movers AS (
  SELECT ticker, CAST(ts_et AS DATE) AS d,
         (close - open) / open AS intraday_pct
  FROM read_parquet('data/polygon_warehouse/day_aggs/**/*.parquet',
                    hive_partitioning=true)
  WHERE volume > 100000 AND (close - open)/open >= 0.30
),
news_count AS (
  SELECT UNNEST(tickers) AS ticker,
         CAST(published_utc AS DATE) AS d,
         COUNT(*) AS n_articles
  FROM read_parquet('data/polygon_warehouse/news/**/*.parquet',
                    hive_partitioning=true)
  GROUP BY 1, 2
)
SELECT m.ticker, m.d, m.intraday_pct, COALESCE(n.n_articles, 0) AS articles
FROM movers m LEFT JOIN news_count n
  ON m.ticker = n.ticker AND m.d = n.d
ORDER BY m.d DESC, m.intraday_pct DESC;
-- Test the doc 93 hypothesis: do "no-news" gappers actually fade?
```

### §5.5 — Joining with splits (fraud filter)
```sql
SELECT m.*, s.execution_date AS reverse_split_date,
       s.split_from || ':' || s.split_to AS ratio
FROM movers m
LEFT JOIN read_parquet('data/polygon_warehouse/corporate_actions/**/splits.parquet',
                        hive_partitioning=true) s
  ON m.ticker = s.ticker
 AND s.execution_date BETWEEN m.d - INTERVAL 90 DAY AND m.d
 AND s.split_from > s.split_to  -- reverse split only
;
-- Microcap fraud filter from Compass §A.6: reverse-split-90d AND gap-up = fade probability.
```

---

## §6 — Manifest + incremental updates

### §6.1 — Manifest file
```
data/polygon_warehouse/_manifest.json
```
Tracks what's loaded:
```json
{
  "day_aggs": {
    "first_date": "2024-01-02",
    "last_date": "2026-05-01",
    "n_files": 1,
    "rows": 345286,
    "size_mb": 8.1
  },
  "minute_aggs": {
    "first_date": "2024-01-02",
    "last_date": "2026-05-01",
    "n_files": 29,
    "rows": null,
    "size_mb": null
  },
  ...
}
```

The Phase 2 conversion will populate this automatically (TODO).

### §6.2 — Daily incremental cron (12:30 ET, post-Polygon-publish)
```powershell
# At 12:30 ET each weekday, pull yesterday's flat files
python scripts/polygon_flatfile_pull.py --dataset day_aggs_v1 --days 2
python scripts/polygon_flatfile_pull.py --dataset minute_aggs_v1 --days 2
# Convert any new partitions
python scripts/polygon_parquet_warehouse.py --dataset day_aggs_v1
python scripts/polygon_parquet_warehouse.py --dataset minute_aggs_v1
# Update manifest
python scripts/polygon_warehouse_manifest.py  # TODO: write this
```

Schedule via Windows Task Scheduler at 12:30 PM ET weekdays. Non-elevated
(per the existing MomentumX-Lottery pattern).

---

## §7 — Backfill priorities (now that Phase 2 is running)

| Phase | Dataset | Days | Size | Status |
|-------|---------|------|------|--------|
| **1** | day_aggs (30d sample) | 30 | 9 MB | ✓ DONE |
| **1+** | day_aggs (5y full) | 1300 | ~400 MB | TODO |
| **2** | minute_aggs | 600 | ~40 GB | RUNNING (PID 47728) |
| 3 | trades (high-mover filtered) | 600 | ~5 GB net | after Phase 2 |
| 4a | reference (weekly snapshots) | now+ | ~5 MB/wk | after Phase 2 |
| 4b | corporate_actions (5y) | 1300 | ~50 MB | after Phase 2 |
| 4c | short_data (FINRA) | 600 | ~100 MB | after Phase 2 |
| 5 | news (90d backfill) | 90 | ~500 MB | after Phase 2 |
| ∞ | quotes (deferred) | — | ~1 TB | only when use case justifies |

After Phase 2 completes (~3-6 hours), the warehouse holds **600 days × full
US universe at minute granularity**. From there:

1. Run the doc 89 5-hypothesis backtest on the 600-day data set — H3 was
   regime-dependent on 88 days; we now have 7× more data to retest.
2. Run the doc 90 lottery backtest on full universe instead of watchlist —
   does freshness premium hold cross-sectionally?
3. Compute the Ising-magnetization detector (doc 95 §9 wild idea #9) —
   2 hours of code over 600 days of breadth.
4. Build the high-mover catalog over 600 days for Phase 3 trades pull.

---

## §8 — Migration: legacy `data/polygon_backfill/`

The pre-warehouse pulls in `data/polygon_backfill/` (1.4 GB) are KEPT but
will not be used by new code. They're already partitioned per (corpus,
ticker) and were used by the doc 88-90 analyses. Leaving them in place
means the historical analyses are reproducible.

**Do not delete** until at least:
- The doc 88-90 analyses have been re-run on the new warehouse
- Outputs match within rounding tolerance
- Then archive to `data/_archive/polygon_backfill_pre_warehouse/`

---

## §9 — Cost monitoring

Polygon Stocks Advanced is $199/mo flat. Storage is local SSD.

| Dataset | Disk usage projection |
|---------|----------------------|
| day_aggs (5y) | ~400 MB |
| minute_aggs (600d) | ~40 GB raw + ~25 GB Parquet |
| trades (filtered) | ~10 GB |
| news (5y) | ~5 GB |
| **Subtotal** | **~80 GB** |
| If we add quotes someday | +600 GB-1 TB |

Current SSD usage: well under 100 GB allocated. Safe.

---

## §10 — Stop conditions

| Question | Result |
|----------|--------|
| Does the warehouse work? | **YES** — 345k rows, queries <100ms |
| Is the layout future-proof? | YES — Hive partitioning + ZSTD survives 10x growth |
| Is the schema documented? | YES — §3 |
| Are the timestamps right? | YES — UTC ns → ET conversion at write time |
| Is incremental update planned? | YES — §6 (cron at 12:30 ET) |
| Is Phase 2 in flight? | YES — PID 47728 |
| Have we touched the live trading bot? | NO — all standalone analysis |

---

## §11 — Bring-up checklist for next session

After Phase 2 finishes:

1. `Get-Process -Id 47728` → verify finished cleanly
2. `Get-Content logs/phase2_minute_aggs_pull.log -Tail 20` → confirm `DONE`
3. `python scripts/polygon_parquet_warehouse.py --dataset minute_aggs_v1`
4. Run sample minute-bar query (modified `polygon_warehouse_query_demo.py`
   targeting minute_aggs)
5. Re-run doc 89 5-hypothesis backtest with `--data-source warehouse`
6. Re-run doc 90 lottery backtest with `--universe full` (vs current
   `--universe watchlist`)
7. Build high-mover catalog over 600 days
8. Schedule Phase 3 (filtered trades) + Phase 4 (news)

---

> "Friday had 14 stocks ≥+30% intraday across the full universe. We caught 1.
> The other 13 were invisible to our 8-pick lottery. Phase 2 finishes the job
> of making them VISIBLE — what we do with that visibility is doc 98."
