# 183 — The Continuer_v2 Mismatch, and the Real Path to an Intraday Continuation Edge

**Author**: Claude Opus 4.8
**Date**: 2026-05-29 (Friday, ~11:30 ET)
**Mandate**: Pierce greenlit "build the Continuer_v2 shadow on momentum picks
next." I started — and on inspecting the model, found it's the **wrong tool**.
This documents why (with empirical proof), and the corrected, honest path.

---

## 0. The honest correction

I recommended shadowing the lottery's `Continuer_v2` on the momentum bot's picks
(doc 181/182 §3). **That recommendation was wrong.** It was based on an audit
agent's overstated "reuse" claim, made before anyone inspected the model's feature
contract. I inspected it, and tested it empirically, before building — and it does
not fit. Building it would have produced **misleading garbage**. Reporting that is
more valuable than shipping a bad shadow.

## 1. Why Continuer_v2 doesn't fit the intraday momentum bot (empirical)

`data/models/continuer_v2_v3_tuned.pkl` has **54 features**. Two fatal mismatches:

1. **Horizon**: it's an **end-of-d0, multi-day** model — features like
   `ret_open_close_d0`, `close_strength`, `last_5min_avg_close`, `prior_cont_rate`,
   `prior_fade_rate`, `prior_avg_t5` predict whether a name that moved *today*
   **continues over d+1..d+5**. That's the **lottery's** swing horizon. The
   momentum bot is **intraday** (enter and exit same session). Different question.
2. **Feature availability**: I scored a real momentum candidate (CGTL: gap 84.9%,
   $0.51, dvol $641K, float 20.6M, mcap $7M) through the model with only the
   features the intraday bot can supply:
   - **12 of 54 features available (22%)** — the rest (`prior_*` continuation
     stats, `rank_intra`/`rank_dvol` universe stats, full-d0-session, intraday
     path) require the polygon warehouse / end-of-day data the bot doesn't have
     live, so they **default to 0**.
   - Result: `continuer_proba = 0.176` → **SKIP tier** (ELITE needs ≥0.60).
   - Perturbing the missing continuation-priors swung it only 0.18→0.23: the model
     **floors everything near 0.18 because its discriminative features are absent.**
   - i.e. a live shadow would output "SKIP, ~0.18" for **nearly every candidate** —
     zero discrimination, worse than nothing.

**Verdict: a live Continuer_v2 shadow on intraday momentum picks is uninformative.
Not built.**

## 2. The real edge the bot needs: an INTRADAY continuation model

The momentum bot's squeeze-vs-pump question is intraday: *"this name is gapping
NOW — will it keep running the next 30–60 min so we can capture it, or fade?"*
That needs a model trained on **the momentum bot's own decision-time features +
intraday forward outcomes** — which we are now collecting:

- **Features**: `src/analysis/feature_logger.py` is ACTIVE (writing
  `data/features/features_<date>.jsonl` today) — gap, rvol, float, mcap, exhaustion
  per evaluation.
- **Labels (the missing half)**: the doc-182 rejection-outcome shadow grades the
  forward (intraday) return of every name the faller/D170 gates reject — run vs
  fade. (The feature_logger's own `record_outcome` is empty because there are ~0
  fills; doc-182 supplies the labels the fills can't.)
- **Entered names**: `trade_results.jsonl` supplies their outcomes.

Together these are the **intraday-continuation training set**. The faller gate is
today's *hand-coded, uncalibrated* stand-in for this model; doc-182 measures whether
it's right.

## 3. What shipped this turn (serving the corrected plan)

Enriched the doc-182 rejection shadow (`src/shadow/rejection_outcome_shadow.py`)
with three high-signal intraday-continuation features it was missing:
- **`minutes_since_open`** (DST-robust ET) — time-of-day is a primary squeeze-vs-
  pump discriminator (early gappers ≠ late ones).
- **`float_shares`** — low float = squeeze fuel.
- **`market_cap`** — size cohort.
Wired the faller call site to pass float/mcap from the candidate. Module tests
still 4/4 green. Write-only, safe, deploys on the next restart with everything else.

So the data we start collecting (on restart) carries {gap, rvol, mfcs, faller_score,
minutes_since_open, float, mcap} + the graded forward outcome — a usable feature set
for the intraday continuer.

## 4. The corrected Phase-C roadmap (replaces "shadow Continuer_v2")

1. **Now → +1–2 weeks**: deploy doc-182 (on restart), accumulate graded rejections
   (run/fade) + feature_logger features + trade_results. Run
   `scripts/finalize_rejection_outcomes.py` each EOD to build the labeled set.
2. **Then**: train a SMALL intraday continuation model (logistic/GBM — NOT a 54-
   feature multi-day ensemble) on {decision features → intraday run/fade}. Validate
   it beats the heuristic faller gate's precision on held-out days.
3. **Then**: shadow THAT model on live picks (write-only), A/B vs the faller gate.
4. **Only then**: if the model reliably separates squeeze from pump, flip the
   staged D170/marketable-limit flags to actually CAPTURE the continuers — with the
   model, not blindly. This is the safe path to bridging the 5% gap.

The lottery's Continuer_v2 stays where it belongs (the multi-day lottery process).

## 5. Lesson for the SYSTEM_MAP
Before wiring any shadow/model into a new process, **inspect its feature contract +
training horizon**. "A model exists for X" ≠ "it fits process Y." The 22%-feature-
availability check (one script, 5 minutes) saved us from a misleading shadow that
would have polluted the very calibration we're trying to build.

## Appendix — files
- `src/shadow/rejection_outcome_shadow.py` (+minutes_since_open/float/market_cap)
- `main.py` (faller call passes float/mcap)
- This doc + `docs/SYSTEM_MAP/changelog.md`. No model shadow built (by design).
