# 116 — Parallelized news backfill + v4 retrain-and-compare pipeline

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [115 --include-news + simplified VETOED + monitor anomaly](115_news_flag_simplified_vetoed_anomaly_monitor.md)

---

## TL;DR

Three production wins while overnight backfills run:

1. **News backfill v2: 2.4× faster + checkpointing.** Killed the stuck
   serial job (frozen at 377/4,080 for ~hour). Rebuilt with
   `concurrent.futures.ThreadPoolExecutor(workers=12)`, explicit 8-second
   per-request timeout (kills hung connections), and 250-row checkpointing
   for resume-from-failure. New rate: **1.7 keys/s** (vs old 0.7/s).
   ETA: ~40 min for 4,080 keys.

2. **`retrain_v4_and_compare.py` ships** (~200 LOC). One command after
   Monday's deploy:
   - Verifies microstructure + news data presence
   - Retrains v4 = v3-tuned-16fold + microstructure + news features
   - Re-runs meta-scorer (conservative + aggressive Kelly)
   - Compares per-tier $-PNL vs v3 baseline
   - Reports verdict: **SHIP v4** / **HOLD v3** based on:
     - +$500 lift threshold
     - No tier regression > 30%

3. **trades_v1 conversion of 12 newly-downloaded days** kicked off
   (Jan 26 - Feb 10). Combined with s113's 9 April days = 21 days of
   ZSTD parquet ready for microstructure feature extraction.

---

## 1. News backfill v2

### What was broken
Session 115's serial backfill stuck at 377/4,080. Root cause: hung HTTP
request without explicit timeout (httpx default is generous; on a slow/
broken connection it can hang indefinitely). No checkpointing meant any
restart would re-fetch from the beginning.

### Fixes

```python
# Per-request timeout (kills hung connections)
def fetch_polygon_news(client, ticker, ..., request_timeout: float = 8.0):
    r = client.get(POLYGON_NEWS_URL, ..., timeout=request_timeout)

# Parallel workers
with cf.ThreadPoolExecutor(max_workers=12) as ex:
    futures = [ex.submit(fetch_one, row) for row in keys_df.itertuples()]
    for fut in cf.as_completed(futures):
        agg = fut.result(timeout=30)
        ...

# Checkpointing every N rows (resume-from-failure)
if (i + 1) - last_checkpoint >= args.checkpoint_every:
    pd.DataFrame(rows).to_parquet(out_path, compression="zstd")
    last_checkpoint = i + 1

# Resume logic at startup
if out_path.exists():
    existing = pd.read_parquet(out_path)
    done_keys = {(r["ticker"], pd.Timestamp(r["d0"])) for r in rows}
    keys_df = keys_df[~keys_df.apply(lambda r: ... in done_keys, axis=1)]
```

### Smoke test (100 keys)

```
$ python scripts/build_news_features_polygon.py --days-back 30 --limit-tickers 100 \
    --workers 8 --request-timeout 8.0 --checkpoint-every 50

[ 57/100] (total 57)  with_news=3 rate=1.4/s eta=31s
[ 69/100] (total 69)  with_news=3 rate=1.4/s eta=22s
[ 85/100] (total 85)  with_news=4 rate=1.5/s eta=10s
[100/100] (total 100) with_news=5 rate=1.7/s eta=0s
[checkpoint] wrote 100 rows -> news_features_polygon_test.parquet
```

**1.7 keys/s = 2.4× the serial 0.7/s.** 4,080 keys → ~40 min total.

### Production run (kicked off)
```
python scripts/build_news_features_polygon.py \
    --days-back 180 --workers 12 --request-timeout 8.0 \
    --checkpoint-every 250 \
    --out news_features_polygon_180d.parquet
```

Currently at 250 rows (one checkpoint) since restart.

---

## 2. v4 retrain-and-compare pipeline

`scripts/retrain_v4_and_compare.py` (NEW, ~200 LOC).

### Five-step pipeline

```
STEP 1 — Verify data sources
  - microstructure_features.parquet (currently 191 rows from s113b)
  - news_features_polygon_180d.parquet (currently 250 rows, growing)
  - intraday_paths_30min.parquet

STEP 2 — Retrain v4 (or --skip-train to reuse existing predictions)
  python ml_continuer_v2_ensemble.py
    --include-paths --include-microstructure --include-news
    --news-path .../news_features_polygon_180d.parquet
    --optuna-params data/models/v3_optuna_full16fold.json
    --out-suffix _v4

STEP 3 — Re-run meta-scorer (both Kelly profiles)
  python ml_meta_scorer.py --v3t-preds <v4 preds> --out-suffix _v4
  python ml_meta_scorer.py --v3t-preds <v4 preds> --out-suffix _v4_aggressive
  (with MX_META_KELLY_PROFILE=aggressive)

STEP 4 — Per-tier compare vs v3-tuned-16fold baseline

STEP 5 — Verdict
  v4 SHIPS IF:
    - aggressive Kelly $-PNL improves by ≥ +$500 vs v3-tuned-16fold
    - AND no tier loses > 30% of its baseline
  Else: HOLD v3
```

### Failure modes handled

- Missing microstructure OR news → use whichever is present (graceful
  degradation; reports lift from just one)
- v4 retrain fails → exit 1 cleanly with rc reported
- Both summaries missing → error before comparison
- Tier regression detection prevents "shifted P&L" silent failures

### Test (with current sparse data: 191 micro + 250 news)
Script correctly fails at STEP 2 with `--skip-train` (no v4 predictions
parquet yet). Will run end-to-end once both backfills complete and the
user invokes without `--skip-train`.

---

## 3. trades_v1 conversion (Jan 26 - Feb 10)

12 newly-downloaded days converting in background:
- Source: Jan 26-30 (5 days, ~14 GB) + Feb 2-10 (7 days, ~20 GB)
- Target: Hive-partitioned ZSTD parquet (~15 GB after compression)
- ETA: ~30-45 min sequential

After completion, microstructure features can be rebuilt for the full
21-day window (Jan 26 - Feb 10 + April 15-29) → ~500 (ticker, d0) rows
from aftermath_strat ≈ 2.5% coverage. Still small, but enough to
demonstrate v4 lift if microstructure features have any signal.

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/build_news_features_polygon.py` | modified (parallel + checkpoints + timeout) | +60 |
| `scripts/retrain_v4_and_compare.py` | NEW | ~200 |
| `docs/research-log/116_news_parallel_v4_compare_pipeline.md` | NEW (this doc) | this |

Total: 1 new + 1 modified + doc, +260 LOC.

---

## 5. Background jobs status (commit time)

| Job | Status | ETA |
|---|---|---|
| trades_v1 80-day download | 75 GB / 240 GB (~31%) | ~5 more hr |
| News v2 backfill (12 workers) | 250 / 4,080 (early) | ~40 min |
| trades_v1 Jan-Feb convert | running | ~30-45 min |

All checkpointing or atomic — safe to interrupt and resume.

---

## 6. Edge stack (post-116, unchanged)

```
Meta-scorer 16-fold AGGRESSIVE       +122.83% bankroll WF (~92% APY)
  Sharpe 3.15, Calmar 31.24, max DD -2.81%
  TCN signal DEBUNKED (s114), VETOED-D candidate (s115): +13.37% per-trade
                                                          on n=67 (vs +9.07%)

Monday deploy ready (rule E in production).
v4 retrain-and-compare pipeline: ready to fire post-deploy
News backfill: 6% complete, ETA 40 min
trades_v1 backfill: 31% complete, ETA 5+ hr
```

---

## 7. Next-session priorities

1. **Monday: paper-deploy + monitor** with `--eod` anomaly detection.
2. **Run `retrain_v4_and_compare.py`** when both backfills complete +
   trades_v1 conversion done. Goal: empirically validate v4 lift.
3. **POST-Monday: strip TCN + swap VETOED to rule D** (per s115 doc).
4. **Install drift cron Task Scheduler** (one-shot).
