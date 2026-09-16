# 252 — Pre-gap anticipation: the model predicts gaps (AUC 0.86) but the "edge" is an un-capturable TAIL ILLUSION (winsorized-negative). The "different game" search is exhausted.

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — the last "different game": predict tomorrow's gap, capture the overnight move before it's priced.

## TL;DR
Predicting next-day gap-ups from prior-day features works *as a classifier* — **AUC 0.83-0.87 cross-regime**, top-slice gap-hit-rate 12.4% vs 0.49% base (25× lift). The top-0.5% overnight return looked positive (+0.82%, CI[+0.49,+1.19]) and even **survived** dropping momentum features, liquid-only, and a 0.5% slippage haircut. **But it died on the decisive test:** the top 10% of trades carry **341% of the P&L**, **winsorizing at +20% flips it to −0.39%**, and the 88% of picks that *don't* gap lose **−2.48% overnight**. The predictable part is the **un-capturable tail** (the monster gaps that carry everything are exactly the ones that halt / gap through any open-fill / can't be shorted), and the capturable part is **negative**. It is the *same fat-right-tail lottery as the rockets*, in overnight form — **not a harvestable edge.**

## The result (LORO, predict gap_t+1 from features ≤ close_t, capture close_t→open_t+1)
| | AUC | top-0.5% overnight (pooled 24+25) |
|---|---|---|
| full | 0.83-0.87 | +0.82% CI[+0.49,+1.19] |
| no momentum/gap features | — | +0.80% CI[+0.50,+1.14] (survives) |
| liquid ADV≥$10M | — | +1.01% CI[+0.41,+1.81] (2026 wobbles −0.30%) |
| net of 0.5% slippage | — | +0.30-0.51% |

**The kill test — tail concentration (top-0.5%, pooled 2024+2025):**
- raw mean +0.82% → **winsorized @+50%: +0.19%, @+20%: −0.39%, @+10%: −0.98%**
- **top 10% of trades = 341% of total P&L** (the other 90% net negative)
- **win-rate 47%, median +0.00%, p5 −13.8%** (brutal overnight gap-down tail you can't stop)
- **mean of the non-gapping 88%: −2.48%** (the cost of holding the lottery tickets overnight)

## Why this is the rockets again, not a new edge
The classifier ranks gap-probability well (AUC 0.86), but **the P&L is entirely the right tail** — the few +50/+100% overnight gaps. Those are precisely the ones you **cannot capture**: they halt, the open print is unfillable at the gap, and the names are HTB/squeeze-prone. Cap the single-trade upside at a realistic +20% (you won't reliably get more at the open) and the strategy is **negative**. The 88% non-gappers bleed −2.48% overnight financing the lottery. This is the identical structure to doc 248/250: **AUC predicts the tail; the tail isn't harvestable; the rest loses.** Anticipating the gap doesn't escape it — it just moves the un-harvestable lottery to the overnight window.

## The "different game" search is exhausted
| game | doc | verdict |
|---|---|---|
| ex-ante SELECTION (which gapper) | 245-249 | no cross-regime edge |
| EXECUTION (broad basket + asymmetric exits) | 250 | ~0% gross, negative after costs |
| REGIME-timing | 250 | best regime ~breakeven after costs |
| DIFFERENT UNIVERSE (every gap×price×ADV gate) | 251 | gap-ups fade everywhere; no long edge; short unborrowable |
| PRE-GAP ANTICIPATION (overnight capture) | 252 | predicts gaps but the edge is an un-capturable tail illusion (winsorized-negative) |
**Every angle on the low-float gapper universe reduces to the same un-harvestable fat-right-tail lottery.** The predictable component is always the tail you cannot capture (halts, slippage, borrow, squeeze); the capturable component is zero-to-negative; and it is negative after costs. This is a complete, rigorous, cross-regime closure.

## The honest bottom line + durable assets
**There is no harvestable alpha in the low-float gapper universe** — not by selection, execution, regime-timing, universe choice, or gap anticipation. The bot's bleed is structural; this game is not winnable as alpha. The constructive options are now narrow and clear: (a) a **fundamentally different strategy/universe/timeframe** (not gappers, not the intraday/overnight window — e.g., slower cross-sectional or a genuinely different inefficiency); (b) **compete on friction only** (lowest cost/variance vs Gemini — operational, not alpha); or (c) **accept the finding** and stop allocating research to this universe.
**What survives and compounds:** the **research method** (pre-registered, cross-regime, cost-aware, tail-aware, hostile-to-surprising-positives — it caught a +1% false lead on the last test), the **infrastructure** (warehouse, shadow, hardened execution), and a **complete, definitive negative** that will keep the program from funding any variant of this lottery again.

## Status
**No live change.** Closes the "different game" investigation. The pre-gap classifier is real but its P&L is the un-capturable tail; winsorized/realistic it is negative. **Basis**: `scripts/pregap_anticipation_doc252.py`, `scripts/pregap_verify_doc252.py` (3.0M stock-days, LORO, winsorize/tail-concentration kill test). **Predecessors**: 251 (fade), 250 (zero-EV), 248 (AUC↑money↓). **Memory**: [[gapper-universe-no-edge]].
