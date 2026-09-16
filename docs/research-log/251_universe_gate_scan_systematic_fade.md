# 251 — Universe gate scan: gap-ups systematically FADE across every corner. No capturable long edge; the short is real but mostly unborrowable. The bot buys the fade.

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — test a "different game": (2) different universe [this doc], (1) pre-gap anticipation [next].

## TL;DR
A comprehensive scan of **112,015 gap-up stock-days (2024-2026)** across a grid of gap × price × ADV gates shows: **open→close is negative in essentially every corner** — gap-ups systematically FADE, cross-regime. **There is no long +EV corner anywhere** (every positive-looking cell's CI spans zero). The fade is a real, robust, cross-regime SHORT edge (3 cells CI-separated, strongest = 30%+ gaps/$5-20 at −5.0%) — **but it is mostly uncapturable**: the biggest fades are hard-to-borrow / no-locate with catastrophic squeeze tail-risk (the rockets), and the only cheaply-shortable corner (liquid, higher-priced) fades only ~−0.5% (thin, low-Sharpe). **This is why the open is efficient and why the bot bleeds: buying gap-ups at the open is buying a systematic fade, and the correct (short) side is locked behind borrow costs + squeeze risk.**

## The scan (open→close basket mean, pooled 2024+2025, day_aggs)
Pattern: **the fade deepens with gap size and shrinks with price/liquidity.**
| corner | pooled 24+25 | CI | note |
|---|---|---|---|
| 30%+ / $5-20 / ADV<10M | **−5.0%** | [−7.7,−2.4] | biggest gaps fade hardest; least borrowable |
| 8-15% / $0.50-2 / ADV<10M | **−2.4%** | [−3.3,−1.4] | low-float small-cap fade |
| 3-5% / $0.50-2 / ADV>10M | **−1.6%** | [−2.1,−1.0] | |
| (typical mid cells) | −0.4% to −1.0% | mostly CI<0 | the fade is pervasive |
| 3-5% / $50-200 / ADV>10M | −0.6% | [−0.7,−0.5] | liquid → the only *cheaply shortable* fade, but thin |
| (best long cells) | +0.5% to +1.8% | **all CI span 0** | no long +EV survives |
Every one of the ~40 cells is negative-or-CI-spans-zero for the long. **Zero long +EV corners.**

## Why this is the deepest explanation of the bleed
- **The long side has no edge anywhere** because the gap IS the move; the regular session is, on average, give-back. The bot's core strategy (buy gappers at/after the open) is **structurally buying the fade** — confirmed independently of doc 250's exit work and doc 245-249's selection work. The −$10K-ish cumulative bleed is the fade + costs, not a bug.
- **The short side is the correct trade but mostly uncapturable**, which is *why the fade persists* (an efficient market):
  - biggest fades (30%+ / low-price / low-ADV) are **HTB/no-locate**, 50-300%+ borrow → not shortable or the fee eats it;
  - catastrophic **squeeze tail-risk** — these are the low-float names that rip +100% (the rockets), an unbounded left tail for a short;
  - the only cheaply-shortable corner (liquid, $50-200, ADV>10M) fades only ~−0.5% gross → ~breakeven after slippage, low-Sharpe.
- The market has priced the fade into the borrow and the squeeze risk. **No free lunch on either side at the open.**

## Leads that survive (thin)
1. **Liquid gap-up fade short** (e.g., 3-5% / $50-200 / ADV>10M, −0.6% CI[−0.7,−0.5]): the one *capturable* edge — borrowable, low squeeze risk — but thin (~−0.5% gross), and must be drilled with 9:50 precision + real borrow/slippage to see if anything survives net. Low magnitude; honest expectation is ~breakeven net.
2. **Pre-gap anticipation** (doc 251 part 2, next): the open is efficient *because the gap already happened*. The untested, higher-ceiling angle is predicting the gap *before* it prints (prior-day/premarket signal → capture the 8%+ overnight move), a genuinely different and less-efficient problem.

## Actionable (live) implication
The strongest, most robust cross-regime finding of the whole arc is simply: **gap-ups fade.** A live bot that *buys* this universe is on the wrong side of a structural edge. The disciplined options are (a) don't trade the long at the open (it's negative-EV); (b) test the capturable liquid-fade short (thin); (c) move to pre-gap anticipation; (d) accept the universe is efficient and compete only on friction.

## Status
**No live change.** Decisive negative for any long-the-open strategy in any gapper corner; a real-but-mostly-unborrowable short; one thin capturable-short lead; pre-gap anticipation still open.
**Basis**: `scripts/universe_gate_scan_doc251.py` (112,015 gap-up stock-days, day_aggs, day-block CIs). **Predecessors**: 250 (low-float basket zero-EV), 235 (variance-not-expectancy). **Memory**: [[gapper-universe-no-edge]].
