# D221 Phase A — Backfill Investigation Report

**Date:** April 17, 2026 (after market close)
**Status:** Investigation complete, ready for Phase B implementation
**Output artifact:** `data/backfill_gap_audit.csv`

## A.1 — Existing backfill code map

| File | Role | Notes |
|------|------|-------|
| `scripts/backfill_harvester.py` | Discovers candidates from Alpaca daily bars | Sequential, in-memory; produces `candidates.jsonl` |
| `scripts/backfill_bars.py` | Downloads 1-minute bars for candidates | 4 calls/sec, sorts by gap_pct, **writes file with `"w"` mode (NOT atomic)**, file-presence check for resume |
| `scripts/backfill_features.py` | Computes features + multi-horizon labels | Pure function `compute_features_and_outcomes()`, **rewrites entire `features_labeled.jsonl` on each run** |
| `scripts/backfill_arena_convert.py` | Converts to arena scenarios | Downstream of features |
| `scripts/backfill_gate_replay.py` | Counterfactual sweep | Downstream |
| `scripts/backfill_analytics.py` | Summary analytics | Downstream |

**Key gaps for the agent:**
1. **No persistent work queue** — file-presence is the only resume mechanism. Crashes mid-write leave corrupted JSON.
2. **No atomic writes** — `backfill_bars.py:111` opens `"w"` mode directly. Worker crash mid-write corrupts the file silently.
3. **Rewrite-from-scratch in features.py** — `"w"` mode at line 173. Cannot run incrementally; cannot run while a daemon is updating bars without race.
4. **No production rate awareness** — sleeps 0.25s blindly. Doesn't know whether pre-market scan is active.
5. **No daemon mode, no signal handling, no heartbeat.**

## A.2 — Per-day gap audit

CSV at `data/backfill_gap_audit.csv` (81 days, one row per).

| Aggregate | Count | % of total |
|-----------|-------|-----------:|
| Total candidates | **5,862** | 100% |
| With bars on disk | **502** | 8.6% |
| With labels in features_labeled.jsonl | **407** | 6.9% |
| **Missing bars (the primary gap)** | **5,360** | 91.4% |

**Top gap days** (the originally-skipped tail of the gap_pct sort):

| Date | Candidates | With bars | Gap |
|------|-----------:|----------:|----:|
| 2026-04-08 | 968 | 16 | 952 |
| 2026-02-06 | 246 | 13 | 233 |
| 2026-02-03 | 168 | 8 | 160 |
| 2026-03-25 | 155 | 5 | 150 |
| 2025-12-18 | 149 | 9 | 140 |

April 8 is an extreme outlier — 968 candidates in one day, only 16 with bars (1.6%). Likely a high-VIX session that produced many gap-ups.

## A.3 — Production rate-limit usage + headroom

Production uses `src/utils/rate_limiter.py:TokenBucketRateLimiter` with two pools:

| Limiter | tokens/min | max_burst | Used by |
|---------|-----------:|----------:|---------|
| `trading_rate_limiter` | 180 (3/sec) | 10 | Order submission (Alpaca trading endpoint) |
| `market_data_rate_limiter` | 9,000 (150/sec) | 50 | Bars, quotes, snapshots |

**But the actual Alpaca server cap is much lower than 9,000.** Free tier = 200 req/min on the data endpoint. Even Algo plan tops out at ~10,000/min. The internal 9,000 figure is the in-process budget, not the server limit.

**Empirical evidence from D219:** `backfill_bars.py` ran at 4 calls/sec = 240/min and CRASHED with a network error mid-run (per `docs/d219_backfill_findings.md`). 240/min is over the 200/min free-tier server cap.

**Conservative budget for the agent (proposed):**

| Time window (ET) | Backfill rate | Production usage | Notes |
|-------------------|---------------|------------------|-------|
| 04:25–09:35 | **PAUSED** | High (pre-market scan + watchlist refreshes) | Hard pause — don't compete with prod |
| 09:30–16:00 | 20/min | Moderate (intraday tracking, fills) | Backfill at ~10% of cap |
| 16:00–04:25 | 80/min | Near-zero | Safe headroom; under 200 server cap |
| Saturday/Sunday | 150/min | Zero | Almost full speed |

**ETA at proposed rates:**
- Off-hours (~12 hrs/day) at 80/min = 57,600/day capacity → 5,360 needed = ~1.1 days at 80/min
- Plus weekend bursts: 5,360 / 150 = ~36 minutes if scheduled for a weekend window
- **Realistic full-gap-fill: 1-2 weekday nights, OR a single Saturday morning**

## A.4 — Completeness definition + count

A row is COMPLETE when all three present:
- (a) candidate metadata in `candidates.jsonl` ✓
- (b) bar file at `data/bar_recordings/<date>/<ticker>.json` with ≥30 bars covering 9:30 ET market open
- (c) labeled outcome in `features_labeled.jsonl` with all horizons (t1, t5, t15, t30, t60, t120, close, mfe_pct, mae_pct, time_to_mfe_min)

**Audit results:**

| Bucket | Count | Action needed |
|--------|------:|---------------|
| No bar file | 5,360 | Fetch bars + label (1 API call + cheap compute) |
| Bars present but <30 bars | 1 | Re-fetch (likely truncated download) |
| Bars present but no 9:30-10:00 window | 4 | Re-fetch (low-activity tickers / partial coverage) |
| Bars valid but no label row | 95 | Label only (no API needed) |
| Bars valid but label missing horizons | 5 | Re-label (no API) |
| **COMPLETE** | **402** | none |

**Total work items: 5,465**
- 5,365 require API calls (bar fetches)
- 100 require local compute only (labeling)

## A.5 — Pipeline idempotency check

**`compute_features_and_outcomes(candidate, bars)`:** pure function, deterministic, no I/O, no mutation. Same inputs → identical outputs every run. **Row-level idempotent: YES.**

**`backfill_features.py main()`:** opens `features_labeled.jsonl` with `"w"` mode (line 173) and writes ALL rows from scratch. **NOT incrementally idempotent — rerunning replaces the file entirely.**

**Implication for agent design:**
- The agent must call `compute_features_and_outcomes()` per row (the safe, idempotent unit)
- The agent must use append-mode with file lock to write to `features_labeled.jsonl`
- The agent must maintain a "seen (date, ticker)" set in its work queue to skip already-labeled rows
- DO NOT use `backfill_features.py main()` as the labeler — it would overwrite live data

**No code changes to `backfill_features.py` needed** — the function is already correct. The agent imports the pure function and handles persistence itself.

## A.6 — Phase A summary

| Question | Answer |
|----------|--------|
| Total rows missing bars (N) | **5,360** |
| Total rows with bars but unlabeled (M) | **100** |
| Estimated API calls for full backfill (K) | **~5,365** (one bar fetch per row) |
| Headroom available off-hours | **80 calls/min safe** (under server cap, plenty of margin for prod) |
| Headroom on weekends | **150 calls/min safe** |
| Estimated wall clock at safe rate | **~1.1 days off-hours** OR **~36 min on a Saturday morning** |
| Pipeline idempotency | **Row-level YES, script-level NO** — agent must use the function, not the script |

**No surprises — gap matches the D219 backfill findings doc exactly. Ready to begin Phase B.**

## Open questions deferred to Phase B

1. **Rate-limit error from Alpaca returns 429.** Is there a `Retry-After` header? If yes, honor it; if no, exponential backoff. (Phase B.4 worker.py)
2. **Concurrent write to features_labeled.jsonl.** SQLite for the queue + a separate filelock for the JSONL append. Consider writing labels to a per-day shard (`features_labeled_2026-04-17.jsonl`) and a lazy aggregator — avoids the single-file contention entirely. (Phase B.4)
3. **Forward-mode candidate generation.** The agent's `--mode forward` extends "today, yesterday, ..." but who runs the harvester to discover new candidates? Production already runs daily scans — the agent can read its journal output as the source of new dates. (Phase B.7 cli.py)
4. **Storage growth.** 5,860 candidates × ~50KB per bar file ≈ 300 MB. Already on disk for ~500 of them. Full backfill = ~280 MB additional. Manageable.
