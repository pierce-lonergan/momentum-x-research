# 2026-04-30 — FAST_PATH flow audit (Bug AT-1 migration)

**Read-only.** Option A vs B for migrating FAST_PATH to
`post_fill_bookkeeping`. `momentum-x` @ `873afe8`.

---

## 1. What `bridge.execute_verdict()` does

`bridge.py:817`. Steps: (1) NO_TRADE/zero guards; (2) D101 spread filter
+ D125 conviction widening; (3) PositionManager tier guard; (4) cache
ScoredCandidate; (5) submit via `AlpacaExecutor.execute(verdict)`;
(6) **broker confirmation** — `_poll_for_terminal_fill(max_polls=6,
poll_interval_s=2.0)` at bridge.py:962; (7) Phase 0 TradeContext +
ChildFill per leg; (8) build `ManagedPosition` with broker-truth
`entry_price=order_result.fill_price` (1137); (9) `add_position`;
(10) D218 T+5/30/60s qty-drift checks.

**Answer:** YES, bridge ALREADY waits for broker confirmation (12s
budget). Terminal `fill_price` set at line 1029 — exactly what
`post_fill_handler.py:138` reads. The 4 migrated sites rely on this.

---

## 2. Why FAST_PATH bypasses the bridge

`fast_path.py:39`: *"Day 5 analysis — 174.8 min average entry delay,
1.7% capture rate."* FAST_PATH fires at 9:30:01 ET BEFORE the ~100s LLM
pipeline produces a `TradeVerdict`. No verdict exists yet — it's
replaced by dip-table limit + 5-agent partial-MFCS.

`FastPathExecutor.execute_queue` (fast_path.py:492) duplicates a SUBSET
of bridge guards: circuit breaker, already-held, `portfolio_risk.check_entry`,
equity sizing. It SKIPS: D101 spread filter, D125 widening, ScoredCandidate
caching, **`_poll_for_terminal_fill` (the AT-1 root cause)**, Phase 0
instrumentation, D218 schedules, broker-truth `ManagedPosition`
construction. Inlined post-fill at `main.py:2272-2423` records D215 with
`fpe.entry_price` (the limit) — Bug AT-1.

`FastPathReconciler` (fast_path.py:619) is a separate later phase based
on the eventual LLM verdict — not a fill confirmation.

---

## 3. Where fill confirmations come from

**(a) REST poll** — `bridge._poll_for_terminal_fill` (bridge.py:72-178).
6× @ 2s on `client.get_orders`. Returns terminal dict on
`status ∈ {filled, done_for_day, canceled, expired, rejected, replaced}`.
Sets `order_result.fill_price` at line 1029. Production path for 4
migrated sites.

**(b) WebSocket trade-updates** — `websocket_client.py:734` →
`trade_updates.py:83` parses into `TradeUpdateEvent` (FILL/PARTIAL_FILL/
CANCELED/EXPIRED/REJECTED/NEW/REPLACED). Routes to
`TrailingStopManager.on_fill` (stop replacement) and
`FillStreamBridge.on_trade_update`. Critically, `FillStreamBridge` only
handles **tranche** fills via `tranche_monitor.on_fill`. **No async
wait_for_entry_fill helper exists today.**

---

## 4. Partial fills

bridge.py:996-1026: broker emits multiple `partial_fill` events, then a
final `fill`. `_poll_for_terminal_fill` keeps polling on
`partially_filled` (NOT in `TERMINAL_ORDER_STATES`); after max_polls
returns last-seen. Bridge accepts partials with `terminal_filled_qty > 0`;
D218 reconciles late completion. Zero-qty partials → cancel (Bug V).

`FillStreamBridge.on_trade_update` only routes to tranche monitor; never
calls a post-fill handler. **Helper sees ONE order per entry** — same
contract as PHASE2_BUY/RESCAN/VWAP.

---

## 5. Broker rejection

`AlpacaExecutor.execute` (alpaca_executor.py:333-347): on
`status ∈ {rejected, canceled, expired}` increments `orders_rejected`,
WARNs, **returns None** (Sweep fix). Bridge propagates None upward
(942). When poll reveals terminal cancel/expired/rejected (line 987),
bridge logs D216 WARN, cleans cache, returns None.

**Block 2:** all 4 sites gate on `if order is not None:` — same for
FAST_PATH. (FAST_PATH today only catches raised exceptions, not
`status=rejected` returned non-exceptionally — Option A closes that
latent bug.)

---

## 6. Existing FAST_PATH call site

**`main.py:2255-2426`.** AT-1 block at lines 2315-2337:

```python
2321  _exec_recorder.record_execution(
2322      ticker=fpe.ticker, side="buy",
2323      fill_price=fpe.entry_price,  # Best estimate; actual fill arrives async
2324      signal_price=fpe.entry_price,
2325      qty=fpe.qty, order_id=fpe.order_id,
2326      execution_path="FAST_PATH", ... )
```

"actual fill arrives async" is aspirational — no override path exists.
Surroundings: `ManagedPosition` registered at `entry_price=fpe.entry_price`
(2295); inline exit-ladder + stop-resubmitter (2344-2407); state_mgr
persist (2410-2421). Discord webhooks intentionally absent.

---

## 7. Recommendation: **Option A** (route through `bridge.execute_verdict`)

1. Bridge already does broker confirmation via the SAME
   `_poll_for_terminal_fill` that hardens the 4 migrated paths.
2. Bridge already constructs `ManagedPosition` with broker-truth fill
   price (bridge.py:1137) — eliminates the complicit
   `_MP(entry_price=fpe.entry_price)` at main.py:2295.
3. Bridge already emits Phase 0 ChildFillRow + TradeContextRow per OTO
   leg (1033-1071) — defensively addresses AT-2 for this path.
4. Doc 82 §1: AT-family is copy-paste-drift; Option B perpetuates it.
5. Doc 83 §7: PROMPT_06 framed FAST_PATH as *"delay D215 + call the
   existing helper"* — that IS Option A.

**If wrong, failure mode:** the 12s poll budget is too tight for
opening-dip limits that may take tens of seconds to walk down. Poll
returns zero-qty partial; bridge auto-cancels (Bug V); the order dies
BEFORE the dip hits limit. Mitigation: parametrize
`max_polls`/`poll_interval_s` per call, or add a first-5-min
no-auto-cancel flag. Catchable with a regression simulating a 30s
walk-down.

---

## 8. Option A: FastPathEntry → verdict mapping

All `TradeVerdict` fields needed by `bridge.execute_verdict` are
derivable from `FastPathEntry` (fast_path.py:78):

| TradeVerdict | FastPathEntry source |
|---|---|
| ticker / entry_price / stop_loss / target_prices | `fpe.*` (direct) |
| action | `"BUY"` |
| mfcs | `fpe.partial_mfcs` |
| kelly_tier | 1 (default) |
| position_size_pct | `settings.fast_path.position_size_pct` |
| gap_pct / rvol / float_shares | `fpe.candidate.*` |
| direction | `"long"` |

**Sub-decision A.1:** delete `FastPathExecutor.execute_queue`'s OTO
submission — let bridge submit. Risk-check parity already exists.
Smaller long-term surface vs A.2 (new
`bridge.confirm_externally_submitted_order` entry point).

---

## 9. Option B: `wait_for_fill_confirmation` location

NOT a new module. Place alongside `_poll_for_terminal_fill` in
`src/execution/bridge.py`. Subscribe to existing `FillStreamBridge`
queue (`fill_stream_bridge.py:160`); REST poll as 30s fallback
(PROMPT_06 §10.2). No new WebSocket subscription.

---

## 10. Risk assessment (Option A)

1. **Opening-dip latency vs 12s poll budget** (§7 failure mode).
   `tests/unit/test_d278_exit_policy.py:253` xfail flips to xpass on
   landing; ADD `test_fast_path_dip_fills_within_poll_budget` simulating
   30s walk-down. Mitigation: parametrize poll knobs OR add first-5-min
   cancel-suppression.
2. **D101 spread filter rejects morning-volatility entries** — D85's
   premise is opening-dip on high-RVOL gaps with wide 9:30:01 spreads.
   D125 conviction widening (bridge.py:868) helps when MFCS ≥ buy
   threshold. Test: add regression covering FAST_PATH path.
3. **Double portfolio-risk check race** — both `FastPathExecutor` and
   bridge call `portfolio_risk.check_entry`. Idempotent today; add a
   unit test to enforce going forward.

**Safety net:** the xfail at test_d278_exit_policy.py:253 auto-validates
the D215 fill-price contract on landing per doc 83 §3.5.

---

**End audit.**
