# SYSTEM_MAP — momentum-x source-of-truth

**Read this file first.** Every agent (human or LLM) working in this repo
should orient via SYSTEM_MAP before reading any specific `docs/research-log/` doc.
The 174+ ship docs are *append-only history*; SYSTEM_MAP is *current state*.

---

## What this system is

momentum-x is a paper-trading bot for sub-$50 gap-up momentum stocks
on Alpaca. Pipeline: pre-market scanner → 6-agent ensemble (LLM +
deterministic) → MFCS composite → conditional debate → XGBoost
meta-scorer with tier classification → Kelly sizing → OTO submission
with L1+L2 hedge integrity defense. ~5–10 candidate evaluations per
session, 1–8 actual trades, target +5–10%/day on $140k paper account.

Architecture and operational details: [`architecture.md`](architecture.md).

---

## Read order for a new agent

| Order | File | Why |
|---|---|---|
| 1 | `INDEX.md` (this file) | Orientation |
| 2 | `changelog.md` | What changed most recently — cheap recency context |
| 3 | `architecture.md` | The 11-layer pipeline + defense wraparound |
| 4 | `d_codes.md` | Canonical D-code registry (TOML blocks, machine-queryable) |
| 5 | `experiments.md` | What's been tested + pending decision dates |
| 6 | `backlog.md` | Priority-tiered work items + dependencies |
| 7 | `models.md` | Per-layer ML/scoring components + calibration data |
| 8 | `monitoring.md` | Alert taxonomy + runbook index |
| 9 | `glossary.md` | Acronyms + jargon defined once |
| — | `data.md` | Lake structure, sources, pipelines |

Skip directly to a specific file when the task is narrow (e.g. "what
does D310 do" → `d_codes.md`).

---

## Where to look for X

| Question | File |
|---|---|
| "What does D310 do? When was it shipped?" | `d_codes.md` |
| "What experiments are currently in shadow mode?" | `experiments.md` |
| "What's the next thing we should build?" | `backlog.md` (Tier-S first) |
| "Why does the bot do X at step Y?" | `architecture.md` then specific D-code |
| "What's the OPS Discord runbook for HEDGE_VIOLATION?" | `monitoring.md` → `docs/RUNBOOK_HEDGE_VIOLATION.md` |
| "When was the last commit to the orchestrator?" | `git log src/core/orchestrator.py` |
| "What was the verdict on T1 stop-widening?" | `experiments.md` |

---

## The single source-of-truth invariant

**If a SYSTEM_MAP file disagrees with a `docs/research-log/N.md` ship doc, SYSTEM_MAP wins.**

Ship docs are append-only history. They are correct *as of their date*.
SYSTEM_MAP is *current state*. When a later ship doc supersedes an
earlier decision, SYSTEM_MAP must be updated in the SAME commit (see
discipline below).

---

## Discipline: the pre-commit hook

Every commit that adds a new `docs/research-log/N.md` ship doc MUST also
stage a change to `docs/SYSTEM_MAP/changelog.md`. Enforced
structurally via `.githooks/pre-commit`.

```
docs/research-log/175_v6_2026_05_25_T3_cutover.md  ADDED
docs/SYSTEM_MAP/changelog.md                  MODIFIED  ← required
```

Activation (one-time per clone): `git config core.hooksPath .githooks`.

Bypass: `git commit --no-verify` (logged in reflog; defeats discipline;
emergencies only). If `--no-verify` is used, the operator MUST follow
up with a SYSTEM_MAP update commit within 24h.

---

## Schemas (locked 2026-05-24)

### `d_codes.md` — one TOML block per code

```toml
[D310]
label = "STOP_WIDENING_SHADOW / T2 arm assignment"
status = "ACTIVE"          # ACTIVE | SHADOW | DEPRECATED | FILED | REVERTED
shipped_date = "2026-05-19"
docs = ["169", "171", "174"]
supersedes = []
superseded_by = []
depends_on = ["D142", "D122", "D294"]
gates = ["MOMENTUM_T2_ENABLED"]
category = "alpha"         # alpha | safety | observability | data
one_line = "Hash-keyed 50/50 split: wide arm uses ATR + halved qty + standalone STOP."
```

**Validation invariants (`_linter.py` will enforce):**
- If `superseded_by != []`, `status` MUST be `DEPRECATED` or `REVERTED`.
- Bidirectional pointers must agree: if `A.supersedes` contains `B`,
  then `B.superseded_by` must contain `A`.
- All `depends_on` entries must resolve to either another D-code in
  this file OR a `module:path` reference (e.g., `src/execution/bridge.py`).
- `category` is mandatory for `ACTIVE` status.

### `experiments.md` — `decision_date` mandatory for SHADOW/LIVE

```toml
[Continuer_v2]
status = "SHADOW"                # PASS | FAIL | REVERT | SHADOW | LIVE | DELETED
decision_date = "2026-06-25"     # weekly hygiene script fails on past decision_date without verdict
disposition_if_pass = "ship as Kelly multiplier on tier output"
disposition_if_fail = "DELETE scripts/continuer_v2_*.py on 2026-07-15"
docs = ["155", "162"]
one_line = "Stacked ensemble (XGB+LGBM+Cat+LR+RF -> LR meta) on T+5 forward return."
```

### `backlog.md` — one block per work item

```toml
[D310_L3]
priority = "S"               # S | 1 | 2 | 3
effort_hours = 6
filed_in = ["169", "171"]
blocks = ["T3", "Move2"]
blocked_by = []
target_date = "2026-06-15"
description = "FSM rewrite OR deletion via Move 2 (data-dependent)."
```

---

## Monday 2026-05-25 launch sequence

Written 2026-05-24 evening when this skeleton was committed. **Do not
reconstruct under fatigue at 06:00 — read this.**

| Time (ET) | Action | Output |
|---|---|---|
| 06:00 | Wake. Black coffee. No phone yet. | — |
| 06:15 | Read `changelog.md` (= last 3 entries) + `experiments.md` SHADOW table | Mental refresh on system state |
| 06:30 | Write `docs/research/cascade_anti_selection_outline.md` (DRAFT-INTERNAL, 2 pages: hypothesis, methodology, dataset spec, claims, target venue) | Outline file exists |
| 07:00 | Audit `feature_logger.log_evaluation()` against outline's "dataset spec" — does it capture every field the paper needs? List gaps. | List of missing fields (probably: `execution_arm`, `qty_multiplier`, per-gate rejection reason) |
| 07:30 | Fix `feature_logger` gaps (observability, not freeze violation). Must land before first T2 verdict. | Commit ships, tests green |
| 08:15 | Populate `d_codes.md` with 10 worked examples (D101, D116, D277, D278, D281, D290, D293, D295, D310, D313). Proves the schema, seeds the rest. | First 10 entries land |
| 08:45 | Run `_linter.py` against the 10 entries. Fix any invariant violations. | Linter exits 0 |
| 09:00 | Status check: bot scheduled to launch at 04:30 already happened. Look at log for D315 boot self-test in Discord. | Confirmed bot is alive + all 4 watchers OK |
| 09:25 | Final pre-open checks per `RUNBOOK_HEDGE_VIOLATION.md` | Pin runbook in browser tab |
| 09:30 | T2 market open. Watch first wide_stop fills in Discord. | First T2 data |
| Market hours | Prose work: fill remaining SYSTEM_MAP files (`models.md`, `monitoring.md`, `data.md`, `glossary.md`, `architecture.md` if time). Low-cognitive-load complement to dashboard-watching. | SYSTEM_MAP grows |
| 16:00 | T2 EOD. Read Discord EOD report. Update `changelog.md` with whatever D-codes need amendment based on what fired today. | First test of the discipline |
| 16:30 | Tuesday plan: 124-window tick audit + force Continuer v2 ship-or-delete confirmation. | Tuesday queue confirmed |
| 17:00 | Close laptop. | — |

---

## Abort criteria for Monday

If any of the following land in Discord, **stop and triage**:

- D315 boot self-test does NOT post within 60s of `schtasks /run` → revert `MOMENTUM_T2_ENABLED=0`
- ≥3 `D313 HEDGE_VIOLATION` in single session → abort T2 per `RUNBOOK_HEDGE_VIOLATION.md` repeated-violation section
- Any `D313 EMERGENCY_STOP_FAILED` → manual stop + investigate before next session
- Wide-arm cumulative P&L < −$2,000 by close → revert per doc 172 abort criteria

Do NOT attempt to live-fix trading paths. The code freeze stands until
Tuesday 16:00 ET regardless of how clean the proposed fix looks.

---

## Provenance

- **Genesis commit:** 2026-05-24 evening (this commit)
- **Authored by:** Claude Opus 4.7 (1M ctx) + the operator review cycles
- **Predecessor docs:** 170 (weekly deep dive), 171–174 (3-layer T2 rollout
  + safety hardening + Pierce's reviews)
- **Next ship doc:** 175 (T3 cutover OR Tuesday SYSTEM_MAP fill-out,
  whichever lands first). MUST update `changelog.md` per pre-commit hook.
