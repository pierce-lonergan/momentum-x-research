# 187 — The Continuation-Edge Research (deep-research report) + Feature Translation

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Source**: deep-research workflow `wf_44e660ad-b68` — 6 angles, 27 sources fetched,
116 claims extracted, 25 verified (3-vote adversarial), **13 confirmed / 12 killed**.
**Question**: what intraday microstructure signals separate a low-float gap-up that
CONTINUES (30–60+ min) from one that FADES?

---

## 1. CONFIRMED signals (ranked, with evidence)

| # | Signal | Direction / strength | Evidence (confidence) |
|---|---|---|---|
| **1** | **Opening Relative Volume** = first-5-min vol ÷ trailing-14-day avg of that 5-min window | Higher → more continuation. **Threshold + rank** effect (RVOL≥100%, top-~20), NOT a smooth gradient. Restricting ORB to top-20 RVOL lifted IRR 3.2%→41.6%, Sharpe 0.48→2.81 | Zarattini/Barbon/Aziz 2024 (SSRN 4729284); replicated QuantConnect/Wealth-Lab. **HIGH** |
| **2** | **Opening-range direction** = sign of the first 5-min candle | Trade in the open's direction (bullish→long above 5-min high; bearish→short; doji→skip). **Don't fade the open.** | Zarattini 2024 p.5. **HIGH** |
| **3** | **Order-Flow Imbalance (OFI)**, scaled by book depth (β≈c/depth) | Buy-side OFI in a *thin* book → strong up-pressure; a deep book dampens it. Distinguishes a real (absorbing) bid wall from a hollow one. | Cont/Kukanov/Stoikov 2011 (arXiv:1011.6402). **HIGH** — but *contemporaneous*, degrades on jumps (gap-ups!) |
| **4** | **Time-of-day**: the continue/fade outcome is decided in the **first ~45 min** (9:30–10:15) | Weight all signals most early; post-10:15 = lower edge | U-shaped vol (Andersen-Bollerslev; Barardehi-Bernhardt). **HIGH** |
| 5 | Holding near premarket highs / tight consolidation | Holding PM highs → continuation; fading off them → fade | practitioner; **MEDIUM** (no quantified isolation) |

## 2. DEBUNKED — actively distrust (12 claims killed in verification)

- **Short interest / days-to-cover ALONE does NOT predict squeezes** — 0.00% spread / 0.000 IR raw; heavily-shorted names *underperform* (informed shorts), effect concentrated in small-caps. At best a *conditioning* variable; standalone it's contrarian/**bearish**. (IHS Markit; Hong et al. NBER w21166; Boehmer-Jones-Zhang.)
- **Static displayed L2 depth / "bid walls" in isolation = unreliable** (spoofing/vanishing). Only score the *interaction* of executed flow with depth (absorption / OFI), never displayed size. (Cont et al.; spoofing lit.)
- **Float rotation as a bullish squeeze loop** — REFUTED (practitioner-only, no evidence).
- **"High premarket RVOL = a real gap that continues"** — REFUTED.
- **The "first-30-min predicts last-30-min" momentum** is MARKET-level (SPY), NOT single-stock — don't misattribute (Gao et al. JFE 2018).
- **"Early extreme = likely the day's high/low"** (Edgeful stats) — REFUTED.
- The **monotonic RVOL→PnL gradient** specifically — REFUTED (only the threshold/rank effect survived).

## 3. ⚠️ The dominating caveat: universe mismatch

Every high-confidence result was validated on **LIQUID** stocks (>$5, >1M avg vol,
top-20 RVOL) — **NOT** our sub-$50 / float<50M / 20%-gap low-float universe. The
**mechanisms transfer** (abnormal volume → trending; thin books amplify imbalance;
absorption beats displayed depth), but the **magnitudes do not** (41.6% IRR, λ=0.98,
65-70% R² are large-cap numbers). And OFI's strong R² is *contemporaneous*; making it
*predictive* on the explosive-jump gap-up regime is an open question. So: **use these
as priors/features, let our OWN doc-182/184 pipeline learn the low-float magnitudes.**

## 4. Translation → the bot (what we did + the roadmap)

### Already in the continuer (doc 184) and VALIDATED by this research
`rvol` (RANK 1 — though scanner-RVOL, not opening-5min), `minutes_since_open`
(RANK 4), `gap_pct`, `log_float`/`log_mcap` (cohort), `faller_score`. The research
confirms the faller gate's instincts: it avoids fades using manip + below-VWAP — the
right direction.

### Shipped THIS pass
- **`vwap_distance`** added to the continuer contract + the rejection-shadow logging
  + the faller wiring (sourced from `FallerAssessment.vwap`). VWAP interaction is a
  top confirmed signal (the verifier hinted it may dominate PM-highs). Now collected
  from day one.

### Feature ROADMAP (next, with the DATA each needs) — ranked
1. **Opening-5min RVOL** (RANK 1, exact form): track the 9:30-9:35 volume vs the
   14-day avg of that window per name. *Data*: a small per-ticker opening-bar tracker
   (we have minute bars). HIGH value, low data cost.
2. **Opening-range sign** (RANK 2): sign of the 9:30-9:35 candle. *Data*: the first
   5-min bar (we fetch bars). Low cost. Pairs with #1 — this is the validated
   ORB+RVOL combo.
3. **OFI / depth-scaled imbalance** (RANK 3): *Data gap* — needs the **L2 book**
   (we have trades+quotes via the WS, not full depth). A real data-acquisition
   project (L2 feed) — highest ceiling, highest cost. Until then, approximate with
   trade-side imbalance (uptick/downtick signed volume) from the tape we DO have.

### DON'T build (the research says these waste effort)
- A short-interest-as-bullish feature (debunked — it's bearish/conditioning only;
  note the faller gate already uses short-interest correctly as a *fade* signal).
- A displayed-L2-depth ("bid wall size") feature in isolation (spoofing).
- A float-rotation "squeeze loop" feature.

### A STRATEGIC option (not built — for a future experiment)
The validated **ORB+RVOL entry** (Zarattini): at 9:35, rank the universe by
opening-5min RVOL, and on the top names place a stop-entry at the 5-min-range high
*in the direction of the open*. This is a cleaner, evidence-backed entry than our
current gap+MFCS+gates path. Worth a shadow/replay A/B against the current entry —
*after* the doc-182/184 pipeline confirms the features hold on our low-float universe.

## 5. Open questions (what the data must answer — feeds doc 184 training)
1. Does RVOL+opening-range-direction hold (and at what magnitude) on sub-$50/float<50M
   20%-gap names, where halts, wide spreads, HTB, and ~17% base win rates differ?
2. Can OFI be made *predictive* (not just contemporaneous) for the gap-up jump regime?
3. Optimal weighting/interaction of {opening-RVOL, opening-range sign, OFI-in-thin-book,
   VWAP reclaim-vs-rejection, absorption} — does VWAP dominate PM-high proximity?
4. Is there a *conditional* squeeze signal (high SI + HTB/high-borrow + high RVOL +
   holding above VWAP) that IS a genuine edge, even though SI-alone is not?

These are exactly what the rejection-grader (doc 182) + the intraday continuer
(doc 184) are built to answer empirically on OUR universe — the research gives the
priors and the feature menu; our data settles the magnitudes.

## Appendix — files & provenance
- Full cited report: workflow `wf_44e660ad-b68` (transcript in the session dir).
- Code this pass: `src/analysis/intraday_continuer.py` (+vwap_distance, now 10 feats),
  `src/shadow/rejection_outcome_shadow.py` (+vwap_distance), `main.py` (faller wiring).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
