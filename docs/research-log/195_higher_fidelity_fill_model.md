# 195 — Higher-fidelity fill model: the staleness correction

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(b) the higher-fidelity fill model" (after wiring the scorecard).

---

## 0. TL;DR — doc 192 UNDER-counted the fill edge by ~10×

doc 192's fill-realism backtest used a crude model (a `LOW ≤ limit` fill proxy, **no
submission staleness**) and concluded the marketable edge was a negligible **±0.2%**.
Two realism upgrades flip that:

1. **Alpaca's real fill rule** (the arena `AlpacaFillModel`): a limit buy fills only when
   `limit_price ≥ ask`, AT the ask (no price improvement) — not "the bar low touched my
   limit."
2. **~45s submission staleness**: the bot submits ~45s after eval (parallel eval +
   decision). By then a runner's ask is already **above** a passive limit pinned to the
   stale eval price → it never fills. *This is the real reason CMND filled 0/2.*

Under the realistic model the marketable edge is **±2–3%, sharply selection-conditional**
— an order of magnitude larger than the proxy showed, and large enough to matter.

## 1. The 3-day picture (arena fill model + 45s staleness)

| Day | Selection (BUY-set fwd) | Passive fill @1min | Mkt fill | **Mkt EDGE @1min** | Caught |
|---|---|---|---|---|---|
| 5/28 (good) | **+3.68%** | **52%** | 93% | **+3.42%** | 44 runners @ +8.3% |
| 5/26 (mid)  | −2.97% | 85% | 98% | −0.03% | 9 @ −0.2% |
| 5/29 (bad)  | **−7.10%** | 74% | 98% | **−1.97%** | 29 faders @ −8.3% |

vs the doc-192 proxy on 5/28 (which showed passive filling 65–82% and edge +1.7%): the
realistic model shows passive filling only **52%** at the 1-min window — a stale limit
genuinely misses **~half** the names on a fast day.

## 2. The corrected thesis (sharper, not overturned)

- **Selection still dominates ABSOLUTE P&L.** The BUY-set return swings +3.68% → −7.10%;
  that is the driver. (doc 192 holds.)
- **The marketable fill edge is LEVERAGE on selection — ±2–3%, not ±0.2%.** It amplifies
  whatever selection delivers: +3.4% on a good day (catches runners a stale passive limit
  misses), ~0 on a flat day, −2% on a fader day (fills more faders).
- **So 189/190 stays ON** — it is a strong amplifier exactly when selection is good, which
  is the whole goal. And it makes improving selection **even more urgent**, because the
  leverage cuts both ways.
- **The synthesis is now explicit: 188/191 SELECT the continuers → 189/190 FILL them for
  +2–3%.** Marketable fills are the multiplier on the continuation edge. Neither is the
  win alone; together they are.

## 3. Why the proxy under-counted (the lesson)

A naive sim made a real, leveraged change look negligible. The two things it missed —
the actual fill rule and the submission lag — are exactly the things that made CMND miss
live. **A sim is only as honest as its microstructure.** doc 192 caught one fill bug
(price improvement); doc 195 caught the two bigger ones (NBBO rule + staleness). The
realistic numbers are the ones to trust.

## 4. The change (`scripts/fill_model_backtest.py`)

- `--fidelity arena` (default): drives the arena `AlpacaFillModel` per bar with NBBO from
  the calibrated `SpreadModel`. `--fidelity proxy` reproduces doc 192's `LOW≤limit`.
- `--submission-delay-sec 45` (default): the order is "live" only from `ts0 + delay`; the
  MARKETABLE limit anchors to the **ask at submission** (`max(eval, ask_sub)*(1+offset)`)
  — exactly what the executor's fresh snapshot does. `0` = no staleness (doc-192 method).
- Still train==serve (`compute_marketable_limit`), still sweeps the window, still emits
  `--json`. The **scorecard now inherits the realistic defaults**, so
  `measurement_trend.jsonl`'s fill-edge column is realistic from the start.

## 5. Caveats + next fidelity

- NBBO is **modeled** from the spread model, not real historical quotes (the realism
  ceiling here). If/when tick quotes are warehoused, swap them in.
- Return is still **gross capture** (fill → +exit-min close), not net of exits — layering
  `phase3_replay`'s D122 exit logic is the next fidelity step (doc 192 §5.4).
- Validated by the proxy-vs-arena cross-check (both agree on direction; arena + staleness
  raise the magnitude, as expected from the missing realism).

## Appendix — files
- `scripts/fill_model_backtest.py` — `--fidelity {arena,proxy}` + `--submission-delay-sec`;
  arena `AlpacaFillModel`/`Bar` integration; submission-anchored marketable limit.
- `scripts/post_close_scorecard.py` — footer note corrected (fill edge = selection
  leverage, ±2–3%, not "2nd order").
- This doc + `docs/SYSTEM_MAP/changelog.md`.
