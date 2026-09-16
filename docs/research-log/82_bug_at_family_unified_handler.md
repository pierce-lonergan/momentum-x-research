# 82 — Bug AT family: architectural synthesis + unified post-fill handler proposal

**Status:** plan, 2026-04-29 PM. Sequel to docs 80 (Bug AS root cause + AT-1/AT-2 escalation), 81 (adverse-selection on real ticks), and the deferred Bug AT (Phase 2 fill-handler bypass) from doc 71 §6.3.

**Severity:** **architectural.** Three bugs in the registry (AT-1 limit-as-fill, AT-2 schema gap, AT-bypass missing Phase 2 handler) are three symptoms of one design issue: post-fill bookkeeping is duplicated inline at 6 entry call sites and has drifted independently. Tonight's session shipped (a) 3 xfail guards in `tests/unit/test_d278_exit_policy.py` so any regression is loud, and (b) the `AdverseSelectionSampler` arena module that the eventual unified handler can use. This doc proposes the surgical refactor that closes the family.

---

## §0 — TL;DR

Six entry call sites in `main.py` each carry their own copy of post-fill bookkeeping (BAR-1 EXIT scheduler, D278 SKIPPED log, stop registration, state-mgr update, tranche register, position-manager add). The copies have drifted:

| Path | LOC | D215 fill price | BAR-1 / D278 gate | Phase 2 handler runs? | Bugs |
|---|---|---|---|---|---|
| **PHASE2_BUY** | main.py:3667 (block 3870–3965) | actual | ✅ yes | ✅ yes | none |
| **RESCAN** | main.py:6748 | trade_context.requested_px (limit) | ❌ no | ❌ no | AT-2 + bypass |
| **VWAP_BREAKOUT** | main.py:6321 | trade_context.requested_px (limit) | ❌ no | ❌ no | AT-2 + bypass |
| **FAST_PATH** | main.py:2238 | fpe.entry_price (limit) | ❌ no | ❌ no | **AT-1** + bypass |
| **D207_SHORT** | main.py:2729 | varies | ❌ no | ❌ no | bypass |
| **D161_FALLER_SHORT** | main.py:3481 | varies | ❌ no | ❌ no | bypass |
| **D170_OBSERVATION** | main.py:5118 | varies | ❌ no | ❌ no | bypass |

Of today's 11 prod-truth corpus rows: only the 5 oldest (4/22 + 4/24) ran through PHASE2_BUY cleanly. The 6 newest (OGN 4/27 + 5 from 4/28 onward) all went through RESCAN, FAST_PATH, or trade_context — every one missing parts of the Phase 2 bookkeeping.

**Recommended fix:** extract `main.py:3870-3965` into a module-level helper `await _post_fill_bookkeeping(order, verdict, scored, candidate, path, settings, ...)`, call it from all 6 call sites. Single source of truth for BAR-1 + D278 + stop registration + state save + journal record. Future fixes (and future entry paths) automatically inherit the correct contract.

**Risks identified by audit (all addressable):**
1. **Double BAR-1 fire** — Phase 3 monitoring loop also schedules BAR-1; the helper must set `_pos._bar1_exit_fired = True` to prevent the race
2. **State-mgr schema drift** — RESCAN currently writes a different schema (no `position_tier`, `kelly_tier`); helper must merge
3. **Tranche-monitor double-register** — RESCAN already registers tranches at 6852; helper must check before registering

---

## §1 — How we got here

The codebase has accumulated entry paths organically:

- **PHASE2_BUY** (oldest): the canonical post-fill block at main.py:3870-3965 with full bookkeeping
- **D207_SHORT, D161_FALLER_SHORT, D170_OBSERVATION**: short-side and observation-only paths added separately, each with their own minimal post-fill blocks
- **RESCAN, VWAP_BREAKOUT**: mid-session intraday scanners that need their own dispatch — copies of PHASE2_BUY's post-fill block were inlined, missing newer additions (BAR-1 scheduler added later, D278 even later)
- **FAST_PATH**: the most recent, most aggressive path — bypasses `bridge.execute_verdict()` entirely; calls `position_manager.add_position(fp_pos)` directly. Records D215 at submission time before the broker confirms a fill — Bug AT-1.

Each path was correct when added. None has been updated when the canonical PHASE2_BUY block was extended (BAR-1 in some prior session, D278 in commit ba030ad on 2026-04-29). The result is a slowly-growing surface where the canonical path keeps gaining features and the copies fall behind.

This is a textbook copy-paste-and-drift architectural issue. **Not a bug in any individual call site — a bug in the design that allowed copies to exist.**

---

## §2 — Proposed refactor: single `_post_fill_bookkeeping` helper

### §2.1 New module

`src/execution/post_fill_handler.py`:

```python
"""Single canonical post-fill bookkeeping handler.

Every entry call site (PHASE2_BUY, RESCAN, VWAP_BREAKOUT, FAST_PATH,
D207_SHORT, D161_FALLER_SHORT, D170_OBSERVATION) MUST call this helper
after broker fill confirmation.

Closes Bug AT-1, Bug AT-2 (schema gap separately fixed at the
trade_context emit), and Bug AT-bypass.

Per doc 82 (2026-04-29 PM).
"""
from __future__ import annotations

from typing import Any, Optional


async def post_fill_bookkeeping(
    *,
    order: Any,                  # the filled order (broker confirmation)
    verdict: Any,
    scored: Any,
    candidate: Any,
    path: str,                   # "PHASE2_BUY" | "RESCAN" | "VWAP_BREAKOUT" | ...
    settings: Any,
    bridge: Any,
    state_mgr: Any,
    position_manager: Any,
    trade_journal: Any,
    stop_resubmitter: Any,
    tranche_monitor: Any,
    exec_recorder: Any,
    logger: Any,
) -> None:
    """Single source of truth for post-fill bookkeeping.

    Steps (in order):
    1. Compute realized fill_price from order.filled_avg_price
       (NOT from any limit/signal price)
    2. Register stop with stop_resubmitter (idempotent)
    3. Schedule BAR-1 EXIT IFF settings.execution.bar1_exit_enabled
       AND not _d278_bar1_gated_off(settings)
    4. Emit D278 BAR-1 SKIPPED log if gated
    5. Save state via state_mgr.update_position with the canonical schema
    6. Register tranche if not already (idempotent)
    7. Record execution via exec_recorder.record_execution with REALIZED fill_price
    8. Set _pos._bar1_exit_fired flag for Phase 3 race prevention
    """
    ...  # implementation
```

### §2.2 Call site changes

Each of the 6 call sites becomes:

```python
# Before (~30-100 LOC of inline bookkeeping):
order = await bridge.execute_verdict(verdict, scored=scored)
if order is not None:
    # ... 30+ lines of stop register, state save, tranche register, ...
    # ... maybe BAR-1 schedule, maybe D278 gate, maybe state save ...
    # ... but probably missing several pieces ...

# After (single call):
order = await bridge.execute_verdict(verdict, scored=scored)
if order is not None:
    await post_fill_bookkeeping(
        order=order, verdict=verdict, scored=scored, candidate=cand,
        path="RESCAN", settings=settings, bridge=bridge,
        state_mgr=state_mgr, position_manager=position_manager,
        trade_journal=trade_journal, stop_resubmitter=stop_resubmitter,
        tranche_monitor=tranche_monitor, exec_recorder=_exec_recorder,
        logger=logger,
    )
```

For FAST_PATH (Bug AT-1 fix), the change is more substantial — the path currently records D215 at SUBMISSION time before any broker confirmation. The fix:
1. Submit the OTO (no D215 emission yet)
2. Wait for broker confirmation via the existing fill-stream / poll mechanism
3. Only after confirmation, call `post_fill_bookkeeping` with the realized fill_price

This is more than a simple call-site change — it's a **flow change**: defer the bookkeeping to after broker confirmation. Risk: if the FAST_PATH WebSocket fill stream is not reliable, the position might be held in the broker but not in the bot's state. Mitigation: hard timeout (e.g. 30s) after which the bot polls broker via REST as a fallback.

### §2.3 Migration sequence (recommended)

Per the audit's risk assessment, do these in order:

**Phase 1 — Test scaffolding (1 session):**
- Tests already added tonight: 3 xfail guards in `tests/unit/test_d278_exit_policy.py` for RESCAN, VWAP_BREAKOUT, FAST_PATH
- Add: integration test that drives a RESCAN fill and asserts `D278 BAR-1 SKIPPED` log emission with `t1_next_open` policy
- Add: integration test for FAST_PATH that asserts D215 is emitted ONLY after fill confirmation, with realized price

**Phase 2 — Extract helper (1 session):**
- Create `src/execution/post_fill_handler.py` with the unified function
- Implementation cribbed from PHASE2_BUY block; expanded to handle the path-specific differences (state schema, tranche registration check)
- Phase 2's `main.py:3870-3965` becomes a single helper call. Test: existing D278 + bug AS tests still pass.

**Phase 3 — Migrate call sites (1 session each, in priority order):**
1. **RESCAN** (highest impact today — every intraday-scanner fill): replace inline block with helper call. Test: RESCAN xfail flips to pass.
2. **VWAP_BREAKOUT**: same. Test: VWAP xfail flips to pass.
3. **FAST_PATH**: defer D215 emission; helper called only after broker confirmation. Test: FAST_PATH xfail flips to pass; explicit broker-confirmation test added.
4. **D207_SHORT, D161_FALLER_SHORT, D170_OBSERVATION**: short-side helpers may need a separate variant (no BAR-1 EXIT for shorts). Defer if time-constrained; the priority for the current EP pivot is long-side correctness.

**Phase 4 — Schema fix (Bug AT-2, 1 session):**
- Add `terminal_filled_avg_price` column to the trade_context orders.parquet schema
- Update `extract_prod_qty_truth.py:97` to prefer `terminal_filled_avg_price` over `requested_px` when present
- Migration: existing legacy rows lose precision; backfill from `scripts/pull_broker_truth.py` for the tickers/dates we care about

---

## §3 — What's already in place tonight (Phase 0 of the migration)

### §3.1 `mx-arena/arena/adverse_selection_sampler.py`

Probabilistic adverse-selection sampler built from `data/calibration/adverse_selection_curves.parquet` (doc 81). 13/13 unit tests pass. Three-level fallback:

1. (price_tier, T_minutes) cell with n ≥ 3 → uniform draw
2. n < 3 → fall back to global pool at T (any tier)
3. global pool empty → 0.0 (identity, no adverse modeled)

Deterministic with seed. Returns one of the actually-observed cohort values (no parametric fit, given the small cohort size).

This is what the eventual unified handler will use to model time-dependent adverse-selection in arena's modeled-exit fidelity.

### §3.2 `tests/unit/test_d278_exit_policy.py` extended

3 new xfail-marked tests:

- `test_main_py_rescan_call_site_uses_d278_helper` — asserts RESCAN block has `_d278_bar1_gated_off` or calls `_post_fill_bookkeeping`. Currently xfail (RESCAN block does neither). When the refactor lands, this auto-flips to pass.
- `test_main_py_vwap_breakout_call_site_uses_d278_helper` — same for VWAP_BREAKOUT
- `test_main_py_fast_path_records_actual_fill_not_limit` — asserts FAST_PATH does NOT use `fill_price=fpe.entry_price`. Currently xfail (FAST_PATH does exactly that). When AT-1 is fixed, auto-flips to pass.

`xfail strict=False` means the tests don't break the suite today, but if the underlying code is fixed, the test will report `XPASS` and we'll know to remove the xfail marker.

### §3.3 What is NOT yet done

- The actual extraction of `main.py:3870-3965` into a helper (Phase 2)
- The call-site migrations (Phase 3)
- The trade_context schema change (Phase 4)
- Wiring `AdverseSelectionSampler` into arena's fill model (separate from this refactor)

---

## §4 — Operator decision points

1. **Approve Phase 2 (extract helper)** for next session? It's a single-session refactor, well-scoped, with test coverage already in place tonight.
2. **Approve Phase 3 (migrate call sites)** in the priority order above? Phase 3 step 1 (RESCAN) closes the AT-bypass for today's most common entry path.
3. **Approve Phase 4 (schema change)** for after Phases 2-3? Schema change requires migration logic for legacy parquet files.
4. **Acceptable to leave short-side paths (D207, D161, D170) for last?** They're lower priority because the current EP pivot is long-only.
5. **FAST_PATH timeout policy**: when broker confirmation doesn't arrive, do we (a) silently abort the recording, (b) fall back to REST poll, or (c) emit a degraded D215_PROVISIONAL? Recommend (b) with a 30s timeout.

---

## §5 — What this enables long-term

Once the unified helper ships:

- **Adding D279, D280, etc.** (future per-position policy gates) means editing one function, not six
- **Wiring `AdverseSelectionSampler` into modeled exits** is a single-point-of-change in the helper
- **Bug AS-class issues** (limit-as-fill recording) become structurally impossible because the helper takes a confirmed `order.filled_avg_price`, not a limit
- **Test coverage scales linearly with paths** instead of multiplicatively — one helper × N path-specific assertions vs N copies × N gates

The 6 inline copies are technical debt that's been quietly accumulating. The Polygon tick data made it visible (Bug AS) but it's been there for months. Tonight's xfail tests + this doc make the debt explicit and small to repay.

---

## §6 — Status

- ✅ Bug AT family root cause identified (6 drifted call sites)
- ✅ Arena adverse-selection sampler shipped (`mx-arena/arena/adverse_selection_sampler.py`, 13/13 tests pass)
- ✅ D278 source-grep tests extended to RESCAN, VWAP_BREAKOUT, FAST_PATH (3 xfail guards)
- ✅ This doc — refactor proposal + risk + sequencing
- ⏳ Phase 2-4 implementation deferred to operator decision

**Discovery rate: 35/0 holds.** No production code changes attempted; tonight is all defensive infrastructure (sampler + test guards + plan doc).
