# 172 — 2026-05-24 Pierce's augmented plan: D312 + 90-day audit + ratchet

**Session date:** 2026-05-24 Sunday late night
**Branch:** develop
**Predecessor:** [doc 171 three-layer T2 rollout](171_v6_2026_05_24_three_layer_T2_wide_stops.md)
**Trigger:** Pierce's reframing of the dead-callback discovery + his 3 aggressive moves and 5 tactical ones

---

## 0. The reframing (Pierce's contribution)

> "If the callbacks were always just comments — never invoked — then TrailingStopManager has been a ghost the entire time. NXXT's 66h unhedge wasn't a regression — it was the default behavior."

This reframes L3 from "fix the FSM" to **"do we need this code at all?"** Confirmed empirically tonight via 90-day audit (below).

---

## 1. git blame the dead callback

```
ff865b7c (the operator 2026-02-02 23:23:28 -0500 750) # TrailingStopManager integration (H-008)
ff865b7c (the operator 2026-02-02 23:23:28 -0500 751) if self.trailing_stop_manager is None:
ff865b7c (the operator 2026-02-02 23:23:28 -0500 752)     return
ff865b7c (the operator 2026-02-02 23:23:28 -0500 753) ...
ff865b7c (the operator 2026-02-02 23:23:28 -0500 761)     # Executor callback to cancel the stop
ff865b7c (the operator 2026-02-02 23:23:28 -0500 766)     # (wired via executor.on_cancel_order callback)
```

**Authored 2026-02-02 in commit `ff865b7c "Additional loaders"`. The body was NEVER written — just comments.** ~4 months of OTO trades have run with a phantom `TrailingStopManager`. This is a "wired the contract but never implemented the impl" pattern from initial development, not a refactor regression.

---

## 2. 90-day historical unhedge audit (Move 1)

`scripts/unhedge_window_audit.py` — pulls last 90 days of broker fills + orders, infers per-position lifetimes, samples every 30s to identify windows where `qty != 0` and no protective sell-stop was active at broker.

### Result: **185 unhedge windows in 90 days**

| Duration bucket | Count | Bar |
|---|---|---|
| 30s–60s | **124** | ████████████████████████████ |
| 60s–5min | 2 | |
| 5min–1h | 8 | ████ |
| **1h–24h** | **36** | █████████████████ |
| **>24h** | **15** | ████████ |

### Top 15 longest unhedge windows

| Ticker | Start | Duration | Qty | Entry | Notional |
|---|---|---|---|---|---|
| **XWEL** | 02-25 15:55 | **113.65h (4.7 days)** | 3076 | $1.22 | $3,753 |
| **CRCA** | 02-25 15:55 | **113.64h (4.7 days)** | 1143 | $3.03 | $3,463 |
| HCAI | 05-01 09:31 | 76.51h | 21 | $10.07 | $211 |
| XRX | 05-01 09:32 | 74.55h | 9 | $2.24 | $20 |
| XRX | 05-01 09:32 | 74.54h | 77 | $2.26 | $174 |
| XRX | 05-01 09:33 | 74.53h | 9 | $2.30 | $21 |
| XRX | 05-01 09:33 | 74.53h | 16 | $2.29 | $37 |
| RPGL | 05-01 09:31 | 72.36h | 89 | $1.92 | $171 |
| RPGL | 05-01 09:33 | 72.33h | 22 | $1.90 | $42 |
| RPGL | 05-01 09:33 | 72.32h | 17 | $1.90 | $32 |
| **NXXT** | **05-18 10:49** | **46.70h** | **40,476** | **$0.51** | **$20,649** |
| LIDR | 04-25 11:24 | 44.97h | 2892 | $2.42 | $7,001 |
| LIDR | 04-25 11:24 | 44.97h | 2372 | $2.42 | $5,740 |
| RLMD | 03-09 16:01 | 41.49h | 2302 | $5.82 | $13,400 |

**NXXT (last week) was bucket #12 of 15 multi-day unhedges this quarter.** This is the empirical proof Pierce's reframing requires.

### What this tells us

1. **L2 watcher's 60s tolerance is well-calibrated.** The 124 sub-60s windows are the normal cancel/resubmit churn — anything tighter than 60s would generate false alarms. Anything looser (e.g. 5min) would miss real unhedges.
2. **Wide-arm 0.5x sizing is reasonable but not overly conservative.** Largest unhedge by notional was NXXT at $20k. Even at 0.5x, the wide arm exposure on a NXXT-class trade is ~$10k of unhedged downside if the hedge breaks. Acceptable.
3. **`MOVE 2` (migrate tight arm to L1) becomes more attractive after seeing this data.** With 185 historical unhedge windows on the existing tight-arm OTO path, deleting `TrailingStopManager` and putting both arms on L1's standalone-stop pattern is genuinely safer — not just cleaner.

Ledger persisted to `data/shadow_stops/historical_unhedge_audit_90d.json` for future analysis.

---

## 3. D312 fix — gate observability (Move 3)

### Friday 5/22 decoded

Phase 2 emitted "10 BUY, 0 HOLD, 0 NO_TRADE" repeatedly. 0 OTO submits all day. EOD said "no candidates met quality bar" — misleading.

Reality: **581 reject-gate logs** fired, dominated by **D216 (123 events)** — the "recently closed (race condition guard)" that blocks re-entry on same-session stop-outs. So the bot tried to re-enter the same names over and over and the cooldown silently rejected them.

### The fix

**No new gate logic — pure observability.** Three small changes:

1. `PhantomJournal.__init__`: add `self._gate_counts: Counter`
2. `PhantomJournal.update_gate(ticker, blocked_by)`: increment counter (excluding `EXECUTED` / `PENDING` operational states)
3. `PhantomJournal.gate_summary() -> dict[str, int]`: return frozen copy
4. `main.py` D304 EOD block: call `_phantom.gate_summary()`, pass as `veto_summary` kwarg to `alert_session_end_rich`
5. `tests/unit/test_d304_d307_*.py`: add `veto_summary` to the alert kwarg contract (now wired)

`alert_session_end_rich` already had veto_summary rendering logic from doc 161 — it just wasn't receiving data. **Closes D312 + the doc 168 veto_summary TODO in one shipment.**

### Tomorrow's EOD Discord (preview using Friday's data)

```
🚫 Vetoes by Reason (145 total)
  D216_RECENTLY_CLOSED: 123
  D85_FAST_PATH:        21
  D56_DUPLICATE:        1
```

Operator immediately sees "D216 ate 123 entries" and can decide: tune the 5-min cooldown OR accept the behavior. **No more silent BUY-killing.**

---

## 4. Pre-commit ratchet criteria (Tactical Move 2)

**Per Pierce: codify NOW so we don't decide under pressure after a good week.**

### Wide-arm sizing ratchet

| Step | qty_multiplier | Trigger |
|---|---|---|
| Start | **0.5** | T2 enable (Monday) |
| Bump 1 | 0.75 | 5 sessions AND `cumulative_wide_arm_pnl > +$3,000` AND `D313 HEDGE_VIOLATION count == 0` |
| Full | 1.0 | 10 sessions AND `cumulative_wide_arm_pnl > +$8,000` AND `D313 HEDGE_VIOLATION count == 0` |
| Revert | 0.5 → 0.25 → 0 | Any `D313 EMERGENCY_STOP_FAILED` OR 3+ HEDGE_VIOLATION in single session |

Implementation: `t2_arm_assignment._WIDE_QTY_MULT` constant. To ratchet, change one line + add a doc commit explaining why.

### T2 → T3 production cutover criterion

| Variable | Criterion |
|---|---|
| Sessions | ≥ 10 |
| Wide-arm cumulative P&L vs tight-arm | wins by ≥ 1.5σ of per-trade variance |
| D313 EMERGENCY_STOP_FAILED count | 0 |
| D310 RESUBMIT_NO_CALLBACK rate | trending toward 0 (L3 work item progress) |
| Net account equity since T2 start | positive |

T3 = delete `TrailingStopManager`, migrate tight arm to L1 standalone-stop pattern. Per Pierce's Move 2 deferred recommendation.

### MOVE 2 deferred decision: trigger to revisit

**Defer the tight-arm L1 migration to Thursday 5/28** (3 trading days into T2). Then evaluate:
- If `D313 HEDGE_VIOLATION` count > 0 in any session: ship Move 2 next morning (the audit data + violations together are the green light)
- If 0 violations: hold the asymmetric setup another week, let T2 A/B data mature with clean controls

---

## 5. Move 2 — tight arm migration (decision pending data, NOT shipped tonight)

My disagreement with Pierce: don't ship Move 2 tonight. Reasons:
1. **A/B clean-signal**: T2 wants to isolate stop width. Same-night change to tight arm path conflates 2 variables for the first 3 days.
2. **Regression risk on fresh code**: tight arm has run for years (in degraded form). New code on it Sunday night for Monday open is more risk than reward when L2 backstops both arms.
3. **Audit data confirms L2 catches the issue within 60s** — the cost of waiting 3 days for cleaner data is bounded.

Pierce's counter "humans will edit one and forget the other" is real but small over 3 days; documented in the deferred-decision section above.

---

## 6. Filed (not shipped tonight)

- **Tactical Move 1 (tighten L2 once stable)**: 20s → 10s polling, 60s → 30s tolerance after 1 clean session. Filed for after T2's first session.
- **Tactical Move 3 (live-vs-shadow nightly delta)**: nightly script diffing live wide-arm P&L vs replayer prediction. Filed for next week.
- **Tactical Move 4 (similar "comment without callsite" patterns)**: grep the codebase for `# Executor callback` / `# wired via` comments NOT followed by actual code. Filed.
- **Tactical Move 5 (feature vector capture at decision time)**: verify `feature_logger.py` is capturing the full vector at T2 verdict time so the cascade-anti-selection paper has data. Filed.

---

## 7. Files in this commit

| File | Change |
|---|---|
| `src/data/phantom_journal.py` | +25 LOC: `_gate_counts` counter + `gate_summary()` method |
| `main.py` | +10 LOC: D312 wiring — call `_phantom.gate_summary()`, pass `veto_summary` kwarg to alert_session_end_rich |
| `tests/unit/test_d312_gate_summary_to_eod.py` | NEW, 11 tests covering counter + EOD wiring + render contract |
| `tests/unit/test_d304_d307_*.py` | `veto_summary` added to alert kwarg contract |
| `scripts/unhedge_window_audit.py` | NEW: 90-day audit (paginated fills + orders, computes invariant) |
| `docs/research-log/172_v6_2026_05_24_augmented_plan_D312_audit.md` | THIS doc |

**Tests:** 11 new D312 + 217/217 full regression.

---

## 8. Verdict + Monday morning checklist

**Status:** **D312 + audit SHIPPED 2026-05-24.**

### Monday pre-open (operator)

```powershell
# Both env vars at User scope so they persist
[Environment]::SetEnvironmentVariable('MOMENTUM_T2_ENABLED', '1', 'User')

# Verify (should print "1")
[Environment]::GetEnvironmentVariable('MOMENTUM_T2_ENABLED', 'User')

# Restart bot
schtasks /end /tn MomentumX-PaperTrading
schtasks /run /tn MomentumX-PaperTrading
```

### Monday startup log lines to watch for

```
D313 HEDGE_WATCHER starting: interval=20s tolerance=60s ...    ← L2 alive
D310.T2 ARM ASSIGNED <ticker>: arm=wide_stop qty_mult=0.50    ← T2 firing
D310.T2 WIDE_STOP <ticker>: submitting plain BUY limit (no OTO) ← L1 path
D310.T2 <ticker>: STANDALONE STOP submitted oid=...           ← L1 stop attached
```

### Monday EOD Discord (new!)

EOD report will include `🚫 Vetoes by Reason (N total)` with breakdown. **No more silent BUY-killing.** If `D216_RECENTLY_CLOSED` dominates, that's the same Friday pattern → tune the cooldown next week.

### Tuesday morning (after Monday's data lands)

1. Read the audit-tooling logs: did T2 wide arm actually fire? Hash collision risk = none (cryptographic MD5).
2. Cross-check `data/shadow_stops/stop_decisions_2026-05-25.jsonl` against broker activities — every wide_stop entry should have a corresponding STANDALONE STOP at broker.
3. Check `D313` count: 0 violations = L1 + L2 healthy. Any violation = investigate.
4. If everything green: nudge to ratchet schedule. If anything orange/red: pause T2 and triage.

---

## 9. The big picture

Tonight we shipped **the observability + safety net that should have existed since February**. The dead callback would have eventually surfaced — L2 + D312 ensure that "eventually" is now bounded to 60 seconds for safety and 1 EOD report for visibility.

The path to 10%/day still depends on wide stops actually working in production. Monday morning we find out. **But for the first time we'll know if it doesn't work, AND we'll know why.**
