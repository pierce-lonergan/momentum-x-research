# 309 — Tuesday Readiness: a Rehearsed First Build, an Honest Health Line, and a Core-Day Verdict

**Date:** 2026-09-28 (evening, after Monday's missed build; see doc 308 §5)
**Status:** The first core allocation is armed for **Tuesday 2026-09-29 at 10:05 ET**. It runs from a one-time trigger on the Windows task `MomentumX-CoreRebalancer`, which needs no approvals and can wake the PC.
- Rehearsal: the exact production path was rehearsed twice tonight and placed no order.
- Code: private commit `33d70b9` (local develop).
- Result so far: **no order has been placed.**

**Method:** one workflow. A builder wrote each workstream, a fresh adversarial skeptic reviewed each builder's work, and the skeptics' patches were applied by hand and re-tested.

---

## 0. The answer

| item | result |
|---|---|
| **Scheduler rehearsal** | I started the task tonight with `Start-ScheduledTask`, which ran the real launcher on the real `--execute` path. The rebalancer refused ("market is closed") and the task recorded exit 2, which is correct. |
| **Launcher dry run** | 59 settings loaded (names only, no values logged), imports OK, clean refusal, 1.5 s |
| **Broker after both** | 0 orders since Monday 00:00 ET, 0 positions, core ledger empty. The holdings invariant passes (both zero). |
| **Plan it would place** | **BUY SPY ≈193 @ ≈765.77 + BIL 404 @ 91.63**. That is ≈97.8% of broker equity, with SPY ≈80% of the sleeve, sized as doc 307 §9 decided. |
| **Trigger state** | Ready. Next run Tue 09-29 10:05 ET, window ends 11:00. Weekly 15:15 runs follow from Wed 09-30, the month-end. |
| **Environment** | 09-29 to 10-02 are normal 09:30–16:00 sessions. Account ACTIVE. Core halt absent (no env flag, no `data/CORE_HALT`). The ops webhook and broker keys are present (checked by name). Wake timers are allowed, and on AC the PC never sleeps. |
| **Nightly health line** | Had been RED every day since at least 09-18, for **three false reasons**. All three are fixed (§1). |
| **Core-day verdict** | New nightly OK / WARN / CRITICAL check of the core (§2). Tonight's live run: `OK — no core yet`. |
| **Tests** | 943 passed, 0 failed across the three affected suites |

## 1. The health line was crying wolf

The nightly `pipeline_health_check` line runs at about 19:31. Replayed over past days, it was RED every day since at least 09-18, for reasons that were not real:

1. **ShadowGrader "still running" (0x41301).** The task that runs the health check is the grader itself, so it was always still running when checked.
   - **Now:** still-running counts as healthy if it started on the checked date.
   - **Still RED:** a run left over from an earlier date, or a non-zero grader exit in its own log.
2. **The nightly adversary wrote onto the live incident bus.** Its synthetic `NAKED_POSITION_RISK` CRITICALs, on a fake symbol, went to the real incident log and the real WAKE file.
   - **Now:** battle incidents go to a separate directory, and the live paths are restored afterwards.
   - **Test:** a new test fails if a battle can touch the live bus.
3. **Missing raw fills while halted.** Under the quarantine no fills are expected, so the fills file now only has to exist.
   - **Exception:** if the core rebalancer submitted an order that day, the halt excuse is removed and the file must hold fresh fills. This checks that the fill stream records the core's first fills.

Replayed for 09-28, the line now reads `PIPELINES 10/10 | outputs 6/6 fresh | CRIT 1`. The one CRITICAL left is a real LLM 503 blip, and it should be RED.

The rebalancer's own rule also moved: from **09-29** on, a weekday with no run, or a task that has never run, is RED. If the PC is off at 10:05 tomorrow, the 19:31 line says so.

## 2. A verdict on the core, every night

`scripts/verify_core_day.py` (new) runs in the nightly scorecard after the shadow tracker. It is read-only against the broker. It joins four sources:

- **Launcher log:** the last `--execute` run, its exit code, and the reason for a refusal.
- **Fills ledger:** booked shares per symbol, using the rebalancer's own booking rules.
- **Broker:** SPY and BIL positions and open orders, including any core-symbol order *without* the `core-` prefix.
- **Bot logs:** every line that touched SPY or BIL, split into exempt, blocked and **acted**. A strategy exit path that acted on the core is exactly what doc 307's 24 exemptions exist to prevent.

**Verdicts:**

| verdict | when |
|---|---|
| CRITICAL | holdings the ledger cannot explain, an un-prefixed or short position in a core symbol, or a bot action on the core |
| WARN | a triggered run that refused for an abnormal reason, or unresolved orders |
| OK | everything else |

A CRITICAL posts to the ops Discord.

**Skeptic verdict: SOUND.** Three refinements are queued. None of them matters before the core has held a position overnight.

- **(D1) Later runs.** Judge only the last run, but do not WARN when a later run on the same day succeeded.
- **(D2) Wrong-kind core-symbol refusals.** A refusal naming a non-core order in a core symbol, or a short position, is CRITICAL whether or not a trigger fired.
- **(D3) Scope of the softened check.** Soften the holdings comparison only for the symbols with unresolved orders.

## 3. What could still go wrong at 10:05, and what catches it

| failure | behaviour | who sees it |
|---|---|---|
| PC shut down | nothing runs (the trigger's window ends at 11:00) | 19:31 health line RED ("no run that day"); the Wed 15:15 run then makes the initial build |
| stale or wide quote, clock failure | refuses, exit 2, no order | core-day WARN; retried at the Wed 15:15 run |
| order not final by the deadline, partial fill | exit 1 + CRITICAL Discord; the next run reconciles before trading | Discord, health line, core-day |
| broker holdings differ from the ledger | refuses + CRITICAL | Discord, core-day CRITICAL |
| a bot exit path acts on SPY/BIL (EOD failsafe, orphan sweep, cancel-all) | exempted in code (doc 307); **Tuesday's 15:50 close is the first live test with a held core** | core-day CRITICAL on any "acted" line |
| first boot with a held core (Wed 04:30) | boot sync must not adopt SPY/BIL as orphans | Wed 09:45 read-only check + core-day |

The strategy halt stays on the whole time. The core ignores it by design, and it ignores the core.

## 4. Found along the way (not in tomorrow's path)

- **Partial-fill re-arm defect (bridge).** The adversary surfaced a real defect: after a partial exit fill, a protective stop re-armed from a position snapshot is sized to the *snapshot* quantity, not the shares still held.
  - It cannot fire tomorrow: strategy entries are halted and the core is excluded from strategy stops.
  - Fix queued: re-arm for min(snapshot, broker position).
- **Log wording.** In `--execute` mode the rebalancer prints its plan ("PLACING BUY …") before the refusal lines. The broker confirms no order was sent. This is a wording fix only, deferred so the rebalancer stays frozen until after its first build.
- **Test suite reads of the live `.env`.** The suite still reads it about 5,300 times (doc 308). It is a read-side gap and remains open.

## 5. Provenance

- Private `33d70b9` (doc 309), on top of `47423af` (doc 308), `fb2b4e7` and `40560e9` (doc 307).
- Changed or new files:
  - scripts: `verify_core_day.py`, `pipeline_health_check.py`, `adversary_run.py`, `post_close_scorecard.py`
  - `docs/QUARANTINE.md`
  - tests: 3 suites
- Scratch: `doc309/`, containing the rehearsal logs and the verify_health and verify_coreday skeptic reports and patches.
