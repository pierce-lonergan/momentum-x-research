# 250 — The asymmetry-harvest fails: the gapper universe at 9:50 is ZERO-EXPECTANCY (negative after costs). Selection, exit-asymmetry, AND regime-timing all fail — the edge must come from a different game.

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — pursue (1) execution asymmetry-harvest AND (2) regime-timing, after selection closed (doc 249).

## TL;DR (the deepest closure of the arc)
A **broad gapper basket** (all 10,254 candidates, equal-weight, 09:50 entry) returns **~0% gross under EVERY exit policy** — EOD, fixed stop, trailing, and the asymmetric "cut-faders-fast / ride-rockets-wide." Best cross-regime (pooled 2024+2025) is **−0.04%, 95% CI [−0.3, +0.2]** — indistinguishable from zero. The universe **mean `eod` is −0.19%, median −0.8%**, entry price median **$4.94** — so after a realistic ~1% small-cap round-trip spread/slippage, the basket is **clearly negative**. **Neither selection (docs 245-249), exit-asymmetry, nor regime-timing extracts positive expectancy.** The low-float gapper universe at the 9:50 open is a **zero-expectancy, high-variance lottery** — efficiently priced. The strategy has **no inherent edge in this game**; the edge, if one exists, must come from a *different* universe / timeframe / signal.

## The backtest — broad-basket mean realized return per regime, by exit policy
| policy | 2024 | 2025 | 2026 | POOL 24+25 (95% CI) |
|---|---|---|---|---|
| EOD hold | −0.21% | −0.38% | +1.06% | −0.31% [−0.8, +0.3] |
| stop −5% | −0.05% | −0.04% | −0.10% | −0.04% [−0.3, +0.2] |
| stop −8% | −0.22% | −0.10% | +0.81% | −0.15% [−0.4, +0.2] |
| trail 15% | −0.30% | −0.48% | +0.08% | **−0.41% [−0.6, −0.2]** |
| trail 25% | −0.09% | −0.48% | +0.80% | −0.32% [−0.6, +0.0] |
| **ASYM −5%/trail20** | +0.05% | −0.09% | −0.30% | −0.04% [−0.3, +0.2] |
| ASYM −8%/trail30 | −0.16% | −0.08% | +0.52% | −0.11% [−0.4, +0.2] |
(Real intraday minute paths, 09:50→16:00, intrabar stop on the low / trail off the running high. Validated: EOD-hold ≈ corpus `eod` mean per regime.)

## Why the asymmetric exit can't rescue it (the mechanism)
The asymmetric policy works *as designed* on the distribution — it **rides the rockets** (+30% mean on the 2% rockets, p95 +15%) and **truncates the left tail** (basket mean rises from EOD −0.31% to −0.04%). But: **win-rate 33%, median trade −5.0%** — two-thirds of trades hit the stop, and their −5% losses exactly offset the rare winners. **You cannot manufacture expectancy from a zero-expectancy entry by reshaping the exit.** doc 235's "buys variance not expectancy" now has its execution corollary: *the asymmetric exit reshapes the variance; it does not create a positive mean.*

## Regime-timing (#2) is answered too
The only positive cell is **2026 EOD-hold (+0.6 to +1.1% gross)** — small, recent, regime-specific, and **~breakeven after costs**. Timing a +1%-gross regime profitably (net of the ~1% small-cap spread) is not viable, and the regime signal itself is what the doc-246 forward shadow already tracks passively. There is no regime rich enough to time into profitably here.

## The complete closure (selection + execution + regime)
| approach | result |
|---|---|
| ex-ante SELECTION (structure, catalyst, L2, float, tape, SOTA archs, free-float, news) — docs 235-249 | no cross-regime edge; the rocket signature = the violent-fader signature |
| EXECUTION asymmetry-harvest (broad basket + asymmetric exits) — doc 250 | **~0% gross, negative after costs** |
| REGIME-timing — doc 250 | best regime +1% gross → not net-profitable |
The oracle ceiling is +40% (the rockets are real), but it is **ex-ante unreachable AND not harvestable by exits** — the universe is efficiently priced at the open. **The 9:50 low-float-gapper-momentum game has no extractable edge.**

## Strategic implication (the honest, important part)
The bot's core strategy — buying low-float gappers around the open — is, on this evidence, a **zero-expectancy game that is negative after costs.** Its cumulative bleed is **structural, not a bug**: better selection, exits, or timing cannot fix a zero-EV entry. To have a durable edge, the program needs a **different game**, e.g.:
- a **different universe** (not low-float gappers — these are efficiently priced at the open);
- a **different timeframe** (the intraday open may be the *most* efficient window; multi-day swing, or pre-gap *anticipation*, are untested here);
- a **different signal class** entirely (the predictive information is not in 9:50 price/volume/tape/catalyst).
Or accept the universe is a coin-flip and compete only on **friction** (lowest cost / best fills / variance control) — a defensible operational posture vs Gemini, but not an alpha edge.

## What is NOT lost (the durable assets)
- The **research method** — pre-registered, cross-regime, cost-aware, AUC-retired, cheap-falsification-first — is strategy-agnostic and is exactly what found this cheaply (days, not months). It will keep the program from funding no-edge bets.
- The **infrastructure** — the data warehouse (corpus, 120M cached trades, minute/day aggs), the forward shadow, the phantom-P&L-hardened execution path — is reusable for *any* strategy.
- A **definitive negative**: we now know, rigorously, that this universe at this entry has no edge — which redirects all future effort productively.

## Status
**No live change.** This closes the rocket arc AND the broader "is there an edge in the 9:50 gapper universe" question: **no** — not via selection, exits, or regime-timing, and negative after costs. The constructive next step is a deliberate decision about *which different game* (universe / timeframe / signal) to test next, with the same rigor.
**Basis**: `scripts/rocket_basket_exits_doc250.py` (real intraday paths, 10,254 candidates, 7 exit policies, day-block CIs). **Predecessors**: 249 (selection closed), 235 (variance-not-expectancy), 246 (forward shadow). **Memory**: [[bet3-rocket-detection]].
