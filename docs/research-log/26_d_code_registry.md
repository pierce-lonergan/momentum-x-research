# 26 — D-Code Registry (active reservation list)

**Purpose.** Every D-prefix log code in MOMENTUM-X reserves a slot in
the operational warning/info namespace. Codes are referenced from
production code, regression tests, dashboards, and Discord alerts;
**reusing a code for a different purpose silently invalidates downstream
filters and grep workflows.** This registry is the source of truth for
what every D-code currently means.

**Convention:** D000–D199 historic (pre-2026-04 sweep), D200–D299
2026-04 onward sweep + v2.2 reservations.

**Last updated:** 2026-04-23 evening (post-D23 patches + v2.2 §0
reservations).

---

## Active codes (2026-04 sweep onward)

### D200-series — bug-sweep + audit infrastructure

| Code | Owner | Meaning | First fired |
|------|-------|---------|------------|
| D200-E4 | filters | CATALYST GATE: ticker blocked — no confirmed catalyst | live |
| D203 | filters | Day-of-week regime gate | live |
| D210 | data | SessionDataCollector lifecycle | live |
| D211 | journal | PhantomJournal init | live |
| D212 | data | WS trade buffer init / float-rotation tracker | live |
| D215 | execution | EXECUTION RECORDED — every fill captured to ledger | live |
| D216 | data | EarningsCalendar / FinBERT lifecycle | live |
| D217 | execution | poll_for_terminal_fill — order reached terminal status | live (Bug D fix) |
| D218 | execution | QTY_DRIFT — internal qty diverged from broker; auto-reconciled | live (Bug D fix) |
| D219 | agents | CATALYST_SHADOW classification — quality assessment | live |
| D220 | core | adaptive_router escape-hatch (deterministic-signal threshold) | live |
| D221 | infra | env_audit + heartbeat keeper task | live |
| D222 | journal | PNL_RECON — broker vs journal reconciliation, EOD | live (Bug #13/Bug N fix) |

### D146 — BAR-1 EXIT lifecycle

| Code | Meaning | First fired |
|------|---------|------------|
| D146 BAR-1 EXIT | Pre-exit: announces fire + Arena_expected backtest mean | live (Bug F fix) |
| D146 BAR-1 ACTUAL | Post-exit: realised P&L + Arena_actual=$X.XX | live (Bug F fix) |

### D91 — Overnight position close

| Code | Meaning | First fired |
|------|---------|------------|
| D91 STEP 1 | Cancel stop_order_id (skip if empty) | reserved (Bug E fix; not yet fired in production) |
| D91 STEP 2 | Submit market sell via close_position | reserved |
| D91 STEP 3 | Confirmed close, removed from internal tracker | reserved |

### D228 — BAR-1 exit-price degradation (Bug Q)

| Code | Meaning |
|------|---------|
| D228 BAR1_EXIT_PX_DEGRADED | Snapshot empty AND broker NBBO lookup failed; fell back to entry_price; exclude from η_perm calibration |

---

## Newly reserved this session (2026-04-23 evening) — v2.2 §0 discovery infrastructure

These codes are reserved NOW to prevent collision when the corresponding
infrastructure ships over the next 6 weeks. **Production code may
reference them in `# Reserved for ...` comments today; emission
permitted only after the named ticket lands.**

### Reconciliation daemon (Track B, Week 2 sprint)

| Code | Tier | Meaning | Action |
|------|------|---------|--------|
| **D230** | Soft (warn) | RECON_WARN: broker/internal divergence detected, within tolerance | Log + Discord ping; no trading impact |
| **D231** | Hard (block) | RECON_HARD_BLOCK: divergence exceeds Tier-1 threshold; new entries blocked | Cancel queued orders; deny new submits; existing positions managed normally |
| **D232** | Lethal | RECON_LETHAL: sustained Tier-1 divergence > 60s OR equity drift > 5%; flat all + halt | Cancel all working orders; market-close all positions; halt session; page operator |

### Property-based testing (Track C, Weeks 3-4)

| Code | Meaning | Action |
|------|---------|--------|
| **D233** | PBT_COUNTEREXAMPLE: Hypothesis shrunk to a new failing case during CI | CI fails; counterexample auto-committed as `@example()` regression case |

### Differential testing (Track D, after Phase 0)

| Code | Meaning | Action |
|------|---------|--------|
| **D234** | DIFFTEST_DIVERGENCE: pre-patch and post-patch transcripts disagree outside the path the patch documents as intentionally changing | Block PR merge; require explicit acknowledgment of additional changed paths |

### Chaos engineering (Track E, Week 6+)

| Code | Meaning | Action |
|------|---------|--------|
| **D235** | CHAOS_INJECTION_ACTIVE: log line emitted from a code path executing under simulated fault | Status marker; tagged so chaos-mode logs are never confused with real failures (filterable in dashboards) |

---

## Reservation discipline

**Adding a new D-code:**
1. Pick the next available slot in this registry.
2. Add a row to the relevant section above with owner + meaning + first-fired status.
3. Reference the code from a `# Reserved for ...` comment in the call site OR from a v2.2 spec doc.
4. Commit the registry change with the production change.

**Repurposing an existing code:**
**Don't.** Mark it deprecated, reserve a new code. Live production
filters / dashboards / Discord routes may depend on the existing
semantics.

**Active conflict check (D-codes that have been used for >1 purpose
historically):**
- D217 — historically "STARTUP" (one CRITICAL line at startup), later
  also "poll_for_terminal_fill terminal status reached" (per-trade
  INFO). Both still active; differentiated by log level + body. **No
  collision in dashboards.**
- D218 — historically "AGENT DISPATCH" (orchestrator startup line),
  later also "QTY_DRIFT" (per-position WARNING). Both still active;
  differentiated by log level + emitter module. **Yesterday's
  reservation review confirmed no operational collision.**

## Track A item 2 reservations (2026-04-23 evening)

Codes added during the AST dark-zone audit pass — each replaces a
silent code path with a labeled INFO/WARN line.

| Code | Meaning | Status |
|------|---------|--------|
| **D236** | PNL_RECON_PATH: which fill-source path the EOD reconciliation took (`get_account_activities` vs `get_orders`-fallback). Marker, not error. | Live (Track A item 2) |

## D24 EOD reservations (2026-04-24 evening, items 25-28 + Bug X/Y/Z follow-ups)

| Code | Tier | Meaning | Status |
|------|------|---------|--------|
| **D241** | Hard (Sweep) | EOD_SAFETY_CANCEL: 15:58 ET sweep cancels any working orders not on the expected-allowlist. Failsafe beneath both Bug V patch + Track B daemon. | Reserved (item 15) |
| **D242** | Hard | EOD_BROKER_FORCE_CLOSE: 15:55 ET — broker has a position the internal tracker doesn't know about. Force market sell to flatten. Failsafe for Bug X/Y/Z class. | Reserved (item 28 follow-up) |
| **D243** | Critical | BRIDGE_CLOSE_FAILED: `close_position` returned non-2xx OR raised; cleanup chain ABORTED, position retained in tracker, retry queued. Replaces today's silent fake-close behaviour (Bug Z). | Reserved (Bug Z patch — Monday) |
| **D244** | Warning | BAR1_EXIT_REPEATEDLY_FAILED: D146 BAR-1 EXIT fired >1 time on the same position without a successful ACTUAL completion. Catches Bug X cascade pattern. | Reserved (Bug X follow-up) |

**Next available code:** D248.

---

## Track C Phase 2 invariant violation codes (2026-04-25 morning)

Per the Track C Phase 2 expansion: each invariant in the SimpleBroker spec
(`31_simple_broker_spec.md` §2) maps to a dedicated D-code so a Hypothesis
counterexample reproduces with a stable, greppable signal in dashboards.
These are emitted by the state machine itself, NOT by production code —
production code references the existing per-bug codes (D215, D217, D222,
D245, etc.) when the same condition surfaces in live trading.

| Code | Invariant | Bug class motivation |
|------|-----------|----------------------|
| **D248** | I2 STOP_MATCHES_BROKER violated: tracker stop_price differs from broker stop_price | Bug R / Bug W |
| **D249** | I3 NO_ORDERS_DURING_HALT violated: order accepted while ticker halted | (defensive) |
| **D250** | I4 NO_NEGATIVE_POSITION violated: position qty crossed through zero in a single op | (defensive) |
| **D251** | I5 CUMULATIVE_FILL_BOUNDED violated: filled_qty > requested_qty | Bug D |
| **D252** | I6 LATE_FILL_ON_REJECTED_ORDER_CANCELED violated: bridge-rejected order not cancelled at broker | Bug V |
| **D253** | I7 TRANCHE_RESTRUCTURE_PRESERVES_TIGHTENED_STOP violated: new stop_price loosened after restructure | Bug W |
| **D254** | I8 EQUITY_CONSERVATION violated: broker equity drifts from initial + realized + unrealized | (catches silent loss class) |
| **D255** | I9 CLOSE_ATTEMPT_ONLY_MARKS_CLOSED_ON_BROKER_2XX violated: tracker mutated despite non-2xx broker response | **Bug Z** |

## Bayesian (η, γ) estimator four-trigger gates (2026-04-25 morning)

Per `compass_artifact_wf-e274cbca-…md` §13.4 and the Bayesian estimator
sprint (next-actions item 14). Each trigger fires automatically as part
of daily EOD recon once the estimator ships.

| Code | Trigger | Action |
|------|---------|--------|
| **D256** | η_perm posterior mean ≥ 0.65 | Capacity number reduces; halt strategies w/ above-tier sizing |
| **D257** | γ rolling drift > 0.20 between sample halves | Methodology paper draft halts; re-fit |
| **D258** | Halt rate > 25% in calibration sample | Cohort screen tightened; estimator re-runs on filtered set |
| **D259** | Martingale residual rejects zero-mean at 2σ | Decomposition inadequate; Paper 1 halts; cohort re-examined |

## QMP signing migration + Phase 0 reservations (2026-04-25 morning)

| Code | Meaning | Action |
|------|---------|--------|
| **D260** | SIGNING_DISAGREEMENT: BJZZ-signed direction disagrees with QMP-signed direction beyond threshold | EOD recon flags trade for manual review |
| **D261** | PHASE0_SCHEMA_VALIDATION_FAILED: an emit_* call's payload failed Pydantic validation | Drop the record; warn; counter-bumps Phase 0 health metric |
| **D262** | BOCPD_REFIT_RECOMMENDED: persisted prior is meaningfully out of sync with the corpus (n_trades grew ≥10 OR mu drift > σ/4 OR sigma drift > 20%) | Operator runs `python scripts/pretrain_bocpd_prior.py` to refresh; surfaces in EOD log |
| **D224** | KELLY_HALVED: D223 BOCPD_BREAK fired and Kelly governor activated (or re-activated). Multiplier drops to 0.5 for next 5 closed trades. RESET log fires when window expires. | Execution code reads `kelly_governor.current_multiplier()` at every entry sizing |

## Knight-Capital additional invariants (Saturday 2026-04-25 evening)

Per Tuesday call agenda + the discovery-infrastructure compounding sprint.
Four new state-machine invariants extending Track C Phase 2's I1-I9 set
with Knight-Capital-class catch surface.

| Code | Invariant | Bug class motivation |
|------|-----------|----------------------|
| **D263** | I10 NO_OPPOSITE_SIDE_ORDERS_SAME_TICKER violated: ticker has both buy AND sell orders in non-terminal status simultaneously | Knight Capital classic — runaway algorithm self-trading |
| **D264** | I11 EQUITY_DRIFT_BOUNDED_PER_SESSION violated: |equity - initial| > 50% of initial within a single session | Silent loss class — sharper than I8's 5x cap |
| **D265** | I12 POSITION_COUNT_MATCHES_BROKER violated: tracker open count != broker non-zero qty count | Enumeration drift — one side lost an entry |
| **D266** | I13 NO_ZERO_QTY_OPEN_POSITION violated: tracker has open=False position with filled_qty=0 and broker entry order past pending | Lifecycle-pop bug class — zombie tracker entry |
| **D267** | I14 STOP_OID_UNIQUENESS violated: same stop_oid attached to >1 open position | Cross-stop assignment — one position becomes a zombie when the stop fires |
| **D268** | I15 ENTRY_QTY_MATCHES_REQUEST violated: order.qty <= 0 (silent qty mutation) | Bridge risk math is broken when broker silently changes the order qty |
| **D269** | I16 FILL_PRICE_WITHIN_QUOTE_BAND violated: filled_avg_price outside ±50% of limit_price | Phantom-quote / circuit-breaker territory; oracle should reject |
| **D270** | I17 TERMINAL_STATUS_CONSISTENT violated: filled status vs filled_qty mismatch (e.g. "filled" with filled_qty<qty, "rejected" with fills) | Broker state corruption — internal consistency lost |

**Next available code:** D271.

---

## Bug Z reservations (Fri 2026-04-24 evening — Knight-Capital-class fix)

Bug Z is the highest-severity bug surfaced this week: the SMART_EXIT
close path treats a 403 broker rejection as silent success, then
proceeds to cancel protective stops + tranche limits, marking the
position internally-closed at fake-positive P&L while broker still
holds it naked. This is the canonical "Knight Capital class" failure
mode the playbook §1 names. Three D-codes wire the patch:

| Code | Tier | Meaning | Action |
|------|------|---------|--------|
| **D245** | Critical (info-level if recoverable) | SMART_EXIT_REJECTED: broker returned non-2xx on close attempt; raw status + body captured | Triggers retry path; tracker NOT mutated |
| **D246** | Warning | SMART_EXIT_RETRY: D245 fired, attempting close again with exponential backoff (attempts 1..N) | Continues until success or D247 |
| **D247** | Critical | SMART_EXIT_ESCALATE: N retries exhausted; position marked `close_attempt_failed=True`; recon daemon notified to expect divergence; OPERATOR INTERVENTION REQUIRED | No state mutation; surfaces immediately |

**Bug Z patch ships before Monday 04:30 launcher restart.** Test-first
per D22-D25 discipline — three tests per user spec.

**Next available code:** D262 (per Track C Phase 2 + Bayesian + QMP + Phase 0 reservations above).

---

## Item 8 — shadow→armed transition criteria for Track B daemon

Per the 2026-04-24 next-actions list item 8: arming the higher tiers
of the recon daemon REQUIRES documented prerequisite session counts.
These criteria are **non-negotiable** — bypassing them re-introduces
the false-positive risk the daemon's bug-hunting authority depends on.

### Criteria for arming D231 RECON_HARD_BLOCK (block new entries)

Currently the daemon ships with `shadow_mode=True` per item 5. To
flip to `shadow_mode=False` so D231 actually blocks new entries:

| Gate | Original threshold | Saturday 2026-04-25 update | Verification |
|------|--------------------|----------------------------|--------------|
| Shadow sessions completed | N=5 full trading days | **N=2 full trading days** (pull-forward) | Count of `data/recon_status.json` files where `tier="hard"` was `[SHADOW]` and the trading session ran to 16:00 ET |
| Tier-1 false positives | = 0 during shadow window | unchanged: **= 0** | A "false positive" = D231 fires on a benign event (Bug D 846/505 partial-fill window, normal stop trigger sequence, etc.) where broker-truth and tracker reconcile within 30 seconds |
| Bug Z / X / Y class fixes | All three patched + verified | unchanged: **all three patched** | Bug Z patch must ship before arm; Bug X / Y depend on Z |
| **NEW** Phase 0 capture | n/a | **≥1 session of Phase 0 instrumentation rows persisted** | `ls data/instrumentation/trade_context/session_date=*` non-empty |

**Pull-forward rationale (Tuesday call agenda discussion item #1).**
The original N=5 was insurance against false-positive risk — kill any
arming where the daemon trips on a benign event. Two changes since:

1. **Bug Z taught us about latency cost.** The 5h 30m discovery latency on
   Bug Z's -$947 unhedged position is the strongest possible argument
   that the false-positive risk we were insuring against is dominated
   by the discovery-latency risk of NOT arming.

2. **Phase 0 (commit `0f0bd0f`, tag `v2.2-phase-0-active`) gives the daemon
   the data it needs to validate against.** Before Phase 0, the daemon
   compared internal tracker vs. broker truth alone; now it has a 4-schema
   ground-truth corpus per session. The false-positive surface narrows
   significantly with the per-fill timing + NBBO context.

**Combined effect:** the original N=5 was sized for a pre-Phase-0 world;
N=2 with Phase 0 is the equivalent risk envelope. Arm Wednesday evening
if Monday + Tuesday both ship clean.

If any false positive fires during the 5 sessions → re-tune the daemon
(broaden the kind="stop_no_oid" Tier-2 vs kind="stop" Tier-1 split,
adjust 30s sustained-violation threshold) → restart the 5-session count.

### Criteria for arming D232 RECON_LETHAL (flat all + halt)

ONLY after D231 has been live (not shadow) AND the criteria above for
N=5 D231-armed sessions are met:

| Gate | Threshold | Verification |
|------|-----------|--------------|
| D231-armed sessions completed | **N=5 additional full trading days** | Same counter, post-D231-arm window |
| D231 false-positive trigger count | **= 0** during D231-armed window | Same definition as above |
| D232 shadow-mode trigger count | **0 false** during D231-armed window | i.e., the lethal threshold (5% equity drift OR 60s sustained Tier-1) was never crossed by a benign event |

Operator decision — D232 lethal IS the kill switch Knight Capital
didn't have. Wrong arming destroys real positions; under-arming leaves
the catastrophic-loss risk uncovered. Conservative is correct.

### Procedure for arming each tier

1. Verify the gate criteria above in writing
2. Update `main.py` `ReconDaemon(...)` construction:
   - For D231: change `shadow_mode=True` → `shadow_mode=False`
   - For D232: no code change needed (lethal_triggered already wired)
3. Update this registry section with the date + commit hash of the arm
4. Operator's decision logged in the architecture-call agenda

### Disarm procedure (if false positive hits post-arm)

1. Re-add `shadow_mode=True` immediately (same-commit emergency revert)
2. Investigate the false-positive root cause
3. Patch the daemon's tier-classification logic
4. Restart the 5-session count from zero — partial credit is not
   defensible

### Why N=5 (not N=1, not N=20)

- N=1: insufficient — one clean session can be statistical noise
- N=5: matches a typical trading week; covers Monday open + Friday
  EOD edge cases + at least one BAR-1 fire + ideally one stop-out
- N=20: too long; defers protection in real (paper-)money exposure
  without proportional risk reduction

The exact value is operator-tunable. N=5 is the proposed starting
discipline per item 8.

## Bug V + Track A follow-up reservations (2026-04-24 pre-open)

Reserved before the patches ship per the discipline of D-code-first.
Each will go live with its named ticket per the 2026-04-24 next-actions
list.

| Code | Tier | Meaning | Status |
|------|------|---------|--------|
| **D237** | Info / Warn | BRIDGE_CANCEL: `_poll_for_terminal_fill` rejected an order (filled_qty=0 after max_polls); cancelled at broker to prevent ghost fill. INFO on success, WARN if cancel itself raised. | Reserved (Bug V patch tonight) |
| **D238** | Warn | EOD_RECONCILIATION_DELTA: broker truth disagrees with internal journal at EOD; per-source breakdown in message. | Reserved (item 23 — EOD report rebase) |
| **D239** | Info | HEARTBEAT_DIRTY_WORKTREE: heartbeat keeper detected `git diff --quiet` non-zero at session start; `commit=` field is the LAST committed hash, not the running-code hash. Fires once per session. | Reserved (item 21 — heartbeat fix) |
| **D240** | Hard (block) | PREOPEN_GHOST_DETECTED: pre-open broker-state snapshot found positions, orders, or stops the internal tracker doesn't know about. Blocks new entries until manual clearance. | Reserved (item 22 — pre-open broker check) |

**Next available code:** D241.
