# 220 — EOD close retries until the bell (the Adversary's architectural flag, closed)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "take the EOD-timing fix next." The 1 architectural flag the Adversary
(doc 219) couldn't survive: `eod_deadline_never_settles` → overnight carry.

---

## 0. The honest root cause (NOT what I first assumed)

I expected "the close fires too late." The 6/1 log says otherwise: **D76 fired at 15:55:23 —
~4.5 minutes before the 16:00 bell.** Plenty of headroom. The real bug: D76 made **ONE pass**
over the positions (3 retries, ~7 seconds total) and then set `eod_close_completed=True`
**even when closes FAILED.** CMND/OPTU 403'd in that 7-second burst (`held_for_orders`,
pre-doc-216 timing) and were **abandoned for the day** — Phase 4 at 16:00 could only carry
them. So the fix isn't "fire earlier"; it's **"keep retrying across the available window
instead of giving up after one burst."**

## 1. The fix (`main.py` D76 block)

- Track `_d76_all_closed` across the close loop (and `_d76_failed_tickers`).
- Mark `eod_close_completed=True` **only if every position actually closed**, OR it's the
  **final pass** (`min_et >= 59`).
- If any close fails, leave the flag **False** → the next Phase-3 cycle (~30s later, still
  before the bell) **retries**. This turns the single 7-second burst into **repeated passes
  across 15:55 → 15:59** — exactly the window the doc-216 cancel-settle poll needs to let a
  blocking-stop cancel actually settle and free the qty.
- Past 15:59 it accepts the carry (don't fire unfillable orders right at the bell); Phase 4 +
  the doc-216/218 path keep the carried position **protected/escalated, never naked/phantom.**

## 2. Why the retry is safe (no double-close, no loop)

- `positions = bridge.position_manager.open_positions` is **re-fetched every Phase-3 cycle**
  (main.py:4467), so a successfully-closed position (removed via `close_with_attribution`)
  never reappears on retry.
- The D86 guard (`if pos.ticker not in broker_tickers: skip`) also skips any name already
  gone at the broker.
- Hard stop at `min_et >= 59` → bounded; cannot loop past the bell.

So each retry re-attempts **only the genuinely-still-open** positions.

## 3. How this closes the Adversary flag

The Adversary's `eod_deadline_never_settles` modeled "close runs past the bell + can't
settle." At the **wrapper** layer that correctly FAILS (no phantom) — but only the **main-loop
layer** can prevent the carry, by retrying across the window. doc 220 is that layer. The
combined story across the execution-hardening arc:
- **216** — the close wrapper waits for the cancel to settle (no naked, no burned retries).
- **218** — the re-arm waits + escalates (no silent naked).
- **219** — partial fills re-close (no strand).
- **220** — the EOD loop retries across 15:55→15:59 (no give-up-and-carry).
Together: a `held_for_orders` position at EOD now gets **many settle-aware close passes before
the bell**, and only carries if it's genuinely un-closeable — in which case it's protected +
a CRITICAL incident fires. The 6/1 CMND/OPTU carry would not recur.

## 4. Verification
- 7 gating-rule tests (`test_eod_close_retry.py`) pin "complete ⟺ (all closed OR final pass)"
  and replay the exact 6/1 case (fails at 15:55 → retries → accepts carry at 15:59).
- 27 crash-recovery tests green; `main` boots; the 10 `pipeline_guards` fails are PRE-EXISTING
  (stash-confirmed identical without this change).

## 5. Remaining execution work
- **doc-185 B1 event-sourced ledger** — the phantom ROOT: book P&L only on confirmed broker
  fills, so a failed close can NEVER book a +$304 phantom. The deepest structural fix; makes
  the whole phantom class impossible rather than caught-after-the-fact.
- A future Adversary scenario: a multi-pass EOD window (model the 30s cycles) to assert the
  retry actually flattens a slow-settle position before the simulated bell.

## Appendix — files
- `main.py` — D76 block: `_d76_all_closed` gate + retry-until-15:59.
- `tests/unit/test_eod_close_retry.py` (7). This doc + changelog.
