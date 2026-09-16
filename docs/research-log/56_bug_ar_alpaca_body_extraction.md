# 56 — Bug AR: Bug AI cancel-and-coordinate was DEAD CODE in production (string-match against truncated err_str never fired)

**Status:** patched 2026-04-28 afternoon (Phase 3.1 of the Discipline Framework).
**Severity:** **P0** — caused (1) Mon 2026-04-27 LIDR 5+ failed close attempts and (2) Tue 2026-04-28 LIDR 10:00:38 ET manual sell with no Bug AI assist.
**Surface:** Phase 0 infrastructure audit forensic on the Tue 10:00:38 ET 403 — the D248 BLOCKING_STOPS log line was ABSENT from the transcript despite a textbook-shape qty conflict.
**Bug class:** false-green test pattern + incorrect assumption about `str(httpx.HTTPStatusError)`. The existing Bug AI tests passed because they used a custom exception whose `__str__` returned the body. Production httpx truncates.

## §0 — TL;DR

Bug AI shipped Mon evening with the substring-match condition:

```python
is_qty_blocked = (
    cancel_blocking_stops_first
    and not cancelled_snapshots
    and ("insufficient qty available" in err_str
         or "40310000" in err_str
         or "available: 0" in err_str)
)
```

where `err_str = str(e)` and `e` is what `httpx.AsyncClient.delete(...).raise_for_status()` raises on a 403.

**The defect:** `str(httpx.HTTPStatusError)` renders only:
```
Client error '403 Forbidden' for url 'https://paper-api.alpaca.markets/v2/positions/LIDR'
For more information check: https://developer.mozilla.org/.../403
```

The Alpaca JSON body (containing `40310000`, `insufficient qty available`, `available: 0`) lives in `e.response.text`. The substring check therefore NEVER matched in production. Bug AI was dead code.

The existing tests (`test_bug_ai_force_close.py`) used a doctored `_Block403Error` whose `__str__` returned the body — false green. Tests passed; production failed.

## §1 — How Phase 0 caught this

The Tue 2026-04-28 transcript at 10:00:38 ET showed:

```
WARNING D245 SMART_EXIT_REJECTED LIDR (attempt 1/3): Client error '403 Forbidden' for url '.../v2/positions/LIDR'
INFO    D246 SMART_EXIT_RETRY LIDR: retrying after backoff (attempt 1 failed; will retry 2 more time(s))
WARNING D245 SMART_EXIT_REJECTED LIDR (attempt 2/3): Client error '403 Forbidden' for url '.../v2/positions/LIDR'
...
ERROR   D247 SMART_EXIT_ESCALATE LIDR: 3 retries exhausted; broker close FAILED.
```

What was MISSING:
- `D248 BLOCKING_STOPS LIDR: 'insufficient qty' detected — enumerating and cancelling competing sell orders before retry`
- The Bug AI cancel-and-coordinate dance never engaged.

Phase 0 §0.3 traced the absence to the substring-match condition and confirmed by inspection: `str(e)` for the production-shape httpx error never contains the needed substring.

This is exactly the **SCAFFOLDING-COMPLETE** failure pattern the Tier 3 audit caught three times last week: the system had every piece in place but the WIRING (in this case, reading `e.response.text` instead of `str(e)`) was missing.

## §2 — Fix

`src/execution/bridge.py:attempt_close_with_status_check` exception handler:

```python
except Exception as e:
    # Bug AR fix (2026-04-28): str(e) on httpx.HTTPStatusError
    # only renders "Client error '403 Forbidden' for url ..." —
    # the Alpaca JSON body containing "insufficient qty
    # available" / "available: 0" / code 40310000 is in
    # e.response.text. Without extracting it, the Bug AI
    # substring match below NEVER fires and cancel-and-coordinate
    # is dead code.
    err_body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            err_body = resp.text or ""
        except Exception:
            err_body = ""
    err_str = f"{type(e).__name__}: {e}"
    if err_body:
        err_str = f"{err_str}; body={err_body}"
    result["last_error"] = err_str
    logger.warning(
        "D245 SMART_EXIT_REJECTED %s (attempt %d/%d): %s",
        ticker, attempt, max_retries, err_str,
    )
    # Bug AR (2026-04-28): match case-insensitively against the
    # COMBINED string (exception repr + body). "40310000" is the
    # Alpaca error code for qty conflicts and is the most reliable
    # signal — appears in the JSON body even when the human-readable
    # message changes.
    err_lower = err_str.lower()
    is_qty_blocked = (
        cancel_blocking_stops_first
        and not cancelled_snapshots
        and ("insufficient qty available" in err_lower
             or "40310000" in err_str
             or "available: 0" in err_lower
             or "qty available" in err_lower)
    )
```

Three changes:
1. Extract `e.response.text` and append to `err_str`.
2. Match case-insensitively (defends against Alpaca casing drift).
3. Add `"qty available"` as a tolerant fallback substring.

## §3 — Aggressive testing (12 tests, all pass)

`tests/unit/test_bug_ar_string_match.py`:

1. **Realistic httpx-shape regression** — uses `_RealisticHTTPStatusError` whose `__str__` does NOT contain the body (mimics real httpx). Verifies the fix triggers cancel-and-coordinate.
2. **Premise sanity check** — explicitly asserts that `str(e)` alone on the realistic error does NOT contain any of the Bug AI signal substrings. If a future test refactor regresses `_RealisticHTTPStatusError` back to the doctored shape, this fails loudly with a clear message.
3-9. **Variant body parametrization** — 7 different 403 body shapes:
   - Today's exact byte-for-byte body
   - Uppercase / mixed-case message
   - Different key order
   - Code-only (message localized away)
   - Older Alpaca format with `available: 0` phrasing
   - Tolerant fallback `"qty available"` only
10. **Negative case** — unrelated 403 (`account is locked`) must NOT trigger cancel. Defends against the obvious failure mode where we'd cancel protective stops on every transient broker error.
11. **100× deterministic stress** — replay LIDR fixture 100 times, expect 100/100 succeeded. No race conditions, no flakes.
12. **Forensics wiring** — `result["last_error"]` must contain `body=...` so post-mortem on broker rejections can see WHY the close failed.

Plus 20 adjacent tests verified non-regressed: `test_bug_ai_force_close.py` (7), `test_d24_bug_z_smart_exit_403.py` (6), `test_d24_bug_v_bridge_cancel.py` (4), `test_d24_bug_w_exit_ladder_stop.py` (3).

## §4 — Why this is also a TEST DISCIPLINE finding

The Bug AI tests passed for two days while the production code was dead. Root cause: the test double's `__str__` was constructed to make the test work, not to mimic production behavior. Bug AR's `_RealisticHTTPStatusError` inverts that — it's constructed to mimic production exactly, even when it's inconvenient.

**Discipline going forward:**
- **Test doubles for HTTP errors must mimic the real library's error rendering.** If the real httpx `HTTPStatusError.__str__` doesn't include the body, neither should the test double.
- **Substring-match conditions are fragile.** When you add one, write a test that proves the substring would NOT match against any realistic flat-string rendering of the exception. `test_bug_ar_old_codepath_would_have_missed_this` is the template.
- **Production-rendering parity is a test invariant.** Add a checked assertion in test setup that confirms the test double behaves like the real thing in the dimensions that matter.

## §5 — Effect on tomorrow (Wed 2026-04-29) and beyond

Tomorrow morning's startup will hit the FAST_PATH on any held position. If a held position needs a SMART_EXIT trigger and a manual or stale stop reserves the shares:
- D245 SMART_EXIT_REJECTED fires on attempt 1.
- **D248 BLOCKING_STOPS now fires** (previously missing).
- Bug AI cancels the stop, retries close, succeeds.
- D247 SMART_EXIT_ESCALATE — never reached.

For any 403 that is NOT a qty conflict (account lock, market closed, permission denied), Bug AI correctly does NOT engage — protective stops stay at the broker. The negative-case test pins this.

## §6 — Discovery rate impact

| Pre-Bug AR | After Bug AR |
|---|---|
| 28 production / oracle / harness / architectural bugs | **29** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **29/0 = ∞** |

Bug AR is a **next-day-of-shipping cascade** discovery — only surfaceable by ACTUALLY running the previous day's fix in production AND auditing the transcript when it didn't engage. Yesterday's Bug AI shipped tested + working in isolation; today's run revealed that the substring condition was unreachable in production.

This is the operator-vigilance discipline working as designed: ship the fix, run the next session, observe the missing log line, surface the cascade. The 7-Phase Discipline Framework's Phase 0 audit (mandatory, code-frozen) is what made this catch possible — without forensics on the transcript before any code change, Bug AR would have stayed dead.

## §7 — Discipline this preserves

The cancel-and-coordinate path is the SINGLE recovery mechanism for qty-conflict deadlocks during exits. Every component downstream (Bug AJ alert, D247 escalation, manual operator intervention) depends on Bug AI either (a) recovering automatically, or (b) re-arming the protective stop and escalating cleanly. Bug AR closes the gap where Bug AI was producing path (b) for the WRONG reason — silently failing to detect the qty-conflict signal — instead of legitimately exhausting recovery options.

The 29/0 ratio holds. Cost of Bug AR in production: 1 dead-code Bug AI fix + 5+ failed close attempts on Mon (LIDR -$316 unrealized → realized loss path was extended) + 1 manual operator close on Tue. Fix cost: ~15 LOC + 12 tests + 1 finding doc.
