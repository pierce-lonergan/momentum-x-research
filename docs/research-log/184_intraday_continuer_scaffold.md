# 184 — The Intraday Continuer Scaffold (Phase-C, end-to-end, ready for data)

**Author**: Claude Opus 4.8
**Date**: 2026-05-29 (Friday, ~11:45 ET)
**Mandate**: Pierce — "continue." Build the training-data assembler + the small
intraday continuer, ready to run the moment the doc-182 graded data accumulates.

---

## 0. What this completes

doc 183 established the bot needs its OWN **intraday** continuation model (squeeze
vs pump over 30-60 min), not the lottery's multi-day Continuer_v2. doc 182 started
collecting the data (graded rejections). This ships the **model half**, so the
Phase-C pipeline is now end-to-end:

```
doc 182 shadow logs rejections (features)  ──┐
                                             ├─► finalize_rejection_outcomes.py (labels: ran/faded)
feature_logger / trade_results (context)  ──┘
                                             └─► train_intraday_continuer.py (THIS) ──► artifact + "beats faller gate?" verdict
                                                                                          └─► (future) live shadow + A/B vs faller gate
                                                                                                └─► (future) flip D170/limit flags to CAPTURE continuers
```

## 1. Shipped

**`src/analysis/intraday_continuer.py`** — the shared contract (train == serve):
- `FEATURE_NAMES` (9): `gap_pct, rvol_log, mfcs, faller_score, minutes_since_open,
  log_price, log_float, log_mcap, is_d170`. **All produced by the momentum bot at
  decision time** (vs Continuer_v2's 22% availability — doc 183).
- `extract_features(row)` / `label_from_outcome(outcome, run_mfe=0.08)` — the
  capturable-continuation label = MFE ≥ +8% and not fully reversed.
- `IntradayContinuer.load().score(candidate)` → P(continue) — the eventual live
  shadow scorer. Returns None/loads-nothing until an artifact exists (safe).

**`scripts/train_intraday_continuer.py`** — assemble + train + judge:
- Reads all `rejection_outcomes_graded_*.jsonl`, extracts the contract, labels.
- **Leave-one-day-out CV** (no temporal leakage) → OOF AUC.
- **The whole point:** compares OOF AUC to the **faller-gate baseline** (ranking by
  `-faller_score`). A small logistic model (L2, balanced) only earns its keep if it
  separates continuers from fades BETTER than the heuristic gate already does.
- **Ship-discipline:** refuses to write an artifact unless `n ≥ min_rows` (60),
  both classes present, AND edge over baseline `≥ min_edge` (0.03). A thin/biased
  early sample CANNOT ship a bad model into the live A/B. (Refusing is a feature —
  it keeps the working heuristic until ML provably beats it.)

## 2. Validation (synthetic, since real graded data needs the restart + ~1-2 wk)

Generated 150 rows / 5 sessions with a planted signal (low float + early +
big gap → continues) and a **noise** faller_score:
- OOF model AUC **0.865** vs faller baseline **0.481** → edge **+0.384** → **SHIPPED**.
- Recovered coefficients: `minutes_since_open −1.83` (early ⇒ continue),
  `log_float −1.18` (low float ⇒ continue), `gap_pct +0.37` — exactly the signal.
- Scorer round-trip: early/low-float/big-gap → P=0.765 vs late/large-float/small-gap
  → P=0.015. Discriminates as designed.
- 9 unit tests pass (feature contract, labeling, vector order, load-missing-safe).
All synthetic artifacts cleaned up; no live state touched.

## 3. How to run it (the operational loop, once deployed)

```
# each session EOD (after doc-182 deploys on the next restart):
python scripts/finalize_rejection_outcomes.py            # grade today's rejections
# weekly (once ~60+ graded rows across several sessions exist):
python scripts/train_intraday_continuer.py               # train + judge vs faller gate
#   -> if it beats the gate, writes data/models/intraday_continuer.pkl + metrics
#   -> if not, prints the scorecard and ships nothing (keep the heuristic)
```

## 4. Status & the remaining (deliberately gated) steps

- **Ready now**: the full collect→grade→train→judge pipeline. Offline, safe, tested.
- **Blocked on**: the restart (deploy doc 179-184) + ~1-2 weeks of graded sessions.
- **Then (future docs)**: (a) wire `IntradayContinuer.score()` as a write-only LIVE
  shadow next to the faller gate (A/B on the same picks); (b) once it demonstrably
  beats the gate live, flip the staged D170 / marketable-limit flags so the bot
  actually CAPTURES the continuers it can now identify — with the model, not blindly.

This is the disciplined path to the 5% gap: the bot's defensive layer already works
(doc 181 — it avoids fades); the missing piece is identifying the *rare continuer*
and being able to enter it. The continuer model is the identifier; the staged
flags are the capture mechanism; the rejection-grader + this trainer are how the
identifier earns the right to flip them.

## Appendix — files
- `src/analysis/intraday_continuer.py` (new — contract + Scorer)
- `scripts/train_intraday_continuer.py` (new — assemble/train/judge/ship)
- `tests/unit/test_intraday_continuer.py` (new — 5 tests)
- This doc + `docs/SYSTEM_MAP/changelog.md`. No live-path code (offline scaffold).
