# 52 — Bug AM: PositionManager.attach_external_stop + auto-discovery in D56 sync

**Status:** patched 2026-04-27 evening (Tier 3 #10).
**Severity:** **HIGH** — D230 RECON_WARN STOP fired every 30s for the entire trading session because the tracker showed COMPUTED-DEFAULT stop ($2.29) while the broker had the actual operator-submitted stop ($2.10) for LIDR.
**Surface:** Live session log + Tier 3 audit. Today's `D230 RECON_WARN STOP LIDR: internal_stop=$2.29 but no stop_order_id (cannot reconcile against broker; D56 no-broker-stop case)` repeated ~780 times (30s ticks × 6.5 hours).
**Bug class:** missing API — no operator-facing way to register an externally-submitted broker stop with the position tracker; no startup auto-discovery from broker orders either.

## §0 — TL;DR

This morning at 08:22 ET I submitted a manual GTC protective stop on LIDR @ $2.10 to bridge the Bug AG fix deployment. The bridge correctly preserved my stop on restart (per Bug AG's D86 PRESERVED logic). But the **internal tracker** had no way to know about it — the D56 fresh-start sync computed a default stop ($2.29 = entry × (1 - 0.055)) and set `stop_order_id = ""`. The result: D230 RECON_WARN STOP fired every 30s for the entire session because the internal stop_loss disagreed with the (unknown-to-tracker) broker stop.

Bug AM provides two complementary fixes:

1. **`PositionManager.attach_external_stop(ticker, stop_order_id, stop_price, qty=None)`** — operator/recovery API to register a broker stop with the tracker after-the-fact.

2. **Auto-discovery in `sync_from_broker(broker_orders=...)`** — when called with the broker's open orders, the sync scans for matching sell-stop orders covering each position's qty and auto-attaches them. main.py's D56 fresh-start path now passes `alpaca_orders` so this happens automatically.

## §1 — Root cause

`src/execution/position_manager.py:sync_from_broker` (D56) had no awareness of broker orders — it computed a default stop value and explicitly set `stop_order_id=""` to signal "no broker stop confirmed yet" (Wed 2026-04-22 Bug C honesty fix). Downstream D230 RECON_WARN treated the empty oid as a drift signal.

The intent of the empty-oid signal was good (don't pretend we have a stop when we don't). The gap was that D56 never queried broker orders to find an existing stop. Operator-submitted stops + carried-over stops from prior sessions were both invisible.

## §2 — Fix (~150 LOC)

**`PositionManager.attach_external_stop`** — the operator API:
- Validates ticker exists in tracker, stop_order_id non-empty, stop_price > 0, optional qty matches
- Mutates `position.stop_order_id` and `position.stop_loss`
- Logs `D273 STOP_ATTACHED` for operator visibility
- Returns True on success, False on validation failure (NEVER raises)

**`PositionManager._find_matching_protective_stop`** — the auto-discovery helper:
- Filters broker orders by symbol + side=sell + type=stop + status in (new, accepted, held)
- Requires qty >= position.qty (over-protective stops accepted, under-protective skipped)
- Returns first match (deterministic) or None

**`PositionManager.sync_from_broker(broker_positions, broker_orders=None)`** — modified:
- If broker_orders provided, runs auto-discovery for each position
- On match: builds ManagedPosition with the broker's stop_order_id + stop_price, logs `D56 sync (Bug AM auto-attach)`
- On no-match: falls through to the existing COMPUTED DEFAULT path (preserves Wed 2026-04-22 honesty)

**`main.py` D56 fresh-start path** — modified:
- Calls `await client.get_orders(status="open", limit=200)` before sync
- Passes the orders to `sync_from_broker(broker_orders=...)`
- Wrapped in try/except for non-fatal API failure (falls back to no-orders behavior)

## §3 — Aggressive testing

`tests/unit/test_bug_am_stop_attach.py` — 12 tests:

- **Direct attach API tests**: success path, qty mismatch detection, parametrized validation failures
- **Auto-discovery tests**: matching stop attached, no-orders falls through to COMPUTED DEFAULT, parametrized non-matching stops are SKIPPED (wrong symbol, buy-stop, take-profit limit, terminal status, smaller qty)
- **Multi-stop disambiguation**: when multiple valid stops exist, picks the first deterministically
- **Over-protective stop**: stop sized for MORE shares than position is accepted (operator slack)
- **End-to-end LIDR**: feeds the exact today's-broker-state into the sync, confirms the tracker reflects $2.10 + the real broker oid

All tests use REAL `PositionManager` (not MagicMock) so the wiring is exercised.

## §4 — How this would have manifested today

08:22 ET: I submit manual stop $2.10 on LIDR (oid `859be64d`).
**Pre-Bug-AM:** at 08:29 ET restart, D56 syncs LIDR with stop=$2.29 COMPUTED DEFAULT, stop_order_id="". Every 30s for the next 7.5 hours, D230 RECON_WARN STOP LIDR fires. Operator sees ~900 D230 lines in the log (no actionable signal because tracker is just wrong).

**Post-Bug-AM:** at 08:29 ET restart, D56 calls `get_orders(status='open')`, sees the manual stop, auto-attaches it. Tracker shows stop=$2.10 with the real broker oid. D230 NEVER fires for this position.

Operator visibility: the `D56 sync (Bug AM auto-attach)` log line is distinct from `D56 sync ... COMPUTED DEFAULT` — easy to grep / filter.

## §5 — Discovery rate impact

| Pre-Bug AM | After Bug AM |
|---|---|
| 24 production / oracle / harness bugs surfaced + fixed | **25** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **25/0 = ∞** |

## §6 — Downstream effects

- D230 RECON_WARN volume drops dramatically on next session (was firing every 30s for any carried position with operator stop)
- The `attach_external_stop` API is now the operator's single-knob recovery path for "I submitted a stop manually, register it with the tracker"
- `sync_from_state_and_orders` (the D64 enhanced-recovery path) ALREADY had stop_order_id resolution from session_state, but only used it when session_state was present. Bug AM doesn't extend that path — but adding the auto-discovery there would be a follow-up improvement (Bug AO candidate)
