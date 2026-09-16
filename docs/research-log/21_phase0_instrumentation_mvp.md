# 21 — Phase 0 Instrumentation MVP Spec

**Status:** scoping doc for next sprint. No production code in this
document.
**Owner:** Pierce.
**Created:** Wed 2026-04-22 evening.
**Companion docs:** `18_slippage_methodology_application_v2.md`
(consumer of this data), `20_d146_bar1_exit_spec.md` (a peer signal
spec — same emission discipline).

## Why this is the binding constraint

Every trading day without microstructure capture is a calibration
day lost. Two paired observations from Wed 2026-04-22 illustrate
exactly the data v2 needs and exactly what we are NOT capturing:

- **MAAS**: ~7% participation (200 sh / ~3000 sh bar-1 volume),
  +2.3% in 60s — a "cheap fill, real move" outcome.
- **ELSE re-entry attempt**: ~1028% participation (would-be qty
  exceeded available bar volume), -0.26% over 30 min — a
  "thick-elephant-in-thin-pond, no move" outcome.

These two paired observations are exactly the (q/v_τ, realized_edge)
contrast that the v2 slippage model needs to fit. **Today both were
ephemeral.** Tomorrow's MAAS+ELSE cohort will be lost the same way
unless the schemas below ship.

The MVP's scope is intentionally minimum-viable: just enough surface
to unblock v2 Phase-1 calibration (target N=60–80 fires per cohort
cell). Everything that does not feed that target is deferred.

---

## 1. `trade_context` — one row per order, three snapshots

**Purpose:** capture the NBBO and order-state at submit, first-fill,
and terminal-fill for every order. Without this we cannot back out
realized slippage vs the spread we faced at decision time.

**Path:** `data/instrumentation/trade_context/session_date=YYYY-MM-DD/orders.parquet`

**Schema (16 columns):**

| Column | Type | Notes |
|---|---|---|
| `order_id` | str | Broker order id |
| `ticker` | str | |
| `side` | str | `"buy" \| "sell"` |
| `requested_qty` | int | What we asked for |
| `requested_px` | float | Limit price (or 0 for market) |
| `submit_ts` | datetime UTC | When we submitted |
| `submit_nbbo_bid` | float \| NULL | NBBO bid at `submit_ts` |
| `submit_nbbo_ask` | float \| NULL | NBBO ask at `submit_ts` |
| `first_fill_ts` | datetime UTC \| NULL | First child fill timestamp |
| `first_fill_nbbo_bid` | float \| NULL | NBBO at first fill |
| `first_fill_nbbo_ask` | float \| NULL | |
| `terminal_ts` | datetime UTC \| NULL | Terminal-fill timestamp (Bug D's `_poll_for_terminal_fill` already produces this — wire it) |
| `terminal_nbbo_bid` | float \| NULL | NBBO at terminal |
| `terminal_nbbo_ask` | float \| NULL | |
| `terminal_status` | str | `"filled" \| "partially_filled" \| "canceled" \| ...` |
| `terminal_filled_qty` | int | What actually filled |

**Hook site (in priority order):**
1. `src/execution/alpaca_executor.py` `submit_oto_order` — write the
   submit row immediately after `client.submit_order` returns. Capture
   `submit_nbbo_*` from the entry snapshot already passed in.
2. `src/execution/bridge.py` `_poll_for_terminal_fill` — when terminal
   reached, update the row with `terminal_*` columns. (The helper
   already polls — add the row write at the success site.)
3. First-fill capture is harder — Alpaca's REST API doesn't give us
   intermediate child fills in real time. Defer to v2 hook on the
   trade-updates websocket if we wire that. For MVP: treat
   first-fill = terminal-fill on instant fills, NULL otherwise.

**Tests:** `tests/unit/test_trade_context_recorder.py` — synthetic
order lifecycle (submit → terminal) round-trip read.

---

## 2. `bar_context` — one row per entry bar

**Purpose:** for every position open, capture the OHLCV of the 1-min
bar in which the entry fired, plus our share of that bar's volume.
This is the denominator of `q/v_τ`, the v2 model's participation
ratio.

**Path:** `data/instrumentation/bar_context/session_date=YYYY-MM-DD/entries.parquet`

**Schema (12 columns):**

| Column | Type | Notes |
|---|---|---|
| `position_id` | str | `pos.order_id` (entry) |
| `ticker` | str | |
| `entry_ts` | datetime UTC | `pos.opened_at` |
| `entry_bar_open_ts` | datetime UTC | Start of the 1-min bar containing `entry_ts` |
| `entry_bar_open` | float | OHLCV |
| `entry_bar_high` | float | |
| `entry_bar_low` | float | |
| `entry_bar_close` | float | |
| `entry_bar_volume` | int | "Other-participants V" *includes* our Q |
| `our_q_shares` | int | Our terminal_filled_qty for this entry |
| `q_over_v_tau` | float | `our_q_shares / entry_bar_volume` |
| `bar_data_quality` | str | `"complete" \| "partial" \| "missing"` |

**Hook site:** new helper `src/analysis/bar_context_recorder.py`,
called from `bridge.execute_verdict()` immediately after
`self._pm.add_position(position)` (right after the post-Bug D
qty-drift scheduling).

**Tests:** `tests/unit/test_bar_context_recorder.py` — synthetic
position open + bar lookup, asserts q/v ratio computed correctly
(e.g., 200 sh / 3000 = 0.0667 for the MAAS case).

---

## 3. `child_fill_ticks` — one row per partial fill

**Purpose:** when an order fills via multiple child fills (today's
AGPU was 505 then 341), capture each. Necessary for the v2 model's
intra-fill price-walk analysis.

**Path:** `data/instrumentation/child_fill_ticks/session_date=YYYY-MM-DD/fills.parquet`

**Schema (8 columns):**

| Column | Type | Notes |
|---|---|---|
| `parent_order_id` | str | The OTO parent or single-leg order |
| `child_fill_ts` | datetime UTC | Fill timestamp |
| `qty` | int | Child fill qty |
| `price` | float | Child fill price |
| `venue` | str \| NULL | Exchange (Alpaca exposes via trade-updates WS) |
| `cumulative_filled_qty` | int | Running total at this fill |
| `nbbo_bid_at_fill` | float \| NULL | |
| `nbbo_ask_at_fill` | float \| NULL | |

**Hook site:** Alpaca trade-updates websocket. Currently we don't
subscribe to per-fill events — only per-order status. **Adding the
subscription is the major lift in this MVP.** Defer to a follow-up
PR if it bloats scope; first MVP can derive child-fill rows from the
post-terminal `client.get_orders(order_id=...)` call which returns a
`legs` array — sufficient for the (q/v) calibration even if it
loses real-time intermediate snapshots.

**MVP decision:** ship REST-derived child-fill rows in v0; mark
`venue` and `nbbo_*_at_fill` as NULL. The (qty, price, ts) triple is
sufficient for v2 Phase-1 calibration. WebSocket-derived NBBO
snapshots become v2 enhancement.

**Tests:** `tests/unit/test_child_fill_recorder.py` — synthetic
3-leg fill (505 / 341 / 0) → 2 rows.

---

## 4. `cohort_registry` — one row per cohort match

**Purpose:** for matched-cohort return computation (v2 §1.5,
Sato-Kanazawa cross-section), the cohort donor pool must be persisted
per session so that EOD can compute matched returns
without re-querying the universe.

**Path:** `data/instrumentation/cohort_registry/session_date=YYYY-MM-DD/cohorts.parquet`

**Schema (10 columns):**

| Column | Type | Notes |
|---|---|---|
| `cohort_id` | UUID | One per (ticker, entry_ts) cohort match |
| `traded_ticker` | str | The ticker we entered |
| `cohort_ticker` | str | A donor pool member |
| `match_dt` | datetime UTC | When cohort match was computed |
| `match_features` | dict (JSON) | The (catalyst_type, market_cap_bucket, gap_pct_bucket, hour_bucket) tuple |
| `cohort_entry_ref_px` | float | Cohort's `entry_ts` reference price |
| `cohort_eod_px` | float \| NULL | Backfilled at EOD |
| `cohort_60min_px` | float \| NULL | Backfilled at T+60 |
| `cohort_signed_return_60min` | float \| NULL | `(cohort_60min_px - cohort_entry_ref_px) / cohort_entry_ref_px` (sign convention per QMP) |
| `cohort_data_quality` | str | `"complete" \| "partial" \| "missing"` |

**Hook site:** new module `src/analysis/cohort_registry.py`. Called
from `bridge.execute_verdict()` immediately after
`bar_context_recorder` writes — same lifecycle. The cohort match
itself uses the existing scanner output to find peers in the same
(catalyst, market_cap, hour) bucket; the matching algorithm is a
v2 implementation detail, the SCHEMA above is what this MVP must
ship.

**Volume note:** if a session has 5 entries × 20 cohort peers each,
that's 100 rows. Trivial. Backfill of `cohort_eod_px` happens at
session close via the same bar_recorder used by D196.

**Tests:** `tests/unit/test_cohort_registry.py` — synthetic 1
traded + 5 cohort members, EOD backfill, signed return computation
matches QMP convention.

---

## 5. Write path — single source of truth

All four schemas land via a unified writer in
`src/analysis/instrumentation_writer.py`:

```python
class InstrumentationWriter:
    def emit_trade_context(row: TradeContextRow) -> None: ...
    def emit_bar_context(row: BarContextRow) -> None: ...
    def emit_child_fill(row: ChildFillRow) -> None: ...
    def emit_cohort_registry(row: CohortRow) -> None: ...

    async def flush_all(reason: str) -> None: ...
```

**Buffering:** in-memory ring (default 10 rows per schema) flushed
on (a) ring full, (b) explicit `flush_all` call, (c) EOD via the
existing session-close hook.

**Atomic writes:** `os.replace()` pattern (same as
`session_report.py:408` D218 fix). Each Parquet file write goes
through `path.tmp` → `path` swap.

**Retention policy:**
- Hot: current session — kept in memory + on disk.
- Warm: 90 days — on disk under `data/instrumentation/`, partitioned
  by `session_date`.
- Cold: archive to `data/instrumentation_archive/` quarterly.
  Calibration loads pull warm + cold transparently via partition
  filter.

**Crash safety:** ring buffer loss is acceptable (≤10 rows per
schema = ≤40 rows per session). Reduces hot-path latency vs
sync-on-emit. If catastrophic loss observed in practice, drop ring
size to 1 (= sync write).

---

## 6. Minimum viable surface — what unblocks v2 Phase-1

**Blocking:**
- `trade_context` (16 cols): unlocks per-order spread/slippage regressions
- `bar_context` (12 cols): unlocks q/v_τ regressions
- `child_fill_ticks` REST-derived only (8 cols, NBBO NULL): unlocks
  intra-fill price-walk for terminal-status orders

**Nice-to-have, deferred:**
- `child_fill_ticks` WebSocket-derived NBBO at each fill — v2 polish
- `cohort_registry` full backfill — needed for v2 §1.5 cross-section
  but NOT for v2 §1.1 (own-trade slippage). Can ship v2.1 without it.
- Per-row `data_quality` granularity beyond the 3-state enum.

**Target reached when:** (count of `bar_context` rows where
`bar_data_quality == "complete"`) >= 60 in a (catalyst, market_cap,
hour) bucket. At today's ~3 entries/session pace that's ~20–25
sessions per bucket — first calibration window opens late May 2026.

---

## 7. Hook order for implementation

The order matters because each hook depends on the prior one's data.

1. **`InstrumentationWriter` shell + `trade_context` rows** (1 PR).
   Hook: `alpaca_executor.submit_oto_order` (submit row),
   `bridge._poll_for_terminal_fill` (terminal columns). Tests: round-trip
   read of a synthetic order.

2. **`bar_context` rows** (1 PR). Hook:
   `bridge.execute_verdict` immediately after `add_position`.
   Depends on bar_recorder being callable inline at entry time —
   verify in pre-PR spike. Tests: q/v computation matches MAAS case.

3. **`child_fill_ticks` REST-derived** (1 PR). Hook:
   `bridge._poll_for_terminal_fill` success path — call
   `client.get_orders(order_id=...)`, extract `legs`, emit one row
   per leg. Tests: 2-leg fill (AGPU 505 then 341) → 2 rows.

4. **`cohort_registry`** (1 PR). New module + hook in
   `bridge.execute_verdict`. Tests: 1 traded + 5 peers + EOD backfill.

5. **WebSocket NBBO enhancement** (deferred, v2.1).

Each PR ships with its test file. Each is independently revertable.

---

## 8. What this MVP intentionally does NOT cover

- **Per-tick NBBO history** for every quote update. Out of scope.
  We only snapshot at the four lifecycle points (submit, first-fill,
  terminal, exit).
- **Order-book depth** beyond top-of-book. Out of scope.
- **Halt event capture** — handled by D147 separately.
- **Real-time dashboard surfacing** of these tables. CLI / notebook
  reads only for v2 Phase-1.
- **Backfill of past sessions.** This MVP starts capturing Thu
  2026-04-23. Past sessions are gone.

---

## 9. Cost estimate

- Storage: ~1 KB / row × ~150 rows / session × 252 trading days
  = ~38 MB / year. Trivial.
- Latency: each emit is an in-memory dict append. `flush_all` is
  one Parquet write per schema per ring-full event. Worst case ~5
  ms / flush, 4 flushes / session = 20 ms / session. Trivial.
- Engineering: 4 PRs above × ~1 day each + 1 day spike on
  bar_recorder inline-call viability = **5 engineering days**.

---

*End of spec. Next action: open the bar_recorder spike ticket so PR
2 (bar_context) is unblocked when PR 1 lands.*
