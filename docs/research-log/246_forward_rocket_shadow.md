# 246 — Forward rocket PAPER SHADOW (the one honest continuation) + the untested-lever register

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "forward paper-shadow the 2026 signal, but document the untested lever."

## Why a shadow (and why it's honest)
doc 245 closed BET#3 as a cross-regime-*robust* edge — but the **2026 regime genuinely works** (top-5% mean
EOD +10.3%, CI excludes 0; and the leave-one-regime-out model trained on **2024+2025** still ranked 2026
rockets +10.2%). Since *now* IS the 2026 regime, the one intellectually honest continuation is to **freeze a
model on all data through now and shadow it forward** — score the live gapper watchlist each day, log the
top-5%/top-1 rocket-slice, and **track its realized out-of-sample return in real time, with zero capital.**
It is explicitly a **regime bet** that will silently fail if the regime shifts; the shadow's job is to tell
us truthfully whether "now" is tradeable, day by day, before any capital is ever risked.

## The harness — `scripts/rocket_shadow_doc246.py`
- `--train` freezes `GBM(MICRO+TICK)` on the doc-245 corpus (n=10,254, 202 rockets, **cutoff 2026-06-01**).
  Saved to `data/research/rocket_shadow_model.pkl`.
- `--run YYYY-MM-DD` / `--run-since` gates the live universe (same gate: gap≥8%, open∈[$0.50,$20], ADV≥$1M),
  computes the **ex-ante-9:50 features the EXACT way the corpus did** (reuses `build_cross_regime_corpus.
  label_day` for MICRO + `build_xregime_tick_features_doc245.compute_tick_feats` for TICK → **no train/serve
  skew**, correctly windowed via raw sip_timestamp), scores, logs every candidate's score + realized EOD to
  `data/research/rocket_shadow_log.jsonl`, and flags **FORWARD-OOS** (session_date > cutoff) vs in-sample.
- `--report` summarizes the accumulated track record, separating the **honest FORWARD-OOS** rows from
  in-sample validation.

## Initial run (validation + first forward day)
| date | regime | top-5% picks | top-5% mean EOD |
|---|---|---|---|
| 2026-05-26 | in-sample | YMAT −11%, MNTS +3% | −4.0% |
| 2026-05-27 | in-sample | UZX −14%, CPSH +1% | −6.3% |
| 2026-05-28 | in-sample | RCAX +13%, OTLK +18% | +15.4% |
| 2026-06-01 | in-sample | **AIM +68% (ROCKET)**, MASK −20% | +23.9% |
| **2026-06-02** | **FORWARD-OOS** | **DBGI −34.5%** | **−34.5%** |

- **Harness validated** end-to-end (gates, windows, scores, picks, logs, reports). In-sample top-5% across 5
  days: +2.6% mean / +1.0% median / 40% top-1 hit — the expected in-regime behavior.
- **Forward-OOS so far: N=1 day, −34.5%.** This proves *nothing* yet (one bad draw; the very next-prior day
  06-01 was AIM +68%). It is a vivid reminder of the **variance**: the slice is a high-variance lottery, and
  only a multi-week forward sample can establish whether the regime-conditional mean is actually positive.
- **Operating note:** the warehouse was missing minute bars for 2026-05-29 (ingest gap) — the shadow
  correctly skipped it. To accrue the track record it must run **daily after the close** (see below).

## The untested-lever register (per Pierce — what's NOT yet falsified)
Everything tested is negative cross-regime: structure (235), catalyst-*homogeneity* (240), L2/true-OFI (244),
shares-outstanding float (243), microstructure+tape (245). **Two feature classes remain untested** — both
infra-blocked, both worth ONE clean pre-registered cross-regime test *if* unblocked:

1. **True FREE-float** (medium priority). doc 243 falsified *shares-outstanding* (redundant with tape
   volume), but the squeeze mechanic depends on **free** float (public float minus locked-up insider/
   restricted/control shares) — a different, sharper signal we've never had. BLOCKED: no free-float source in
   the warehouse. Unblock: **Finnhub `/stock/metric`** (`floatShares`) + a join keyed on ticker-day. Low
   cost. One pre-registered test: does free-float (or float-rotation = premarket_vol / free_float) add
   cross-regime top-slice return over MICRO+TICK?

2. **Real catalyst/news CONTENT** (medium-high priority — the practitioner-favorite signal). doc 240
   falsified catalyst *type homogeneity*, NOT extracted catalyst *content* as a selection feature. The
   deep-research ranks catalyst quality/novelty + **dilution risk** (toxic-lender/offering → sell-the-news,
   per the catalyst memory) among the highest-conviction continuation signals — and they've **never been
   cleanly tested** as rocket features. BLOCKED: the live news pipeline times out (Tier-2 news agent ~67%
   empty, doc 177) and the Together-AI LLM tagger 403s from the shell. Unblock: a reliable news fetch
   (**Polygon `/v2/reference/news`** — already in-subscription) + a working LLM extractor (catalyst type,
   novelty, dilution-flag, sentiment) on the candidate ticker-days. Medium cost. One pre-registered test:
   does extracted catalyst content add cross-regime top-slice return?

If *both* of these also fail the cross-regime money gate, the feature space for ex-ante rocket selection on
this universe is exhausted, and the honest conclusion is that the right tail is not ex-ante selectable here.

## Status
- **No live change.** Pure research/logging. The shadow risks zero capital; it is an instrument, not a
  strategy. **To accrue the OOS record, it must run daily** (`--run-since <last_run_date>` after the close) —
  offered as an optional Windows scheduled task; not created without Pierce's go (it touches the live host).
- **Basis**: `scripts/rocket_shadow_doc246.py`, `data/research/rocket_shadow_model.pkl`,
  `data/research/rocket_shadow_log.jsonl`. **Predecessors**: 245 (closed BET#3 cross-regime), 241/242, 240,
  243, 244. **Memory**: [[bet3-rocket-detection]], [[trades-parquet-tzbug]].
