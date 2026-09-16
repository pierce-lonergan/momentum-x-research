# D214 Post-Mortem: Archetype Exit System

**Status:** NEGATIVE RESULT — system does not ship.
**Date:** 2026-04-07
**Commits:** aaf518a through 285e82f (8 commits)

## What We Tried

Replace the hardcoded null curve in AlphaDecayOracle with per-archetype
empirical decay curves learned from historical minute-bar data. The hypothesis:
different stock archetypes (serial gappers vs fresh gappers) have different
intraday decay profiles, and per-archetype exit timing would capture more alpha
than the static 10%/35% stop or bar-1 exit.

## What We Built

- **ArchetypeClassifier**: HDBSCAN clustering on log-transformed features
  (gap_pct, rvol, prior_gap_count), stratified by is_day2_runner.
  Min cluster size = 50. GMM fallback when HDBSCAN finds no clusters.
- **DecayCurveLibrary**: Per-archetype mean + quantile envelopes (p10-p90)
  of entry-normalized return paths over minutes 0-60.
- **ArchetypeExitStrategy**: Z-score comparison of live return vs archetype
  curve, with z-threshold exit, p25 confirmation, time-stop, and target.
- **Validation gauntlet**: 6-baseline comparison (static, bar-1, archetype,
  shuffled-archetype, single-archetype, random-curve) with bootstrap CIs,
  10-fold walk-forward CV, per-archetype PF CIs, and lookahead audit.
- **195 real minute bars** fetched from Alpaca for historical scenarios.
- **18 unit tests** covering classifier, curves, strategy, persistence.

## What the Bug Hunt Found

**Close-price normalization bug** (Smell #3): Curves were computed from
bar close prices, which understate intraday peaks by ~10x (close < high at
every bar by definition). Peaks showed 0.6-0.8% when actual highs averaged
6.77%. Fixing to (high+close)/2 blend improved best PF from 0.47 to 0.93.

This was a real implementation bug worth finding. The +98% PF improvement
from one line change validates the bug hunt process.

## Why the Curves Failed (Even After the Bug Fix)

**Phase 1 Smell #9**: Cross-archetype Spearman rank correlation between
learned curve predictions and realized returns at minutes {1, 3, 5, 10, 15, 30}:
all |rho| < 0.12, all p > 0.12. Random curves achieved similar correlations.

**Root cause**: 195 scenarios across 43 tickers, clustered into 2 weakly-distinct
archetypes (KS p=0.51), do not produce per-archetype return curves that predict
future returns at any minute. The signal-to-noise ratio at n=71-77 per archetype
is too low for the curves to be informative.

**This is not a "fix the estimator" problem.** Isotonic smoothing, GP regression,
and hierarchical pooling were considered and rejected because the underlying
data carries no signal to smooth or pool.

## Why the Label Didn't Help Either

**Difference-in-differences test**: Does archetype label predict which exit
window (T+1m vs T+15m) wins per trade?

- Archetype 0 (serial gappers): prefers T+1m (PF=0.73)
- Archetype 1 (fresh gappers): prefers T+1m (PF=1.22)
- Both prefer the same window. CIs overlap completely.

The bimodal T+1m/T+15m PF pattern is a universe-level microstructure
artifact (momentum spike then VWAP reclaim), not archetype-specific.

## What Bar-1 Taught Us

**Bar-1 PF=1.08 exactly matches dumb T+60s exit PF=1.08.** Bar-1's entire
edge is time discipline: exit in the first minute, capture the initial gap
momentum before it fades. No curve, no z-score, no cluster needed.

The T+1m and T+15m PFs are coincidentally close (1.081708 vs 1.081388) but
achieve it through different winner/loser sets (67 trades differ). Both
converge to the same PF because both capture the same underlying edge:
gap-up stocks have a brief positive-EV window that decays to zero.

## Retrain Trigger

The system will automatically check retrain eligibility when:
- Total scenarios >= 500 (currently 195, need ~30 more trading sessions)
- Each non-fallback archetype has n >= 100

Wired into `scripts/train_archetypes.py` — prints `RETRAIN_ELIGIBLE: true/false`.
When eligible, re-run `scripts/validate_archetypes.py`. If the gauntlet still
fails at n=500+, the approach is dead at any reasonable scale and should be
abandoned permanently.

## What Ships

1. Bar-1 at T+1m as the production exit (PF=1.08, confirmed)
2. Archetype infrastructure (dormant, `archetype_exit_enabled=False`)
3. Validation gauntlet (reusable for any future exit strategy)
4. 195 real minute bars (reusable for any future intraday analysis)
5. This post-mortem

## Reusable Knowledge

- **Always check price normalization.** Close vs high vs (high+close)/2 matters
  enormously for intraday curve analysis. A one-line fix produced +98% PF.
- **Run correlation tests before building estimator improvements.** Smell #9
  (30 minutes of work) saved 10+ hours of isotonic/GP/hierarchical implementation
  that would have produced nothing.
- **Test the label independently from the curve.** A label can carry coarse signal
  even when per-minute curves are noise. The diff-in-diff test is the right way
  to check this.
- **The six-baseline gauntlet correctly rejected a system that doesn't work.**
  Especially the shuffled-archetype and random-curve baselines — if your learned
  system can't beat random, the learning isn't learning.

## Open Questions

- Will the curves become informative at n=500+? Unknown. The retrain trigger
  will tell us.
- Is there a better feature space for clustering? Microstructure features
  (opening spread, tick imbalance, premarket slope) might produce sharper
  clusters, but we don't have these for historical scenarios.
- Could a simpler archetype scheme (just binary: serial gapper vs fresh) work
  as a bar-1 skip signal? The diff-in-diff says no at n=195, but serial gappers
  (35% WR) vs fresh gappers (52% WR) is a real 17pp gap in outcome rates. The
  faller detector already uses this signal (+0.15 for serial gappers). It may be
  sufficient as a gate rather than an exit modifier.
