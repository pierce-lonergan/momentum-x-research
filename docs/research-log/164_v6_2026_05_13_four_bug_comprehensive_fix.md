# 164 — 2026-05-13 four-bug comprehensive fix (D294, D295, D296, doc-163-followup)

**Session date:** 2026-05-13 night
**Branch:** develop
**Predecessors:** [doc 163 shadow runner live-mode](163_v6_2026_05_12_shadow_runner_live_mode.md), [doc 162 fresh-data experiments](162_v6_2026_05_12_fresh_data_three_experiments.md)
**Trigger:** Today's deep-dive into the 2026-05-13 paper-trading run surfaced 5 production bugs. 4 are fixed in this commit (the 5th was a side-effect of #4).

---

## 0. Origin: today's deep dive

The 2026-05-13 session report:
- Daily P&L: −$199.62
- 1 round-trip trade (VELO, stopped out −$172.87)
- 9 orders submitted, 1 filled (11.1% fill rate)
- 21 LLM circuit-breaker trips between 09:20-10:32 ET
- D222 PNL_RECON divergence: broker $41,083 vs journal $0 across 70 tickers (all marked `journal=MISSING`)
- D-codes fired: D230-equity (1)
- 0 BUY votes from any debate (35 triggered, all skipped for budget)

What that diagnostic surfaced:

| ID | Bug | Severity | Root cause |
|---|---|---|---|
| #1 | Doc 163 shadow runner ran 1 day too early | NEW (my fault) | `--date $TODAY` at 17:30 ET when catalog max(d0) = T-1 |
| #2 | D91 next-open exit didn't call `record_close()` | Production-critical | D146 fix never extended to D278's t1_next_open path |
| #3 | Together.ai LLM circuit-breaker storm | Operational-critical | Zero backoff between primary/fallback retries |
| #4 | QUCY-style sub-$1 stop math 422 errors | Sniper-bug | Stop landed within $0.01 of base, hitting Alpaca's strict floor |
| #5 | D237 BRIDGE_CANCEL on every initial OTO | Cascading | **Same root as #4** — every OTO submit was rejected for stop violation, then bridge cancelled |

---

## 1. Pre-commits (locked binary gates)

### Gate D294 — Alpaca stop-price floor clamp (Bug #4 + #5)

**Method:** new helper `_clamp_stop_below_base(stop_loss, base_price, side)` in
`src/data/alpaca_client.py`. Used by `submit_oto_order`, `submit_oto_short_order`,
and `submit_bracket_order`. Enforces:
- long: `stop ≤ round(base − 0.01, decimals)`
- short: `stop ≥ round(base + 0.01, decimals)`
- decimals: 2 if base ≥ $1.00 else 4

**Pass:** new test `tests/unit/test_d294_alpaca_stop_floor.py` (19 tests):
- QUCY 2026-05-13 production case: base $0.5573, stop $0.55 → clamped to $0.5473
- AAPL-style $200 base: stop $200 → clamped to $199.99
- Already-legal stops untouched (parametric)
- Short-side mirror works
- Clamp NEVER tightens (only widens for safety)
- Invalid `side` raises ValueError

### Gate D295 — D91 next-open close records journal (Bug #2)

**Method:** added `trade_journal: Any = None` kwarg to
`bridge._close_overnight_position`. New STEP 4 (post-close): if
`trade_journal` provided, fetch snapshot for exit_price, compute realized_pnl,
loop journal `_entries` to find open BUY for ticker, call `record_close(...)`
with `exit_reason="NEXT_OPEN"`. Wrapped in try/except — broker close already
succeeded; journal write is advisory.

`main.py:2202` updated to pass `trade_journal=trade_journal` into the call.

**Pass:** new test `tests/unit/test_d295_d91_journal_record_close.py` (6 tests):
- Headline regression: D91 close + journal kwarg → exactly 1 record_close called
  with `exit_reason="NEXT_OPEN"` and computed pnl
- Backwards-compat: omitting `trade_journal` preserves the original 3-step
  behavior — TestD91CloseRoutineSteps + D279 tests still pass (verified)
- Snapshot failure → exit_price falls back to entry_price (pnl = 0)
- record_close exception → warning logged, broker close still reports True
- Missing BUY entry → warning, no record (no false-positive close in journal)
- Zero entry_price → no divide-by-zero, pnl = 0

### Gate D296 — Jittered backoff between LLM fallback retries (Bug #3)

**Method:** new helper `_llm_fallback_backoff_ms(err)` in `src/agents/base.py`.
Returns jittered sleep before each fallback model call:
- Rate-limit errors → 1500ms base × uniform[0.5, 1.5] jitter (750-2250ms range)
- Other errors (Timeout, 5xx, ServiceUnavailable) → 200ms base × jitter (100-300ms)

Wired into `BaseAgent.analyze()` fallback loop with `await asyncio.sleep(...)`
before each fallback `_call_llm` attempt.

**Pass:** new test `tests/unit/test_d296_llm_fallback_backoff.py` (15 tests):
- 5 rate-limit error string variants → all get long backoff (≥750ms)
- 5 transient error variants → all get short backoff (≤300ms)
- 100-sample jitter check: ≥30 distinct values (concurrent agents de-stagger)
- Backoff always positive, always bounded
- Reproducible with seeded RNG
- Constants exposed for runtime tuning

### Gate Doc-163-followup — Shadow runner timing + exit codes (Bug #1)

**Method:** in `scripts/tabpfn_shadow_runner.py`:
1. `--mode live` without `--date` defaults to `df["d0"].max().date()` instead
   of `pd.Timestamp.today().date()`. The catalog's max d0 is the freshest
   scoreable date (typically T-1; today's day_aggs aren't published until
   ~04:00 ET tomorrow).
2. Split skip counter into `skipped_no_data` (informational: target d0 not
   in catalog OR file already exists) and `skipped_other` (actionable: too
   few train rows OR TabPFN crash). Exit 0 unless `skipped_other > 0` AND
   `written == 0`.

`scripts/daily_data_ingest.ps1` updated to drop `--date $Today` from the
step 4a invocation.

**Pass:** new test `tests/unit/test_doc163_followup_shadow_runner_timing.py`
(6 tests):
- `--mode live --date 2099-12-31` (no rows) → exit 0 (not 1)
- Pre-existing parquet skip → exit 0
- argparse accepts `--mode live` without `--date`
- Launcher source no longer contains `--date $Today` for shadow step
- Source guard: old `return 0 if written > 0 else 1` line is gone
- Help text mentions historical/live modes

### Composite

All 4 gates must PASS for ship. Verification:
- 133/133 tests pass across new + adjacent regression suites
- Live verification: `python scripts/tabpfn_shadow_runner.py --mode live`
  produced `2026-05-12.parquet` with 36 rows in 7 s on RTX 5070

---

## 2. Why this matters for tomorrow's run

**Without these fixes:**
- Sub-$1 stocks (QUCY, sub-$1 movers) hit BRIDGE_CANCEL retry storm on every
  attempt → ~5+ wasted orders/day, fill window blown
- Every D278 carry-overnight close adds to D222/D238 journal divergence,
  potentially masking real reconciliation bugs in the noise
- Next Together.ai rate-limit window mutes the bot the same way it did today
- Tonight's data ingest would WARN-FAIL the shadow step again (and again,
  every night, until manually fixed)

**With these fixes (effect compounds over time):**
- Tomorrow's 17:30 ET ingest writes shadow file for 2026-05-13 (T-1 from
  the perspective of Friday's run) — D293.8 pool starts accumulating
- D278 carry-overnight closes now journal cleanly → D238 noise drops to
  zero except for genuine drift
- LLM rate-limit windows still cause local agent failures, but jitter
  prevents the 21-trip cascade from recurring
- QUCY-class candidates can actually fill their first OTO submission

---

## 3. Files in this commit

| File | Change |
|---|---|
| `src/data/alpaca_client.py` | +49 LOC: `_clamp_stop_below_base` helper + 3 callsite uses (OTO long/short, bracket) |
| `src/execution/bridge.py` | +57 LOC: STEP 4 in `_close_overnight_position` for journal recording (kwarg-only, backwards-compat) |
| `main.py` | +6 LOC: pass `trade_journal=trade_journal` into D91 call site |
| `src/agents/base.py` | +44 LOC: `_llm_fallback_backoff_ms` helper + jittered `asyncio.sleep` before fallback retries |
| `scripts/tabpfn_shadow_runner.py` | ~25 LOC modified: max(d0) default in live mode + split skip counters + exit-0 semantics |
| `scripts/daily_data_ingest.ps1` | 2 lines: drop `--date $Today` from step 4a |
| `tests/unit/test_d294_alpaca_stop_floor.py` | NEW, 19 tests |
| `tests/unit/test_d295_d91_journal_record_close.py` | NEW, 6 tests |
| `tests/unit/test_d296_llm_fallback_backoff.py` | NEW, 15 tests |
| `tests/unit/test_doc163_followup_shadow_runner_timing.py` | NEW, 6 tests |

**Total:** ~180 LOC production code, 46 new tests.

---

## 4. Verdict

**Status:** **COMPOSITE PASS — SHIPPED 2026-05-13.**

| Gate | Outcome | Evidence |
|------|---------|----------|
| D294 (stop-floor) | **PASS** | 19/19 tests; QUCY production case clamps correctly |
| D295 (D91 journal) | **PASS** | 6/6 tests; backwards-compat verified (12 D91/D279 tests still pass) |
| D296 (LLM backoff) | **PASS** | 15/15 tests; jitter spreads ≥30 distinct values across 100 samples |
| Doc-163 followup | **PASS** | 6/6 tests; live invocation produced 36 shadow preds for 2026-05-12 in 7s |
| Composite | **PASS** | 133/133 across all touched + adjacent code paths |

### Hygiene rule observation

Today reinforced **Rule 11** (deep bug sweep on second pass): the morning
deep-dive surfaced 5 bugs not captured in the original session WARN
reports. Three of the five (#2, #4, #5) had been silently degrading the
system for weeks; D278 was the trigger that made #2 visible. The cost of
not running deep dives is that production bugs accumulate in the
"normal" log noise.

### Out of scope

- **D293.8 retest still gated on shadow pool ≥200 picks.** Tonight's
  shadow file (2026-05-12, 36 picks) gets us started. Need ~5 more days
  of clean operation.
- **Together.ai fallback PROVIDER (e.g. Groq) not added.** The D296
  jittered backoff addresses the cascade but not the underlying rate
  limit. If trips per day exceed 10 again next week, file follow-up to
  add a multi-provider fallback chain.
- **D-code cleanup.** D230-equity fired today because broker_equity_eod
  ($140,147.61) drifted from internal tracker tolerance. Fix #2 (D295)
  should reduce these spurious fires, but doesn't eliminate the
  tolerance threshold question. Filed for D297 if it persists.
