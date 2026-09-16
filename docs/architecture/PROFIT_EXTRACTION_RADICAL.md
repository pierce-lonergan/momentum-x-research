# Profit Extraction: Tested Results + Next Innovations

**Version**: D146 (March 28, 2026)
**Current best**: 100% bar-1 exit = +$5.11 (+299% vs original)
**Deployed**: Enter at 09:30:01, sell at 09:31:01
**Monitoring**: 40-trade window, revert to 50% bar-1 if <2 outliers

---

## Part 1: What We Know (D146 State)

The arena produced a complete theory of gap-up momentum profitability:

- MFE peaks at bar 1 (the first 60 seconds). Bar-1 return avg = +6.1%.
- MFE continues past bar 1 on 71% of trades. Growth ratio 1.4x.
- BUT the pullback between spike and continuation kills every trailing mechanism.
- Remainder captures -88% of MFE. 73/86 remainder exits are stops.
- 100% bar-1 exit = +$5.11. Eliminating remainder = eliminating the loss center.
- Edge is outlier-driven: top 5 trades = 85% of P&L. Median = breakeven.
- Outlier frequency: 6.3% (5/79). All $3-$5 stocks, 5 different dates.
- Regime makes no difference. Bar-1 timing is universal.
- Walk-forward: 100% bar-1 = 2.0x overfit (borderline). 50% = 1.5x (robust).

**The fundamental constraint:** At 1-minute resolution, we sell at bar_1.close
(T+60s). The intra-bar peak might be at T+25s. We capture the close, not the peak.

---

## Part 2: Tested Results (What Worked, What Failed)

### WORKED (+P&L or validated)

| Innovation | Result | Status |
|-----------|--------|--------|
| **100% bar-1 exit** | +$5.11 (+299%). Walk-forward 2.0x (borderline). | **DEPLOYED** |
| **Tight tranches (+1/+2.5/+5)** | +$1.70 (+33%). Superseded by bar-1 exit. | Superseded |
| **Capture decomposition** | Bar-1 = +22%, remainder = -88%. Breakthrough insight. | Validated |
| **R5: Multi-TF MFE** | 71% continue past bar 1. Growth 1.4x. Remainder untestable. | Validated |
| **Spread filter** | +130% by removing 51% neg-expectancy trades. | Validated (re-test with bar-1) |
| **Pipeline inversion** | +$0.95 vs sequential. Directionally correct. | **DEPLOYED** |

### FAILED (Tested, rejected with data)

| Innovation | Result | Why It Failed |
|-----------|--------|---------------|
| **R2: Conditional bar-1 fractions** | Flat 100% beat all variants by $1.19-$1.81 | Breakout trades have same spike-pullback-continuation pattern |
| **A4: Bar-2 fade classifier** | 100% bar-1 beats 80%+trail by $0.76 | Even bar-2 classifier can't fix the remainder problem |
| **Trailing stop on remainder** | -$0.62 net P&L | Pullback triggers trail before continuation |
| **Re-entry after pullback** | +$0.08 marginal | Continuation is small after spread costs |
| **$3-$10 price gate (R4)** | Would lose +$2.64 | Non-outlier trades outside range contribute |
| **Regime-conditional timing (E7)** | Zero difference (LOW_VOL=ELEVATED) | Bar-1 is universal, not regime-dependent |
| **Time exits at bar 5/15** | -38% vs price targets | Sell during pullback, miss spike |

### NOT TESTED YET (From original doc)

| Innovation | Priority | Blocker |
|-----------|----------|---------|
| R1: Sub-minute exit (T+30s) | HIGH | Needs tick/5-sec data from Alpaca |
| R3: Entry price improvement | MEDIUM | Risk of missing outlier entries |
| A1: Tick-level exit optimization | HIGH | Needs trade-level data download |
| A2: Liquidity-aware split selling | LOW | Market impact negligible at retail scale |
| A5: Overnight pre-loading (09:29:55) | LOW | Can't simulate auctions, asymmetric risk |
| A6: Dynamic max positions | MEDIUM | Reframe as selection quality |
| E6: Multi-day continuation | MEDIUM | Needs Day 2 data, different strategy |

### KILLED (Not worth pursuing)

| Innovation | Reason |
|-----------|--------|
| A3: Sector cascades | No small-cap sector correlation. Company-specific events. |
| E1: RL exit agent | 79 trades = 3 orders of magnitude too few. |
| E2: Cross-stock propagation | Same as A3. No evidence of lagged correlation. |
| E3: Volatility surface | $3-$5 stocks don't have liquid options. Wrong universe. |
| E4: Intraday pairs trading | Short selling unavailable on micro-caps. Wrong universe. |
| E5: Microstructure exploitation | Requires sub-second execution on retail API. Not feasible. |

---

## Part 3: New Innovations (Post-D146)

### N1: Relaxed Entry Criteria Sweep (HIGHEST PRIORITY)

Both external reviewers identified this as the single biggest remaining lever.
Every exit optimization since D142 operates on the same 79 trades. The bar-1
exit at +299% on 79 trades becomes +299% on 120+ trades if we find more entries.

**Test:** Lower gap from 8% to 5%, RVOL from 2.5x to 2.0x, momentum score
from 1.0 to 0.7. Run decision replay across all 75 dates. If trade count
increases from 79 to 110+, walk-forward the relaxed criteria to confirm
they don't admit trash candidates.

**Expected impact:** 40%+ more trades = 40%+ more outlier opportunities.
At 6.3% outlier rate on 120 trades = ~7-8 outliers instead of 5.
Each outlier adds ~+$0.50 P&L. Net: +$1.00-$1.50 additional.

**Arena test:** 30 minutes. Decision replay with 3 relaxation levels.

### N2: Sub-Minute Exit Validation (T+30s vs T+60s)

The biggest remaining capture improvement. Currently selling at bar_1.close
(T+60s). The intra-bar spike peaks earlier. Need real data to validate.

**Step 1:** Download Alpaca trade-level data for 10-20 historical entries.
Build per-second price curve for first 120 seconds.

**Step 2:** Find the exact second where MFE peaks per trade. If it
consistently peaks at T+20-40s, the T+30s exit captures more.

**Step 3:** Compute: does selling at T+30s price beat T+60s price after
accounting for wider spreads at T+30s? (Opening auction resolution means
spreads are wider earlier.)

**Risk:** T+30s is still in the auction resolution chaos on small-caps.
The bid at T+30s might be 1% below the bid at T+60s because the spread
hasn't tightened yet. Don't deploy without tick data confirmation.

### N3: Position Sizing by Outlier Probability

The 5 outliers are all $3-$5 stocks. But R4 showed a hard price gate
loses +$2.64 from non-$3-$10 trades. Better approach: SIZE by tier,
don't FILTER.

**$3-$10 stocks:** Full position (15% of equity)
**$0-$3 and $10+ stocks:** 75% position (11.25% of equity)

This concentrates capital where outliers occur without eliminating
profitable non-outlier trades. If an outlier hits at full size vs
75% size, the P&L gain is +25% on that single trade.

**Arena test:** 20 minutes. Compare uniform sizing vs tiered across 79 trades.

### N4: Pre-Market Volume Acceleration Filter

Not all gap-ups are equal. Stocks with accelerating pre-market volume
(volume increasing in each 30-min bucket from 04:00 to 09:30) have
stronger opening momentum than stocks with flat or declining pre-market
volume.

**Hypothesis:** Stocks with accelerating pre-market volume produce
larger bar-1 spikes because buying pressure is increasing into the open.

**Arena test:** From journal data, extract pre-market volume profile
(the premarket cache has this). Correlate with bar-1 return. If
correlation > 0.3, use as entry quality filter.

### N5: Spread-Cost-Adjusted Position Sizing

Finding 6 showed 51% of trades are negative-expectancy after spread.
Instead of filtering (which loses +$2.64 outside range), reduce
position size on high-spread candidates.

**Formula:** position_size = base_size * max(0.25, 1.0 - spread_cost / expected_MFE)

On a $5 stock with 12 bps spread and 5.3% expected MFE:
ratio = 0.0012 / 0.053 = 0.023. Size = 97.7% (essentially full).

On a $1 stock with 80 bps spread and 3.5% expected MFE:
ratio = 0.008 / 0.035 = 0.23. Size = 77% (reduced).

This continuously adjusts sizing rather than binary filter/keep.

### N6: The 60-Second Scalp Universe Expansion

The bar-1 exit reduces the strategy to a 60-second scalp. This scalp
works on gap-up momentum stocks. Does it work on OTHER 60-second
patterns?

**Test 1:** Run the bar-1 exit on all 168 tickers across all 75 dates
(not just gap-up candidates). Some stocks that don't gap up still have
bar-1 spikes from other catalysts (earnings pre-market, FDA approvals,
analyst upgrades).

**Test 2:** Run the bar-1 exit on the intraday rescan candidates
(stocks that appear on the watchlist after 09:30, during Phase 3).
The bar-1 spike might exist at any entry time, not just at market open.

**Expected impact:** If the bar-1 spike pattern exists on 20%+ of
non-gap-up entries, the trade universe expands dramatically.

### N7: Adversarial Stress Testing of Bar-1 Exit

The bar-1 exit has never been tested against adversarial scenarios.
What happens when:

- **Spread shock:** Spreads 3x wider at T+60s (VIX spike during bar 1)
- **Volume kill:** Volume drops to 10% at T+60s (liquidity vacuum)
- **Flash crash during bar 1:** Stock drops 5% between T+30s and T+60s

Use the arena's scenario generator (spread_shock, volume_kill injections)
on real historical bars. If the bar-1 exit P&L collapses under stress,
the strategy needs a conditional: don't sell at T+60s if bar-1 return
is negative (the spike failed, hold for recovery or stop out).

### N8: Intraday Re-Entry at Specific Signal

The re-entry test (D145) showed +$0.08 from generic re-entry at bar 4.
Marginal. But what about signal-specific re-entry?

**After the bar-1 exit at 09:31:** Monitor the stock. If at 09:35-09:40
the stock makes a new high above bar-1 high (a genuine breakout beyond
the initial spike), re-enter with 50% position and a tight 1% stop.

This is different from the D145 re-entry test which entered at bar 4
regardless of whether the stock was making new highs. Signal-specific
re-entry only triggers on confirmed breakout — a much smaller but
higher-quality subset.

**Arena test:** Count how many of the 79 trades make a new high after
bar 5. If >15% do, test the re-entry P&L on just those.

### N9: Daily P&L Prediction Model

After 20+ days of daily retrospective data (predicted vs actual P&L),
build a simple regression model: can the arena's predicted P&L for
tomorrow predict the actual P&L range?

Features: number of candidates, average gap%, average RVOL, regime label.
Target: actual daily P&L.

If the model achieves R^2 > 0.3, the arena can predict which days are
high-expected-value (enter aggressively) vs low-expected-value (reduce
position sizes). This is meta-optimization: optimizing HOW MUCH to
trade each day, not what to trade or how to exit.

### N10: Fill Model Calibration From Live Trades

After 40 live trades: compare the arena's predicted fill price (from
spread model) to the actual Alpaca fill price. Compute per-price-tier
bias. If the model systematically overstates fills on $3-$5 stocks by
8 bps, add a -8 bps correction.

This is the single action that would most improve arena fidelity.
Every simulation result would become marginally more accurate, which
compounds across hundreds of comparisons.

---

## Part 4: Prioritized Action List (D146+)

### Immediate (Today)
1. **N1: Relaxed entry criteria sweep** — 30 min. Highest lever.
2. **N3: Tiered position sizing** — 20 min. Quick win if data supports.

### This Week
3. **N2: Download tick data** for 10-20 trades. Validate T+30s vs T+60s.
4. **N7: Adversarial stress** on bar-1 exit. Spread shock + volume kill.
5. **N4: Pre-market volume correlation** with bar-1 return.

### As Live Trades Accumulate
6. **N10: Fill model calibration** — after 40 live trades.
7. **N9: Daily P&L prediction model** — after 20 retrospectives.
8. **N8: Signal-specific re-entry** — after bar-1 exit is live-validated.

### Exploratory (Data Dependent)
9. **N6: 60-second scalp on non-gap-ups** — needs broad universe testing.
10. **N5: Spread-adjusted sizing** — continuous version of the binary filter.
11. **E6: Multi-day continuation** — needs Day 2 data collection.
12. **A6: Dynamic max positions** — reframe as selection quality ranking.

---

## Part 5: The Realistic Capture Path (Corrected)

The D144 projection of 46% → 90% was aspirational. Corrected based on
reviewer feedback and diminishing returns:

| Stage | Capture | How | Confidence |
|-------|---------|-----|------------|
| **D145 (current)** | **~46%** | 100% bar-1 exit | Measured |
| +Entry relaxation | ~48% | More trades, same capture per trade | High |
| +Sub-minute exit | ~52% | T+30s instead of T+60s (if tick data confirms) | Medium |
| +Position sizing | ~54% | More capital on high-MFE candidates | Medium |
| **Realistic ceiling** | **~55-60%** | Diminishing returns from 1-min resolution | Estimated |

70%+ requires sub-minute execution infrastructure (co-located servers,
direct market access). 90% is theoretical fantasy — no momentum strategy
at any scale achieves 90% capture.

The biggest remaining lever is NOT capture improvement — it's trade count.
More trades at 46% capture produces more total P&L than fewer trades at
55% capture. Entry criteria relaxation (N1) is worth more than all
capture optimizations combined.
