# 198 — Selection study: what separates the continuers from the faders

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(e) a small selection study — mine the feature logs for what
separated the winners from the faders."

---

## 0. The prize is real (and big)

`scripts/selection_study.py` labeled every BUY candidate across 3 sessions (5/27–5/29)
by its forward path — **RAN** (max favorable excursion ≥ +10% within 60min) vs **FADED**:

> **n = 302 BUY candidates · RAN 86 (28%) · FADED 216 · mean MFE: ran +23.3% vs faded −0.2%.**

So **~28% of the bot's picks run, and the runners average +23% MFE.** Selecting that ⅓ is
the entire 5% story. The question this doc answers: *what, at decision time, separated
them?* (Rank-based AUC: 0.50 = no separation; >0.50 = higher feature → RAN.)

## 1. The damning finding: MFCS is ANTI-predictive

| feature | AUC | sep | ran-mean | fade-mean | direction |
|---|---|---|---|---|---|
| **gap_pct** | **0.36** | **0.14** | 0.46 | 0.55 | **bigger gap → FADE** |
| current_price | 0.38 | 0.12 | $5.3 | $9.3 | lower price → RAN |
| cs.volume_rvol | 0.39 | 0.11 | — | — | higher → FADE |
| **mfcs** | **0.40** | **0.10** | **0.251** | **0.300** | **higher MFCS → FADE** |
| market_cap | 0.59 | 0.09 | — | — | higher → RAN |
| rvol | 0.42 | 0.08 | — | — | higher → FADE (exhaustion) |

**rvol_exhaustion flag: ran 20% (flagged) vs 41% (not).** has_news_catalyst, debate-
qualified: no signal.

**The headline: MFCS — the bot's primary selection score — inversely ranks continuation
within its own BUY set.** Faders score *higher* MFCS (0.300) than runners (0.251). That is
the mechanical root of the selection problem documented all session: the bot's funnel
ranks its picks by a score that, among the names it wants to buy, is **negatively**
correlated with running.

## 2. Three concrete, actionable, counterintuitive signals

1. **Bigger gaps fade** (gap_pct, the #1 separator). The +55% gaps faded; the +46% gaps
   ran. The bias toward the most explosive gappers is **backwards** — a moderate-gap
   preference would improve selection.
2. **Lower price runs** ($5.3 vs $9.3). Cheaper small-caps continue more often.
3. **Exhaustion is real and already computed**: `rvol_exhaustion=True` ran **20%** vs
   **41%** — a ready-to-use down-weight signal. High raw RVOL → fade (extreme premarket
   volume = exhaustion, not fuel).

## 3. Cross-check against doc-187 (the research holds)

- **Confirmed**: premarket/extreme RVOL does NOT predict continuation — it inversely does
  (doc-187 debunked "high PM RVOL = real gap"). This study independently reproduces it on
  OUR tape.
- **Motivates 188**: the features that *should* separate per doc-187 — **opening-5min
  RVOL** and **VWAP reclaim** — aren't in the feature log yet (doc 188 adds opening-range;
  VWAP-distance was added doc 187). Once they log live (Monday+), re-run this study; they
  should top the table where premarket RVOL fails.

## 4. Implications (what to build, measured)

- **The selection lever is NOT "trust MFCS more" — it's rebuild ranking on the separating
  features**: prefer moderate gaps + lower price + NOT-exhausted + (once live) confirmed
  opening-RVOL/VWAP. Feed exactly these to the **184 intraday continuer** (it already has
  gap, rvol, mfcs, price; this says *which sign* each should carry).
- **Reconsider the doc-178 ELITE sizing press** (size UP at MFCS ≥ 0.50): if higher MFCS
  fades more, the press may be **sizing into faders**. This is now measurable — the
  scorecard's net P&L by MFCS bucket will confirm. Flag, watch, likely gate the press on a
  continuation signal instead of raw MFCS.
- **A cheap immediate win**: down-weight `rvol_exhaustion=True` names (20% vs 41% run
  rate) — a one-line sizing/gate tweak backed by data, not anecdote.

## 5. Caveats

- **n = 302 over 3 days**; directional, not definitive. The auto-scorecard + re-running
  this weekly will harden it. Treat AUC/rank (robust), not the means (outlier-skewed by
  the +400% rockets), as the signal.
- All candidates are `final_action=BUY` — this measures ranking WITHIN the buys, not the
  BUY/NO_TRADE gate. (MFCS may still gate correctly while ranking continuation poorly.)
- Label = MFE ≥ +10% ("ran"); a name can run +10% then fade — net P&L (doc 196/197) is the
  realized story. This study is about *selection*, the upstream lever.

## Appendix — files
- `scripts/selection_study.py` (new) — load BUY candidates (multi-day) → fetch forward
  path → label RAN/FADED → rank features by AUC + boolean ran-rate splits → `--json`.
- Output: `data/reports/selection_study.json`.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
