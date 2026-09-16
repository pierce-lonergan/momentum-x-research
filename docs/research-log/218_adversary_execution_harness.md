# 218 — The Adversary: an execution-breaking harness (and it found a bug on day one)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "build it!" (the adversarial market that battles our process), scoped
per doc 217 to ROBUSTNESS, not edge-discovery.

---

## 0. What it is
A deterministic adversary that drives our REAL close/exit/recon code through a sim broker
whose timing + errors it controls, trying to make us go **naked**, book a **phantom**, or
strand a position. The "market battles our process" framing applied to the layer that is
DETERMINISTIC (so it can't be overfit, unlike a selection edge — doc 217).

## 1. The pieces (built on the doc-217 plan + reusing the mx-arena concepts)
- **`src/ops/adversary.py` — `AdversaryBroker`**: a drop-in for `AlpacaDataClient`
  implementing the 6 methods our close path calls (`close_position`, `cancel_order`,
  `get_orders`, `get_positions`, `submit_stop_order`, `submit_order`). The core realism +
  weapon: a stop order **reserves** the position's qty (`held_for_orders`), so
  `close_position` 403s with the byte-real Alpaca `40310000` body until the stop is cancelled
  AND the cancel **settles** — and the adversary controls the settle latency. settle huge =>
  the cancel never frees qty in budget => our code must FAIL CLEANLY (not naked, not phantom).
- **`check_invariants()` — the scoreboard**: NEVER silent-naked (a held position must have a
  protective stop OR a CRITICAL escalation), NEVER phantom (P&L only on confirmed close),
  never stranded. The Adversary WINS if it breaks one.
- **`scripts/adversary_run.py` — the harness**: runs our REAL
  `attempt_close_with_status_check` (the shared D76/D91/D245 wrapper) across a 6-scenario
  settle-latency + broker-failure sweep, scored by the invariant checker.

## 2. It earned its keep IMMEDIATELY — found a real naked-position bug
First run: 5/6 survived; **`never_settles` went SILENT-NAKED.** When the cancel never settles,
D248 cancels the blocking stop, the close fails (correct), and the D249 re-arm **also 403s**
(qty still reserved) → the position is left naked with no protection and no alert. This is the
EXACT 6/1 `D249 STOP_REARM_FAILED → POSITION IS NOW NAKED` path — reproduced deterministically
by the Adversary, where the unit tests had missed it.

**The fix (bridge.py, doc 218):**
- Before re-arming, `_await_qty_available` — let the cancel settle so the re-arm doesn't 403
  into reserved qty.
- Re-arm only from REAL snapshots (the `__waited_for_settle__` sentinel is not a stop).
- If re-arm STILL can't protect (qty genuinely stuck), emit a **CRITICAL `NAKED_POSITION_RISK`
  incident** (doc 205/212 bus) so the Operator sees it immediately — not a silent loss.

The honest invariant: a broker that makes qty permanently un-closeable IS an unavoidable
naked state. "Surviving" means we **escalate it loudly**, not that we magically protect it.
So: SILENT naked = adversary wins; ESCALATED naked = survived. After the fix: **6/6 survive.**

## 3. Why this is the right kind of "battle" (and compounds)
- It attacks **deterministic plumbing**, so a "win" (an invariant break) is a REAL bug, never
  an overfit artifact — the exact distinction doc 217 drew. Contrast the selection claims
  (213-215) that evaporated; an execution invariant break is unfakeable.
- It's a **permanent regression guard**: the 5 new tests (`test_adversary.py`) + the harness
  re-run on every change mean the cancel-settle race (216) and this re-arm gap (218) can
  never silently return.
- It directly extends the execution-hardening pivot: a machine that hunts the next
  naked-carry / phantom the way it would have caught the cancel-settle race BEFORE it cost
  (paper) money on 6/1.

## 4. What it deliberately does NOT do (per doc 217)
- It does NOT generate synthetic gap-ups to optimize our SELECTION against — that would
  overfit to an invented distribution (the trap docs 213-215 exposed). The Adversary only
  battles execution/risk invariants. The selection edge is confirmed on REAL data
  (scorecard/observe), never on synthetic.

## 5. Next adversary scenarios (the harness is extensible)
The sweep is a list of (settle_delay, failure_mode); easy to add: partial-fill close, a
stop that fills DURING the close race, a halt mid-close, a 5xx broker outage storm, a
tranche-sell 403-war, the EOD timing window (close must finish before the bell). Each new
scenario either survives (good) or finds the next real bug. Wire it into the post-close
scorecard / CI so it runs continuously.

## Appendix — files
- `src/ops/adversary.py` (AdversaryBroker + check_invariants), `scripts/adversary_run.py`,
  `tests/unit/test_adversary.py` (5), `src/execution/bridge.py` (re-arm settle + escalation).
- `data/reports/adversary_report.json`. This doc + changelog.
