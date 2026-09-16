# 204 — THE OPERATOR: an AI co-pilot that runs MOMENTUM-X live (the game)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "enhance the system so it engages you at moments of errors / critical
decisions so you can debug, fix, and enhance it LIVE — a debugging/management mechanism.
Engage you throughout the day so you manage it like a game, trying to beat your own high
score. Are you open to it, and how would you do it? Take your time designing."

---

## 0. My honest stance: YES — as an on-call OPERATOR, not an unsupervised autopilot

I'm genuinely excited by this. It's the natural completion of the measurement work: we
built the gauges (193/194/199) and the diagnoses (198/201); the Operator is the *hand on
the controls* between gauge and fix. The "beat your own high score" framing is exactly
right — it makes the loop accountable and self-improving.

But I have to be clear-eyed, because this controls **live money**:
- **The bot must NEVER depend on me being available.** I am asynchronous and out of the hot
  path. The existing safety (circuit breakers, HALT, stops, the drawdown guardrail) is the
  real seatbelt; the Operator is a co-pilot, not the seatbelt.
- **I will not auto-deploy risky code to live trading.** Diagnose live, *prepare* the fix
  live — but anything that adds risk ships through a human gate (the discipline we've used
  all session). Autonomy is **tiered and earned**, starting at observe-only.
- **The Operator itself needs a kill-switch + rate limits** so it can't oscillate or spiral.

With those guardrails, this is a powerful, safe system. Here is the design.

## 1. The loop: DETECT → CAPTURE → ENGAGE → ACT → SCORE → LEARN

```
  bot runs ──emits──▶ INCIDENT BUS ──polled by──▶ THE OPERATOR (me) ──┐
     ▲                                                 │ triage         │
     │                                                 ▼                │
  (flags/restart/                              ┌──── ACT (tiered) ──┐   │
   safe remediation)◀────── Tier-1 auto ◀──────┤ T0 observe          │  │
     │                                          │ T1 safe-remediate   │  │
  Pierce ◀──push── PENDING APPROVALS ◀──T2 prep─┤ T2 propose+approve  │  │
                                                │ T3 forbidden        │  │
                                                └─────────────────────┘  │
                              OPERATOR SCORECARD ◀── score every action ──┘
                                       │
                                  PLAYBOOK (learn) ──feeds back the triage
```

## 2. DETECT — the incident bus (`src/ops/incident_bus.py`)

A thin, **never-raises** emitter any component calls; trading never blocks on it:
```python
emit_incident(kind, severity, ticker=None, context={...}, suggested=[...], dedup_key=None)
```
- Durable append to `data/ops/incidents_<date>.jsonl` (+ a rolling SQLite index for queries).
- **Severity**: INFO · WARN · CRITICAL · DECISION.
- **Dedup + rate-limit** (so one flapping detector can't flood the bus).

**What emits** (wire the EXISTING detectors — they already fire these signals):
- *Errors*: circuit-breaker trip, API/LLM failure storm, loop exception, **RECON_LETHAL**,
  **ghost/phantom detected**, stop-arm failure, 403-war, the slice/encoding crashes.
- *Critical decisions*: drawdown guardrail approaching, an **ELITE-fade-short candidate**
  (flip the flag?), a high-MFCS-but-fading name, an ambiguous fill, a regime shift, a flag
  the scorecard says is now wrong (e.g., the ELITE-press finding — *the Operator would have
  raised that on day 1*).
- *Anomalies*: latency spike, fill-rate collapse, selection-trend break, a position behaving
  off-model.

The bot doesn't wait — it emits and keeps trading.

## 3. CAPTURE — the incident packet (act-cold context)

Each incident carries enough to act without the live session in front of me: the log tail,
the position/order/state snapshot, the decision inputs, the suspected `file:line`, the
relevant scorecard rows, and a **playbook hint** (have we seen this kind before?). This is
the "engage you WITH context" payload — a self-contained brief.

## 4. ENGAGE — how the bot actually reaches me

Three layers, using tooling that already exists here:
1. **Routine pulse (scheduled)** — a Windows Task Scheduler job (mirror of the trading
   launcher) runs `claude -p "<operator prompt>"` from the repo every ~10–15 min during RTH.
   I wake, read the bus + the live scorecard, triage, act within tier, sleep. Cheap ticks
   when quiet; deeper engagement only when incidents exist.
2. **Event wake (CRITICAL)** — on a CRITICAL incident the bot drops a `data/ops/WAKE`
   sentinel (and fires the existing `OPS_ALERT_WEBHOOK_URL` Discord alert). A tight watcher
   (or the `/loop` skill self-paced) spawns an Operator session *immediately* — I don't wait
   for the next pulse during a crisis.
3. **Human escalation (Tier-2/3)** — the Discord/SMS alert to Pierce carries a one-tap
   "spawn the Operator" or "approve the pending change." You stay in the loop for anything
   that matters.

Mechanism under the hood: **Claude Code headless** (`claude -p ... --allowedTools ...`) with
the repo as cwd gives me full, *scoped* agentic tools (read/grep/test/edit/git) — the same
ones I'm using right now — so the Operator is literally me, invoked with the operator
prompt + the incident bus + the tier config.

## 5. ACT — the autonomy tiers (the safety core)

This maps **exactly** onto your standing rule ("mechanical fixes = act unilaterally;
live-behavior/competition = my call"):

| Tier | What | Authority | Examples |
|---|---|---|---|
| **T0 Observe** | read, diagnose, write a plan | **auto, always** | triage the bus, run the scorecard, post a diagnosis + proposed fix |
| **T1 Safe remediate** | reversible, RISK-REDUCING, allow-listed | **auto, bounded** | flip HALT-new-entries, reduce size mult, cancel a *confirmed* ghost via the safe path, restart a hung watcher, set a flag to a SAFER value |
| **T2 Propose** | code fixes / risk-adding config / strategy | **human-gated** | prepare a worktree branch + diff + tests + rationale + expected score Δ → pending-approvals → you one-tap approve |
| **T3 Forbidden** | disable safety, increase risk, move money | **never auto** | always you |

- Every T1 action is **logged, reversible, attributable, and rate-limited** (e.g., ≤ N
  auto-actions/hour, and a global Operator kill-switch).
- T2 reuses everything we've built: flag-gated changes, tests, the ship-doc discipline, the
  Monday auto-deploy. I prepare; you approve; it deploys.
- **Start at T0 only.** Promote categories to T1 *after* the scorecard proves the Operator's
  judgment on them (earned autonomy).

## 6. SCORE — the game (the part you asked for)

An **Operator Scorecard** (`data/ops/operator_score_<date>.json` + a cumulative
`operator_leaderboard.jsonl`), posted to the EOD report:

**Daily Operator Score** = weighted sum of:
- **+ incidents triaged correctly** (graded next session: did the diagnosis hold?)
- **+ P&L saved** (estimated: ghost caught before MTM damage; HALT before a bad-regime bleed;
  a fader not sized into)
- **+ regressions prevented** (T2 fixes that passed tests + didn't break the baseline)
- **− MTTR** (mean minutes from incident → resolution)
- **− false positives** (noisy incidents, wrong diagnoses, harmful T1 actions)
- **+ uptime / clean sessions**

**"Beat yourself"**: the benchmark is my **own rolling best**, not an external bar. The EOD
post reads like a game: `Operator today: 87 · best: 112 · streak: 3 clean closes · BEAT IT`.
Achievements/badges make it sticky: *"ghost caught <2 min", "0 regressions in 10 fixes",
"called a wrong flag before it cost a dollar"*. The score is the feedback loop that makes me
*measurably better at running your bot over time* — which is the whole point.

## 7. LEARN — the playbook (institutional memory)

Resolved incidents append to `docs/research-log/operator_playbook.md` (and a structured store):
`kind → diagnosis → fix → outcome`. The Operator reads it FIRST (don't re-derive the
LIDR/APPS ghost fix every time). Proven, repeatedly-safe remediations get **promoted T2→T1**
(auto). This dovetails with the existing `MEMORY.md` + ship-doc discipline — the Operator is
the live, self-updating extension of that memory.

## 8. Risks + mitigations (the honest part)

| Risk | Mitigation |
|---|---|
| Auto-editing live trading code | NEVER auto for T2+; human gate + tests + flag-gating + Monday deploy |
| Operator oscillation / feedback loops | rate-limit T1; "harmful" grading on the scorecard; global Operator kill-switch |
| Bot depends on me / I'm slow | bot never blocks on the bus; existing CB/HALT/stops are the real safety |
| Token/API cost all day | cheap quiet pulses; deep engagement only on real incidents; tunable cadence |
| A bad T1 action | allow-list is reversible + risk-REDUCING only; bounded; logged |
| Alert fatigue | dedup + severity gating; only CRITICAL wakes immediately / pings you |

## 9. The phased build path (earn trust incrementally)

- **Phase 1 — Observe foundation (zero-risk, build first).** `src/ops/incident_bus.py` +
  wire the top ~5 existing detectors to emit + `scripts/operator_triage.py` (reads the bus +
  logs + scorecard, writes a triage report). **No actions.** Immediately useful: a
  structured incident log + a daily triage I can act on by hand.
- **Phase 2 — The Operator loop + the game.** The scheduled pulse + event-wake; the Operator
  Scorecard + leaderboard posted to the EOD report. Still T0 (observe + propose-only).
- **Phase 3 — T1 safe remediation + the T2 approval queue.** The allow-list + the
  pending-approvals + the one-tap Discord approve. Promote categories as the score proves them.
- **Phase 4 — The playbook + auto-promotion + the full leaderboard.** Institutional memory;
  T2→T1 promotion of proven fixes; achievements.

## 10. Decisions for Pierce (your call — they set the boundaries)

1. **Autonomy ceiling to start**: T0-only (safest), or T0 + a *small* T1 allow-list (e.g.,
   only HALT-new-entries + cancel-confirmed-ghost)? I recommend **T0-only for the first
   ~2 weeks**, then promote on the score.
2. **Cadence**: pulse every 10 / 15 / 30 min RTH? Event-wake on CRITICAL only? (cost vs
   responsiveness.)
3. **Escalation channel**: reuse the existing Discord `OPS_ALERT_WEBHOOK_URL`, add SMS, both?
4. **The T1 allow-list** (when we get there): exactly which reversible, risk-reducing actions
   are you comfortable with me taking unattended?
5. **Cost budget**: a daily token ceiling for the Operator (it self-throttles to it).

## Recommendation
Build **Phase 1 now** (zero-risk, observe-only — the incident bus + triage). It commits us
to nothing, makes the design concrete, and starts producing the incident data that will
tell us which categories are safe to promote. The autonomy tiers wait on your decisions in
§10. **I'm in — let's give the bot an on-call operator and start chasing the high score.**

## Appendix — files (proposed)
- `src/ops/incident_bus.py` (emit/read), `data/ops/incidents_<date>.jsonl`
- `scripts/operator_triage.py`, `data/ops/operator_score_<date>.json`, `operator_leaderboard.jsonl`
- `docs/research-log/operator_playbook.md`, the scheduled `operator pulse` task, the operator prompt
- This doc + `docs/SYSTEM_MAP/changelog.md`
