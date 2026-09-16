# 216 — Execution hardening #1: the cancel-settle race (the dominant P&L leak)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "pivot to hardening execution, highest ROI."

The stress-test gauntlet (docs 213-215) proved the convergent truth: **selection buys
variance, not expectancy; the durable edges are timing + EXECUTION.** Day 1 lost money on
execution, not picks. This is the #1 execution fix — proven from the live 6/1 log.

---

## 0. The smoking gun (6/1 log, the D76 EOD close)

```
15:55:23 D76 EOD CLOSE: 2 positions closing at 15:55 ET
15:55:24 close CMND -> 403 'insufficient qty available, held_for_orders=3282, available:0'
15:55:24 D248 BLOCKING_STOPS CMND: cancelled stop sell 2aebfae9 (was reserving shares)
15:55:25 close CMND -> 403 AGAIN  (cancel not settled, ~1s later)
15:55:27 close CMND -> 403 AGAIN  -> 3 retries exhausted -> close FAILED
15:55:27 D249 STOP_REARM CMND: re-arm stop -> ALSO 403 -> "POSITION IS NOW NAKED"
```
Same for OPTU. Both carried overnight, **naked** (no protective stop). CMND was −$1,508
unrealized. This is the dominant P&L leak: every time a `held_for_orders` close 403s, the
position can end the day naked AND carried.

## 1. Root cause

The shared close wrapper `attempt_close_with_status_check` (used by D76 EOD, D91 overnight,
D245 exits) had a cancel-settle race:
- A protective stop reserves the shares (`held_for_orders == qty`) → `close_position` 403s.
- D248 cancels the blocking stop — but the cancel is **async at the broker** and takes
  **1–3s+** to free `qty_available`.
- The old code slept a **blind 0.5s** then retried. The cancel hadn't settled → 403 again.
- Each settle-wait **burned a close-retry attempt** (`for attempt in range(max_retries)`),
  so all 3 retries 403'd on the same unsettled cancel before the qty freed → close FAILED →
  the re-arm also 403'd (qty still reserved) → **naked + carried.**

(doc 209 fixed this race only on the D91 *overnight* STEP-1 path — NOT the shared wrapper
that D76 uses. So the fix didn't cover the EOD close, which is where 6/1 bled.)

## 2. The fix (`src/execution/bridge.py`)

1. **NEW `_await_qty_available(client, ticker, need_qty, timeout_s=6)`**: polls the broker's
   native `qty_available` until the cancelled stop has SETTLED and freed the shares (the
   exact precondition the close needs). Bounded; never raises.
2. **Separate settle budget**: the qty-blocked branch now calls `_await_qty_available`
   instead of a blind 0.5s sleep, on its OWN bounded budget (`settle_budget=3`) that does
   **not** consume the close-retry budget. Converted the `for` loop to a `while` and
   decrement `attempt` on a settle-wait, so the broker gets time to free qty WITHOUT burning
   the close attempts. Hard-capped → no infinite loop.

Net effect: a `held_for_orders` close now **waits for the cancel to actually settle, then
closes** — instead of exhausting retries into an unsettled 403 and going naked.

## 3. Verification

- **4 new tests** (`test_close_cancel_settle.py`): the exact 6/1 CMND scenario (403-until-
  settle now **SUCCEEDS**, not naked); the settle-poll frees-after-N-polls; the never-frees
  timeout (bounded False); and the **clean bounded FAIL** if qty never frees (no hang, no
  infinite loop — the position stays tracked + protected for the failsafe).
- 35 close-path + crash-recovery + short-recovery tests green; `main` boots.
- All production callers pass `qty` (D76 main.py:4615, D91 bridge.py:781, D245 main.py:6240)
  so the settle-poll precondition is always available.

## 4. Why this is the highest ROI
Selection is a coin-flip (variance, per docs 213-215). But a naked overnight carry is a
*structural* loss that recurs every time a close 403s — and it's asymmetric (the carries are
underwater losers, by definition the ones the bot tried to close). Fixing the close to
actually flatten avail=0 positions before the bell directly stops the bleed day 1 showed.
The boring structural lever, exactly as the stress tests predicted.

## 5. Follow-ons (the rest of the execution-hardening queue)
- **EOD close timing margin**: D76 fires at 15:55; with the settle-poll it now needs ~5-10s
  of headroom — confirm it completes before ~15:59 (the spawned EOD-timing task covers this).
- **The phantom root** (doc-185 B1 event-sourced ledger): book P&L only on confirmed broker
  fills, so a failed close can never book a +$304 phantom. Strategic; the bigger structural fix.
- **D242 EOD failsafe** already force-closes ghosts at 16:00 (it caught STG on 6/1) — this
  fix makes the *primary* 15:55 close succeed so the failsafe is rarely needed.

## Appendix — files
- `src/execution/bridge.py` — `_await_qty_available` + settle-budget retry loop.
- `tests/unit/test_close_cancel_settle.py` (4 tests). This doc + changelog.
