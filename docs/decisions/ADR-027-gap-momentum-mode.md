# ADR-027: Gap-Up Momentum Mode (D126)

## Status
**Accepted** — Deployed March 25, 2026

## Context

After 3 consecutive zero-trade days (March 23-25), analysis of watchlist performance revealed a structural flaw: the deterministic technical agent uses lagging indicators (MACD histogram, EMA crossover, VWAP position) that produce BEAR signals on gap-up stocks during the first 5-10 minutes of market open.

On March 25, 8 of 13 watchlist stocks were BIG WINNERS (+22% to +136%). The technical agent gave BEAR signals on 6 of them. The system's factor voting system:
- MACD histogram negative (pullback from gap) → -1 bearish
- EMA(21) > EMA(9) (insufficient bars for crossover) → -1 bearish
- Price below VWAP (initial pullback) → -1 bearish
- net_bull = -2 → BEAR signal

Additionally, D104 time decay multiplied confidence by 0.0 at T+0, killing signals at peak momentum.

## Decision

Implement a **Gap-Up Momentum Mode** that overrides lagging indicator penalties when a stock meets clear gap-momentum criteria.

### Activation: Composite Threshold
```
momentum_score = gap_pct × rvol
_gap_momentum_mode = (
    momentum_score > 1.0      # Composite threshold
    AND gap_pct > 0.08        # Floor (EMC minimum)
    AND rvol > 2.5            # Floor (real volume)
    AND minutes_since_open < 30  # Decay window
)
```

**Rationale for composite over simple floors:**
- `gap_pct * rvol > 1.0` captures the full spectrum: 10% gap × 10x RVOL, 20% gap × 5x, 50% gap × 2x
- A high gap threshold (e.g., 25%) would miss most winners — 3 of 4 example winners had gaps below 25%
- The gap × volume composite measures conviction: a 10% gap on 50x RVOL is stronger than a 30% gap on 3x RVOL

### Factor Override
In momentum mode:
- Skip MACD and EMA factors (lagging, wrong on gap-ups)
- Start with +2 bullish base (the gap IS the signal)
- VWAP position: +1/-1 (real-time, valid)
- RSI 50-75: +1 bullish; RSI > 75: NO penalty (confirmation, not overbought)

### Gradual Decay (Not Cliff)
- 0-10 minutes: full momentum mode (weight = 1.0)
- 10-30 minutes: linear blend back to normal voting
  - weight = 1.0 - (minutes - 10) / 20
  - At T+20: 50/50 blend
  - At T+30: fully normal
- Prevents contradictory signals on consecutive evaluation cycles

### Time Decay Override
- Momentum stocks: confidence starts at 0.8 at T+0, reaches 1.0 by T+5
- Normal stocks: unchanged D104 decay (0.0 at T+0, 1.0 at T+30)
- Rationale: the opening auction on a confirmed-volume gap-up is the highest-conviction moment

## Alternatives Considered

### A: Lower factor thresholds
Change `net_bull <= -1 → BEAR` to `net_bull <= -2 → BEAR`. This would prevent BEAR on stocks with only -2 factors, but also reduces sensitivity to genuinely bearish setups.

**Rejected**: Affects ALL stocks, not just gap-ups. Normal stocks with -1 net_bull should still be BEAR.

### B: Ignore technical agent entirely for gap-ups
Always return BULL for stocks gapping > 20%.

**Rejected**: Too aggressive. No calibration based on real-time indicators. Would enter every gap-up regardless of VWAP position or RSI.

### C: Add a "gap-up pattern" to the pattern detection
Like CONSOLIDATION_BREAKOUT, add GAP_UP_MOMENTUM as a detected pattern that adds +2 bullish.

**Rejected**: Still subject to MACD/EMA penalties that could flip net_bull negative. A pattern adding +2 with penalties of -3 still produces BEAR.

## Consequences

### Positive
- System can trade its target universe (small-cap gap-ups with high RVOL)
- 6 of 8 March 25 winners would have triggered momentum mode
- Gradual decay prevents cliff-edge signal flips on re-evaluation
- Time decay override captures the peak momentum entry window

### Negative
- Momentum mode is optimistic by design — may produce false positives on promotional pumps
- Mitigated by: D106 manipulation classifier (halves position, tight stop, 10:30 hard exit), MFCS threshold, D124 consensus alignment, stop loss

### Risks
- A stock gapping +30% on fake volume with RVOL = 5x (score = 1.5) would trigger momentum mode
- The manipulation classifier should catch most of these (flagging "no catalyst + extreme RVOL")
- Even if it doesn't: half position + tight stop + 10:30 hard exit limits max loss

## Validation
Backtested against March 25 intraday data:
- 16 unit tests covering activation, signals, decay, time decay, and config
- 2020 total tests passing
- March 25 simulation: 6 of 8 BIG WINNERS would have triggered
