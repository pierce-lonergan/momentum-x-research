# 169 — 2026-05-19 D308/D309 stop-widening T1 shadow + critical NXXT finding

**Session date:** 2026-05-19 night
**Branch:** develop
**Predecessor:** [doc 168 Discord completeness](168_v6_2026_05_19_discord_completeness_sweep.md)
**Trigger:** Diagnostic confirmed every trade is being stopped out at exactly −1.5% by D142 Phase 1 override before any thesis plays out. NXXT (+44% unrealized) only worked because the bot crashed and didn't have a stop running.

---

## 0. The plan

Three-tier rollout. **This commit ships T1.** T2 and T3 are gated on T1 data.

| Tier | What | Risk | Gate to next |
|---|---|---|---|
| **T1 — Pure shadow** (tonight) | Log what wide-stop would have done. No order changes. | **Zero** | 3 trading days of clean data |
| **T2 — A/B split** (Fri) | 50% of trades get ATR stops with halved sizing | Bounded — half-size on wide arm | 5 days, wide arm > tight arm by ≥1.5σ |
| **T3 — Production cutover** (next week) | Replace D142 override with phase-widening callback | Full | NXXT trigger-reliability fix first |

---

## 1. T1 — what's in this commit

### D308 — `StopDecisionLog` (emitter)

`src/analysis/stop_decision_log.py` — a single class that writes one JSONL line per OTO submission. Every event captures:

- `entry`, `atr_stop` (wide), `atr_dist_pct`
- `phase1_stop` (tight), `phase1_dist_pct`, `phase1_pct_config`
- `submitted_stop` (what actually went to broker), `submitted_strategy`
- `qty` and `qty_halved_hypothetical` (T2 sizing)
- Optional context: `gap_pct`, `atr_14d`, `kelly_tier`, `mfcs`, `d106_class`, `target_prices`

**Defensive guarantees**: never raises, dir-create-failure silently disables emission, write-failure logs a warning but does not block the OTO submission. Path lookup failure leaves `_path=None` and `log_stop_decision()` becomes a no-op.

Wired into `AlpacaExecutor.__init__` as a new optional `stop_decision_log` kwarg (backwards-compatible). Emitted from `submit_entry` immediately BEFORE the OTO submission so every long-side attempt is captured — including those that subsequently get BRIDGE_CANCEL'd.

### D309 — `scripts/stop_widening_replay.py` (offline simulator)

Reads every `stop_decisions_*.jsonl`, simulates each event against minute_aggs:

- **Stop-hit check**: `low <= atr_stop` → exit at `atr_stop`
- **Target ladder**: 33% at +3%, 33% at +6%, 34% at +10% (matches D106)
- **EOS fallback**: if no exit triggered by 15:55 ET, exit at last close

Output: `data/shadow_stops/replay_results.parquet` with per-event:
- ATR-stop arm: exit price, strategy, tranches hit, full-qty P&L, half-qty P&L
- Phase1-stop arm: same fields (sanity check vs actual)
- `delta_pnl_atr_vs_phase1`: positive = wide stop wins

### Launcher wiring

`scripts/daily_data_ingest.ps1` step **4c** runs the replayer every night at 17:30 ET. `-Critical $false` — a replay failure delays analysis by a day, doesn't break the lake.

---

## 2. The result — wide stops would have made +$1,843 last week

Manually backfilled stop_decisions for the 7 actual losses + NXXT and ran the replayer against real minute bars:

| Metric | Phase1 stops (actual) | Wide ATR stops (counterfactual) |
|---|---|---|
| Win rate | **0/7** (0%) | **6/8 (75%)** |
| Mean exit | −0.54% | **+2.05%** |
| Full-qty P&L | −$33 | **+$1,810** |
| Half-qty P&L (T2 sizing) | -- | **+$905** |
| **Delta** | -- | **+$1,843** |

Per-trade breakdown:

| Ticker | Phase1 exit | ATR exit | Delta P&L |
|---|---|---|---|
| VELO | −1.51% (stopped) | −1.55% (eos) | −$10 |
| MOBX | −1.58% (stopped) | **+6.37% (targets full)** | **+$1,147** |
| YOOV | −1.47% (stopped) | −13.97% (stopped wider) | −$1,889 |
| SLE | −1.53% (stopped) | **+6.37% (targets full)** | **+$994** |
| AUUD | −1.52% (stopped) | +2.97% (partial) | +$15 |
| CISS | −1.50% (stopped) | **+6.37% (targets full)** | **+$824** |
| GCTS | −1.59% (stopped) | +3.51% (partial) | +$762 |
| NXXT | +6.37% (luck) | +6.37% (same) | $0 |

YOOV is the one big loser with wider stops (the "cost" of letting trades breathe). But the 4 trades that hit `targets_full` more than offset it (+$3,727 wins vs −$1,889 loss). **The thesis is empirically supported even on this small sample.**

This is observational, not validated:
- 8 events from production-history backfill — not from live emission
- One-shot replay, no cross-validation
- T1 success criterion needs ~15 live events over 3 trading days

But it's strong enough to ship T1 + start collecting live data tonight.

---

## 3. **CRITICAL — NXXT stop-reliability finding (T3 blocker)**

Per the user's plan, I investigated whether Alpaca actually triggered NXXT's $0.50 stop when price dipped to $0.42 Monday. **The stop never had a chance to trigger because the bot CANCELED it 2 minutes 3 seconds after submission and never re-submitted.**

Alpaca order timeline from `/v2/orders?symbols=NXXT`:

```
10:46:43.10  BUY  oid=d67accee  limit=$0.5134  status=filled   qty=40476
10:46:43.35  SELL oid=2874d803  stop=$0.5034   status=canceled  qty=40476
10:48:46.34  ←── STOP CANCELED HERE
16:00:21     SELL market         status=canceled (EOD attempt, blocked by D278)
```

Bot log at exactly 10:48:46:
```
D215 PATH P&L: NXXT closed via TRAILING_STOP | P&L=$-109.29 | Path RESCAN cumulative: $-109.36
```

**The bot's internal journal recorded a $109.29 close — but no sell ever happened at the broker.** Walking the code:

`src/data/websocket_client.py:735-779` shows the `TrailingStopManager` flow:
1. OTO buy fills → `on_fill()` returns `CANCEL_STOP` action
2. Executor cancels the original protective stop
3. Executor SHOULD submit a new trailing stop
4. **The submit callback evidently failed silently** — no trailing stop appears in broker orders

NXXT has been **completely unhedged for 30+ hours**. The +$8,880 unrealized gain is pure luck — if price had gone to $0.20 instead of $0.73, we'd be down ~$12k.

**This blocks T3 production cutover.** Wider stops are dangerous if the bot can silently delete them mid-day. **Filed D310** to fix the cancel→resubmit chain in `TrailingStopManager` before any wide-stop production rollout.

Mitigations until D310 lands:
- Tomorrow morning's D91 next-open close will exit NXXT cleanly (per doc 167 + D295 wiring)
- If price drops aggressively overnight, the position is unhedged — operator should monitor

---

## 4. Decision gates (per user's plan)

```
T1 SHIPPED tonight  ──→  3 days live shadow data  ──→  ATR-stop mean P&L > 0?
                                                       │
                                          yes ────────┴──────── no
                                           │                     │
                                           ▼                     ▼
                                       T2 Friday              Re-examine selection
                                           │                  (D293.8 path)
                                  5 days A/B data
                                           │
                                           ▼
                          wide arm > tight arm by 1.5σ?
                                           │
                                  yes ─────┴───── no
                                   │              │
                                   ▼              ▼
                              T3 next week    Iterate or kill
                          (REQUIRES D310 fix)
```

**T1 abort criteria** (will abort BEFORE T2 if any of these):
- Live shadow data shows ATR-stop arm cumulative P&L < −$2,000 across 3 days
- D310 NXXT trigger-reliability bug is unfixable in time
- Bot has another crash or critical failure that masks the data

---

## 5. Files in this commit

| File | Change |
|---|---|
| `src/analysis/stop_decision_log.py` | NEW: D308 emitter (~170 LOC) |
| `src/execution/alpaca_executor.py` | +35 LOC: kwarg + dual-stop tracking + emission BEFORE OTO submit |
| `main.py` | +5 LOC: construct StopDecisionLog, pass into AlpacaExecutor ctor |
| `scripts/stop_widening_replay.py` | NEW: D309 replayer (~230 LOC) |
| `scripts/daily_data_ingest.ps1` | step 4c: nightly replayer invocation |
| `data/shadow_stops/stop_decisions_BACKFILL_2026-05-13_to_2026-05-18.jsonl` | One-time backfill: 8 historical events for tonight's first replay |
| `data/shadow_stops/replay_results.parquet` | Initial baseline: +$1,843 wide-stop counterfactual |
| `tests/unit/test_d308_d309_stop_widening_shadow.py` | NEW: 18 tests (emitter + simulator + load + wiring contract) |
| `docs/research-log/169_v6_2026_05_19_d308_d309_stop_widening_T1_shadow.md` | THIS doc |

**Tests:**
- 18/18 D308/D309 tests pass
- 224/224 full regression across all touched + adjacent code paths

---

## 6. Filed for follow-up

- **D310 (T3 BLOCKER)**: Fix `TrailingStopManager.on_fill` → cancel → resubmit chain. The cancel succeeds at broker but the resubmit doesn't fire, leaving positions unhedged. Need to either guarantee atomic cancel+resubmit OR delete the cancel until resubmit succeeds first.
- **NXXT current state**: position still open, unhedged. Tomorrow's bot will D91-close it at 09:30 ET if it stays at +44%. Operator should be aware.
- **`MIN_PRICE_FOR_STOP_LOSS` floor (per user's plan)**: even if D310 is fixed, sub-$1 names may have unreliable Alpaca stop-trigger behavior. Investigate adding `$1.00` floor and time-based market exit below that.
- **Wider-stop position sizing**: when T3 lands, size DOWN to maintain dollar-risk. Current sizing assumes 1.5% stop × 2% portfolio risk → halve position size when stops widen to 5-10%.

---

## 7. Verdict

**Status:** **T1 SHIPPED 2026-05-19.**

Tomorrow's bot will emit live `stop_decision` events for every OTO submit. Tomorrow night's 17:30 ET data ingest will append those events to `data/shadow_stops/replay_results.parquet`. By Thursday close we have ~3 days of live shadow data. Friday morning the T2 decision gate fires.

This is the cheapest possible validation step:
- 0% risk to production (no order changes)
- 0% cost (just one extra JSONL write per OTO + 30s nightly replay)
- 100% of the data needed to make the T2 call with confidence

The path to 10%/day starts with proving wide stops do better than tight stops on REAL live trades. That data starts arriving tomorrow.
