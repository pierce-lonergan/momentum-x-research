# 135 — v6 Phase 1: Microstructure Pack Validation — MARGINAL (89% / 90%)

> **⚠️ READ 136 FIRST.** The "marginal positive" verdict in this doc
> was wrong. Diagnostics in
> [136_v6_phase1_diagnostics_honest_negative.md](136_v6_phase1_diagnostics_honest_negative.md)
> show the v6 pack hurts ELITE precision (the tier we trade), helps only
> BROAD, has its lift in the middle of the distribution rather than the
> top tail, and uses post-09:30 features that can't feed an "enter at
> open" decision. CPCV results in this doc are partially based on a
> degenerate 4-of-4-fold subset (CPCV code bug). Doc kept for the
> research record; the verdict belongs to 136.

**Session date:** 2026-05-07
**Branch:** develop
**Predecessors:** [134 v6 Phase 1 microstructure pack](134_v6_phase1_microstructure_pack.md)
**Successor:** [136 v6 Phase 1 diagnostics — honest negative](136_v6_phase1_diagnostics_honest_negative.md)
**Status:** ~~v6 pack STAYS OUT of production for now; clear next-step plan~~ → see 136 for the corrected interpretation

---

## TL;DR

Ran the v6 microstructure pack through the Phase 0 validation framework
on 6,768 (ticker, d0) keys (2025-08 → 2026-04). Result is **marginal**:

| Metric | Control (v3) | Treatment (v3+v6) | Delta | Gate | Verdict |
|---|---|---|---|---|---|
| Raw Spearman | +0.2022 | +0.2184 | **+0.0162** | — | improves |
| **100%-neutralized ρ** | +0.1940 | +0.2097 | **+0.0157** | ≥+0.01 | ✅ **PASS** |
| **Deflated Sharpe Ratio** | 0.9972 | 0.9893 | (both strong) | ≥0.5 | ✅ **PASS** |
| **CPCV frac >0 (200 subsets)** | 86% | **89%** | +3% | ≥90% | ❌ **MARGINAL FAIL** |
| CPCV mean Sharpe | +3.890 | +3.331 | −0.559 | — | slight regression |

The v6 pack **passes** the neutralized-ρ gate (the most important
single metric for cross-sectional alpha) and the DSR gate (the
multiple-testing protection). It **marginally fails** the strict
CPCV-stability gate (89% < 90% target).

**Decision: pack stays OUT of production for now, but this is a much
more positive result than v5 was — there's real signal here.**

---

## 1. What the validation actually measured

Single XGBoost regression (default hyperparameters — NO grid search,
NO Optuna), 12-fold walk-forward (120d train / 15d test), inner-joined
to v6-coverage rows only:

```
CONTROL    = v3-features-only (54 features) → ret_t5 prediction
TREATMENT  = v3-features ⊕ v6-pack (64 features) → ret_t5 prediction
```

The two models share IDENTICAL hyperparameters. The only difference is
the v6 microstructure block (10 columns: 6 raw + 4 log-transformed).

**Why use defaults instead of v3 prod hyperparameters?** Production
v3's Optuna params were tuned on the same data we're evaluating —
using them would inject leakage and inflate the control. Defaults are
the unbiased baseline; they let us isolate the *feature* contribution.

---

## 2. Why the 89% / 90% CPCV result is interesting

The CPCV-approximation samples 200 random 4-of-12 fold subsets and
asks: "in what fraction does the per-day top-quintile P&L Sharpe stay
positive?"

| | Control | Treatment |
|---|---|---|
| frac > 0 (any positive Sharpe) | 86% | **89%** |
| mean Sharpe | +3.89 | **+3.33** |
| std Sharpe | (similar) | (similar) |

The treatment is *more often positive but with a lower mean*. That's
not a contradiction — the v6 features add information that helps the
median fold-subset cross zero, but they also produce a few catastrophic
fold-subsets that drag the mean down.

**Hypothesis:** the catastrophic subsets are ones where the
microstructure regime in train differs sharply from test (e.g.,
training on a quiet-microstructure regime, testing on an ISO-burst
regime). VPIN and Kyle's λ are highly time-varying; if a model leans
on them too hard during a structural break, OOS performance crashes.

A proper fix: add a regime-detection layer or add temporal-rolling
versions of the v6 features (e.g., 30-day VPIN std) so the model
learns regime stability rather than absolute values.

---

## 3. The neutralized-ρ improvement is the headline

The 100%-neutralized Spearman captures alpha that's ORTHOGONAL to:
- log_market_cap
- log_dvol_d0
- prior_avg_t5
- intraday_pct

Improving neutralized ρ from **0.1940 → 0.2097** (+8.1% relative) is a
**real, structural** improvement. It says the v6 pack adds information
the v3 features cannot represent — exactly the M.md prediction.

This is much stronger evidence of edge than the v5 architectural
changes ever produced (v5 went BACKWARDS on neutralized ρ).

---

## 4. Why we're not shipping yet (despite passing 2-of-3 gates)

The 90% CPCV gate is strict by design. Phase 0 set it to catch *exactly*
this failure mode: a feature pack that improves *average* metrics but
introduces tail risk in some fold-subsets.

If we ship v6 to production now and a structural break happens (it
will — see how VPIN regimes shifted during Aug 2024 carry-trade
unwind), the bot may underperform v3 baseline for weeks before we
recover. That's an unacceptable risk for live capital.

**The bar to clear is: improve CPCV frac>0 to ≥90% while keeping the
neutralized-ρ lift.**

---

## 5. Three concrete next steps to push v6 past the gate

### 5.1 Per-tier evaluation (highest priority)

The CPCV+global-ρ measurement averages over BROAD/VETOED/HIGH/ELITE
tiers equally. But we believe (per M.md) the v6 pack's lift is
**concentrated in ELITE** (~7% of keys). Run the same validation but
restricted to:

```
ELITE_proxy = top-10% of preds_ctrl by predicted ret_t5
HIGH_proxy  = next-15%
VETOED_proxy = top-quartile
BROAD_proxy = full
```

Compute neutralized ρ + CPCV frac>0 SEPARATELY for each tier. If the
ELITE-proxy CPCV frac>0 is ≥95% (likely, given the +0.0157 global
lift), we can ship the v6 pack as an ELITE-tier-only feature.

This matches the existing v3 cohort cascade architecture (BROAD /
VETOED / HIGH / ELITE specialists). The wiring change is small.

### 5.2 Add temporal-rolling v6 features

Augment the pack with:
- `vpin_d0_30d_std` — rolling σ of VPIN over prior 30 days (regime stability proxy)
- `kyle_lambda_d0_zscore_30d` — zscore vs prior 30 days
- `hawkes_fano_d0_pct_30d` — percentile rank vs prior 30 days

This gives the model "is today's microstructure WEIRD vs recent history"
not just "what is today's microstructure value." Should reduce fold-subset
tail risk.

### 5.3 Drop the fattest-tail v6 features

`hawkes_fano_d0` ranges from 0 to 16,000+ across our keys. Even with
log-transform it carries massive scale variance. Try a v6_pack_lite:

```
vpin_d0
ofi_first30_d0      (when non-NaN — only ~38% coverage)
log_kyle_lambda_d0
log_amihud_illiq_d0
```

Drop hawkes_fano + iso_sweep. See if smaller pack passes 90% CPCV
while keeping most of the neutralized-ρ lift.

---

## 6. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v6_phase1_validation.py` | NEW | Control/treatment WF + CPCV + DSR + neutralized ρ |
| `data/models/v6_phase1_validation.json` | NEW (gitignored) | Numerical results |
| `data/.../ml_v6_phase1_control_preds.parquet` | NEW (gitignored) | OOS preds for v3-only model |
| `data/.../ml_v6_phase1_treatment_preds.parquet` | NEW (gitignored) | OOS preds for v3+v6 model |
| `data/.../microstructure_v6_pack.parquet` | NEW (gitignored) | 6,768-row backfill output |
| `docs/research-log/135_v6_phase1_validation_marginal.md` | NEW (this doc) | Full writeup + next steps |

The launcher and production model are UNCHANGED. Wednesday production
behavior remains: pure v3-tuned-16f + s125 hybrid ELITE + bug fixes.

---

## 7. The honest research arc summary so far

| Phase | Outcome |
|---|---|
| v5 Tier-S architecture | **NEGATIVE** (Spearman 0.041 vs v4's 0.141) |
| Phase 0 validation hardening | **POSITIVE** (proved v4's apparent lift was multiple-testing artifact; rolled back D281+D282) |
| Phase 1 microstructure pack | **MARGINAL** (passes ρ+DSR, fails CPCV by 1pp) |

Three iterations in ~8 days. Two negative results, one marginal. Each
one taught us a real thing:
- v5: architecture is exhausted on this universe
- Phase 0: our prior validation methodology was wrong
- Phase 1: data DOES help, but the validation gates are tighter than
  we'd intuited

**The v6 thesis (data > architecture) is INTACT.** The path forward is
the per-tier evaluation in §5.1 — that's the test that tells us
whether the v6 pack's lift is concentrated where it matters most.

---

## 8. References

- Bailey, López de Prado (2014), *Deflated Sharpe Ratio*, JPM
- Bailey, Borwein, López de Prado, Zhu (2016), *PBO of backtests*, JCMS
- López de Prado (2018), *Advances in Financial Machine Learning*, Ch. 7 (CPCV)
- Numerai (2019), *Feature Neutralization* (proportion-α form)
