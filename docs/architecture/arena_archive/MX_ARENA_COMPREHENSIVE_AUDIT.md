# mx-arena: Comprehensive Audit & Path to 10/10

**Date**: March 27, 2026 (D132)
**Current Score**: 7.5/10 (execution sim + decision replay, with critical bugs)
**Audited by**: Deep code-level inspection of every component

---

## Part 1: Honest Component Scores

| Component | Score | Critical Issues |
|-----------|-------|-----------------|
| SimClock | 9/10 | Minor: no holiday calendar, no half-day sessions |
| SimExchange | 7/10 | No order rejection on insufficient cash, day TIF cancel logic fragile |
| AlpacaFillModel | 6/10 | Price improvement bug (optimistic 50-200 bps), no buy trailing stops |
| SpreadModel | **3/10** | **SHOWSTOPPER: UTC vs ET timezone bug makes spreads 4-6x wrong at open** |
| DataEngine | 6/10 | Bar indexing by sequence not timestamp, no gap handling, no validation |
| DecisionReplay | 6/10 | Only replicates 1 of 50+ signal types from real technical agent |
| TradeSimulation | 5/10 | Entry fill logic too generous (2% slippage), stop fills at exact price |
| REST API | 8/10 | Screener endpoints fake, PDT detection wrong |
| WebSocket | 7/10 | Functional but not tested in integration with bot |
| Tests (101) | 8/10 | Good happy-path coverage, missing edge cases and integration |

### Weighted Overall: 6.5/10 (honest) to 7.5/10 (generous)

---

## Part 2: Critical Bugs Found

### BUG 1: Spread Model Timezone (SHOWSTOPPER)

**File**: `arena/spread_model.py:69`
```python
hour = timestamp.hour + timestamp.minute / 60.0
```
The timestamp is UTC (from SimClock). `.hour` reads UTC, not ET. At 09:30 ET (13:30 UTC),
the model sees hour=13.5 and applies 1.0x midday multiplier. Should apply 2.5x opening
multiplier.

**Impact**: Spreads are 4-6x too tight at market open (when all entries happen) and 2x too
wide at close. Every fill price in every simulation is wrong. The Mar 26 validation results
($+16.94 P&L) are optimistic because entries got phantom-tight spreads.

**Fix**: One line — `timestamp.astimezone(ET)` before extracting hour.

### BUG 2: Price Improvement on Limit Orders (Optimistic)

**File**: `arena/fill_model.py:101-102`
```python
price = min(order.limit_price, ask)  # Buy
price = max(order.limit_price, bid)  # Sell
```
This gives price improvement on every limit fill. Alpaca paper fills at NBBO, not better.
A limit buy at $5.50 when ask is $5.45 should fill at $5.45 (correct), but the `min()`
also means a limit at $5.50 when ask is $5.48 fills at $5.48 instead of $5.50. The net
effect is 50-200 bps phantom profit on entries.

**Fix**: Fill at `ask` for buys, `bid` for sells (NBBO, no improvement).

### BUG 3: Entry Fill Detection in Trade Simulation (Too Generous)

**File**: `arena/runner.py:170-172`
```python
if bar.low <= entry_price * 1.02:  # Allow 2% slippage
    fill_price = min(bar.open, entry_price * 1.01)
```
Allows 2% slippage on entry AND fills at min(open, entry+1%). This means a stock with
entry $5.00 and bar open $4.50 fills at $4.50 — a phantom 10% price improvement. Should
fill at ask price when bar crosses limit.

**Fix**: Fill when `bar.low <= entry_price`, at the ask price from spread model.

### BUG 4: Stop Exit at Exact Price (Optimistic)

**File**: `arena/runner.py:197-199`
```python
if stop_loss > 0 and bar.low <= stop_loss:
    exit_price = stop_loss
```
Assumes perfect fill at stop price. In reality, stops fill at market when triggered —
which on a gap-down can be significantly below stop. Should fill at bid when triggered,
not at stop price.

**Fix**: `exit_price = bid` (from spread model) when stop triggers, not `stop_loss`.

### BUG 5: Bar Indexing by Sequence (Silent Data Loss)

**File**: `arena/data_engine.py:123, 150`
```python
bars[len(bars)] = bar  # Sequential: 0, 1, 2, ...
```
If Parquet/JSON has gaps (minute 30 missing) or out-of-order bars, the index doesn't
match actual time. `get_bars_at_tick(30)` returns the 31st bar in the file, not the bar
at minute 30.

**Fix**: Index by minute offset from market open, or validate chronological order.

---

## Part 3: What's Missing (Production vs Arena)

### Tier 1: Missing and Critical for Accurate P&L

| Missing Feature | Impact on Simulation Accuracy | Effort |
|-----------------|-------------------------------|--------|
| **Exit intelligence (13 signals)** | Positions never exit smart — held until stop or EOD. Real bot exits on velocity fade, volume exhaustion, VWAP deterioration. Overstates hold times, understates win rate on positions that would have been exited early at profit. | Large |
| **Stop ratcheting** | Stops never move up after tranche fills. Real bot ratchets stop to breakeven after T1, to T1 after T2. Overstates drawdown, understates realized P&L on multi-tranche winners. | Medium |
| **Multi-tranche exit** | No tranche limit sells at T1/T2/T3 prices. Real bot exits 1/3 at each target. Simulation only tracks EOD close or stop hit. Massive P&L distortion on winning trades. | Medium |
| **Spread filter** | No pre-entry spread check (D101). Real bot rejects entries where spread > 1.5% (3% for high-conviction). Arena allows fills on illiquid stocks that production would skip. | Small |

### Tier 2: Missing and Important for Realistic Behavior

| Missing Feature | Impact | Effort |
|-----------------|--------|--------|
| **VWAP streaming** | No real-time VWAP. Bot uses VWAP for exit signals and intraday scanning. Arena snapshots use static bar VWAP. | Medium |
| **Phase 1.5 fast-path** | No 09:25 pre-scoring. Can't test whether fast-path entries capture opening momentum. | Medium |
| **EOD forced close at 15:55** | No time-based forced exit. Simulation runs to 20:00 ET. | Small |
| **Intraday rescan at 14:00** | No re-scanning for late movers. Can't test afternoon entries. | Medium |
| **Order rejection on insufficient cash** | Market orders can overdraw account. | Small |

### Tier 3: Missing but Acceptable Simplifications

| Missing Feature | Why It's Acceptable |
|-----------------|-------------------|
| Session recovery | Simulation is single-run; no crashes to recover from |
| Rate limiting | Zero-latency is actually more accurate for backtesting |
| Shapley attribution | Post-trade analysis, doesn't affect P&L |
| Holiday calendar | Can be avoided by not simulating holidays |
| Fractional shares | Bot uses integer shares |

---

## Part 4: What a 10/10 System Looks Like

A 10/10 backtesting system produces results that match production P&L within 5% on
replayed days, with statistical validation that the match holds across 50+ days. Here
is exactly what's needed:

### Level 1: Fix Critical Bugs (7.5 -> 8.0)

These are 1-line to 10-line fixes that eliminate systematic bias:

1. **Spread model timezone** — Fix UTC vs ET. Every fill price is currently wrong.
2. **Price improvement** — Remove `min()`/`max()` on limit fills. Fill at NBBO.
3. **Entry fill logic** — Require `bar.low <= entry_price` exactly. Use spread model ask.
4. **Stop fill logic** — Fill at bid when triggered, not at exact stop price.
5. **Bar indexing** — Validate chronological order or index by timestamp.

### Level 2: Add Multi-Tranche Exits + Stop Ratcheting (8.0 -> 8.5)

The single biggest P&L distortion is that winning trades aren't partially exited at
target prices. In production, RMSG entry at $0.57 would have T1=$0.66 (sell 1/3),
T2=$0.72 (sell 1/3), T3=$0.80 (sell 1/3). The stop ratchets to breakeven after T1,
to T1 after T2.

Implementation: Parse target_prices from journal entries. For each simulated trade,
check if bar.high crosses T1/T2/T3 and compute partial exit P&L. Ratchet the stop
after each tranche fill.

### Level 3: Add Exit Intelligence (8.5 -> 9.0)

Port the 4 parallel exit strategies to the simulator:
- **Velocity exit**: Compute from bar-over-bar returns. If momentum decelerating, exit.
- **Volume exhaustion**: Compute from volume profile. If volume fading from peak, exit.
- **Gratitude exit**: Time-based decay from catalyst. After catalyst half-life, exit.
- **Alpha decay oracle**: Null curve comparison. If return < expected for this minute, exit.

These don't need LLM calls — they're all computed from bar data + elapsed time.
Extract the computation logic from `src/execution/exit_intelligence.py` and
`src/execution/parallel_exit_engine.py`.

### Level 4: Full Bot End-to-End (9.0 -> 9.5)

Run `main.py` against the arena HTTP server. This requires:
- **Pre-market bar data** (04:00-09:30 ET) in the data engine
- **News API replay** from journal-cached responses
- **LLM response mocking** from journal agent signals
- **Phase-aware clock** that triggers phase transitions at correct ET times
- **Spread filter** before entry (reject wide-spread stocks)

This makes the entire decision pipeline testable, not just execution.

### Level 5: Statistical Validation Framework (9.5 -> 10.0)

The difference between 9.5 and 10.0 is TRUST. A 10/10 system has:

1. **Shadow trading validation**: Run arena on Day N while real bot trades Day N.
   Compare fill prices, order counts, P&L. Track divergence over 20+ days. If
   arena P&L is within 5% of production P&L with p < 0.05, the simulator is
   validated.

2. **Walk-forward cross-validation**: Every parameter sweep partitions dates into
   train/test. Parameters optimized on train, evaluated on test. Report both.
   No single-date conclusions allowed.

3. **Monte Carlo confidence intervals**: Every P&L result comes with a 95% CI
   from bootstrap. Configurations ranked by CI lower bound. This already exists
   in `arena/stats.py` but isn't enforced in the sweep display.

4. **Regression test suite against known days**: Mar 25 (0 trades), Mar 26
   (7 BUY signals, known outcomes), Mar 27 (SPY halt). Golden-file the expected
   results. Any code change that shifts results must be justified.

5. **Production P&L reconciliation**: After each live trading day, replay the
   same day in the arena. Compute delta. Track delta trend over time. If delta
   is shrinking, simulator is improving. If growing, investigate.

---

## Part 5: Implementation Roadmap

### Sprint 1: Bug Fixes (1 day)
- [ ] Fix spread model timezone (1 line)
- [ ] Fix price improvement on limits (2 lines)
- [ ] Fix entry fill logic in trade simulation (5 lines)
- [ ] Fix stop fill at bid not exact price (3 lines)
- [ ] Validate bar chronological order on load (10 lines)
- [ ] Add spread filter before entry in decision replay (10 lines)
- [ ] Re-run Mar 26 validation and compare P&L delta

### Sprint 2: Multi-Tranche Exits + Stop Ratcheting (2-3 days)
- [ ] Parse target_prices from journal entries
- [ ] Simulate T1/T2/T3 partial exits (bar.high crosses target)
- [ ] Implement stop ratcheting (breakeven after T1, T1 after T2)
- [ ] Track per-tranche P&L and exit reasons
- [ ] Re-run Mar 26 sweep with tranche exits

### Sprint 3: Exit Intelligence (3-5 days)
- [ ] Port velocity exit from src/execution/parallel_exit_engine.py
- [ ] Port volume exhaustion (peak volume ratio computation)
- [ ] Port gratitude exit (catalyst half-life decay)
- [ ] Port alpha decay oracle (null curve comparison)
- [ ] Compute exit composite score each tick
- [ ] Add EXIT/TIGHTEN signal to position lifecycle

### Sprint 4: Full Bot Wiring (1 week)
- [ ] Pre-market bar data in data engine
- [ ] News API replay from journal cache
- [ ] LLM response mocking from journal
- [ ] Phase-aware clock triggers
- [ ] Spread filter integration
- [ ] Run main.py end-to-end against arena

### Sprint 5: Statistical Validation (ongoing)
- [ ] Shadow trading pipeline
- [ ] Walk-forward enforced in sweep engine
- [ ] Golden-file regression tests
- [ ] Production P&L reconciliation automation

---

## Part 6: Score Projection

| Milestone | Score | What Changes |
|-----------|-------|-------------|
| Current state (D132) | 7.5 | Execution sim + decision replay, critical bugs |
| After Sprint 1 (bug fixes) | 8.0 | Fills accurate within 50 bps, data validated |
| After Sprint 2 (tranches) | 8.5 | Winning trades correctly partially exited |
| After Sprint 3 (exit intel) | 9.0 | Positions exit on real signals, not just stop/EOD |
| After Sprint 4 (full bot) | 9.5 | Entire pipeline testable, signal params sweepable |
| After Sprint 5 (validation) | 10.0 | Statistically validated against production within 5% |
