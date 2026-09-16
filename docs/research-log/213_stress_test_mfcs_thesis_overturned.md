# 213 — Stress test: the MFCS-anti-predictive thesis was a 3-day artifact (overturned)

**Author**: Claude Opus 4.8
**Date**: 2026-06-01
**Mandate**: Pierce — "continue to refine and aggressively stress-test our approaches."

This is the most important correction of the session. We **flipped a live flag**
(`EXEC_ELITE_SIZING_PRESS_ENABLED=false`, doc 199/200) on a 3-day analysis. Stress-testing
that analysis on the full data + a red-team found it was a **methodology artifact**. The
flip was **not empirically justified.**

---

## 0. What we claimed (doc 198/199, 3 days, n=302)
- MFCS is ANTI-predictive (rank-AUC 0.40, higher MFCS → FADE).
- The MFCS≥0.50 "ELITE" bucket RAN only 4% / faded −6.19% → "the press sizes into faders."
- → We disabled the ELITE sizing press LIVE.

## 1. The red-team (general-purpose agent, adversarial) found two fatal flaws

1. **A real BUG in `selection_study.py`**: `_load` did `rows[:max_per_day]` — truncating by
   **file order = TIME order**. So a cap kept only the EARLIEST N evals of the day. The
   3-day run (cap 110) and the first wide run (cap 60) therefore sampled **different times
   of day**, not different sample sizes. The 3-day "ELITE" was 57% late-morning/midday
   entries (the 12-2pm dead-zone MEMORY flags as worst); its mean MFE was *negative* (names
   that never traded above entry). The "contradiction" was partly a **cap artifact**, my own
   tool's bug — and I'd used it to flip a live flag.
2. **MFCS-AUC within the BUY set is near-meaningless**: 67% of BUYs cluster in MFCS
   0.20–0.30 (the bot only buys above a gate → range-restricted). You cannot measure a
   variable's selection power inside a set already filtered on it. Both "AUC 0.40" and "AUC
   0.50" are artifacts of a degenerate distribution + tie-handling.
3. **Underpowered**: 3-day ELITE was n=23, RAN-rate CI [0.8%, 21%]. A single name moved it
   4pp. Plus 5/29 (a catastrophic 7%-win day) was 1 of only 3 days = outsized regime weight.

## 2. The definitive re-run (bug fixed: stratified sample, NO cap, n=2048, +Wilson CIs +median)

`_load` now takes an evenly-spaced (time-representative) sample, or NO cap (`--max 0`).

| MFCS bucket | n | RAN% | 95% CI | median fwd60 | mean fwd60 |
|---|---|---|---|---|---|
| 0.00–0.20 | 203 | 17% | [13,23]% | −1.49% | −2.03% |
| 0.20–0.30 | 1298 | 25% | [23,28]% | −2.11% | −1.11% |
| 0.30–0.40 | 282 | 20% | [15,25]% | −4.06% | −0.85% |
| 0.40–0.50 | 117 | **30%** | [22,39]% | +0.00% | +0.96% |
| **≥0.50 (ELITE)** | 148 | **36%** | [29,44]% | −0.18% | −0.16% |

## 3. The honest, stable conclusions

1. **MFCS is NOT anti-predictive.** Higher MFCS → *higher* RAN% (17%→36%, ~monotonic). The
   "MFCS is broken / inverted" thesis is **DEAD** — it was a cap×regime artifact.
2. **But the ELITE edge is in RAN% (spike frequency), NOT median P&L.** ELITE median fwd60
   is **−0.18%** and its CI overlaps the 0.40–0.50 bucket. High-MFCS names *spike more often*
   (supports the doc-178 fat-tail press thesis — size up where the +30/+95% tails cluster)
   but the **median ELITE name still round-trips to roughly flat/slightly red.**
3. **The deepest, most stable finding: selection buys VARIANCE, not positive expectancy.**
   Median fwd60 is NEGATIVE in every bucket. Selection gets you a higher chance of a big
   spike (RAN%), not a positive median outcome. THIS is the real path-to-5% problem — and
   it's why exits/sizing (capture the spike, cut the round-trip) matter as much as picking.

## 4. The live-flag decision (honest)

**Flipping `EXEC_ELITE_SIZING_PRESS_ENABLED=false` was NOT justified** — it acted on the
n=23 cap artifact. The corrected data **weakly FAVORS the press** (ELITE has the highest
RAN% = the most fat-tail spikes, which is exactly what a 2%-risk press is meant to catch).

BUT: I will **NOT reflexively flip it back ON** — that would repeat the original sin
(acting on still-thin per-bucket data; ELITE n=148, CI [29,44]% overlaps neighbors). The
disciplined posture: **the press is currently OFF; leave it OFF until the daily scorecard +
weekly study (now methodology-fixed) confirm the fat-tail edge on accruing live data, then
flip it back ON with evidence.** Net effect: we under-size some good names for now — a
conservative error, not a capital-risk one. Recorded as a known, evidence-pending decision.

## 5. What this stress test changed in the TOOL (so future studies are honest)

- **Fixed the cap bug**: stratified/even-spaced sample or `--max 0` (no cap). Time-of-day no
  longer drives the result.
- **Added Wilson 95% CIs** to every MFCS bucket — underpowered buckets are now visible at a
  glance (the red-team's core ask).
- **Added median fwd60** alongside mean (means are small-cap-rocket-skewed).
- The auto-scorecard/weekly study inherit the fix.

## 6. The meta-lesson (the point of stress-testing)
We shipped fast on small samples all session. This is the first claim we **aggressively
re-tested**, and it **failed** — the tool had a bug, the stat was degenerate, the sample was
underpowered AND regime-confounded, and we'd flipped a live flag on it. Every other
small-sample claim (fade-short n=11 +9.47%, the continuation-edge features) is now suspect
by the same standard and must clear the same bar (no-cap, CI, median, multi-regime) before
it gates live behavior. **The fade-short wide re-run already showed ELITE fade-short LOSES
(−1.65%, 41% win on n=66) — so the doc-202 fade-short stays flag-gated OFF, correctly.**

## Appendix — files
- `scripts/selection_study.py` — `_load` cap bug fixed (stratified / `--max 0`); Wilson CIs
  + median in the MFCS-bucket report.
- `data/reports/selection_study_definitive.json` (n=2048, no cap), `_wide.json` (cap 60).
- This doc + `docs/SYSTEM_MAP/changelog.md`. Red-team: general-purpose agent (adversarial).
