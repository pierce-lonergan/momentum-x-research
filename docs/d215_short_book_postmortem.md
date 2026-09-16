# D215: Short Book Post-Mortem

**Status:** KILLED — D161 and D207 both disabled 2026-04-08
**Trades:** 25 round-trips
**Performance:** WR=20%, PF=0.11, -244.6% cumulative

## Original Thesis

**D207 (Aggressive Short):** "Extreme gap-ups (>50%) with no catalyst are
promotional pumps that statistically fade -27.7% avg. Short them directly
from scanner output before LLM evaluation."

**D161 (Faller Short):** "Stocks that fail the faller gate (score >0.60)
with high gap and RVOL should be shorted instead of simply rejected.
Converts a dead trade into a revenue opportunity."

Both were inspired by the accidental EEIQ short ($19K profit) and the
D200-D206 analysis showing gap >30% losers had avg -25.6% drops.

## Why It Failed

### 1. The thesis was correct but the implementation was adversarial

The thesis — "promotional gap-ups fade" — is supported by data (serial
gappers have 35% WR). But the system was:

- **Going long AND short the same stock on the same day.** All 7 overlap
  tickers (IOVA, VIR, MOBX, JZXN, ASNS, CANF, NVTS) show same-day
  whipsaw. The system would:
  1. Go long at market open
  2. Get stopped out (loss #1)
  3. D161 faller route triggers short on the stop-out
  4. Get stopped out of the short (loss #2)
  5. Sometimes re-enter long again (loss #3)

  This is paying spread and slippage 4-6 times on a stock that's
  range-bound, not trending in either direction.

### 2. March 4 was a catastrophe caused by re-entry loops

5 short trades on March 4 alone lost -181.6% cumulative:
- ASNS: 4 short entries, all stopped out (-31.6%, -19.5%, -38.8%, -9.6%)
- CANF: 3 positions (2 long stopped out, 1 short stopped out)

The D94b stopped-out guard wasn't catching shorts that re-entered after
the long was stopped out (different direction = different guard scope).

### 3. Short borrow and timing worked against us

Small-cap gap-ups have the worst short borrow conditions:
- High borrow fees (not modeled in our system)
- Hard-to-borrow / no-borrow status (caused 422 errors from Alpaca)
- Shorts entered AFTER the initial fade, catching the bounce

## Subset Analysis

| Subset | n | WR | PF | Verdict |
|--------|---|-----|-----|---------|
| All shorts | 25 | 20% | 0.11 | Dead |
| Excl March 4 | 20 | 25% | 0.33 | Still dead |
| March 4 only | 5 | 0% | 0.00 | Catastrophic |
| Overlap tickers (whipsaw) | 14 | 14% | 0.08 | Fighting itself |
| Non-overlap | 11 | 27% | 0.18 | Still bad |

**No subset is profitable.** The strategy fails uniformly across every
dimension we can measure.

## The 7 Overlap Tickers — All Whipsaw

Every overlap ticker shows same-day long-then-short or short-then-long:
- ASNS: 8 orders on 2026-03-04, alternating buy/sell/buy/sell
- CANF: 6 orders, same pattern
- IOVA: Long and short on consecutive days
- JZXN: Long, stopped out, short, stopped out, long again
- MOBX: Long +95% on day 1, then whipsaw on day 2
- NVTS: Same-day long and short
- VIR: Same-day long and short

This is the system fighting itself. Each whipsaw costs 2x spread +
2x slippage with zero chance of profit if the stock is range-bound.

## What We Learned

1. **Long and short must be mutually exclusive on the same ticker within a session.**
   If you go long and get stopped out, you do NOT short the same name.
   The D94b guard exists for longs but doesn't block shorts on stopped-out tickers.

2. **PF=0.11 is not bad luck at n=25 — it's a broken strategy.**
   Even the "best" subset (non-overlap, excl March 4) has PF=0.18.
   The thesis was right (gaps fade) but the execution was wrong
   (entering shorts after the fade has already happened, fighting
   the same stock multiple times, no borrow cost modeling).

3. **Shorts on Alpaca small-caps are structurally disadvantaged.**
   422 errors for non-shortable stocks, unreliable locate, no
   borrow cost modeling. If we ever revisit shorts, it likely
   needs IBKR with proper locate infrastructure.

## Conditions for Re-Evaluation

Do NOT re-enable D161/D207 unless ALL of these are met:
1. Same-day long/short exclusion rule is implemented
2. Borrow cost modeling is added to P&L computation
3. Short locate availability is checked BEFORE entry signal generation
4. Separate short-book P&L dashboard is live and monitored daily
5. At least 50 simulated short trades in arena show PF > 1.20 after costs
6. Post-mortem reviewed and signed off

## Impact on System

Disabling shorts immediately:
- Eliminates -244.6% cumulative short losses going forward
- Removes the whipsaw risk on overlap tickers
- Simplifies execution (5 active paths instead of 7)
- Makes all future P&L measurements cleaner (long-only, no contamination)

## Appendix: D94b Long-Side Whipsaw Investigation

**Date:** 2026-04-08
**Trigger:** Post-mortem review flagged potential long→stop→long cycle

### Q1: Do long→stop→long sequences exist without a short leg?

**YES.** March 4 shows direct long re-entries after stop-outs:
- ASNS: 4 OTO buy+stop pairs on same day (entered 4x, stopped 4x)
- JZXN: buy→stop→buy at 16:12:55 (same second)
- CANF: buy→stop→buy at 15:37:06→15:37:26 (20 seconds apart)
- MOBX: buy→stop→buy at 14:30:21→14:31:03 (42 seconds apart)

### Q2: Does D94b cover all paths?

**YES.** All 5 active entry paths check `_stopped_out_tickers`:
- Phase 2 BUY (line 2254)
- VWAP Breakout (line 5180)
- Rescan (line 5574)
- Fast-Path (line 1443)
- D170 is add-only (lines 4751, 4956), not checked at entry

### Q3: Guard time horizon?

**Session-permanent.** No expiry, no clear, no remove.
Once a ticker is in `_stopped_out_tickers`, it stays there for the session.

### Root Cause: Race Condition

The guard checks `_stopped_out_tickers` at entry time, but the ticker
is only ADDED to the set when Phase 3 detects the stop fill. Phase 2
can submit a new OTO entry between Phase 3 cycles (~60s gap). The new
entry fills before the previous stop-out is detected.

**The same-second timestamps** on JZXN and ASNS are OTO pairs (buy+stop
submitted together), not re-entries. But the MULTIPLE OTO pairs on the
same day (4 for ASNS, 2 for CANF) are genuine re-entries that slipped
through the timing gap.

### Risk Assessment (Post-Short-Disable)

With shorts disabled, the race only causes long→long re-entry. Impact:
2x spread + 2x slippage per whipsaw, probably 5-10% of long-book losses.
Less severe than the short book (PF=0.11) but still real.

### Recommended Fix

Add ticker to `_stopped_out_tickers` at STOP ORDER SUBMISSION time (when
the stop is first placed), not at STOP FILL time. This closes the race
window because the ticker is locked out before the stop can possibly
trigger and before the next evaluation cycle runs.
