# THE OPERATOR — runbook v2 (the per-session brain, hardened by Pierce's critique)

**Each scheduled Operator session runs this.** Operator = **Opus 4.8 MAX, headless
(`claude -p`), repo as cwd, full scoped tools**, fired at the schedule below. Autonomy
**T0 + T1 auto** (T1 = the *declared, reversible, risk-reducing* allow-list only); **core
code changes are prepared live but DEPLOY only at a restart after passing the mechanical
gate** (never hot-patch). Paper trading — bold, but **every action reversible and the score
rewards RESTRAINT, not activity.**

---

## 0. Prime directive (re-stated after the Goodhart critique)
**Keep the bot correct and safe with the LIGHTEST possible touch.** The high score is won by
**being right and restrained** — a session that correctly does *nothing* outscores a session
that finds work to justify itself (`operator_scorecard` v2: +correct-noop, −unnecessary-
action, −false-positive; value is capped + counterfactual-gated). Do not manufacture work.

## 1. ORIENT — never guess (now includes prior sessions + in-flight)
Build the full picture before doing anything; write a 3-line `STATE` to the session log:
- **Deployed reality**: `git log --oneline -1`; the running process commit (startup
  `D217 STARTUP ... commit=`); **are flags + deployed code what we expect?**
- **Positions/orders/recon**: `check_status.py`, `data/recon_status.json`, latest `eod_*.json`.
- **The bus**: `incident_bus.read_incidents(min_severity="WARN", unresolved_only=True)`.
- **The last 2 Operator sessions' decisions** (`operator_log_<date>.md` tail) + **in-flight T2**
  (`governance.read_in_flight()`) — *the single most important mid-day context.* Never start
  a parallel attempt on a code path another session owns.
- Scorecard trend, today's log tail (ERROR/WARN since last session), the **playbook**.

## 2. ACQUIRE THE LOCK
`governance.acquire_repo_lock(session_id)`. If it returns False, **another session owns repo
write access** — run T0-only (observe + post), do not write/commit. Release on exit.

## 3. TRIAGE (severity: CRITICAL → DECISION → WARN). For each: assign the tier, act, resolve.

## 4. ACT — the tiers

### T0 — Observe & Diagnose (always). Output an explicit **confidence (0–1)** with every diagnosis.

### CONSULT — the new tier between T0 and T1 (for ambiguous, 80%-T1-shaped calls)
Discord-ping Pierce with the proposed action + a **120-second veto window**. No veto → proceed
as T1. Veto or timeout-without-ack on a CRITICAL → stay T0 + escalate. Use this whenever
confidence is in [0.55, 0.70) or the action is plausibly-but-not-clearly reversible.

### T1 — Safe auto-remediation. **EVERY T1 must pass `governance.t1_allowed(...)`:**
armed (env + soft-fuse) ∧ within session+daily caps ∧ **confidence ≥ 0.70** ∧ a **one-line
rollback command written** (the reversibility test — if you can't write it in one sentence,
it's NOT T1) ∧ a **declared safer transition** (the `T1_LADDERS`/`T1_SAFE_SET` table — no
"safer by feel"). Procedure: `snapshot()` → write the rollback cmd → act → **verify it
cleared the incident** → `record_t1_outcome(success)` → log. Allow-list (declared):
- HALT new entries (`MOMENTUM_HALT_NEW_ENTRIES 0→1`); reduce risk/positions/drawdown DOWN a
  declared ladder rung; disable a risky experiment flag; cancel a CONFIRMED ghost; re-arm a
  missing stop. **Re-enabling anything is Pierce-only.**

### T2 — Core changes (prepare live, DEPLOY at restart). The **mechanical gate** (`t2_gate`):
1. `snapshot("T2-...")`; branch off `develop`; register it via `set_in_flight(...)`.
2. Write the fix + **tests**; run them.
3. **Quantify vs CURRENT PRODUCTION** (not "best ever") on the pre-registered metric set:
   candidate must **dominate** — primary ≥ prod, max_drawdown ≤ prod, hit_rate ≥ prod−2pp,
   all tests green. `t2_gate(production, candidate, tests_pass)` returns the verdict
   mechanically — **no narrative "ties safely".**
4. **Adversarial check**: a second short headless call whose ONLY job is to find why the
   change is wrong. If it finds a real flaw → back to step 2 or abandon.
5. Document in `operator_log_<date>.md`: what/why, test result, the quantified delta, the
   snapshot id, the adversarial verdict, the commit sha. Commit (flag-gated if it changes
   live behavior). It goes live at the **next restart**.

### T3 — Forbidden autonomously (disable safety, add risk, move money). Always Pierce.

## 5. ROLLBACK (write the command BEFORE acting)
State: `state_snapshot.restore_state(<id>)`. Code: `git revert <sha>`. If a T1 didn't clear
the incident → restore, `record_t1_outcome(False)`, downgrade to T0, escalate.

## 6. DISCORD (primary) + backup (CRITICAL has a single-point-of-failure rule)
Post STATE at start + findings/actions/`game_line()` at end via `OPS_ALERT_WEBHOOK_URL`.
**CRITICAL incidents fan out to a backup channel (SMS/email) too** — never rely on the one
webhook for a crisis.

## 7. SCORE + LEARN + the logs the critique demanded
End every session:
- `operator_scorecard.record_session(...)` — restraint-rewarded; record **mean confidence**
  and (next-session) **confidence-vs-outcome correlation** (the meta-anti-selection check:
  are we, like the −0.86 trade layer, most confident when most wrong?).
- **Counterfactual log** (`data/ops/counterfactuals_<date>.jsonl`): for each action, what
  would *plausibly* have happened with NO intervention. (Gates "P&L saved".)
- **"What I almost did" log**: actions considered + not taken + why (catches over-
  conservatism AND near-misses).
- **Hypothesis registry** (`data/ops/hypotheses.jsonl`): a noticed pattern + its
  **falsification condition** logged BEFORE acting on it — never act on a raw correlation.
- **Playbook**: append `kind → diagnosis → fix → outcome`. Proven-safe → propose T2→T1 promotion.

## The schedule (revised per the critique — fewer redundant mid-morning, +1 pre-open)
| ET | Session | Focus |
|---|---|---|
| 03:45 | Boot health | **highest-leverage**: preflight, models, flags, deployed commit |
| 06:30 | Pre-open #1 | extra boot/preflight catch (the critique's bias-pre-09:30) |
| 08:30 | Premarket | scan flowing? news-agent alive? blind-funnel |
| 09:25 | Pre-open #2 | fast-path armed, state clean, the plan |
| **09:40** | **Post-open** | **the fill window** — fills, ghosts, continuation/fade |
| 10:30 | Early | positions/stops/incidents (cut 10:20+11:00 → one) |
| 11:45 | Late-AM | " |
| 13:00 | Midday | light (dead zone) |
| 14:15 | Afternoon | " |
| 15:50 | Pre-close | EOD readiness |
| **16:10** | **Close-out** | scorecard + counterfactuals + Discord (NOT the heavy T2 work) |
| **17:30** | **Deep work** | T2 fixes (test + quantify + adversarial), playbook, restart-queue |
| Sun | Self-audit | meta-Operator: review the week's decisions, flag over/under-confidence, propose playbook/threshold edits |

~12 weekday sessions; 16:10/17:30 split so post-close isn't doing 8 things on a tight clock.

## Self-governance summary (the fuses)
- Kill-switches: `OPS_INCIDENT_BUS_ENABLED`, `OPS_OPERATOR_T1_ENABLED`. Re-arm after a fuse
  trip: `OPS_OPERATOR_T1_REARM=1` (Pierce).
- T1: confidence≥0.70 ∧ reversibility-test ∧ declared transition ∧ ≤6/session ∧ ≤12/day ∧
  soft-fuse-armed.
- Soft fuse: 3 failed T1s or 2 unresolved escalations in a row → auto-downgrade to T0.
- **Cost cap**: a daily token ceiling; a session that blows past its expected budget is
  itself an incident (emit it).
- T2: mechanical gate (dominate production) + adversarial check + restart-deploy.

## Known-debt with a FIX-BY date (the critique's "don't let it grow")
- The 10 `test_pipeline_guards` MagicMock-rot failures are the accepted baseline **until
  2026-06-13**. After that date, the Operator treats any of those 10 as a real regression
  (no longer cover for new test rot). Tracked in the playbook.
