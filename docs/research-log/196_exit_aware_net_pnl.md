# 196 — Closing the fidelity loop: exit-aware NET P&L

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(c) close the fidelity loop with exit-aware net P&L."

---

## 0. The last gap: gross capture ignored the exit

docs 192/195 measured **gross capture** — fill price → a fixed +60min close. That ignores
the three things that actually determine realized P&L: the **stop** (caps fader losses),
the **D122 exit intelligence** (times the upside), and the **hold to EOD**. So a fader
"captured" its full −9% fade; in reality the stop would have cut it at −5.5%. This doc
closes that gap.

## 1. What it does — reuse the LIVE exit machinery

Rather than re-implement exits, the backtest now **reuses `phase3_replay_simulator`** (the
investigation found it mirrors `exit_intelligence.py` with high parity): `SimPosition`,
`SimBar`, `D122ExitEvaluator` (the 6 parallel strategies — VelocityEngine, Pullback,
VolumeExhaustion, Gratitude, CatalystHalfLife, AlphaDecayOracle — with upgrade-only
semantics), and `replay_position` (stop-loss → D122 exits → EOD close).

For each candidate, under PASSIVE and MARKETABLE fills (at the realistic ~2-min window),
the fill is replayed to EOD through that machinery → **realized net P&L per name**. The
D122 evaluator uses the **live config** (`parallel_exit_min_confidence` 0.85,
`min_strategies_for_exit` 2). Fetch extended to ~EOD (`_FETCH_MIN=400`) so the replay can
run to the close. Still train==serve (PROD `compute_marketable_limit` + the D122 mirror).

## 2. The finding — stops transform the distribution

| Day | Selection (win% / median) | NET realized (marketable) | NET edge (mkt − passive) | Exit mix |
|---|---|---|---|---|
| 5/28 (good) | 54% / +1.6% | **+3.38%** | **+1.68%** | D122 73%, stop 27% |
| 5/29 (bad)  | 3% / **−8.92%** | **−1.98%** | −0.84% | D122 57%, **stop 43%** |

- **The stop caps the downside.** On 5/29 the BUY set's *gross* median was −8.92% (3%
  win) — catastrophic — but the *realized net* was only **−1.98%**, because 43% stopped
  out at −5.5% and the rest exited via D122/EOD. **Realized P&L is far less volatile than
  raw selection** — the risk machinery does its job. (This is also why the bot survives
  blind-funnel days near break-even rather than down 7%.)
- **The marketable net edge is +1.68% (good) / −0.84% (bad)** — still selection-
  conditional, but **smaller than the gross ±2–3%** (doc 195), because the shared exit
  logic compresses the entry-price difference. Keep 189/190 ON (net-positive when
  selection is good); it remains a multiplier on selection, not a standalone edge.

## 3. A metric bug I fixed: the selection MEAN lies

The selection **mean** read "+3.49%" on 5/29 — a 3%-win day looking *positive* — because a
single small-cap rocket (+hundreds of %) skewed it. Small-cap return means are dominated
by rare lottery tickets. **Fixed: lead with WIN% and MEDIAN** (5/29 median −8.92% — the
honest signal). The scorecard now reports `selWin% / selMed / netReal / netEdge`; the mean
is kept but flagged as outlier-skewed. This is itself a fidelity fix — the headline metric
must not be foolable by one outlier.

## 4. The fidelity ladder is now complete

1. **doc 192** — `LOW≤limit` proxy, gross capture. (Made 189/190 look like ±0.2% noise.)
2. **doc 195** — Alpaca NBBO fill rule + 45s submission staleness. (Edge is really ±2–3%.)
3. **doc 196 (this)** — exit-aware NET P&L (stop / D122 / EOD), honest selection metric.
   *The realistic number.*

The backtest now models the full chain — **SELECT → FILL → EXIT** — and reports realized
net P&L, win%, median, and the marketable net edge. The scorecard (doc 193/194) inherits
all of it, so `measurement_trend.jsonl` carries the realistic headline from Monday on.

## 5. Caveats (the remaining ceiling)

- **Single-exit**: stop / one D122 exit / EOD — tranche partial-sells (sell ⅓ at T1…) are
  NOT modeled, so the upside on big runners is slightly under-counted.
- **Stop is a config-% approximation** (`--stop-pct 0.055`); the live wide-arm stop is
  ATR-based and wider — sweepable via the flag.
- **Modeled NBBO** (spread model, not real historical quotes) — the realism ceiling.
- D122 parity is high but not exact (no D142 phase-stop tightening, no portfolio risk).

These are refinements; the dominant exit dynamics (stop caps losses, D122/EOD time the
upside) are captured, and the SELECTION/win%/median signals are robust to all of them.

## Appendix — files
- `scripts/fill_model_backtest.py` — `--net-pnl`/`--net-window`/`--stop-pct`; reuses
  phase3_replay `SimPosition`/`SimBar`/`D122ExitEvaluator`/`replay_position`; selection
  reported as win%/median (mean flagged); `selection_median_ret` + `net_pnl` in `--json`.
- `scripts/post_close_scorecard.py` — table now `selWin% / selMed / netReal / netEdge`;
  trend row carries them.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
