# 166 — 2026-05-19 D297 + D298 — OTO stop-fill journal + EOD exception logging

**Session date:** 2026-05-19 (Tuesday early morning)
**Branch:** develop
**Predecessors:** [doc 165 Discord enhancements](165_v6_2026_05_13_discord_pct_change_enhancement.md), [doc 164 four-bug fix](164_v6_2026_05_13_four_bug_comprehensive_fix.md)
**Trigger:** 3-day catch-up review found 4 production issues. Two are code defects (fixed in this commit); one is a research/calibration question (filed for D300).

---

## 0. What 3 days of running surfaced

After last session's doc 164 + 165 ship, the bot ran Thu 5/14, Fri 5/15, Mon 5/18. Net result: **−$1,304 realized + −$3,671 unrealized (NXXT still open) = −$5,369 equity drift (−3.83%)** over 3 trading days. The deep-dive into Monday's logs surfaced 4 distinct issues:

| Bug | Severity | Status |
|---|---|---|
| **A** Stop-leg OTO fills don't journal `record_close()` | Production-critical | **FIXED D297** |
| **B** Stop-leg OTO fills don't update internal tracker (613 ERROR-level qty_drift on Monday alone) | Production-critical | **FIXED D297** (same root) |
| **C** EOD failsafe exception logging shows empty `raised ()` for empty-message exceptions | Observability | **FIXED D298** |
| **D** Lottery runner: 0 META-PASS picks in 5 trading sessions | Research/calibration | **FILED D300** |

---

## 1. D297 — OTO stop-leg fill must journal record_close + tracker remove

### Production evidence (Mon 5/18 EOD report)

```
broker_pnl=$-680.71  journal_pnl=$-109.29  delta=$-571.42 — 3 ticker disagreements:
  CISS   broker=$-561.87  journal=$+0.00   (closed via OTO stop, journal MISSING)
  GCTS   broker=$-118.84  journal=$+0.00   (closed via OTO stop, journal MISSING)
  NXXT   broker=$+0.00    journal=$-109.29 (carry-overnight, not yet realized)
```

Plus: **613 ERROR-level** `EOD RECON: qty_drift=1` records all day, `D74 close_position GCTS → 404` (bot tried to close already-closed position), and `[SHADOW] D232 RECON_LETHAL: drift sustained 9980s exceeds 60s lethal threshold` (if D232 weren't in shadow mode, the bot would have force-flat'd everything).

### Root cause

`src/execution/fill_stream_bridge.py:on_trade_update()` receives OTO stop-leg fills via websocket. It dispatches to `tranche_monitor.on_fill()` which returns `None` (the stop-leg order_id isn't a registered tranche). The function then returns silently at line 127:

```python
result = self._tranche_monitor.on_fill(fill_event)
if result is None:
    return  # Unknown order — not a tranche fill
```

Consequence: journal never recorded the close, tracker never knew the position was gone. The D146 fix (Bug #13, 2026-04-08) plugged this hole in the BAR-1 path; D295 plugged it in the D91 next-open path; **D297 plugs it in the OTO stop-leg path** — the third instance of the same architectural pattern.

### Fix

`FillStreamBridge.__init__` now accepts two new kwarg-only parameters:

```python
def __init__(self, tranche_monitor, stop_resubmitter=None, *,
             position_manager=None, trade_journal=None):
```

When `tranche_monitor.on_fill()` returns `None`, the new `_handle_oto_stop_fill(event)` method:

1. Checks `event.side == "sell"` and `position_manager.has_position(ticker)`
2. Computes `realized_pnl = (fill_price - entry_price) × qty`
3. Calls `trade_journal.record_close(reason="STOP_FILL")` (best-effort, never fatal)
4. Calls `position_manager.remove_position(ticker)` (best-effort)
5. Emits a `FillEvent(position_closed=True)` for downstream Phase 3 consumers

`main.py:719` now passes the two new kwargs through. Every existing caller that omits them keeps the legacy "silently ignore unknown order_id" behaviour — critical for the existing tranche_monitor unit tests and S025/S026 stream tests.

### Tests (16 cases, `tests/unit/test_d297_oto_stopfill_journal_and_tracker.py`)

| Category | Tests |
|---|---|
| Headline regression | CISS replay, GCTS replay |
| Backwards-compat | no journal/no pm → silent; only-pm → no-op; only-journal → no-op |
| Selectivity | BUY-side fill ignored; unknown ticker ignored; non-FILL event ignored |
| Defensive | zero entry_price; pm exception; journal exception; remove_position exception; no matching journal entry |
| Downstream | FillEvent emitted with `position_closed=True` and correct pnl |
| Regression | tranche-fill path unchanged when D297 kwargs are present |
| E2E | shows that D238 journal=MISSING gap stops appearing |

**16/16 PASS.** Adjacent test suites: 12/12 D91 + 8/8 D279 + 6/6 D295 + 35/35 D121 stream tests **all green**.

---

## 2. D298 — EOD failsafe exception logging must include type

### Production evidence (Fri 5/15)

```
D238 EOD_RECONCILIATION_DELTA DEGRADED: get_orders raised () — recon skipped
```

Empty parens. The underlying exception (likely `httpx.ReadTimeout`) had no message, so `f"({e})"` collapsed to `"()"` and the operator had no diagnostic info. Was it timeout? 500? auth? network blip?

### Root cause

4 sites in `src/monitoring/eod_failsafes.py` (D241, D242 × 2, D238) used the bare pattern `f"raised ({e})"`. When the caught exception has empty `args`, `str(e)` returns `""` and the log silently loses the exception class identity.

### Fix

New helper at top of file:

```python
def _fmt_exc(e: BaseException) -> str:
    msg = str(e).strip()
    name = type(e).__name__
    return f"{name}: {msg}" if msg else name
```

All 4 sites updated: `_fmt_exc(e)` replaces bare `e` in the logger calls. The Friday 5/15 case now reads `raised (ReadTimeout)` instead of `raised ()` — actionable.

### Tests (17 cases, `tests/unit/test_d298_eod_failsafe_exception_logging.py`)

| Category | Tests |
|---|---|
| `_fmt_exc` unit | empty → just type; non-empty → "Type: msg"; whitespace-only → just type; multi-line; custom subclass; httpx subtypes; BaseException subclass |
| Per-site (D241) | empty exception logged; messaged exception logged; orphan cancel failure logged |
| Per-site (D242) | get_positions failure logged; ghost-close failure logged |
| Per-site (D238) | the headline 5/15 regression; messaged exception preserves message |
| Coverage guard | static check: no bare `, e,` pattern remains in eod_failsafes |
| Surface | `_fmt_exc` is exported for other modules to use |
| Recovery | next call after degraded run returns normally |

**17/17 PASS.** Existing `test_d24_eod_failsafes.py` (10 cases) **still green**.

---

## 3. Bug D — Lottery 0-pick streak (filed for D300 research, not fixed)

```
Last 4 lottery sessions (5/13, 5/14, 5/15, 5/18) -- 40 candidates total:
  v3t  min=0.124  p25=0.144  median=0.167  p75=0.189  max=0.271
  Tier pass rate:
    VETOED bar (v3t>=0.30):  0/40 = 0%
    HIGH bar   (v3t>=0.50):  0/40 = 0%
    ELITE bar  (v3t>=0.60):  0/40 = 0%
  TCN status: 40/40 = 100% have tcn=None at 09:25 ET
```

Even the best candidate this week (0.271) is below the VETOED threshold (0.30). And TCN is unavailable pre-market by design ("TCN-veto won't fire pre-market; VETOED tier becomes available [at intraday refresh, 10:00 ET]"). So lottery_runner at 09:25 can only ever produce HIGH/ELITE picks (v3t ≥ 0.50), and that hasn't happened in 5 sessions.

This is a calibration / research question, not a code defect. Three plausible directions:
- **(a)** Accept 0 picks — current behaviour. Market regime is genuinely poor.
- **(b)** Recalibrate HIGH threshold downward (e.g. 0.40) to be more aggressive. Risks: signal degradation.
- **(c)** Move TCN computation pre-market so VETOED tier opens at 09:25. Risks: TCN model may not have enough intraday context to score reliably pre-market.

**Filed for D300:** dump the v3 OOS test-set score distribution for the same date range and compare with this week's lottery candidates. If the production candidates are systematically below the OOS distribution, that's evidence of a feature-pipeline bug or training-test mismatch. If they're in-distribution, then market regime is just poor and threshold should stay.

---

## 4. What's in this commit

| File | Change |
|---|---|
| `src/execution/fill_stream_bridge.py` | +120 LOC: `__init__` kwargs + `_handle_oto_stop_fill` method |
| `main.py` | +9 LOC: pass `position_manager` + `trade_journal` into bridge ctor |
| `src/monitoring/eod_failsafes.py` | +20 LOC: `_fmt_exc` helper + 4 callsite updates |
| `tests/unit/test_d297_oto_stopfill_journal_and_tracker.py` | NEW, 16 tests |
| `tests/unit/test_d298_eod_failsafe_exception_logging.py` | NEW, 17 tests |
| `docs/research-log/166_v6_2026_05_19_d297_d298_oto_stopfill_and_exception_logging.md` | THIS doc |

**Total**: ~150 production LOC, 33 new tests, **219/219 regression** across all touched + adjacent suites.

---

## 5. Verdict

**Status:** **PASS — SHIPPED 2026-05-19.**

### What tomorrow's bot will do differently

1. When NXXT closes at Tuesday's 09:30 ET market open via D91 (carrying overnight from Monday), D295 already records that close to journal cleanly.
2. **If** Tuesday's bot opens new OTO positions and any of them hit their stop intraday, **D297 will now record the close to journal + remove from tracker**. No more 613 ERROR-level qty_drift records. No more D238 "journal=MISSING" disagreements for stop-fill closes.
3. If `get_orders` (or any of the 4 patched sites) throws an empty exception, the log will say `raised (ReadTimeout)` instead of `raised ()`. Operator can immediately classify network vs auth vs data issue.

### Out of scope (filed)

- **D300 (research)**: lottery v3t threshold investigation. Compare production candidate scores against OOS test-set distribution to determine if calibration drift is real or market regime is just poor.
- **D296 follow-up still open**: Together.ai LLM trips continue 14-41/day. D296 jitter prevents cascade but doesn't fix rate limit. Multi-provider fallback (Groq?) still pending.
- **Polling-based reconciler**: if the websocket misses a fill (e.g. dropped connection), only EOD recon catches it. A 5-minute polling reconciler would catch it sooner. Filed.
