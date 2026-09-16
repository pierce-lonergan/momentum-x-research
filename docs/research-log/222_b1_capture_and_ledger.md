# 222 — B1: raw-fill capture + the event-sourced ledger (Phase 1-3 built, on evidence)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "wire the raw-payload capture now, then build the Phase 1/2 schema +
ledger on confirmed evidence."

The disciplined version: capture ground truth (Monday), AND build the schema/ledger to be
robust to either outcome of the one unverified assumption — so we're not blocked on the
capture, but we're also not betting the dedup key on faith.

---

## 1. Raw-fill capture (Phase-1 task#0 — gathers the proof while we sleep)

`src/data/websocket_client.py` (the `_dispatch_trade_event` handler, the single point every
`trade_updates` msg flows through) now tees the **raw payload verbatim** BEFORE parsing →
`data/ops/raw_fills_<date>.jsonl` via `src/ops/fill_capture.py`. Never-raises, gated by
`OPS_RAW_FILL_CAPTURE` (default on — it's a zero-risk tee, no behavior change). Monday's live
session hands us the ground truth of the per-fill id key (`data.execution_id`? something
else?) + a replay corpus for the Phase-6 6/1 replay and the ledger ingest.

## 2. The dedup key (the Phase-0 gap, now fixed — robust to either outcome)

`parse_trade_update` historically read only `data.order` and **dropped `data.execution_id`**
(doc 221). Now it extracts it into `TradeUpdateEvent.execution_id`, and computes:

```
broker_event_id = "exec:<execution_id>"           if Alpaca provides it
                = "hash:<sha1(event|order_id|filled_qty|filled_price|ts)[:20]>"   else
```

So the ledger can dedup **regardless** of whether the real payload carries `execution_id` —
and the raw capture *confirms* which path is live. The fallback hash is **deterministic**
(never random/uuid) so the same fill delivered twice (stream reconnect) maps to the same id —
replay + idempotency depend on that.

## 3. The event-sourced ledger (`src/ops/ledger.py`)

### Phase 1 — schema (tight, append-only, idempotent)
`ORDER_SUBMITTED` / `ORDER_ACKED` / `ORDER_REJECTED` / `ORDER_CANCELLED` (**never book**),
`FILL_PARTIAL` / `FILL_COMPLETE` (**book the slice; REQUIRE a broker_event_id**),
`RECON_DELTA` (drift; never books). **The structural rule, enforced on `append()`: a FILL
without a `broker_event_id` is REJECTED.** No fill books P&L without broker confirmation —
that single invariant is most of B1.

### Phase 2 — durable append-only SQLite
WAL + `synchronous=FULL` + `wal_checkpoint(FULL)` on every fill-class event (survives
`kill -9` — gate 4). `broker_event_id` is a UNIQUE column → a duplicate fill is an idempotent
no-op (stream-reconnect safe — gate 2).

### Phase 3 — position & realized P&L as a PURE FOLD
`position(ticker) = fold(fill_events_for(ticker))`, `realized_pnl(ticker) = Σ slice_pnl`.
No caches that can diverge from the log; a fresh `Ledger` on the same db computes the same
answer (gate 1, purity). Avg-cost entry on buys; realize-against-avg on sells.

## 4. Proof: the 6/1 phantom is now structurally impossible

Live demo (in the commit) + a pinned test: the STG 6/1 sequence —
`ORDER_SUBMITTED → ORDER_REJECTED` (close 403'd, never filled) — books **$0.00**. The old
optimistic path (`close_with_attribution` from a passed-in exit_price) booked **+$304.20**. A
real `FILL_COMPLETE` (buy@10 → sell@11) books **+$100** correctly. **With no FILL event there
is no P&L — by construction, not by detection.**

## 5. Gauntlet status (Pierce's 8 gates)
| Gate | Status |
|---|---|
| 1 fold purity | ✅ `test_fold_is_pure_reread` |
| 2 dedup (replay = no-op) | ✅ `test_duplicate_fill_is_idempotent` |
| 3 ∀ realized_pnl ∃ FILL w/ broker_event_id | ✅ structural rule enforced + tested |
| 4 crash test (kill -9) | ⏳ Phase-6 (WAL+FULL+checkpoint in place; needs the kill-test harness) |
| 5 Adversary (5 B1 scenarios) | ⏳ Phase-6 |
| 6 6/1 replay (phantom absent, delta 0) | ◐ unit-proven; full replay needs the captured corpus |
| 7 shadow ≥10 sessions | ⏳ Phase-4/7 (after the optimistic path emits events alongside) |
| 8 CI invariant (no P&L mutation outside fill ingestion) | ⏳ Phase-4 (grep/AST guard ships with the rewire) |
8 ledger tests + 19 trade_updates tests green; `main` boots.

## 6. What is deliberately NOT done yet (Phase 4+)
**No optimistic site is rewired.** `close_with_attribution` (O1) and its ~6 callers still
book the old way. Phase 4 (replace O1-O6 → emit non-bookable ORDER events; only stream
`FILL_*` book; delete the optimistic path) is **gated on**: (a) the raw-capture confirming
the `execution_id` key, (b) a ≥5-session SHADOW where the ledger runs alongside the old path
and the deltas are zero-or-explained, and (c) the Phase-6 Adversary scenarios + crash test
green. Cutover before that = building on faith. The ledger exists, is proven in isolation,
and now waits for evidence before it becomes authoritative.

## Appendix — files
- `src/data/trade_updates.py` (execution_id + broker_event_id + deterministic fallback),
  `src/data/websocket_client.py` (raw tee), `src/ops/fill_capture.py` (raw capture),
  `src/ops/ledger.py` (the ledger), `tests/unit/test_ledger.py` (8). This doc + changelog.
