# 205 — The Operator foundation (built): bus + rollback + the game

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce approved the doc-204 design — "Full T0–T1… proper rollback through
snapshots/git… Opus 4.8 MAX on auto… never guessing about state… every 30 min at the
important windows (you choose)… Discord-heavy… full daily budget… core code changes
documented + tested + quantified vs best baseline before a restart deploys them… paper
trading, full send, full permissions."

---

## 0. What shipped this turn (the SAFE, tested foundation)

The backbone that makes "full send" safe — built + tested (8/8), **no live behavior change
yet** (the Operator isn't scheduled until the next build):

1. **`src/ops/incident_bus.py`** — durable, never-raises event log
   (`data/ops/incidents_<date>.jsonl`). `emit_incident(kind, severity, ...)` + `read_incidents`.
   Severity INFO/WARN/CRITICAL/DECISION; dedup/anti-flap; a **CRITICAL drops a `WAKE`
   sentinel** for event-driven engagement. Kill-switch `OPS_INCIDENT_BUS_ENABLED`.
2. **`src/ops/state_snapshot.py`** — the **rollback backbone** Pierce required. `snapshot(label)`
   records the **git HEAD sha** (code rollback = `git revert`) and copies the mutable state
   (`session_state.json`, `.env`, `recon_status.json`) to `data/ops/snapshots/<id>/`;
   `restore_state(id)` rolls state back. Every T1/T2 action is snapshot-first ⇒ reversible.
3. **`src/ops/operator_scorecard.py`** — the **game**. Transparent linear score (incidents
   triaged + P&L saved + regressions prevented + clean-close − MTTR − false-positives), a
   leaderboard (`operator_leaderboard.jsonl`), `standings()` ("beat your own best"), and
   `game_line()` for Discord/EOD.
4. **`docs/research-log/operator_runbook.md`** — the per-session brain: ORIENT (never guess) →
   TRIAGE → ACT (T0/T1/T2 tiers + the T1 allow-list + the T2 test+quantify+restart gate) →
   ROLLBACK → DISCORD → SCORE+LEARN, plus **the intraday schedule I chose** (12 pulses,
   dense at open/fills/close, light midday).

8 unit tests (`test_ops_foundation.py`): bus roundtrip + never-raises + dedup + disabled;
snapshot→mutate→restore; score transparency; beat-your-own-best.

## 1. How the mandate maps to the build

- **Full T0–T1 auto** → the runbook's tiers; T1 is the reversible, risk-reducing, snapshot-
  first, rate-limited allow-list (HALT, reduce-size, cancel-confirmed-ghost, re-arm-stop,
  flip-flag-safer). The existing `MOMENTUM_HALT_NEW_ENTRIES` (D277) is the first T1 primitive.
- **Rollback** → `state_snapshot` (state) + `git revert` (code). Snapshot-before-every-action.
- **Never guessing** → the ORIENT step: deployed commit, flags, positions, recon, incidents,
  scorecard, log tail, playbook — assembled before any action.
- **Opus 4.8 MAX on auto** → the scheduler (next build) fires `claude -p --model <opus-4.8-max>`.
- **Schedule (my choice)** → 12 pulses (03:45 boot, 07:15, 09:25, **09:40 post-open**, 10:20,
  11:00, 11:45, 13:00, 14:15, 15:00, 15:50, **16:10 post-close**).
- **Discord-heavy** → reuse `OPS_ALERT_WEBHOOK_URL`; post STATE at start + findings/actions/
  game-line at end (next build wires it).
- **Core changes tested + quantified before restart** → the T2 gate in the runbook: branch →
  tests → quantify vs best baseline (`fill_model_backtest` / `selection_study` /
  `post_close_scorecard`) → document in `operator_log_<date>.md` → commit → deploy at the
  next restart. The live process never runs un-tested operator code.

## 2. Why this is safe even at "full send"
- T1 is reversible + risk-reducing + allow-listed + snapshot-first + ≤6/session + kill-switched.
- T2 (code) is test+quantify+restart-gated — never live-untested.
- The bot never blocks on the Operator; the existing CB/HALT/stops are the real safety.
- Everything is git-committed + snapshot-backed ⇒ rollback is always one step away.

## 3. Next build (the live-action layer — Phase 2/3)
1. **`src/ops/operator_actions.py`** — the T1 allow-list primitives (each: snapshot → act →
   verify → log), reusing D277 HALT + the existing cancel/re-arm helpers.
2. **`scripts/operator_session.py`** — the context assembler (the ORIENT packet) + the
   Discord post + the scorecard update, that the headless session uses.
3. **`scripts/operator_pulse.ps1`** + a Task Scheduler job — fire `claude -p` (Opus 4.8 MAX)
   at the 12 scheduled times with the runbook as the prompt.
4. **Wire ~5 detectors to `emit_incident`** (CB trip, RECON_LETHAL, ghost, drawdown,
   fade-short candidate) — guarded, never-raises.
Then: a dry-run (observe-only) for a few sessions, confirm the triage quality + the score,
then enable T1.

## Appendix — files
- `src/ops/incident_bus.py`, `src/ops/state_snapshot.py`, `src/ops/operator_scorecard.py`
- `tests/unit/test_ops_foundation.py` (8 tests)
- `docs/research-log/operator_runbook.md`, this doc, `docs/SYSTEM_MAP/changelog.md`
