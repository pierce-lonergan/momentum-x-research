# 214 — Stress test #2: the marketable-fill "+2–3%" edge (survives, downgraded)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "next suspects." After doc 213 overturned the MFCS thesis, run the
marketable-fill claim (docs 189/190/195, **live on disk**) through the same gauntlet.

---

## 0. First: the SAME cap bug was in `fill_model_backtest.py`

doc 213 found `selection_study.py` truncated by time order (`rows[:max]`). The fill
backtest had the identical defect (`cands[:args.max]`, line 336) — a cap kept only the
EARLIEST evals of the day. **Fixed** (stratified even-spaced sample, or `--max 0` = no cap),
same as 213. The original fill runs used `--max 120/130` on days with ≤135 candidates, so
the cap mostly didn't bite — but it was the same latent bug.

## 1. The claim under test
docs 189/190/195: marketable (cross-the-spread) entry limits beat passive (rest-at-eval)
limits by **+2–3%, selection-conditional**. This is LIVE (`EXEC_MARKETABLE_LIMIT_ENABLED`
default ON). The "+2–3%" came from the GROSS sweep on essentially 2 days (5/28, 5/29).

## 2. Stress test: 5 days, no cap, exit-aware NET edge (the realistic number)

| Day | Selection (win%/median) | mkt NET edge (d122) | regime |
|---|---|---|---|
| 5/27 | 66% / +6.58% | **−0.06%** | great tape |
| 5/28 | 50% / +0.96% | **+1.56%** | good |
| 5/22 | 50% / −0.65% | **+1.52%** | mixed |
| 6/1  | 39% / −1.77% | **+1.17%** | poor |
| 5/29 | 10% / −7.98% | **−0.70%** | catastrophic |

**~+0.7% average NET edge across 5 days; range −0.70% to +1.56%.**

## 3. Honest conclusions

1. **The edge is REAL but ~half the headline.** Gross-capture shows +3%; exit-aware NET is
   ~+0.7% avg. The "+2–3%" was the gross number on 2 days — inflated. **Downgraded, not
   debunked.**
2. **Selection-conditional, but not monotonic in day quality.** Biggest on MIDDLING days
   (5/28, 5/22, 6/1) where marketable catches runners passive misses; ~ZERO on the GREAT day
   (5/27 — passive already filled everything that ran); NEGATIVE only on the catastrophic day
   (5/29 — filling more of a fader-heavy set loses more).
3. **Net-positive on 4 of 5 days, only mildly negative on the worst.** A legitimate KEEP.
4. **It corroborates doc 213's deepest finding**: median forward return is NEGATIVE on 3 of
   5 days (positive only on genuinely strong tape) → **selection buys variance, not
   expectancy.** The fill edge is a multiplier on a base that is itself often negative.

## 4. Live decision: NO CHANGE (correctly)
`EXEC_MARKETABLE_LIMIT_ENABLED` stays ON. Unlike the MFCS press (which we'd flipped on an
artifact), this claim — even corrected/downgraded — is **net-positive on average and rarely
materially negative**, so the live setting was already right. The only correction is to the
*magnitude we believe* (~+0.7% net, not +2–3%) — which matters for prioritization: fills are
a real but second-order lever, NOT the path to 5%.

## 5. Scorecard so far (the stress-test gauntlet)
- **MFCS anti-predictive / ELITE press harmful** (doc 213): ❌ **OVERTURNED** (cap artifact).
- **Fade-short ELITE +6.38%** (doc 200/201): ❌ **FAILED** wide (−1.65%/41% win, n=66) → stays OFF.
- **Marketable fill +2–3%** (doc 214): ✅ **SURVIVES, downgraded** to ~+0.7% net → stays ON.
- Still untested: the doc-187 continuation features (opening-RVOL/VWAP, liquid-validated,
  applied to low-float) — the next suspect.

## Appendix — files
- `scripts/fill_model_backtest.py` — cap bug fixed (stratified / `--max 0`).
- 5-day no-cap runs (5/22, 5/27, 5/28, 5/29, 6/1). This doc + changelog.
