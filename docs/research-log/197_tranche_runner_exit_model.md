# 197 — Tranche + runner exit model (the runner-upside half of net P&L)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(d) add tranche partial-sells to the net-P&L (tighten the runner
upside)."

---

## 0. Why

doc 196's net P&L used a SINGLE-exit model (stop / one D122 intelligence exit / EOD at
full size). That under-counts runners — the MOBX case (+95% real vs single-exit +6%) is
the canonical failure. The live bot doesn't single-exit; it **scales out** (sell ⅓ at
T1/T2) and lets a **runner** ride the D163 trail. doc 197 adds that model and reports it
alongside D122, so the truth is bracketed.

## 1. The tranche+runner model (`_replay_tranche`)

Config-driven (`tranche_t1_pct` +3%, `tranche_t2_pct` +8%; `TrailingStopConfig`):
- Sell **⅓ at T1 (+3%)**, **⅓ at T2 (+8%)** — limit-style (fills when the bar high reaches
  the target). Hard stop **ratchets to breakeven after T1**.
- The **final ⅓ is the RUNNER**: rides the **D163 trail** (activate +6%, give back 30% of
  max gain, ≥5% / ≤35%) — no hard T3 sell, so a fat-tail name keeps running.
- Hard stop on un-sold qty throughout; EOD closes the remainder. Weighted realized P&L.

Convention: tranche targets (resting limits) fill on the bar HIGH first, then the
protective stop on the LOW (target-favorable scale-out, conservative protection).

## 2. The finding — an exit-posture tradeoff (not a clear winner)

| Day | model | mkt realized | mkt median | mkt win% | NET edge (mkt−passive) |
|---|---|---|---|---|---|
| 5/28 (good) | D122 | **+3.38%** | +1.62% | 60% | +1.68% |
| 5/28 (good) | tranche | +2.64% | +1.00% | **79%** | +0.98% |
| 5/29 (bad) | D122 | −1.12% | −1.22% | 39% | −0.69% |
| 5/29 (bad) | tranche | **−0.41%** | **+1.00%** | **58%** | −0.72% |

- **Tranche/scale-out is more ROBUST**: on the bad day it's −0.41% (median **+1.00%**, 58%
  win) vs D122's −1.12% — because it **locks the brief +3% T1 pop before the fade**. On the
  good day it has the higher **win%** (79%) but a lower **mean** (the scale-out caps the
  modest runners).
- **D122/ride+intelligence has the higher good-day MEAN** (+3.38%) — it rides winners to a
  velocity/volume-timed exit instead of scaling out at +3%.
- **The live bot runs BOTH** (tranche orders + D122 intelligence + the trail), so its real
  behavior is **bracketed by these two** — and the key takeaway is that **the bot is more
  robust than the D122-only view (doc 196) suggested**: a −8.9%-median selection day nets
  only −0.4% under scale-out. This is *why* blind-funnel days land near break-even.

## 3. 189/190 verdict — unchanged, confirmed across both models

The marketable NET edge is **+1.0% to +1.7% (good) / ~−0.7% (bad)** under *both* exit
models — robustly selection-conditional. Keep 189/190 ON; it's a multiplier on selection
regardless of exit posture.

## 4. What shipped

- `scripts/fill_model_backtest.py` — `_replay_tranche` (scale-out + runner trail);
  `_net_for_fill` now returns BOTH models; the net-P&L section prints a per-model table
  (mktRealized / mktMed / mktWin% / passiveReal / NETedge) + per-model exit mixes; `--json`
  `net_pnl.models.{d122,tranche}`.
- `scripts/post_close_scorecard.py` — `netReal` / `netEdge` now read the **tranche** model
  (the more live-representative, runner-aware number; falls back to d122). Trend row tags
  `net_model`.

## 5. Caveats / next fidelity

- The two models are run SEPARATELY; the live bot interleaves tranche sells WITH D122
  intelligence on the remainder — a combined model is the next refinement (the two bracket
  it for now).
- Config-% stop approximation (`--stop-pct`); modeled NBBO; the runner-trail is the doc-178
  D163 spec (give-back of max gain), not the exact per-cycle software-trail implementation.

## Appendix — files
- `scripts/fill_model_backtest.py` — `_replay_tranche`, `_net_for_fill`, two-model net section.
- `scripts/post_close_scorecard.py` — tranche-model net columns.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
