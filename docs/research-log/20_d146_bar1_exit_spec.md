# 20 — D146 BAR-1 EXIT Formalization Spec

**Status:** v1 spec — extracted from production code, not invented.
Becomes the ticket for the dedicated-table instrumentation work.
**Owner:** Pierce.
**Created:** Wed 2026-04-22 evening.
**Predecessors:** `19_eod_bug_findings.md` (Bug F resolution),
`18_slippage_methodology_application_v2.md` (calibration consumer).

D146 is currently the strongest realized-alpha pattern in the system.
On Wed 2026-04-22 it saved an estimated ~$3,925 of counterfactual
drawdown across three trades by exiting at T+60s before the
remainder of the position decayed. That kind of saved-loss attribution
needs to live in a dedicated, queryable table — not be reconstructible
only by reading log lines after the fact.

This spec defines the signal as a first-class production object so it
can be (a) tracked per-fire, (b) calibrated against the v2 slippage
research, and (c) compared apples-to-apples against future variants.

---

## 1. Stable signal ID and trigger conditions

### Signal ID

Canonical ID: **`D146_BAR1_EXIT_v1`**

Stable: any future variant gets a new suffix (`v2`, `T+90s_v1`, etc.).
Logged on every fire so audit / replay can filter on signal version.

### Trigger conditions (from `main.py` BAR-1 fire site, post-Bug F)

Reading the production code directly — these are the operative
preconditions, not a wish-list:

```
fire_iff (
    settings.execution.bar1_exit_enabled                        # config gate
  AND not getattr(pos, "_bar1_exit_fired", False)               # one-shot per position
  AND _pos_age_s >= settings.execution.bar1_exit_delay_seconds  # T+window elapsed
  AND _bar1_qty = int(pos.remaining_qty * bar1_exit_pct) > 0    # qty resolves >0
)
```

Source: `main.py` D146 BAR-1 EXIT block (post-2026-04-22 Bug F fix).

### What is NOT currently a precondition (intentional gaps the spec must own)

The current code fires regardless of:
- **Direction of the bar-1 move.** No price-action predicate. A bar-1
  drop fires the same way as a bar-1 rip; both result in 100% exit.
- **Catalyst type.** Triggers identically across `fda_approval`,
  `earnings_beat`, `unknown`, etc.
- **Volume / RVOL at bar 1.** No participation gate.
- **Spread at exit.** No widening abort.
- **Manipulation phase.** No gate on `pos.manipulation_phase`.

These gaps are intentional in v1 — the Arena research validated the
unconditional rule. v2 of this spec is where the price-action /
catalyst / RVOL predicates get added once we have enough captures
(see Section 3 metrics) to test them.

### Eligibility filters that DO apply (upstream, not in BAR-1 itself)

Bar-1 inherits any filter that prevents `pos` from existing in the
first place — Phase-2 entry filters, Kelly tier sizing, faller-risk
gate. None of these are bar-1-specific; documented here only so the
spec is honest about its dependency surface.

---

## 2. Config block schema

### Current production values (verified against `config/settings.py:716-735`)

| Field | Type | Current | Bounds | Notes |
|---|---|---|---|---|
| `bar1_exit_enabled` | bool | `True` | n/a | Master gate. |
| `bar1_exit_pct` | float | `1.00` | `(0, 1.0]` | 100% = sell entire position. Walk-forward: 50% PF=1.5x (most robust), 80%=1.8x, 100%=2.0x (at threshold). Fallback to 0.50 if monitoring shows problems. |
| `bar1_exit_delay_seconds` | float | `60.0` | `[30.0, 300.0]` (proposed sensible bounds) | 60s = first 1-min bar. Lower bound 30s avoids racing fill confirmation; upper 300s = 5 bars (degenerates into "EOD-lite" past that). |

### Proposed v2 additions (NOT yet implemented; spec only)

These are the hooks the calibration table (Section 4) needs in
order to support per-cell variant testing later. None of them are
operative in v1; they appear as v2 schema so the table writer can
emit nullable columns now and avoid a schema migration later.

| Field | Type | Default | Purpose |
|---|---|---|---|
| `bar1_min_volume_at_t60s` | int \| None | `None` | Optional RVOL gate. Skip exit if bar-1 volume < threshold (i.e., no real move to exit before). |
| `bar1_skip_if_in_drawdown_pct` | float \| None | `None` | Optional: if `(current_price - entry_price) / entry_price < -X`, skip — let Phase-2 stop handle. |
| `bar1_skip_if_spread_widened_pct` | float \| None | `None` | Optional: if `current_spread / entry_spread > 1+X`, skip — adverse exit microstructure. |
| `bar1_eligible_catalyst_types` | list[str] \| None | `None` (= all) | Optional whitelist of catalyst types this signal applies to. |

---

## 3. Per-trade metrics (the spec's main payload)

Every BAR-1 fire must persist exactly one row to the dedicated table
with the following columns. **No silent gaps.** If a value cannot be
captured (e.g., NBBO unavailable for a thinly-quoted name), persist
explicit `NULL` with a `data_quality` flag — never default-zero.

### Identity columns

| Column | Type | Source |
|---|---|---|
| `signal_id` | str | Hardcoded `"D146_BAR1_EXIT_v1"` |
| `fire_id` | UUID | New per-fire UUID4 |
| `ticker` | str | `pos.ticker` |
| `position_id` | str | `pos.order_id` (entry order id) |
| `session_date` | date | UTC session date |

### Timing columns

| Column | Type | Source |
|---|---|---|
| `entry_ts` | datetime UTC | `pos.opened_at` |
| `exit_decision_ts` | datetime UTC | At fire moment, BEFORE first `await` |
| `exit_submit_ts` | datetime UTC | After `client.close_position(ticker)` returns |
| `exit_terminal_ts` | datetime UTC \| NULL | Terminal-fill timestamp from broker (uses Bug D's `_poll_for_terminal_fill`) |
| `t_window_s_actual` | float | `(exit_decision_ts - entry_ts).total_seconds()` |

### Price columns

| Column | Type | Source |
|---|---|---|
| `entry_px` | float | `pos.entry_price` (bridge already captures the actual fill, post-Bug D) |
| `exit_px` | float | Avg fill price across all child fills on the close order |
| `signal_px` | float | `pos.signal_price` (price at evaluation, for slippage delta) |

### Bar-1 OHLCV columns (the bar in which the exit fires)

| Column | Type | Source |
|---|---|---|
| `bar1_open` | float | First trade ≥ entry_ts in the 1-min bar containing exit_decision_ts |
| `bar1_high` | float | Bar high |
| `bar1_low` | float | Bar low |
| `bar1_close` | float | Bar close (or last trade if mid-bar) |
| `bar1_volume` | int | Bar total volume |
| `bar1_vwap` | float | Bar VWAP |

### Microstructure columns (the v2 calibration payload)

| Column | Type | Source |
|---|---|---|
| `our_q_shares` | int | Our fill qty across all child fills on the close order |
| `bar1_total_v_shares` | int | Same as `bar1_volume` (renamed for slippage-paper convention) |
| `q_over_v_tau` | float | `our_q_shares / bar1_total_v_shares` (clamped to `[0, ∞)`) |
| `nbbo_bid_at_entry` | float \| NULL | NBBO bid at `entry_ts` |
| `nbbo_ask_at_entry` | float \| NULL | NBBO ask at `entry_ts` |
| `nbbo_bid_at_exit` | float \| NULL | NBBO bid at `exit_terminal_ts` |
| `nbbo_ask_at_exit` | float \| NULL | NBBO ask at `exit_terminal_ts` |
| `entry_spread_bps` | float \| NULL | `(ask - bid) / mid * 1e4` at entry |
| `exit_spread_bps` | float \| NULL | `(ask - bid) / mid * 1e4` at exit |

### Outcome columns (realized + counterfactual)

| Column | Type | Source |
|---|---|---|
| `realized_pnl_usd` | float | `(exit_px - entry_px) × our_q_shares` (post-Bug F: matches `Arena_actual` log) |
| `realized_edge_bps` | float | `(exit_px - entry_px) / entry_px × 1e4` |
| `mfe_in_window_pct` | float | Max `(high - entry_px) / entry_px` for any tick in `[entry_ts, exit_decision_ts]` |
| `mae_in_window_pct` | float | Min `(low - entry_px) / entry_px` for any tick in same window |
| `counterfactual_hold_to_eod_pnl_usd` | float \| NULL | Hypothetical P&L if we had not fired BAR-1 — needs EOD price for ticker. Backfilled at session close (~16:05 ET). |
| `counterfactual_drawdown_avoided_usd` | float \| NULL | `min(0, counterfactual_hold_to_eod_pnl_usd - realized_pnl_usd)` — the "saved loss" Wed 2026-04-22 estimated at $3,925. |

### Quality columns

| Column | Type | Source |
|---|---|---|
| `nbbo_data_quality` | str | `"complete" \| "partial" \| "missing"` |
| `bar_data_quality` | str | Same |
| `notes` | str \| NULL | Free-text per-fire annotation (e.g., `"halt window collided with exit"`) |

**Total: 32 columns.** Wide schema by design — calibration regressions
on this table directly feed v2 slippage paper.

---

## 4. Dedicated table schema (storage layer)

### Storage choice

Append-only Parquet partitioned by `session_date`. Path:

```
data/signals/D146_BAR1_EXIT_v1/session_date=YYYY-MM-DD/fires.parquet
```

Parquet over JSONL because (a) all 32 columns are typed, (b) calibration
loads use columnar projection — only `our_q_shares`, `bar1_total_v_shares`,
`realized_edge_bps`, `entry_spread_bps`, `exit_spread_bps` for v2
slippage regressions, (c) partition by date trivially supports
walk-forward CV splits.

### Write path

Single function in `src/analysis/signal_recorder.py` (new module):

```python
def record_bar1_fire(row: dict) -> None: ...
```

Called from `main.py` after `format_arena_actual_line` is logged
(post-Bug F site). Buffered in-memory and flushed every 5 fires or
at EOD, whichever first. Crash-safe via `os.replace()` atomic
write (same pattern as `session_report.py:408` D218 fix).

### Schema versioning

`signal_id = "D146_BAR1_EXIT_v1"`. A v2 of the spec ships a NEW
table directory `D146_BAR1_EXIT_v2/...`; never mutate v1 schema in
place. Reads union via `pd.concat([read(v1), read(v2)])` with a
`signal_version` column tag.

### Calibration consumer

`src/analysis/v2_slippage_calibration.py` (already on the v2 plan
roadmap) reads this table to compute per-cell:

- `α_v2 = E[realized_edge_bps]` (intercept)
- `β_v2 = ∂(realized_edge_bps) / ∂(entry_spread_bps)` (cost slope)
- `γ_v2 = ∂(realized_edge_bps) / ∂(q_over_v_tau)` (participation impact)

Per cell of the (catalyst_type, market_cap_bucket, hour_bucket)
matrix from `18_slippage_methodology_application_v2.md`. Target
N=60–80 fires per cell — currently we have ~3 per session, so
~20–30 sessions to first calibration window.

---

## 5. Worked examples — Wed 2026-04-22 closures

These are the spec's first three rows. Numbers extracted from the
postmortem; per-cell completeness matches what we'd capture with
the spec live. **Italicized** values are estimates because the
infrastructure didn't exist yet on 2026-04-22 — they become real
captures starting Thu 2026-04-23 if the spec ships.

### Row 1 — ELSE (the bug-affected close, see Bug E + Bug #13)

| Column | Value |
|---|---|
| signal_id | `D146_BAR1_EXIT_v1` |
| ticker | `ELSE` |
| entry_ts | `2026-04-21T21:48Z` *(overnight hold, NOT a same-day BAR-1 — see note)* |
| exit_decision_ts | `2026-04-22T14:00:00Z` (10:00 ET) |
| t_window_s_actual | *N/A — overnight, not a BAR-1 fire* |
| entry_px | `7.65` |
| exit_px | `7.7703` |
| our_q_shares | `2478` |
| realized_pnl_usd | `+298.20` |

**Note.** ELSE is included as a worked example because it appeared in
today's three-closure cohort and the postmortem references it, but
strictly it is NOT a D146 BAR-1 fire — it was the D91 overnight close
fixed by Bug E. Including it in the spec rows would mis-categorize
the data; the row is shown here struck-through to flag the gap so
the implementer doesn't accidentally write it to the table.

### Row 2 — AGPU (the actual BAR-1 fire)

| Column | Value |
|---|---|
| signal_id | `D146_BAR1_EXIT_v1` |
| ticker | `AGPU` |
| position_id | `1f40d2da` |
| entry_ts | `2026-04-22T14:51:31Z` (terminal fill, post-Bug D) |
| exit_decision_ts | `2026-04-22T14:52:31Z` (T+60s) |
| t_window_s_actual | `60.0` |
| entry_px | `9.59` |
| exit_px | `9.59` |
| our_q_shares | `846` (post-Bug D — pre-fix this would have been 505) |
| bar1_volume | *not captured today* |
| q_over_v_tau | *not captured today* |
| realized_pnl_usd | `0.00` |
| realized_edge_bps | `0.0` |
| mfe_in_window_pct | *not captured today; estimated +0.3%* |
| mae_in_window_pct | *not captured today; estimated -0.4%* |
| counterfactual_hold_to_eod_pnl_usd | *AGPU 16:00 close = ?; backfill needed* |

**Calibration value.** AGPU was a flat outcome but the participation
ratio and microstructure were the interesting variables. With the
spec live, this row would carry the (q/v, spread) snapshot that
Arena research currently has to estimate.

### Row 3 — MAAS

| Column | Value |
|---|---|
| signal_id | `D146_BAR1_EXIT_v1` |
| ticker | `MAAS` |
| entry_ts | `2026-04-22T15:24:00Z` (10:24 ET — estimated) |
| exit_decision_ts | `2026-04-22T15:25:00Z` (10:25 ET, T+60s) |
| t_window_s_actual | `60.0` |
| entry_px | `12.00` |
| exit_px | `12.153` |
| our_q_shares | `200` |
| realized_pnl_usd | `+30.60` |
| realized_edge_bps | `127.5` |

**Spec gap surfaced.** Without the spec table, we know MAAS made
+$30.60 but not whether bar-1 volume was 1k shares or 1M, not
whether spread doubled at exit. Either of those microstructure
facts changes whether the v2 calibration treats this as a "good
participation" or "tiny-window-of-opportunity" outcome.

---

## 6. Implementation checklist (for the next sprint)

Not patches — these become tickets:

- [ ] Create `src/analysis/signal_recorder.py` with `record_bar1_fire(row)`
      and `_flush_bar1_buffer()`. Follow `session_report.py:408` atomic
      write pattern.
- [ ] Create the Parquet directory structure under
      `data/signals/D146_BAR1_EXIT_v1/`.
- [ ] Wire `record_bar1_fire(...)` call into `main.py` immediately
      after the post-Bug F `format_arena_actual_line` log.
- [ ] Wire NBBO snapshot capture (via existing `_d78_snapshots`) into
      the row build. Tag `nbbo_data_quality` on missing fields.
- [ ] Wire bar-1 OHLCV snapshot via `bar_recorder` (already used at
      EOD; can be queried inline at the fire site or via a 5-fire
      flush callback).
- [ ] Backfill counterfactual columns at session close (~16:05 ET) by
      reading EOD price from `bar_recorder` for each fired ticker.
- [ ] Write `tests/unit/test_d146_signal_recorder.py` — the table
      writer + roundtrip read. Three synthetic fires per the worked
      examples above.
- [ ] Bridge to v2: ensure `v2_slippage_calibration.py` reader can
      load this table when it exists. (Plan placeholder; not gating.)

---

## 7. Open questions for the architecture call

1. **Fire-site vs background-flush.** Inline write per fire is simpler
   but blocks the BAR-1 hot path with disk I/O. Background flush is
   correct but adds a buffer-loss window on crash. Acceptable to
   accept up to 5 fires of buffer loss (= one session's worth).

2. **Counterfactual backfill schedule.** EOD backfill misses the case
   where ticker halts before EOD. Should we backfill at T+30min from
   each fire instead? Trade-off: data freshness vs read-volume.

3. **NBBO source.** Polygon NBBO costs vs Alpaca's quote stream.
   Alpaca quotes are sufficient for the entry/exit snapshots; the
   v2 calibration paper claims Polygon for NBBO. Does that block v1
   spec readiness, or do we ship with Alpaca quotes and version-tag
   the NBBO source column?

---

*End of spec. Becomes the ticket for the dedicated-table
instrumentation work; no production code in this document.*
