# Daily Session Diagnostics

This directory contains comprehensive day-by-day diagnostic journals for each Momentum-X paper trading session.

## File Naming Convention

```
SESSION_LOG_YYYY-MM-DD.md    # Full session diagnostic for that trading day
```

## What Each Session Log Contains

1. **Session Summary** — P&L, trade count, scan/eval/debate counts, error counts
2. **Trade Details** — Entry/exit prices, sizing chain, agent signals, Kelly tier classification
3. **Watchlist vs Actual Price Action** — Every candidate tracked against real market outcome
4. **Missed Opportunities** — Stocks that ran but we didn't trade, with root cause analysis
5. **D102 Experiment Replay** — How each parameter variant would have performed
6. **Systems That Worked** — What the system got right (important for confidence)
7. **Bugs Found** — Full bug reports with severity, root cause, and fix recommendations
8. **Market Context** — VIX, SPY, macro environment
9. **Priority Fixes** — Ranked action items for overnight fixes
10. **Open Questions** — Strategic decisions surfaced by the day's data

## Session Index

| Date | Day | P&L | Trades | Key Events |
|------|-----|-----|--------|------------|
| 2026-03-17 | Tue | -$248 | 1 | First D113-D115 live day. NVTS stopped out. CTMX +56% missed (BUG-001 killed technical_agent). 7 bugs found. |
