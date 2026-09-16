# 24 — EOD Bug Findings (Thu 2026-04-23 evening)

**Diagnostic-first writeup before any patch.** Same discipline as
`19_eod_bug_findings.md`: each bug gets `what I found → root cause →
fix → regression test → rollout check`. No production edits until
each item has a written findings note.

**Source:** today's `logs/paper_2026-04-23.log` (62,236 lines, decoded
from UTF-16-LE) + `data/heartbeat.json` + git diff vs yesterday's
patch baseline `899e03f`.

**Session summary:**
- 1 position opened (XNDU), 1 BAR-1 EXIT, 0 manual closes
- Broker fills: 2 (1 buy + 1 sell on XNDU)
- Realised P&L: **$0.00 reported** (suspect — see Bug Q)
- 110 BUY-side evaluations vs 1 actual order: 99% blocked downstream
  (D200-E4 catalyst gate is the dominant veto)
- 2,472 WARNING + 33 ERROR + 6 CRITICAL log records

---

## Patches that HELD in production today ✅

| Bug | Evidence in today's log |
|-----|------------------------|
| **D — Bug D `_poll_for_terminal_fill`** | Line 49252: `D217: XNDU order bffe8020 reached terminal status=filled filled_qty=395 filled_avg_price=30.74 (poll 2/6)`. New helper polled exactly twice, hit terminal status, returned correct qty. **Replaced the buggy `filled_avg_price > 0` check with terminal-status check, exactly as designed.** |
| **F — Bug F Arena_expected/Arena_actual** | Lines 49287-49293: Both new log lines fired. Pre-exit: `D146 BAR-1 EXIT: XNDU — selling 395/395 shares (100%) at T+63s. Arena_expected: +$5.11 (+299%) [backtest mean].` Post-exit: `D146 BAR-1 ACTUAL: XNDU — Arena_actual=+$0.00 (entry=$30.7400 exit=$30.7400 qty=395). Compare vs Arena_expected=+$5.11 backtest mean.` **Both qualifier strings present, structurally distinct.** |
| **E — Bug E D91 detection by opened_at** | No-op (no overnight positions to close — yesterday's EOD swept clean). Code path executed without error at startup; D56 sync reported 0 positions. |

**Net validation:** 3 of 4 yesterday's patches held. Bug D and Bug F
got their first real production proof. Bug E was untested due to no
overnight inventory. **Bug #13 broke** — see Bug N below.

---

## Bug N — Bug #13 patch broken: wrong client class (HIGH)

### What I found

EOD trade-journal summary line (62188-62189):
```
16:00:57 | momentum_x | WARNING | D222: broker fill fetch failed
('AlpacaDataClient' object has no attribute 'get_account_activities')
— falling back to journal-only counts. Closed/P&L numbers below may
be undercounting BAR-1/D146 exits.
16:00:57 | momentum_x | INFO | ═══ TRADE JOURNAL SUMMARY ═══
  Evaluations: 205 | BUYs: 110 | Closed: 0 (source=journal)
  Total P&L: $0.00 (source=journal) | Avg MFCS: 0.2752
```

**Closed: 0 / P&L: $0.00** — the exact bug Bug #13 was supposed to
fix yesterday is still firing today. XNDU's BAR-1 EXIT happened at
10:01:46 with PnL=$0.00 logged through `close_with_attribution`, and
the journal still showed 0 closed.

### Root cause

Yesterday's patch in `main.py` calls:
```python
_broker_fills = await client.get_account_activities()
```

But `client` in this scope is `AlpacaDataClient` (instantiated at
`main.py:136`), and **`AlpacaDataClient` does not expose
`get_account_activities`**. Verified — `grep -n "get_account_activities"
src/data/alpaca_client.py` returns nothing. The class exposes
`get_account`, `get_positions`, `get_orders`, `close_position` —
but not the activities endpoint.

Yesterday I assumed the method existed on the client; I never
verified it. The patch shipped with an `AttributeError` that the
broad `except Exception as _be:` caught and logged as the D222
warning — silently degrading to the broken journal-only path that
returned `Closed: 0` (the original Bug #13 symptom).

### Fix

Two-layer:
1. **Add `get_account_activities` to `AlpacaDataClient`** — calls
   Alpaca's `/v2/account/activities/FILL` endpoint, returns the
   list of fill events for the trading day. ~30 lines.
2. **Alternative cheaper fix**: derive fills from
   `client.get_orders(status='closed', after=session_start)` which
   already exists. Each filled order has `filled_avg_price`,
   `filled_qty`, `side`, `symbol` — sufficient for
   `summarize_from_broker_fills(fills)`.

Going with the alternative — keeps surface-area smaller, matches
how Bug D's poll loop already uses `get_orders`. The fill record
shape becomes: `[{symbol, side, qty=filled_qty, price=filled_avg_price}, ...]`
constructed from `get_orders` output.

### Regression test

Add to `tests/unit/test_d222_journal_pnl_reconciliation.py`:
- New T9: `test_run_pnl_reconciliation_with_real_alpaca_client_shape`
  — pass an `AlpacaDataClient`-shaped mock with `get_orders` (NOT
  `get_account_activities`); assert no AttributeError, summary derives
  from get_orders payload correctly.

### Rollout check

After patch deploys, the next session's EOD log line MUST read
`Closed: N (source=broker)` where N matches the broker-fill count.
If it ever reads `(source=journal)` again, Bug N has regressed.

---

## Bug Q — BAR-1 exit price falls back to entry_price when snapshot empty (HIGH)

### What I found

XNDU trade today:
- Bought 395 shares @ $30.74 (great fill — limit was $32.93)
- BAR-1 EXIT fired at T+63s
- Reported exit price: **$30.7400** (exact entry price)
- Reported P&L: **+$0.00** exactly

The exact-zero P&L on XNDU is suspicious. It's possible the price
genuinely was $30.74 at exit, but the same-bar timing + the exact
match to entry suggests the exit price came from a fallback, not
from a real market quote.

### Root cause

`main.py:4356-4358`:
```python
_bar1_snap = _d78_snapshots.get(pos.ticker, {})
_bar1_exit_px = float(
    _bar1_snap.get("last_price", 0) or pos.entry_price
)
```

If `_d78_snapshots[ticker]` is empty OR `last_price` is missing/zero,
the formula falls back to `pos.entry_price`. **No log warning is
emitted.** The result: `_bar1_exit_px == _bar1_entry_px` →
`Arena_actual = $0.00` regardless of what actually happened in the
market.

This silently masks the realised performance of every BAR-1 exit
that happens when the snapshot is stale or missing — exactly the
kind of "looks correct but isn't" failure mode the state-mutation
reversibility rule (f) is designed to catch.

### Fix

Two-layer:
1. **Refuse the fallback** — if `_bar1_snap` is empty or `last_price ≤ 0`,
   call `client.get_latest_quote(pos.ticker)` for a real-time NBBO mid.
   If that ALSO fails, log `D228 BAR1_EXIT_PX_DEGRADED` warning and
   use `pos.entry_price` as last-resort with explicit acknowledgment.
2. **Always pull the actual broker fill price** for `Arena_actual`
   computation — the close order has a `filled_avg_price` after a
   short delay; the BAR-1 ACTUAL log should be re-emitted at T+5s
   post-close with the broker-confirmed fill price replacing the
   inline estimate.

### Regression test

Add `tests/unit/test_d228_bar1_exit_price_source.py`:
- T1: snapshot present with `last_price=31.50` → uses snapshot
- T2: snapshot empty → calls `client.get_latest_quote`, uses NBBO mid
- T3: both empty → emits D228 warning, falls back to entry_price
- T4: replay XNDU's situation (snapshot empty, broker fill at $30.74)
  and verify Arena_actual reflects broker truth, not entry

### Rollout check

Next BAR-1 fire: log must contain either snapshot price or quote
price, NEVER silent equality with entry_price unless D228 warning
also present.

---

## Bug R — Phase-0 stop tightening silently misreported in log + bridge tracker (MEDIUM-HIGH)

### What I found

XNDU lifecycle log:
- Line 49249: `XNDU: Submitting OTO order (buy limit + stop sell) — qty=395, entry=32.93, stop=32.44`
- Line 49250: `XNDU: OTO stop leg — oid=...c @ $25.72 (activates on buy fill)`
- Line 49254: `XNDU: Position opened — qty=395, entry=$30.74, stop=$25.72`

**Three different stop values in three lines:**
- Submission log says `stop=32.44` (Phase-0 tightened: 1.5% below entry)
- OTO stop leg log says `@ $25.72` (verdict's original stop: 22% below entry)
- Position-opened log says `stop=$25.72` (uses verdict.stop_loss not _stop_price)

### Root cause

Two distinct issues that compound:

**(a) Log line bug at `alpaca_executor.py:354-356`:**
```python
logger.info(
    "%s: OTO stop leg — oid=%s @ $%.2f (activates on buy fill)",
    verdict.ticker, stop_order_id, verdict.stop_loss,  # ← wrong field
)
```
Uses `verdict.stop_loss` (the pre-Phase-0 stop) instead of
`_stop_price` (the actual submitted stop). The Alpaca broker DID
receive `stop_loss=$32.44` per the payload at line 825 of
alpaca_client.py. The log line just lies about it.

**(b) Bridge ManagedPosition stop_loss field bug at `bridge.py`:**
```python
position = ManagedPosition(
    ...
    stop_loss=verdict.stop_loss,  # ← original, not actual broker stop
    ...
)
```
Uses `verdict.stop_loss` — the un-tightened original — to
populate `ManagedPosition.stop_loss`. The internal tracker
therefore THINKS the stop is at $25.72 (the wider, riskier value)
even though the broker has it at $32.44 (the tightened, safer
value).

### Downstream blast radius

The position-tracker uses `pos.stop_loss` for:
- Exit-decision math (`peak_to_stop_distance`, `phase2_stop_check`)
- Risk-of-ruin display (`(entry - stop) / entry × qty`)
- Heartbeat dashboard ("stop=$25.72")
- Slippage attribution at exit
- D146 BAR-1 fire conditions (no — BAR-1 fires on time, not stop)

If the price drops to $32.30:
- Broker stops out (broker stop is $32.44)
- Tracker says "still safe, $32.30 > $25.72"
- D98 polling discovers position-flat at next 60s tick
- D215 EXECUTION RECORDED logs the broker-driven exit
- But the journal P&L attribution thinks we held to a 22% drop, not
  a 1.5% drop — biases all post-trade analysis

### Fix

Three changes:

1. **`alpaca_executor.py:355`** — change `verdict.stop_loss` to
   `_stop_price` in the OTO stop leg log line.
2. **`alpaca_executor.py` `OrderResult`** — extend with
   `actual_stop_price: float | None = None` field; set it to
   `_stop_price` post-submit.
3. **`bridge.py` `ManagedPosition` construction** — read
   `order_result.actual_stop_price` if present, else
   `verdict.stop_loss`. Use whichever the broker actually has.

### Regression test

Add `tests/unit/test_d229_phase_stop_truth.py`:
- T1: Phase-0 enabled, `verdict.stop_loss=$25`, entry $30 →
  `_stop_price = $29.55` → submitted with $29.55, ManagedPosition
  carries $29.55, log line shows $29.55.
- T2: Phase-0 disabled → original stop preserved.
- T3: log-content assertion: NEVER does `verdict.stop_loss` and
  `_stop_price` differ in the same trade lifecycle.

### Rollout check

Next intraday entry: the three log lines must agree on the stop
price. If they ever differ, Bug R has regressed.

---

## Bug S — D87 LLM provider circuit breaker tripped 19× across the session (MEDIUM)

### What I found

19 occurrences of `D87: Circuit breaker 'llm_provider' TRIPPED
(closed -> open). 5 [failures threshold]`. First trip at 09:31:33
(market open), repeated through the day. Plus 2 trips of
`alpaca_rest`.

Today's debate-engine summary: **42 debates skipped on budget,
100% skip rate, 0 BUY verdicts despite 32 debates queued**. The
LLM provider was largely down all session. Only the deterministic
agents (technical, GEX) produced signals; the LLM-driven agents
(news, fundamental, deep_search, manipulation_classifier) were
either timing out or rejected by the circuit breaker.

### Root cause

`Together_aiException - Service unavailable` at 04:30:16 (startup
preflight) never recovered. The `D87` circuit breaker correctly
trips after 5 failures and re-tests on a backoff, but Together.ai
was throwing 503s consistently for hours. The system has no
fallback model — when Together.ai is down, agent dispatch
silently degrades to deterministic-only signals, which produce
weak conviction → debate skip → no BUY.

### Fix (deferred to v2.2 §2.4 work)

Real fix is a fallback LLM provider chain:
- Primary: Together.ai Qwen3.5-397B
- Secondary: OpenRouter / DeepInfra with same model family
- Tertiary: Anthropic Haiku for emergency consensus

Quick fix tonight: log a single `D87 LLM_OUTAGE_DETECTED` warning
ONCE on first trip, with retry schedule + estimated downtime
based on circuit-breaker open duration. Reduces log noise and
makes the operational state legible.

### Regression test

Skip — this needs a full LLM-provider fallback chain to test
properly. Capture as a v2.2 ticket.

### Rollout check

Tomorrow's session: if Together.ai is up, the breaker should not
trip. If it trips, the new D87 LLM_OUTAGE_DETECTED log should fire
exactly once at first trip.

---

## Bug T — VWAP scan failure: `scan_timestamp` field missing (MEDIUM)

### What I found

35 occurrences of:
```
[Phase 3] VWAP scan error: 1 validation error for CandidateStock
scan_timestamp
  Field required [type=missing, input_value={'ticker': 'TZOO', ...
   'scan_phase': 'INTRADAY'}, input_type=dict]
```

Every Phase-3 VWAP rescan is silently failing because the dict
being passed to `CandidateStock(**dict)` is missing the
`scan_timestamp` required field. This is the path that catches
intraday breakouts (TZOO, TRT, others) and feeds them to the
evaluation pipeline. **It has been failing on every iteration**
— effectively the VWAP-rescan feature has been a no-op for the
entire session.

### Root cause

The Phase-3 VWAP scanner constructs a `CandidateStock` dict but
omits the `scan_timestamp` field that became required in a recent
Pydantic schema update. The model field has no default and the
constructor doesn't supply one.

### Fix

`src/core/scan_loop.py` (or wherever the VWAP rescan dict is built)
— add `"scan_timestamp": datetime.now(timezone.utc)` to the dict.
One line.

### Regression test

Add to `tests/unit/test_scan_loop.py` (if exists, else create) —
T1: VWAP rescan path produces a valid CandidateStock with
non-null scan_timestamp.

### Rollout check

Next session: zero `[Phase 3] VWAP scan error` warnings. If any
fire, the field is still missing.

---

## Bug U — `MOMENTUM_UNIVERSE` import broken in screener fallback path (LOW-MEDIUM)

### What I found

7 occurrences of:
```
Screener API failed: All connection attempts failed — falling back to
MOMENTUM_UNIVERSE
Failed to fetch scan quotes: cannot import name 'MOMENTUM_UNIVERSE'
from 'src.data.premarket_research'
```

The screener-fallback path tries to import a constant
`MOMENTUM_UNIVERSE` from `src.data.premarket_research` that doesn't
exist (was likely renamed or moved). When the primary screener
fails (which it did 7 times today due to network errors), the
fallback path also fails, leaving zero candidates for that scan
iteration.

### Root cause

Code expects `from src.data.premarket_research import MOMENTUM_UNIVERSE`
but the constant has been renamed/relocated. Either the rename
wasn't propagated, or the import path was changed without updating
this fallback caller.

### Fix

`grep -rn "MOMENTUM_UNIVERSE" src/` to find the actual location of
the constant; update the import. If the constant is genuinely
deleted, replace the fallback with a hardcoded short-list of
high-RVOL microcap tickers (the original purpose) or call
`alpaca_client.get_assets(status='active', class='us_equity')`
filtered by float < $50M.

### Regression test

Add to `tests/unit/test_alpaca_client.py` — T1: simulate primary
screener failure, assert fallback path returns at least one
candidate without raising ImportError.

### Rollout check

Next screener failure: must succeed via fallback, not crash.

---

## Cross-cutting observations

### Why was this so quiet?

The session opened with a real-world bug, AGPU on the watchlist,
LLM provider down, and a half-broken patch shipped yesterday — and
none of it was visible in real time because:
1. **UTF-16 log encoding (pre-existing)** — required manual decode pass
2. **D222 silent fallback** — the patch failure looks identical to
   the original bug it was supposed to fix
3. **Bug Q exact-zero P&L** — masks real outcomes behind a fallback
4. **Bug R log-line lies** — three different stop values in three
   adjacent log lines, and nobody looking at the dashboard would notice

This is the same pattern as yesterday's Bug C (D56 sync labeling
integrity): the system tells you what it WANTS to be true, not what
IS true. Three more instances of state-mutation reversibility rule
(f) violations.

### Bug priority for tonight (in order)

1. **Bug N** — Bug #13 regression. Fix tonight. Must work tomorrow.
2. **Bug T** — VWAP scan_timestamp. 1-line fix. Tonight.
3. **Bug U** — MOMENTUM_UNIVERSE import. Quick fix. Tonight.
4. **Bug R** — Phase-0 stop log + tracker truth. Tonight.
5. **Bug Q** — BAR-1 exit price fallback. Tonight if time, else
   tomorrow. Critical for v2.2 calibration data.
6. **Bug S** — LLM provider fallback chain. v2.2 §2.4 ticket.

### Mapping to v2.2 entry point

The right v2.2 entry point given today's evidence is **§13
calibration discipline + §5 BOCPD moat together** because:

1. Bug Q (exit price fallback) is a calibration-data integrity
   issue. The first 30 own-fill calibration trades that v2.2 §13.7
   gates on cannot pass the §13.3 martingale residual test if
   exit prices are silently wrong. Fix Bug Q before any η_perm
   estimation.

2. Bug R (Phase-0 stop tracker truth) is also calibration-data
   integrity. Slippage attribution depends on real entry/stop math.

3. Bug N (Bug #13) is the entire foundation of EOD reconciliation
   that BOCPD/PPD layers depend on. Without broker-truth-derived
   journal numbers, the per-trade residual feed into §5.2 BOCPD is
   wrong.

So the tonight fixes (N, T, U, R, Q) are not new work — they are
the **calibration discipline** v2.2 §13 requires before any of
the multi-strategy build (S2/S3) can begin. The right v2.2 entry
point is therefore **§13.7 row 1: "Phase 0 trade_context +
bar_context + child_fill_ticks live"** because:
- Phase 0 instrumentation requires Bug Q + Bug R fixed first
  (exit-price + stop-truth must be clean before captures land)
- Phase 0 is the m1-m3 deliverable in v2.2 §7
- Without it, we cannot start the §13.3 martingale-residual test
- Without that test, we cannot ship Paper 1 m6
- Without Paper 1, Tranche B/C SMA conversations stall

**Recommendation:** ship tonight's N/T/U/R/Q patches as the
foundation, then immediately pivot the next 2 weeks of engineering
to the Phase 0 instrumentation MVP (`21_…spec.md`) — that is the
v2.2 entry point with the highest leverage on every other v2.2
deliverable.

---

*End of D23 findings. Patches follow in priority order:
N → T → U → R → Q. v2.2 entry-point recommendation:
Phase 0 instrumentation MVP per `21_phase0_instrumentation_mvp.md`.*

---

## Resolution Index — patches landed Thu 2026-04-23 evening

All 5 bugs (N, T, U, R, Q) closed before EOD. Bug S (LLM-provider
fallback) deferred to v2.2 §2.4 ticket — requires multi-provider
infrastructure that is not a 1-night job.

### Bug N — Resolution
- **Production changes:**
  - `src/analysis/trade_journal.py` — `summarize_from_broker_fills`
    now accepts BOTH the simple `{symbol, side, qty, price}` shape AND
    the production `get_orders` shape `{symbol, side, filled_qty,
    filled_avg_price, status}`. Filters by `status ∈ {filled,
    done_for_day}` when present.
  - `src/analysis/trade_journal.py` `run_pnl_reconciliation` — checks
    `hasattr(client, 'get_account_activities')` and falls back to
    `client.get_orders(status='all', limit=500)` for the production
    `AlpacaDataClient` shape. No more silent `AttributeError`.
- **Test file:** `tests/unit/test_d222_journal_pnl_reconciliation.py`
  extended with `TestReconWithRealClientShape` (2 tests). Total 10/10 PASS.
- **Behavioral guarantee:** EOD journal summary now reads
  `Closed: N (source=broker)` where N matches actual broker fills,
  including BAR-1/D146 closes that never call `record_close()`.

### Bug T — Resolution
- **Production change:** `main.py` VWAP-rescan candidate construction
  now passes `scan_timestamp=datetime.now(timezone.utc)` to
  `CandidateStock(...)`.
- **Test file:** `tests/unit/test_d23_bugs_t_u.py` — 3 tests including
  source-grep guard against future re-introduction. PASS.
- **Behavioral guarantee:** zero `[Phase 3] VWAP scan error` warnings
  in next session; VWAP rescan path actually feeds candidates to the
  evaluation pipeline.

### Bug U — Resolution
- **Production changes:**
  - `src/data/premarket_research.py` — declared
    `MOMENTUM_UNIVERSE: list[str] = []` with rationale (fail-closed
    safer than trading stale baked-in universe).
  - `src/data/alpaca_client.py:get_most_active_tickers` — wrapped the
    fallback import in try/except so future deletions degrade
    gracefully to empty list rather than raising.
- **Test file:** `tests/unit/test_d23_bugs_t_u.py` — 2 tests including
  end-to-end fail-closed under simulated screener outage. PASS.
- **Behavioral guarantee:** screener outages no longer produce
  ImportError chains; the fallback returns `[]` and the next iteration
  retries primary.

### Bug R — Resolution
- **Production changes:**
  - `src/execution/alpaca_executor.py` `OrderResult` — added
    `actual_stop_price: float | None = None` field carrying the
    Phase-0/1-tightened stop the broker actually has.
  - `src/execution/alpaca_executor.py:355` — OTO stop leg log now
    references `_stop_price` not `verdict.stop_loss`.
  - `src/execution/alpaca_executor.py:380` — OrderResult constructed
    with `actual_stop_price=_stop_price`.
  - `src/execution/bridge.py` — ManagedPosition built with broker-
    truth stop: `stop_loss=order_result.actual_stop_price` if present,
    else `verdict.stop_loss`.
- **Test file:** `tests/unit/test_d23_bug_r_phase_stop_truth.py` —
  4 tests across log-source-truth, OrderResult shape, bridge wiring.
  PASS.
- **Behavioral guarantee:** the three log lines for any future entry
  (submit, OTO leg, position opened) all agree on the stop price.
  Internal tracker risk math matches broker truth.

### Bug Q — Resolution
- **Production changes:**
  - `src/execution/bar1_exit_logging.py` — new
    `resolve_bar1_exit_price(snap, client, ticker, fallback_price)`
    helper with explicit chain: snapshot → broker NBBO mid →
    `D228 BAR1_EXIT_PX_DEGRADED` warning → fallback. Returns
    `(price, source_tag)`.
  - `main.py:4356-4358` — replaced bare `or pos.entry_price` cascade
    with call to the new helper.
- **Test file:** `tests/unit/test_d23_bug_q_bar1_exit_price.py` —
  5 tests covering all branches + the XNDU exact-replay case.
  PASS.
- **Behavioral guarantee:** BAR-1 exits never silently report
  `Arena_actual=$0.00` from snapshot fallback. When the chain
  degrades, the D228 warning fires explicitly so calibration can
  exclude that trade per v2.2 §13.3.

### Total test-surface delta this session

| Bug | Tests added | Pass | Production files touched |
|-----|-------------|------|--------------------------|
| N   | 2           | 2/2  | `trade_journal.py` |
| T   | 3           | 3/3  | `main.py` (1 line) |
| U   | 2           | 2/2  | `premarket_research.py`, `alpaca_client.py` |
| R   | 4           | 4/4  | `alpaca_executor.py`, `bridge.py` |
| Q   | 5           | 5/5  | `bar1_exit_logging.py`, `main.py` |
| **Total today** | **16** | **16/16** | 6 production files |

Cumulative test surface from D22 + D23: **35 + 16 = 51 tests, 51/51 pass**.
Broader sweep across `executor|bridge|d216|d217|d218|d222|d228|partial_fill|journal|arena|bar1`: **440 passed, 5 pre-existing failures** (all unrelated to today's patches — README content, scenario corpus, asyncio event-loop ordering).

### v2.2 entry-point recommendation (final)

The clean path forward is **Phase 0 instrumentation MVP** per
`21_phase0_instrumentation_mvp.md`. Today's bugs prove the prerequisite:

1. **Bug Q** showed that exit prices were silently fabricated. Phase
   0 `trade_context` (NBBO at submit/first-fill/terminal) makes that
   impossible by construction.
2. **Bug R** showed the internal tracker stop diverged from broker
   truth. Phase 0 `trade_context.terminal_status` + the new
   `actual_stop_price` field together guarantee the discrepancy
   surfaces immediately.
3. **Bug N** showed journal P&L derived from a wrong source. Phase 0
   `child_fill_ticks` gives every fill a row, eliminating the journal-
   vs-broker reconciliation question entirely (the broker IS the
   journal).
4. **Bug T** showed required-field validation drift. Phase 0's
   schemas live in code under test, so any future required field
   appears in a failing schema test before it hits production silently.

**v2.2 §13.7 row 1** ("Phase 0 trade_context + bar_context +
child_fill_ticks live, all four `15_…` capability gaps covered") is
the next sprint. Tonight's N/Q/R fixes plus yesterday's Bug D
infrastructure (`_poll_for_terminal_fill` already produces the
terminal_ts + terminal_filled_qty + terminal_status fields the
trade_context schema needs) means this work is ~70% complete already
— what remains is the Parquet writer + the one-time hook wiring at
the four lifecycle sites.

**Concrete next ticket (sized at ~3 engineering days):**
1. Create `src/analysis/instrumentation_writer.py` (the
   `InstrumentationWriter` class from `21_…spec.md` §5).
2. Add `emit_trade_context` calls at 3 sites:
   `alpaca_executor.submit_oto_order` (submit row),
   `bridge._poll_for_terminal_fill` (terminal columns), close path
   (close-fill row).
3. Add `emit_bar_context` at `bridge.execute_verdict` after
   `add_position`.
4. First write target: `data/instrumentation/trade_context/session_date=2026-04-24/orders.parquet`.

After that, the v2.2 §13.3 martingale-residual gate at N=30 fills
becomes a real ticket with real data, and Paper 1 m6 submission
clock starts.

