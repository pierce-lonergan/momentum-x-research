# 47 — Bug AH: Track B daemon TypeError on every tick (`run_eod_invariants` returns None)

**Status:** patched 2026-04-27 evening (post-close).
**Severity:** **HIGH** — Track B continuous broker-vs-internal reconciliation daemon was offline for the entire trading session (09:31:01 ET → 16:00:41 ET, ~6.5 hours).
**Surface:** **LIVE PRODUCTION SESSION** — observed in `logs/momentum_2026-04-27.log` once per 30-second tick. Crash was non-fatal (caught by `except Exception` in `run_forever`) so the daemon's outer loop kept retrying — and crashing — silently in the background.
**Bug class:** function declares return type `dict[str, Any]` and a docstring promising a dict, but the implementation falls off the end of the function body without a `return` statement → implicitly returns `None`. Caller subscripts the None with `recon["qty_drift_count"]` → `TypeError`.
**Predecessors:** `44_bug_ag_track_b_unbound.md` (Bug AG launched the daemon successfully — this bug is what kills it on the first tick), `43_bug_af_uncond_cand_reference.md` (canonical UnboundLocalError fix pattern).

---

## §0 — TL;DR

```
src/monitoring/eod_recon.py:316  ← function ends here without `return result`
```

The `run_eod_invariants` async function builds a `result: dict[str, Any]` at line 104, mutates it through 200 lines of invariant checks, logs a summary at line 307-316 — and **never returns it**. The function falls off the end and Python implicitly returns `None`.

Every caller assumed a dict and crashed at the first attribute access:

```
src/monitoring/recon_daemon.py:167
    self.state.last_qty_drift_count = recon["qty_drift_count"]
                                      ~~~~~^^^^^^^^^^^^^^^^^^^
TypeError: 'NoneType' object is not subscriptable
```

One-line fix: add `return result` at the end of the function.

---

## §1 — Root cause

`src/monitoring/eod_recon.py` `run_eod_invariants(...)`:

```python
async def run_eod_invariants(...) -> dict[str, Any]:
    """
    ...
    Returns:
        {
          "broker_reachable": bool,
          "qty_drift_count": int,
          ...
        }
    """
    result: dict[str, Any] = {
        "broker_reachable": True,
        "qty_drift_count": 0,
        ...
    }
    # ... 200 lines mutating result ...
    logger.log(summary_level, "EOD RECON SUMMARY: ...")
    # END OF FUNCTION — no return statement
```

Both the type annotation and the docstring promise a dict. The implementation builds and uses one. But the actual `return result` statement was never added (or was deleted in some refactor and never restored).

The Python type system catches this only with strict-mode analyzers — pyright's `reportReturnType = "warning"` would have caught it, but our `pyrightconfig.json` has it set to `"none"` to keep the gate focused on the AF/AG class. Mypy at the project's current strictness also doesn't flag this.

The 30-second `run_forever` loop in `recon_daemon.py` wraps every tick in a broad `except Exception`, so the crash was silently logged and the loop continued. The daemon appeared "alive" (the log line "Track B daemon: tick raised unexpectedly" repeated every 30s) but produced zero useful reconciliation work.

---

## §2 — Why the bug was latent

Two reasons it survived undetected until 2026-04-27:

1. **Test coverage masked it.** `tests/unit/test_d230_eod_recon_invariants.py` contains 8 tests that call `run_eod_invariants` and subscript the result. **4 of those tests have been failing silently** with the same `TypeError`. Either:
   - The CI step that runs unit tests was excluding this file, OR
   - The failures were never noticed because they happened in a flaky/skipped suite

2. **The daemon's defensive outer try/except.** `recon_daemon.py:140-144`:
   ```python
   except Exception as e:
       logger.error(
           "Track B daemon: tick raised unexpectedly (%s) — "
           "continuing loop", e, exc_info=True,
       )
   ```
   This was correct defensive engineering — a single tick crashing should not kill the daemon. But it also silenced what would otherwise have been a fatal error visible at process start.

The combination of "tests not catching it" + "production swallows the exception" let the bug live in main for an unknown duration.

---

## §3 — How it surfaced

**Direct observation by Pierce.** Monday 2026-04-27, post-close (~17:00 ET), reviewing the day's log for "why no trades" turned up the repeated `ERROR | Track B daemon: tick raised unexpectedly` line firing at every 30-second tick. The bug was not surfaced by any test layer; it was surfaced by **operator log-reading discipline** — same as Bug AG this morning.

Time-to-detection: ~6.5 hours after first crash (limited by operator schedule, not the system).

---

## §4 — Fix

Single-line change:

```python
    logger.log(
        summary_level,
        "EOD RECON SUMMARY: broker_reachable=%s qty_drift=%d stop_drift=%d "
        ...
    )

    # Bug AH fix (2026-04-27): function used to fall off the end here,
    # implicitly returning None. The docstring (lines 93-102) and the
    # type annotation (`-> dict[str, Any]`) both promised a dict.
    # ...full comment in the source...
    return result
```

Plus the 4 silently-failing unit tests in `test_d230_eod_recon_invariants.py` now pass.

---

## §5 — How this would have manifested in production

Track B's three escalation tiers (D230 warn / D231 hard-block / D232 lethal) are designed to catch broker-vs-internal divergence within 30 seconds. With Track B dead all day:

- **D231 RECON_HARD_BLOCK on OGN** fired only via the DIFFERENT one-shot EOD recon path (which is called from main.py:7013 and apparently DOES return None and crash too — but the crash there is caught by main.py's outer error handler). The hard-block log line ("internal_qty=999 broker_qty=MISSING") appeared in the log but no automated remediation occurred.
- **Stop drift on LIDR** (internal_stop=$2.29 vs broker stop=$2.10 from operator intervention) was detected by D230 but no Track B tier-2 escalation surfaced it loudly.
- **Equity drift** (`internal_estimate=$0.00 delta=$+140470.37`) — same story.

The operational outcome: **the LIDR position drained $1,316 unrealized** while the system tried (and failed, see Bug AI/AJ to follow) to close it 5+ times. Track B operating correctly would have surfaced the deadlock to Discord/heartbeat within the first 30 seconds, allowing operator intervention 6 hours earlier.

Worst case: a Knight-Capital-class broker-vs-internal divergence that grows unchecked for an entire trading day because Track B can't see it.

---

## §6 — Discovery rate impact

| Pre-Bug AH | After Bug AH |
|---|---|
| 19 production / oracle / harness bugs surfaced + fixed | **20** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **20/0 = ∞** |

Bug AH is the **second bug surfaced live in a paid session** (Bug AG was the first, this morning). Both fixed within 24 hours of surfacing. Bug AG was a 2-line hoist; Bug AH is a 1-line `return result` add.

---

## §7 — What changes downstream

1. **Track B daemon resumes operating** on next process restart. The 30-second continuous reconciliation comes online. D230/D231/D232 escalations work as designed.

2. **4 previously-silent unit-test failures now visible.** `test_d230_eod_recon_invariants.py` goes from 4 failed / 4 passed → 8 passed. This is a "tests now actually test" win.

3. **Bug-letter alphabet advances to AI for the next surface.** Bug AI is the bridge `force_close` API needed because the LIDR close-attempt deadlock surfaced today (operator stop holds shares, bridge can't close). Bug AJ is the OTO take-profit width audit (today's OGN scalped at +0.4%).

4. **Pyright config consideration**: bumping `reportReturnType` from `"none"` to `"warning"` would have caught this class statically. Tracked as a follow-up in `tests/static_analysis/_pyright_baseline.json` for a future tightening iteration (similar to how I12 was tightened in commit 87efd85).

---

## §8 — The discipline this preserves

Per `42_monday_operator_runbook.md` §10:

> The discovery rate must exceed the introduction rate.

Bug AH was a defect that lived in main for an unknown duration, hidden behind silent test failures and a defensive try/except. It surfaced only when **a human read the live system log** at end-of-day. This is the same vector as Bug AG (08:00 morning surfacing) — direct operator observation closes the gap that test layers + static analysis miss.

The combined pattern from Bugs AG + AH: **Track B (the continuous-reconciliation defense layer) had two consecutive bugs** — one in launch (AG, fixed AM), one in execution (AH, fixed PM). Both were the same class (function-returning-None / variable-unbound). Both were caught by reading logs. Both were one-line fixes.

The 20/0 ratio holds through Monday 2026-04-27 EOD.

The operational cost of the bug: ~$1,316 in LIDR unrealized loss (compounded by the related Bug AI close-deadlock). The fix cost: 1 line of code + 1 finding doc.
