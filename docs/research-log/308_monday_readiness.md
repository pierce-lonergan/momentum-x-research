# 308 — Monday Readiness: the Core's Guards, a Dividend Test, and a Standing Rebalancer

**Date:** 2026-09-27 → 2026-09-28 (before the open)
**Status:** Ready for the first core allocation on Monday 2026-09-28 at 10:05 ET. The recurring rebalancer is installed and first runs Wednesday 09-30 at 15:15 ET, the month-end. Every change passed an adversarial review. Private commit `47423af` (local develop). **No order has been placed yet.**
**Method:** three workflows (13 agents), one builder per workstream, each followed by a fresh adversarial skeptic, repeated until the skeptics' findings were LOW or none. One builder died at a session limit; its unreported work was verified by an independent skeptic before commit.

---

## 0. The answer

| item | result |
|---|---|
| **Pierce's "Choice B"** (doc 307 §9) | Adopted: invest the whole account (sleeve fraction 0.98) and size on broker equity. Rejected: 95/5 SPY/cash, the LGD-100 earnings long/short, and the nightly LLM strategy tournament |
| **Monday 10:05 allocation** | A one-time Claude task runs a time gate, then `--check`, and executes only if 7 preconditions hold. Plan at 07:25 ET: **BUY SPY 192 @ 767.46 + BIL 404 @ 91.63** (97.6% of equity, SPY 79.9% of the sleeve). A 10:12 fallback check covers a stalled task |
| **Holdings invariant** | The rebalancer refuses and raises a CRITICAL report whenever the broker's SPY/BIL differ from what its own fills imply. Before this, a core sold behind its back would have been bought back as an "initial build" |
| **Paper-dividend test** | A nightly checker records whether Alpaca paper credits BIL's October distribution. The tracker labels the CORE line from the verdict |
| **Recurring rebalancer** | Windows task `MomentumX-CoreRebalancer`: weekdays 15:15 ET, wakes the PC, skips missed runs. It trades only on the month-end or ±5-point drift trigger |
| **Test isolation** | A full pytest run used to write into live files (70 writes, including today's `raw_fills` record, plus 22 live runner-log writes). It now writes **0** |
| **Suite** | Run under an independent write guard: 79 failed / 5,818 passed. **0 failures outside the pre-existing 80-test baseline**, and 0 live writes |

---

## 1. What was built

- **`scripts/core_rebalancer.py` hardening.**
  - **Holdings invariant.** It compares the ledger's booked fills (one terminal row per order) with `GET /v2/positions`. Any difference refuses with `UNEXPLAINED CORE HOLDINGS`, including a fraction, a short, or shares with an empty ledger. Both-zero passes, as on Monday's first run.
    - `--liquidate` only warns, so the emergency exit always works.
    - Nothing clears a mismatch automatically: a human either restores the broker or makes a reviewed ledger correction.
  - **Reports** go to the ops Discord webhook:
    - INFO when `--execute` places orders;
    - CRITICAL on unexplained holdings, a reconciliation failure, any exit 1, and every liquidation;
    - nothing on routine refusals or runs without a trigger.
    - A failed post never changes the exit code, and the URL is never printed.
  - **Output unchanged.** The protected `--check` lines that Monday's task parses are byte-identical to HEAD across 27 fixtures; the only addition is an `invariant` line.
- **`scripts/check_paper_distributions.py` (new).**
  - **Method.** For each SPY/BIL ex-date the core held, it compares the expected cash (shares × Polygon `cash_amount`) with Alpaca's DIV-family activities, inside a holiday-aware pay-date window.
  - **Verdicts.**
    - **CREDITS** needs a real in-window match.
    - **DOES_NOT_CREDIT** needs at least one judged miss *and* no dividend-type activity of any symbol since inception. Any unexplained or unreadable DIV row withholds it: unknown is not none.
  - It runs nightly in the scorecard, before the tracker.
- **`scripts/core_rebalancer_launcher.ps1` and `install_core_rebalancer_task.ps1` (new).**
  - **Launcher.** It logs to `logs/core_rebalancer_<date>.log` and skips weekends. Exit 2 counts as a refusal only when the rebalancer printed a `[core]` line; python's own exit 2 (missing script, usage error) is treated as an error. Capture files are kept if the log write fails.
  - **Installer.** It installed the task with WakeToRun on, StartWhenAvailable off, IgnoreNew and a 20-minute limit, under the same principal as the lottery task.
- **`scripts/pipeline_health_check.py`.** The new task is judged healthy on exit 0 or 2. It is RED on exit 1, on a weekday without a run, or if it has never run after 09-30.
- **`tests/conftest.py`.** Autouse fixtures redirect every test-time writer to tmp: fill capture, shadow logger, feature logger, experiment journal, alert spool, label shards and runner log handlers. **The live-data leak that let doc 304's fixtures pollute a live trace is closed for the whole suite.**

## 2. What the skeptics found and what was fixed

- **Round 1.**
  - **Rebalancer:** SOUND.
  - **Distribution checker:** 1 medium, 4 low. A credit booked under the wrong symbol could still yield "does not credit".
  - **Launcher:** 1 minor. Python's own exit 2 was read as a routine refusal.
  - **In passing:** a live alert-spool leak from one test.
- **Round 2.**
  - All fixed.
  - **New LOW items:** a misleading tracker note, an overflow on an absurd pay date, and the launcher deleting captures after a failed log write.
- **Round 3.** All fixed and SOUND. The session limit then killed the test-isolation builder before it reported.
- **Isolation verification.** An independent skeptic confirmed the unreported work.
  - **Scope:** additions only, no production file touched, no skip or xfail added.
  - **Control copy:** with the diffs removed, it made 90 writes to data/logs.
  - **Full suite:** 0 live writes, 0 new failures.

## 3. Standing operational facts

- **Keep the PC awake.** The Claude scheduled tasks need the PC awake and the app open, and cannot wake it. The bot's launcher and the new Windows task can wake it. 09-24 was lost to the PC being off, and nothing alerted.
- **No pytest during a session** until this commit is in place. It now is: `47423af`.
- **The suite still reads the live `.env` about 5,300 times** through `Settings()`. That is a read-side isolation gap, left open.

## 4. Provenance

- Private `47423af`, on top of `40560e9` + `fb2b4e7` (doc 307).
- The one-time Claude tasks: `momentum-x-core-initial-allocation` (Mon 10:05), `momentum-x-core-eod-verify` (Mon 16:40), `momentum-x-core-overnight-boot-verify` (Tue 09:45).
- Scratch: `doc308/` (rebalancer, distributions, schedule, fix_*, final_*, every verify / reverify directory, and verify_isolation).
