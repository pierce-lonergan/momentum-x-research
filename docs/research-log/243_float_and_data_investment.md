# 243 — BET#3 feature push: float is redundant; the L2/true-OFI path is OPEN; the data investment, scoped.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "do all three [L2 quotes / more tick history / float+catalyst], keep pulling features
that select rockets, then consider a (possibly week-long, checkpoint-validated) SOTA model."

## 1. FLOAT — built, tested, does NOT add (shares-outstanding proxy redundant)
ticker_details has `share_class_shares_outstanding` + `market_cap` (81% coverage). Univariately the rocket
signature IS there: **rockets have ~half the float** (median 16M vs 31M shares) and **4-5× the premarket
rotation** (pm_vol/share 0.004-0.005 vs 0.001). But on the model (`scripts/add_float_features_doc243.py`,
CPCV OOS):
- **FLOAT-only AUC = 0.50** (chance); adding float to micro+tick **drops** AUC 0.689→0.671 (lift −0.018,
  CI incl 0, ns) at both rocket thresholds.
- **Why:** the rotation signal is already captured by the tape volume features (pm_vol, rvol_cum), so raw
  share-count is redundant + noisy on 34-73 rockets; and **shares-outstanding ≠ free float** (locked-up
  insider shares pollute it — the true low-float signal needs *free* float, which we don't have).
- **Verdict:** the **tape is the signal** (doc 242), not the float proxy. A *true free-float* source
  (Finnhub) could be revisited, but it's low priority — the tape dominates.

## 2. L2 / TRUE-OFI — the path is OPEN (API-reachable), and it's the highest-EV remaining feature
- doc 242 used **tick-rule** OFI (a *proxy*). The doc-187-validated signal is **true book-imbalance OFI**
  (trades classified by the NBBO at trade time — Lee-Ready), which needs quotes.
- **The Polygon quotes API is reachable** (`/v3/quotes/{ticker}`, status OK). So **true OFI is buildable
  via TARGETED API pulls** for a sample of rocket+control ticker-days' early windows — **NOT gated by the
  disk wall** that blocks the flat-file route.
- **Recommended next build (highest EV):** pull early-session (premarket+9:30-9:50) NBBO for a sample of
  the tick-covered rocket + matched non-rocket ticker-days, compute true OFI / quoted-spread / depth-at-
  touch / VWAP-reclaim, and test whether they beat tick-rule OFI on rocket discrimination. *If true OFI
  materially beats tick-rule, that's the green light to fund the full cross-regime build.*

## 3. The DISK constraint — the full flat-file pulls don't fit (the honest blocker on "all three")
- **254 GB free** (1.9 TB drive, 87% used; trades_v1 alone is 451 GB).
- **Full extra tick history** (2024 + 2025-H1 + 2026-05+, ~19 months × ~40 GB) ≈ **760 GB** → **won't fit.**
- **Full L2 quotes flat-files** (quotes ≈ 3-5× trades volume) ≈ **1-2 TB** → **won't fit.**
- So 2 of the 3 full pulls are **disk-blocked**. Paths: (a) provision more disk (the clean fix for the
  cross-regime tick history + a quotes warehouse); (b) **targeted API sampling** (works now for the L2
  proof-of-concept, §2); (c) prune/compress (low value vs cost).
- **Status of "all three":** FLOAT = done (negative); **L2/true-OFI = path OPEN via API (build next)**;
  more-tick-history = disk-blocked (needs disk); catalyst = blocked (news fetch + the Together-403 LLM tagger).

## 4. The model — premature; data is the bottleneck, not model capacity
Pierce floated a week-long, checkpoint-validated SOTA model. The disciplined sequencing:
- **Now:** GBM is the right tool. With **34-73 rockets** in a 9-month window, a high-capacity model
  (transformer/deep net) would **overfit** — more capacity can't manufacture signal that the data doesn't
  yet contain. The within-window tape result (AUC ~0.68) is what we have.
- **The unlock is DATA, not architecture:** more rockets (cross-regime tick history) + true-OFI/L2 features
  + free-float + catalyst. Once those exist (thousands of rockets, richer features), a bigger model + the
  checkpoint-validation idea becomes appropriate — as the **last** step, on data that can support it.
- **The honest bar stays:** realized **traded-slice return positive in every regime** (not AUC; AUC was
  real in doc 242 and is still not P&L). The model is the amplifier of an edge the *data* must first show.

## Verdict & sequenced plan
- **The tape carries real rocket signal (doc 242); float doesn't add; L2/true-OFI is the next feature and
  it's accessible.** Float is closed (negative); the green shoot stands.
- **Sequenced investment:** (1) **true-OFI-via-API sample test** (cheapest proof the L2 lever is real, no
  disk needed) — *do this next*; (2) **provision disk** + pull cross-regime tick history → the powered,
  pre-registered cross-regime rocket test; (3) **free-float (Finnhub) + catalyst** features; (4) **THEN** a
  higher-capacity model with incremental-checkpoint validation, on data that can support it.
- **No live change.** Everything remains research toward a SOTA rocket-finder; the gate is unchanged
  (positive traded-slice return, cross-regime).

**Basis**: `scripts/add_float_features_doc243.py` + the quotes-API reachability probe. **Predecessors**:
242 (tape adds rocket signal), 241 (rockets rankable, feature-gated), 187 (true-OFI is the validated signal).
