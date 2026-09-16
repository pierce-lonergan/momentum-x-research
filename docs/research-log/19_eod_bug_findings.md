# 19 — EOD Bug Findings (Wed 2026-04-22 evening)

**Diagnostic-first writeup before any patch.** Per the user directive:
each bug gets `what I found → root cause → fix → regression test →
rollout check`. No production edits until each item has a written
findings note.

Source: today's session log + broker order history. All file:line
refs are post-Bug C commit `899e03f`.

---

## Bug D — AGPU qty 846/505 mismatch  (HIGH)

### What I found

**Broker truth** (`/v2/orders/1f40d2da`):
```
submitted_at:     2026-04-22T14:51:26.648Z
filled_at:        2026-04-22T14:51:31.129Z   ← terminal fill: +4.5 seconds
status:           filled
qty:              846
filled_qty:       846
filled_avg_price: 9.59
```

**System log** (`paper_2026-04-22.log`):
```
10:51:26  D101 SIZING AGPU: qty_risk=846 → qty=846
10:51:26  Submitting OTO order — qty=846, entry=9.60, stop=9.46
10:51:28  D217: Order 1f40d2da confirmed fill at $9.59 (poll 1/3)   ← poll fired at +2s
10:51:28  AGPU: Position opened — qty=505, entry=$9.59              ← captured 505
10:51:28  D215 EXECUTION RECORDED: BUY AGPU long qty=505 fill=$9.59
```

**Timing reconstructs exactly**: poll 1 fires at submit + 2s = 14:51:28.6
UTC. Terminal fill arrives at 14:51:31.1. Poll captured a partial-fill
snapshot at 505 shares; never re-polled.

**Downstream impact**: position tracker held 505 shares of AGPU; broker
held 846. Exit (broker-driven `close_position`) sold all 846. P&L
attribution journal recorded 505 (wrong).

### Root cause

`src/execution/bridge.py:248-257`:

```python
_d216_fill = float(_d216_check.get("filled_avg_price") or 0)
if _d216_fill > 0:                                          # ← TRUE on partial
    order_result.fill_price = _d216_fill
    order_result.qty = int(_d216_check.get("filled_qty") or order_result.qty)
    logger.info("D217: Order %s confirmed fill at $%.2f (poll %d/%d)", ...)
    _d217_confirmed = True
    break                                                    # ← exits loop on partial
```

The condition `_d216_fill > 0` evaluates **TRUE on partially_filled**
status because Alpaca returns `filled_avg_price` populated as soon as
ANY child fill executes. The poll loop accepts the partial as
"confirmed" without checking `status`.

Compounding: the executor itself (`src/execution/alpaca_executor.py:307-325`)
has a similar `partially_filled` branch that captures and returns the
partial qty WITHOUT polling for completion. So the bug exists in TWO
places and reinforces.

### Fix

Two-layer:

**Layer 1 — `bridge.py:248-257`**: change the confirm condition from
`filled_avg_price > 0` to `status in ('filled', 'done_for_day')` (terminal
states). Continue polling on `partially_filled`. Increase max polls + total
budget to accommodate slow fills (currently 3 polls × 2s = 6s; insufficient
for some instances). Suggest 6 polls × 2s = 12s.

**Layer 2 — `alpaca_executor.py:307-325`**: when the OTO submission
response is `partially_filled` at submit time (rare but possible), do
NOT accept the partial as final. Instead, return an `OrderResult` with
`status="partially_filled"` and the bridge poll loop will then handle it.

**Layer 3 — defensive verification (per user directive)**: add post-entry
assertions at T+5s, T+30s, T+60s. Compare `position.qty` (internal)
against `broker.get_position(ticker).qty`. On divergence:
- log `D218 QTY_DRIFT` warning with both values
- auto-reconcile internal qty to broker truth
- emit metric `qty_drift_count` for ongoing observability
- update the journal entry with the corrected qty

### Regression test

`tests/unit/test_d217_partial_fill_handling.py`:

1. **`test_partial_fill_does_not_terminate_poll_loop`** — mock client
   returns sequence `[{status: "partially_filled", filled_qty: 505,
   filled_avg_price: 9.59}, {status: "partially_filled", filled_qty:
   700, ...}, {status: "filled", filled_qty: 846, ...}]`. Assert poll
   loop runs 3 times and final captured qty=846.

2. **`test_filled_status_terminates_poll_loop`** — mock returns
   `{status: "filled", filled_qty: 500, filled_avg_price: 9.59}` at
   poll 1. Assert poll exits after 1 iteration with qty=500 (terminal).

3. **`test_qty_drift_assertion_logs_warning`** — simulate internal
   qty=505, broker qty=846. Call the new assertion helper. Assert
   warning logged with code `D218 QTY_DRIFT` containing both values
   and that internal_qty was reconciled to 846.

4. **Reproduction-of-AGPU test**: replay the exact 4.5s timing from
   today's log: status sequence `[partially_filled@505, filled@846]`
   at poll-relative times `[0s, 3s]`. Assert final qty=846 and a
   `D217 ... poll 2/N` log line is emitted.

### Rollout check

After deploy, on first new entry:
- grep for `D217.*poll [2-9]` — confirm at least one entry needed >1 poll
- grep for `D218 QTY_DRIFT` — confirm assertion fires (or doesn't, if
  no drift; either is informational, never silent)
- query broker post-fill, compare against internal — confirm match

### Backfill (per user directive)

Scan last 30 days of fills for internal/broker qty divergence. Method:
for each `EXECUTION RECORDED` line in `logs/paper_2026-*.log`,
extract `qty` and `order_id`. For each, query broker `/v2/orders/<id>`
for true `filled_qty`. Flag divergences. **Tracked separately as
appendix work**, not blocking the patch.

---

## Bug E — D91 09:30 close-at-open silent failure  (MEDIUM)

### What I found

**Today's log** (Wed 2026-04-22):
```
04:30:30  [original startup, pre-Bug-B-fix]
          D86: Broker has 1 positions at startup, tracker has 1, ghosts=0, overnight=1
          D91: 1 OVERNIGHT positions detected from previous session: ['ELSE']. Will close at market open.

06:52:17  [Phase 3 restart, post-Bug-B-fix]
          D64: Loaded session state — 1 positions, daily_pnl=$0.00
          D86: Cancelled 0 stale orders, PRESERVED 1 protective GTC stops on held positions
          D86: PRESERVED order b062f185 (ELSE) — GTC stop
          (... no D91 log line in this restart ...)

09:30 ET  [market open]
          (... no D91 close logged ...)
          (... ELSE re-evaluated by agents at 09:54 ...)

10:00:57  D146 BAR-1 EXIT: ELSE — selling 2478/2478 (100%) at T+85431s
10:00:59  ELSE: Closed WITHOUT attribution (no cached ScoredCandidate) — PnL=$-46.09
```

The 04:30 startup correctly queued ELSE for D91 close. The 06:52
restart didn't re-emit D91 because the 06:52 startup ran on **Bug B
fix** which loaded the populated session_state (no longer "stale").
Phase 3 evaluation pipeline then handled ELSE through the normal
re-evaluation path. The `WITHOUT attribution` warning at 10:00:59
confirms the eventual exit was outside the verdict pipeline.

### Root cause

**`main.py:1162-1165`** in the D91 detection block:

```python
elif sym in _tracked_tickers and session_state is None:
    # D91: Position is tracked but session state was stale (from yesterday).
    # These positions are leftovers from a failed D76 EOD close yesterday.
    # Queue them for close at market open to prevent carrying overnight.
    _overnight_positions_to_close.append(sym)
```

The detection condition is `session_state is None` — i.e., D91 only
queues overnight positions when D64 fails to load state. **Bug B fixed
D64's failure mode**; consequently D91's "stale state" trigger never
fires anymore. The two interact:

| State load result | Pre-Bug-B (was) | Post-Bug-B (is) |
|---|---|---|
| D64 returns None  | D91 queues overnight close ✓ | (rare; only on missing+missing-bak) |
| D64 returns state | D91 skips (assumes intentional carry) | D91 skips even if positions are overnight |

After Bug B, **every restart on a populated state file results in
overnight positions silently surviving** through the Phase 3
re-evaluation pipeline — which wastes LLM calls re-scoring positions
we'd already decided to close, and produces a non-deterministic exit
mechanism (whichever path fires first: BAR-1 EXIT, time-decay,
trailing stop, agent re-verdict).

This is a textbook case of **the kind of state-mutation regression
the v2 plan's "every state-mutation path needs a reversibility test"
discipline (rule (f)) is designed to catch**: Bug B's success changed
D91's contract without anyone updating D91.

### Fix

Two-part:

**Part 1 — explicit overnight detection** (separate from "stale
state"):

```python
# main.py D91 detection — replace the session_state-coupled detection
# with explicit overnight-marker logic.
#
# A position is "overnight" iff:
#   (a) it was opened on a prior session_date, AND
#   (b) D76 EOD close did not fire successfully for it
#
# session_state.positions[ticker].opened_at is the ground truth.
# If opened_at < today's session_date, it's an overnight carry.
for bp in _startup_broker_pos:
    sym = bp.get("symbol", "")
    if sym not in _tracked_tickers:
        # ghost case (existing logic)
        continue
    pos_state = session_state.positions.get(sym) if session_state else None
    if pos_state is None:
        # tracked but no state — treat as overnight (defensive)
        _overnight_positions_to_close.append(sym)
        continue
    opened_iso = pos_state.opened_at or ""
    if opened_iso and opened_iso < today_iso:
        _overnight_positions_to_close.append(sym)
```

**Part 2 — close_pending tag for evaluation queue** (per user directive):

```python
# When D91 detection adds a ticker to _overnight_positions_to_close,
# also tag the position so the evaluation pipeline skips it.
for sym in _overnight_positions_to_close:
    pos = position_manager.get_position(sym)
    if pos is not None:
        pos.close_pending = True   # NEW field on ManagedPosition
        pos.close_pending_reason = "D91_overnight"

# In the evaluation queue (orchestrator.evaluate_candidates and
# the re-evaluation paths in main.py Phase 3 loop):
candidates = [c for c in candidates
              if not (position_manager.has_position(c.ticker)
                      and position_manager.get_position(c.ticker).close_pending)]
```

**Part 3 — D91 owns the close + logs both steps**:

```python
# D91 close routine (main.py:1782-1809) — make it explicit:
for _ov_sym in _overnight_positions_to_close:
    pos = position_manager.get_position(_ov_sym)
    # Step 1: cancel any GTC stop (Bug A territory: respect it but
    # cancel it now since we're closing the position)
    if pos and pos.stop_order_id:
        try:
            await client.cancel_order(pos.stop_order_id)
            logger.info("D91 STEP 1: cancelled GTC stop for %s (oid=%s)",
                        _ov_sym, pos.stop_order_id)
        except Exception as e:
            logger.warning("D91 STEP 1 CANCEL FAILED for %s: %s — proceeding to close",
                           _ov_sym, e)
    # Step 2: market sell
    try:
        await client.close_position(_ov_sym)
        logger.info("D91 STEP 2: market close submitted for %s", _ov_sym)
    except Exception as e:
        logger.error("D91 STEP 2 CLOSE FAILED for %s: %s", _ov_sym, e)
        continue
    # Step 3: remove from tracker
    position_manager.remove_position(_ov_sym)
    state_mgr.remove_position(_ov_sym)
    state_mgr.save()
    logger.info("D91 STEP 3: removed %s from tracker + state", _ov_sym)
```

### Regression test

`tests/unit/test_d91_overnight_detection.py`:

1. **`test_position_with_yesterdays_opened_at_is_overnight`** — create
   a populated SessionState with one position whose `opened_at` is
   yesterday. Run D91 detection. Assert ticker added to
   `_overnight_positions_to_close`.

2. **`test_position_with_todays_opened_at_is_not_overnight`** —
   ticker opened today (intentional carry within a session). Assert
   NOT added.

3. **`test_close_pending_position_skipped_by_eval_filter`** — set a
   ManagedPosition with `close_pending=True`. Build a candidate list
   including its ticker. Assert filter excludes it.

4. **`test_d91_close_logs_three_steps`** — mock client. Run D91 close
   on a position with stop_order_id. Assert log lines `D91 STEP 1`,
   `D91 STEP 2`, `D91 STEP 3` all present in order.

5. **`test_d91_step2_failure_does_not_remove_from_tracker`** — mock
   client.close_position to raise. Run D91 close. Assert tracker
   STILL has the position (so we retry on next iteration).

6. **Reproduction-of-today test**: simulate Bug-B-fix-loaded state
   with ELSE having yesterday's opened_at. Assert D91 fires AND
   close_pending tag is set AND eval pipeline excludes ELSE.

### Rollout check

On first restart with an overnight position:
- grep for `D91 ... OVERNIGHT positions detected` — confirm new
  detection path triggers
- grep for `D91 STEP 1/2/3` — confirm explicit step-by-step logging
- grep for the position's ticker in agent evaluation lines AFTER
  09:30 — confirm it does NOT appear (close_pending excluded)
- broker check — confirm position closed by 09:30:30

---

## Bug #13 — trade journal P&L counter broken  (MEDIUM)

### What I found

EOD log:
```
═══ TRADE JOURNAL SUMMARY ═══
  Evaluations: 205 | BUYs: 119 | Closed: 0
  Total P&L: $0.00 | Avg MFCS: 0.3379
```

But broker truth:
- 3 closures (ELSE, AGPU, MAAS)
- Realized P&L: +$328.80

The summary is computed via `trade_journal.session_summary()`
(`src/analysis/trade_journal.py:648`):

```python
closed_entries = [e for e in buy_entries if e.exit_price is not None]
total_pnl = sum(e.realized_pnl or 0 for e in closed_entries)
```

So `Closed: 0` means **no journal entry has `exit_price` set**.
Someone needs to call an "update journal entry on exit" function;
none of today's exit paths did so.

### Root cause

The journal entry's `exit_price` and `realized_pnl` fields are set by
the `record_exit` (or equivalent) method on `TradeJournal`. The exit
paths in main.py call:

- `bridge.close_with_attribution(ticker, exit_price)` — for the
  attribution side
- `position_manager.remove_position(ticker)` — for tracker cleanup
- `state_mgr.remove_position(ticker)` + `state_mgr.save()` — for
  persistence

But **none** of those call `trade_journal.record_exit()` or update
the JournalEntry's `exit_price`. The journal stays at the entry-time
state; the EOD summary correctly reports "0 closed" because zero
entries have `exit_price` populated.

This is **not a "BAR-1 path doesn't increment a counter" bug** as the
postmortem hypothesized — it's a "no exit path on any signal updates
the journal entry's exit fields" bug. The journal has only ever been
populated correctly on entry; exit-side population was never wired.
This morning's first-ever profitable session surfaces the gap.

### Fix

Single source of truth: **the trade journal P&L counter must derive
from broker fill ledger**, not from per-path counter increments.

Implementation:

```python
# At EOD summary time, before computing closed_count + total_pnl:
# Fetch broker fills for today, match against journal entries by
# (ticker, side, time-window). Update each matched entry's
# exit_price and realized_pnl from the broker truth.

async def _reconcile_journal_against_broker(trade_journal, client, session_date):
    """Single-source-of-truth reconciliation:
    pull today's broker fills, match against journal entries,
    backfill exit_price + realized_pnl from broker."""
    fills = await client.get_orders(
        status="all", limit=500, after=f"{session_date}T04:00:00Z",
    )
    sells = [f for f in fills if f.get("side") == "sell"
             and f.get("status") == "filled"
             and float(f.get("filled_qty", 0)) > 0]

    for entry_id, entry in trade_journal._entries.items():
        if entry.action != "BUY" or entry.exit_price is not None:
            continue
        # Find matching sell by ticker + post-entry time
        matching_sells = [s for s in sells
                          if s["symbol"] == entry.ticker
                          and s["filled_at"] > entry.fill_timestamp]
        if not matching_sells:
            continue
        # Take the chronologically-first sell (the close)
        sell = min(matching_sells, key=lambda s: s["filled_at"])
        entry.exit_price = float(sell["filled_avg_price"])
        entry.exit_qty = int(float(sell["filled_qty"]))
        entry.realized_pnl = round(
            (entry.exit_price - entry.fill_price) * entry.exit_qty, 2,
        )
        entry.exit_timestamp = sell["filled_at"]
```

Call this **before** `session_summary()` at EOD. The journal becomes
authoritative because it's broker-derived.

Bonus correctness: this also catches the qty-drift case (Bug D). If
internal entry qty is 505 but broker filled qty is 846, the
reconciliation uses broker's 846 for P&L — so even if the entry-time
qty is wrong, the reconciled exit_qty/realized_pnl is correct.

### Regression test

`tests/unit/test_trade_journal_reconciliation.py`:

1. **`test_reconciliation_populates_exit_price`** — seed journal
   with a BUY entry (no exit_price). Mock client returns a matching
   sell fill. Call reconcile. Assert entry.exit_price set to broker
   fill price.

2. **`test_reconciliation_handles_no_matching_sell`** — seed BUY
   entry; broker returns no sells. Reconcile. Assert no exit_price
   set, no exception.

3. **`test_reconciliation_picks_chronologically_first_sell`** — two
   sells for same ticker (e.g., partial close + final close).
   Assert first one used as the "close" reference (defensive
   conservatism: first close is the canonical exit timestamp).

4. **`test_reconciliation_fixes_qty_drift_in_pnl`** — entry has
   qty=505 (Bug-D-style), broker sell qty=846. Reconcile. Assert
   realized_pnl computed using broker's 846, not internal 505.

5. **`test_reconciliation_idempotent`** — call reconcile twice;
   assert second call doesn't double-update.

6. **Reproduction-of-today test**: mock today's fill set (3 sells:
   ELSE/AGPU/MAAS). Run reconcile. Assert
   `closed_count=3, total_pnl≈+$328.80` matches broker math.

### Rollout check

On next session EOD:
- grep `═══ TRADE JOURNAL SUMMARY ═══` — confirm `Closed: N` matches
  the actual position-closure count
- confirm `Total P&L:` matches broker realized P&L within $1
  (rounding)
- confirm no new `WARNING|ERROR` in the journal-reconciliation path

---

## Bug F — "Arena: +$5.11 (+299%)" hardcoded  (LOW-MEDIUM)

### What I found

`main.py:4257`:
```python
logger.info(
    "D146 BAR-1 EXIT: %s — selling %d/%d shares "
    "(%.0f%%) at T+%.0fs. Arena: +$5.11 (+299%%).",
    pos.ticker, _bar1_qty, pos.remaining_qty,
    _bar1_pct * 100, _pos_age_s,
)
```

The literal `+$5.11 (+299%%)` is part of the format string and is
NOT computed per-trade. The comment block at `main.py:4243-4248`
explains:

```
# The arena's strongest finding: MFE peaks at bar 1.
# 100% exit at T+60s = +$5.11 (+299% vs original).
# Walk-forward: 2.0x overfit (borderline). Test PF=3.20.
```

So `+$5.11 (+299%)` is the **Arena research backtest's expected
edge** for the BAR-1 EXIT pattern, not a per-trade attribution. The
`Arena:` label without qualification is misleading because it reads
like a per-trade computed value.

### Root cause

The label was correct in design intent (Arena-backtest expectation)
but ambiguous in operator-facing log output. Today's log has the
identical "+$5.11 (+299%)" emitted across three structurally
different trades (real per-trade P&Ls -0.6%, -0.83%, +2.3%) which
makes the misleading nature obvious.

### Fix

Per user directive: "If it's meant to be pattern-backtest expectation:
relabel `Arena_expected` and add `Arena_actual` alongside."

```python
# Compute per-trade actual edge using the most recent quote
_arena_actual_px = await _last_price_or_mid(client, pos.ticker)
_arena_actual_pnl = (_arena_actual_px - pos.entry_price) * _bar1_qty
_arena_actual_bps = (_arena_actual_px - pos.entry_price) / pos.entry_price * 10000

logger.info(
    "D146 BAR-1 EXIT: %s — selling %d/%d shares (%.0f%%) at T+%.0fs. "
    "Arena_expected: +$5.11 (+299%% vs hold; backtest constant). "
    "Arena_actual: $%+.2f (%+.0fbps).",
    pos.ticker, _bar1_qty, pos.remaining_qty, _bar1_pct * 100, _pos_age_s,
    _arena_actual_pnl, _arena_actual_bps,
)
```

Alternative if `_last_price_or_mid` isn't immediately available
(would require an async API call inside the exit path that was
designed to be fast): defer the actual-edge computation to the
post-exit attribution step that fires within seconds of the exit, and
log "Arena_actual" there.

### Regression test

`tests/unit/test_d146_bar1_exit_logging.py`:

1. **`test_arena_expected_label_is_unmistakable`** — capture the
   format string used. Assert it contains `Arena_expected:` and a
   parenthetical disclaimer like `backtest constant`.

2. **`test_arena_actual_appears_alongside_expected`** — same format
   string. Assert it ALSO contains `Arena_actual:` placeholder.

3. **`test_no_bare_arena_colon_label_in_d146`** — grep main.py for
   any `"Arena:"` literal in a D146-related context. Assert zero
   occurrences (the bare label is the bug we're fixing).

### Rollout check

On first BAR-1 EXIT after deploy:
- grep log for `Arena_expected:` and `Arena_actual:` on the same line
- confirm `Arena_actual` value differs across trades (not constant)
- confirm `Arena_expected` value remains the constant `+$5.11 (+299%)`
  with explicit "backtest constant" qualifier

---

## Cross-cutting observations

Three of these four bugs share the same structural signature this
week's earlier audits surfaced:

- **Bug D** (poll-1-confirms-partial-as-final): silent acceptance of
  intermediate state as terminal state. Same shape as the watchdog
  120s-vs-actual-latency mismatch fixed Tue.
- **Bug E** (D91 contract regression after Bug B): one fix changed
  another's preconditions silently. Same shape as the v2 plan's
  warning that "D86 was written assuming all stale orders are
  garbage" — assumption boundaries need to be explicit.
- **Bug #13** (no exit path updates the journal): silent absence of
  required state mutation. Same shape as the launcher's
  `$stateJson.date` bug — the missing call looks like normal
  fall-through.

The state-mutation reversibility rule (f) — "every state-mutation
path needs a reversibility test" — directly applies. Today's bugs
each mutate (or fail to mutate) state without a built-in check that
the mutation matches reality. The fixes above each add such a check:
qty drift assertion, D91 step-by-step logging, journal reconciliation
against broker.

---

## Backfill (per user directive on Bug D)

Action item: scan last 30 days of `EXECUTION RECORDED` lines, pull
broker `filled_qty` for each `order_id`, flag divergences. Tracked as
appendix work; will run after the patches land. If divergences exist
in past records, the trade-journal reconciliation patch (Bug #13)
will retroactively correct P&L attribution at next-session EOD.

---

*End of findings. Patches follow in priority order: D → E → #13 → F.*

---

## Resolution Index — patches landed Wed 2026-04-22 evening

All four bugs closed. Each entry: production files modified, test
file with count, key behavioral change. Patches sit on the
`funny-hoover` worktree pending the user-driven git commit.

### Bug D — Resolution

**Production changes**
- `src/execution/bridge.py` — added module-level `TERMINAL_ORDER_STATES`
  / `TERMINAL_SUCCESS_STATES`, `_poll_for_terminal_fill()`,
  `_assert_qty_matches_broker()`, plus `ExecutionBridge._schedule_qty_drift_checks()`
  scheduled at T+5/30/60s after every position open. Replaced the
  inline 65-line poll loop (was lines 209–274) with delegation to
  `_poll_for_terminal_fill`. Poll budget bumped 3→6 (max wait 6s→12s).
- `src/execution/alpaca_executor.py` — `partially_filled`-at-submit
  branch (was lines 307–325) no longer overwrites ordered qty with
  submit-time `filled_qty`; the bridge poll loop now drives partial
  fills to terminal.
- `src/execution/position_manager.py` — touched only by Bug E (no Bug D
  changes here).

**Test file**
- `tests/unit/test_d217_partial_fill_handling.py` — **9/9 PASS**. Covers
  T1-T4 (poll-loop semantics on partial vs terminal), T5 (D218 drift
  warning + reconcile), T6 (AGPU exact 14:51:26→14:51:31 sequence
  reproduction → 846 not 505), T7 (executor partial-at-submit preserves
  ordered qty).

**Behavioral guarantee.** A partial fill (`filled_avg_price > 0` but
`status == "partially_filled"`) no longer terminates the poll loop.
Terminal state requires `status ∈ {filled, done_for_day, canceled,
expired, rejected, replaced}`. Late terminal fills (after the 12s poll
budget) are caught by the T+5/30/60s D218 QTY_DRIFT scheduled
assertions which auto-reconcile internal qty to broker truth and emit
a labeled warning.

### Bug E — Resolution

**Production changes**
- `src/execution/bridge.py` — added `_PREMARKET_OPEN_HOUR_ET = 4`,
  `_is_overnight_position(opened_at, now_et)` (pure function, decoupled
  from session_state), `filter_eligible_for_eval(positions)` (skips
  `close_pending=True`), and `_close_overnight_position(client, pm,
  ticker)` with explicit `D91 STEP 1/2/3` logging.
- `src/execution/position_manager.py` — added `close_pending: bool =
  False` field on `ManagedPosition`.
- `main.py` — D91 detection block (was ~lines 1147–1182) now uses
  `_is_overnight_position` against today's 04:00 ET pre-market boundary,
  tags detected positions with `close_pending=True`. The 09:30 close
  routine (was ~lines 1782–1809) now delegates to
  `_close_overnight_position` for step-by-step instrumentation; state
  manager update wrapped to be non-fatal.

**Test file**
- `tests/unit/test_d91_overnight_close.py` — **12/12 PASS**. Covers
  T1-T3 (detection by opened_at, NOT session_state), T4
  (`close_pending` field exists + writable), T5-T7 (step-by-step close
  routine — cancel-then-sell sequencing, missing-stop case,
  cancel-failure-still-sells case), T8 (eval queue filter respects
  flag), T9 (ELSE Tue 21:48 → Wed 09:25 exact replay).

**Behavioral guarantee.** Detection no longer depends on `session_state
is None`. Any position with `opened_at < today_04:00_ET` is detected as
overnight regardless of restart history, tagged `close_pending=True`
(eval queue filter excludes), then closed at 09:30 via three labeled
log lines (`D91 STEP 1`, `2`, `3`) so the trace is greppable per ticker.

### Bug #13 — Resolution

**Production changes**
- `src/analysis/trade_journal.py` — appended three module-level
  helpers: `summarize_from_broker_fills(fills)` derives Closed count +
  Total P&L from the broker fill ledger as single source of truth;
  `emit_pnl_reconciliation(...)` compares journal vs broker and emits
  `D222 PNL_RECON` warning on >$1 divergence with per-ticker
  breakdown; `run_pnl_reconciliation(client, journal_pnl, ...)` is the
  EOD entrypoint, non-fatal on broker-API failure.
- `main.py` — EOD trade-journal summary block (was ~lines 6608–6624)
  now calls both `summarize_from_broker_fills` (for Closed/P&L numbers,
  with `source=broker` tag) and `run_pnl_reconciliation` (for the D222
  divergence check). Falls back to journal-only with a labeled warning
  if the broker fetch fails.
- New constant: `PNL_RECON_TOLERANCE_USD = 1.00`.

**Test file**
- `tests/unit/test_d222_journal_pnl_reconciliation.py` — **8/8 PASS**.
  Covers T1-T3 (broker-fill-derived counts including partial-close
  semantics), T4 (today's three closures replay → `closed_count == 3`,
  `total_pnl ≈ +$328.80`), T5-T6 (D222 warning fires only on >$1
  divergence), T7 (per-ticker breakdown identifies missing tickers),
  T8 (broker outage is non-fatal).

**Behavioral guarantee.** The Closed count and Total P&L on the EOD
summary now derive from broker fills; AGPU's BAR-1 exit (which never
called `record_close`) will count correctly going forward. Any future
exit path that forgets to update the journal triggers the D222
PNL_RECON warning at EOD with the specific ticker named.

### Bug F — Resolution

**Production changes**
- `src/execution/bar1_exit_logging.py` — new module. Centralizes
  `ARENA_EXPECTED_MEAN_USD = 5.11` and `ARENA_EXPECTED_UPLIFT_PCT =
  299.0` as named constants. Provides `format_arena_expected_line(...)`
  (pre-exit, qualified annotation) and `format_arena_actual_line(...)`
  (post-exit, realized per-trade P&L tagged `Arena_actual=$X.XX`).
- `main.py` — D146 BAR-1 EXIT log site (was line ~4301) replaced with
  call to `format_arena_expected_line`; new post-exit block emits
  `format_arena_actual_line` after `close_with_attribution` succeeds.

**Test file**
- `tests/unit/test_bug_f_arena_expected_actual.py` — **6/6 PASS**.
  Covers T1 (pre-exit qualifier), T2 (post-exit realized P&L formatting
  including negative + zero cases), T3 (three trades produce three
  distinct `Arena_actual` values — the literal anti-regression for
  today's bug), T4 (constants centralized as named module attributes,
  not magic literals).

**Behavioral guarantee.** The pre-exit log line is now explicitly
labeled `Arena_expected: +$5.11 (+299%) [backtest mean]`. Each fired
exit also emits a separate `D146 BAR-1 ACTUAL` line carrying
`Arena_actual=$X.XX` with the realized per-trade P&L. Three distinct
trades produce three distinct lines, structurally indistinguishable
from each other in form but with different values.

### Total test surface delta this session

| Bug | Tests added | Pass count |
|-----|-------------|-----------|
| D   | `test_d217_partial_fill_handling.py` | 9/9 |
| E   | `test_d91_overnight_close.py`        | 12/12 |
| #13 | `test_d222_journal_pnl_reconciliation.py` | 8/8 |
| F   | `test_bug_f_arena_expected_actual.py` | 6/6 |
| **Total** |                              | **35/35** |

