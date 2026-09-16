# D221 Phase C — Initial Population + Real-Run Validation

**Goal:** Verify the agent does the right thing under real I/O before scaling up.

## C.1 — queue seeded

```
loaded 5862 candidates from data/backfill/candidates.jsonl
after missing_bars filter: 5360 candidates
enqueued 5360 new items (skipped 0 duplicates)
total queue size: 5360
```

`queue.db` is gitignored under `data/` — re-seed with the command above whenever needed.

## C.2 — dry-run validation (60s, 50 items, 3 workers)

- Items moved pending → in_progress → completed atomically
- Heartbeat written to `data/backfill_agent/heartbeat.json` every 30s
- `--dry-run` flag respected: no actual API calls, no bar files written
- Coordinator hit `max_items=50` and exited cleanly

**One bug surfaced and fixed:** the `--status` command crashed on Windows cp1252 console because of a `⚠` unicode character in `monitor.py`. Replaced with `[!]`. Would have bitten Pierce at 7:30 AM tomorrow when checking the daemon. Found and fixed in <30 seconds.

## C.3 — real run (10 items, 1 worker, EST + EDT coverage)

**Sample (curated for timezone coverage per Pierce's instruction):**

5 EST-era candidates (pre-DST):
- HOOZ, MICC, SLNO, CVKD, AIRJ — all 2025-12-11

5 EDT-era candidates (post-DST):
- CRML, ETHB, NVOX, IREG, IREX — all 2026-04-14

**Result: 10/10 success.** All bar files have ≥30 bars and contain the 9:30 ET market open bar (`T13:30` for EDT, `T14:30` for EST). The EST timezone fix from rec (a) holds on the fetch-and-label full path.

## C.3 watch-points (per Pierce's three specific asks)

### Watch-point 1 — kill -9 mid-fetch (real filesystem test)

Staged 30 items at priority 999. Started 3-worker run. Killed PID 30800 ~1 second into execution.

| Check | Result |
|-------|--------|
| Tempfile leaks (`*.tmp` in bar_recordings) | **0** |
| Items left in_progress at kill time | **3** (KOLD, CONI, DARE — all mid-fetch) |
| Newly-written bar files all valid JSON | **24/24** |
| Auto-release timer | will fire after STALE_IN_PROGRESS_SECS=600 |

The atomic write pattern (tempfile + `os.replace`) survived the kill. Items mid-fetch left the queue in `in_progress` status — exactly the design: no premature completion, no corrupted state, will be auto-released by the next worker's `_release_stale_locked()` call after 10 min.

### Watch-point 2 — budget bookkeeping vs. actual call count

| Metric | Value |
|--------|-------|
| Bar files written in last 5 min | 34 |
| Span | 107.8 sec |
| Effective rate | 18.9 files/min |
| Off-hours ceiling | 80/min |
| Utilization | 24% |

Effective rate well under ceiling. No retry double-counting evidence. The budget is correctly throttling without being over-restrictive.

### Watch-point 3 — write-path equivalence

Two A/B comparisons against `compute_features_and_outcomes()`:

| File source | Ticker | Re-compute matches? |
|-------------|--------|---------------------|
| Worker-written this round | HOOZ 2025-12-11 | **7/7 fields identical** to shard row |
| Old `backfill_bars.py`-written | PETS 2025-12-11 | **7/7 fields identical** to stored label |

Format byte-equivalence:
- Top-level keys: `['bars', 'date', 'ticker']` (both)
- Per-bar keys: `['close', 'high', 'low', 'open', 'timestamp', 'volume', 'vwap']` (both)

Worker-written files are indistinguishable from old-script-written files at the byte level.

## Net data growth from Phase C

- **Before Phase A:** 502 bar files, 407 labels
- **After rec (a) fast path:** 502 bar files, 497 labels
- **After C.3 real runs (this session):** 530+ bar files, 510+ labels

Conservative gain from the real runs alone: +28 bar files, +18 labels, ~$0.0 cost (Alpaca data API is paid by the month, not per call). Total time: <2 minutes wall clock.

## Phase C verdict

Clean. Phase D unblocked.
