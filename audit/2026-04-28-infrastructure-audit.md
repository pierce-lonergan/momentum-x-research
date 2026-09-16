# Infrastructure Audit — 2026-04-28 (post-session)

**Status:** PHASE 0 deliverable per the discipline framework. NO PRODUCTION CODE was changed during this audit.
**Scope:** trace every entry/exit codepath, audit mx-arena parity, root-cause Bug AI's no-fire on LIDR, map OTO bracket coverage, reconstruct today's complete trade tape.
**Author:** Claude (under operator direction, post-session diagnostic)

---

## §0 — Executive Summary

Today's trading produced **3 new entries, 0 wins, -$372 realized on new trades.** The +$785 headline P&L came entirely from a +$1,000 LIDR overnight carry-forward bounce. **Today was a losing day on system intelligence.** The infrastructure shipped over the last 36 hours enabled more trades; the trades themselves lost money.

**Highest-severity findings:**

1. **Bug AI cancel-and-coordinate did NOT fire on LIDR exit** (10:00:38–10:00:43 ET). D245/D246/D247 escalation fired instead. Root cause TBD (Phase 0.3 has hypotheses + grep results below; needs deeper trace tomorrow before P0 fix).

2. **D231 RECON_HARD_BLOCK STOP fired on every RESCAN entry today** (SBLX, ATER, SEGG). The internal `stop_order_id` field stored values that did NOT correspond to active stops at the broker. Investigation reveals the stored IDs are actually the BAR-1 EXIT sell-order IDs — meaning the position's stop_order_id field was OVERWRITTEN with the close order ID when BAR-1 EXIT fired. **This is a NEW Bug AQ-class finding** (tracker stop_oid corruption) separate from yesterday's Bug AM (auto-attach).

3. **The mystery `6181f681` LIDR sell at 10:00:39** is a market-sell order that succeeded after Bug Z's DELETE-positions failed with 403. Identity unknown — could be operator manual sell, could be a fallback codepath I haven't traced. **Unattributed live behavior is more dangerous than a known bug**; this MUST be identified before tomorrow's open.

4. **Today's 3 RESCAN entries all hit local tops.** SBLX -2.6% in 36s, ATER -2.3% in 35s, SEGG slippage -172 bps. n=3 is not statistically meaningful but the pattern matches the engineering hypothesis that there's no pre-entry momentum confirmation.

5. **0 of 3 captured DecisionRows produced a profitable outcome.** Bug AO replay infrastructure was successful in CAPTURING (verified live), but the corpus shows the Bug AK-enabled trades were all unprofitable.

---

## §0.1 — Codepath Map

### Entry codepaths

#### FAST_PATH (`src/execution/fast_path.py` + `main.py:5XXX`)

- **Trigger condition:** Phase 1.5 (09:20 ET) — D85 fast-path scoring on premarket scan top-5 candidates with deterministic-only signals (no LLM blocks open). Threshold: `D85: enabled=True, threshold=0.35, max=3, sizing=8.0%`.
- **Submission shape:** OTO bracket — single Alpaca submit with `order_class="oto"` containing parent (limit buy) + stop_loss leg + take_profit leg. Source: `src/execution/alpaca_executor.py:272-280`.
- **Child orders:** stop @ `verdict.stop_loss` (typically entry × 0.945 or ATR-derived); take-profit @ `verdict.target_prices[0]` (entry × 1.05 default).
- **Error handling:** if OTO submit raises, the helper catches and returns None → executor logs failure. Tracker NOT mutated.
- **Log markers:** `D85 SUBMITTED`, `D85: Registered fast-path position`, `D215 EXECUTION RECORDED ... path=FAST_PATH`.
- **Yesterday's evidence (OGN 09:30:30 ET):** `D85 SUBMITTED: OGN | qty=999 | entry=$11.25 | stop=$10.63 | oid=cd07bf9c-c01` — confirms OTO with stop child.

#### RESCAN path (`main.py:6498-6800` — Phase 2/3 RVOL re-evaluation loop)

- **Trigger condition:** during Phase 2 / 3, candidates that newly pop above re-eval threshold (D124 verdict=BUY after rescan). Each cycle, the rescan helper iterates `verdicts` from orchestrator.
- **Submission shape:** plain `submit_oto_order` call via `executor.execute(verdict)` → BUT `target_prices` array on the verdict is empty in the RESCAN path (need to verify; the executor only adds OTO if order_class is set and target is present).
- **Post-fill protection: `cancel_stop_and_submit_exit_ladder`** (`src/execution/exit_ladder.py`). Steps: cancel any old stop → submit limit tranches at target_prices[0..2] → submit residual GTC stop @ verdict.stop_loss. Result: tracker.stop_order_id ← the new GTC stop oid.
- **Log markers:** `RESCAN ORDER`, `D215 EXECUTION RECORDED ... path=RESCAN`, `RESCAN exit-ladder` (helper internal logs).
- **Today's evidence (SBLX 10:49:05 ET):** `RESCAN ORDER: SBLX qty=3490 @ $3.03` — but NO follow-up `RESCAN exit-ladder` log line found in today's log via grep. **Possible cause: the exit_ladder helper failed silently OR was never called.** Investigation deferred to Phase 0.4.

#### Other entry codepaths

- **Fast-path `cmd_paper`** at line 1355 has another `action="BUY"` site — appears to be reservation/registration, not entry submission.
- **`bridge.execute_verdict`** can be called for one-off operator-driven entries; not exercised today per logs.

### Exit codepaths

#### D146 BAR-1 EXIT (`src/execution/bar1_exit.py` + main.py)

- **Trigger condition:** T+60s post-entry, sell 100% of position via `client.close_position(ticker)` (DELETE /v2/positions/{symbol}). Cameron-style discipline.
- **Today's firings:** LIDR @ 10:00:38, 10:01:46 (carry); SEGG @ 10:33:40 (success); SBLX @ 10:50:31, 10:51:32, 10:52:36 (multiple retries, all fail with D231 mismatch); ATER similar pattern.
- **Log markers:** `D146 BAR-1 EXIT: TICKER — selling N/N shares (100%) at T+Xs`.

#### D245/D246/D247 escalation chain (`src/execution/bridge.py:attempt_close_with_status_check`)

- **D245** SMART_EXIT_REJECTED — close attempt returned non-2xx
- **D246** SMART_EXIT_RETRY — backoff + retry up to N attempts
- **D247** SMART_EXIT_ESCALATE — exhausted retries; operator intervention required
- **Today (LIDR 10:00:38–43):** D245 (×3) → D247. The Bug AI cancel-and-coordinate path was NOT entered. See §0.3.

#### Bug AI cancel-and-coordinate (`src/execution/bridge.py:_cancel_blocking_sell_orders`)

- **Trigger condition:** if `cancel_blocking_stops_first=True` AND error string contains "insufficient qty available" or "40310000" or "available: 0".
- **Today:** NEVER FIRED despite the LIDR scenario being the canonical Bug AI use case.
- See §0.3 for forensics.

#### The mystery `6181f681` LIDR market sell (10:00:39 ET)

- Order details (from broker tape):
  - id: `6181f681-...`
  - submitted_at: `2026-04-28T14:00:39 UTC` = 10:00:39 ET
  - side: sell, qty: 5264, type: market, status: filled at $2.36
  - 9 partial_fill activities + 1 final fill in FILL log
- **NOT in any source-code grep for D215 EXECUTION RECORDED** (only the bridge SMART_EXIT_REJECTED logs reference LIDR around this time).
- **Hypotheses for source of this order:**
  1. **Operator manual sell** via Alpaca console (Pierce mentioned "I'll manually sell")
  2. **Phase 4 / EOD `_close_overnight_position` helper** — but this would be at 16:00, not 10:00
  3. **A separate code path** that submits a market sell via POST /v2/orders (which respects qty even when stops are holding shares) instead of DELETE /v2/positions
- **Pierce: please confirm whether you manually closed LIDR at 10:00:38 ET via Alpaca console.** If yes, mystery solved + Bug AI fix becomes urgent. If no, there's an undocumented codepath.

---

## §0.2 — mx-arena Parity Audit

| Capability | Production | mx-arena | Verdict |
|---|---|---|---|
| Live Alpaca fill prices | Real broker | Stored fixture replay | **Modeled** (intended; arena is offline by design) |
| Slippage modeling | Real (today's SEGG -172 bps) | Synthetic clamp ±50% per R3 in PBT harness | **Missing fidelity** (arena's slippage model doesn't match today's distribution) |
| OTO bracket child rejection | Real (broker may reject) | SimpleBroker oracle: always succeeds | **Missing** |
| Fill races (parent + children settle in different orders) | Real | Not modeled in PBT or arena | **Missing** |
| Partial fills | Real (today's SEGG: 4 partial fills + 1 final) | r2_partial_fill rule in PBT only | **Modeled** in PBT, **Missing** in arena |
| Timezone-edge fill timing | Real (D108 morning bug class) | Some PBT coverage | **Modeled** |
| 403 / "insufficient qty" Alpaca responses | Real (today's LIDR) | NOT in any fixture | **Missing** — blocker for Bug AI fix verification |
| Bug AO decision-replay corpus → arena | Schema exists, hook live | NOT yet wired into arena replay | **Missing** |
| Today's 7-trade tape replay capability | N/A | Arena cannot reproduce today's fills byte-for-byte | **Missing** |

**Honest assessment:** the arena is currently **NOT a faithful replay engine for live execution.** It models structural properties (PBT invariants, oracle correctness) but does not model fill realism (slippage tails, broker rejections, partial fills, or the 403/qty-blocked scenarios that bit us today). Any backtest claim grounded purely in arena results carries the caveat that **arena ≠ Alpaca.** This is the foundation of all downstream evaluation work.

---

## §0.3 — Bug AI Wire-in Forensics

**Today's actual log around 10:00:38–10:00:43 ET (LIDR exit attempt):**

```
10:00:38 D146 BAR-1 EXIT: LIDR — selling 5264/5264 shares (100%) at T+64839s
10:00:40 D245 SMART_EXIT_REJECTED LIDR (attempt 1/3): Client error '403 Forbidden' for url '/v2/positions/LIDR'
10:00:40 D246 SMART_EXIT_RETRY LIDR: retrying after backoff (attempt 1 failed; will retry 2 more time(s))
10:00:41 D245 SMART_EXIT_REJECTED LIDR (attempt 2/3): Client error '403 Forbidden' for url '/v2/positions/LIDR'
10:00:41 D246 SMART_EXIT_RETRY LIDR: retrying after backoff (attempt 2 failed; will retry 1 more time(s))
10:00:43 D245 SMART_EXIT_REJECTED LIDR (attempt 3/3): Client error '403 Forbidden' for url '/v2/positions/LIDR'
10:00:43 D247 SMART_EXIT_ESCALATE LIDR: 3 retries exhausted; broker close FAILED. Position remains open at broker; tracker MUST NOT be mutated. Last error: Client error '403 Forbidden'
10:00:43 D247 SMART_EXIT_ESCALATE LIDR: protective stops MUST NOT be cancelled. OPERATOR INTERVENTION REQUIRED.
```

**Critical observation:** the error string logged is just `Client error '403 Forbidden' for url '/v2/positions/LIDR'`. The Alpaca 403 BODY (which includes `available: 0` and `insufficient qty available`) is NOT in the log — likely because the bridge's exception handling captured `httpx.HTTPStatusError.__str__()` which only includes the URL summary, not the response body.

**Bug AI's trigger logic (`src/execution/bridge.py:attempt_close_with_status_check`):**

```python
is_qty_blocked = (
    cancel_blocking_stops_first
    and not cancelled_snapshots
    and ("insufficient qty available" in err_str
         or "40310000" in err_str
         or "available: 0" in err_str)
)
```

**Root cause (hypothesis ranked):**

1. **(a) Error string mismatch — HIGHEST CONFIDENCE.** The error string `err_str` is `Client error '403 Forbidden' for url '...'`. None of the three trigger substrings (`"insufficient qty available"`, `"40310000"`, `"available: 0"`) match because the Alpaca BODY isn't in the exception string. The bridge needs to extract `e.response.text` or `e.response.json()` to access the body content for matching.

2. **(b) Wire-in conditional — LOW CONFIDENCE.** main.py:5699 passes `cancel_blocking_stops_first=True` for the SMART_EXIT site. Verified via grep: this site IS the one that fires for D146 BAR-1 EXIT.

3. **(c) Different call site — LOW CONFIDENCE.** No second SMART_EXIT call site found.

**Recommended fix (Phase 3.1):**

```python
# In attempt_close_with_status_check exception handler:
err_body = ""
if hasattr(e, "response") and e.response is not None:
    try:
        err_body = e.response.text
    except Exception:
        pass
err_str = f"{type(e).__name__}: {e}; body={err_body}"
```

Then the existing trigger logic will work because the body containing `"available": "0"` will be in `err_str`.

**Test plan (Phase 3.1):** capture the exact 403 body Alpaca returned today as a fixture; replay through arena; confirm the new code triggers cancel_blocking_stops_first.

---

## §0.4 — OTO Bracket Coverage Map

| Entry path | OTO at submit? | Stop child? | TP child? | Source line | Today's evidence |
|---|---|---|---|---|---|
| **FAST_PATH** | ✅ Yes | ✅ Yes | ✅ Yes | `alpaca_executor.py:272-280` | OGN yesterday confirmed |
| **RESCAN path** | ❌ No (plain limit) | ⚠️ Submitted post-fill via `exit_ladder` helper | ⚠️ Submitted post-fill (limit tranches) | `main.py:6627`, `exit_ladder.py` | SBLX/ATER/SEGG today: D231 fired = stops submitted but tracker confused |

**Key finding:** RESCAN does have post-fill protection — but the post-fill mechanism is fragile. Today, every RESCAN entry triggered D231 RECON_HARD_BLOCK STOP within ~30s of fill, indicating the stop_order_id stored in the tracker did NOT match an active stop at the broker.

**Investigation:** the stored stop_order_ids (`b9703115`, `f755a657`, `9f5c76a9`) appear in today's broker FILL log as the SELL orders that closed the positions (BAR-1 EXIT). This suggests **the tracker's `stop_order_id` field was overwritten with the close-order ID when BAR-1 EXIT fired**, or the exit_ladder helper assigned the close order id as the stop_order_id incorrectly.

**Risk quantification:** if exit_ladder fails to submit a proper stop AND BAR-1 EXIT also fails (today's LIDR scenario), the position is unbounded. Today's RESCAN entries had max-loss exposure of ~$10.5K each (SBLX $3.03 × 3490 = $10.6K notional). At 100% drop, max loss = $10.6K per position vs the realized -$279.

**Phase 3.2 recommendation:** verify the exit_ladder helper actually submits stops AND that the tracker stores the stop oid correctly (not the close-order oid). If the exit_ladder mechanism is fundamentally broken, fall back to FAST_PATH-style OTO submission for RESCAN too.

---

## §0.5 — Today's Tape Reconstruction

**Sources:** `/v2/orders` + `/v2/account/activities/FILL` + `logs/momentum_2026-04-28.log` execution_recorder records.

| # | Time (ET) | Sym | Side | Qty | Type | Status | Fill price | Intent | Slippage | Hold | Realized P&L | Exit codepath |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 10:00:39 | LIDR | sell | 5264 | market | filled | $2.36 | (carry, no intent) | n/a | overnight | **+$1,000** vs yesterday's mark | `6181f681` (mystery — TBD) |
| 2 | 10:32:36 | SEGG | buy | 9281 | limit | filled | $1.12 (4 partial fills) | $1.14 | **-172 bps** | n/a | (entry) | RESCAN |
| 3 | 10:33:41 | SEGG | sell | 9281 | market | filled | $1.12 (1 fill) | (BAR-1 exit) | 0 bps | 65s | **$0.00** | D146 BAR-1 EXIT |
| 4 | 10:49:03 | SBLX | buy | 3490 | limit | filled | $3.03 (3 partial fills) | $3.03 | **-1 bp** | n/a | (entry) | RESCAN |
| 5 | 10:49:06 | ATER | buy | 3122 | limit | filled | $1.29 (3 partial fills) | $1.29 | **0 bps** | n/a | (entry) | RESCAN |
| 6 | 10:49:39 | SBLX | sell | 3490 | (close_position) | filled | $2.95 | (BAR-1 exit) | -260 bps vs entry | 36s | **-$279.20** | D146 BAR-1 EXIT (oid `b9703115`) |
| 7 | 10:49:42 | ATER | sell | 3122 | (close_position) | filled | $1.26 | (BAR-1 exit) | -230 bps vs entry | 35s | **-$93.66** | D146 BAR-1 EXIT (oid `f755a657`) |

**Cancellations (limit not hit):**
- ATER 3075 @ 10:32:10 (re-tried successfully at 10:49:06 with qty=3122)
- SNBR 1174 @ 10:32:25 (canceled, NEVER refilled)
- NEXR 4596 @ 10:49:08 (canceled)
- SNBR 1422 @ 10:49:20 (canceled)

**Reconciliation to daytrade_count=14:**
- SEGG round trip (entry+exit) = 2
- SBLX round trip = 2
- ATER round trip = 2
- LIDR exit (entry was yesterday) = 1
- Plus partial fills counted separately by Alpaca's PDT rule? Possibly. 6+ partials counted separately could explain the gap to 14.

**Per-trade alpha capture (back-of-envelope):**

| Trade | Theoretical alpha (intent → exit if no slip) | Slippage cost | Net captured |
|---|---|---|---|
| SEGG | (1.14 → 1.12)×9281 = -$185 (signal already wrong) | -172 bps entry × $10.4K = -$179 | **$0** (came out flat by luck of timing) |
| SBLX | (3.03 → 2.95)×3490 = -$279 | -1 bp + (exit not slipped vs intent — exit IS at market) | **-$279** (no slippage benefit, just bad timing) |
| ATER | (1.29 → 1.26)×3122 = -$93 | 0 bps + market exit | **-$93** |

**P&L decomposition for today:**
- Alpha (intent quality): negative (-$185 SEGG + -$279 SBLX + -$93 ATER = -$557 total alpha lost on signals that turned against us)
- Slippage cost: SEGG -$179 of the -$185 alpha already accounted in the -$179 entry slip
- Carry: +$1,000 (LIDR, completely unrelated to today's signals)
- Luck: SEGG's flat exit (came out at exactly entry price) vs the -$185 alpha = +$185 luck

**Net: -$372 on system intelligence + $1,000 carry + small noise = +$785 reported.**

---

## Phase 0 conclusions

1. **Three NEW production-code issues surfaced** beyond yesterday's known list:
   - **Bug AR candidate**: Bug AI doesn't fire because error string lacks the Alpaca body (§0.3)
   - **Bug AS candidate**: RESCAN tracker.stop_order_id corruption — stored value is the close-order ID, not the stop's ID (§0.4)
   - **Mystery `6181f681`**: undocumented codepath (or operator action) that succeeded where DELETE positions failed (§0.1)

2. **Arena is NOT a faithful replay engine** for live execution. Backtests against arena should be treated as estimates with significant uncertainty bounds (§0.2).

3. **Today's net was +$785 but new-trade alpha was -$372.** The system has not yet demonstrated profitability on its own intelligence (§0.5).

4. **The infrastructure shipped over the last 36 hours WORKS** (Bug AG/AH/AM/AN/AP all verified live today). What's missing is the trading-discipline layer: pre-entry confirmation, slippage feedback, OTO bracket reliability.

**Phase 0 deliverable: this document. NO production code changes were made during Phase 0.**

**Gate to Phase 1:** ✅ committed.
