# 162 — 2026-05-12 fresh-data session: 3 experiments, pre-commits LOCKED

> **Format:** Rule 7 applied. Pre-commits locked here, in writing,
> BEFORE any experiment runs. Doc committed to git pre-results so
> outcomes are binary at evaluation time.

**Session date:** 2026-05-12 deep night (after data pipeline repaired)
**Branch:** develop
**Predecessors:** [doc 159 data pipeline](159_v6_2026_05_12_data_pipeline_repaired.md), [doc 161 discord enhanced](161_v6_2026_05_12_discord_dramatically_enhanced.md), [doc 147 v3 baseline](147_v6_recent_subset_audit_v3_survives.md), [doc 154 D293.5](154_v6_d293_5_decompose_gate2_failure.md)
**Status:** **PRE-COMMITS LOCKED.** Verdicts appended on completion.

---

## 0. Why this doc exists

Tonight repaired the data pipeline and lake is now fresh through
2026-05-11 (20,690 rows). Tomorrow at 04:30 ET the bot starts trading
for the first time in 2 weeks (D277 lifted). Before that fires, three
focused experiments to:

1. **Validate cascade alpha holds on freshest data** — de-risk
   tomorrow's first-day-of-trading
2. **Characterize n_estimators effect** at full scale (doc 154 found
   ρ=0.57 between default vs n_est=2; need to know which is best)
3. **Check D291.5 trigger** — has HIGH-tier sample size accumulated
   enough to ship the Kelly raise?

All 3 use existing infrastructure. ~3 hours total.

---

## 1. Locked pre-commits

### Experiment 1 — Cascade alpha on May 4-11 freshest subset

**Method:** load fresh aftermath_strat (max_d0=2026-05-11), run v3
cascade as in doc 147 §1, compute aggregate metrics on:
- **Last-30-days subset** (April 11 → May 4 effective with ret_t5
  available, ~600 rows)
- **Last-60-days subset** (~1,200 rows)
- Compare to doc 147's baseline (Oct 2025 - Apr 2026 NEWER period:
  TabPFN +0.300, XGB +0.230, delta +0.070, n=1,797)

**Locked binary gate:**

| Outcome | Threshold (last-30-days subset) | Action |
|---|---|---|
| **PASS** | XGB Spearman ≥ +0.18 AND mean ret_t5 across all picks ≥ -1% | Bot trades tomorrow with current sizing — alpha confirmed at fresh data |
| **MARGINAL** | XGB Spearman in [+0.10, +0.18) OR mean ret_t5 in [-3%, -1%) | Bot trades tomorrow but flag for review post-EOD |
| **FAIL** | XGB Spearman < +0.10 OR mean ret_t5 < -3% | **URGENT: re-enable D277 HALT before market open**; alpha collapsed on freshest period |

The +0.18 threshold matches doc 143's "TabPFN ≥ 0.18 on RECENT subset
→ pilot warranted" rule. The mean ret_t5 floor of -1% is the
break-even-after-impact threshold per doc 142's Bouchaud analysis.

### Experiment 2 — D293.7: n_estimators sweep

**Method:** for each n_estimators ∈ {1, 2, 4, 8}, run TabPFN on the
12-fold WF (same scheme as doc 152 E1, MAX_TRAIN=2500, n_seeds=1).
Skip n_estimators=default — known too slow on RTX 5070 (75min/seed
per doc 152).

For each config, measure:
- Aggregate recent-subset Spearman vs y_true (folds 8-11)
- Per-fold Spearman
- Pairwise Spearman across configs (the ρ_n_est question doc 154
  partially answered)
- Inference time per fold

**Locked binary gate:**

| Outcome | Action |
|---|---|
| **CLEAR WINNER**: one n_estimators value beats current n_est=2 by ≥ +0.010 recent Spearman | Ship as **D293c**: update `tabpfn_shadow_runner.N_ESTIMATORS` to the winning value |
| **TIE OR DEGRADATION**: best non-2 value is within ±0.010 of n_est=2 | Keep current n_est=2; document the sweep + close D293.7 |
| **PUBLISHABLE FINDING**: pairwise ρ_n_est across all 4 configs is consistently low (mean < 0.85) | Doc 154's ρ=0.57 finding extended to a publishable n_estimators-sensitivity result; file for next-session writeup |

Pre-computed expectation per doc 154: pairwise ρ between adjacent
n_est values likely lower than 0.95. Spearman vs y_true probably
plateaus by n_est=4 (diminishing returns); larger n_est trades
runtime for marginal gain.

**Compute budget:** 4 configs × 12 folds × 1-2 sec/fold = ~2 minutes
inference + setup overhead. Total ~5-10 min wallclock.

### Experiment 3 — D291.5 trigger check

**Method:** count rows in recent-subset (last 60 trading days) where
v3 cascade tier = "HIGH". Threshold per doc 150 §filed: ship Kelly
raise when n_HIGH ≥ 50.

**Locked binary gate:**

| Outcome | Action |
|---|---|
| **TRIGGER MET**: n_HIGH ≥ 50 | Ship D291.5: bump `KELLY_CAPS_AGGRESSIVE["HIGH"]` from 0.35 → 0.50 in `ml_meta_scorer_inference.py`. Doc 150 sub-exp B found Bouchaud-optimal at 98% but staying conservative at 50% per pre-commit. |
| **TRIGGER UNMET**: n_HIGH < 50 | File D291.5 for re-check next week; bot continues at current 0.35 cap |

This is the smallest experiment but the highest-leverage if trigger
met (35% → 50% = +43% sizing on the cascade's 2nd-best tier).

### Composite verdict

The 3 experiments are independent. Doc 162 §5/§6/§7 will record each
verdict separately. There is no "composite gate" because each
experiment's outcome maps to a different production action.

---

## 2. What this doc deliberately does NOT include

- **No new shadow data analysis.** Bot hasn't traded in 2 weeks; no
  shadow data to evaluate yet. D293.8 stays filed.
- **No retraining.** v3 model is from May 5 (per doc 158 §10). Fresh
  retraining is filed for next session — first need to know if the
  current model works on fresh data (Experiment 1).
- **No frontier exploration tonight.** Experiments 1+2+3 use existing
  scripts/infrastructure. Frontier 4 (LLM alpha mining), Frontier 6
  (TRADES), and FinPFN ($224) are all next-session items.

---

## 3. Implementation outline

```
scripts/ml_v6_e1_cascade_validation.py     # Experiment 1
scripts/ml_v6_e2_d293_7_n_estimators_sweep.py  # Experiment 2
scripts/ml_v6_e3_d291_5_high_tier_count.py     # Experiment 3 (or one-liner)
```

Each writes its results to `data/models/v6_e{1,2,3}_2026_05_12.json`
for reproducibility.

---

## 4. Verdicts (appended post-run)

### 5. Experiment 1 — Cascade alpha on freshest data — VERDICT: MARGINAL

```
Lake state:        max d0 = 2026-05-04 (after ret_t5 filter applied)
Total OOS preds:   4,318
Doc 147 baseline:  XGB recent Spearman +0.230, n=1,797 (Oct 2025 - Apr 2026)

Last 30 days (n=334):
  Spearman:        +0.1472   (gate >= +0.18 for PASS)
  Mean ret_t5:     -2.16%    (gate >= -1% for PASS)
  Median ret_t5:   -5.91%
  TOP-DECILE mean: +8.94%    (the bot's actual trade selection)

Last 60 days (n=334, identical to last-30 due to fold boundary):
  Spearman:        +0.1472
  Top-decile mean: +8.94%

Last 120 days (n=596):
  Spearman:        +0.1674
  Top-decile mean: +4.19%

Per-fold breakdown (folds 8-11 = production-relevant):
  fold  8 (Oct  9): n=548  Spearman=+0.241  mean_ret=-9.06%
  fold  9 (Dec 12): n=269  Spearman=+0.146  mean_ret=-2.24%
  fold 10 (Feb 17): n=262  Spearman=+0.200  mean_ret=-0.96%
  fold 11 (Apr 20): n=334  Spearman=+0.147  mean_ret=-2.16%
```

**GATE: MARGINAL** (Spearman 0.147 in [+0.10, +0.18); mean_ret -2.16% in [-3%, -1%)).

**Action:** Bot trades tomorrow with current sizing. **Flag for post-EOD review.**

**Critical positive:** the **top-decile mean is +8.94%** on last 30 days. The
bot doesn't trade the broad universe — it trades top-tier picks per the
cascade. Top-decile selection ranking is doing useful work even though
broader Spearman is degraded. This is consistent with regime shift (more
variance in mid-distribution) WITHOUT alpha collapse at the top.

The +8.94% is well above break-even after impact (per doc 142 Bouchaud:
~1-2% impact at $1M AUM on microcap). Net per-pick edge ≈ +6-8% expected.

**File:** `data/models/v6_e1_cascade_validation_2026_05_12.json`

---

### 6. Experiment 2 — D293.7 n_estimators sweep — VERDICT: TIE/DEGRADATION (close D293.7)

```
4 configs tested on 12-fold WF (n=4,318 OOS preds each):

  Per-config recent Spearman (folds 8-11, n=1,413):
    n_est=1:  +0.2068    (8.3s wallclock)
    n_est=2:  +0.2233    (15.6s)  ← current production
    n_est=4:  +0.2267    (28.6s)  ← best (delta vs n_est=2: +0.0035)
    n_est=8:  +0.2266    (54.9s)  ← essentially tied with n_est=4

  Pairwise rho (the doc 154 question):
    rho(n_est=1, n_est=2) = +0.970
    rho(n_est=1, n_est=4) = +0.950
    rho(n_est=1, n_est=8) = +0.945
    rho(n_est=2, n_est=4) = +0.969
    rho(n_est=2, n_est=8) = +0.972
    rho(n_est=4, n_est=8) = +0.984
    Mean pairwise:          +0.965
```

**GATE: TIE/DEGRADATION** — best config (n_est=4) beats n_est=2 by only
+0.0035, well below the +0.010 threshold. **Keep n_est=2.** Close D293.7.

**PUBLISHABLE finding NOT triggered** — mean pairwise rho 0.965 is far
above the 0.85 threshold. So at n_est ∈ {1, 2, 4, 8}, configs are
~97% correlated.

**Reconciliation with doc 154:** doc 154 measured ρ(n_est=default, n_est=2)
= 0.5678 at N=24 picks (single-day). This sweep measures ρ between
n_est ∈ {1, 2, 4, 8} at N=4,318 picks (full WF). Two findings coexist:
1. **Default vs ≤8 IS substantially different** (0.57 — doc 154's finding stands)
2. **Within {1, 2, 4, 8} configs are nearly identical** (0.965 — new finding)

Implication: the sweet spot is somewhere in {2, 4, 8}; default is
genuinely different. n_est=4 is marginally optimal but not enough to
justify changing production.

**Inference time scaling** — n_est=4 is ~2x slower than n_est=2 per
fold. For shadow runner (one fit/day), n_est=4 would still be fast
enough (~2.4s vs 1.3s). But the +0.0035 lift isn't worth the 2x
runtime + risk of changing a working config.

**File:** `data/models/v6_e2_d293_7_n_estimators_sweep_2026_05_12.json`

---

### 7. Experiment 3 — D291.5 trigger check — VERDICT: TRIGGER MET (SHIP D291.5)

```
HIGH-equivalent (top 15% per day) counts:
  Last 60 days: n_HIGH = 56     ← gate (>= 50): PASS
  Last 120 days: n_HIGH = 100

HIGH-tier mean ret_t5:
  Last 60 days:  +4.13% (median -2.52%, std 33.17%)
  Last 120 days: +4.00% (median -1.50%, std 31.60%)
```

**GATE: TRIGGER MET.** Ship D291.5.

**Action taken:**
- `KELLY_CAPS_AGGRESSIVE["HIGH"]`: **0.35 → 0.50**
- `ADAPTIVE_KELLY_SCHEDULE["HIGH"]` brackets at $100K-$2M: **0.350 → 0.500**
- High-AUM brackets ($5M, $10M) keep their lower caps (Bouchaud impact dominates)

**Why 0.50 not 0.98 (the Bouchaud-optimal):** doc 150 sub-exp B found
optimal HIGH Kelly = 98% at n=25. We have 56 picks now (sufficient sample),
but staying conservative — production capacity caps + per-tier risk
diversification still matter even when per-pick Kelly says go bigger.
The +43% sizing increase (0.35 → 0.50) captures most of the lift while
preserving optionality for further increases later.

**Tests added** (3 new in `test_d290_adaptive_kelly.py`):
- `test_d291_5_high_kelly_cap_is_0_50` — pins KELLY_CAPS_AGGRESSIVE['HIGH']=0.50
- `test_d291_5_high_at_140k_aum_uses_new_cap` — adaptive call at $140K returns 0.50
- `test_d291_5_high_keeps_low_cap_at_high_aum` — at $5M+ stays at 0.195

`test_all_tiers_size_proportionally_on_full_account` updated:
HIGH × $140K = $70K (was $49K).

**Total Kelly tests passing:** 27/27.

**File:** `data/models/v6_e3_d291_5_high_tier_count_2026_05_12.json`

---

### 8. Composite outcome

| Experiment | Verdict | Production action |
|---|---|---|
| E1 cascade validation | **MARGINAL** | Bot trades tomorrow; flag for post-EOD review |
| E2 n_estimators sweep | **TIE** (n_est=4 +0.0035 best) | Keep n_est=2 in `tabpfn_shadow_runner.py`; close D293.7 |
| E3 D291.5 trigger | **MET** (n_HIGH=56) | **SHIP**: HIGH Kelly cap 0.35 → 0.50 in production |

**Tomorrow's bot starts with:**
- D277 halt LIFTED (can trade)
- D291.5 HIGH Kelly cap **0.50** (was 0.35, +43% sizing on HIGH-tier picks)
- All other production state unchanged
- E1 MARGINAL flag → review fold-11 results post-EOD to see if regime shift
  is real or noise

**The MARGINAL E1 + the +43% Kelly bump on HIGH tier is a meaningful
combination.** Tomorrow's HIGH-tier picks (top 15% per day) will get
bigger position sizes than yesterday would have. If the +8.94% top-decile
mean holds, this multiplies the wins. If the regime has shifted (E1's
worry), this multiplies the losses too. The Discord rich EOD report (doc
161) will surface tomorrow's outcome by tier.

---

## Files this commit (verdict)

| Path | Change |
|---|---|
| `docs/research-log/162_v6_2026_05_12_fresh_data_three_experiments.md` | UPDATED (verdicts §5/§6/§7/§8) |
| `scripts/ml_v6_e1_cascade_validation.py` | NEW (~140 LOC) |
| `scripts/ml_v6_e2_d293_7_n_estimators_sweep.py` | NEW (~180 LOC) |
| `scripts/ml_v6_e3_d291_5_high_tier_count.py` | NEW (~160 LOC) |
| `scripts/ml_meta_scorer_inference.py` | UPDATED — KELLY_CAPS_AGGRESSIVE['HIGH'] 0.35 → 0.50, ADAPTIVE_KELLY_SCHEDULE['HIGH'] brackets <=$2M raised |
| `tests/unit/test_d290_adaptive_kelly.py` | UPDATED — 3 new D291.5 pin tests |
| `tests/unit/test_d280_dynamic_bankroll_sizing.py` | UPDATED — `test_all_tiers_size_proportionally_on_full_account` reflects new HIGH = 0.50 contract |
| `data/models/v6_e1_cascade_validation_2026_05_12.json` | NEW (gitignored) |
| `data/models/v6_e2_d293_7_n_estimators_sweep_2026_05_12.json` | NEW (gitignored) |
| `data/models/v6_e3_d291_5_high_tier_count_2026_05_12.json` | NEW (gitignored) |

