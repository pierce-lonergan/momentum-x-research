# 221 — B1 Phase 0: ground truth (the optimistic-booking map + the confirmation-channel gap)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce's B1 plan — "Phase 0: do not skip. Find every optimistic-booking site +
the broker confirmation channel(s). No code yet. If you can't find the confirmation channel
cleanly, that's the first finding — surface it before designing."

This is the deliverable. **No ledger code.** Two parallel read-only audits + my own
verification of the one claim the whole design hinges on.

---

## 0. THE headline finding (surface-before-design)

> **A broker fill-confirmation channel EXISTS and is wired live — the Alpaca `trade_updates`
> WebSocket stream (`fill_stream_bridge.on_trade_update`, main.py:1083). But it is NOT
> durable (in-memory deque, drained per Phase-3 cycle, never persisted) and it DROPS the
> dedup key Alpaca actually provides.**

The agent reported "no unique per-fill id exists." I verified the parser
(`trade_updates.py parse_trade_update`) + the wire fixture (`test_trade_updates.py`): the
parser reads only from `data.order` (`order.id` = the ORDER id, `filled_qty`,
`filled_avg_price`). **Alpaca's real trade_update carries `data.execution_id` (a unique
per-fill id) as a sibling of `data.order` — and we never extract it.** So the precise gap is
narrower and more fixable than "no channel": **the channel is live; B1's prerequisite is
(a) extract `data.execution_id` as the `broker_event_id`, and (b) persist every fill event
durably before processing.** That is B1 Phase 1-2, and it's a real-field extraction, not an
invention.

**Implication for the plan:** B1 can be built on the existing stream — but Phase 1/2 (the
event schema + the durable append-only log keyed on `execution_id`) must come BEFORE Phase 4
(wiring), exactly as the mandate sequenced. The confirmation channel is not missing; its
*durability + dedup key* are. Do not start the fold (Phase 3) until the log captures
`execution_id`.

## 1. Optimistic-booking sites (the B1 surface area to replace)

Books/mutates P&L or position state on WRAPPER/REQUEST success, before a confirmed fill:

| # | Site | Trigger | Mutates | Note |
|---|---|---|---|---|
| O1 | `bridge.close_with_attribution` (`position_manager.close_position_with_attribution`, pm.py:1097-1107) | caller passes `exit_price`; books `pnl=(exit-entry)*qty` | `_daily_realized_pnl +=`, `remove_position`, `trade_results.jsonl` | **the engine of the phantom** — pnl from a passed-in price, not a fill |
| O2 | `post_fill_handler.py:374-412` (D146 BAR-1 exit) | `await client.close_position()` *doesn't raise* | O1 | **UNGUARDED** — no fill check at all |
| O3 | `main.py:4579` (stopout circuit-breaker close) | stopout *assumed* | O1 | unguarded |
| O4 | `main.py:5124` (D146 override path) | BAR-1 retry | O1 | unguarded |
| O5 | `bridge.py:905-912` (D91 overnight journal) | `close_result.succeeded` | journal only (position NOT removed) | partial fix doc 177; incomplete |
| O6 | the other `close_with_attribution` call sites in main.py (≈5302, 5923, 6096, 6350, 7102…) | varying | O1 | guard varies per site |

**The common root = O1.** `close_with_attribution` computes P&L from a **passed-in
`exit_price`** and books it — it has no idea whether the broker filled. doc 177 added a
*guard* at the D76 site (only call O1 if `attempt_close.succeeded`), and docs 216/218/219/220
hardened *when* succeeded is true — but **`succeeded` is a wrapper hint, not a broker fill**,
and the ~half-dozen OTHER callers (O2-O4, O6) reach O1 with weaker/no guards. That's the
suppressible-not-impossible gap Pierce named: as long as O1 books off `exit_price`, a caller
that reaches it without a confirmed fill makes a phantom.

## 2. Confirmed-booking sites (already correct — the model to copy)

These book ONLY on an actual broker fill event — they are what the whole system should look
like after B1:

| Site | Trigger | Why it's correct |
|---|---|---|
| `tranche_monitor.py:213-243` | `on_trade_fill()` with `filled_qty>0`, `filled_price` from the stream | books `(fill_price-entry)*filled_qty` off a real fill |
| `fill_stream_bridge.py:248-309` (D297 OTO stop-leg) | WebSocket sell-fill event | `record_close` + `remove_position` off the actual fill |

**B1 = make ALL booking look like these two**, fed by a durable log keyed on `execution_id`.

## 3. The fill-observation mechanisms (ranked)

1. **WebSocket `trade_updates` stream (PRIMARY, live, main.py:1083)** — the right channel.
   Carries `order.id`, `filled_qty`, `filled_avg_price`, `filled_at`, legs. **Missing:
   `data.execution_id` (not extracted) + durability (in-memory deque only).**
2. **`_poll_for_terminal_fill` (bridge.py:72-176, D217)** — polls `get_orders` up to 6×/12s
   after submit. A *backstop*, same fields, same missing dedup id.
3. **EOD recon `get_positions` (eod_recon.py, D238)** — broker truth, but position-level, no
   fill id, end-of-day only. This is the *reconciliation* input (B1 Phase 5), not a fill feed.
4. **D165 price-tranche fallback** — a corrective *action*, not an observation. Not a channel.

## 4. The 6/1 phantom sequence (the Phase-6 replay seed)

Reconstructed from the 6/1 log + this map:
1. D76 EOD close on STG → `close_position` 403 (`held_for_orders=3382`) — broker did NOT fill.
2. (pre-216) wrapper retries 403 ×3 → `succeeded=False`. **But STG's journal shows +$304.20.**
3. The phantom was booked by an **O1-class** path (`close_with_attribution` / `record_close`)
   reached **without** a confirmed fill — the journal recorded an exit (`+$304.20`) the broker
   never executed (`broker_total_pnl` for STG = $0). `D222 PNL_RECON DIVERGENCE` caught it
   *after* the fact.
4. The honest replay seed: a sequence where `close_position` 403s / never fills, yet a
   booking site writes `realized_pnl > 0`. **In the B1 world, with no `FILL_*` event (no
   `execution_id`), the fold books $0 — the phantom cannot exist.**

## 5. Phase-0 verdict + the sequenced plan (unchanged from Pierce's, with the gap inserted)

- **Confirmation channel: EXISTS (the live stream), but needs `execution_id` extraction +
  durability.** That is the FIRST build, before the fold.
- **Surface area: ~6 optimistic sites, all funneling into O1 (`close_with_attribution`'s
  book-from-exit_price).** Replacing O1's authority is the core of B1.
- **Revised Phase order:**
  1. **Phase 1+2 FIRST** — extract `data.execution_id` into `TradeUpdateEvent` + define the
     event schema + the durable SQLite append-only log (dedup PK = `broker_event_id`).
     (The mandate's Phase 1/2; the `execution_id` extraction is the one addition.)
  2. **Phase 3** — position/P&L as a pure fold over the log.
  3. **Phase 4** — replace O1-O6 so they emit non-bookable events; only stream `FILL_*` events
     (with `execution_id`) book. Delete the optimistic path.
  4. **Phase 5** — recon loop (eod_recon already gives broker truth; add the
     `LEDGER_BROKER_DRIFT` CRITICAL on any delta).
  5. **Phase 6** — the 5 Adversary scenarios (`wrapper_success_without_fill` = the literal
     6/1 phantom) + the 8-gate gauntlet + the ≥5-session shadow.
- **No code shipped this turn** (Phase 0 is investigation, per the mandate).

## 5b. The ONE assumption to verify before Phase 1 (don't build on faith)

I assert `data.execution_id` exists in Alpaca's `trade_updates` wire format from API
knowledge — but I CANNOT prove it from this repo (no raw-payload capture exists; the test
fixture is hand-authored and omits it, and `execution_id` is referenced nowhere in the code,
confirmed by grep). **Phase 1 task #0: capture one real raw `trade_updates` message** (log
`msg` verbatim in the websocket client for a session, or hit Alpaca's docs/API) and confirm
the per-fill unique id's actual key (`data.execution_id`? `data.order.id` per partial?
something else). If Alpaca does NOT provide a per-fill id, the dedup key falls back to a
deterministic hash of `(order_id, filled_qty, filled_avg_price, filled_at)` — workable but
weaker on stream-reconnect duplicates. **Verify the real field before designing the schema
around it** — the same ground-truth discipline that caught the cap bug and the MFCS artifact.

## 6. What I verified myself (not just trusted the agents)
- The `execution_id` claim: confirmed the parser + fixture only use `data.order`; Alpaca's
  real payload has `data.execution_id` we drop. **This reshaped the plan** (channel exists,
  durability+dedup is the gap) vs the agent's "no channel" framing.
- O1 as the phantom engine: confirmed `close_with_attribution` books from a passed-in
  `exit_price` with no fill check — the structural root, exactly as Pierce diagnosed.

## Appendix — key file:line
- O1 root: `src/execution/position_manager.py:1097-1107`, `src/execution/bridge.py:1639-1734`.
- Confirmed model: `src/execution/tranche_monitor.py:213-243`, `fill_stream_bridge.py:248-309`.
- The channel: `src/data/trade_updates.py:parse_trade_update` (extracts `data.order` only —
  **add `data.execution_id`**), `main.py:1077-1089` (stream wiring).
- Backstop poll: `src/execution/bridge.py:72-176`. Recon: `src/monitoring/eod_recon.py`.
- This doc + changelog. NO ledger code yet (Phase 0).
