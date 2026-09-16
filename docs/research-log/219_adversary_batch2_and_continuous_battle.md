# 219 — Adversary batch #2 (partial-fill strand) + continuous battle in the scorecard

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "do (b) then (a)": more adversary scenarios, then wire it into the
scorecard to battle continuously.

---

## (b) New adversary weapons — found+fixed the partial-fill strand bug

Added 5 scenarios to the harness (`AdversaryBroker` weapons): `partial_fill`,
`stop_fills_mid_close`, `trading_halt`, `eod_deadline`, `eod_deadline_never_settles`. They
surfaced two issues:

### 1. partial_fill — a REAL wrapper bug (FIXED)
`attempt_close_with_status_check` set `succeeded=True` on ANY close response. A
`partially_filled` close (e.g. 1969/3282 — Alpaca DOES partial-fill market closes on thin
names) reported **SUCCESS while the unfilled remainder stranded at the broker with no stop**,
the bot believing it was flat. The Adversary scored it `SILENT-NAKED + PARTIAL-STRAND`.
**Fix (bridge.py)**: detect partial (`status==partially_filled` or `filled_qty < qty`) and
RE-CLOSE the remainder; if still partial after retries, report `succeeded=False` (never a
silent strand). Now survives.

### 2. eod_deadline_never_settles — an ARCHITECTURAL flag (NOT a wrapper bug)
When the close runs past the EOD bell AND can't settle, the position carries overnight. The
wrapper correctly **FAILS** (no phantom) — only the EOD-timing fix (start the close earlier)
can prevent the carry. The harness now SEPARATES **wrapper bugs** (must fix in the close
code) from **architectural flags** (point at the spawned EOD-timing task) and PASSES on 0
wrapper bugs. The Adversary literally tells us which fix each loss points at.

**Result: 10/11 survive — 0 wrapper bugs, 1 architectural flag.** 8 adversary tests + 39
close/recovery tests green.

### The harness scoreboard so far (3 real execution bugs found+fixed)
| doc | Adversary scenario | Real bug it caught | Status |
|---|---|---|---|
| 216 | realistic_settle | cancel-settle race (close 403s, retries burn out) | fixed |
| 218 | never_settles | naked re-arm (D249 re-arm 403s -> silent naked) | fixed (escalates) |
| 219 | partial_fill | partial close reported success -> remainder strands | fixed |

## (a) Continuous battle — wired into the post-close scorecard

`post_close_scorecard.py` now runs the full adversary sweep every session (env
`MX_ADVERSARY_BATTLE`, default on). It prints
`survived N/total | wrapper bugs: [...] | arch flags: [...]`, and **any WRAPPER bug emits a
CRITICAL `ADVERSARY_WRAPPER_BUG` incident** (the close path regressed and can lose money ->
the Operator triages it; do NOT arm T1 until green). Architectural flags don't alarm.

Net effect: **every change to the execution code is now continuously re-battled.** A
regression that reintroduces the cancel-settle race / naked re-arm / partial-strand is caught
the same evening, as a CRITICAL incident, before it can carry into a live session. This is
the robustness loop compounding — deterministic, can't be overfit, automatic.

## Why this is the compounding execution edge (vs the selection mirage)
The stress tests (213-215) showed selection edges evaporate. The Adversary shows the
opposite: **execution invariants, once hardened + continuously battled, STAY hardened.** Each
bug it finds is a permanent gain (a real loss-path closed + a regression guard added). 3
found+fixed in 3 docs; the harness now runs forever. This is the durable edge — not picking
better, but never bleeding on the plumbing.

## Appendix — files
- `src/ops/adversary.py` — 4 new weapons (partial_fill, stop_fills_at_tick, deadline_tick,
  halt_until_tick) + invariants (PARTIAL-STRAND, DEADLINE-CARRY).
- `scripts/adversary_run.py` — dict-based scenarios; wrapper-bug vs architectural-flag split.
- `src/execution/bridge.py` — partial-fill re-close (never report success on a strand).
- `scripts/post_close_scorecard.py` — continuous battle + CRITICAL on a wrapper-bug regression.
- `tests/unit/test_adversary.py` (8 tests). This doc + changelog.
