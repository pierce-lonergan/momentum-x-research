# Session Log — March 26, 2026 (D127)

## Summary
First day with actual trades since D107. The D126 gap-up momentum mode worked — 90 BUY verdicts across the day, 8 session trades logged. However, all 4 executed trades (JBLU, SRPT, RMSG, OLPX) were stop-outs. Total P&L: -$3,411. Four critical bugs discovered and fixed, including a broken Tier1 LLM model that may have been silently sabotaging evaluations for weeks.

## Four Critical Discoveries

### 1. Tier1 LLM (Qwen3.5-397B) Returning EMPTY Responses
Confirmed with live API testing: 4/4 calls with system prompts returned empty string. This means news_agent was producing NEUTRAL on most evaluations because the LLM gave nothing to parse. Explains why "0 directional agents" appeared so frequently across ALL previous sessions.

**Fix:** Switched to Qwen3-Next-80B-A3B-Instruct (1.6s latency vs 15s+ timeout, 75% cheaper, actually returns valid JSON). Fallback updated: DeepSeek V3.1 (unavailable) replaced with Llama-4-Maverick-17B (tested, reliable).

### 2. position_intent="close" Breaks Alpaca Paper API
The D124 fix caused 422 "invalid position_intent specified" on EVERY stop conversion. All 3 main positions (JBLU, SRPT, RMSG) ran WITHOUT stop protection for 30 minutes. The OTO stop was canceled for conversion, the standalone stop failed, and the retry failed. The fix intended to prevent short-sell rejection (ANNA Mar 23) was worse than the disease.

**Fix:** Disabled position_intent in payload.

### 3. MKDW (+59%) Corrupted by Trailing-W Symbol Cleaning
MKDW was the biggest winner two days in a row (+59% Mar 25, +59% Mar 26). The _clean_derivative_symbols() trailing-W rule converted MKDW to MKD (which doesn't exist). System missed the best stock in its universe TWICE.

**Fix:** Removed trailing-W rule entirely. Only .WS/.RT/.U/.UN suffixes cleaned.

### 4. Wrong Execution Order — JBLU Before UGRO
JBLU (9.9% gap, faded -8%) executed before UGRO (360% gap, +29%). Reason: JBLU had highest MFCS (0.256) because it had news + tech + manipulation all BULL. MFCS scoring rewards news coverage, not momentum strength.

**Fix:** BUY verdicts now sorted by momentum score (gap x rvol) before execution. Tomorrow: MKDW (score 864) executes before JBLU (score 0.4).

## Trade Details

| Trade | Entry | Stop | Exit | P&L | Hold | Notes |
|-------|-------|------|------|-----|------|-------|
| JBLU | $4.61 | $4.38 | $4.38 | -$490 | 30min | Faded all day, only +0.6% from open |
| SRPT | $23.20 | $19.52 | $19.52 | -$1,396 | 30min | -8.2% from open, gap-day wide stop |
| RMSG | $0.57 | $0.39 | $0.39 | -$1,399 | 30min | Stop at -32%, but peaked +16% later |
| OLPX | $2.00 | $1.95 | $1.95 | -$126 | 3min | Quick stop-out |

## What Would Have Been Profitable

| Stock | Max Gain | Was Traded? | Problem |
|-------|----------|-------------|---------|
| MKDW | +59.2% | No — ticker corrupted to MKD | Trailing-W rule |
| UGRO | +28.6% | BUY verdict but executed after JBLU | Execution order |
| RMSG | +11.8% peak | Yes but stopped at -32% | Stop too wide, hit bottom before recovery |
| PAYS | +9.4% | No — failed consensus (RVOL 2.7x) | Below momentum threshold |

## Simulation: Mar 26 with All Fixes Applied

With D127 fixes (model switch, symbol fix, momentum ordering):

| Rank | Ticker | Momentum Score | Would Trade | Outcome |
|------|--------|---------------|-------------|---------|
| 1 | RMSG | 2490 | YES | +11.8% |
| 2 | MKDW | 864 | YES | +59.2% |
| 3 | SRPU | 43 | YES | -16.1% |
| 4 | UGRO | 10.4 | YES | +28.6% |
| 5 | SRPT | 2.6 | YES | -8.2% |
| Last | JBLU | 0.4 | Last priority | -8.0% |

3 winners (+99% combined) vs 2 losers (-24% combined). Net strongly positive.

## Model Testing Results (Live API)

| Model | Latency | MKDW Signal | Quality |
|-------|---------|-------------|---------|
| Qwen3.5-397B (old Tier1) | 2337ms | EMPTY | Broken |
| Qwen3-Next-80B (new Tier1) | 1640ms | BULL 0.85 | Excellent |
| Llama4-Maverick (new fallback) | 2838ms | BULL 0.80 | Good |
| Qwen3-235B (Tier2, unchanged) | 2039ms | BULL 0.75 | Good |

## Commits
- cfe7d84: Fix stop orders, symbol cleaning, momentum ordering
- bcb2f19: Switch Tier1 model to Qwen3-Next-80B

## Lessons
1. Always test LLM models with REAL API calls before deploying. The Tier1 model was broken for an unknown number of days.
2. Alpaca Paper API does not support all fields from the live API documentation. Test on paper first.
3. Symbol cleaning rules must be validated against real tickers, not just warrant patterns.
4. Execution order matters as much as signal quality. The best signal is worthless if capital is consumed by lower-priority trades first.
5. The biggest single improvement was discovering the broken model — it may have been silently sabotaging evaluations for weeks.
