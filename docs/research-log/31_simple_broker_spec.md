# 31 — SimpleBroker Reference Model Spec (Track C, Hypothesis state machine)

**Status:** specification only. NO implementation in this document.
**Predecessors:** `25_bug_hunting_playbook.md` §2 (Hypothesis state-
machine pattern), `28_track_a_summary.md` (handoff to Track B/C),
`29_eod_bug_findings_d24.md` + `30_bug_w_lidr_evidence.md`
(Bug V/W as the load-bearing motivation).
**Created:** Fri 2026-04-24 evening, items 16-19 of next-actions list.

## Purpose

This spec defines the API surface, state, invariants, rules, and bug-
corpus audit that the Track C `RuleBasedStateMachine` test will exercise
against the production execution stack (`bridge.py` + `alpaca_executor.py`
+ `position_manager.py`). The SimpleBroker is the **oracle** — if it
contains bugs, the invariants check against fiction.

**Design principle.** Specification is cheap and unblocks the later
sprint. Implementation is non-trivial but bounded once the spec is
audited against every bug in the corpus. Spec-first discipline is
non-negotiable here per item 20 of the next-actions list.

---

## §1 — SimpleBroker API surface

The SimpleBroker is a deterministic, in-process Python class that
mimics Alpaca's broker semantics with enough fidelity to drive the
production code through realistic interleavings.

### State held

```python
class SimpleBroker:
    # Per-order state
    orders: dict[str, OrderRecord]
        # parent OTO, children stop+limit, standalone limits, market sells
        # OrderRecord includes: id, symbol, side, qty, type,
        #   limit_price, stop_price, time_in_force, status, parent_id,
        #   filled_qty, filled_avg_price, submitted_at, terminal_at,
        #   cancel_requested_at, child_order_ids
    
    # Per-ticker state
    positions: dict[str, PositionRecord]
        # Synthesized from filled orders: net qty (long-positive,
        # short-negative), avg_entry_price, side
    
    # Halt state
    halted_tickers: set[str]
    
    # Account
    equity: float
    cash: float
    
    # Internal fill scheduler — deterministic via injected RNG seed
    _scheduled_fills: list[ScheduledFill]
        # Hypothesis-controlled; rules enqueue these
```

### Methods the production code calls

| Method | Signature | Spec |
|--------|-----------|------|
| `submit_order(payload)` | dict → dict | Accepts the Alpaca-shape payload (incl. OTO with stop_loss leg). Creates the parent `OrderRecord` plus child `OrderRecord(s)` for stop_loss / take_profit legs. Returns the parent dict with `id`, `legs`, `status="accepted"`. |
| `cancel_order(order_id)` | str → dict | Marks the order `status="canceled"`. If it's a parent OTO, cancels all child orders too (mimics Alpaca behaviour). Idempotent — calling twice on a canceled order returns success. |
| `get_orders(status, limit, symbols)` | kwargs → list[dict] | Returns a paginated, status-filtered slice of `orders`. Most recently submitted first. |
| `get_positions()` | () → list[dict] | Returns the non-flat positions. Side-aware (long vs short). |
| `get_account()` | () → dict | Returns equity, cash, buying_power, daytrade_count. |
| `close_position(symbol)` | str → dict | Submits a market sell of net qty for `symbol`. Returns the new sell order's dict. |
| `get_latest_quote(symbol)` | str → dict | Returns synthetic NBBO (bid, ask, sizes) — Hypothesis-controlled. |
| `get_account_activities()` | () → list[dict] | Optional method (D24 Bug N — deliberately absent on real `AlpacaDataClient`). SimpleBroker exposes it for richer test scenarios; production call site must use `hasattr` (Bug N fix discipline). |

### Methods the test rules call (NOT in production code)

These drive the broker's internal state directly to simulate broker-
side events that the production code observes through the standard
methods above.

| Method | Spec |
|--------|------|
| `partial_fill(order_id, qty, price)` | Increments `filled_qty` for the order; sets `status="partially_filled"`. Updates the corresponding child stop leg (OTO arm-on-fill semantics). |
| `terminal_fill(order_id, price)` | Fills the residual; `status="filled"`. Activates the OTO stop leg. Updates `positions`. |
| `reject(order_id, reason)` | `status="rejected"`, terminal. |
| `expire_at_eod()` | All `status="new"` orders with `time_in_force="day"` flip to `status="expired"` (SimpleBroker's analogue of 16:00 ET cutoff). |
| `halt(ticker)` | Adds to `halted_tickers`; subsequent `submit_order` for that ticker returns `status="rejected"` with reason "halted". |
| `resume(ticker)` | Removes from `halted_tickers`. |
| `trigger_stop(order_id)` | Stop sells fire; activates as a market sell (synthetic close). Updates positions. |
| `set_equity(value)` | Hypothesis-controlled equity injection (for D232 lethal-tier tests). |

---

## §2 — Invariant set

Per item 17 of the next-actions list — six from the playbook plus the
Bug-V-specific invariant plus a new Bug-W-motivated one.

| # | Invariant | Failing-bug history |
|---|-----------|---------------------|
| **I1** | **`tracker_matches_broker`**: for every ticker with any internal `ManagedPosition`, `pos.qty == broker.positions[symbol].qty` | Bug D (XNDU 846/505), Bug N (journal vs broker counts) |
| **I2** | **`stop_matches_broker`**: for every ticker with `pos.stop_order_id != ""`, `pos.stop_loss == broker.orders[stop_oid].stop_price` (within $0.01) | Bug R (Phase-0 tracker drift), Bug W (exit-ladder restructure) |
| **I3** | **`no_orders_during_halt`**: if `broker.halted_tickers contains symbol`, no new `submit_order` call for that symbol succeeds | (no live bug yet; defensive) |
| **I4** | **`no_negative_position`**: `pos.qty >= 0` for long positions, `pos.qty <= 0` for shorts; never crosses through zero in a single operation | (no live bug yet; defensive) |
| **I5** | **`cumulative_fill_bounded`**: `sum(child_fill.qty for fill in order.fills) == order.filled_qty <= order.requested_qty` | Bug D (partial-fill overwrite at submit) |
| **I6** | **`late_fill_on_rejected_order_canceled`**: if bridge rejects an order (returns None from execute_verdict), within 1 logical tick the broker order's status must be `canceled` (not `new`/`accepted`/`partially_filled`) | **Bug V** ($978 silent loss yesterday) |
| **I7** | **`tranche_restructure_preserves_tightened_stop`**: after any exit-ladder restructure that cancels-and-resubmits the protective stop, the new broker stop_price equals the previous broker stop_price (within $0.01) | **Bug W** (LIDR observed today) |
| **I8** | **`equity_conservation`**: `broker.equity == initial_equity + sum(realized_pnl for closed positions) + sum(unrealized_pnl for open positions) − fees` (within $1 tolerance) | Implicit yesterday's $978 drift; would catch any "magic dollars appearing/disappearing" |
| **I9** | **`close_attempt_only_marks_closed_on_broker_2xx`**: for any bridge call to close_position, the internal tracker mutates to "closed" ONLY if the broker response status is 2xx. On 4xx/5xx/raise, the tracker MUST retain the position; the protective stop MUST remain active; the tranche limits MUST remain active | **Bug Z** (D24 LIDR catastrophe) |

**Each invariant is checked after EVERY rule transition.** If ANY
invariant fails after any rule, Hypothesis shrinks to the minimal
failing rule sequence and reports.

---

## §3 — Rule set

Per item 18 — eight rules from the playbook plus two motivated by today.

| Rule | Preconditions | State transition | Triggers invariant check |
|------|---------------|------------------|--------------------------|
| **R1 — `submit_entry(ticker, qty, entry, stop)`** | Ticker not halted; `qty * entry < broker.cash * leverage`; ticker has no active OTO | Broker creates parent OTO + stop leg (`status="accepted"`); production bridge.execute_verdict called; new `ManagedPosition` may be created depending on poll outcome | I1, I2, I3, I8 |
| **R2 — `partial_fill(order_id, qty, price)`** | Order exists, `status in ("accepted", "partially_filled")`, fill qty + already_filled <= requested_qty | Broker increments `filled_qty`; bridge poll loop may observe (depending on timing) | I1, I5 |
| **R3 — `full_fill(order_id, price)`** | Order exists, `status in ("accepted", "partially_filled")` | Broker fills residual; OTO stop leg activates; bridge poll loop observes terminal | I1, I2, I5 |
| **R4 — `reject(order_id, reason)`** | Order exists, `status not in TERMINAL_ORDER_STATES` | Broker marks `status="rejected"`; bridge sees terminal-rejected and skips position creation | I1 |
| **R5 — `cancel(order_id)`** | Order exists, `status not in TERMINAL_ORDER_STATES` | Broker marks `status="canceled"`; if parent OTO, all children canceled too | I1, I6 |
| **R6 — `halt(ticker)`** | Ticker not currently halted | Add to halted_tickers; pending OTOs for that ticker auto-rejected by broker | I3 |
| **R7 — `resume(ticker)`** | Ticker currently halted | Remove from halted_tickers | I3 |
| **R8 — `trigger_stop(order_id)`** | Order exists, `type=="stop"`, `status="new"` (i.e., active stop leg) | Broker fires the stop as market sell; updates positions; production bridge close_with_attribution may be triggered via D215 EXECUTION RECORDED loop | I1, I8 |
| **R9 — `tranche_restructure(ticker)` (Bug W)** | Ticker has open ManagedPosition with stop_order_id; restructure path is enabled (Phase 2 / VWAP / RESCAN gate met) | Production code path: `cancel_stop_and_submit_exit_ladder` fires; cancels old stop, submits new stop + tranches | I2, I7 |
| **R10 — `bridge_timeout_rejection(order_id)` (Bug V)** | Order exists, `status="accepted"`, bridge poll budget exhausted (max_polls × poll_interval) without terminal | Bridge marks the position as never-opened (returns None from execute_verdict); production code MUST cancel the broker order to prevent ghost (Bug V fix) | I1, I6 |

---

## §4 — Bug-corpus audit (item 19)

For every bug shipped (D, E, #13, F, N, T, U, Q, R, V, W), identify
the rule sequence + invariant check that catches it. If any bug is
uncovered, the rule or invariant set is incomplete.

| Bug | Rule sequence | Failing invariant |
|-----|---------------|-------------------|
| **D — partial-fill overwrites qty** | R1 → R2 (small) → R2 (more) → R3 (terminal) | I1, I5 (would fail at R3 if bridge stored partial qty) |
| **E — D91 detection by opened_at** | (out-of-scope: this is a startup-time invariant, not a state-machine rule transition. Covered by Track A item 22 pre-open broker check.) | n/a |
| **#13 — journal counts wrong** | R1 → R3 (full fill) → R8 (stop trigger close) → check journal | I1 (journal-derived qty != broker qty) |
| **F — Arena hardcoded literal** | (not a state invariant; pure log-line correctness. Covered by `test_bug_f_arena_expected_actual.py`.) | n/a |
| **N — wrong client method** | Same as #13. The PBT setup uses SimpleBroker which exposes `get_account_activities` so Bug N's specific failure mode (AttributeError on production AlpacaDataClient) wouldn't surface — **GAP**: PBT alone doesn't catch this. Static type-check + production-shape integration test required. | n/a (covered by Track A item 1 mypy strict) |
| **Q — silent BAR-1 exit-price fallback** | R1 → R3 (terminal) → wait T+60s → BAR-1 exit fires → check D146 ACTUAL log | I8 (equity_conservation: silent fallback masks real P&L; SimpleBroker's deterministic fill price would expose the divergence) |
| **R — Phase-0 stop drift** | R1 (with Phase-0 tightening) → check ManagedPosition.stop_loss vs broker stop | I2 (would fail immediately after R1) |
| **T — VWAP scan_timestamp** | (out-of-scope: schema-validation bug, not state machine. Covered by `test_d23_bugs_t_u.py`.) | n/a |
| **U — MOMENTUM_UNIVERSE import** | (out-of-scope: import-error-on-fallback-path bug, not state machine. Covered by `test_d23_bugs_t_u.py`.) | n/a |
| **V — bridge rejects without cancelling** | R1 → R10 (timeout rejection); broker order remains `status="accepted"` after bridge returns None | **I6** (would fail: late_fill_on_rejected_order_canceled) |
| **W — exit-ladder restructure drops Phase-0 stop** | R1 (Phase-0 tightened) → R3 (terminal) → R9 (tranche_restructure); new stop_price differs from old | **I7** (would fail: tranche_restructure_preserves_tightened_stop) |

**Coverage analysis:**
- 6 of 11 bugs have direct PBT coverage (D, #13, Q, R, V, W)
- 5 of 11 are out-of-scope for state-machine PBT (E, F, N, T, U) but
  covered by other test categories already in place
- **Zero bugs uncovered by some defense.** Audit complete.

---

## §5 — Implementation phasing (when sprint begins)

Per item 20: implementation begins ONLY after this spec is audited.
Audit is complete (above). Build order:

### Phase 1 — SimpleBroker oracle (~3 days)

1. `tests/property/simple_broker.py` — the deterministic broker class.
   No test logic; pure model. Audited against the bug corpus (above).
2. Unit tests for SimpleBroker itself: order lifecycle, fill semantics,
   OTO arm-on-fill, halt rejection, cancel-cascade. ~30 tests.

### Phase 2 — RuleBasedStateMachine wiring (~2 days)

3. `tests/property/test_momentum_x_state_machine.py` — Hypothesis
   state machine with the 10 rules + 8 invariants from above.
4. Integration: SimpleBroker as `_client`; production
   ExecutionBridge + AlpacaExecutor + PositionManager unmodified.
5. Run for `max_examples=1000` initial; expect counterexamples within
   first 50 examples per Hughes-Volvo published rate.

### Phase 3 — Triage + regression suite (~ongoing)

6. Every counterexample shrinks to minimal sequence; commit as
   explicit `@example()` in the test file (Hypothesis pattern).
7. Bug fixes ship under D-code reservation discipline as before.
8. After 4 weeks of clean runs (no new counterexamples on
   `max_examples=10000`), arm CI.

### Total effort

~5 days for Phase 1+2 working code; ongoing maintenance trivial. The
heavy investment is the spec-and-audit phase (this document) which is
now complete.

---

## §6 — What this spec deliberately omits

Out-of-scope to keep the sprint bounded:

- **Multi-broker simulation** (one SimpleBroker = one Alpaca paper account)
- **Latency / timing simulation** at sub-second resolution
  (Hypothesis controls logical ordering, not wall-clock timing)
- **Network failures** (covered by Track E chaos engineering, not PBT)
- **WebSocket / streaming events** (REST-poll is the audited interface;
  WS is a complement, not a substitute)
- **Cross-account / portfolio-level invariants** (single-account scope
  for v1)

These are appropriate v2 scope after the v1 PBT suite ships.

---

## §7 — D-code reservations needed for Track C ship

Already reserved per `26_d_code_registry.md`:
- D233 PBT_COUNTEREXAMPLE — fires when Hypothesis shrinks to a new
  failing case during CI; auto-commits the counterexample as a
  regression `@example()`.

No new D-codes needed for spec or implementation. **Next available
remains D241** (per the registry update from D24).

---

## §8 — Discovery-rate evidence this spec captures

Per item 22 of the next-actions list: this spec is itself a discovery-
rate-exceeds-introduction-rate datapoint. Bug W was discovered in <2h
on D24 morning via Track A's source-grep test family. The PBT state-
machine, when implemented, would catch the same bug class via I7 in
<60 generated sequences. **Two independent defenses for the same bug
class** = the antifragility property the playbook §8.5 names.

When the architecture call asks "what is the discovery infrastructure
ROI," the answer is concrete: this spec exists, the bugs it would
catch exist, the cost of building the implementation is bounded at
5 engineering days, and the protection it provides compounds for every
future patch.

---

*End of spec. Implementation begins per §5 phasing. Spec is audit-
complete and ready for Track C sprint kickoff.*
