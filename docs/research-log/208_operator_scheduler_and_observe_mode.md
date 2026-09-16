# 208 — Operator scheduler + detector wiring + OBSERVE mode (Monday-ready)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "build the scheduler + detector wiring next. Let's see this working
for Monday!"

---

## 0. What shipped — the Operator runs (observe-only) on Monday

The clock + the senses + the visible heartbeat, all observe-only (T1 disarmed) so it's a
zero-risk shakedown:

1. **`scripts/register_operator_schedule.ps1`** — run ONCE (elevated) to create the
   `MomentumX-Operator` Task Scheduler job with **12 weekday triggers** at the runbook times
   (03:45 boot · 06:30 · 08:30 · 09:25 · **09:40 post-open** · 10:30 · 11:45 · 13:00 · 14:15
   · 15:50 · **16:10 close-out** · 17:30 deep-work). Mirrors the trading launcher's style.
2. **`scripts/operator_pulse.ps1`** — fired by each trigger: maps the time → a session label,
   skips weekends, runs the reliable observe session, and (gated by
   `OPS_OPERATOR_USE_CLAUDE_CLI`, default off) optionally invokes the richer Opus-4.8-MAX
   operator via `claude -p` once the CLI is confirmed headless. Never blocks trading.
3. **`scripts/operator_observe.py`** — the **observe-only session** (the guaranteed-working
   artifact): synthesize incidents → ORIENT brief → triage (classify only, **acts on
   nothing**) → **post to Discord** → append the daily operator log → record a restraint-aware
   score → clear the WAKE. Dry-run verified end-to-end (`clean=True`, log written).
4. **`src/ops/discord_notify.py`** — self-contained Discord post (stdlib urllib, reuses
   `OPS_ALERT_WEBHOOK_URL`, never-raises, isolated from the bot's alerting).
5. **`src/ops/incident_synth.py`** — **detector wiring, done safely**: derives incidents from
   the bot's EXISTING artifacts (`recon_status.json` → RECON_STATUS_BAD/CRITICAL; the day's
   log → LOG_CRITICAL_PATTERN / LOG_ERROR_RATE_HIGH) — **zero live-trading-code edits**,
   dedup'd. Real signal from day one.

## 1. Why artifact-derived detector wiring (not scattered live-code emits)

The cleanest, safest "detector wiring" for a Monday shakedown reads what the bot already
emits rather than surgically inserting `emit_incident()` into the hot path under time
pressure. `incident_synth` turns recon-status + the log into incidents with no live-code
risk. The `emit_incident()` contract is ready, so wiring it INTO specific detectors
(circuit breaker trip, drawdown halt, ghost detection) — for sub-minute real-time signal — is
a clean, verify-each-point follow-on, not a blocker for Monday.

## 2. How Monday works (and Pierce's one action)

- **Pierce runs once, elevated**: `.\scripts\register_operator_schedule.ps1`. (I can't —
  registering a scheduled task needs the elevated shell, same as the trading task.)
- Each weekday the Operator wakes 12× → posts a Discord heartbeat: "all clear ✅" or
  "⚠️ N CRITICAL/DECISION need attention" + the game line. The daily operator log
  (`docs/research-log/operator_log_<date>.md`) accumulates the briefs for the next session's
  ORIENT.
- **Observe-only** (`OPS_OPERATOR_T1_ENABLED` defaults armed in code, but the observe runner
  takes NO actions; the pulse runs observe-only). Nothing is touched.

## 3. The arm sequence (after the shakedown)

1. **This week**: watch the 12 Discord heartbeats + the operator logs. Confirm the briefs are
   accurate (deployed-commit, HALT/T1, incidents) and the synthesized incidents are real
   (low false-positive rate — the Goodhart-hardened score punishes FPs).
2. **Then** flip `OPS_OPERATOR_USE_CLAUDE_CLI=1` + `OPS_OPERATOR_MODEL=<opus-4.8-max>` once
   `claude -p` is confirmed to run headless in the scheduled context — the richer operator.
3. **Then** arm T1 (`OPS_OPERATOR_T1_ENABLED=true`) — the auto-remediation, on the governance
   rails (ladders, caps, soft fuse, confidence gate, reversibility test).
4. Wire `emit_incident()` into the specific live detectors for real-time signal.

## 4. Status
- 5 new files; the observe session dry-runs clean; both ps1 parse cleanly; the existing 22
  ops tests still green; `main` boots. **No live trading behavior change** — the Operator
  is observe-only and out of the bot's process.

## Appendix — files
- `scripts/register_operator_schedule.ps1`, `scripts/operator_pulse.ps1`,
  `scripts/operator_observe.py`
- `src/ops/discord_notify.py`, `src/ops/incident_synth.py`
- (writes) `docs/research-log/operator_log_<date>.md`, `logs/operator_pulse_<date>.log`
- This doc + `docs/SYSTEM_MAP/changelog.md`
