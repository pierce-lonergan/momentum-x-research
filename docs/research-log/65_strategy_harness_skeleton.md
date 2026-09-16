# 65 — Block C: Strategy harness skeleton

**Status:** shipped 2026-04-28 PM. Runs end-to-end on 4/28; 7-test regression suite green.
**Severity:** infrastructure. Block 4.4 prerequisite — replaces "harness implementation is multi-session work" with "harness skeleton works; 86-session run is filling in the orchestrator policy + handling per-session edge cases."

---

## §0 — TL;DR

Three modules + one runner script + 7 tests:
- `mx-arena/arena/sim_alpaca_client.py`: thin shim mirroring AlpacaDataClient's order-submission surface, routes to SimExchange + queries FailureInjector. **No changes to production AlpacaDataClient** (per stop condition: <50 LOC of critical-path changes preserved).
- `mx-arena/arena/orchestrator_stub.py`: `DecisionRowOrchestrator` (replays recorded decisions from instrumentation corpus) + `DeterministicPolicyOrchestrator` (open-buy at session-open) + `make_orchestrator()` factory.
- `scripts/strategy_harness_run.py`: per-session driver. Walks every minute 09:30→16:00 ET; calls orchestrator at each tick; submits OTO via sim client; exits at T+60s default.
- `tests/unit/test_sim_alpaca_client.py`: 7 tests pinning routing, halt-switch parity, FailureInjector AR-scenario replay end-to-end.

End-to-end test on 4/28 with `decision_row` mode: **10 trades, total P&L -$16,758**. Result is COHERENT (entries fire on tagged signals, exits at configured policy, P&L computes); does NOT match prod (different orchestrator policy — the stub doesn't have prod's consensus gate / position-count limit / debate logic).

---

## §1 — Architecture

```
┌─────────────────────┐    candidates     ┌──────────────────────┐
│ Orchestrator stub   │ ────────────────→ │ Strategy harness     │
│                     │                   │  runner              │
│  decision_row mode  │                   │                      │
│  policy mode        │                   │                      │
└─────────────────────┘                   └──────────┬───────────┘
                                                     │
                                          submit/cancel/close
                                                     │
                                                     ▼
                                          ┌──────────────────────┐
                                          │ SimAlpacaClient      │
                                          │  (thin shim)         │
                                          │                      │
                                          │  • halt switch       │
                                          │  • FailureInjector   │
                                          │  • → SimExchange     │
                                          └──────────┬───────────┘
                                                     │
                                                     ▼
                                          ┌──────────────────────┐
                                          │ SimExchange          │
                                          │  • OrderState        │
                                          │  • PositionState     │
                                          │  • FillModel         │
                                          └──────────────────────┘
```

The strategy harness sees `SimAlpacaClient` exactly the way production sees `AlpacaDataClient`: same async method signatures, same response shapes (`.to_alpaca_dict()`), same env-aware halt switch behavior. **Real strategy code can swap in the sim client without changes.**

---

## §2 — What this validates

1. **Wiring works end-to-end**: orchestrator → sim client → SimExchange → fill → exit → P&L.
2. **FailureInjector queryable via the wired path**: `test_ar_scenario_fixture_raises_with_response_body` injects today's LIDR Bug AR fixture and verifies the close raises an exception with `.response.text` containing the production-shape body. Bug AR's substring match would fire correctly. **AR scenario reproduces in arena.**
3. **Halt switch parity**: 4 truthy env values (`1`, `true`, `yes`, `on`) all return `halted_by_operator` from the shim; no order is recorded in SimExchange. Same logic as production AlpacaDataClient — verified by 4 parametrized tests.
4. **Coherent on 4/28**: decision_row mode produces 10 trades; policy mode would produce 1 per ticker. Both run without errors.

---

## §3 — What this DOES NOT validate

- **Strategy edge.** The orchestrator stub is a SIMPLIFIED policy. It honors agent classifications but lacks consensus gates, position-count limits, debate logic, kelly sizing, etc. The 10-trade output on 4/28 has no claim about edge.
- **Modeled-exit fidelity.** Exits are bar-anchored at T+60s. Per Block A's falsification verdict, the BAR-1 timing lift COLLAPSES under adversarial fade and OOS testing — so any P&L claim derived from these exits is suspect at the magnitude level. The harness produces NUMBERS but the numbers' fidelity is bounded by the same uncalibrated arena that Block B's calibration v2 (identity ship) couldn't fix without intra-bar tick data.
- **86-session run.** This session ships the skeleton on ONE session as proof-of-life. Block 4.4 (the 86-session OOS Sharpe headline) needs:
  - Orchestrator policy decisions for sessions WITHOUT decision_row corpus (only 4/28 has it; pre-Bug-AO sessions need policy mode)
  - Per-session bar coverage validation (use audit_bar_coverage.py)
  - Aggregation across sessions into equity curve + Sharpe + drawdown
  - Honest §1.4-equivalent stratifying outcomes by data completeness

---

## §4 — Stop conditions checked

The brief's stop condition: "If SimExchange wiring requires invasive changes to AlpacaDataClient that risk production behavior (>50 LOC across critical-path modules), STOP and ship harness with a thinner shim that bypasses production code."

**Outcome:** thin shim shipped. Zero changes to `src/data/alpaca_client.py`. Production code untouched. SimAlpacaClient is a complete parallel implementation of the order-submission surface, ~150 LOC in arena/, none in production paths.

---

## §5 — Logged findings

(no new bugs; design notes only)

1. **Orchestrator stub modes are non-equivalent**: decision_row replays whatever the recorded ensemble produced (which includes BUY decisions that prod ultimately did NOT execute due to consensus/position-limits). For Block 4.4, the stub policy needs to either (a) implement those gates, or (b) explicitly note that the harness over-counts entries vs prod.
2. **Sim client's `close_position` requires a position to exist** — the simple-close code path raises `RuntimeError` if SimExchange has no position for the symbol. Production AlpacaDataClient returns a 404 in this case. The shim's behavior is OK for arena but is a known divergence; a future tighter shim would mirror the 404 response.
3. **Cycle-tick granularity is 1 minute** in the runner. Real prod runs sub-minute. For arena replay against minute bars, 1-minute is the natural granularity; no fidelity loss vs the bar data we have.

---

## §6 — Status: SHIPPED SKELETON

- ✅ `mx-arena/arena/sim_alpaca_client.py` — thin shim (150 LOC; zero prod changes)
- ✅ `mx-arena/arena/orchestrator_stub.py` — decision_row + policy modes + factory
- ✅ `scripts/strategy_harness_run.py` — per-session driver
- ✅ `tests/unit/test_sim_alpaca_client.py` — 7 tests including end-to-end AR-scenario replay
- ✅ End-to-end run on 4/28 produces 10 coherent trades

**Block 4.4 readiness:** infrastructure complete. Remaining work is policy decisions for 86-session run + aggregation. **Shipping next session if no surprises emerge.**
