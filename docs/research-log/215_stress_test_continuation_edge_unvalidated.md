# 215 — Stress test #3: the doc-187 continuation edge is UNVALIDATED on our data

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "next suspects." The doc-187 continuation features underpin docs
188/191/192 (the whole continuation-edge selection layer + the faller-exemption flag).

---

## 0. The headline: it was never tested on our universe — by construction

doc 187 (deep research) ranked continuation features: opening-RVOL (#1), opening-range
direction (#2), OFI (#3), first-45-min window (#4), VWAP. docs 188/191/192 built a detector
+ a faller-exemption flag + a continuer model on these. **Stress-test finding: the
continuation features (`opening_rvol`, `opening_range_sign`, `broke_or_high`,
`vwap_distance`) are NOT in the historical feature logs at all** — they are computed ONLY
live in doc-188's observe-mode, starting ~now. So:

> **The doc-187 continuation edge has NEVER been tested on MOMENTUM-X data.** It was wired on
> the strength of external liquid-stock research — exactly the caveat doc 187 flagged ("the
> mechanism transfers but the magnitudes don't; not validated on our low-float universe").

This is not "debunked" — it's **UNVALIDATED**, and (correctly) held in observe-mode. But it
must be stated plainly: **docs 188/191/192 rest on an unproven-on-our-data premise.** The
observe-first discipline (faller-exemption flag OFF, continuer not trained) is what makes
that safe. The honest status: we cannot confirm OR deny the continuation edge until the
observe data accrues. Do NOT flip the doc-191 faller-exemption on faith.

## 1. The ONE testable sub-claim — first-window timing (RANK 4) — HOLDS (modestly)

Of all doc-187 features, only the time-of-day proxy is in the historical logs. On the
definitive run (no-cap, n=2048):
- **`minute_et` is the #1 separator** (AUC 0.40, sep 0.10 — the strongest in the entire
  study). RAN names were evaluated at **~37 min** since open vs **~41 min** for faders →
  **earlier → more likely to run.**
- CAVEAT (honest): `hour_et` is near-flat (AUC 0.52, sep 0.02). So the signal is real but
  **modest and partly confounded** (minute-of-hour ≠ pure time-since-open). Not a strong
  standalone edge — but directionally it VALIDATES, on our data, both the doc-187 "first-45-
  min" thesis and the existing **D97 stale-entry-cutoff** (block rescans after 10:30). Keep
  D97; it's earning its keep.

## 2. What this means for the continuation-edge stack (188/191/192)

- **doc 188 opening-range detector**: keep in OBSERVE-mode. It is now (since ~today) logging
  the features to the rejection-shadow; in ~2-4 weeks we'll have the FIRST on-our-data test
  of whether opening-RVOL/range/VWAP actually separate continuers here. Until then it is an
  unvalidated hypothesis, correctly not gating anything.
- **doc 191 faller-exemption**: STAYS FLAG-GATED OFF. It would exempt "confirmed continuers"
  from the faller block — but "confirmed" rests on the untested signature. Flipping it now
  would be acting on faith. (Same error class as the MFCS press flip — do not repeat it.)
- **doc 192 fill-realism / 184 continuer**: the continuer model must be trained on the
  observe data ONCE IT EXISTS, not on the (absent) historical features. The 184 scaffold is
  correct to be untrained.

## 3. The gauntlet scorecard (3 suspects tested)

| Claim | Status | Action |
|---|---|---|
| MFCS anti-predictive / ELITE press harmful (213) | ❌ **OVERTURNED** (cap artifact) | press flip was unjustified; leave OFF pending evidence |
| Fade-short ELITE +6.38% (200/201) | ❌ **FAILED** wide (−1.65%/41%, n=66) | stays flag-gated OFF |
| Marketable fill +2–3% (214) | ✅ **SURVIVES, downgraded** (~+0.7% net) | stays ON (2nd-order lever) |
| doc-187 continuation edge (215) | ⚠️ **UNVALIDATED on our data** (features never logged) | observe-only; do NOT flip 191 |
| first-window timing (RANK 4) (215) | ✅ **HOLDS, modest** (minute_et #1 sep) | keep D97 stale-entry-cutoff |

## 4. The convergent, stable truth across all 3 stress tests

Every test points the same way: **selection (which name) buys VARIANCE, not expectancy
(median fwd return is negative in nearly every bucket, every regime). The durable edges are
TIMING (earlier in the session) and EXECUTION (capture the spike via exits/sizing, don't
round-trip).** The "magic feature that picks winners" (MFCS, the continuation signature, the
fade-short) keeps evaporating under scrutiny; the boring structural levers (trade early,
exit the spike, don't carry losers overnight) keep surviving. **That is the path to 5% — and
it's the opposite of where we were spending our conviction a week ago.**

## Appendix — files
- Definitive no-cap selection study (n=2048): `data/reports/selection_study_definitive.json`.
- This doc + `docs/SYSTEM_MAP/changelog.md`. No code change (a measurement/validation finding).
