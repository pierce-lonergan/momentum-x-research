# 206 — Operator v2: Pierce's critique incorporated (the hardened protocol)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce's design review of doc 204/205 — "make the changes you think are right
(the highest-ROI engagement protocol), then proceed into the live-action layer."

Pierce's critique was excellent and every point is incorporated. This doc records what
changed and why; the next doc builds the live-action layer on this hardened spec.

---

## 1. The scoring Goodhart trap → FIXED (the biggest risk)

v1 score maximized `incidents triaged + P&L saved + ...` — which rewards inflated incident
counts, post-hoc unfalsifiable P&L, and ACTIVITY on quiet days. **v2 (`operator_scorecard`)**:
- **Restraint is the win**: `correct_noop` is the headline reward (+25). A session that
  correctly does nothing outscores a busy one. `unnecessary_action` is taxed (−20).
- **"incidents triaged" removed** as a positive (it rewarded inflation).
- **Value is gated + capped**: `pnl_saved` counts ONLY with a logged counterfactual;
  `regression_prevented` ONLY when the mechanical T2 gate verified a real one; total value
  is capped (60) so it can never dominate hygiene.
- **MTTR penalized only when action was warranted** (no fast-and-dirty-T1 pressure).
- **Calibration tracked**: mean confidence vs retrospective outcome — the meta-anti-selection
  check (below). Tests pin "restraint > activity" and "value gated + capped".

## 2. Definitional gaps → made MECHANICAL (`operator_governance`)

- **"Reduce size by how much"** → **declared transition ladders** (`T1_LADDERS`):
  `EXEC_RISK_PER_TRADE_PCT [0.02→0.01→0.0075→0.005]`, `EXEC_MAX_POSITIONS [8→6→4→2→0]`,
  `EXEC_DAILY_DRAWDOWN_LIMIT_PCT [0.05→…→0.02]`. The Operator may ONLY step toward safety —
  never "safer by feel". Boolean `T1_SAFE_SET` (HALT, disable ELITE press / fade-short) can
  be set to the safe value only; **re-enabling is Pierce-only.**
- **"ties safely" / "best baseline"** → `t2_gate(production, candidate, tests_pass)`:
  reference is **CURRENT PRODUCTION** (not "best ever" — that ratchets into never shipping);
  candidate must **dominate** on a pre-registered set (primary ≥ prod, max_drawdown ≤ prod,
  hit_rate ≥ prod−2pp, tests green). Mechanical verdict, no narrative.
- **The 10 pipeline_guards MagicMock-rot fails** → a **FIX-BY date (2026-06-13)** in the
  runbook; after it, those count as real regressions (no cover for new rot).

## 3. Session coordination → ADDED

- **Repo write LOCK** (`acquire_repo_lock`): exclusive per session; a session that can't get
  it runs T0-only. Stale-lock breaking.
- **In-flight T2 registry** (`in_flight.json`): a session sees what T2 work is mid-flight and
  never starts a parallel attempt on the same path.
- **ORIENT now reads the last 2 Operator sessions' decisions** — the critique's "single most
  important mid-day context".

## 4. Self-governance → HARDENED

- **Daily T1 cap (12)** in addition to the per-session cap (6).
- **Soft fuse** (`record_t1_outcome` / `record_escalation`): 3 failed T1s or 2 unresolved
  escalations in a row → auto-downgrade to T0 until Pierce re-arms (`OPS_OPERATOR_T1_REARM`).
  `OPS_OPERATOR_T1_ENABLED` was too coarse; this is the soft fuse the critique asked for.
- **Confidence gate**: T1 unavailable below 0.70 regardless of allow-list eligibility.
- **Adversarial check on T2**: a second short headless call that ONLY tries to refute the
  change, before commit.
- **The explicit reversibility test** (the philosophical forcing function): a T1 is only T1
  if the Operator writes the exact one-line rollback command first. Enforced in
  `reversibility_ok` + `t1_allowed`.

## 5. The meta-anti-selection check (the sharpest point)

MOMENTUM-X's own research found LLM consensus is anti-predictive at the trade layer (−0.86).
The same dynamic can recur at the meta layer: an LLM Operator may be most confident exactly
when most wrong. **Instrumented**: every diagnosis logs a confidence; the scorecard tracks
confidence-vs-outcome correlation; the Sunday self-audit reviews it. If we see the inverted
curve, we *lower* the confidence the Operator is allowed to act on — the system watches
itself for its own known failure mode.

## 6. Added capabilities (the critique's "things to add")

- **CONSULT tier** (between T0 and T1): Discord-ping Pierce with a 120s veto window for
  ambiguous, 80%-T1-shaped calls (confidence in [0.55, 0.70) or plausibly-reversible).
- **Counterfactual log** (gates "P&L saved"), **"what I almost did" log**, **hypothesis
  registry** (pattern + falsification condition logged BEFORE acting — no acting on raw
  correlations), **weekly self-audit** (Sunday meta-Operator), **cost cap** (over-budget
  session = an incident), **backup escalation** (CRITICAL fans out beyond the one webhook).

## 7. Schedule (revised)
Cut redundant mid-morning (10:20/11:00/11:45 → 10:30 + 11:45); **+1 pre-open** (06:30) since
boot/preflight is the highest-leverage catch; **split 16:10 (close-out) / 17:30 (deep T2
work)** so post-close isn't doing eight things on a tight clock; **+ Sunday self-audit.**

## 8. What shipped this turn (tested)
- `operator_scorecard.py` v2 (Goodhart-hardened) — restraint-rewarded, value gated+capped.
- `operator_governance.py` (NEW) — ladders, caps, soft fuse, confidence gate, reversibility
  test, mechanical T2 gate, repo lock + in-flight. 9 tests.
- `operator_runbook.md` v2 — every point above, the CONSULT tier, the revised schedule, the
  fix-by date.
- 17 ops unit tests green.

## 9. NEXT (the live-action layer, built on THIS spec)
`operator_actions.py` (T1 primitives, governance-gated) + `operator_session.py` (the ORIENT
context assembler + lifecycle: lock, consult, counterfactual/almost-did/hypothesis logs,
score, Discord) + `operator_pulse.ps1` + Task Scheduler (`claude -p` Opus 4.8 MAX) + wire
~5 detectors to emit → observe-only dry-run → enable T1.

## Appendix — files
- `src/ops/operator_scorecard.py` (v2), `src/ops/operator_governance.py` (new)
- `tests/unit/test_ops_foundation.py` (updated), `tests/unit/test_operator_governance.py` (new)
- `docs/research-log/operator_runbook.md` (v2), this doc, `docs/SYSTEM_MAP/changelog.md`
