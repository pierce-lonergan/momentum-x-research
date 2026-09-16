# 241 — Rocket detection (the reframe): the tail IS rankable ex-ante, but feature-gated. The first real signal.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "stop trying to trade all these stocks. Learn to trade ONLY the right ones — the
~1/day-to-week rocket that explodes and STAYS up. Invest the engineering in a SOTA system that finds the
rockets in our watchlist." A rare-event PRECISION problem, not a mean problem.

## 0. Rockets are real, recurring, cross-regime (the reframe is empirically grounded)
Cross-regime corpus = 10,552 gate-eligible gappers (the watchlist proxy, 2024-26). EOD-from-9:50 return:
median −0.8% (the base loses, as proven), but a fat right tail (p99 +49%, p99.9 +162%, max +453%). Rocket
= EOD ≥ +30% AND sustained (close ≥ 50% of run-high):
| def | base rate | freq | by year (24/25/26) |
|---|---|---|---|
| +20% sustained | 3.9% | ~1 / 2 days | 143/221/48 |
| **+30% sustained** | **2.05%** | ~1 / 3.5 days | 69/117/30 |
| +50% sustained | 0.85% | ~1 / week+ | 34/44/12 |
Present every year — **not a regime artifact.** "1 a day or 1 a week that explodes and stays" is confirmed.

## 1. THE GATING RESULT — the tail IS partially rankable ex-ante (unlike the mean)
Rocket-classifier (GBM, class-balanced, microstructure-at-9:50 features), CPCV OOS, cross-regime:
- **AUC(rocket): 2024 0.645, 2025 0.719, 2026 0.831, pooled 0.711.** All years > 0.64 → the extreme tail
  carries a real, cross-regime ex-ante signal. **The KS-on-the-bulk homogeneity tests (235/240) MISSED
  this** — they're insensitive to a thin right-tail difference. *Focusing on the tail found signal where
  the aggregate mean had none. The reframe is validated as a direction.*

## 2. BUT it is NOT yet tradeable — the top slice is loser-dominated, regime-dependent
Realized FORWARD (EOD-from-9:50) return of trading ONLY the top-scored slice:
| slice | 2024 | 2025 | 2026 | rocket% (lift) |
|---|---|---|---|---|
| top 1% | **−6.9%** | **−3.0%** | +6.2% | 0–12% (up to 6.6×) |
| top 2% | −4.6% | −3.8% | +8.3% | 3–12% |
| top 5% | −1.2% | −1.2% | +4.7% | 6–12% |
| top 10% | −1.4% | −1.7% | +5.4% | 5–9% |

- **Negative mean in 2024 AND 2025; positive only in 2026.** And the **median of the top slice is −5% to
  −7% in *every* year** (even 2026) — the slice is **dominated by losers**; the rockets are still rare
  within it (6–12%), and only in 2026 were they big/frequent enough for the mean to clear.
- **Diagnosis:** at 9:50 with 5-min OHLCV, a rocket and a pump-that-will-fade **look identical** (both up
  big, high RVOL, above VWAP). The classifier catches *both* → the slice is rocket-enriched but
  loser-heavy → loses cross-regime. The rocket-vs-fader *discriminator* is not in the data we have.
- **The quantified bar:** with rockets ≈ +50% and losers ≈ −7%, a traded slice breaks even at
  ~12% rocket-precision (rate·50 > (1−rate)·7 → rate > ~12%). Current features hit ~12% only in 2026's
  top-1-2%; 2024-25 slices sit at 3–6% — **precision must roughly DOUBLE to be tradeable cross-regime.**

## 3. Why this is the most hopeful result of the arc — and where to invest
Unlike everything before (selection mean dead, overlay no-op, archetypes homogeneous), **the tail has a
real cross-regime ranking signal (AUC 0.71, lift 6.6×).** The limitation is **PRECISION**, and precision
is a **FEATURE** problem (acquirable), not an "absence of signal" problem. The rocket-vs-fader
discriminator is exactly what the doc-187 deep research validated and **we do not currently have**:
- **Tick/L2 order-flow** — OFI scaled by book depth (real bid wall vs hollow), the tape (block/sweep
  prints, accumulation vs distribution), VWAP **reclaim-vs-rejection** dynamics. (Polygon trades+quotes.)
- **Float & rotation** — float-rotation rate, premarket-volume/float (low-float rockets behave differently).
- **Premarket structure** — premarket VWAP hold, gap *quality* (news-backed vs hollow).
- **Catalyst quality** — the genuine FDA/clinical/M&A tag (doc-240 nominal-high biotech), kappa-validated.
None of these are in our 5-min-OHLCV corpus. **This is the engineering investment Pierce asked for: acquire
the richer ex-ante microstructure/float/premarket/catalyst features and build the SOTA rocket-classifier on
them** — then the precision can plausibly reach the ~12% bar where the top slice flips positive cross-regime.

## 4. Verdict & the program (BET#3 — rocket detection)
- **The reframe SURVIVES the first gate (the tail is rankable cross-regime) — the first thing this session
  has that does.** It is **NOT yet tradeable** (slice loser-dominated, precision too low outside 2026).
- **The path is data-acquisition, not model-tuning:** the rocket-vs-fader signal lives in tick/L2/float/
  premarket/catalyst features we don't have. Acquiring them is the concrete, justified engineering bet.
- **Rigor bar (unchanged):** once richer features exist, pre-register the rocket-classifier with cross-
  regime CPCV; the decision metric is **realized return of the traded slice, positive in ALL regimes**
  (not AUC — AUC was real here and still lost money, the session's iron law). Honest prior: hard, but for
  the first time there's a real signal to amplify and a concrete acquirable lever, not a dead end.

**No live change.** **Basis**: `scripts/rocket_classifier_doc241.py` on 10,552 cross-regime gapper
ticker-days. **Predecessors**: 235/240 (mean+archetypes homogeneous — the tail was hiding under the KS
bulk), 187 (the validated microstructure features we lack), 230 (P&L is tail-driven).
