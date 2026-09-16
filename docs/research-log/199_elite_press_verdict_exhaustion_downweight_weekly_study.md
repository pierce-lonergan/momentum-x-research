# 199 — ELITE-press verdict + exhaustion down-weight + weekly auto-study

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "full send the next actions" — (f) the cheap exhaustion down-weight
+ an MFCS-bucketed net view to test the ELITE-press concern, and (g) re-run the selection
study weekly.

---

## 0. (f2) The ELITE-press verdict — CONFIRMED mis-targeted

The doc-198 selection study, bucketed by MFCS:

| MFCS bucket | n | RAN% | mean MFE | mean fwd 60min |
|---|---|---|---|---|
| 0.00–0.20 | 29 | **66%** | +10.8% | **+4.09%** |
| 0.20–0.30 | 191 | 24% | +5.7% | −1.81% |
| 0.30–0.40 | 53 | 36% | +10.8% | +3.47% |
| 0.40–0.50 | 6 | 33% | +6.5% | −0.43% |
| **≥0.50 (ELITE)** | **23** | **4%** | **−1.6%** | **−6.19%** |

**The doc-178 ELITE press sizes 2× (1%→2% risk) into the single WORST bucket.** Names with
MFCS ≥ 0.50 ran just **4%** of the time and faded **−6.19%** at 60min — the relationship is
essentially *inverted* (the *lowest* bucket runs 66%). The press is doubling down on the
bot's worst picks.

**Recommendation (Pierce's call — live behavior):** flip `EXEC_ELITE_SIZING_PRESS_ENABLED=
false` now, or gate the press on a *continuation* signal (doc 188) instead of raw MFCS. I
did NOT disable it unilaterally (live competition-strategy knob), but the data is
unambiguous on 3 sessions and it is now measurable every session via the scorecard. Left
ON pending your call + a few more sessions of confirmation.

## 1. (f1) Exhaustion down-weight — shipped, flag-gated OFF

The cheap, data-backed win from doc 198: `rvol_exhaustion=True` ran **20%** vs **41%**
un-flagged. The bot already computes the flag; doc 199 adds a sizing down-weight:
- `main.py` (just before execution): if `EXEC_RVOL_EXHAUSTION_SIZE_PENALTY_ENABLED` and the
  candidate is `rvol_exhaustion=True`, multiply `position_size_pct` by
  `EXEC_RVOL_EXHAUSTION_SIZE_MULT` (default 0.5).
- **Default OFF** (observe-first) and **risk-reducing** (only shrinks size) when on. Flip
  after the auto-scorecard confirms on more sessions. 74 regression tests green (no-op by
  default).

## 2. (g) Weekly selection study — auto, on Fridays

`post_close_scorecard.py` now runs `selection_study.py --days 5` automatically on **Friday**
closes (gated by `MX_WEEKLY_SELECTION_STUDY`, default on), writing
`data/reports/selection_study_<date>.json`. The feature-separation AUC table + the MFCS
buckets re-harden every week as data accrues — so the MFCS-anti-predictive and
ELITE-press findings get more (or less) support automatically, and the 188/191/184
selection signals are continuously re-validated on OUR tape.

## 3. Why this is the disciplined "full send"

- The **measurement** (f2, g) is built and runs — non-negotiable, zero live-risk.
- The **constructive build** (f1) is flag-gated OFF and risk-reducing — ready to flip on
  data, can't hurt live.
- The **one genuinely impactful live change** (disable/gate the ELITE press) is surfaced
  with hard data and left for Pierce — because it's a competition-strategy knob, and the
  rule is: mechanical fixes unilaterally, live-behavior changes his call. The data is
  strong enough that I expect we flip it; the scorecard will make that decision on numbers.

## Appendix — files
- `config/settings.py` — `ExecutionConfig.rvol_exhaustion_size_penalty_enabled` (False) +
  `rvol_exhaustion_size_mult` (0.5).
- `main.py` — D199 exhaustion down-weight before `execute_verdict` (flag-gated).
- `scripts/selection_study.py` — MFCS-bucket breakdown (RAN% / MFE / fwd60 per bucket) + JSON.
- `scripts/post_close_scorecard.py` — Friday auto-run of the selection study.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
