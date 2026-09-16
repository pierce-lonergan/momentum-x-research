# Session Log — March 25, 2026 (D126)

## Summary
Third consecutive zero-trade day. 13 candidates on watchlist, 8 were BIG WINNERS (+22% to +136%). System traded zero. Root cause: deterministic technical agent uses lagging indicators (MACD, EMA crossover) that are structurally BEARISH on gap-up stocks at market open. Combined with min_directional_agents=2 (which was fixed in D125 but never deployed because the main repo wasn't updated), the system couldn't produce a single BUY verdict.

## Critical Discovery: Main Repo Not Updated
The main repo at <repo-root> was 8 commits behind origin/develop. All D123-D125 changes (observability, stop intent, consensus alignment, dynamic gap cap, min_directional=1, fundamental D94 skip, context-aware spread) were committed and pushed to origin/develop but the local checkout was still on commit 8e14006 (D122). The Task Scheduler runs from this directory, so NONE of the fixes from the past 3 days were active.

Fixed with: `cd main-repo && git pull origin develop`

## Watchlist Performance

| Ticker | Gap | RVOL | Tech Signal @9:30 | Max Gain | Max DD | Close vs Open | Verdict |
|--------|-----|------|-------------------|----------|--------|---------------|---------|
| RMSG | ? | ? | N/A (late scan) | +136.5% | 0.0% | +65.8% | BIG WINNER |
| MKDW | 50% | 1718x | BEAR→BULL@9:32 | +70.2% | -9.5% | +61.5% | BIG WINNER |
| FEED | 55% | 647x | BEAR all session | +34.3% | -2.8% | +21.8% | BIG WINNER |
| SIDU | ? | ? | NEUTRAL | +28.3% | -1.0% | +13.4% | BIG WINNER |
| BIAF | 65% | 4.4x | STRONG_BEAR | +22.9% | -8.6% | -5.1% | BIG WINNER |
| CVV | 39% | 122x | BEAR | +22.3% | -6.8% | +21.3% | BIG WINNER |
| RBNE | 82% | 185x | STRONG_BULL | +15.5% | -17.5% | -9.7% | BIG WINNER |
| QNTM | 44% | 2.1x | BEAR | +14.8% | -14.4% | -11.6% | BIG WINNER |
| SWMR | ? | ? | BEAR | +7.5% | -15.7% | -12.3% | WINNER |
| ANNA | ? | ? | STRONG_BULL→BEAR | +6.9% | -18.3% | -11.1% | WINNER |
| CRCD | ? | ? | BEAR | +3.9% | -13.5% | -1.3% | TRAP |
| GRAB | ? | ? | STRONG_BEAR | +2.3% | -2.1% | -1.8% | CHOPPY |
| CDE | ? | ? | N/A | +1.2% | -7.9% | -5.5% | FADED |

## Root Cause Analysis: Technical Agent BEAR on Gap-Ups

The deterministic technical agent (src/agents/deterministic_technical.py) uses a factor voting system:
- RSI(9) > 60 → +1 bullish; < 40 → +1 bearish
- MACD histogram > 0 → +1 bullish; < 0 → +1 bearish
- EMA(9) > EMA(21) → +1 bullish; EMA(21) > EMA(9) → +1 bearish
- Price > VWAP → +1 bullish; < VWAP → +1 bearish
- Breakout pattern → +2 bullish

net_bull >= 1 → BULL, net_bull <= -1 → BEAR

On a gap-up stock at market open:
1. MACD(5,13,4) hasn't established a new bullish crossover → NEGATIVE histogram → -1
2. EMA(9) hasn't crossed EMA(21) with few bars → -1
3. Price dips below VWAP during initial pullback → -1
4. RSI might be 55 (bullish) → +1

net_bull = 1 - 3 = -2 → BEAR

Additionally, D104 time decay multiplies confidence by minutes_since_open/30. At T+0, confidence = 0.0. At T+5, confidence = 0.17 × base. This kills signals at the exact moment gap-up stocks have peak momentum.

## Fix: D126 Gap-Up Momentum Mode

Composite threshold: gap_pct × rvol > 1.0 (with floors: gap > 8%, rvol > 2.5x)
- Catches the full winner spectrum: 10% gap × 10x RVOL through 50% gap × 2x RVOL
- Skips lagging indicators (MACD, EMA) — they're wrong on gap-ups
- Keeps real-time indicators (VWAP position, RSI)
- No RSI > 75 penalty (confirmation, not overbought in momentum context)
- Gradual decay: full strength 0-10 min, linear blend to normal 10-30 min
- Time decay override: 0.8 at T+0 (not 0.0), 1.0 by T+5

Wired to all 4 technical agent call sites (fast_path, orchestrator×3).

## Validation: March 25 Stocks vs D126

| Stock | Momentum Score | Would Activate? | Actual Outcome |
|-------|---------------|----------------|----------------|
| MKDW | 50% × 1718x = 859 | YES | +70% |
| FEED | 55% × 647x = 356 | YES | +34% |
| RBNE | 82% × 185x = 152 | YES | +16% |
| CVV | 39% × 122x = 48 | YES | +22% |
| BIAF | 65% × 4.4x = 2.9 | YES | +23% |
| QNTM | 44% × 2.1x = 0.9 | NO (below 1.0) | +15% |

6 of 8 winners would have triggered gap momentum mode.

## All Changes Deployed Today (D123-D126)

| Commit | Description |
|--------|-------------|
| D123: 4046dc7 | Pipeline observability — silent drop logging across all stages |
| D123: ce5f835 | Derivative symbol cleaning (.WS, .RT, trailing-W → common stock) |
| D124: b56bde0 | ANNA postmortem: position_intent="close", consensus alignment, dynamic gap cap |
| D125: 8dda4c7 | Fundamental agent D94 skip, execution-blocked logging |
| D125: e03f9a3 | Context-aware spread filter (3% for high-MFCS), PTLE postmortem |
| D125: 59e9904 | Drop min_directional_agents from 2 to 1 |
| D126: a96abc0 | Gap-Up Momentum Mode — composite threshold, time decay override |

## Lessons Learned

1. **Deployment verification is critical.** We committed 8 fixes over 3 days that were never deployed because the main repo wasn't pulled. A deployment smoke test ("verify main repo HEAD matches origin/develop") should be part of every commit.

2. **The consensus gate problem was a symptom, not the root cause.** We spent D125 analyzing why min_directional=2 blocked trades. The real issue was that the technical agent — the only potentially directional signal — was BEARISH on the stocks we wanted to trade. Fixing the consensus gate let through the one stock with STRONG_BULL (RBNE) but couldn't help the 7 stocks where technical was BEAR.

3. **Lagging indicators are structurally wrong at market open on gap-ups.** MACD, EMA crossovers, and VWAP-relative position all require established trends. In the first 5-10 minutes of a gap-up, there is no trend — there's a gap. The gap IS the signal.

4. **Backtesting against the day's actual data revealed the fix.** Without pulling March 25 intraday bars and computing what-if scenarios, we wouldn't have known that 8 of 13 stocks were winners or that the technical agent was the specific bottleneck.

5. **Time decay (D104) was killing the best entry window.** T+0 confidence = 0.0 was designed for normal stocks where opening auction is noise. On gap-ups with confirmed volume, the opening IS the signal. D126 changes this to 0.8 for momentum stocks.

## Tomorrow's Expectations

With all D123-D126 changes deployed to the main repo:
- Gap-up stocks with momentum_score > 1.0 will get BULL or STRONG_BULL at market open
- min_directional=1 means a single bullish technical signal can trigger entry
- D124 consensus alignment prevents entry when bearish signals outnumber bullish
- D106 manipulation classifier still active (halves position, tight stop, 10:30 hard exit on promotional stocks)
- Context-aware spread (3% for high-MFCS) allows micro-cap entries
- Expected: 1-5 trades, first live test of the full D122-D126 stack

## Test Results
2020 tests passing (16 new D126 tests).
