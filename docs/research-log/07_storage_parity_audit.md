# Storage Parity Audit — D221 v2 feature plumbing

**Date:** 2026-04-18 (Saturday afternoon, weekend deliverable #1)
**Audit script:** `scripts/audit_module_storage.py` (re-runnable)
**Window queried:** 2025-12-11 to 2026-04-14 (matches composite training window)

## TL;DR

**All four modules are LIVE-ONLY.** None of them persist historical data
to disk; all are stateless analyzers or in-memory caches that lose state
on process restart. The Monday "press play" execution plan needs revision
before experiment #1 can run.

| Module | Classification | Backfill tractability |
|---|---|---|
| `short_interest` | LIVE-ONLY | Hard (API quota + FINRA lag) |
| `sentiment_velocity` | LIVE-ONLY | Hard (needs external headline archive) |
| `order_flow` | LIVE-ONLY | Hard (T&S replay at scale = enormous data) |
| `premarket_velocity` | LIVE-ONLY | **Tractable** (replay existing premarket bars) |

## Per-module findings

### `short_interest` — LIVE-ONLY

In-memory TTL cache (4 hours), no disk persistence. Tier cascade (Alpaca
→ yfinance → Finviz → FINRA stub) returns one point-in-time value per
ticker. Cache wiped on process restart.

- Storage state: `src/data/short_interest.py:188` — `self._cache: dict[str, _CacheEntry]`
- No `import json`, no `open()`, no `pickle.dump`, no SQLite anywhere in module

**Backfill path:** Alpaca short-interest API supports historical reads
but with FINRA's structural ~2-week lag. Reconstructing per-(ticker, date)
historical short-interest for ~5,800 rows = ~5,800 API calls plus the lag
adjustment. Tractable but expensive in API quota; would take days at
Alpaca's standard rate limits.

### `sentiment_velocity` — LIVE-ONLY

In-session memory only. Hawkes-process intensity recomputed on each
event registration. Headlines themselves are NOT stored anywhere in the
repo — the module consumes them but does not persist them.

- Storage state: `src/data/sentiment_velocity.py:164` — `self._trackers: dict[str, SentimentVelocityResult]`
- `HeadlineEvent` lives in `result.events` list (line 198), in-memory only

**Backfill path:** Requires an external historical headline archive
(Benzinga, NewsAPI, AlphaVantage News, or commercial). The repo contains
no historical headline data. This is the **least tractable** of the four
because the upstream data source must first be acquired before any backfill
work can begin.

### `order_flow` — LIVE-ONLY

Pure stateless analyzer. `analyze_trades(ticker, trades, bid, ask)` accepts
T&S events and returns an `OrderFlowResult`. No state retained between calls.

- Stateless: `src/data/order_flow.py:222`
- No instance attributes beyond thresholds

**Backfill path:** Requires Alpaca historical T&S API. For ~5,800
ticker-date pairs × thousands of ticks per day per ticker, this is the
largest data-volume backfill — easily tens of GB and weeks of API calls
at standard rate limits. **Not tractable for a weekend.**

### `premarket_velocity` — LIVE-ONLY (but tractable)

In-session memory of `VelocitySnapshot` lists per ticker. `reset()` wipes
at session start.

- Storage state: `src/data/premarket_velocity.py:61`

**Backfill path: TRACTABLE.** Premarket minute bars already exist on
disk at `data/bar_recordings/<date>/<ticker>.json` for the full training
window. We can write a simple replay function that walks each bar file,
emits synthetic `record_snapshot()` calls at 2-minute intervals using bar
close prices and volumes, then reads the resulting velocity/acceleration
metrics. Estimated 4-6 hours of work to write + validate.

The data we need (premarket OHLCV) is on disk; only the snapshot-emission
glue is missing.

## What this means for the Monday plan

The original plan: plumb 4 modules into `compute_features_and_outcomes`,
backfill 81 days, retrain, measure CV AUC delta. **That plan is not
executable** because 3 of 4 modules cannot be backfilled cheaply, and
the 4th (premarket_velocity) needs a half-day of replay code that doesn't
exist yet.

Three alternative paths, ranked by leverage:

### Path A — Premarket-velocity-only experiment (recommended)

Implement the premarket_velocity bar-replay backfill this weekend
(~4-6 hours). Add only premarket_velocity features (~2 numerics + 1
categorical = 4 columns) to `compute_features_and_outcomes`. Backfill
the 5,774 rows. Train v2_pmv. Measure CV AUC delta against v0/v1.

- **Diagnostic value:** Smaller delta possible (only 2-3 features added)
  but real and clean. If even a single module's features move CV AUC
  meaningfully, it justifies investing in the other three's backfill.
- **Time to result:** Monday afternoon.
- **Risk:** Smaller AUC delta = less signal-to-noise, which the bootstrap
  CI methodology (3 seeded runs + 5,000-sample bootstrap) was designed
  to handle.

### Path B — Add forward-only persistence to all 4 modules now

Add JSONL append-mode persistence to `short_interest`, `sentiment_velocity`,
`order_flow`, `premarket_velocity` so they capture data going forward. No
historical backfill. v3 (in ~90 days) trains on the enriched features.

- **Diagnostic value:** None this week. Buys forward optionality.
- **Time to result:** ~3 months until enough new data accumulates.
- **Worth doing in parallel with Path A** because the persistence work is
  small per module and unblocks v3 regardless of v2's outcome.

### Path C — Full historical backfill (rejected)

Acquire the upstream data sources for all four modules and backfill the
full training window. Cost estimate: 2-4 weeks of engineering plus
external data subscriptions plus significant API quota. Not a weekend
project.

## Recommendation

**Path A + Path B in parallel:**

1. **Saturday/Sunday:** Implement premarket_velocity bar-replay backfill.
   Add forward-only JSONL persistence to all four modules (small change
   per module, ~30 min each). Plumb the four modules' features into
   `compute_features_and_outcomes` schema, but populate only
   premarket_velocity for the historical window; the other three columns
   are NaN with `feature_source_status="unavailable_historical"`.

2. **Monday:** Backfill premarket_velocity for 5,774 rows. Train v2_pmv
   with 3 seeded CV runs. Bootstrap AUC delta vs v0/v1. Decision:
   - If premarket_velocity alone moves CV AUC ≥ +0.02: invest in
     short_interest API backfill (the second-cheapest of the remaining
     three) for week 2.
   - If <+0.02: pivot to experiment #2 (phantom journal IPW), which
     doesn't depend on these modules.

3. **Forward:** As live trading accumulates 30+ days of forward data
   for the four persisted modules, v3 becomes possible without the
   backfill problem.

## What changes in the rest of the weekend plan

- **Deliverable #4 (plumbing scaffold)** still ships, but the schema
  extension only emits real values for premarket_velocity; the other 12
  feature columns are NaN-with-status until persistence accumulates.
- **Deliverables #2, #3, #5, #6** unchanged.
- **Add a new deliverable: forward-only persistence layer** for the
  three modules we can't backfill. Small, ~2 hours total, pure
  infrastructure investment for v3.

## Re-running the audit later

When persistence is added and accumulating:

```bash
python scripts/audit_module_storage.py --window 2025-12-11 2026-04-14
```

The script returns exit 0 if any module is READY for the window, exit 1
otherwise. Today's run: exit 1 (no module READY).
