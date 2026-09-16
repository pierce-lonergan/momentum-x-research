# 193 — Post-close scorecard: make quantification a habit

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "quantify the performance of our changes" (the operational half of
doc 192).

---

## 0. The gap this closes

doc 192 found two things: (1) **SELECTION dominates fills ~30×**, and (2) our measurement
was a **one-off** — the doc-182 rejection grader had been *built but never run*, and there
was no running record of selection quality. Measurement that isn't habitual is measurement
that doesn't happen. This doc makes it a daily habit.

## 1. Shipped: `scripts/post_close_scorecard.py`

Per recent session it runs — best-effort, never crashes:
- the **fill-realism backtest** (doc 192) → SELECTION win% + mean forward return + the
  marketable fill edge (`--json` summary, added to `fill_model_backtest.py`);
- the **doc-182 rejection grader** → gate-correct % (faller/D170 block correctness);

and appends one idempotent row per date to **`data/reports/measurement_trend.jsonl`**:
`{date, n, selection_win_rate, selection_mean_ret, fill_edge_2min, gate_graded_n,
gate_correct_pct}`. It prints a trend table:

```
       date |    n | selWin% |  selMean | fillEdge@2m |         gate
 2026-05-29 |  130 |      7% |  -6.86%  |      -0.37% |   100% (n=1)
```

**HEADLINE METRIC = selWin% / selMean.** That is the number to move (doc 192). `fillEdge`
is the 189/190 amplifier (2nd order). `gate` populates from Monday, when 177-191 deploy
and the rejection instrumentation finally runs live.

## 2. How to run

```bash
python scripts/post_close_scorecard.py            # last 5 sessions with feature logs
python scripts/post_close_scorecard.py 2026-05-29 # one date
python scripts/post_close_scorecard.py --days 10  # trend over 10 sessions
```

Recommended: run after every close (or wire into the launcher's Phase-4 tail). The trend
file is the dataset that will tell us — on OUR tape, day over day — whether the selection
levers (188 continuation detector, 191 faller exemption, the 184 continuer) actually move
the headline number, *before* we flip any of them live.

## 3. Why this is the right "next feature"

doc 192's data says the next *trading* feature should improve SELECTION — but the
selection levers are **data-gated** (they need the Monday-onward observe data). The
**non-data-gated** highest-value build is the instrument that produces that data and
tracks the headline metric. Build the gauge before turning the knobs. The selection
features come next, once a week of `measurement_trend.jsonl` tells us which knob moves
selWin%.

## Appendix — files
- `scripts/post_close_scorecard.py` (new) — the daily runner + trend writer.
- `scripts/fill_model_backtest.py` — added `--json <path>` summary output.
- Output: `data/reports/measurement_trend.jsonl` (+ per-date `fill_backtest_<date>.json`),
  `data/shadow/rejection_outcomes_graded_<date>.jsonl` (runtime artifacts).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
