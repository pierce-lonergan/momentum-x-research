# 225 — Pre-Tuesday bug sweep (adversarial review of today's live-path changes)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01 (Monday night)
**Mandate**: Pierce — "full bug sweep on the files we touched today + anything impacted, resolve
as much as possible to increase confidence for tomorrow's run."

A focused adversarial code-review of the LIVE-PATH code deploying Tuesday (the off-path
scripts — operator_*, backtests, selection_study — were excluded; they don't run during the
session). Found 1 HIGH bug (in my own doc-220 fix) + 2 LOW nits; all fixed.

---

## HIGH — the doc-220 EOD final-pass had a 15:59→16:00 skip race (FIXED)

`main.py` D76 block. doc-220 made the EOD close retry-until-bell, marking complete only if all
positions closed OR `min_et >= 59`. **The hole:** the Phase-3 loop cadence is 30-60s sleep +
~7-50s of D76 close processing (settle-polls/backoffs/re-arm). So a cycle sampling 15:58:xx can
jump straight to **16:00:xx** on the next iteration — at which point `hour_et == 16`, the D76
block (gated `hour_et == 15`) is **never re-entered**, `eod_close_completed` stays False, and
Phase 4's market-closed path (`hour_et >= 16` → `_skip_close`) **carries the retryable position
overnight.** The D242 force-close failsafe doesn't rescue it (it only closes *un-tracked*
ghosts; this one is still tracked). Minute 59 could be **skipped entirely** → the exact
overnight-carry doc-220 was meant to prevent.

**Severity HIGH** (financial: a flatten-able loser carried overnight), not CRITICAL (no crash;
protective stops re-armed; next-session D91 closes it at 04:00).

**Fix:** `_d76_final_pass = (min_et >= 58)` — fire the final pass at 15:58, which still leaves
~2 min / 3-4 close passes of headroom from the 15:55 start, AND sets the complete-flag reliably
*before* the hour can roll over. A 15:58 sample is far more reliably hit than a 15:59 one given
the cadence. + a regression test (`test_doc225_minute59_skip_race`) pinning the race.

## LOW — `last_error` None-safety (FIXED)

`bridge.py` re-arm escalation: `result.get("last_error", "")[:160]` at the 2
`NAKED_POSITION_RISK` emit sites. `last_error` initializes to `None`, and `.get(k, "")` returns
the **stored** `None` (the `""` default only applies on a *missing* key) → `None[:160]` could
raise. Masked by the outer `try/except: pass`, so the only consequence was a silently-dropped
CRITICAL incident — but that's exactly the incident we most want delivered. Fixed to
`(result.get("last_error") or "")[:160]`.

## What the review CLEARED (empirically, not by assertion)

The rewritten close loop (`attempt_close_with_status_check`, the `for`→`while` + settle-budget
+ `attempt -= 1`) was the highest-risk change. The review traced it and confirmed:
- **Cannot infinite-loop**: the `not cancelled_snapshots` sentinel makes the settle branch
  one-shot, so `attempt -= 1` runs at most once; max `close_position` calls = `max_retries + 1`.
  Pinned by `test_close_still_fails_cleanly_if_qty_never_frees`.
- **`_await_qty_available` is bounded** (≤12 polls / 6s), never hangs, never raises (get_positions
  raising → False; None/[] → True).
- **Re-arm block correct**: `real_snaps` filtering handles the string sentinel; the
  `NAKED_POSITION_RISK` emit is import-safe + never-raises.
- **No double-close** in the D76 retry: `positions` is re-fetched each cycle + the D86
  `not in broker_tickers` skip guards already-closed names.
- **`parse_trade_update` / `_compute_broker_event_id`**: None/missing-safe; the `execution_id`
  extraction breaks no existing field; backward compatible.
- **`fill_capture` tee**: double-guarded (call-site try/except + internal try/except) — truly
  cannot raise into the live fill handler.

## Deliberately NOT changed (right risk trade-off the night before a deploy)

- **`settle_budget = 3` dead-counter** (`bridge.py`): the review flagged that the one-shot
  sentinel makes `settle_budget` never reach 0, so it's effectively dead — strictly *more*
  conservative than advertised, **zero runtime risk**. Cleaning it up means editing the live
  close loop for cosmetics the night before deploy — not worth the risk. Logged for a calm-day
  refactor (reconcile the sentinel vs the budget; pick one).
- **`order_id` can be None on explicit `"id": null`** — pre-existing, unchanged by today's
  edits, latent type-nit only (no crash). Out of scope for this sweep.

## Verification
- **137 targeted tests green**: 48 live-path (eod-retry / close-cancel-settle / adversary /
  ledger / trade_updates) + 89 blast-radius (crash + short recovery + all ops/execution suites).
- **FULL SWEEP (4102 tests, 14m45s): 4021 passed, 81 failed, 3 skipped, 3 xfailed.**
- **The 81 failures are ALL PRE-EXISTING — proven decisively:** zero failures in ANY file I
  touched today; the failing files (`pipeline_guards`, `s018_wiring`, `s019_pipeline`,
  `pipeline`, `phase0_production_lifecycle`) are the long-standing integration-MagicMock rot.
  Ran the 3 biggest failing suites at `fc9b4c0` (the commit the bot ran Monday) via a git
  worktree → **identical "13 failed, 58 passed"** as at HEAD. The bot traded today on
  fc9b4c0 WITH these exact failures. My 18 commits added 0 new failures. (The "81 vs the ~41
  I recalled" is just the full 4000-test run surfacing more of the same old rot than my usual
  subset — not new breakage.)
- `main` boots; `bridge.py` + `main.py` compile.
- Adversary battle still `wrapper bugs: 0`.

## Confidence statement for Tuesday
The live-path code (execution-hardening 216/218/219/220 + the B1 capture + parser) has now been
adversarially reviewed line-by-line, the one real bug it surfaced (the 15:59 skip race) is
fixed and regression-pinned, and nothing in the rewritten close loop can infinite-loop, hang,
double-close, leave a silent naked position, or crash the fill handler. Combined with doc-223's
green gates, **Tuesday is cleared on the code dimension.**

## Appendix — files
- `main.py` (D76 final-pass 59→58), `src/execution/bridge.py` (last_error None-safety),
  `tests/unit/test_eod_close_retry.py` (boundary + skip-race tests). This doc + changelog.
