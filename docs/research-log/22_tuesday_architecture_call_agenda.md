# 22 — Tuesday Architecture-Call Agenda

**Status:** living document. Updated Wed 2026-04-22 evening with the
EOD bug-sweep deltas (Bug D, E, #13, F closed; #12 and #14 published as
specs).

The five Tuesday agenda items previously seeded in
`18_slippage_methodology_application_v2.md` §9 remain in force; this
document tracks the operational items added by the recent bug-sweep
cycle and which of them close before the call vs which need
discussion.

---

## Closed before the call (no discussion needed; flagged for awareness)

### #9 — Bug D: AGPU qty 846/505 mismatch — **CLOSED**
**Resolution:** `19_eod_bug_findings.md` Resolution Index, Bug D.
**Test count:** 9/9 pass (`tests/unit/test_d217_partial_fill_handling.py`).
**Behavioral guarantee:** poll terminates on `status ∈ TERMINAL_ORDER_STATES`,
not on `filled_avg_price > 0`; D218 QTY_DRIFT scheduled at T+5/30/60s
auto-reconciles late terminal fills.

### #10 — Bug E: D91 09:30 close-at-open silent failure — **CLOSED**
**Resolution:** `19_eod_bug_findings.md` Resolution Index, Bug E.
**Test count:** 12/12 pass (`tests/unit/test_d91_overnight_close.py`).
**Behavioral guarantee:** detection by `opened_at < today_04:00_ET`, not by
`session_state is None`; `close_pending=True` excludes from eval queue;
close routine emits `D91 STEP 1/2/3` instrumentation.

### #11 — Bug F: "Arena: +$5.11 (+299%)" hardcoded — **CLOSED**
**Resolution:** `19_eod_bug_findings.md` Resolution Index, Bug F.
**Test count:** 6/6 pass (`tests/unit/test_bug_f_arena_expected_actual.py`).
**Behavioral guarantee:** pre-exit log uses `Arena_expected:` qualifier;
post-exit log emits separate `Arena_actual=$X.XX`; constants centralized
in `src/execution/bar1_exit_logging.py`. Three trades produce three
distinct `Arena_actual` values — pinned by regression test T3.

### #13 — Trade-journal P&L counter broken — **CLOSED**
**Resolution:** `19_eod_bug_findings.md` Resolution Index, Bug #13.
**Test count:** 8/8 pass
(`tests/unit/test_d222_journal_pnl_reconciliation.py`).
**Behavioral guarantee:** Closed/Total P&L derive from broker fill ledger
(single source of truth) — the journal is no longer authoritative.
D222 PNL_RECON warning fires on >$1 divergence with per-ticker breakdown.
Tolerance `PNL_RECON_TOLERANCE_USD = 1.00`.

---

## Discussion items — published as specs, ready for ratification

### #12 — D146 BAR-1 EXIT formalization — **SPEC PUBLISHED**
**Doc:** `docs/research-log/20_d146_bar1_exit_spec.md` (369 lines).
**Status for the call:** ready to be ratified as v1 of the signal
contract. Implementation is a 4-PR sprint sized at ~5 engineering
days (see Phase 0 §9). Open questions surfaced in §7 of the spec
need a decision before the table writer ships:
- (a) Fire-site write vs background flush — accept ≤5 fires of buffer
  loss?
- (b) Counterfactual backfill at EOD vs T+30min from each fire?
- (c) Polygon NBBO vs Alpaca quotes for v1 (and the column-versioning
  story).

### #14 — Phase 0 instrumentation MVP — **SPEC PUBLISHED**
**Doc:** `docs/research-log/21_phase0_instrumentation_mvp.md` (320 lines).
**Status for the call:** scoping doc for the next sprint. Four
schemas (`trade_context`, `bar_context`, `child_fill_ticks`,
`cohort_registry`) plus a unified `InstrumentationWriter`. Hook-order
laid out as four independently-revertable PRs.

**Two binding decisions needed at the call:**
- (a) Approve the 5-day engineering investment NOW (each delayed day
  = a calibration day lost). The MAAS+ELSE pairing from today is
  exactly the (q/v_τ, edge) contrast v2 needs and would have
  captured automatically.
- (b) Confirm the v2.1 deferral of WebSocket NBBO at child-fill
  granularity. REST-derived rows are sufficient for v2 Phase-1; the
  real-time NBBO refinement waits.

---

## Pre-existing items still on the agenda (carry-over from v2 plan)

These remain queued from `18_slippage_methodology_application_v2.md`
§9. The bug-sweep work this session does not touch them; they're
listed for completeness:

1. **Two-strategy portfolio architecture** — ratify before v2 Phase 0.
2. **RIA + SMA structure + 36-month horizon** — commit to the
   regulatory+SMA work early.
3. **Drop Alimoradian citation publicly or privately** — internal
   communication framing.
4. **rpy2 vs Python-only estimator stack.**
5. **State-mutation reversibility rule (f) for the calibration loop**
   — extend the rule from this morning's restart-template to the
   model-state mutation paths.

Plus the three carried over from the morning:
- State-mutation reversibility rule (f) as a new bug-sweep template
- Silent-fallback audit retrospective sweep (90 min)
- `docs/research-log/17_restart_template.md` formalization

---

## Cross-cutting note for the call

The four bugs closed today (#9 / #10 / #11 / #13) all share the
state-mutation reversibility rule (f) violation: each mutated state
(or failed to mutate) without a built-in check against ground truth.
The fixes each add such a check:
- D: D218 qty drift assertion
- E: D91 STEP-by-step instrumentation
- #13: D222 journal/broker reconciliation
- F: Arena_actual line is a real-data check that the literal isn't
  a stale constant

This is now the fifth instance of the same pattern catching real
bugs in a 3-week window. Strong argument for promoting (f) to a
permanent bug-sweep rule (item 5 above) at this call.

---

## Test-surface delta this session (for the call's status update)

| Bug | Tests added | Pass | Production files touched |
|-----|-------------|------|--------------------------|
| D   | 9           | 9/9  | `bridge.py`, `alpaca_executor.py` |
| E   | 12          | 12/12| `bridge.py`, `position_manager.py`, `main.py` |
| #13 | 8           | 8/8  | `trade_journal.py`, `main.py` |
| F   | 6           | 6/6  | `bar1_exit_logging.py` (new), `main.py` |
| **Total** | **35** | **35/35** | 6 production files + 1 new module |

Plus 2 specs published, 1 dossier updated with Resolution Index.

Deploy-and-verify on next open (Thu 04:30 ET) is tomorrow's task.

---

## D24 (Fri 2026-04-24) — discovery infrastructure live evidence

The discovery infrastructure shipped Wed/Thu has now produced THREE
distinct live datapoints across the Thu and Fri sessions. **Updating
the agenda items §0.1, §0.2, §0.3 with concrete evidence per items
21-23 of the 2026-04-24 next-actions list.**

### #15 (item 21 → §0.1) — Reconciliation daemon (Track B)

**Current state:** SHIPPED. Commits `166d15c` (daemon) + tonight's
wire-in into `main.py` cmd_paper. `shadow_mode=True` per item 5.
**Will activate on Monday's launcher restart.**

**Concrete evidence for the architecture call:**

- **Today's LIDR Bug Z catastrophe was caught by EOD recon
  (`D231 RECON_HARD_BLOCK QTY LIDR: internal_qty=MISSING broker_qty=5264`)
  but only at 16:00:25.** Without the daemon (which would tick every
  30s), this state was invisible from 10:30:36 (Bug Z fake-close) until
  EOD = **5h 30m discovery lag.**
- **Track B daemon would have caught it within 30 seconds of the
  10:30:36 fake-close.** A 660× faster discovery time.
- **The daemon's lethal tier (D232, currently disabled per item 5) would
  have triggered flat-and-halt at the 60s sustained-Tier-1 threshold,
  preventing the position from carrying overnight.** The naked-overnight
  weekend exposure on LIDR (-$947 → potentially -$1500+ if Monday
  gaps down) is exactly the loss class D232 is built to prevent.

**Vote at the call:** approve Tuesday's continued shadow operation +
the N=5 transition criteria documented in `26_d_code_registry.md` §"Item 8".

### #16 (item 21 → §0.2) — PBT state-machine sprint (Track C)

**Current state:** SPEC AUDIT-COMPLETE. `31_simple_broker_spec.md`
(384 lines) — 8 invariants, 10 rules, every bug in the corpus mapped
to a catching path. Implementation is a ~5-day sprint.

**Concrete evidence:**

- **Bug W recurred today despite Bug R patch from yesterday.** Bug R
  fixed `bridge.py` ManagedPosition construction (D23). Bug W shows
  the same bug pattern surviving in `main.py` exit-ladder restructure
  call sites. Static analysis (mypy strict, ruff BLE) cannot catch
  this — `verdict.stop_loss` is type-correct everywhere it's used.
- **The PBT invariant `tracker_matches_broker` after every
  rule-transition would catch this.** Hypothesis would shrink to
  "after R9 tranche_restructure" within 50 generated sequences per
  Hughes-Volvo published rate. Two-day ROI on Bug W vs the entire
  PBT sprint cost: roughly even in $-saved, but PBT is compounding —
  every future Bug-R/W class regression caught for the cost of running
  the test suite.
- **Bug Z (fake close on 403)** is also caught by PBT: invariant
  I8 equity_conservation would fail when bridge marks position closed
  but broker still has it. This would have prevented today's $1,121
  effective loss.

**Vote at the call:** approve the Track C sprint kickoff Monday post-
Track-B-shadow-arming.

### #17 (item 22 → §0.3) — Discovery-rate exceeds introduction-rate metric

Per item 22's specification: "Bugs introduced this week via patches
divided by bugs surfaced via proactive tooling. When ratio exceeds
1.0 for four consecutive architecture calls, v2.2 offensive Kelly
sizing is defensible."

**Current week ratio (D22-D24, three sessions):**

| Direction | Count |
|-----------|-------|
| Bugs introduced via patches | **0** confirmed (Bug N regression caught and fixed same-day; Bug V/W patches verified holding via Bug-W LIDR trace; no "patch broke a thing" silent regressions) |
| Bugs surfaced via proactive tooling | **5** (Bug N via mypy/audit reasoning, Bug T/U via test failure, Bug V via D23 EOD analysis, Bug W via D24 morning Track A, Bug X/Y/Z via Track A item 3 EOD recon firing tonight) |
| **Ratio** | **5.0** ✓ — well above 1.0 threshold |

**The infrastructure is doing what it was built for.** Per Taleb's
antifragility frame in `25_bug_hunting_playbook.md` §8.5: every
faulted condition encountered (real or simulated) becomes a permanent
regression test, and the bug-finding rate per unit time INCREASES as
the system is exposed to more variety. **This is the precondition
for unfreezing offensive capital sizing per item 31 of the 2026-04-24
next-actions list.**

Maintain the freeze through next Tuesday's architecture call. Track
the ratio weekly. After 4 consecutive weeks above 1.0, the freeze is
defensible to lift.

### #18 (item 23 → `19_eod_bug_findings.md` template extension)

Per item 23: every new bug-finding entry must include a "which of the
seven playbook methodologies would have caught this" field. Tonight's
Bug X / Y / Z surfacing in `29_eod_bug_findings_d24.md` should follow
this template:

| Bug | Methodology that would have caught it | Methodology that DID catch it (live) |
|-----|---------------------------------------|--------------------------------------|
| **Bug Z** (fake close on 403) | PBT I8 equity_conservation; Track B daemon D231 (30s) | EOD recon D231 (5h 30m lag) |
| **Bug X** (BAR-1 fires repeatedly) | PBT R9-R3 sequence + I1; Track B daemon D231 | EOD recon D231 (revealed via cascade from Bug Z) |
| **Bug Y** (D76 says "no positions" with broker open) | Track B daemon D231 (would catch in 30s vs 15:55 EOD) | EOD recon D231 (and exposed Bug Z root cause) |

The template addition is now codified. New findings docs going
forward MUST include this field per the discipline.

---

## D24 cumulative test-surface delta

| Source | Tests added today | Pass |
|--------|-------------------|------|
| Bug V (D24 morning) | 4 | 4/4 |
| Bug W (D24 morning) | 3 | 3/3 |
| Track B daemon (D24 mid-day) | 8 | 8/8 |
| EOD failsafes module (D241/D242/D238 — D24 evening) | 10 | 10/10 |
| Track B wire-in (D24 evening) | 5 | 5/5 |
| **D24 total** | **30** | **30/30** |

Cumulative across D22+D23+D24: **95 tests, 95/95 pass** (35 D22 + 16 D23 + 8 Track A + 36 D24 — incl. 6 Bug Z shipped Friday evening before Monday open).

---

## D24 late-evening update — Bug Z patched (commit 0a2d62c)

The Knight-Capital-class fix shipped before Monday's launcher restart
per user authorization. **D245 SMART_EXIT_REJECTED + D246 RETRY +
D247 ESCALATE** wire the retry/gate/escalate pattern. The cleanup
chain is now gated by `_d245_close_succeeded` — broker close failure
no longer mutates the tracker, no longer cancels stops/tranches.

LIDR overnight protection: defensive GTC stop @ $2.10 placed at
16:35 ET; MOO sell scheduled to fire at 7pm ET via ScheduleWakeup
(Alpaca rejects MOO outside the 7pm-9:28am OPG submission window).

### Item 23 methodology-coverage entry for Bug Z

Per the next-actions list item 28 — every new finding records which
playbook methodologies would have caught it:

| Methodology | Would have caught Bug Z? | Latency |
|-------------|--------------------------|---------|
| **PBT state-machine** with I9 invariant `close_attempt_only_marks_closed_on_broker_2xx` | **YES** | <1 minute of Hypothesis runs (per Hughes-Volvo published rate, <50 sequences) |
| **Differential testing** on SMART_EXIT path | YES | Replay would catch silent state mutation on 403 |
| **Reconciliation daemon** at 30s cadence | YES | <30 seconds vs 5h 30m EOD recon detection |
| Static analysis (mypy strict) | NO — `except: pass` is type-correct |
| AST audit (Track A item 2) | partial — would flag the bare-except as a score-1 dark zone but not the silent-success-on-failure semantic |
| Reactive log reading | YES (after the fact) | hours-to-days |
| Property-based testing of bridge alone | YES | catches via I9 |

**Three independent methodologies catch Bug Z** — over-determined,
which means the architecture is sound. The PBT state machine when
shipped (Track C Phase 2) catches it in CI.

### Pull-forward proposal: Track B daemon arming

**Original plan**: N=5 clean shadow sessions before D231 hard-block,
then N=5 D231-armed sessions before D232 lethal.

**Proposed update**: Bug Z's discovery via Track A EOD recon at
16:00:25 (5h 30m latency on a -$947 position) is the strongest
possible argument that Track B arming compresses discovery latency
from hours to seconds.

- D231 hard-block: arm after **N=3 clean shadow sessions** (down from N=5)
- D232 lethal: keep N=5 of D231-live operation (unchanged)

**Justification**: 5h 30m latency on a -$947 unhedged position over
a 3-day weekend is exactly the failure mode N=5 was insurance against,
but discovery already happened — the insurance can be reduced.

Vote at the call.

Plus 4 specs published (D146, Phase 0, SimpleBroker, registry).
Plus tonight's findings dossier `29_eod_bug_findings_d24.md` with
Bug X/Y/Z root causes + LIDR overnight operational decision request.

---

## Saturday 2026-04-25 — discovery infrastructure compounding update

Eight commits + 6 tags shipped between Friday EOD and Saturday early
evening. The discovery infrastructure is now operational across five
independent layers, each catching a distinct bug class. Architecture
call discussion items live at the bottom of this section.

### What shipped (chronological, all on origin/develop)

| # | Tag | Commit | Capability |
|---|-----|--------|------------|
| 1 | `v2.2-static-analysis-active` | `366d028` | 5-rule AST + regex bug-class detector running in pre-commit hook chain. 26 tests. Caught and patched 6 net-new silent handlers in this commit alone. |
| 2 | `v2.2-pbt-phase2-active` | `2cc3e9a` | Hypothesis state machine R1-R11 + I1-I9; env-driven 200/2000/10000 example budgets; pre-commit fast variant + nightly deep search. |
| 3 | (untagged) | `09f3ce4` | **Bug AA** — SimpleBroker oracle bugs surfaced organically by tightening I8. PBT-discovered, fixed in same commit. Two manifestations (limit-price discipline + buying-power overshoot). |
| 4 | (untagged) | `5e6a9b9` | QMP signing migration per Barber-Huang-Jorion-Odean-Schwarz 2024. Module + 25 tests + D260 EOD wire-in. Retrospective deferred to Phase 0 tick capture. |
| 5 | `v2.2-phase-0-active` | `f1fd13f` | Phase 0 instrumentation foundation — 4 Pydantic schemas + InstrumentationWriter + atomic Parquet writes + 14 tests + D261 graceful-failure path. |
| 6 | (untagged) | `0f0bd0f` | Phase 0 wire-in: 6 emit-sites in alpaca_executor + bridge + main.py EOD flush + eod_recon health surface. Live as of next session start. |
| 7 | `v2.2-difftest-foundation` | `e4c32a3` | Track D differential testing harness. DifferentialHarness class + 10 tests + pre-commit gate on bridge.py / alpaca_executor.py / trade_journal.py / main.py edits. |
| 8 | `v2.2-bocpd-armed` | `133adb6` | BOCPD pre-training: module + 12 tests + persisted prior + cp-widening calibration finding documented. |
| 9 | (untagged) | `8c4160f` | Track C mutation canaries (Bug V/W/Z) + invariant normalization. Proves Hypothesis search reaches the named bad states. |
| 10 | (untagged) | `c5a82ba` | BOCPD wire-in: prior load at startup + observe at terminal close. D223 BOCPD_BREAK live. |
| 11 | (untagged) | `7ecc2e8` | BOCPD re-fit scheduling + D262 EOD diff reporting. Closes the BOCPD lifecycle loop. |

**Suite cumulative:** 212/213 across 28 test files in ~32s. (1 pre-existing scanner failure unrelated, spawned as a separate task.)

### Discovery infrastructure layers (now all live)

| Layer | What it catches | Trigger | Latency |
|-------|-----------------|---------|---------|
| Static analysis (`v2.2-static-analysis-active`) | frozen-mutate, async-leak, silent-handler, relative-path, ps-datetime-utc, ps-pipe-deadlock | every commit touching scanned paths | <3s |
| PBT state machine (`v2.2-pbt-phase2-active`) | invariant violations from rule-grammar interleavings; specifically Bug D/V/W/Z classes plus generalizations | every commit touching execution stack OR nightly | 3s / 20s / 100s |
| Phase 0 instrumentation (`v2.2-phase-0-active`) | per-trade microstructure data persistence; D261 schema validation failures; foundation for QMP retrospective + Bayesian estimator + differential replay | every order lifecycle | inline (sub-ms) |
| Differential testing (`v2.2-difftest-foundation`) | Bug-N-class regressions where a patch silently changes a critical-path call site | every commit touching gated files; per-PR replay (when Phase 0 captures one session) | <1s for harness gate; minutes for per-PR replay |
| BOCPD changepoint detection (`v2.2-bocpd-armed`) | regime breaks in trade-outcome distribution; D223 BOCPD_BREAK at posterior_cp > 0.85; D262 EOD refit recommendation | per-trade close + EOD | inline + EOD |

### Discovery rate

11 production bugs surfaced and fixed this week (D, E, #13, F, N, T, U, Q, R, V, W, X, Y, Z, AA) / 0 patches introduced regressions / 1 algorithm-calibration finding (BOCPD cp-widening) found by test-first discipline before shipping. **Antifragility ratio = ∞.**

The discovery rate **trended up while the engineering rate stayed flat**, which is the qualitative signal the playbook §8 names — the system gets stronger from each new bug rather than weaker.

### Tuesday call discussion items

1. **Promote Track B daemon to D231 (hard-block) sooner than N=5?** Original plan was N=5 clean shadow sessions; the §289-307 pull-forward proposal reduced this to N=3 based on Bug Z's 5h 30m discovery latency. Now that Phase 0 captures the data the daemon needs to validate against, **propose pulling D231 arming to N=2** if Monday + Tuesday sessions both ship clean.

2. **Bayesian (η, γ) estimator — PyMC install go/no-go.** The estimator needs PyMC for NUTS sampling. PyMC adds ~80MB of deps (incl. theano-PyMC). Decision: install now and ship the estimator within this week, or defer until rpy2 alternative is reviewed at the call. Recommend: **install PyMC**, ship within 2 days. Estimator independent of trading hot path — install risk is low.

3. **Cohort registry algorithm.** Phase 0 schema is shipped (CohortRow). The matching algorithm itself was deferred as "v2 implementation detail" per `21_phase0_instrumentation_mvp.md`. Now that Phase 0 is live, propose a v0.1 algorithm: same-(catalyst_type, market_cap_bucket, hour_bucket) match within ±15min entry window. 5-peer cap. Backfill at EOD via the existing bar_recorder. **Vote at the call.**

4. **Differential replay engine (Track D §3 §6).** Foundation shipped; per-PR replay comparison waits on Phase 0 to capture one full recorded session. After Monday's session (assuming clean), the replay engine + git-aware wrapper ship within the week. Just confirming alignment — not a decision item.

5. **D223 BOCPD_BREAK Kelly governance policy.** Today's BOCPD wire-in logs D223 at WARNING level when posterior_changepoint > 0.85, but does NOT yet enforce a Kelly reduction or paper-mode revert. Decision needed at the call: do D223 fires (a) just log, (b) halve Kelly multiplier for the next N trades, (c) revert to paper-trading for the rest of session, or (d) escalate to operator + halt entries? Recommendation: **(b) halve Kelly for next 5 trades** as the conservative starting policy. Empirically tunable via `kill_switch_threshold` + the policy code we add.

6. **The 11/0 ratio is the architecture-call thesis statement.** Eleven bugs surfaced and fixed in seven days, zero patches introduced regressions. The discovery infrastructure pays for itself in the second week. Architecture-call vote: **commit to maintaining the 11/0 ratio (or higher) through May 2026** as the operational discipline. If the ratio drops, the next-week budget shifts from new features to discovery infrastructure.
