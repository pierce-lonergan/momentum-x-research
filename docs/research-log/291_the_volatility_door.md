# 291 — THE VOLATILITY DOOR: open at the HAR-RV level — and the honest decomposition of who opened it

**Author**: Claude (Fable 5, ultracode; preregs frozen pre-execution `7cc09fe` sha256 `5155f3df…a0c4`; 3-skeptic verification fleet on the passing gate) | **Date**: 2026-07-11 | **Class**: PRE-REGISTERED FEASIBILITY CAMPAIGN (3 stages, free data only) + procurement memo + config-diff proposal | **Mandate (Pierce)**: doc-290 §8's road out — "decide, cheaply and honestly, whether the volatility road exists — before any money is spent."

> **First line, the pivot verdict: THE VOLATILITY DOOR IS OPEN AT THE HAR-RV LEVEL** — the Stage-2 challenger beats both frozen baselines on 151 liquid names / 70,966 out-of-sample forecasts, clears the pre-registered minimum meaningful improvement in both halves, and survived a hostile 3-skeptic fleet (leakage-clean, transform-free-loss-robust, composition-robust). **And right behind it, the decomposition verdict the prereg demanded: the thing that opened the door is not our vocabulary.** Stage 0 shows the doc-290 microcap signal reduces to a known volatility-proxy stack (D2 gate FAILS); Stage-2 attribution shows the liquid-name gain is carried by *generic post-HAR literature features* (intraday RV timing + a liquidity proxy) with the momentum-x path-shape/gap vocabulary contributing **≈0%**. The honest restatement: *we have proven we can build a competent modern RV forecaster from data we already own — a real, necessary first rung — not that a proprietary edge transferred.* The durable improvement is **~3.6%** (the 5.9% headline is shock-loaded). Whether that buys a Stage-3 ticket against implied vol — the benchmark that already embeds everything generic — is a capital decision, and it is Pierce's.

## §0 — The honest status of the input signal (as pre-registered)

The doc-290 ρ≈0.30 was a post-hoc characterization inside a failed prereg — treated here as an *upper estimate*, re-derived fresh at every stage. That discipline paid: the fresh full-model estimate came in at 0.313 pooled, but the *decomposition* shows most of it was never ours (§1).

## §1 — Stage 0: what is the signal made of? (frozen gates; verdict: the proxy stack)

Same 875 events / 51 sessions; identical rank-calibrated GBM on every rung (the doc-290 fair-baseline lesson); nested ladder; B=200 within-session permutation nulls; regime replication.

| rung | features | pooled-OOS Spearman |
|---|---|---|
| B0 | unconditional | 0.000 |
| B1 | log price | 0.128 |
| B2 | + float / rotation | 0.153 |
| B3 | + trailing realized vol (warehouse; 855/875 covered) | 0.198 |
| B4 | + per-ticker historical peak mean (LOO) | 0.228 |
| FULL | + the doc-290 39-feature set | 0.313 |

- **D1 (FULL−B3 = +0.115): PASSES** (p=0.025, BH-pass, replicates early +0.082 / late +0.046) — something beats *cheap + small + recently-volatile*.
- **D2 (FULL−B4 = +0.084): FAILS** (p=0.085, BH-fail; late-regime replication collapses to +0.004). Per the frozen verdict language: **the signal reduces to a known volatility-proxy stack** — ~73% of the fresh estimate is price + float + trailing vol + ticker history; the residual is suggestive, uncorrected, and regime-fragile. (Descriptive: the FULL score flags blow-ups at AUC 0.70 vs the −10% stop — the §2 defensive value.)

## §2 — Stage 1: in-universe defensive utility (risk-shaping, proposal-only)

On the 875-event book (disclosed substitution — the live ledger is n≈10), fillable policy, net of the 1.5% floor. Baseline: mean −0.18%/event, stop-rate 20.8%.

| use | risk improvement | mean-net effect | frozen gate |
|---|---|---|---|
| U1 exclude top-decile vol score | stop-rate −12.6%, maxDD −19.4% | Δ −0.63% (CI incl. 0) | **FAIL** (maxDD under the 20% bar) |
| **U1 exclude top-quintile** | **stop-rate −22.4% (CI excl. 0), maxDD −25.3%** | Δ −0.87% (CI incl. 0) | **PASS** |
| **U2 inverse-vol sizing (shrink-only)** | **maxDD −60.6%** at 50% capital deployed | Δ/dollar −0.82% (CI incl. 0) | **PASS** |
| U3 score-conditioned stop width | none (maxDD −4.9% worse) | ~0 | **FAIL** |

Exactly the frozen expectation: **drawdown/stop-rate shaping at statistically-unchanged mean — a portfolio-quality tool, not an edge.** Denominator honesty: the mean-net *point* estimates lean negative (the high-vol names contain the winners too — doc-290's twin-coin law), the CIs just can't distinguish it from zero. **Config-diff proposal (NOT applied; yours to accept or reject):** add an OOS-vol-score top-quintile entry veto and/or inverse-vol position scaling under the frozen 5% cap. Given the negative point-drift on a book you may canary anyway, my recommendation is to hold this in reserve rather than wire it — the stop-rate reduction is real, but the book's problem is its mean, not its variance.

## §3 — Stage 2: the transfer test (frozen gates: PASS — with the attribution that matters)

151 liquid names (megacaps, liquid ETFs, active mid-caps), 2024-01→2026-07 minute bars **already local — this test cost $0**. Strict walk-forward (train < t, refit every 21 sessions), next-day RV, QLIKE on the variance scale, date-blocked bootstrap.

| model | pooled QLIKE |
|---|---|
| EWMA(0.94) reference | 0.323 |
| HAR-OLS (ticker FE) | 0.254 |
| **GBM[HAR-only]** (fair model-class control — the binding baseline) | **0.133** |
| **Challenger: GBM[HAR + 10 transferable features]** | **0.125** |

**All three frozen gates pass**: beats both baselines (CIs exclude 0), both halves (H1 +8.4%, H2 +2.8%), pooled +5.91% ≥ the frozen 2.0% MMI. Per-day rank-ρ 0.912→0.914 — the gain is *level calibration*, which is what options pricing consumes.

**The 3-skeptic fleet (all reproduced the headline to the digit; all artifact=FALSE on the pass itself):**
- *Leakage/protocol — CLEAN.* Target alignment audited per-ticker (zero misalignments); walk-forward strict; the gain survives transform-free losses (MSE-log +4.4%, both halves CIs exclude 0 — not a Jensen artifact). Two nits disclosed: a numerically-harmless off-by-one in the bootstrap (corrected CIs identical to 4 decimals) and a weak/nonstandard HAR-OLS (irrelevant — the binding baseline was the GBM control).
- *Composition — ROBUST but shock-loaded.* 94% of names improve (median +5.9%); survives excluding leveraged ETFs (+5.87%) and the 20 most-volatile names (+6.05%). But the **April-2025 vol spike carries ~42% of the differential**; ex-shock the pooled gain is **+3.63%** (still passes every gate); quarterly trajectory is episodic — 2026Q1 was −0.8% (dead quarter), 2026Q2 +3.7% (CI excl. 0). The gain correlates 0.83 with the baseline's per-day loss: **the mechanism is reacting faster than daily-HAR on regime-transition days** — which is precisely where implied vol is already instantaneous. Quote **~3.6%** as the durable figure.
- *Feature attribution — THE NARRATIVE CORRECTION (HIGH severity).* Removing the HAR-derived ratio leaves the pass intact (5.6%); the ratio alone adds nothing. But the gain is carried by **`last_hour_rv_share`** (+4.4% standalone — a *generic* intraday-RV-timing decomposition, standard in the post-HAR literature) and the **volume group** (+3.7% — where `log_dvol` plausibly acts as a soft ticker fixed-effect the GBM baseline lacks). The signature momentum-x **path-shape and gap/range groups contribute ≈0** (dropping path-shape *improves* the challenger; both fail to replicate standalone in H2). **Honest restatement: "daily-HAR + intraday RV timing + liquidity beats daily-only HAR" — a known result class, not a vocabulary transfer.**

**Convergence across §1 and §3** — two universes, two frozen preregs, one law: *the forecastable volatility structure is real everywhere we look, and everywhere we look it is carried by known, generic quantities — never by the program's proprietary vocabulary.* Doc-290's discovery survives as competence, not as property.

## §4 — What this buys, and the decision that is Pierce's

The necessary condition is met at $0 spend: our warehouse + pipeline produce a modern intraday-informed RV forecaster that beats the field-standard daily baseline, adversarially verified. The sufficient condition — value **over implied vol, net of option spreads** — needs paid data ([291_PROCUREMENT.md](291_PROCUREMENT.md): vendor options, Polygon-entitlement check first, a $0 VIX-family pilot path, Stage-3/4 prereg sketches). The honest prior for Stage 3 is **lowered** by the attribution finding: IV already embeds generic public information, and our gain's mechanism (shock-day reaction speed) is IV's home turf. This is a lottery ticket on a measured foundation — the program's first *earned* one, but a ticket, not an edge.

**Pierce-action list:** (1) `_doc288_apply_catchup_triggers.ps1` STILL NOT RUN — the task shows one trigger; one elevated click. (2) Stage-3 procurement decision (or the $0 VIX-family pilot first). (3) Ratify or amend the kill/continue proposal (frozen text in the prereg): the door is "real" only after Stage 3 (vs IV) AND Stage 4 (net of spreads); a Stage-3 fail closes the family. (4) The 5% cap remains frozen per doc-290 §6 — nothing this session touched it; concentration stays off the table pending your written word. (5) Accept/reject/hold the §2 config-diff proposal (my recommendation: hold).

## §5 — Multiplicity ledger

Stage 0: 2 gate tests (BH α=0.05) + 6 descriptive rungs. Stage 1: 4 gate tests. Stage 2: 1 gate (intersection of 2 baseline comparisons) + descriptive curves. Skeptic sensitivities (disclosed, not gates): ~14 (transform-free losses ×3, exclusions ×4, quarterly ×9 cells, ablations ×7). Housekeeping verified: rocket-gate n=3/30 healthy (next advance Monday), 64 targeted tests green pre-work.

**Artifacts** (`data/research/doc291/`): stage0_decomposition.json, stage1_defensive.json, stage2_harrv.json, attack3_feature_attrib (skeptic), stdout logs. Scripts: `_doc291_PREREG.md` (frozen), `_doc291_stage0_decomposition.py`, `_doc291_stage1_defensive.py`, `_doc291_stage2_harrv.py`.
