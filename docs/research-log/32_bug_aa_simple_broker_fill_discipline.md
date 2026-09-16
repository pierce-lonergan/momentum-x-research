# 32 — Bug AA: SimpleBroker oracle fill discipline (Track C surfacing)

**Status:** patched 2026-04-25 morning. Track C Phase 2 first PBT-surfaced bug.
**Predecessors:** `31_simple_broker_spec.md`, `26_d_code_registry.md` D248-D255 reservations.
**Bug class:** oracle bug (not production code), surfaced by tightening I8 equity_conservation invariant.

---

## TL;DR

Hypothesis shrunk a 3-rule sequence (out of an 11-rule grammar) to prove that SimpleBroker — the audit-complete oracle from Phase 1 — accepts fills that drive `broker.equity` negative. Two distinct manifestations of the same root cause:

| Manifestation | Counterexample | Equity outcome |
|---------------|----------------|----------------|
| **AA.1** limit-price discipline | `r1_submit_entry(AAPL, qty=990, entry=$1, stop=$1)` → `r3_full_fill(price=$102)` → `r8_trigger_stop` | -$89 |
| **AA.2** buying-power overshoot | `r1_submit_entry(AAPL, qty=971, entry=$104, stop=$1)` → `r3_full_fill(price=$104)` → `r8_trigger_stop` | -$13 |

Both fixed in `tests/property/simple_broker.py`. Plus an upstream R1 risk-check mirror (production discipline) added to `tests/property/test_bridge_state_machine.py` so the harness rejects pathological position sizing the way real trading code would.

---

## How it surfaced

The minimum-viable Track C state machine (3 rules, 2 invariants) shipped clean at 500 examples. Phase 2 expansion added R4-R11 + I2-I9. Initial 2000- and 10000-example passes also clean — but the I8 invariant was overly loose (`equity bounded by initial × 100` and no negative check).

Tightening I8 to:
```python
assert eq > 0, "Knight-Capital-class silent loss exceeds entire account"
assert eq < self.initial_equity * 5, "broker accounting bug"
```

…immediately produced the AA.1 counterexample at HYP_MAX_EXAMPLES=2000. Patching the limit-price discipline produced AA.2 (different mechanism, same I8 firing). Patching that produced no further counterexamples through HYP_MAX_EXAMPLES=10000.

**Key observation:** the original I8 was bug-coverage theatre. A test that says "fail if equity exceeds 100x initial" detects only complete runaway. Sharpening to "fail if equity goes negative" — a constraint every real broker enforces — surfaced two latent oracle bugs in a single deeper pass.

---

## Root cause AA.1 — limit-price discipline missing

**Pre-fix:** `SimpleBroker.partial_fill(order_id, qty, price)` accepted any `price` the rule passed without checking against `order.limit_price`. A buy-limit @ $1.00 could be filled at $102.

**Real broker behaviour:** Alpaca rejects fills outside the limit price. Buy-limit fills must be at-or-below the limit; sell-limit fills must be at-or-above.

**Fix:**
```python
if order.type == "limit" and order.limit_price is not None:
    if order.side == "buy" and price > order.limit_price:
        raise BrokerError(f"Bug AA: buy-limit @ {order.limit_price} cannot fill at {price}",
                          status_code=422)
    if order.side == "sell" and price < order.limit_price:
        raise BrokerError(...)
```

---

## Root cause AA.2 — buying-power overshoot

**Pre-fix:** `SimpleBroker.submit_order(payload)` accepted any qty/price combination without checking against `cash * margin`. A 971 @ $104 = $100,984 buy on a $100,000 cash account was accepted, fills executed at the limit, and the stop @ $1 liquidated the entire account.

**Real broker behaviour:** Alpaca rejects with `403 insufficient buying power` when notional > buying_power.

**Fix:** added at the top of `submit_order`:
```python
if side == "buy":
    est_price = (
        float(payload["limit_price"]) if payload.get("limit_price")
        else float(payload["stop_price"]) if payload.get("stop_price")
        else None
    )
    if est_price is not None:
        buying_power = self.cash * 4
        notional = qty * est_price
        if notional > buying_power:
            raise BrokerError(..., status_code=403)
```

---

## Production-mirror addition: R1 risk-check

The buying-power check (4× margin) is loose enough that pathological stop placement still produces account-wiping risk: 971 @ $104 with a $1 stop is $100,013 of dollar-risk on a $100k account, well within the $400k buying-power envelope.

Production `bridge.execute_verdict` calls `RiskManager` upstream of submit, which rejects positions whose dollar-risk exceeds a per-position cap. Mirror this discipline in R1:

```python
dollar_risk = qty * max(entry - stop, 0.01)
if dollar_risk > self.initial_equity * 0.05:
    return ""  # production rejects upstream
```

This is **defense in depth**: I8 catches the degenerate state if the harness or production code ever bypass the upstream check; R1 prevents the harness from generating sequences that production would never submit.

---

## Regression coverage

Three layers, all in `tests/property/test_invariant_injection.py`:

1. `test_bug_aa_three_rule_sequence_no_negative_equity` — pinned unit test reproducing the AA.1 sequence by direct rule invocation. Fails fast (~0.4s) if Bug AA regresses, independent of the `.hypothesis/` example database.
2. `test_i8_fires_on_implausible_equity_drift` — proves I8 actively catches negative equity (existing).
3. The state-machine PBT itself, now passing at HYP_MAX_EXAMPLES=10000 with `--hypothesis-seed=0`.

Hypothesis also persists the shrunk counterexample in `.hypothesis/examples/` for replay during `Phase.reuse` on every subsequent run — automatic regression even without the unit test.

---

## What this proves about the harness

**Per the playbook §6.1 discipline**: a state-machine bug-hunter must be able to surface bugs the spec didn't anticipate, not just those the spec enumerated. The Bug-AA finding pattern:

1. Spec audit was complete (per `31_simple_broker_spec.md` §4 — every shipped bug had a mapped rule sequence + invariant).
2. Initial implementation was clean against the audited bugs.
3. Tightening I8 from "catches runaway" to "catches negative equity" surfaced two oracle bugs the spec audit had missed.
4. Each fix shrank to a 3-rule sequence with high readability — the canonical PBT outcome.

**Discovery rate this session: 1 oracle bug (AA.1 + AA.2 = same root cause = 1 bug class). Total this week: 10. Patches introduced: 0.** Ratio holds at 10/0 = ∞.

---

## Knock-on items deferred

- **Mutation canary tests** (`tests/property/test_mutation_canaries.py`): subclass with intentionally-buggy R9/R10/R11, expect AssertionError from I7/I6/I9 within a bounded search budget. Demonstrates the harness catches Bug V/W/Z classes via search, not just direct injection. Deferred — Bug AA fix consumed this loop iteration.
- **Risk-check parameter sourcing**: the 5% cap is hard-coded. Production reads from a config file. Future iteration: thread the actual production RiskManager into R1.
- **AA.2 short-side check**: the buying-power check currently only fires for buys. Shorts have a different margin formula. Defer until R1 grows a short-side variant.
