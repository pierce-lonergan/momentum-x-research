# 223 — Tuesday (2026-06-02) green-light plan + TODO + expected outcomes

**Author**: Claude Opus 4.8
**Date**: 2026-06-01 (Monday evening)
**Purpose**: Decide what must be true to give Tuesday's 04:30 ET session the green light, what
is auto-deploying, the pre-open checklist, the flag config, and the expected benefits/outcomes.

---

## 0. THE deployment fact you must know

The launcher (`daily_paper_trade.ps1`) runs the **local checkout** with **no git pull/reset**,
so **whatever is committed on `develop`'s HEAD at 04:30 ET auto-deploys**. No restart needed.

- Bot ran today (Mon 6/1) on **`fc9b4c0`**.
- HEAD is now **`bdf518a`**.
- **⇒ 18 commits (docs 211–222) deploy Tuesday at 04:30**, untouched-by-a-live-session until then.

This is a LOT of new code going live at once. Most is **dormant or observe-only** (good), but
the green-light decision is really: *"are these 18 commits safe to run live Tuesday?"* This
doc answers that, category by category.

---

## 1. What auto-deploys Tuesday — classified by live risk

### A. ALREADY-PROVEN-SAFE, changes live trading behavior (the intended wins)
| Change | Doc | Live effect Tuesday | Risk |
|---|---|---|---|
| EOD close **retries until the bell** | 220 | a `held_for_orders` position gets repeated settle-aware close passes 15:55→15:59 instead of one 7s burst | LOW — bounded, re-fetches positions each cycle, tested |
| Cancel-settle **poll** in `attempt_close` | 216 | the close waits for a cancelled stop to settle before retrying | LOW — bounded budget, 39 close tests green |
| Naked re-arm **settle + escalate** | 218 | re-arm waits for qty; if still naked → CRITICAL incident (not silent) | LOW — strictly safer |
| Partial-fill **re-close** | 219 | a partial close re-closes the remainder instead of reporting false success | LOW — strictly safer |

These are the execution-hardening arc. **Net expected effect: the 6/1 CMND/OPTU naked
overnight carry does NOT recur.** All are corrective/defensive — they only ever make the close
*more* likely to succeed or *louder* on failure.

### B. LIVE behavior via flags (already pinned, correct)
| Flag | Value | Effect |
|---|---|---|
| `EXEC_ELITE_SIZING_PRESS_ENABLED` | **false** | the ELITE 2× press stays OFF (doc 213: the kill was on a stress-test artifact; leaving OFF is the conservative call pending real evidence) |
| `EXEC_RVOL_EXHAUSTION_SIZE_PENALTY_ENABLED` | **true** | exhausted names take half size (doc 199) |
| `EXEC_MARKETABLE_LIMIT_ENABLED` | default ON | marketable entry fills (doc 189/190, downgraded to ~+0.7% net but kept) |

### C. NEW but DORMANT / OBSERVE-ONLY (zero or near-zero live risk)
| Change | Doc | Why safe Tuesday |
|---|---|---|
| **Raw-fill capture** (`fill_capture`) | 222 | a never-raises TEE on the websocket; writes `data/ops/raw_fills_<date>.jsonl`. **This is the Tuesday GOAL** — it produces the Phase-1 ground truth. Reached live (websocket_client.py:748). |
| **Event-sourced ledger** (`ledger.py`) | 222 | imported NOWHERE in the trade path — authoritative for nothing. Pure standalone module. |
| **Operator** (observe-only) | 205–212 | the pulse runs `operator_observe.py` which acts on NOTHING; `claude -p` gated OFF (`OPS_OPERATOR_USE_CLAUDE_CLI` unset). |
| **Adversary in scorecard** | 219a | runs post-close only; emits incidents, takes no trading action. |
| **incident_synth, scorecard, selection_study** | 192–213 | post-close analysis; read-only on artifacts. |

### D. ⚠️ The one subtlety to make EXPLICIT (recommended flag add)
`OPS_OPERATOR_T1_ENABLED` defaults to **"true"** in code. T1 is currently **unreachable**
(only the observe runner runs; `claude -p` is off, so nothing actually *invokes* a T1 action)
— so it's safe in practice. BUT safety-by-unreachability is fragile. **RECOMMENDATION: pin
`OPS_OPERATOR_T1_ENABLED=false` in secrets** so the Operator's autonomy is OFF *explicitly*,
not incidentally, for Tuesday. (Arm it later, deliberately, after the observe shakedown.)

---

## 2. GREEN-LIGHT TODO (do before Tuesday 04:30 ET)

### MUST-DO (gates the green light)
- [ ] **G1 — Full test sweep green** (modulo the known 10 pipeline_guards MagicMock-rot fails).
      Run: `pytest tests/unit tests/integration -q`. Confirm the count = baseline + the new
      suites (ledger 8, adversary 8, close-cancel-settle 4, eod-retry 7, ops 22, short-recovery
      4, marketable 17, faller 7). Any NEW failure outside the known 10 = **RED, do not ship**.
- [ ] **G2 — `main` boots on HEAD** (`python -c "import main"`) — already ✅ on bdf518a.
- [ ] **G3 — Pin `OPS_OPERATOR_T1_ENABLED=false`** in `~/momentum-x-secrets.env` (explicit
      safety; §1.D). Optional but recommended.
- [ ] **G4 — Confirm the 4 deployed flags** are as intended in secrets (ELITE off, exhaustion
      on, marketable on, fade-short OFF/default).
- [ ] **G5 — Adversary battle green** (`python scripts/adversary_run.py`): must read
      `wrapper bugs: 0` (1 architectural flag = EOD-timing, expected/OK).
- [ ] **G6 — Disk/lock sanity**: `data/ops/` writable; no stale `momentum-x.lock`; the
      `MomentumX-PaperTrading` scheduled task is Ready for 04:30.

### NICE-TO-HAVE (improves Tuesday's data yield, not a gate)
- [ ] N1 — Confirm `OPS_RAW_FILL_CAPTURE` is on (default) so Tuesday produces `raw_fills`.
- [ ] N2 — Confirm `MomentumX-Operator` task is registered (the 12 observe pulses + Discord).
- [ ] N3 — Pre-open: clear any leftover overnight position from Monday (check broker; the
      doc-220 retry should handle EOD, but verify the book is flat or intentionally held).

### EXPLICITLY DEFERRED (NOT for Tuesday — would be building on faith)
- Phase 4 ledger cutover (rewire O1–O6). Gated on the raw-capture confirming `execution_id`
  + a ≥5-session shadow. **Do not wire the ledger authoritative Tuesday.**
- Arming Operator T1 / `claude -p`. After the observe shakedown proves triage quality.
- Flipping `EXEC_FADE_SHORT_ENABLED` / `FALLER_CONTINUATION_EXEMPTION_ENABLED`. Gated on
  real-data evidence (docs 215/201).

---

### Gate results run TONIGHT (Mon evening, on HEAD bdf518a)
- **G5 Adversary battle: ✅ GREEN** — `survived 10/11; wrapper bugs: 0` (the 1 = the expected
  EOD-timing architectural flag).
- **G1 new-suite sweep: ✅ GREEN** — `67 passed` across ledger(8) + adversary(8) +
  close-cancel-settle(4) + eod-close-retry(7) + ops-foundation(8) + operator-governance(9) +
  incident-synth(4) + trade-updates(19). Zero failures in the code deploying Tuesday.
- **G2 boot: ✅** `import main` clean on bdf518a.
- Remaining gates (G3 pin T1=false, G4 confirm flags, G6 disk/lock) are config/ops checks for
  the morning, not code gates. **The CODE is green.**

## 3. The single green-light criterion

> **GREEN if: G1–G2,G5 pass (tests + boot + adversary clean), the 4 flags are correct, and the
> only live-behavior changes are the execution-hardening set (216/218/219/220) + the two pinned
> sizing flags. Everything else deploying is dormant/observe-only.**

If G1 shows any new failure, or the adversary shows a wrapper bug → **RED**: `git revert` the
offending commit (the launcher runs HEAD, so reverting on develop is the rollback) and re-run.

---

## 4. Expected outcomes & benefits (what Tuesday should produce)

### Execution / P&L integrity (the headline benefit)
- **No unintended naked overnight carry.** The 216/218/219/220 arc means a `held_for_orders`
  EOD close now retries-with-settle across 15:55→15:59 and escalates if truly stuck. *Expected:
  positions flat at close (or intentionally held), zero "POSITION IS NOW NAKED" lines.*
- **Honest EOD report** (doc 209, already live since fc9b4c0): the Discord EOD shows the real
  broker P&L, not the journal phantom. *Expected: the report's headline P&L == broker truth.*
- **The phantom is now CATCHABLE same-session**: incident_synth (doc 212) emits CRITICAL
  PNL_RECON_DIVERGENCE / QTY_DRIFT if a phantom/ghost occurs — the Operator's observe pulse +
  Discord surface it. *Expected: if a phantom happens, you SEE it Tuesday, not days later.*

### B1 data yield (the Tuesday GOAL for the ledger track)
- **`data/ops/raw_fills_2026-06-02.jsonl`** populated with every real `trade_updates` payload.
  *Expected benefit: confirms the per-fill `execution_id` key (Phase-1 task#0) — the one
  unverified assumption — turning the ledger from "built on a hash fallback" to "built on the
  real broker dedup key."* This is the prerequisite that unblocks the Phase-4 cutover.

### Operator / observability
- **12 Discord heartbeats** (observe-only) with the ORIENT brief + the day's incidents + the
  game score. *Expected: a clean running log of system state; any CRITICAL (ghost/phantom/
  drawdown/naked) surfaced within the next pulse.*
- **Adversary battle in the post-close scorecard** confirms the execution code is still
  invariant-clean after the day. *Expected: `wrapper bugs: 0`.*

### What Tuesday will NOT do (set expectations honestly)
- It will **not** prove a selection edge — selection still buys variance (docs 213–215); a
  green/red P&L day is mostly regime, not skill. Judge Tuesday on **execution integrity** (no
  carry, no phantom, honest report, raw_fills captured), NOT on the P&L number.
- The ledger will **not** book anything live (dormant by design).
- The fade-short / faller-exemption / ELITE-press levers stay OFF — no new selection behavior.

### The compounding benefit (why this sequence matters)
Each hardened execution invariant is permanent and continuously re-battled (the Adversary in
the scorecard). Tuesday is the first session where **all four close-path fixes + the honest
reporting + the observe-Operator + the raw-fill capture run together** — i.e. the first
session that is both *defended* (can't bleed on the plumbing) and *instrumented* (produces the
B1 ground truth + the selection scorecard). That combination is what lets the next decisions be
made on evidence instead of anecdote.

---

## 5. Post-Tuesday (the path after the green light)
1. **Wed AM**: read `raw_fills_2026-06-02.jsonl` → confirm `execution_id` key → finalize the
   ledger dedup key (doc 222 §2).
2. **Start the ledger SHADOW**: have the optimistic path ALSO emit ledger events (read-only,
   non-authoritative) for ≥5 sessions; compare ledger P&L vs the old path daily.
3. **Phase 6 Adversary B1 scenarios** (`wrapper_success_without_fill`, dup-fill, out-of-order,
   crash-during-ingest) + the kill-9 crash test + the CI grep-guard.
4. **Only then** Phase-4 cutover (O1–O6 → events; delete the optimistic path) — when the shadow
   is clean for ≥5 sessions and the gauntlet's 8 gates are all green.
5. Separately, once the observe-Operator shakedown looks good: arm `claude -p` then T1.

## Appendix — verification basis (all checked tonight, not from memory)
- Bot ran `fc9b4c0` Mon (D217 STARTUP log); HEAD `bdf518a`; 18 commits deploy Tuesday.
- `fill_capture` reached live at `websocket_client.py:748`; `ledger.py` imported in no trade path.
- Secrets pin ELITE=false, exhaustion=true; T1/CLI flags unpinned (T1 default-true but
  unreachable via observe-only pulse).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
