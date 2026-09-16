# 207 — Operator live-action layer: the T1 toolkit + the ORIENT context assembler

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "proceed straight into the live-action layer" (on the doc-206 hardened spec).

---

## 0. What shipped (the executable core, on the governance rails)

1. **A live HALT mechanism (the enabling fix).** D277's HALT was only checkable via the
   process env var (`MOMENTUM_HALT_NEW_ENTRIES`, set at launch) — which an *out-of-process*
   Operator can't set. Added a **file check** to the D277 helper (`alpaca_client.py`):
   it now also halts if `data/HALT_NEW_ENTRIES` exists, **checked LIVE on every submission**.
   Additive — it only ever ADDS a halt condition; positions, exits, stops, ratchets are
   never gated. This makes the Operator's flagship T1 work without a restart.

2. **`src/ops/operator_actions.py` — the T1 toolkit** (governance-gated, reversible):
   - `halt_new_entries(reason, confidence, session_count, daily_count)` — the flagship: passes
     `governance.t1_allowed(...)`, snapshots, drops `data/HALT_NEW_ENTRIES`. **LIVE this
     session.** Rollback (one line): `rm data/HALT_NEW_ENTRIES`. `clear_halt()` rolls it back.
   - `stage_flag_safer(flag, current, proposed, ...)` — steps a flag DOWN its declared safety
     ladder (governance-enforced) by atomically upserting the secrets file; **restart-STAGED**
     (config loads at launch) and says so. Rollback restores the prior value.
   - Every action returns an `ActionResult` with `ok / blocked / rollback_cmd / snapshot_id /
     applies` — `blocked=True` (a governance refusal) is distinct from an error.

3. **`src/ops/operator_session.py` — the ORIENT context assembler** ("never guess"):
   `assemble_context()` gathers the full state — deployed-commit-vs-HEAD, HALT/T1/fuse state,
   unresolved incidents (+ WAKE), in-flight T2, scorecard standings, recon/EOD-truth — and
   `render_brief()` prints the markdown brief the scheduled `claude -p` session reads FIRST.
   All reads guarded; assembling the brief never raises. (Decisions are the Claude session,
   guided by the runbook — this is the brief + the lifecycle, not the brain.)

Tests: 22 ops unit tests green (foundation + governance + actions); `main` boots; the brief
renders. **No live behavior change** until the scheduler is wired + T1 is armed.

## 1. The correctness catch (why verification matters)
The first cut dropped a `data/HALT_NEW_ENTRIES` file — but the bot didn't check a file, only
the env var (`halt_new_entries` isn't even a declared config field, so `.env` wouldn't bind
it). Verifying the actual D277 path caught it: the fix was to make the bot check the file
live (additive), so the Operator's halt is real, not a no-op. Same discipline the whole
session has run on — confirm the mechanism, don't assume it.

## 2. What's left (the final build → then dry-run → then arm T1)
- **`scripts/operator_pulse.ps1`** + a Task Scheduler job — fire `claude -p` **(Opus 4.8
  MAX)** at the 12 runbook times with the runbook as the prompt + `assemble_context` piped in.
- **Wire ~5 detectors to `incident_bus.emit_incident`** (CB trip, RECON_LETHAL, ghost,
  drawdown, fade-short candidate) — guarded, never-raises.
- **Discord post + cost-cap + the counterfactual / "almost did" / hypothesis logs** in the
  session lifecycle.
- **Observe-only dry-run** for a few sessions (T1 disarmed) → confirm triage quality + the
  score calibration → **then `OPS_OPERATOR_T1_ENABLED` on** (T1 armed).

## Appendix — files
- `src/data/alpaca_client.py` — D277 file-based live HALT check (additive).
- `src/ops/operator_actions.py` (new, 5 tests), `src/ops/operator_session.py` (new).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
