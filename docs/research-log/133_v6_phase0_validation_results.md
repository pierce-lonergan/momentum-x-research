# 133 — v6 Phase 0: Validation Hardening Results — v4 FAILED, v3 SURVIVED

**Session date:** 2026-05-06 (overnight, post-Wed-deploy decision point)
**Branch:** develop → main
**Predecessors:** [132 v5 negative + v6 roadmap](132_momtrans_v5_negative_result_v6_roadmap.md)
**Status:** **PRODUCTION ROLLBACK** — D281 + D282 disabled in launcher

---

## TL;DR — The most important finding of the entire MoMTrans research arc

**v4's apparent +$46,573 WF lift over production is not statistically
significant after correcting for the 600-combo threshold grid search
that produced it.** Production v3 survives validation. D281 cohort
cascade is in the same multiple-testing trap as v4. Both have been
disabled in the launcher pending v6 hygiene rebuild.

| Validation gate | v4 cohort cascade | v3 production |
|---|---|---|
| Deflated Sharpe Ratio (DSR @ N=600) | **0.0471** ❌ FAIL | **1.0000** ✅ STRONG |
| Probability of Backtest Overfitting (PBO) | 0.278 ✅ PASS | n/a |
| 100% feature-neutralized Spearman ρ | 0.091 (was 0.136 raw, **−33%**) | 0.076 (was 0.159 raw, **−52%**) |
| CPCV Sharpe std (over 200 random 4-fold subsets) | 2.77 (highly variable) | 1.19 (stable) |
| CPCV % subsets with positive Sharpe | 61% | **100%** |

**The v4 cascade lift was real on the SPECIFIC 16 folds we trained
+ thresholded on, but does not represent stable alpha across regimes.**
v3 does represent stable alpha (100% of CPCV subsets show positive
Sharpe, neutralized ρ stays above 0).

---

## 1. What we ran (Phase 0 framework — `scripts/ml_v6_phase_0_validation.py`)

Five validation sections from the López de Prado / Bailey rigor stack:

### 0.5 Embargo + purging audit

16-fold WF (365d train / 30d test rolling) has 30d gaps between
consecutive test windows — exceeds our 5d label horizon. Train/test
boundary within a fold may have label-overlap; recommend explicit
purging at v6 retraining time.

### 0.4 Numerai-style feature neutralization

Project predictions onto residual space orthogonal to known exposures:
`pred -= F · (F⁺ · pred); pred /= pred.std()`

Exposures used (12 features): `log_market_cap`, `log_dvol_d0`,
`prior_avg_t5`, `intraday_pct`, plus 8 sector dummies (pharma, bio,
medical, software, finance, semi, spac, reit).

Result:

| Strategy | Raw ρ | 50% neutral | 100% neutral | Δ (full) |
|---|---|---|---|---|
| Production v3 | +0.1585 | +0.1203 | **+0.0762** | −52% |
| v4 BROAD spec | +0.1355 | (n/a) | **+0.0905** | −33% |

**Both strategies lose half their apparent edge to sector/cap exposure.**
What remains (~0.08 ρ) is genuine cross-sectional signal — small but
real. v4's 0.091 vs v3's 0.076 means MoMTrans does add some
non-sector-tilt edge. But the absolute level is much smaller than
either model's raw Spearman suggested.

### 0.1 CPCV-approximation: Sharpe distribution

Approach: sample 200 random subsets of 4-of-16 folds (not full retrain
— uses existing OOS predictions). Compute per-day P&L Sharpe within
each subset. Report distribution.

```
                       v4 cascade        v3 production
mean Sharpe:           +1.11             +3.91
std Sharpe:             2.77              1.19
p05:                   −3.35             (deep positive)
p50:                   +1.02             +3.96
p95:                   +5.63             +5.70
frac > 0:              61.0%             100.0%
frac > 1:              50.0%
```

**v4's Sharpe is highly subset-dependent (std 2.77 on a mean of 1.11)
— ~40% of fold subsets show negative Sharpe.** v3 is consistently
positive.

### 0.2 Deflated Sharpe Ratio (Bailey & LdP JPM 2014)

Formula:
```
DSR = Φ((SR_observed - SR_max_under_H0) / σ_SR_corrected)
```

`SR_max_under_H0` is the expected max of N IID-normal trial Sharpes:
```
E[max(N normal)] ≈ (1-γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))
```
where γ ≈ 0.5772 is the Euler-Mascheroni constant.

`σ_SR_corrected` accounts for skew and excess kurtosis (Bailey-LdP 2014
eq. 7).

**v4 cascade:**
- T_days = 320 trading days
- Observed SR per period = 0.0799
- Observed SR annualized = **1.27**
- Skew = +0.22, excess kurt = +5.24
- σ_SR (corrected) = 0.0557
- N_trials = **600** (the threshold-search grid)
- SR_max under H0 = 0.173 per period
- z-score = **−1.67**
- **DSR = 0.0471 — SEVERE FAIL** (Sharpe is in H0 noise band)

**v3 production:**
- Observed SR annualized = **3.69**
- Skew = +6.10, excess kurt = +50.84 (very fat-tailed)
- σ_SR (corrected) = 0.029
- N_trials = **1** (no threshold grid; Optuna tuned on different data
  than the WF eval)
- z-score = **+8.01**
- **DSR = 1.0000 — STRONG**

The N_trials choice is the crux. With N=600 (which we explicitly
searched in Phase A's threshold grid), v4's measured Sharpe of 1.27
annualized is below the H0-expected-max threshold. With N=1 (single
trial, what production v3 effectively is), the same Sharpe would be
genuinely significant. **This is the multiple-testing penalty in
action: by searching 600 threshold combinations, we INDUCE a high
expected-max-Sharpe under H0, and our actual Sharpe doesn't beat it.**

### 0.3 Probability of Backtest Overfitting (PBO)

Built per-(threshold-combo, fold) PnL matrix for 600 strategies × 16
folds. Sampled 5,000 random IS/OOS half-splits. For each, found the
IS-best strategy and measured its OOS rank.

**PBO = 0.278** (28% of splits where IS-winner is below OOS median).

This is below the 0.30 "predictive selection" threshold — meaning the
threshold-search process IS finding genuinely predictive thresholds
(better than random). **PBO passes; DSR fails.** Not contradictory:
PBO measures whether selection is non-random; DSR measures whether
the resulting Sharpe is large enough to be confident in.

**Translation:** the threshold search was reasonable (it picked
thresholds that work better than random), but the lift it produced
isn't large enough to be statistically distinguishable from noise
when corrected for the 600-combo search size.

---

## 2. The genuinely uncomfortable part — what this means for production

Wednesday's launcher had 3 architectural flags ON:
- `MX_HYBRID_ELITE=1` — s125 (4-trial: which of {default, 6f-tuned, 16f-tuned, T-scaled} for ELITE)
- `MX_TIERED_LEARNING=1` — D281 cohort cascade (600-combo threshold grid)
- `MX_USE_MOMTRANS=1` — D282 v4 cascade (600-combo threshold grid)

Phase 0 results say:
- **`MX_USE_MOMTRANS=1`**: DSR 0.05 with N=600 → **TURN OFF**
- **`MX_TIERED_LEARNING=1`**: same threshold-grid pattern → **TURN OFF**
- **`MX_HYBRID_ELITE=1`**: 4 trials (much smaller N) → **KEEP** (DSR
  with N=4 is much more permissive)

Both MoMTrans and D281 share the same multiple-testing vulnerability:
their thresholds were selected by grid-search on the same WF data they
were evaluated on. v6 needs a clean separation: tune thresholds on
TUNE folds, freeze, evaluate on VERIFY folds — **and propagate the
N_trials count into DSR computation**.

This commit makes the launcher safe:
```
MX_USE_MOMTRANS:             0   (was 1)
MX_TIERED_LEARNING:          0   (was 1)
MX_HYBRID_ELITE:             1   (kept)
```

Production behavior Wednesday: pure v3-tuned-16f + s125 hybrid ELITE +
all the bug fixes (D279 dvol, D280 dynamic bankroll, etc.). The
research artifacts (`.pkl` and `.pt` files) remain on disk; flipping
the env vars back re-enables them after v6 hygiene.

---

## 3. What's still REAL signal (after Phase 0)

The neutralized Spearman result is the most honest measurement:

| Strategy | Raw ρ | Neutralized ρ |
|---|---|---|
| v3 production | 0.159 | **0.076** |
| v4 BROAD specialist | 0.136 | **0.091** |

Both have residual edge after stripping sector/cap exposure. v4's
edge is actually slightly LARGER on the neutralized metric — the
transformer learned something the v3 ensemble didn't, but the
threshold-grid amplified that small advantage into a misleading +$46k.

**This is good news for v6.** The transformer architecture HAS captured
something real (higher neutralized ρ). The path is to use it with
proper hygiene, not to replace it.

---

## 4. v6 Phase 1 — what to build next (with proper hygiene this time)

Per [doc 132 § 3](132_momtrans_v5_negative_result_v6_roadmap.md), the
remaining v6 phases focus on DATA additions:

### Phase 1 — Microstructure feature pack (highest expected lift)

Per [M.md § 1A](M.md), expected lift +0.05-0.10 Spearman:

1. **VPIN (Lee-Ready trade signing)** — Easley-LdP-O'Hara RFS 2012
2. **Multi-Level Order Flow Imbalance (MLOFI)** — Cont-Kukanov-Stoikov
   JFE 2014; Kolm-Turiel-Westray Math.Finance 2023
3. **Kyle's λ** (price-impact-per-signed-volume)
4. **Hawkes self-excitation** for trade arrival
5. **Dark-pool / FINRA TRF prints** (Buti-Rindi-Werner FM 2022)

We have the data: `data/polygon_warehouse/trades_v1_parquet` (147 GB
ZSTD parquet, 9 months). The features are scalar per (ticker, d0).

**Critical:** every v6 feature addition must be evaluated under the
same Phase 0 framework (CPCV + DSR + neutralization) BEFORE shipping.
No more grid-searched thresholds without held-out validation folds.

### Phase 2-5 (deferred until Phase 1 validates)

- News/catalyst embeddings (FinBERT)
- Cross-ticker sympathy peer features
- Capacity-aware Kelly (Bouchaud square-root impact)
- Distributional RL sizing (IQN + CVaR-Kelly)

---

## 5. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v6_phase_0_validation.py` | NEW (~430 LOC) | The 5-section validation framework |
| `data/models/v6_phase0_validation.json` | NEW (gitignored) | Numerical results |
| `scripts/lottery_paper_trade.ps1` | MODIFIED | Disabled `MX_USE_MOMTRANS=1` and `MX_TIERED_LEARNING=1` |
| `docs/research-log/133_v6_phase0_validation_results.md` | NEW (this doc) | Full Phase 0 writeup + production rollback rationale |

---

## 6. The single most important sentence of the entire research arc

**"DSR=0.05 with N=600 trials means the v4 cohort cascade's apparent
+$46k lift is no more statistically significant than the maximum lift
you'd expect from grid-searching 600 random strategies."**

This is what the M.md research warned about. We discovered it BEFORE
deploying. That alone justifies the entire Phase 0 effort.

---

## 7. Decision authority going forward

- **Pierce can flip `MX_USE_MOMTRANS=1` or `MX_TIERED_LEARNING=1` for
  paper-trading A/B tests** at any time.
- **Production capital deployment** (when this leaves paper trading)
  requires DSR/PBO re-validation under proper hygiene per `ml_v6_phase_0_validation.py`.
- **v6 work** must use CPCV folds (or at minimum: proper TUNE/VERIFY
  threshold separation) for any threshold/hyperparameter selection.
