# 254 — The runner-vs-faller predictor hunt: NO positive predictor exists; the appealing leads are too rare; the one "robust" discriminator was a CURATION ARTIFACT (caught by computational validation). "Information is the gap" = watchlist, not selection.

**Author**: Claude Opus 4.8 (20-agent fader-forensic workflow + discrimination synthesis + computational validation)
**Date**: 2026-06-04
**Mandate**: Pierce — "trigger deep-research agents on each watchlist stock to extrapolate a predictor for which will be the runner vs the faller." The other half of doc 253: forensics on the FADERS to find the discriminator.

## TL;DR
We forensically profiled 20 matched FADERS (same micro-float/reverse-split/theme/dilution shell as the rockets, but gapped and faded) and compared them feature-by-feature to the 20 rockets. **There is no positive ex-ante "this one will rocket" predictor in this information set.** The structural-candidacy signature is **statistically identical** between rockets and faders (it's a *candidacy gate*, confirming doc 253 from the other side). The two hoped-for "smart-money" leads (insider-buy cluster, coiled catalyst) are **real-in-sign but live at a 5% base rate in the winners themselves** — tie-breakers, not predictors. The one apparently-robust discriminator (a prior-runner "fade-veto") **was a curation artifact**: on the *unfiltered* 13,511-gapper universe, prior-runners **rocket MORE, not less** (6.6% vs 2.4%). Net: **conditional on candidacy, runner-vs-faller is ~irreducible from this information.** "Information is the gap" holds as a *universe-construction* thesis (doc 253) and **fails as a *selection/timing* thesis** (this doc).

## The side-by-side feature table (20 rockets vs 20 matched faders)
| feature | rocket | fader | verdict |
|---|---|---|---|
| hot-theme attached | 85% | 90% | **SHARED (candidacy)** |
| dilution overhang | 80% | **95%** | **SHARED — heavier in faders** ("rally is sold into") |
| recent reverse-split | 60% | 60% | **SHARED (candidacy)** |
| foreign private issuer | 60% | 55% | **SHARED (candidacy)** |
| micro-float <5M | 90% | 45-55% | weak-differential (rockets tighter, but faders are micro too) |
| insider open-market buy cluster | 5% | 0% | differential-in-sign, **5% base — tie-breaker only** |
| coiled catalyst (absorbed flat) | 5% | 0% | differential-in-sign, **5% base — tie-breaker only** |
| social building *before* launch | 25% | 0% | largest positive gap, but **25% recall** |
| fresh hard catalyst on gap day | 30% | 10% | weak differential |
| **prior-runner history** | **40%** | **75%** | **apparent inversion → tested below** |

The four candidacy features (theme, dilution, reverse-split, foreign-issuer) are **statistically indistinguishable** — and dilution is *worse* in faders. **Conditional on the candidacy shell, these carry ~zero discriminating information.**

## The two leads, tested head-on — real but too rare
- **Insider buy cluster:** rocket 1/20 vs fader 0/20. Directionally correct (+ 3 faders showed net insider *selling* into the gap). But at a **5% base rate in the winners**, it fires on ≤5% of true rockets — **a confirmatory/veto overlay, not a primary signal.**
- **Coiled catalyst:** rocket ~1/20 (AIXI) vs fader 0/20. Rocket-exclusive in this sample, but **5%-recall** — a needle, not a screen.
- **Honest read:** the two tells the program hoped would separate the cohorts are **too rare in the winners themselves** to anchor a watchlist. They are tie-breakers.

## The "prior-runner fade-veto" — a CURATION ARTIFACT (the validation that earned its keep)
The discrimination's one robust-looking finding: faders were 75% prior-runners vs rockets 40% → "veto exhausted runners." The synthesis agent **flagged its own #1 caveat**: the fader set was *curated* to be matches-that-faded, possibly over-sampling dead-cat second-legs. So I validated it on the **unfiltered** universe (`prior_runner_veto_doc254.py`, 13,511 gap≥8% stock-days):
| group (unfiltered) | rocket-rate (close ≥+30%) | mean open→close |
|---|---|---|
| FRESH gapper | 2.4% | −0.9% |
| **PRIOR-RUNNER** | **6.6%** | −2.0% |
- **The inversion is FALSE de-curated: prior-runners rocket ~2.75× MORE (6.6% vs 2.4%), cross-regime** (2024 6.7 / 2025 6.6 / 2026 6.2%). The curated faders manufactured the false "veto."
- The truth: **prior-runner is a VARIANCE indicator** — more rockets AND more fade (worse mean −2.0% vs −0.9%). The only survivor: FRESH gappers have a ~1pp better *mean* (CI [+0.23, +1.92]) → a marginal **long-basket** de-selection, but it would veto the **rocket-richest** population. Contradiction for a rocket-hunter; not a usable runner-predictor.

## Coiled-catalyst, computable version (doc-253 B4, price-signature only)
`coiled_catalyst_backtest_doc254.py` (1.3M stock-days): the extreme price signature (≥20× volume + flat price) gives **2× the forward-10d +30%-pop rate** (15.0% vs 7.6% base) and +29% mean forward-max-high — but **2024-weak** (7% ≈ base) and `fwd-max-high` is an uncapturable upper bound. A watchlist **flag** (pairs with the agent verifying a *real* absorbed catalyst), not a standalone trade.

## Verdict — "information is the gap" resolved
- **TRUE as universe construction** (doc 253): a better *watchlist* is ex-ante observable from filings/float/theme/dilution that price-only scanners miss.
- **FALSE as runner-prediction** (this doc): conditional on the candidacy shell, **no positive ex-ante factor separates the runner from the faller.** The appealing leads (insider, coiled) are 5%-rare tie-breakers; the social-pre-build is 25%-recall; the prior-runner "veto" inverts and is a variance indicator, not a predictor. **The magnitude is irreducibly random conditional on candidacy** — the deepest, most-tested confirmation of the whole arc, now from the *information* side, not just the price side.
- **The rigor that matters:** the discrimination's single positive finding was a curation artifact, and the *computational re-test on the unfiltered universe caught it.* (As did winsorizing the doc-252 pre-gap lead, and the doc-245 tz-bug sanity check.) Be as hostile to a hoped-for positive as to a negative.

## What this means for the morning watchlist pipeline (Pierce's vision)
A daily Opus-agent pipeline on the watchlist is **still worth building — but as a WATCHLIST + DATA-COLLECTION engine, not a runner-predictor**:
1. **Candidacy screen** (computable, real value): the doc-253 structural filter → a tractable, higher-quality watchlist than a price-only scanner.
2. **Flag the rare tie-breakers prospectively** (insider Form-4 clusters via EDGAR, coiled-catalyst via volume/price + agent news-verification) — log them forward to *prospectively* test the 5%-leads out-of-sample (n was too small here).
3. **Tag the variance/risk** (prior-runner = higher-variance; active-dilution = "rally sold into" risk).
4. **Log forward outcomes** → accumulate the labeled forward dataset Pierce's month-long commitment would build, for a future, properly-powered re-test.
It **cannot** ex-ante rank which watchlist name will be the runner — the evidence is now clear it isn't there in this information set. Honest framing: it improves the *universe* and *collects* the data; it does not *predict the winner*.

## Status
**No live change.** The predictor hunt is complete and honest: no positive runner-vs-faller predictor exists in the information set (forensic + discrimination + computational validation, 40 deep-dives + 14.8K-gapper backtests). The watchlist/collection engine is the realistic deliverable if Pierce wants the forward month.
**Basis**: workflow `wf_d1811372-cc1` (20 fader dossiers + discrimination), `prior_runner_veto_doc254.py`, `coiled_catalyst_backtest_doc254.py`. **Predecessors**: 253 (rocket forensics — the watchlist signature), 250-252 (the price/volume closure). **Memory**: [[gapper-universe-no-edge]].
