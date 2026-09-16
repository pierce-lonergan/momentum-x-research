# 244 — True L2 OFI does NOT beat tick-rule. L2/float investments killed; the lever is cross-regime POWER.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "do both" (the true-OFI-via-API test + get disk). Full send.

## The true-OFI test — L2 quotes add NOTHING over the trades-only tape
`scripts/build_true_ofi_doc244.py`: pulled 9:30-9:50 NBBO via the Polygon quotes API for 333 ticker-days
(73 +20% rockets + 260 controls), aligned to the local trade tape via **Lee-Ready** (each trade signed by
the prevailing NBBO midpoint), computed **true book-imbalance OFI + quoted spread + depth-at-touch ("real
bid wall")**. 96% coverage (319/333). Result (oversampled rocket-discrimination, CPCV OOS):
| feature set | AUC | vs tick-rule |
|---|---|---|
| micro + tick (tick-rule OFI) | **0.699** | — |
| micro + tick + **TRUE L2 OFI** | 0.692 | **lift −0.006, CI [−0.033, +0.021] — ns** |
| TRUE-OFI-only | 0.581 | (weak) |
- **True L2 OFI does NOT beat the tick-rule proxy** — adding it (and depth-at-touch / spread) is a no-op
  (slightly negative, CI includes 0). The two OFI measures even **disagree in sign** on rockets
  (tick_ofi −0.049 vs true_ofi +0.048) → OFI is a *weak, noisy* rocket signal either way.
- **Implication:** the doc-242 tape lift is coming from **volume / intensity / large-prints**, not from
  OFI precision. The expensive part of doc-187's thesis (true book-imbalance OFI > tick-rule) **does not
  transfer to rocket detection on these names.**

## The money-saving conclusion: kill the L2 (and float) investments
Of the three "richer feature" investments Pierce greenlit, two are now tested and **negative**:
| investment | verdict |
|---|---|
| **L2 quotes / true OFI** (doc 244) | **NO lift over tick-rule → NOT justified.** Do *not* build the 1-2 TB quotes warehouse. |
| **Float** (doc 243) | No lift (shares-outstanding proxy redundant; true free-float unavailable) → not justified. |
| **More tick history** (cross-regime) | **The one justified investment** — see below. |
**Only the trade tape (which we already have, 451 GB) carries rocket signal.** This *saves* the largest
data spend (L2 quotes) — the no-disk API test paid for itself by killing a 1-2 TB pull that wouldn't help.

## The refocused lever: cross-regime POWER (not richer features)
doc 242's green shoot (tape adds rocket signal, +0.05-0.07 AUC, CI excl 0) stands — but it is
**underpowered & within-window** (34-73 rockets, 9 months). The binding need is **not more feature types
(L2/float tested, don't help) — it's more ROCKETS across regimes** to confirm the tape signal holds
out-of-period and converts to a positive traded-slice return. That requires more **tick history**, which
needs disk:
- **Disk reclaim CONFIRMED:** raw `trades_v1` is CSV.gz with the *exact same coverage* as the parquet copy
  (2025-08→2026-04) → **redundant; safely deletable for +451 GB** (re-derivable from Polygon S3), taking
  254 GB → ~705 GB free — enough for the cross-regime tick pull. (Pierce's call to delete; or attach a drive.)

## Refocused BET#3 plan (tightened by the negatives)
1. **Reclaim 451 GB** (delete redundant raw `trades_v1`) or attach a few-TB drive.
2. **Pull cross-regime tick history** (2024 + 2025-H1 + 2026-05+, Polygon trades flat-files) → 3-4× more rockets.
3. **Re-run the doc-242 tape rocket-classifier, PRE-REGISTERED cross-regime** (CPCV); decision = **realized
   traded-slice return positive in ALL regimes** (not AUC). This is the real gate.
4. **Dropped:** L2 quotes + float (tested, no lift). Catalyst remains a separate, lower-priority blocked item.
5. **Model:** still the LAST step — a higher-capacity / checkpoint-validated model only once cross-regime
   data gives thousands of rockets. Data (rockets), not capacity, is the bottleneck.

## Honest status
- **Two clean negatives that focus the program:** L2 quotes and float don't help; the tape does. We now
  know *exactly* what to invest in (tick history for power) and what to skip (L2 quotes, float) — and the
  L2 kill alone saves the largest spend.
- **The green shoot (tape → rockets) is still unconfirmed cross-regime** and still must clear the
  traded-slice-return-positive-in-all-regimes bar. AUC ≠ P&L remains the iron law.
- **No live change.**

**Basis**: `scripts/build_true_ofi_doc244.py` (333 ticker-days, Polygon quotes API + local trade tape,
Lee-Ready). **Predecessors**: 242 (tape adds rocket signal), 243 (float doesn't), 187 (true-OFI thesis —
which does *not* transfer here).
