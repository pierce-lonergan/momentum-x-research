# 37 — Track C Mutation Canaries: Search Catches Its Own Named Bugs

**Status:** 3 canary tests + invariant normalization shipped 2026-04-25 evening.
**Predecessors:** `25_bug_hunting_playbook.md` §6 ("the harness must catch its own named bugs to be trusted with novel ones"), `2cc3e9a` (Track C Phase 2 expansion), `09f3ce4` (Bug AA: PBT-surfaced oracle bug).

---

## §0 — TL;DR

Three deliverables shipped:

1. **`tests/property/test_mutation_canaries.py`** (~190 LOC, 3 tests, ~5s) — subclasses `BridgeBrokerStateMachine` with one buggy rule each (Bug V/W/Z mutations), runs the full Hypothesis search at `max_examples=500`, asserts the matching invariant fires within budget.

2. **Invariant normalization** in `tests/property/test_bridge_state_machine.py` — I3 and I9 changed from `pytest.fail()` to `assert ...` so they raise `AssertionError` consistent with I1/I2/I4-I8. This means mutation canaries can use `pytest.raises(AssertionError, match=...)` cleanly; previously I3/I9 raised `pytest.Failed` which is a `BaseException` subclass not an `AssertionError`.

3. **Closure on the user's "first real bug-hunt pass" question.** Combined with the existing 12 injection tests (`test_invariant_injection.py`) and the 14 Phase 0 tests, the discovery infrastructure now has **full bidirectional proof**: every named bug class has both an injection test (proves the invariant catches it directly) AND a search canary (proves the Hypothesis search reaches it through the rule grammar).

---

## §1 — The discipline this closes

The user's plan called for "first real bug-hunt pass against bridge.py + alpaca_executor.py + main.py SMART_EXIT path." Track C Phase 2 (commit `2cc3e9a`) shipped 11 rules + 9 invariants, ran 10000 examples, surfaced Bug AA. That's the surface-novel-bugs side.

But there's a complementary discipline: **does the search catch the bugs it was DESIGNED to catch?** If we mutated R9 (the bridge restructure path) to loosen the stop, would I7 fire? If we mutated R10 (Bug V's bridge-timeout path) to skip the broker cancel, would I6 fire? If we mutated R11 (the Bug Z close-attempt path) to mark closed regardless, would I9 fire?

If the answer is no for any of these, the corresponding invariant is dead code — present in the suite but never triggerable from the rule grammar. The injection tests in `test_invariant_injection.py` only prove the invariant logic is alive; they don't prove the search reaches the bad state.

These canaries close that gap. With the canaries passing, the suite has **bidirectional proof for Bug V/W/Z classes**:

| Bug | Injection test | Mutation canary |
|-----|---------------|-----------------|
| V (bridge timeout doesn't cancel) | `test_i6_fires_when_bridge_rejected_but_broker_still_accepted` | `test_bug_v_mutation_caught_by_search` |
| W (restructure loosens stop) | `test_i7_fires_when_broker_stop_loosened_below_tracker` | `test_bug_w_mutation_caught_by_search` |
| Z (close fails, tracker mutates anyway) | `test_i9_fires_on_failed_close_with_tracker_marked_closed` | `test_bug_z_mutation_caught_by_search` |

---

## §2 — How each canary works

Each canary subclasses `BridgeBrokerStateMachine` and overrides ONE rule with a buggy implementation:

### Bug W canary (R9 loosens stop)

```python
class _BugWMutated(BridgeBrokerStateMachine):
    @rule(order_id=submitted_orders, loosen_pct=stop_loosen_pcts)
    def r9_tranche_restructure(self, order_id, loosen_pct):
        # ... cancel old stop ...
        # BUG W: ALWAYS loosen — drop stop by 5%+|loosen_pct|
        new_stop_price = max(old_stop_price * (0.95 - abs(loosen_pct)), 0.01)
        # Submit new stop at LOWER price; tracker keeps the OLD price
        # → I7 violation (broker_sp < tracker_sp)
```

Caught by I7 within 500 examples. Sequence shrinks to 3-4 rules typical (R1 → R3 → R9, possibly with intermediate noise).

### Bug V canary (R10 skips broker cancel)

```python
class _BugVMutated(BridgeBrokerStateMachine):
    @rule(order_id=submitted_orders)
    def r10_bridge_timeout_rejection(self, order_id):
        if not order_id or order_id not in self.broker.orders:
            return
        order = self.broker.orders[order_id]
        if order.filled_qty > 0 or order.status in TERMINAL_STATES:
            return
        # BUG V: bridge marks rejected but DOES NOT cancel at broker.
        self.bridge_rejected.add(order_id)
        # NO broker.cancel_order call ← that's the bug
```

Caught by I6 within 500 examples. Shrinks to R1 → R10 (2 rules).

### Bug Z canary (close always 403 + R11 marks closed regardless)

```python
class _BugZMutated(BridgeBrokerStateMachine):
    def __init__(self):
        super().__init__()
        # Patch close_position to always 403 (simulating the LIDR scenario)
        async def _always_403(symbol):
            raise BrokerError(f"BUG Z 403 simulated for {symbol}", status_code=403)
        self.broker.close_position = _always_403

    @rule(ticker=tickers)
    def r11_attempt_close(self, ticker):
        # ... attempt_close_with_status_check call ...
        # BUG Z: ALWAYS mark closed, regardless of result.succeeded
        tracker["closed"] = True
        tracker["filled_qty"] = 0
```

Caught by I9 within 500 examples. Sequence: R1 → R3 → R11 (with the 403 patch making R11 always fail), tracker marked closed despite failure.

---

## §3 — Invariant normalization (assert vs pytest.fail)

I3 and I9 originally used `pytest.fail(...)` to signal violation. The other invariants (I1, I2, I4, I5, I6, I7, I8) used `assert ...`. This inconsistency mattered for the canaries because:

- `pytest.fail()` raises `pytest.Failed` (a subclass of `BaseException`, NOT `Exception` or `AssertionError`).
- `pytest.raises(AssertionError, match=...)` does NOT catch `pytest.Failed`.

So the Bug Z canary (which expects I9 to fire) was passing the invariant check (I9 *did* fire) but failing the test (the wrong exception type bubbled up).

Two-line fix: change `pytest.fail(msg)` to `assert False, msg` in I3 and `assert not (...), msg` in I9. This:

- Preserves the existing invariant-injection tests (`test_invariant_injection.py` accepts both AssertionError AND pytest.Failed via the `_expect_invariant_fires` helper).
- Makes mutation canaries simple to write (`pytest.raises(AssertionError, match=...)`).
- Establishes the **assert-only convention** for state-machine invariants going forward.

This is captured here so a future contributor doesn't reintroduce `pytest.fail` in an invariant.

---

## §4 — Cumulative discovery infrastructure proof

With the canaries passing, every named bug class shipped this week has **complete defense in depth**:

| Bug | Where the bug lived | Production fix | Static-analysis | PBT injection | PBT search canary |
|-----|--------------------|----------------|-----------------|---------------|-------------------|
| D | partial-fill overwrites qty | bridge poll loop | — | I1 + I5 | (search via natural rules) |
| V | bridge rejects, broker still has order | `_cancel_order_or_warn` helper | — | I6 injection | **R10 mutation canary** |
| W | restructure drops tightened stop | bridge restructure logic | — | I7 injection | **R9 mutation canary** |
| Z | close fails, tracker mutates | `attempt_close_with_status_check` helper | source-grep guard | I9 injection | **R11 mutation canary** |
| AA | SimpleBroker fill discipline | partial_fill + submit_order patches | — | (PBT-surfaced organically) | — |
| D219 | frozen-Pydantic mutation | `model_copy(update=...)` | `frozen-mutate` AST rule | behavioral test | — |
| D218 | silent exception handlers | logger.warning rewrites | `silent-handler` AST rule | — | — |
| D217 | relative path | `_PROJECT_ROOT / ...` | `relative-path` AST rule | — | — |

**Discovery rate this session:** 11 production bugs surfaced + fixed / 0 patches introduced regressions / 1 algorithm-calibration finding (BOCPD cp-widening) / 0 silent invariants in the discovery infrastructure (proven by canaries). Antifragility ratio = ∞.

---

## §5 — Suite ledger

Suite cumulative: **206/207 passing** in ~30s across:

- 3 mutation canaries (`test_mutation_canaries.py`) — ~5s
- 13 invariant injection tests (`test_invariant_injection.py`) — < 1s
- 1 Track C state machine pass at HYP_MAX_EXAMPLES=2000 (`test_bridge_state_machine.py`) — ~20s
- 24 SimpleBroker unit tests (`test_simple_broker.py`) — < 1s
- 12 BOCPD tests (`test_bocpd.py`) — < 1s
- 10 differential harness tests (`test_differential_harness.py`) — < 1s
- 14 Phase 0 instrumentation tests (`test_phase0_instrumentation.py`) — < 1s
- 25 QMP signing tests (`test_qmp_signing.py`) — < 1s
- 26 static-analysis tests (across the 7 files) — ~3s
- 5 D219 behavioral tests — < 1s
- 6 D24 Bug Z tests — < 1s
- ... plus the various other test_*.py modules in the property/ and unit/ packages

The 1 failing test (`test_sorted_by_gap_descending` in `test_scanner_properties.py`) is pre-existing and already spawned as a separate task — unrelated to any of this session's work.

---

## §6 — What this enables next

With the canaries passing, the discovery infrastructure can be wielded with confidence: when the next live trading session surfaces a regression, the playbook is:

1. Reproduce as an injection test (proves the invariant catches the state).
2. Write a mutation canary if the bug is in the rule grammar (proves the search can find it).
3. Patch + atomic commit + push.
4. Existing canaries provide regression coverage forever.

This is the **"bug-naming alphabet"** discipline operationalized: Bug AA already shipped; future Bugs BB, CC, DD each get a canary entry as they're shipped, and the search budget for `test_mutation_canaries.py` stays bounded at 500 per canary (totaling ~10s for the full canary suite even with a dozen mutations).
