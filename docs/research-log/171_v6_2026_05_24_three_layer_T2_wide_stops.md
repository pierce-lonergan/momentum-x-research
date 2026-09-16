# 171 — 2026-05-24 Three-layer T2 wide-stops rollout (L1 + L2 + D310 obs)

**Session date:** 2026-05-24 Sunday night
**Branch:** develop
**Predecessor:** [doc 170 weekly deep-dive + T2 GO](170_v6_2026_05_24_weekly_deep_dive_T2_go.md)
**Trigger:** Pierce's 3-layer sequencing plan to unblock T2 without bottlenecking on D310's proper fix

---

## 0. The architecture

Per Pierce's plan, ship three independent layers tonight (well, two of them — Layer 3 stays gated for the week):

| Layer | What | Why |
|---|---|---|
| **L1** | Wide arm submits **plain BUY limit + standalone STOP** (no OTO) | Bypasses `TrailingStopManager` entirely → D310 cannot regress the wide arm |
| **L2** | `HedgeIntegrityWatcher` polls broker every 20s, invariant `qty != 0 → has_protective_order` | Catches D310 + D312 + **unknown-unknowns** within 60s. Pierce: "highest leverage per hour of work" |
| **L3 prep** | Structured D310 diagnostic logging in `websocket_client.py` | Surfaces the silent-callback root cause for next week's proper fix |

All gated behind **`MOMENTUM_T2_ENABLED=1`** so the operator can flip the experiment on Monday morning without redeploy.

---

## 1. Layer 1 — Standalone-stop on the wide arm

### TradeVerdict additions

`src/core/models.py` — two new fields on the frozen `TradeVerdict`:
- `execution_arm: str = "tight_stop"` (default preserves pre-T2 behavior)
- `qty_multiplier: float = 1.0` (default preserves pre-T2 sizing)

### Orchestrator wiring

`src/core/orchestrator.py:1739` (the verdict-return point):

```python
from src.execution.t2_arm_assignment import assigned_arm_for_verdict
verdict = assigned_arm_for_verdict(verdict)
```

`assigned_arm_for_verdict()` is a no-op when `MOMENTUM_T2_ENABLED` is unset. When enabled, it:
1. Skips short-side trades (T2 long-only for now)
2. Hashes `{trading_date_ET}:{symbol.upper()}` with MD5
3. Takes top 8 hex chars as a 32-bit int, bucket = `int % 100`
4. `bucket < 50` → `("wide_stop", 0.5)`; else `("tight_stop", 1.0)`
5. Returns `verdict.model_copy(update={...})` (frozen-Pydantic-safe)

Deterministic per `(trading_date, symbol)` so re-evaluation of the same name on the same day always lands in the same arm — no churn.

### Executor branch

`src/execution/alpaca_executor.py`:

1. **Sizing**: apply `qty_multiplier` immediately after qty is computed (halves wide-arm to bound risk on the 3-25× wider stop).
2. **D142 skip**: when `verdict.execution_arm == "wide_stop"`, skip the Phase 1 1.5% override entirely → use the orchestrator's wide ATR stop as-is.
3. **Submission**: instead of `submit_oto_order` (entry + stop child), the wide arm uses `submit_limit_order` (plain BUY) followed by a background `_t2_arm_standalone_stop()` task that:
   - Polls `get_orders` for fill confirmation (max 120s)
   - On fill: calls `submit_stop_order(..., time_in_force="gtc")` for the protective stop
   - On terminate (canceled/rejected): logs and exits (no position to protect)
   - On timeout: logs warning, **Layer 2 watcher backstops within 60s**

The wide-arm OTO is replaced by a 2-step submit. `TrailingStopManager` never sees the position because there's no OTO leg ID for it to track — **D310 cannot regress the wide arm**.

---

## 2. Layer 2 — Hedge integrity watcher (Pierce's safety net)

`src/monitoring/hedge_integrity_watcher.py` (new ~200 LOC class). Launched from `main.py` alongside the Track B recon daemon.

### The invariant

> For every position with `qty != 0`, there MUST be a protective order at the broker (sell-stop for longs, buy-stop for shorts).

### The loop

```
every 20s:
  positions = await client.get_positions()
  orders    = await client.get_orders(status='open')
  hedged    = {(symbol, stop_side) for o in orders if o.type in {stop, stop_limit, trailing_stop}}
  
  for p in positions where qty != 0:
    if (p.symbol, required_stop_side) in hedged:
      state.first_unhedged_utc = None   # reset
    else:
      if state.first_unhedged_utc is None:
        state.first_unhedged_utc = now  # start clock
      elif (now - state.first_unhedged_utc) >= 60s:
        emit D313 alert + submit emergency stop
```

### Emergency stop math

`emergency_stop = min(avg_entry × 0.85, last_price × 0.92)` for longs (the WIDER of "15% from entry" or "8% from current" — defensive).

For sub-$1 positions (where Alpaca stop-trigger is unreliable per D294), the watcher **alerts only** — no auto-submit. Operator must intervene manually for those.

### Catches

The NXXT 2026-05-18 → 5/20 unhedge (66 hours) would have been a **30-60 second** window with this watcher running. It covers:
- D310 (TrailingStopManager cancel-without-resubmit)
- D312 (Friday silent-BUYs that fail to submit OTO)
- L1 standalone-stop submission failure (network blip after fill)
- StopResubmitter ratchet failures
- Manual broker-side cancellations
- Any future variant of "qty exists but stop doesn't"

**Always-on** (not gated by `MOMENTUM_T2_ENABLED`) — it's a global safety net for the tight-stop arm too.

---

## 3. Layer 3 prep — D310 diagnostic logging

`src/data/websocket_client.py:740-779` — the dispatch path that should call `executor.on_cancel_order` and `executor.on_submit_trailing` callbacks. **Reading the code closely, the callbacks were never actually invoked — the original lines were just COMMENTS.** That's the root cause of NXXT's unhedge.

Tonight's instrumentation adds:
1. `D310 CANCEL_REQUESTED` when manager.on_fill returns CANCEL_STOP
2. `D310 CANCEL_DISPATCHED` / `D310 CANCEL_CALLBACK_FAILED` / `D310 CANCEL_NO_CALLBACK` per dispatch result
3. `D310 RESUBMIT_REQUESTED` when manager.on_stop_canceled returns SUBMIT_TRAILING_STOP
4. `D310 RESUBMIT_DISPATCHED` / `D310 RESUBMIT_CALLBACK_FAILED` / `D310 RESUBMIT_NO_CALLBACK` per dispatch result

Also wires `self.on_cancel_order(...)` and `self.on_submit_trailing(...)` callsites (instead of just comments) — so when the executor wires the callback (L3 next week), it actually fires.

Until then, `D310 *_NO_CALLBACK` logs will appear on every OTO buy fill, telling the operator EXACTLY what's broken. Layer 2 catches the symptom; this surfaces the cause.

---

## 4. Tests — 33 new + 198 regression

`tests/unit/test_doc171_t2_layers.py`:

| Category | Tests |
|---|---|
| T2 env gating | disabled by default, 5 truthy values, 5 falsy values, returns tight when disabled |
| T2 arm assignment | deterministic, ~50/50 distribution over 500 tickers, half qty multiplier, short verdicts unchanged, disabled passes through, model_copy round-trip |
| L1 wiring contract | executor branches on execution_arm, defines `_t2_arm_standalone_stop`, calls `submit_limit_order`, main.py launches `HedgeIntegrityWatcher`, orchestrator wires `assigned_arm_for_verdict` |
| L2 hedge watcher | positions with stops → no violation; unhedged within tolerance → clock starts; past tolerance → D313 + emergency stop; emergency stop math; sub-$1 alert-only; re-armed stop resets clock; closed position pruned from state; short requires buy-stop; dry-run mode; never raises on broker failure |
| Property test | invariant holds across multi-event replay (Pierce's spec) |
| TradeVerdict | accepts new fields, defaults preserve old behavior |

**Results: 33/33 doc 171 + 198/198 full regression** across all touched + adjacent suites.

---

## 5. Monday morning deployment plan

### Step 1 — pre-open (operator manual)

```powershell
# Enable T2 A/B at User scope (persists across bot restarts)
[Environment]::SetEnvironmentVariable('MOMENTUM_T2_ENABLED', '1', 'User')
schtasks /end /tn MomentumX-PaperTrading
schtasks /run /tn MomentumX-PaperTrading
```

### Step 2 — sanity checks at startup

Watch the bot log for:
- `D313 HEDGE_WATCHER starting: interval=20s tolerance=60s ...` (L2 launched)
- `D310.T2 ARM ASSIGNED <ticker>: arm=wide_stop qty_mult=0.50` for ~half of the BUY verdicts
- `D310.T2 WIDE_STOP <ticker>: submitting plain BUY limit (no OTO) ...` for wide-arm trades
- `D310.T2 <ticker>: STANDALONE STOP submitted oid=...` after the fill confirms (~2-30s later)

### Step 3 — operator monitors throughout the day

| Watch for | What it means |
|---|---|
| `D313 HEDGE_WATCHER ... unhedged` (info) | Brief unhedge during cancel/resubmit churn; normal if it clears within tolerance |
| `D313 HEDGE_VIOLATION` (ERROR) | Position unhedged > 60s, emergency stop being submitted |
| `D313 EMERGENCY_STOP_SUBMITTED` (ERROR) | Watcher saved a position |
| `D310 *_NO_CALLBACK` (ERROR) | TrailingStopManager wanted to act but the callback isn't wired (L3 work item) |

### Step 4 — Monday EOD

Run `python scripts/stop_widening_replay.py` on the live shadow data. Compare wide-arm actual P&L (from broker activities) vs the replayer's expected. If they match within $50/trade, the L1 wiring is working end-to-end.

### Abort criteria

- Wide arm cumulative P&L < −$2,000 within first 3 trading days → revert (set `MOMENTUM_T2_ENABLED=0`, restart bot)
- Any `D313 EMERGENCY_STOP_FAILED` ERROR → investigate before next session (broker actually refused the emergency stop = wider liquidity issue)
- 3+ `D313 HEDGE_VIOLATION` events in a single session → L1 wiring is unreliable; revert to tight arm + investigate

---

## 6. Files in this commit

| File | Change |
|---|---|
| `src/core/models.py` | +20 LOC: `execution_arm` + `qty_multiplier` fields on TradeVerdict |
| `src/execution/t2_arm_assignment.py` | NEW, ~110 LOC: env gating + deterministic hash arm assignment |
| `src/core/orchestrator.py` | +14 LOC: invoke `assigned_arm_for_verdict` at verdict-return |
| `src/execution/alpaca_executor.py` | +130 LOC: sizing multiplier + D142 skip for wide arm + standalone-stop submission branch + `_t2_arm_standalone_stop` background helper |
| `src/monitoring/hedge_integrity_watcher.py` | NEW, ~225 LOC: D313 L2 watcher with emergency-stop submission |
| `main.py` | +30 LOC: launch `HedgeIntegrityWatcher` alongside Track B recon daemon |
| `src/data/websocket_client.py` | +50 LOC: D310 diagnostic logging + actual callback invocation |
| `tests/unit/test_doc171_t2_layers.py` | NEW, 33 tests covering all of the above |
| `docs/research-log/171_v6_2026_05_24_three_layer_T2_wide_stops.md` | THIS doc |

**Total: ~600 LOC production + tests, all gated behind env var for safe rollout.**

---

## 7. Verdict

**Status:** **SHIPPED 2026-05-24 (gated OFF by default).**

Monday morning the operator flips `MOMENTUM_T2_ENABLED=1` and the bot:
- Splits new BUY verdicts ~50/50 across wide/tight arms (deterministic per (date, symbol))
- Wide arm uses plain BUY + standalone STOP (no OTO, immune to D310)
- Halves wide-arm sizing to bound dollar-risk on the 5-25% ATR stop
- L2 hedge watcher backstops EVERYTHING (both arms) every 20s

Layer 3 (proper TrailingStopManager FSM rewrite) is gated to next week per Pierce's plan. With L1 bypassing the manager and L2 catching its failures within 60s, T2 ships without D310 needing to land first.

The path to 10%/day starts Monday morning with the env var flip. The data from doc 170 says wide stops would have made +$4,111 on this week's 6 events alone. T2 is the experiment that lets us actually capture that.

---

## 8. Filed for the week

- **L3 (D310 proper fix)**: TrailingStopManager FSM rewrite. Reproduce in paper sandbox (Pierce's hypotheses: a/Alpaca-rejects-resubmit, b/async race, c/wide-vs-phase1 config divergence). Build explicit `PENDING_CANCEL → CANCELLED → PENDING_SUBMIT → ACTIVE` state machine. Gates T3 production wide-stop cutover.
- **T3 (production cutover)**: per Pierce, requires L3 + the property-test extension on the replayer (`position.qty != 0 ⇒ protective_order_exists` at every sampled point).
- **D312 visibility gap**: still open. The Friday silent-BUY pathology needs a `D312 GATE_REJECT` log when a Phase 2 BUY doesn't reach OTO submission.
