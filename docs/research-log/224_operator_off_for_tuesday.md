# 224 — Operator OFF for the Tuesday (6/2) deploy

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "turn our operator off for now."

For Tuesday's deploy the focus is the execution-hardening arc (216/218/219/220) + the
raw-fill capture (the B1 Phase-1 ground-truth goal). The Operator's observe pulses + Discord
heartbeats are noise we don't need during that — so it's turned OFF, cleanly and reversibly.

## What "off" means (precise scope)

A master kill-switch `OPS_OPERATOR_ENABLED` (default "1"). When `0`/`false`:
- `scripts/operator_pulse.ps1` exits before spawning anything (logs "DISABLED").
- `scripts/operator_observe.py main()` returns a clean no-op even if invoked directly.
- ⇒ no observe sessions, no Discord heartbeats, no scoring, no `claude -p` (already gated
  off separately by `OPS_OPERATOR_USE_CLAUDE_CLI`), no T1.

Belt-and-suspenders: also pinned `OPS_OPERATOR_T1_ENABLED=false` (doc 223 §1.D — it was
default-true-but-unreachable; now off explicitly).

## What STAYS ON (these are NOT the Operator — important)

The kill-switch deliberately does NOT touch:
- **Raw-fill capture** (`fill_capture`, websocket-side) — *this is the Tuesday goal*; it
  writes `data/ops/raw_fills_<date>.jsonl` to confirm the `execution_id` key.
- **Incident bus** (`incident_bus`, bot-side) — the bot itself still writes CRITICAL
  naked/phantom/ghost escalations (docs 212/218); the bus is the bot's safety telemetry, not
  the Operator.
- **Post-close scorecard + Adversary battle** — wired into the *trading launcher's* Phase-4
  tail (doc 194/219a), not the Operator pulse; still runs after close.
- **EOD reporting, recon, all execution-hardening** — untouched.

So Tuesday still gets: hardened closes, honest EOD report, same-session phantom detection,
the post-close scorecard + adversary battle, and the raw_fills capture — just without the
12 Operator heartbeats.

## How it's set (where the flag lives)

The scheduled `MomentumX-Operator` task runs in its OWN process, so the authoritative switch
is a **User-scope env var** (`[Environment]::SetEnvironmentVariable('OPS_OPERATOR_ENABLED','0','User')`).
Also mirrored into `~/momentum-x-secrets.env` + `.env` for documentation + the observe-runner's
`load_dotenv` path. Verified: `OPS_OPERATOR_ENABLED=0 operator_observe.py` → "DISABLED … no-op".

## Re-enabling later (after the Tuesday deploy + a deliberate Operator shakedown)

1. `[Environment]::SetEnvironmentVariable('OPS_OPERATOR_ENABLED','1','User')` (or set in secrets).
2. The 12 daily observe pulses + Discord heartbeats resume immediately (task is still
   registered; the kill-switch just no-ops it).
3. Separately, when ready: `OPS_OPERATOR_USE_CLAUDE_CLI=1` (richer operator) then
   `OPS_OPERATOR_T1_ENABLED=true` (autonomy) — each deliberately, after the observe data
   confirms triage quality (doc 212 lesson: the Operator was blind on day 1 until
   incident_synth was hardened; arm only after a clean observe stretch).

## Appendix — files
- `scripts/operator_observe.py` (master kill-switch in `main()`, +`import os`),
  `scripts/operator_pulse.ps1` (early-exit gate; AST-parse-verified).
- env (local, not committed): `OPS_OPERATOR_ENABLED=0`, `OPS_OPERATOR_T1_ENABLED=false` (User
  scope + secrets + .env). This doc + changelog.
