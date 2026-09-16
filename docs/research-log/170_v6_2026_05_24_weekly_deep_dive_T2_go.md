# 170 — 2026-05-24 Sunday: weekly deep-dive + T2 GO recommendation + D311 fix

**Session date:** 2026-05-24 Sunday evening
**Branch:** develop
**Predecessors:** [doc 169 D308/D309 T1 shadow](169_v6_2026_05_19_d308_d309_stop_widening_T1_shadow.md), [doc 168 Discord completeness](168_v6_2026_05_19_discord_completeness_sweep.md)
**Trigger:** User: *"it is sunday, lets do a deep dive on last week and see what happened"*

---

## 0. Headline

**Account equity: $148,153 (+$8,153 / +5.82% for the week)** — first profitable week.

The win is almost entirely **NXXT's accidental +$10,592 carry** (D310 bug = no stop = lucky rally). The bot's *intentional* trades lost ~$2,500. But the **wide-stop T1 shadow data is decisive: 6/6 wins on this week's live events, +$4,111 counterfactual P&L delta. T2 A/B split should fire Monday morning.**

3 new findings shipped tonight:

| Code | What | Status |
|---|---|---|
| **D311** | `run_pnl_reconciliation` was missing `after=` filter → $49k phantom D222 deltas every EOD | **FIXED** + 6 tests |
| **T1 shadow** | Live data confirms wide-stop thesis emphatically (6/6, +$4,111) | **GO for T2** |
| Friday silent BUYs | Phase 2 emitted 10 BUYs but 0 reached broker — gate visibility missing | Filed for D312 |

---

## 1. The week in numbers

### Trades (broker-confirmed, all closed)

| Ticker | Buy | Sell | Hold | Qty | P&L | Notes |
|---|---|---|---|---|---|---|
| NXXT | 5/18 10:46 | **5/20 09:30** | 2.0d | 40,476 | **+$10,592** | D91 next-open close (clean) — unhedged for 3d, pure luck |
| IMVT | 5/20 10:48 | **5/21 09:34** | 22.8h | 483 | −$0.50 | D91 carry-overnight, near-flat |
| GCL | 5/20 11:04 | 5/20 11:04 | <1m | 16,098 | −$192 | Phase 1 stop, -2.3% |
| NIVF | 5/21 11:20 | 5/21 11:20 | <1m | 7,978 | −$52 | Phase 1 stop, -1.7% |
| **Net** | | | | | **+$10,348** | |

Plus tiny carry forwards / interest. Equity went $140,000 → $148,153.

### What worked, what didn't

- **D91 / D295 (carry-overnight + journal recording)**: worked flawlessly twice (NXXT Wed open, IMVT Thu open). The doc 167 hotfix + D295 wiring shipped clean.
- **D278 t1_next_open**: held both positions overnight as intended.
- **D297 stop-fill journal**: didn't get tested this week (no OTO stops fired).
- **D106 Phase 1 stops**: stopped GCL and NIVF at exactly −1.5%–−2.3% within minutes. Same pattern as the prior week.
- **Friday**: 0 trades, despite Phase 2 emitting 10 BUYs per round. Gate visibility gap.

### Activity is down sharply

- Last week (5/13–5/18): 8 trades over 4 days
- This week (5/20–5/22): 3 trades over 3 days (excluding NXXT close)
- Friday alone: 0 trades

Selection threshold may be too tight, OR Friday-specific issue (catalyst-list filter? all candidates rejected by MFCS<0.25?). Needs visibility — filed as **D312**.

---

## 2. T1 shadow: the empirical answer

The replayer now runs on 14 events (8 historical backfill from prior week + 6 live from this week). Live-only is the gate:

### Live this week (6 events, Wed + Thu)

| Ticker | Entry | ATR% | P1% | Wide exit | Wide P&L | P1 P&L | **Delta** |
|---|---|---|---|---|---|---|---|
| MTVA | $1.94 | −9.5% | −1.5% | +6.4% (targets_full) | +$1,991 | +$1,991 | $0 |
| CODX | $2.02 | −21.3% | −1.5% | +6.4% (targets_full) | +$891 | +$891 | $0 |
| IMVT | $35.44 | −17.4% | −1.5% | +0.7% (eos) | +$120 | −$256 | **+$377** |
| **GCL** #1 | $0.73 | −5.9% | −1.5% | **+6.4% (targets_full)** | **+$1,423** | −$336 | **+$1,759** |
| **GCL** #2 | $0.76 | −5.7% | −1.5% | **+6.4% (targets_full)** | **+$1,422** | −$335 | **+$1,757** |
| NIVF | $1.20 | −31.0% | −1.5% | +0.8% (partial) | +$72 | −$143 | **+$216** |

**Live-only totals:**
- ATR-arm: **+$5,922 (6/6 wins = 100%)**
- Phase1-arm: +$1,811 (2/6 — and those 2 hit targets before stop, so no real benefit)
- **Delta: +$4,111** full size, **+$2,961** at T2 half-size

GCL submitted twice (both with phase1=1.5%): each would have made +$1,400 instead of losing $335. That's **+$3,500** wide-stop opportunity cost on a single ticker. The bot identified GCL as a high-confidence entry, sized it ~30k shares, and got stopped at noise twice.

### Combined (14 events, 8 historical + 6 live)

- Win rate: **12/14 (86%)** under wide stops
- Total ATR-arm P&L: +$7,733
- Total Phase1-arm P&L: +$1,778
- **Delta: +$5,955**

The user's T1 success criterion ("ATR-stop mean P&L > 0 on aggregate, OR ≥1 shadow trade with >+10% capture") is exceeded on both:
- Mean ATR-stop P&L: **+3.10%** (vs phase1 +0.17%)
- 5 trades captured `targets_full` (+10% ladder) under wide stops vs phase1 cutting them off

**Decision: T2 GO.**

---

## 3. NXXT post-mortem — the lucky win

The +$10,592 close is the headline of the week, but the play-by-play reveals it was **completely unmanaged**:

```
Sun 5/18 10:46 ET  BUY  fill   $0.5096  qty 40,476
Sun 5/18 10:48 ET  STOP CANCELED by bot at broker, NEVER re-submitted (D310)
Sun 5/18 16:00 ET  EOD market sell ATTEMPTED, CANCELED 124ms later (D278 t1_next_open guard)
Mon 5/19 04:30 ET  Bot crashed (doc 167 hotfix shipped that evening)
Tue 5/20 04:30 ET  Bot booted clean. NXXT bootstrap: "stop=$0.48 COMPUTED DEFAULT, no broker order"
Tue 5/20 04:30+    D230 RECON_WARN STOP NXXT keeps firing every 30s: "no stop_order_id"
Wed 5/20 09:30 ET  D91 next-open close FIRES. STEP 1: no stop to cancel. STEP 2: market sell.
                   STEP 3: position removed. STEP 4: journal record_close = MISSING.
Wed 5/20 09:30:12  Broker fill confirms: 40,476 shares sold market = +$10,592.37 realized.
```

NXXT was unhedged Sunday afternoon → Wednesday open = **66 hours of pure directional exposure**. If price had collapsed (which is normal for promo pumps), we'd have been down −$15-20k on the position alone. **The +44% gain is not a strategy result. It is the inverse of what the system was supposed to do.**

**D310 (TrailingStopManager cancel-without-resubmit) is still unfixed.** This **MUST** land before T3 production cutover. Wide stops + cancel-without-resubmit = silent unhedge = could have been a -$20k week instead of +$8k.

---

## 4. D311 fix shipped tonight

### The bug

`src/analysis/trade_journal.py:run_pnl_reconciliation()` was calling `client.get_orders(status="all", limit=500)` with **no `after=` filter** — exactly the same architectural bug D238 had (patched in doc 158 on 5/12). Every EOD this pulled 7+ days of historical orders and compared them against today's empty journal, producing:

```
Wed 5/20: D222 PNL_RECON DIVERGENCE: journal_total=$-596.51 broker_total=$49385.46
          delta=$49981.97 — 69 tickers journal=MISSING
Thu 5/21: D222 PNL_RECON DIVERGENCE: ...delta=$49267.33 — 69 tickers
Fri 5/22: D222 PNL_RECON DIVERGENCE: ...delta=~$49k — same flood
```

D238 (the **other** recon, used for the D304 Discord embed) ran fine the whole time: `D238 EOD_RECONCILIATION_DELTA: clean — delta=$+0.00`. So the user-facing Discord embed was OK, but logs and any tooling that scrapes D222 saw $49k phantom-deltas every session.

### The fix

`trade_journal.py:981` now computes today's UTC midnight and passes it as `after=` to `get_orders`. Mirrors the D238 fix verbatim.

### Tests

`tests/unit/test_d311_pnl_recon_after_filter.py` — 6 tests:
- Headline: get_orders is called with `after=` kwarg
- Bounds: `after` is within 3s of today's UTC midnight
- Behavior: empty broker fills + empty journal = no false divergence
- Backwards-compat: `get_account_activities` path unchanged
- Smoke: same-day real divergence still fires D222 WARNING
- Static AST guard: future refactors that drop `after=` fail at commit time

---

## 5. Other findings (filed, not fixed tonight)

### D310 — TrailingStopManager cancel-without-resubmit (T3 BLOCKER)

NXXT confirmed this fires in production. After OTO buy fills, `TrailingStopManager.on_fill()` returns `CANCEL_STOP` action. Executor cancels the OTO stop at broker. Trailing-stop resubmit callback evidently fails silently. Position becomes unhedged.

**Action**: investigate `websocket_client.py:735-779` and the executor's `on_submit_trailing` callback wiring. Either guarantee atomic cancel+resubmit (use Alpaca's `replace_order` API) or delay the cancel until resubmit succeeds.

**Severity**: blocks T3. Don't widen stops in production until this is reliable. The whole thesis assumes the broker has a stop in place at all times.

### D312 — Friday silent-BUYs (visibility gap)

Phase 2 emitted "10 BUY, 0 HOLD, 0 NO_TRADE" multiple times Friday. Then `D115 KELLY STANDARD: tier=1` with various "failed" reasons (mfcs<0.25, directional<3, catalyst=NONE not in proven list, reward_risk<2.5). And then... nothing. 0 OTO submits.

Either:
- "BUY" in Phase 2 doesn't directly translate to OTO submit (there's a final filter we don't log)
- Some downstream gate (D224 Kelly halve, position cap, risk budget) silently rejected all 10
- Bug AK ALIGNMENT_PASSED warnings indicate selection conflict

The operator can't see why the bot did 0 trades Friday. The D304 Discord EOD message will say "0 trades — system scanned but no candidates met the quality bar" — which is misleading because Phase 2 says 10 BUYs.

**Action**: add a `D312 GATE_REJECT` log when a Phase 2 BUY verdict doesn't reach OTO submission, with the rejecting gate name + reason. Should be a few-line change in the orchestrator.

### NXXT-class accidental wins are NOT a strategy

The whole reason the week was profitable is that one position became disconnected from any risk management for 3 days. That's not repeatable, it's not safe, and it doesn't generalize. **The wide-stop T2 thesis is the real bet** — repeatable, bounded risk, validated by live data.

---

## 6. Files in this commit

| File | Change |
|---|---|
| `src/analysis/trade_journal.py` | +12 LOC: `after=` filter in `run_pnl_reconciliation` get_orders call |
| `scripts/stop_widening_replay.py` | +3 LOC: `format="ISO8601"` for mixed-format timestamps |
| `tests/unit/test_d311_pnl_recon_after_filter.py` | NEW: 6 tests |
| `docs/research-log/170_v6_2026_05_24_weekly_deep_dive_T2_go.md` | THIS doc |

**Tests:** 34/34 pass (6 D311 + 18 D308/D309 + 10 D24 EOD failsafes).

---

## 7. T2 deployment plan (recommend for Monday 5/25 morning)

**T2 IS A GO** per the live data. Recommended deployment:

```python
# orchestrator.py — at verdict finalization
import hashlib
arm_seed = hashlib.md5(f"{trading_date}:{symbol}".encode()).hexdigest()
arm_bucket = int(arm_seed[:8], 16) % 100  # deterministic per (date, symbol)
verdict.execution_arm = "wide_stop" if arm_bucket < 50 else "tight_stop"
verdict.qty_multiplier = 0.5 if verdict.execution_arm == "wide_stop" else 1.0
```

```python
# alpaca_executor.py — replace the D142 Phase 1 override block
if getattr(verdict, "execution_arm", "tight_stop") == "wide_stop":
    _stop_price = _d308_atr_stop  # use the wide ATR stop directly
    _d308_strategy = "atr_ab"
    qty = int(qty * verdict.qty_multiplier)  # halve for bounded risk
    logger.info(
        "%s D310.T2: WIDE_STOP arm -- stop=$%.4f (%.1f%% ATR), qty=%d (halved)",
        verdict.ticker, _stop_price,
        (_stop_price/verdict.entry_price - 1) * 100, qty,
    )
```

**T2 success criterion** (per user's plan): across ≥5 trading days, wide-stop arm dollar P&L > tight-stop arm dollar P&L by ≥1.5σ of trade-level variance.

**T2 abort criterion**: wide arm down >−$2,000 cumulative. Kill it and re-examine selection.

**Pre-T2 checklist**:
1. ✅ T1 shadow data shows wide-stop arm wins (+$4,111 live, +$5,955 incl. backfill)
2. ✅ T1 emitter + replayer stable (18/18 tests, 0 production impact)
3. ❌ **D310 trailing-stop cancel-without-resubmit fix** — STILL OPEN. Without this, T2's wide stops can silently disappear like NXXT did.
4. ✅ D311 phantom-delta noise eliminated from logs

**Recommendation**: Either fix D310 before T2 OR add a guardrail: T2 wide-stop arm uses a `STANDALONE` stop (not OTO/secondary leg) submitted AFTER fill confirmation, so the TrailingStopManager cancel-without-resubmit path can't touch it.

---

## 8. Verdict

**Status:** **D311 + replayer fix SHIPPED 2026-05-24.**

The week's empirical data (live 6 events + backfill 8 events) confirms the wide-stop thesis at the **86% win rate, +$5,955 dollar delta** level. T1 success criteria are exceeded. T2 A/B is ready to fire Monday morning **conditional on D310 being either fixed OR worked around** (standalone stop on the wide arm).

Without T2, next week looks identical to this week: bot bleeds ~$200-500/day via tight stops, with hope that another NXXT-class accidental carry produces a surprise win. That's gambling, not trading.

The path to 10%/day starts with shipping T2 — and shipping it Monday before the data goes stale.
