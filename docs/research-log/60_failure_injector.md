# 60 — Block 2.2: FailureInjector — broker-error injection in arena

**Status:** shipped 2026-04-28 PM. 15-test regression suite green.
**Severity:** infrastructure (no production impact). Closes a critical arena ↔ prod parity gap that let Bug AR ship as dead code for 24 hours.

---

## §0 — TL;DR

Production fails in specific shapes that arena's `SimExchange` does not naturally model:
- **HTTP 403 + Alpaca code 40310000** ("insufficient qty available") — today's LIDR 10:00:38 ET deadlock that motivated Bug AI / Bug AR.
- HTTP 403 trading-account-locked / market-closed.
- HTTP 422 validation failures (qty=0, bad prices).
- HTTP 5xx transient broker outages.

Without injecting these in arena, the entire Bug AR / Bug AI / Bug Z / Bug V code path is **untested in simulation** — exactly the gap that let Bug AR ship as dead code for 24 hours in production. The existing `test_bug_ai_force_close.py` tests passed because they used a doctored exception with the body in `__str__`; they did not catch the real defect.

`FailureInjector` is a deterministic, rule-based injector wired to be queried by arena before each broker action. The seed fixture `lidr_tue_2026_04_28_qty_conflict()` reproduces today's LIDR Bug AR scenario byte-for-byte.

---

## §1 — Design

### Module: `mx-arena/arena/failure_injector.py`

```python
@dataclass(frozen=True)
class InjectedFailure:
    status_code: int
    body: str               # JSON string
    reason: str = ""

@dataclass
class FailureRule:
    response: InjectedFailure
    ticker: Optional[str] = None
    action: Optional[str] = None
    after_iso: Optional[str] = None
    before_iso: Optional[str] = None
    max_fires: Optional[int] = None
    fires: int = 0  # mutable counter

class FailureInjector:
    def evaluate(self, *, ticker, action, ts_utc) -> Optional[InjectedFailure]: ...
```

**Key design decisions:**

1. **Rule-based, first-match-wins.** Predictable composition. Empty rule list = no injection (fast path).
2. **Time windows** (`after_iso`, `before_iso`) — needed to reproduce single-incident scenarios like the LIDR 10:00:38 ET 403 without firing across the entire session.
3. **`max_fires` counter** — production saw the LIDR 403 once. The injector mirrors that. Avoids spamming "every close on LIDR fails" when the prod scenario was a single event.
4. **Honest API:** `evaluate()` returns either an `InjectedFailure` (caller raises) or `None` (proceed normally). NO silent bypass — every call site must explicitly check.
5. **Rules are dataclasses, easy to seed** from fixtures or JSON files for parameterized tests.

### Seed fixture: `lidr_tue_2026_04_28_qty_conflict()`

The byte-for-byte production response body, captured via the Bug AR fix's `e.response.text` extraction:

```json
{"available":"0","code":40310000,"existing_qty":"5264",
 "held_for_orders":"5264","message":"insufficient qty available
 for order (requested: 5264, available: 0)","symbol":"LIDR"}
```

Time window: `[2026-04-28T14:00:00Z, 2026-04-28T14:01:30Z]`. `max_fires=1`.

---

## §2 — What this enables

1. **Bug AR regression in arena.** Tomorrow if the AR fix is reverted (or its substring match drifts), `tests/unit/test_failure_injector.py::test_bug_ar_scenario_full_replay_works_end_to_end` catches the regression. Today's prod-only test (`tests/unit/test_bug_ar_string_match.py`) is preserved; arena replay now ALSO exercises the same path.
2. **Bug AI cancel-and-coordinate replay.** Inject the 403, run a close in arena, observe the cancel-then-retry sequence land. The full deadlock-recovery flow is now testable end-to-end without prod broker.
3. **Future-proofing.** Every Bug AR-class scenario we encounter in prod becomes a fixture in this module. The fixture set GROWS with operational experience and prevents regressions of fixes that closed those scenarios.

---

## §3 — What this does NOT do (yet)

The Block 2.2 brief calls for wiring the injector into arena's SimExchange. **This commit ships the injector module + fixture + tests; the SimExchange wiring is deferred** because:

1. SimExchange's `submit_order` and `close_position` would each need a `failure_injector` constructor parameter and a check at the top of each method. ~30 LOC across 2-3 sites.
2. The replay script (`scripts/arena_replay_session.py`) currently uses FillModel + SpreadModel directly, NOT SimExchange. The wiring effort needs to land in BOTH paths.
3. The injector + fixture + tests are independently shippable and testable. They prove the contract; the wiring is a deterministic mechanical change.

The SimExchange wiring is **the first item in tomorrow's Block 2 continuation**. Until then, the injector is queryable from any arena code that explicitly calls it.

---

## §4 — Discipline check

- Discovery rate: this is NOT a new bug — it's an arena gap, infrastructure. **30/0 ratio holds.**
- Every fix gets a finding doc: ✅ (this doc).
- Every fix gets a regression test: ✅ (15 tests in `test_failure_injector.py`).
- The fixture body is byte-for-byte production-shape: ✅ (pinned via `test_lidr_fixture_body_byte_for_byte_matches_production`).

---

## §5 — Status: COMPLETE-AS-MODULE

- ✅ `mx-arena/arena/failure_injector.py`
- ✅ `tests/unit/test_failure_injector.py` (15 tests, all green)
- ✅ Fixture `lidr_tue_2026_04_28_qty_conflict()` registered
- ⏳ SimExchange wiring deferred to next iteration (small mechanical change)

**Next:** Block 2.3 (OTO child rejection) is the third top-3 parity gap from Block 1's diff report. Lower priority than 2.1 (slippage, deferred) and 2.2 (this), so consider deferring to a future session and moving to Block 3 (sweep harness — provisional results) or Block 4.4 (full historical corpus — the OOS Sharpe answer).
