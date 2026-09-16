# Live Session Bug Tracker

Bugs discovered during live paper trading sessions. Each entry includes root cause
analysis and the fix applied.

---

## 2026-03-17 (Session Day 1 — First Live Market Session)

### BUG-001: Dashboard "Open" counter not decrementing on D98 stop-out

| Field | Value |
|-------|-------|
| **Severity** | HIGH |
| **File** | `main.py` (D99 stop-out path, ~line 1858) |
| **Symptom** | Dashboard showed `Open: 1` with NVTS position details even after position was stopped out. Phase 3 heartbeat correctly said "monitoring 0 positions". |
| **Root Cause** | When D98 detects a position gone from the broker and the Shapley attribution path fails (D99 fallback), the code recorded P&L via `daily_pnl.inc()` but **never updated the `open_positions` gauge**. The gauge is only set in `bridge.py` on normal open/close paths. The D99 path bypasses bridge entirely, removing the position directly from PositionManager. |
| **Fix** | Added `_gm_d103().open_positions.set(len(bridge.position_manager.open_positions))` after the `daily_pnl.inc()` call in the D99 path. |
| **Impact** | Cosmetic — dashboard showed stale position count. No effect on actual trading or P&L tracking. |

---

### BUG-002: Phase 3 VWAP scan TypeError on every cycle

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM |
| **File** | `main.py` (~line 2206) |
| **Symptom** | `[Phase 3] VWAP scan error: '>' not supported between instances of 'NoneType' and 'int'` logged every ~60 seconds during Phase 3. |
| **Root Cause** | `orchestrator._get_vwap(ticker, 0.0)` can return `None` (by design — D106 changed the fallback from `price*0.98` to `None` to avoid false rejections when WebSocket is unavailable). The comparison `if real_vwap > 0:` then crashes because Python cannot compare `None > int`. |
| **Fix** | Changed to `if real_vwap is not None and real_vwap > 0:` |
| **Impact** | Phase 3 VWAP-based re-scan was completely broken — no intraday VWAP breakout detection was running. Stocks that reclaimed VWAP intraday would not be detected as re-entry candidates. |

---

### BUG-003: Technical agent float(None) crash on every evaluation

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM |
| **File** | `src/agents/deterministic_technical.py` (~line 268-270) |
| **Symptom** | `D92: Agent technical_agent error: float() argument must be a string or a real number, not 'NoneType'` on both CTMX and NVTS evaluations. |
| **Root Cause** | Three lines used raw `float()` on kwargs that could contain explicit `None` values: `float(kwargs.get("current_price", 0))`. The default `0` only applies when the key is **missing** from kwargs — if the key exists with value `None`, `float(None)` raises TypeError. The file already had a `_safe_float()` helper that handles None correctly, but these three lines didn't use it. |
| **Fix** | Changed all three to use `_safe_float(kwargs.get("current_price"), 0.0)` (and same for `rvol`, `vwap`). Removed the default from `.get()` since `_safe_float` handles None. |
| **Impact** | Technical agent returned no signal for every candidate, causing all evaluations to run with only 2-3 agents instead of the expected 4+. This directly caused CTMX and NVTS to fail the D101 consensus gate (needed 2 directional agents, only got 1) because the technical agent was always erroring out instead of providing its signal. |

---

### Observations (not bugs)

#### OBS-001: D92 LLM fallback working correctly
- News agent primary (Qwen3-235B) timed out on CTMX and AAL after 15s
- Fallback to MiniMax-M2.5 succeeded in both cases (~8-10s additional)
- Manipulation classifier hit ServiceUnavailable on Qwen3, fell back to DeepSeek-V3.1 successfully

#### OBS-002: VIX elevated at 26.1
- All position sizing automatically halved (VIX > 25 threshold)
- This is correct defensive behavior on a volatile day (SPY -0.16%)

#### OBS-003: NVTS position entered and stopped out
- Entry: $10.61, 425 shares (~$4,509 position)
- Stop: $8.43 (ATR-based, ~20% distance)
- Exit: ~$10.03 (estimated, position disappeared from broker)
- Loss: -$248.01 (5.5% on position, ~0.25% of portfolio)
- Note: Stop was at $8.43 but exit was at $10.03 — suggests the position was closed by a mechanism other than the stop loss (possibly a trailing stop ratchet or manual close on Alpaca side)

#### OBS-004: D112 Adaptive Router filtering effectively
- 4/6 candidates instant-rejected for RVOL < 1.5x minimum
- Only CTMX (42.3x) and NVTS (2.1x) advanced to full evaluation
- Saved significant LLM cost by not evaluating weak candidates

---

## 2026-03-20 (Session Day 4 — MOBX Phantom Gap)

### BUG-006: Stale `prevDailyBar.c` Creates Phantom Gaps

| Field | Value |
|-------|-------|
| **Severity** | P0 — caused trades on wrong-day signals |
| **File** | `src/scanners/premarket.py:351-376`, `main.py` (`_fetch_scan_quotes`) |
| **Symptom** | MOBX appeared as +15.4% gapper on March 20 with 22 BUY evaluations in 30 min. Stock was actually DOWN -6.6% from March 19 close. |
| **Root Cause** | Alpaca's `prevDailyBar.c` returned $0.4592 (March 18 close) on both March 19 AND March 20. On March 20, scanner computed gap = ($0.53 - $0.4592)/$0.4592 = +15.4% when actual gap from March 19 close ($0.5676) was -6.6%. Micro-cap settlement delay caused 2-day-old close to persist. |
| **Fix** | Added stale gap detection: compare `dailyBar.o` (today's open) vs `prevDailyBar.c`. If open already accounts for ≥70% of computed gap AND intraday change <3%, gap is from a prior session → skip. Also added `day_open` passthrough in `main.py:_fetch_scan_quotes`. |
| **Impact** | MOBX entered at $0.5676, lost money trading a stock that was declining. Wasted 22 agent evaluations on a phantom candidate. |
| **Tests** | `tests/property/test_scanner_properties.py::TestD121StaleGapDetection` |

---

### BUG-007: Dead Exhaustion Detection — `prior_day_gap_pct` Never Populated

| Field | Value |
|-------|-------|
| **Severity** | P1 — multi-day runners not flagged as exhaustion |
| **File** | `src/scanners/premarket.py:391-402` |
| **Symptom** | `classify_gap_quality()` has a back-to-back gap exhaustion check (lines 85-88) that existed since D101 but **never fired**. |
| **Root Cause** | `prior_day_gap_pct` parameter was added to `classify_gap_quality()` as forward-looking design but no caller ever computed or passed it. Always `None`. The check `if prior_day_gap_pct is not None and prior_day_gap_pct > 0.10` was dead code. |
| **Fix** | Compute `prior_day_gap_pct` from `day_open` vs `previous_close` in scanner loop. If `day_open` >5% above `previous_close`, set `prior_day_gap_pct` to that delta and `multi_day_run=True`. |
| **Impact** | Stocks running for multiple consecutive days (back-to-back >10% gaps) were never flagged as exhaustion. These are the highest-risk entries with 72% reversal probability (LuxAlgo). |
| **Tests** | `tests/property/test_scanner_properties.py::TestClassifyGapQualityExhaustion` |

---

### BUG-008: News Agent Treats Stale Catalysts as Fresh

| Field | Value |
|-------|-------|
| **Severity** | P1 — amplifies phantom gap signals |
| **File** | N/A (mitigated by BUG-006 scanner-level fix) |
| **Symptom** | News agent found MOBX catalyst articles from March 19 and treated them as fresh catalysts for March 20. Both scanner AND agents agreed MOBX was strong when it was declining. |
| **Root Cause** | News agent lacks robust catalyst-date comparison. Articles about MOBX's March 19 move were technically still "about" the stock, but the catalyst was already priced in. |
| **Fix** | Mitigated: BUG-006's stale gap detection filters phantom candidates at scanner level, preventing them from reaching the news agent. Full fix (catalyst-date staleness check in news agent) deferred as lower priority. |
| **Impact** | Without scanner gate, news agent would continue reinforcing phantom signals on stale candidates. |

---

## D121 Code Audit (2026-03-21)

### BUG-C1: PnL Double-Count on Position Close After Tranche Fills

| Field | Value |
|-------|-------|
| **Severity** | P0 — circuit breaker gets wrong daily P&L |
| **File** | `src/execution/position_manager.py:799`, `src/execution/bridge.py:304` |
| **Root Cause** | `close_position_with_attribution()` computed PnL as `(exit - entry) * qty` using ORIGINAL qty. But tranche fills already recorded PnL for sold shares via `tranche_monitor.record_realized_pnl()`. Using full qty double-counted tranche P&L. Bridge also captured `qty` instead of `remaining_qty` for metrics. |
| **Fix** | Changed both to use `remaining_qty` — only the shares still held at close time. |
| **Impact** | Circuit breaker threshold was systematically wrong after any tranche fill. A position that sold T1 at profit then closed the rest at a loss would overstate the loss by including T1's shares again. |

---

### BUG-C3: Fast-Path Queue Destroyed Every Phase 1 Iteration

| Field | Value |
|-------|-------|
| **Severity** | P0 — fast-path feature completely broken |
| **File** | `main.py:877-881` |
| **Root Cause** | `fast_path_scored=False` and `fast_path_queue=[]` reset every 60-second Phase 1 loop iteration. The queue built at ~9:25 ET was destroyed at ~9:26 ET. By the time Phase 2 checked `fast_path_queue` at 9:30, it was empty. |
| **Fix** | Guarded resets with `if not fast_path_scored:` — once the queue is built, subsequent iterations don't reset it. |
| **Impact** | Fast-path OTO orders at market open never fired. The entire fast-path feature (D85) was a no-op. |

---

### BUG-C4: RVOL Inflated to Millions for Stocks With No Volume History

| Field | Value |
|-------|-------|
| **Severity** | P0 — fabricated metrics pass all filters |
| **File** | `src/scanners/premarket.py:187` |
| **Root Cause** | `avg_volume_at_time.replace(0, 1)` replaced zero with 1, making `RVOL = premarket_volume / 1`. A stock with 500K premarket volume and no history got RVOL = 500,000x, passing extreme RVOL overrides and exhaustion flags simultaneously. |
| **Fix** | Changed floor from 1 to 50,000 — a conservative default that produces plausible RVOL for stocks without history. |
| **Impact** | New IPOs or recently listed stocks with no volume history could enter the pipeline with fabricated extreme-RVOL metrics. |

---

### BUG-H1: D98 Stop-Out Uses Hardcoded 5.5% Instead of Actual Stop

| Field | Value |
|-------|-------|
| **Severity** | P1 — phantom losses recorded |
| **File** | `main.py:1918` |
| **Root Cause** | When a position disappeared from the broker (stop-out detected), exit price was estimated as `entry * (1 - 0.055)` regardless of actual stop level. After trailing ratchets stop to breakeven ($10 entry, stop at $10), this recorded exit at $9.45 — a phantom $0.55/share loss. |
| **Fix** | Changed to use `pos.stop_loss` — the actual, ratcheted stop level. |
| **Impact** | Every stop-out with a ratcheted stop recorded a worse loss than reality. Corrupted daily P&L and session reports. |

---

### BUG-H2: `opened_at` Type Mismatch — str vs datetime

| Field | Value |
|-------|-------|
| **Severity** | P1 — crashes stop-out detection |
| **File** | `main.py:1492, 1914` |
| **Root Cause** | `opened_at` stored as `isoformat()` string during state persistence (line 1492), but D98 stop-out detection did `datetime - pos.opened_at` (line 1914) which crashes with `TypeError` if `opened_at` is a string. This broke the D99 grace period check, meaning ALL positions (even fresh ones) would be checked for stop-out, potentially removing positions that hadn't filled yet. |
| **Fix** | Added `isinstance` check: convert string to datetime via `fromisoformat()` before arithmetic. Matches the pattern already used at line 1949. |
| **Impact** | After process restart (state recovery), all position age calculations would crash, disabling stop-out detection for the rest of the session. |

---

### BUG-M1: Entry Price Uses Verdict Estimate Instead of Fill Price

| Field | Value |
|-------|-------|
| **Severity** | P2 — slippage not captured in position tracking |
| **File** | `src/execution/bridge.py:231` |
| **Root Cause** | `ManagedPosition.entry_price` was set from `verdict.entry_price` (the price at evaluation time), not the actual fill price from `OrderResult`. All downstream P&L calculations used the wrong entry price. |
| **Fix** | Use `order_result.fill_price` when available, fall back to `verdict.entry_price`. |

---

### BUG-M7: Confidence Deflation Fallback Mismatched Config Default

| Field | Value |
|-------|-------|
| **Severity** | P2 — LLM confidence 14% more deflated than intended |
| **File** | `src/agents/base.py:311` |
| **Root Cause** | Hardcoded fallback `_deflation = 0.6` when settings load fails, but config default is `0.70`. After D106 raised it to 0.70, the fallback was never updated. |
| **Fix** | Changed fallback from 0.6 to 0.70. |

---

### BUG-M8: `elo_stddev` Was PnL Variance, Not Elo Uncertainty

| Field | Value |
|-------|-------|
| **Severity** | P2 — misleading metric label |
| **File** | `src/arena/strategy_arena.py:492-512` |
| **Root Cause** | `elo_stddev` computed standard deviation of per-session PnL, but was named/displayed as if it was Elo rating uncertainty. |
| **Fix** | Renamed to `pnl_stddev` everywhere. `from_dict` accepts both old and new key for backward compat. |

---

### BUG-M9: Shutdown Handler Recorded $0 P&L for All Positions

| Field | Value |
|-------|-------|
| **Severity** | P2 — corrupts end-of-day P&L |
| **File** | `main.py:3032-3036` |
| **Root Cause** | Shutdown close used `exit_price=pos.entry_price`, which always records $0 P&L regardless of actual market price. |
| **Fix** | Fetch current prices from broker positions before close. Fall back to entry_price only if broker unavailable. |

---

### BUG-M10: EDT Volume Profile Off by 1 Hour

| Field | Value |
|-------|-------|
| **Severity** | P2 — volume profile shifted during EDT |
| **File** | `src/data/alpaca_client.py:380` |
| **Root Cause** | Fallback (no zoneinfo) hardcoded `dt.hour - 14` for EST. During EDT (Mar-Nov), market opens at 13:30 UTC not 14:30, shifting all volume buckets by 60 minutes. |
| **Fix** | Use month-based DST approximation: `_market_open_hour_utc = 13` during Mar-Nov, `14` otherwise. |

---

### BUG-M4: Zero-Confidence Debate BUY Got Unsized Position

| Field | Value |
|-------|-------|
| **Severity** | P2 — position_pct falls through to 0 |
| **File** | `src/core/orchestrator.py:2378-2387` |
| **Root Cause** | Sizing had `if debate and debate.confidence > 0` then `elif not debate and scored.mfcs > 0`. When debate exists but confidence=0 and MFCS>0, neither branch runs → position_pct stays at 0. |
| **Fix** | Changed `elif` to check `scored.mfcs > 0` regardless of debate presence. Zero-confidence debates now fall through to MFCS-based sizing. |

---

### BUG-H5: Exhaustion Threshold Conflicts With Sub-$3 Price Override

| Field | Value |
|-------|-------|
| **Severity** | P1 — sub-$3 stocks always flagged as exhaustion |
| **File** | `src/scanners/premarket.py:385` |
| **Root Cause** | `rvol_exhaustion = rvol_val > 5.0` used the same 5.0x threshold as `price_override_rvol`. Every sub-$3 stock that passed the price override (requiring RVOL > 5.0) was immediately flagged as exhaustion. |
| **Fix** | Use threshold of 15.0x for sub-$3 stocks (`current_price < price_min`), 5.0x for standard stocks. |

---

### BUG-H6: Debate Error Strings Fed to Judge as Arguments

| Field | Value |
|-------|-------|
| **Severity** | P1 — judge evaluates error text as argument |
| **File** | `src/agents/debate_engine.py:217` |
| **Root Cause** | When bull/bear agent fails, returns `[BULL AGENT ERROR: ...]` string. This was passed directly to the judge, which evaluated the error text as a trading argument. |
| **Fix** | Detect error prefix and replace with neutral placeholder: "The [bull/bear] advocate was unable to present an argument." |

---

### BUG-H8: VWAP=None Silently Weakens Technical Signals

| Field | Value |
|-------|-------|
| **Severity** | P1 — STRONG_BULL harder to reach without VWAP |
| **File** | `src/agents/deterministic_technical.py:305, 389` |
| **Root Cause** | When VWAP unavailable (WebSocket down), `vwap=0.0` drops the VWAP factor entirely. Max bullish score drops from 6 to 5, making STRONG_BULL (net_bull >= 4) nearly impossible since it needs 4 out of 5 remaining factors. |
| **Fix** | When VWAP unavailable, lower STRONG_BULL threshold from 4 to 3 and STRONG_BEAR from -3 to -2. Signals are not penalized for missing data. |

---

### BUG-H4: Runner Mode Gets 0 Shares in Replay Simulator

| Field | Value |
|-------|-------|
| **Severity** | P1 — runner feature completely broken in replay |
| **File** | `scripts/replay_optimizer.py:610-635` |
| **Root Cause** | `runner_qty = 0` at initialization. T3 sell computed `sell_qty = remaining_qty` (since `not runner_mode`), selling everything. Then `runner_mode=True` activated with `runner_qty = remaining_qty = 0`. |
| **Fix** | Pre-compute `runner_qty = max(1, int(qty * runner_pct))` at init. T3 sell reserves those shares: `sell_qty = remaining_qty - runner_qty`. |

---

### BUG-H7: Market Hours Filter Admits Pre-Market Bars

| Field | Value |
|-------|-------|
| **Severity** | P1 — replay uses 9:00-9:29 ET bars |
| **File** | `scripts/replay_optimizer.py:813`, `src/arena/strategy_arena.py:457` |
| **Root Cause** | Filter `b.dt.hour >= 13` admits 13:00-13:29 UTC (9:00-9:29 ET), which is pre-market. Market opens at 13:30 UTC. |
| **Fix** | Changed to `b.dt.hour > 13 or (b.dt.hour == 13 and b.dt.minute >= 30)`. |

---

### BUG-L1: `_phase2_start` Undefined on Late Starts

| Field | Value |
|-------|-------|
| **Severity** | P3 — NameError crash on late start |
| **File** | `main.py:1503` |
| **Root Cause** | `_phase2_start` only defined inside Phase 2 block (line 1086). If system starts during market hours, Phase 3 check at line 1503 references it before Phase 2 ever runs. |
| **Fix** | Initialize `_phase2_start = 0` before the main loop. |

---

### BUG-L3: Arena Re-Run Double-Counts Totals

| Field | Value |
|-------|-------|
| **Severity** | P3 — stale variant data on re-run |
| **File** | `src/arena/strategy_arena.py:591` |
| **Root Cause** | `run_tournament()` accumulates `total_pnl += psr.total_pnl` on existing `StrategyVariant` objects. Calling it twice doubles all totals. |
| **Fix** | Reset `self._variants = {}` at the start of each tournament run. |

---

### BUG-L4: Draw Threshold Hardcoded to $138k Equity

| Field | Value |
|-------|-------|
| **Severity** | P3 — wrong threshold for different equity sizes |
| **File** | `src/arena/strategy_arena.py:637` |
| **Root Cause** | `equity = 138_000.0` hardcoded in `_record_matchup()`. Draw threshold (0.1% of equity) is wrong for different equity sizes. |
| **Fix** | `StrategyArena.__init__` accepts `equity` parameter, used in `_record_matchup`. |

---

## 2026-03-21 (Sweep Round 6 — Cross-Cutting Integration Audit)

### BUG-X1: D99 PnL Double-Count (P0)

| Field | Value |
|-------|-------|
| **Severity** | P0 — inflates realized losses by 2x on non-Shapley stops |
| **File** | `main.py:1977-1979` |
| **Root Cause** | `close_position_with_attribution` (position_manager.py:798-804) already removes the position and records PnL, even when returning None (no Shapley data). The D99 fallback then called `record_realized_pnl()` and `remove_position()` again, double-counting PnL into the circuit breaker. |
| **Fix** | Removed duplicate `record_realized_pnl()` and `remove_position()` calls. Kept metrics gauge update (bridge.py skips it when enriched=None). |

---

### BUG-X2: `scored=None` Always Passed — Shapley/Kelly/Win-Loss Dead (P0)

| Field | Value |
|-------|-------|
| **Severity** | P0 — Shapley attribution, Kelly tier tracking, and win/loss metrics permanently disabled |
| **Files** | `main.py:1363,2484,2765`, `src/core/orchestrator.py` |
| **Root Cause** | All 3 entry paths (main eval, VWAP, Rescan) passed `scored=None` to `bridge.execute_verdict()`. The bridge never cached a ScoredCandidate, so `close_position_with_attribution` always returned None. Result: bridge.py metrics (session_trades, win_count, loss_count) and Kelly tier tracking via TradeResultTracker were permanently dead. |
| **Fix** | Added `_scored_by_ticker` cache to orchestrator (populated at MFCS compute time). All 3 entry paths now retrieve `orchestrator._scored_by_ticker.get(verdict.ticker)` and pass to bridge. |

---

### BUG-X3: Stop Resubmit Qty Double-Decrement (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — stop orders submitted with too-low qty after tranche fills |
| **File** | `main.py:1869` |
| **Root Cause** | `tranche_monitor.on_fill()` already decrements `position.remaining_qty`. The stop ratchet code then did `new_qty=pos.remaining_qty - to.qty`, subtracting again. |
| **Fix** | Changed to `new_qty=pos.remaining_qty` (already decremented). |

---

### BUG-X4: `stop_order_id` Not Transferred to ManagedPosition (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — StopResubmitter can't find/cancel the original stop on crash recovery |
| **File** | `src/execution/bridge.py:251` |
| **Root Cause** | `OrderResult` has `stop_order_id` and `ManagedPosition` has the field, but bridge.py never passed it during construction. |
| **Fix** | Added `stop_order_id=order_result.stop_order_id` to ManagedPosition construction. |

---

### BUG-X5: `fill_price` Never Populated on OrderResult (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — entry price always uses limit order price, not actual fill price |
| **File** | `src/execution/alpaca_executor.py:257` |
| **Root Cause** | `OrderResult` had no `fill_price` field. Added in earlier sweep but `execute()` never set it from Alpaca response. Bridge.py's `hasattr` guard always fell back to `verdict.entry_price`. |
| **Fix** | Added `fill_price: float = 0.0` field, populated from `response.get("filled_avg_price")` for instant fills. Falls back to verdict.entry_price when 0.0 (pending orders filled via WebSocket). |

---

### BUG-X6: Fast-Path Missing D100 Stop Conversion + Tranches (P0)

| Field | Value |
|-------|-------|
| **Severity** | P0 — fast-path entries have OTO stops that block ALL tranche sells (403 errors) |
| **File** | `main.py:1107-1147` |
| **Root Cause** | Fast-path entry path registered positions but never performed: (1) D100 stop conversion (cancel OTO stop, submit standalone), (2) tranche order submission, (3) stop registration with StopResubmitter. OTO stops block partial sells → 403 → profit-taking never works on fast-path entries. |
| **Fix** | Added full D100 infrastructure to fast-path: cancel OTO stop, submit standalone stop, submit tranche limit sells, register with StopResubmitter. Mirrors the regular Phase 2 entry path. |

---

### BUG-E1: Tranche Size = 0 When qty=1-2 (P0, Arena Backtest)

| Field | Value |
|-------|-------|
| **Severity** | P0 — all tranche exits skipped for small positions |
| **File** | `scripts/replay_optimizer.py:554` |
| **Root Cause** | `tranche_size = qty // 3` = 0 when qty is 1 or 2. All tranche exit logic uses `if tranche_size > 0`, so profit-taking is completely disabled for small positions. |
| **Fix** | `tranche_size = max(1, qty // 3)` |

---

### BUG-E5: Exit Intelligence API Mismatch — Silent No-Op in Backtests (P0, Arena Backtest)

| Field | Value |
|-------|-------|
| **Severity** | P0 — exit intelligence signals never computed in any backtest |
| **File** | `scripts/replay_optimizer.py:704-730` |
| **Root Cause** | `compute_exit_signals()` call had 4 wrong parameters: missing `ticker` (required first arg), used `bar_history` instead of `bars`, passed nonexistent `peak_price`/`time_held_minutes`/`session_high` kwargs. Every call threw `TypeError`, caught by bare `except Exception: pass`. Exit signal-based exits never fired in arena tournaments — all exit decisions were purely tranche/stop/EOD. |
| **Fix** | Fixed API call to match actual signature. Used `setup.ticker`, renamed `bar_history` → `bars`, replaced nonexistent params with `hour_et`/`minute_et`. Changed return value access from `.get("composite")` to `.composite_exit_urgency`. |

---

### BUG-E4: Polars Division by Zero in Scanner Gap Calculation (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — stock with previous_close=0 gets Inf gap_pct, passes all filters |
| **File** | `src/scanners/premarket.py:178-182` |
| **Root Cause** | Polars expression `(current_price - previous_close) / previous_close` doesn't guard against `previous_close=0`. While `compute_gap_percent()` function guards this, the DataFrame vectorized path did not. |
| **Fix** | Wrapped denominator in `pl.when(col > 0).then(col).otherwise(1.0)`. |

---

### BUG-E6: Float=0 Treated as Large-Cap (P2)

| Field | Value |
|-------|-------|
| **Severity** | P2 — zero-float stocks sized as large-cap instead of micro-float |
| **File** | `src/core/orchestrator.py:2396` |
| **Root Cause** | `float_shares = candidate.float_shares or 50_000_000` — Python `or` treats 0 as falsy, so zero-float maps to 50M (large-cap), inverting the sizing multiplier from 1.5x to 0.8x. |
| **Fix** | Changed to explicit `if candidate.float_shares is not None`. |

---

## 2026-03-21 (Sweep Round 7 — Cross-System Lifecycle Audit)

### BUG-L3: Tranche State Not Restored on Recovery (P0)

| Field | Value |
|-------|-------|
| **Severity** | P0 — post-recovery tranche fills regress stop ratchet and break full-close detection |
| **File** | `src/execution/tranche_monitor.py:107,177` |
| **Root Cause** | `_ticker_tranches` dict starts empty on restart. After recovery restores positions with `tranches_filled=2`, the next fill sets `prev_tranches=0`, `new_tranches=1`, overwriting `position.tranches_filled` from 2 to 1. Stop ratchets to wrong level; position never detected as fully closed. |
| **Fix** | Added `restore_tranche_state()` method that syncs `_ticker_tranches` from recovered positions. Called after `sync_from_state_and_orders` in main.py. |

---

### BUG-L4: Orphaned Stop Order After Full Tranche Close (P0)

| Field | Value |
|-------|-------|
| **Severity** | P0 — broker executes stop-sell on already-exited position → accidental short |
| **Files** | `src/execution/tranche_monitor.py:207-224`, `main.py:1936` |
| **Root Cause** | When all 3 tranches fill, `tranche_monitor` removed the position from PM but never canceled the broker-side stop order or cleaned up `StopResubmitter` tracking. The orphaned stop could execute, creating an unintended short position. |
| **Fix** | Injected `stop_resubmitter` into `TrancheExitMonitor`. On full close: cleans up tracking, returns `orphaned_stop_oid` in `RatchetResult`. Main.py cancels the orphaned stop at the broker. |

---

### BUG-L5: Phantom PnL on Double-Close via Tranche + Bridge (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — phantom PnL recorded when close_with_attribution called on already-closed position |
| **File** | `src/execution/bridge.py:309-311` |
| **Root Cause** | If `tranche_monitor` fully closed a position, `bridge.close_with_attribution()` found `_pos=None` but defaulted `_pos_qty=1`. Then `enriched.pnl * 1` recorded per-share PnL as if 1 share was closed, double-counting into daily metrics and win/loss counters. |
| **Fix** | Added early return when `_pos is None` — logs and returns None, pops stale scored cache. |

---

### BUG-O1: Debate Fallback Path Skips _safe_judge_float (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — LLM string prices like "$4.50" crash downstream arithmetic |
| **File** | `src/agents/debate_engine.py:487-489` |
| **Root Cause** | Primary judge path uses `_safe_judge_float()` to sanitize LLM outputs. Fallback path passed raw `entry_price`, `stop_loss`, `target_prices` values, allowing strings like "$4.50" to flow into orchestrator math. |
| **Fix** | Applied `_safe_judge_float()` to all three fields in fallback path, matching primary path. |

---

### BUG-O2: Bridge Never Extracts catalyst_type/gap_pct/manipulation_phase from ScoredCandidate (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — exit strategies always see catalyst="unknown", gap=0.0, manipulation="UNCERTAIN" |
| **Files** | `src/execution/bridge.py:206-212` |
| **Root Cause** | `getattr(scored, "catalyst_type", "unknown")` — but `ScoredCandidate` has no such field. These live on `scored.candidate` (a `CandidateStock`). `getattr` always returned defaults. Also checked `TradeVerdict` for fields it doesn't have (dead code). |
| **Fix** | Extract from `scored.candidate` for catalyst_type/gap_pct. Extract manipulation_phase from `ManipulationSignal` in `scored.agent_signals`. Also check `verdict.catalyst_profile.catalyst_type`. |

---

### BUG-O3: Slippage Metric Always 0 (P2)

| Field | Value |
|-------|-------|
| **Severity** | P2 — fill_slippage_bps metric is dead |
| **File** | `src/execution/bridge.py:267-269` |
| **Root Cause** | Compared `submitted_price` vs `verdict.entry_price` — both are always identical (set from same value). Real slippage (fill vs intended) never measured. |
| **Fix** | Use `order_result.fill_price` when > 0, fallback to `submitted_price`. |

---

### BUG-A6: Arena Exit Intelligence hour_et Computed Wrong (High, Arena Backtest)

| Field | Value |
|-------|-------|
| **Severity** | High — time_decay signal systematically wrong for non-9AM entries |
| **File** | `scripts/replay_optimizer.py:711-713` |
| **Root Cause** | `_hour_et = 9 + int(_time_held // 60)` assumed entry always at 9AM. An 11AM entry with 30min hold computed hour_et=9 instead of 11. Suppressed time_decay for afternoon entries. |
| **Fix** | Use `bar_et.hour` and `bar_et.minute` from the already-computed ET-converted bar time. |

---

### BUG-A4/A5: --exit-only Mode Drops Exit-Layer Fields (Medium, Arena)

| Field | Value |
|-------|-------|
| **Severity** | Medium — profiles with custom EOD close time or equity lose those settings |
| **File** | `scripts/run_arena.py:549-582` |
| **Root Cause** | `--exit-only` profile reconstruction omitted `eod_close_hour_et`, `eod_close_minute_et`, and `equity`. |
| **Fix** | Added all three fields to the profile copy. |

---

### BUG-P1: D78 Smart Exit Captures Position AFTER Close Removes It (HIGH)

| Field | Value |
|-------|-------|
| **Severity** | HIGH — `_exit_pos` always None, PnL defaults to 1 share |
| **File** | `main.py` (D78 smart exit path) |
| **Root Cause** | `close_with_attribution()` removes the position from `_positions` dict. Code then searched `positions` list for the ticker to get `remaining_qty`, but the position was already gone. `_exit_qty` always fell to default of 1. |
| **Fix** | Moved `_exit_pos` capture BEFORE `close_with_attribution()` call. |

---

### BUG-P5: D78 Smart Exit Does Not Cancel Orphaned Stop/Tranche Orders (HIGH)

| Field | Value |
|-------|-------|
| **Severity** | HIGH — orphaned tranche limit sells can fill after exit, creating accidental short positions |
| **File** | `main.py` (D78 smart exit path) |
| **Root Cause** | When ExitIntelligence triggers a smart exit, the code closed the position but left broker-side stop and tranche limit sell orders active. On a price rebound, these orphaned orders could fill, selling shares the system no longer owned. |
| **Fix** | Added stop/tranche order cancellation and cleanup before `close_with_attribution()`. Same pattern as tranche_monitor full-close cleanup. |

---

### BUG-P6: D98 Stop-Out Does Not Cancel Orphaned Tranche/Stop Orders (HIGH)

| Field | Value |
|-------|-------|
| **Severity** | HIGH — same orphaned order risk as BUG-P5 but in the D98 stop-out detection path |
| **File** | `main.py` (D98 stop-out path, ~line 2048) |
| **Root Cause** | When D98 detects a position is gone from the broker (stop-out), it closed the internal tracker but left tranche limit sell orders active. On a price rebound, these orphaned sells could fill against no position, creating accidental short positions. |
| **Fix** | Added stop_resubmitter removal, tranche order cancellation, and tranche_monitor cleanup before `close_with_attribution()`. |

---

### BUG-P7: Fast-Path Fire Skips Stopped-Out Ticker Check (MEDIUM)

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM — stopped-out tickers can re-enter via fast-path on crash recovery |
| **File** | `main.py` (fast-path fire section, ~line 1117) |
| **Root Cause** | The fast-path fire loop iterated all entries in `fast_path_queue` without checking `_stopped_out_tickers`. If a ticker was stopped out during D98 detection and then the system restarted, the fast-path queue (persisted in state) could re-fire an entry for the stopped-out ticker. |
| **Fix** | Added `_stopped_out_tickers` check at the top of the fast-path loop, skipping any ticker already stopped out. |

---

### BUG-P3: Fast-Path add_position Does Not Update Prometheus Gauge (MEDIUM)

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM — dashboard undercounts open positions for fast-path entries |
| **File** | `main.py` (fast-path fire section, ~line 1135) |
| **Root Cause** | `position_manager.add_position()` does not update the Prometheus `open_positions` gauge. Normal entries go through `bridge.execute_verdict()` which calls `bridge_metrics.open_positions.set()`, but fast-path entries bypass the bridge entirely. |
| **Fix** | Added `get_metrics().open_positions.set(len(position_manager.open_positions))` after each fast-path `add_position()`. |

---

### BUG-P9: WebSocket Subscriptions Never Updated for Phase 3 Tickers (HIGH)

| Field | Value |
|-------|-------|
| **Severity** | HIGH — VWAP breakout and rescan tickers use degraded prev_close VWAP proxy |
| **File** | `main.py` (Phase 3 VWAP/rescan paths), `src/data/websocket_client.py` |
| **Root Cause** | The market data WebSocket subscribes once at startup (line ~915) with the initial watchlist + recovered positions. Phase 3 VWAP breakout scanner and rescan discover NEW tickers not in the original watchlist. These tickers are never added to the WebSocket subscription, so `_get_vwap()` falls back to `prev_close` proxy — degrading breakout signal quality on gap-up stocks (prev_close < real VWAP). |
| **Fix** | Added `add_symbols()` method to `AlpacaWebSocketClient` that sends subscription messages on the existing connection for new symbols. Called after successful VWAP breakout and rescan order execution. |

---

### BUG-P10: Fast-Path Can Overwrite Recovered Positions (MEDIUM)

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM — overwrites position object, losing tranche state and accumulated PnL |
| **File** | `main.py` (fast-path fire section, ~line 1144) |
| **Root Cause** | If a position was recovered from state file during startup AND the fast-path queue still contains the same ticker, `add_position()` overwrites the recovered position with a fresh instance. This resets `tranches_filled`, `realized_pnl`, and other accumulated state. |
| **Fix** | Added `position_manager.has_position(fpe.ticker)` guard before `add_position()`, skipping tickers already loaded from recovery. |

---

## 2026-03-21 (Comprehensive Sweep Round 9)

### BUG-R1: WebSocket Reconnect Loses Dynamically Added Symbols (HIGH)

| Field | Value |
|-------|-------|
| **Severity** | HIGH — Phase 3 tickers lose real-time data after any WebSocket reconnect |
| **File** | `src/data/websocket_client.py` |
| **Root Cause** | `connect()` reconnects using only the original `symbols` parameter. Symbols added via `add_symbols()` (BUG-P9 fix) are stored in `_subscribed_symbols` but not included in the reconnection subscription. After any disconnect/reconnect, dynamically discovered Phase 3 tickers revert to prev_close VWAP proxy. |
| **Fix** | Reconnect now merges `_subscribed_symbols` with original symbols: `_all_symbols = list(set(symbols) | self._subscribed_symbols)`. |

---

### BUG-R3: compute_exit_tranches Creates 0-Share Tranche (MEDIUM)

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM — submitting 0-share sell order to broker fails or is rejected |
| **File** | `src/execution/position_manager.py` |
| **Root Cause** | When `remaining_qty=0` (all shares sold via tranches but position not yet removed), `compute_exit_tranches()` still creates a single tranche with `qty=0` because `0 < 3` enters the small-position branch. |
| **Fix** | Added early return: `if total_qty <= 0: return []` before tranche computation. |

---

### BUG-R4: Agent Signal Caches Bleed Across Evaluation Cycles (HIGH)

| Field | Value |
|-------|-------|
| **Severity** | HIGH — stale signals from previous candidate appear in journal entries for new candidates |
| **File** | `src/core/orchestrator.py` |
| **Root Cause** | `evaluate_candidates()` clears per-ticker caches (`_signals_by_ticker`, etc.) but not the `_last_*` fallback variables (`_last_agent_signals`, `_last_variant_map`, etc.). If an exception occurs during evaluation, the journal falls back to stale `_last_*` values from the previous cycle, recording wrong signals for the new candidate. |
| **Fix** | Added clearing of all `_last_*` variables at the start of `evaluate_candidates()`. |

---

### BUG-R5: Circuit Breaker Triggers Immediately with Zero Starting Equity (MEDIUM)

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM — any negative PnL triggers circuit breaker when equity=0 |
| **File** | `src/execution/position_manager.py` |
| **Root Cause** | `threshold = -starting_equity * daily_loss_limit_pct`. When `starting_equity=0`, threshold=0, so `daily_realized_pnl < 0` (any loss) triggers the breaker immediately, blocking all new entries. |
| **Fix** | Added guard: `if self._starting_equity <= 0: return False` — circuit breaker is disabled rather than hyper-sensitive with invalid equity. |

---

### BUG-R7: StopResubmitter Accepts qty=0 (MEDIUM)

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM — creates invalid 0-share stop order at broker |
| **File** | `src/execution/stop_resubmitter.py` |
| **Root Cause** | `resubmit()` accepts `new_qty` without validation. If `remaining_qty=0` is passed (e.g., after all tranches fill), it submits a 0-share stop order to Alpaca, which is either rejected or creates a broken order state. |
| **Fix** | Added `if qty <= 0: return StopResubmitResult(success=False, error="Invalid qty")` guard before cancel/resubmit sequence. |

---

### BUG-R8: Exit Intelligence OBV Bounds Check Asymmetry (LOW)

| Field | Value |
|-------|-------|
| **Severity** | LOW — near-zero OBV values could pass check and cause inflated divergence scores |
| **File** | `src/execution/exit_intelligence.py` |
| **Root Cause** | Guard used `first_half_obv_max == 0` (exact equality) while price guard used `<= 0` (inclusive). A very small positive OBV value (e.g., 0.0001) passes the check and is used as a denominator in divergence calculation, potentially producing extreme values. |
| **Fix** | Changed to `first_half_obv_max <= 0` for consistent bounds checking. |

---

---

## D121 Final Sweep (2026-03-21, Round 2)

Cross-system audit round 2 — focused on crash resilience, LLM output safety,
and code quality extraction to eliminate duplication.

### BUG-S1: Unsafe float() on LLM Bull/Bear Strength (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — crash if LLM returns non-numeric string |
| **File** | `src/agents/debate_engine.py` L341-342 |
| **Root Cause** | `float(raw.get("bull_strength") or 0.5)` crashes on non-numeric strings like "high" or "0.75/1.0". The `_safe_judge_float()` helper already existed for entry_price/stop_loss but wasn't used here. |
| **Fix** | Replaced with `_safe_judge_float(raw.get("bull_strength")) or 0.5` for both bull and bear strength. |

### BUG-S2: FillStreamBridge Crashes if StopResubmitter is None (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — crash on fill event if stop_resubmitter not injected |
| **File** | `src/execution/fill_stream_bridge.py` L179 |
| **Root Cause** | `drain_and_resubmit()` called `self._stop_resubmitter.resubmit()` without checking if `_stop_resubmitter` is None (it's Optional per constructor). Pending resubmits are queued from `on_trade_update()` regardless. |
| **Fix** | Added None guard at top of pending-resubmit loop — clears queue and returns early if no resubmitter. |

### BUG-S3: Phase 3 Crash on Corrupt opened_at Timestamp (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — crash halts Phase 3 stop-out detection |
| **File** | `main.py` L2039-2042 |
| **Root Cause** | `datetime.fromisoformat(pos.opened_at)` in D98 stop-out detection is not wrapped in try-except. A corrupt state file with malformed timestamp string crashes the entire Phase 3 loop. |
| **Fix** | Wrapped in try-except (ValueError, TypeError), falls back to current time (position enters grace period and proceeds normally next cycle). |

### BUG-S4: Fast-Path Stop Conversion Failure Leaves Position Unprotected (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — position has NO stop-loss after failed conversion |
| **File** | `main.py` L1170-1192 |
| **Root Cause** | Fast-path D100 stop conversion cancels the OTO stop (L1173), then submits a new standalone stop. If the new submission fails, `_fp_new_stop_oid` stays empty — the OTO stop was already canceled, so the position has no protection. |
| **Fix** | Added emergency stop re-submission after failure. If retry also fails, logs at CRITICAL level for manual intervention. |

### BUG-S7: session_state from_dict() Crashes on Malformed Values (P2)

| Field | Value |
|-------|-------|
| **Severity** | P2 — crash recovery fails on corrupt state file |
| **File** | `src/execution/session_state.py` L65-82 |
| **Root Cause** | `PositionState.from_dict()` uses naked `float()` and `int()` conversions. If state file has malformed numeric values (e.g., `"qty": "N/A"`), TypeError/ValueError crashes recovery. |
| **Fix** | Added `_safe_int()` and `_safe_float()` helpers with try-except and sensible defaults. |

### CQ-1: Extract TrancheExitMonitor.cancel_all_for_ticker()

| Field | Value |
|-------|-------|
| **Type** | Code Quality — DRY violation |
| **File** | `src/execution/tranche_monitor.py` + `main.py` |
| **Issue** | Tranche cancel + order_map cleanup was duplicated 3+ times in main.py (D78 smart exit, D98 stop-out, VWAP close). |
| **Fix** | Extracted `async def cancel_all_for_ticker(ticker, client)` method. Replaced inline blocks in D78 and D98 paths. |

### CQ-2: Extract StopResubmitter.cancel_and_remove()

| Field | Value |
|-------|-------|
| **Type** | Code Quality — DRY violation |
| **File** | `src/execution/stop_resubmitter.py` + `main.py` |
| **Issue** | Stop cancellation + tracking cleanup was duplicated across exit paths. |
| **Fix** | Extracted `async def cancel_and_remove(ticker)` method with broker cancel + internal cleanup. |

---

---

## D121 Final Sweep (2026-03-21, Round 3)

Deep audit of data layer, EOD close, crash recovery, shutdown, scripts, and
exit intelligence. Five new bugs fixed.

### BUG-S8b: Graceful Shutdown Does Not Persist State (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — ghost positions in state file after Ctrl+C |
| **File** | `main.py` L3314-3329 |
| **Root Cause** | Shutdown handler closes positions via `close_with_attribution()` but never calls `state_mgr.remove_position()` or `state_mgr.save()`. On next restart, recovery sees closed positions as still open. |
| **Fix** | Added `state_mgr.remove_position()` + `state_mgr.save()` after each shutdown close. |

### BUG-S9: EOD Close Missing Final Order Sweep (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — orphaned orders survive EOD close |
| **File** | `main.py` L1863 |
| **Root Cause** | D76 EOD close calls `cancel_all_orders()` at the START, but if concurrent fill handlers or WebSocket events submit new orders during the close loop, those orders are never canceled. Positions closed but tranche/stop orders from concurrent events left active. |
| **Fix** | Added final `cancel_all_orders()` call after all positions are closed, before marking `eod_close_completed=True`. |

### BUG-S10: replay_optimizer Division by Zero on mfcs_scaling_denom (P1)

| Field | Value |
|-------|-------|
| **Severity** | P1 — ZeroDivisionError crashes arena replay |
| **File** | `scripts/replay_optimizer.py` L460 |
| **Root Cause** | `mfcs / config.mfcs_scaling_denom` has no guard for `mfcs_scaling_denom=0`. Some profiles could inherit 0.0 from defaults. |
| **Fix** | Added guard: `_denom = config.mfcs_scaling_denom if config.mfcs_scaling_denom > 0 else 0.5`. |

### BUG-S11: OBV Divergence Extreme Values from Near-Zero Denominator (P2)

| Field | Value |
|-------|-------|
| **Severity** | P2 — exit signal spikes cause premature exits |
| **File** | `src/execution/exit_intelligence.py` L716 |
| **Root Cause** | Guard `abs(earlier_obv) > 0` allows very small OBV values (e.g., 0.001) as denominators, producing extreme divergence ratios (1000x+). The `min(1.0, ...)` clamp prevents infinity but still causes max urgency on non-meaningful OBV data. |
| **Fix** | Changed threshold from `> 0` to `> 1.0` to require meaningful OBV magnitude. |

### BUG-S12: WebSocket add_symbols on Closed Connection (P2)

| Field | Value |
|-------|-------|
| **Severity** | P2 — dynamic subscription silently fails |
| **File** | `src/data/websocket_client.py` L533 |
| **Root Cause** | `add_symbols()` checked `self._ws is None` but not `self._ws.closed`. After a WebSocket disconnect/reconnect cycle, `_ws` may point to a closed connection before the new one is established. Sending on a closed connection raises an exception caught by the broad try-except, silently losing the subscription. |
| **Fix** | Added `getattr(self._ws, 'closed', True)` check alongside the None check. |

---

## D122: Post-Freeze Transition (March 21, 2026)

External system evaluation scored the system 6.8/10 (8.5 architecture, 2.0 proven alpha). Zero systematic exits in 16 trading days. The configuration freeze was formally ended after accumulating 25 exceptions. Three code changes implemented, informed by diagnostic analysis.

### Diagnostic Findings

**Entry-Delay Sweep** (scripts/backtest.py, 90 days, 91 trades):
- Profit factor degrades with delay: 1.86 (bar 0) → 1.20 (bar 15)
- Win rate flat across delays (~64-68%) — system picks right stocks, enters late
- **41-47% of stops trigger after price had prior gain** — stops consistently too tight
- Conclusion: BOTH entry timing and stop distances are problems

**Signal History Analysis** (357 signals, 3 days):
- Parallel strategies would have fired EXIT on **93%** of all signals
- pullback: 325/357 EXIT (EXHAUSTED state on nearly all positions)
- catalyst_half_life: 307/357 EXIT (all positions have catalyst_type="unknown" → 20-min half-life)
- Conclusion: parallel engine needs confidence + multi-strategy gates before activation

**Experiment Data** (14,689 variants, 23 experiments, 10 days):
- `lambda_0.05` (MFCS scaling) performs best (avg MFCS 0.4257 vs 0.3969 baseline)
- ATR variants (1.5x-3.0x) show identical MFCS — ATR multiplier doesn't affect MFCS scoring
- Buy threshold variants show expected entry count tradeoff (thresh_0.15: 629 entries vs thresh_0.35: 410)

### D122-A: Parallel Exit Engine Activation

| Field | Value |
|-------|-------|
| **Files** | `config/settings.py`, `src/execution/exit_intelligence.py`, `main.py` |
| **Change** | Activated parallel exit strategies with UPGRADE-ONLY semantics + safety gates |
| **Safety Gates** | (1) `parallel_exit_min_confidence=0.7` — filters low-confidence fires; (2) `parallel_min_strategies_for_exit=1` — consensus gate dropped from 2→1 because correlated strategies (CatalystHalfLife + PullbackClassifier on stalled positions) don't provide independent signals; real safety is confidence threshold + stop_loss invariant; (3) `parallel_tighten_min_confidence=0.5` — TIGHTEN threshold |
| **TIGHTEN Math** | Uses ATR-grounded trail (1.5× ATR below current price), NOT confidence-based percentage. Confidence-based trail was dangerously tight (1% at conf=1.0 = $0.05 on $5 stock). |
| **Logging** | Logs when TIGHTEN is skipped due to ratchet-up invariant (new_stop < current stop) for calibration. |
| **Rollback** | Set `parallel_strategies_active=False` in config. Instant revert. |

### D122-B: Catalyst Date Staleness Fix (Enhanced)

| Field | Value |
|-------|-------|
| **File** | `src/core/orchestrator.py` |
| **Root Cause** | BUG-008 (MOBX Day 4, -$9.4%) — stale "Anti-Drone" news from prior session treated as fresh catalyst. D117 added scanner-level pub-time filter, but recap articles that are technically "new" but describe yesterday's move were not caught. |
| **Fix** | Two-layer filter: (1) pub-time < previous 4PM ET, (2) recap headline detection via regex (past-tense price verbs: soared, surged, jumped, etc. + "here's why", "what happened"). |
| **Location** | Called at top of `_evaluate_candidate_inner()` before any agent dispatch. |

### D122-C: Gap-Day Stop Widening

| Field | Value |
|-------|-------|
| **File** | `src/core/orchestrator.py` (`_compute_atr_stop`) |
| **Root Cause** | Days 11-14 all stopped out in 15 minutes. 2.0× ATR_14d on a stock gapping 10%+ is too tight — intraday volatility on gap days is 2-3× the daily average. Entry-delay sweep confirmed 41-47% false stop-out rate. |
| **Fix** | On gap-ups > 8%, use `gap_pct × 0.5` as minimum stop distance (half the gap). Existing 20% cap still applies. Example: 20% gap → 10% stop floor instead of 4% ATR-based. |
| **Config** | `gap_day_stop_widening_enabled=True`, `gap_day_threshold=0.08`. Disable via config. |

### D122-D: Deflation Replay Bug Fix

| Field | Value |
|-------|-------|
| **Files** | `src/core/models.py`, `src/agents/base.py`, `src/core/orchestrator.py` |
| **Root Cause** | `confidence_deflation_factor` is applied upstream in `base.py` and baked into `signal.confidence` before experiment replay receives the signals. Variant overrides to deflation factor had zero effect — all deflation variants produced identical MFCS (0 delta). ~3000 wasted experiment records. |
| **Fix** | Added `raw_confidence` field to `AgentSignal` (pre-deflation value). `base.py` now stores raw confidence alongside deflated. `_replay_experiment_variants()` detects when a variant's deflation differs from primary, rebuilds signals with `raw_confidence × variant_deflation`, then scores. Deterministic agents (no `raw_confidence`) pass through unchanged. |
| **Impact** | Deflation sweep experiments will now produce meaningful data — different deflation factors will yield genuinely different MFCS scores, enabling proper calibration of the LLM overconfidence correction. |

### D122-E: Consensus Gate (2→1) + Strategy Exclusion List

| Field | Value |
|-------|-------|
| **Files** | `config/settings.py`, `src/execution/exit_intelligence.py`, `main.py` |
| **Root Cause** | `parallel_min_strategies_for_exit=2` reintroduced consensus trap (correlated signals don't give independent confirmation). However, dropping to 1 without filtering exposed a deeper problem: PullbackClassifier fires EXIT at 92% rate (EXHAUSTED state on flat positions, not just exhausted advances) and CatalystHalfLife fires at 85% (all positions have `catalyst_type="unknown"` → 20-min half-life). At min=1 + confidence>=0.7, the system would EXIT on **92% of all evaluation cycles**. |
| **Fix** | (1) Default min_strategies set to 1 (consensus gate principle is wrong). (2) Added `parallel_exit_excluded_strategies = ["pullback", "catalyst_half_life"]` — excludes the two broken strategies from triggering EXIT/TIGHTEN until underlying bugs are fixed. (3) Remaining 4 strategies (velocity, volume_exhaustion, gratitude, alpha_oracle) fire at **2.8% combined rate** — genuinely selective. |
| **Unblock** | Remove strategies from exclusion list after fixing: (a) catalyst_type propagation (D111 bug — positions always get "unknown"), (b) PullbackClassifier EXHAUSTED-on-flat-positions — **fixed in D122-H** but keep excluded until validated in live trading. |
| **Active strategy validation** | Signal history analysis of the 4 active strategies: 10/355 fires (2.8%), **9/10 correct** (position was losing), 1 false positive (GratitudeExit at +1.1%). Zero co-fires between any two active strategies → min_strategies=2 would make engine completely inert. CHNR case: alpha_oracle would have exited at -1.2% instead of legacy -2.1% (0.9% improvement). |

### D122-H: PullbackClassifier Minimum Advance Gate

| Field | Value |
|-------|-------|
| **File** | `src/execution/exit_strategies.py` |
| **Root Cause** | The 30% PULLBACK and 50% EXHAUSTED retracement thresholds are relative to advance size. A $0.01 advance on a $5 stock (0.2%) counts as a valid advance, so a $0.005 normal fluctuation is a "50% retracement" triggering EXHAUSTED. Result: 325/355 (92%) cycles in terminal EXHAUSTED state — the strategy measures noise, not momentum exhaustion. |
| **Fix** | Added `MIN_ADVANCE_PCT = 0.01` (1% of entry price). Retracement tracking doesn't start until advance exceeds this floor. EXHAUSTED state triggered by sub-threshold advance resets to ADVANCING. Example: $10 stock must advance to $10.10 before pullback tracking begins. |
| **Status** | Fixed but kept on exclusion list for Monday — validate in live signal history before unblocking. |

### D122-F: AlphaDecayOracle Minimum Evaluation Time

| Field | Value |
|-------|-------|
| **File** | `src/execution/exit_strategies.py` |
| **Root Cause** | At minute 0, observed return ≈ 0% but null curve says gap-up stocks should be at +3.0% by minute 5. Alpha = 0% - 3.0% = -3.0%, firing EXIT on every position within the first minute. Confidence is 0.7 (hardcoded formula: `0.5 + abs(-3.0) * 0.1 = 0.8`), so the confidence gate doesn't catch it. Phase 3 replay simulator confirmed: without this fix, every position exits at bar 0. |
| **Fix** | Added `MIN_EVALUATION_MINUTES = 5` — oracle returns HOLD for all evaluations before minute 5 (the first point on the null curve). At minute 0, there's no observation to compare against the null expectation. |

### D122-G: Pre-Monday Validation Tooling

| Field | Value |
|-------|-------|
| **Files** | `scripts/phase3_replay_simulator.py`, `scripts/monte_carlo_analysis.py`, `tests/property/test_d122_properties.py` |
| **Purpose** | Three validation tools added: (1) Phase 3 replay simulator — replays historical/synthetic positions through complete D122 exit pipeline, comparing legacy vs D122 outcomes. Supports confidence threshold sweep and ATR stop multiplier sweep. (2) Monte Carlo bootstrap analyzer — computes 95% CI on profit factor, win rate, Sharpe from backtest CSV. Includes CPCV (PBO and DSR graduation criteria). Signal history confidence distribution analyzer for pre-session gate calibration. (3) Hypothesis property-based tests — 26 tests verifying system invariants (upgrade-only semantics, monotonicity, confidence bounds, strategy-count monotonicity) across thousands of random market inputs. |

---

## D125 — March 24: PTLE Missed Trade + Consensus Gate Recalibration

### D125-A: Spread Filter Blocked 20% Winner (PTLE)

| Field | Value |
|-------|-------|
| **File** | `src/execution/bridge.py` lines 118-147, `config/settings.py` |
| **What happened** | PTLE gapped +52% on legitimate catalyst (merger termination). System produced 4 BUY verdicts (MFCS=0.257-0.287, news=BULL, technical=STRONG_BULL). All 4 blocked by bid-ask spread filter: 2.65-6.04% vs 1.0% max. PTLE then ran +20% from open by 10:00 AM. |
| **Root cause** | Fixed 1.0% spread threshold treats all wide spreads equally. On a stock running 20%, a 2.65% spread cost is acceptable (expected profit >> spread cost). On a manipulated pump, 2.65% spread is a trap. The filter can't distinguish the two. |
| **Design change** | Context-aware spread threshold: when MFCS > debate threshold AND news agent is BULL with confidence > 0.3 (real catalyst detected), widen the spread tolerance to `gap_day_spread_max` (default 3.0%). This accepts the spread cost when the expected move is large enough to absorb it. On stocks without strong catalyst signal, keep strict 1.0% max. |
| **Why not just raise the threshold** | Raising to 3.0% globally would let through illiquid pump-and-dump stocks that regularly appear with 2-4% spreads and no catalyst. The gate must be selective: wide spreads are acceptable ONLY when catalyst quality is high. |
| **Config** | `gap_day_spread_max: float = 0.03` — applied when news=BULL + MFCS > threshold |

### D125-B: Consensus Gate + D94 Fundamental Agent Skip

| Field | Value |
|-------|-------|
| **File** | `src/agents/fundamental_agent.py` |
| **Root cause** | `fundamental_agent` returned NEUTRAL on every small-cap ("Float size is unknown") but without `D94_NO_DATA_SKIP` flag. This counted as "analyzed and found neutral" in the consensus denominator, raising the bar from 2-of-3 to 2-of-4 directional agents. Most small-cap gappers have no float/filing data — the agent was consuming a Tier 2 LLM call to return the same NEUTRAL every time. |
| **Fix** | When `float_shares`, `short_interest`, `recent_filings`, and `shares_outstanding` are all unavailable, return NEUTRAL with `D94_NO_DATA_SKIP` flag and skip the LLM call. This reduces the consensus denominator on small-caps, making news+technical alignment sufficient. |

### D125-C: Execution-Blocked Observability

| Field | Value |
|-------|-------|
| **File** | `main.py` |
| **Problem** | Phase 2 verdict summary showed ">>> PTLE: BUY" without indicating the spread rejection. Led to incorrect diagnosis that "BUY verdicts don't produce orders." The spread rejection log existed (`D101 SPREAD REJECT`) but wasn't connected to the verdict summary. |
| **Fix** | Added `D125 EXECUTION BLOCKED: {ticker} BUY verdict (MFCS=X) rejected by execution layer` log at WARNING level immediately after `execute_verdict()` returns None. |

---

## 2026-03-24 — D125: Two Zero-Trade Days

### D125-A: Fundamental Agent Consensus Inflation

| Field | Value |
|-------|-------|
| **Severity** | HIGH |
| **File** | `src/agents/fundamental_agent.py` |
| **Symptom** | Every candidate blocked by D101 consensus gate: "1 directional agents < 2 minimum." System produced 0 trades on March 24 despite evaluating 157 candidates and producing 4 BUY verdicts for PTLE. |
| **Root Cause** | `fundamental_agent` returns NEUTRAL with confidence 0.50 on every small-cap because "float size is unknown." The consensus gate counts this as "analyzed and found neutral" rather than "had no data," inflating the denominator. With 4 non-skip agents, min_directional=2 requires 2 of 4 to be bullish — effectively requiring news + technical agreement. News is sparse on small-caps. |
| **Fix** | Added `D94_NO_DATA_SKIP` flag to `fundamental_agent` when float, filings, and short interest are all unavailable. Skips the wasted Tier 2 LLM call and reduces consensus denominator. Also dropped `min_directional_agents` from 2 to 1 — the real safety nets are MFCS threshold + D124 consensus alignment + D106 manipulation classifier + stop loss. |

### D125-B: BUY Verdicts Not Producing Orders (PTLE)

| Field | Value |
|-------|-------|
| **Severity** | HIGH (initially diagnosed as P0, turned out to be correct behavior) |
| **File** | `src/execution/bridge.py` lines 118-147 |
| **Symptom** | PTLE received 4 BUY verdicts (09:30-09:34) but 0 orders were submitted. The verdict summary showed ">>> PTLE: BUY" with no indication of why the order was blocked. |
| **Root Cause** | The ExecutionBridge spread filter correctly rejected all 4 entries: PTLE had 2.65%-6.04% bid-ask spread vs 1.0% max threshold. Entering a 2.65% spread stock means starting down 2.65% instantly. This was CORRECT risk management, but the rejection was only logged at WARNING level in bridge.py, not visible in the Phase 2 verdict summary. |
| **Fix** | (1) Added "D125 EXECUTION BLOCKED" log at INFO level after `bridge.execute_verdict()` returns None. (2) Added context-aware spread: entries with MFCS >= 0.15 get 3.0% spread tolerance instead of 1.0%. |

### D125-C: Main Repo Not Updated (3 Days of Undeployed Fixes)

| Field | Value |
|-------|-------|
| **Severity** | CRITICAL |
| **Root Cause** | All D123-D125 commits were pushed to `origin/develop` but the main repo checkout at `<repo-root>` was never pulled. Task Scheduler runs from this directory. The local checkout was 8 commits behind — still on D122 (commit 8e14006). None of the observability, stop intent, consensus alignment, gap cap, min_directional=1, or fundamental skip fixes were running for 3 trading days. |
| **Fix** | `cd main-repo && git pull origin develop`. Added to deployment checklist: always verify `git log --oneline -1` in the main repo matches the latest push. |
| **Impact** | 3 zero-trade days that could have been 1 zero-trade day (Monday would still have been zero due to ANNA spread, but Tuesday and Wednesday might have traded with the fixes active). |

---

## 2026-03-25 — D126: Gap-Up Momentum Mode

### D126-A: Technical Agent BEAR on Gap-Up Winners

| Field | Value |
|-------|-------|
| **Severity** | CRITICAL — structural design flaw preventing all trading |
| **File** | `src/agents/deterministic_technical.py` |
| **Symptom** | 8 of 13 watchlist stocks were BIG WINNERS (+22% to +136%). Technical agent gave BEAR signals on 6 of them at market open. System caught zero. |
| **Root Cause** | Factor voting system uses MACD histogram, EMA(9)/EMA(21) crossover, and VWAP position — all lagging indicators that are structurally negative during the first 5-10 minutes after a gap-up open. A stock gapping +50% pulls back 1-2% at open, making MACD negative (-1), EMA unaligned (-1), price below VWAP (-1). net_bull = -2 → BEAR. Additionally, D104 time decay sets confidence to 0.0 at T+0, killing the signal at peak momentum. |
| **Fix** | D126 Gap-Up Momentum Mode: When gap_pct × rvol > 1.0 (with floors gap > 8%, rvol > 2.5x), skip MACD and EMA factors. Use gap momentum base (+2 bullish) plus real-time indicators (VWAP, RSI). No RSI > 75 penalty in momentum mode. Gradual decay: full strength 0-10 min, linear blend to normal 10-30 min. Time decay override: 0.8 at T+0 (not 0.0), 1.0 by T+5. |
| **Validation** | 6 of 8 March 25 winners would have triggered momentum mode. |

### D126-B: gap_pct Not Wired to Technical Agent

| Field | Value |
|-------|-------|
| **Severity** | HIGH (would have made D126 silently inert) |
| **Files** | `src/execution/fast_path.py`, `src/core/orchestrator.py` (3 sites) |
| **Root Cause** | The technical agent's `evaluate_signal()` receives kwargs but `gap_pct` was never passed from any of the 4 call sites. Without it, `_safe_float(kwargs.get("gap_pct"), 0.0)` returns 0.0, momentum_score = 0.0, and gap momentum mode never activates. |
| **Fix** | Added `gap_pct=c.gap_pct` (fast_path) and `gap_pct=candidate.gap_pct` (orchestrator ×3) to all 4 call sites. Critical: the fast_path site at L308 is the most important — it handles the 9:30:01 entries. |

### D127 (2026-03-26): First Trading Day — Four Critical Failures

### D127-A: Tier1 LLM Returning Empty Responses

| Field | Value |
|-------|-------|
| **Severity** | P0 — silently sabotaged ALL evaluations |
| **Root Cause** | Qwen3.5-397B-A17B on Together AI returns empty string on calls with system prompts. Confirmed with 4/4 live API test calls. News agent received empty response, couldn't parse JSON, fell back to NEUTRAL. This produced "0 directional agents" on most evaluations across multiple sessions. |
| **Impact** | Unknown duration — may have been broken for days/weeks. Every "0 directional agents" log entry was potentially caused by this. |
| **Fix** | Switched Tier1 to Qwen3-Next-80B-A3B-Instruct (1.6s latency, valid JSON, BULL 0.85 on test). Fallback to Llama-4-Maverick-17B. |

### D127-B: position_intent="close" Invalid on Alpaca Paper API

| Field | Value |
|-------|-------|
| **Severity** | P0 — all positions ran without stop protection |
| **Root Cause** | D124 added `position_intent="close"` to stop orders to prevent short-sell rejection. Alpaca Paper API rejects this field with 422 "invalid position_intent specified". OTO stop canceled, standalone failed, retry failed. JBLU/SRPT/RMSG all UNPROTECTED for 30 min. |
| **Fix** | Disabled position_intent in payload. Stop orders use normal `side="sell"`. |

### D127-C: MKDW Corrupted by Trailing-W Symbol Cleaning

| Field | Value |
|-------|-------|
| **Severity** | P1 — missed biggest winner 2 days in a row |
| **Root Cause** | `_clean_derivative_symbols()` trailing-W rule: MKDW (real ticker) -> MKD (doesn't exist). MKDW was +59% on both Mar 25 and Mar 26. |
| **Fix** | Removed trailing-W rule entirely. Only .WS/.RT/.U/.UN suffixes cleaned. |

### D127-D: BUY Execution Order Not Momentum-Ranked

| Field | Value |
|-------|-------|
| **Severity** | P1 — wrong stocks traded first |
| **Root Cause** | BUY verdicts executed in evaluation order (roughly by gap size from scanner). JBLU (9.9% gap, MFCS 0.256) executed before UGRO (360% gap, MFCS 0.184) because MFCS rewards news coverage. |
| **Fix** | BUY verdicts sorted by momentum score (gap × rvol) before execution. MKDW (score 864) now executes before JBLU (score 0.4). |

---

*This document is updated after each live session with new findings.*
