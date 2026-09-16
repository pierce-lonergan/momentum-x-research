# D219 Backfill Findings — 79 Days of Historical Data

**Status:** Complete | **Date:** April 16, 2026 | **Data:** 5862 candidates / 407 labeled with minute bars

## Pipeline Summary

| Block | Output | Status |
|-------|--------|--------|
| 1. Harvester | 5862 candidates over 79 trading days (Dec 11 → Apr 14) | Complete (crashed on network at end of run, recovered partial data) |
| 2. Minute bars | 500 ticker-day bar files (top 500 by gap) | Complete (0 failures) |
| 3. Features + labels | 407 fully-labeled rows with multi-horizon outcomes | Complete (93 dropped: insufficient bars or no 9:30 open) |
| 4. Gate replay | +59 trades gained by D219, 0 lost | Complete |
| 5. Arena conversion | 407 scenarios with 10 archetype tags | Complete |
| 6. Analytics | Validation report + daily P&L sim | Complete |

## Headline Findings

### 1. ORB Confirmation: STRONGLY VALIDATED (+41.6pp edge)

| Group | n | Close WR | Avg MFE | Median TtMFE |
|-------|---|----------|---------|--------------|
| ORB Broken | 320 | **53%** | +25.9% | 69 min |
| ORB Held (no breakout) | 87 | **11%** | +4.4% | 0 min |

**The single largest signal in the data.** D219 Phase 5 (ORB confirmation gate) ships correctly. Stocks that fail to break their first 5-minute high have only an 11% chance of finishing positive — they should be hard-rejected.

### 2. Best Hold Window: T+15 Minutes (54% WR, +1.46% avg)

| Horizon | n | WR | Avg | Median |
|---------|---|----|-----|--------|
| t1 | 407 | 40% | -0.12% | -0.18% |
| t5 | 407 | 45% | +0.23% | -0.18% |
| **t15** | 407 | **54%** | **+1.46%** | **+0.33%** |
| t30 | 407 | 49% | +1.31% | -0.03% |
| t60 | 407 | 48% | +1.09% | -0.20% |
| t120 | 402 | 45% | +0.22% | -0.86% |
| close | 407 | 44% | -0.52% | -2.13% |

**Implication:** The MFCS edge is concentrated in the first 15 minutes after entry. D170 TP1 around T+15 is well-calibrated. Hold-to-close hurts (loses 10pp of WR).

### 3. Gap Cap 50%→100%: NUANCED — Higher Ceiling, Different Strategy

| Gap | n | WR | Avg Close | Avg MFE | Avg MAE |
|-----|---|----|-----------| --------|---------|
| 5-10% | 2 | 50% | -1.9% | +6.5% | -7.0% |
| 10-20% | 4 | 25% | -3.0% | +7.4% | -8.7% |
| 20-30% | 177 | 47% | +0.5% | +18.4% | -11.5% |
| 30-50% | 119 | 50% | +0.9% | +21.1% | -14.2% |
| 50-75% | 40 | 40% | -1.6% | +18.5% | -16.2% |
| 75-100% | 19 | 42% | -4.9% | +29.8% | -21.1% |
| 100%+ | 46 | 24% | -5.1% | +33.9% | -21.5% |

- **<50% gaps:** 48% WR, +19.2% MFE
- **≥50% gaps:** 33% WR, +27.3% MFE

**Verdict:** Gaps ≥50% have **lower close WR but bigger MFE**. They demand tight stops and aggressive scalping. The D219 gap cap raise gives access to setups like **UGRO +72% close (64% gap)**, **EEIQ +66% close (75% gap)**, **AZI +30.9% close (53% gap)** — but only with ORB + early exit.

**Action:** Keep the gap cap at 100% but require ORB confirmation AND set tighter T+15 TP1 for high-gap stocks.

### 4. Equal-Weight Strategy: Unprofitable Without Selection (-19.3% over 79 days)

Trading every passer: **33/79 winning days (42%), Sharpe -0.36**.

This validates the existence of MOMENTUM-X. Raw gap-up screening is a coin flip — the alpha lives in:
1. **MFCS scoring** (selecting which passers to trade)
2. **ORB confirmation** (timing the entry)
3. **D170 TP1 around T+15** (capturing the early edge)

### 5. Archetype Distribution

| Archetype | n | % | Avg Close |
|-----------|---|---|-----------|
| ORB_BREAKOUT | 108 | 26.5% | **+19.4%** |
| MEGA_GAP_RUNNER (gap≥50%, close>+10%) | 18 | 4.4% | **+36.4%** |
| EARLY_SPIKE (TtMFE≤5min) | 8 | 2.0% | +5.2% |
| WINNER | 8 | 2.0% | +3.5% |
| FLAT | 27 | 6.6% | -0.0% |
| LATE_RUNNER | 42 | 10.3% | -1.6% |
| LOSER | 62 | 15.2% | -9.9% |
| SPIKE_AND_FADE | 43 | 10.6% | -11.6% |
| FAILED_BREAKOUT | 48 | 11.8% | -17.6% |
| MEGA_GAP_FADER | 43 | 10.6% | **-23.5%** |

**Key:** ORB_BREAKOUT and MEGA_GAP_RUNNER together = 31% of trades and the bulk of profits. FAILED_BREAKOUT + MEGA_GAP_FADER = 22% of trades and the bulk of losses. **The ORB gate filters out exactly the wrong half.**

### 6. Time-to-MFE Distribution

| Percentile | Time to MFE |
|-----------|-------------|
| p10 | 0 min (immediate fade) |
| p25 | 10 min |
| p50 | 60 min |
| p75 | 136 min |
| p90 | 310 min |

- 51% of stocks peak by T+60
- 86% peak after T+1
- D170 TP1 at T+15 captures ~29% of MFE distribution
- Trail stops should hold past T+60 for the long-tail runners

### 7. Worst Day: April 8, 2026 (16 trades, 6% WR, -10.09%)

A single high-gap-count day with broad fade — exactly the scenario VIX-aware sizing should mitigate. Recommend session circuit breaker after 3 consecutive losses.

## Action Items for D220+

1. **Ship Phase 5 ORB confirmation** — already in production
2. **Tighten TP1 for high-gap candidates** — gap≥50% should TP1 at T+10 instead of T+15
3. **Add session circuit breaker** — pause new entries after 3 consecutive losses or -3% session P&L
4. **Train LambdaMART** — we now have 407 rows with archetype labels and multi-horizon outcomes (need ~1000 for stable training, get more from extending bar download to top 1500)
5. **Stricter MAE-based stop** — average MAE -14% suggests time-phased stops (D218 innovation #2) would meaningfully reduce drawdown

## Files Generated

- `data/backfill/candidates.jsonl` — 5862 raw candidates
- `data/backfill/candidates_raw.jsonl` — Pre-filter backup (5920 rows)
- `data/bar_recordings/<date>/<ticker>.json` — 500 minute bar files
- `data/backfill/features_labeled.jsonl` — 407 fully-labeled training rows
- `data/backfill/arena_scenarios.jsonl` — 407 arena-formatted scenarios with archetypes
- `data/backfill/analytics_report.txt` — Full analytics output

## Data Limitations

- **Bar coverage:** Only top 500 by gap (out of 5862). Full coverage would require ~12 hours of API calls.
- **No float/market_cap:** Alpaca doesn't return these in historical bars; would need Finnhub backfill
- **No live MFCS scores:** All MFCS validation has to wait for live shadow data
- **April 14-15 gap:** Recent days included but no live BUY decisions to compare against
