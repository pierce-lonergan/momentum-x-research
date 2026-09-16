# 155 — v6 D293.6: theory-grounded gates resolve D293's conditional ship

> **Format:** Rule 7 + 9 applied. Pre-commits locked here, in writing,
> BEFORE the verification runs. Gates are theory-grounded (Fisher-z CI,
> bootstrap CI) per Rule 9, replacing doc 154's heuristic gates.

**Session date:** 2026-05-12
**Branch:** develop
**Predecessors:** [154 D293.5 decomposition HOLD](154_v6_d293_5_decompose_gate2_failure.md), [153 D293 conditional ship](153_v6_d293_tabpfn_ensemble_ship.md)
**Status:** **PRE-COMMITS LOCKED.** Verdict appended on completion.

---

## 0. The question this doc resolves

Doc 153 → CONDITIONAL SHIP (literal Gate 2 failed at 0.65).
Doc 154 → HOLD (Gate B PASS via apples-to-apples, but heuristic Gates C+D
failed by margins explainable as gate miscalibration, not ensemble bug).

D293.6 runs the corrected verification: Gate B re-verified on 7 dates
for power, Gate C as Fisher-z one-sided test on partial Spearman (theory-
grounded), Gate D as per-date bootstrap CI (proper finite-sample
treatment).

**Outcome is binary:** all 3 gates PASS → **D293 → FULL SHIP retroactively**.
Any gate FAILS → REVERT ensemble code, keep n_estimators=2 fix as D293a.

---

## 1. Why the gates are theory-grounded

Per Hygiene Rule 9 (added doc 154 §5): heuristic gates are diagnostics,
not load-bearing. D293.6 explicitly removes the heuristics:

| Doc 154 (heuristic, retired) | D293.6 (theory-grounded) |
|---|---|
| Gate C: \|ρ_C − ρ_A × ρ_B\| ≤ 0.10 (path product) | Gate C: Fisher-z one-sided test of H0: partial_ρ(A, C \| B) ≤ 0.30 |
| Gate D: ensemble Spearman ≥ mean per-seed (strict, no CI) | Gate D: bootstrap 95% CI on (ensemble − mean per-seed) lower bound ≥ −0.05 |

The path-product gate confused "Spearman is approximately multiplicative"
(it isn't) with a strict consistency requirement. The strict-inequality
Gate D ignored that small-N Spearman has standard error ~0.20 — a delta
of -0.005 is 1/40 of the noise floor and statistically indistinguishable
from zero.

Both replacements use standard inferential statistics:
- **Fisher z transformation** for partial-correlation CI (Fisher 1915,
  Olkin & Pratt 1958 for partial extension)
- **Percentile bootstrap** for the per-date metric difference CI
  (Efron 1979)

---

## 2. Locked pre-commits

### Gate B-extended (re-verification with more power)

**Method:** Same as doc 154 Gate B but on 7 dates (2026-04-24 through
2026-04-15, excluding any with <20 picks). For each date, compute
single-seed and 5-seed ensemble at n_estimators=2.

**Pass:**
- ρ_B aggregated (across all picks pooled, expected n≥200) ≥ 0.85
- Per-date ρ_B ≥ 0.80 on at least 6 of 7 dates

This is doc 154's Gate B with marginally more power. The doc 154
version already passed (5/5 dates, ρ=0.88); reproducing on 7 dates
provides additional confidence and lets us plot ρ_B distribution.

### Gate C-revised (information-theoretic, replaces doc 154 heuristic)

**Method:** Compute partial Spearman correlation
`partial_ρ(A, C | B)` from the existing 2026-04-24 measurements:
- A = OLD parquet (single-seed, n_estimators=default), n=24
- B = single-seed (n_estimators=2)
- C = 5-seed ensemble (n_estimators=2)

Formula (Olkin-Pratt 1958):
```
partial_ρ(A, C | B) = (ρ_AC − ρ_AB · ρ_BC) / sqrt((1 − ρ_AB²)(1 − ρ_BC²))
```

Apply Fisher-z transformation: `z = atanh(partial_ρ)`, with
`SE(z) = 1 / sqrt(N − 3 − k)` where k=1 (one conditioning variable).

**One-sided test:** H0: partial_ρ ≤ 0.30 vs H1: partial_ρ > 0.30.
Compute t-statistic and one-sided p-value.

**Pass:** one-sided p-value > 0.05 (cannot reject H0; data consistent
with partial_ρ ≤ 0.30, i.e. ensemble adds no extra information about
A beyond what single-seed B already captures).

**Pre-computed expectation (from doc 154 numbers):**
- partial_ρ point estimate: 0.385
- Fisher z: 0.406, SE: 0.224 at N=24, k=1
- t-stat for H0: partial_ρ ≤ 0.30: t = (0.406 − 0.310) / 0.224 = 0.43
- One-sided p-value: ~0.33 → **PASS expected**

The pre-commit is locked PRE-RUN; the expected-pass calculation here
is just the math the script will reproduce. If a recomputation gives
different ρ_AB, ρ_BC, or ρ_AC values, the verdict could differ.

### Gate D-revised (bootstrap CI on per-date metric delta)

**Method:** For each Gate B date, on the actual picks of that date:
1. Resample picks with replacement (B=1000 bootstrap iterations)
2. For each iteration, compute:
   - `ensemble_spearman_y` = Spearman(ensemble preds, y_true) on resample
   - `mean_per_seed_spearman_y` = mean over 5 seeds of Spearman(seed_pred, y_true) on resample
   - `delta` = ensemble_spearman_y − mean_per_seed_spearman_y
3. Compute 95% percentile CI on `delta`: [2.5th percentile, 97.5th percentile]

**Pass:** for ALL 7 dates, the 95% CI lower bound ≥ −0.05.

This says: the ensemble may be slightly worse than mean per-seed within
reasonable sampling noise (up to −0.05 Spearman delta), but not
catastrophically worse. The threshold −0.05 is calibrated to the small-N
Spearman SE (~0.20 at N=27); −0.05 is well within this noise floor.

**Pre-computed expectation:** doc 154 showed point-estimate deltas of
+0.018, +0.002, −0.005, −0.009, +0.030 across 5 dates. None are below
−0.05 at point estimate. Bootstrap CI lower bounds will be pessimistic
but should remain above −0.05 for healthy distributions. **PASS expected**
but not guaranteed at this CI calibration.

### Composite outcome (binary, locked)

| B + C + D | Action |
|---|---|
| **All 3 PASS** | **D293 → FULL SHIP retroactively.** Update doc 153 §5 cross-ref. Production ensemble code stays in place; conditional designation lifted. |
| **Any FAIL** | **REVERT ensemble code in `tabpfn_shadow_runner.py` (back to single-seed); keep n_estimators=2 fix.** Mark as D293a (production safety only). Document in §5 which gate failed. |

No HOLD outcome this time. Doc 154 had HOLD because heuristic gates'
failures could be either real or methodological; theory-grounded gates
remove that ambiguity.

---

## 3. Implementation outline

Single script `scripts/ml_v6_d293_6_calibrated.py`:

```python
# Step 1: load v3 panel + features (same as before)
# Step 2: load OLD parquet snapshot (2026-04-24, single-seed, n_est=default)
# Step 3: for each of 7 dates:
#           generate 5-seed predictions at n_est=2
#           save per-seed preds for bootstrap
# Step 4: compute Gate B aggregated + per-date
# Step 5: compute Gate C-revised (Fisher-z on partial Spearman from 2026-04-24)
# Step 6: compute Gate D-revised (per-date bootstrap CI on metric delta)
# Step 7: emit verdict per outcome map
```

Compute budget: ~3 minutes (7 dates × ~10 sec for 5 seeds at n_est=2;
plus bootstrap is sub-second per date; partial correlation is closed-form).

Output: `data/models/v6_d293_6_calibrated.json`.

---

## 4. What this doc deliberately does NOT do

- **No new TabPFN runs at n_estimators=default.** Gate C uses the
  existing 2026-04-24 OLD parquet for the A measurement. Generating
  n_est=default for additional dates would take ~6 min/date × 6 = 40 min,
  and the partial correlation at N=24 already gives a clear answer per
  the pre-computed math.
- **No production code changes this commit.** Verdict drives a SEPARATE
  follow-up commit if REVERT.
- **No exploration of D293.7 (n_estimators sweep).** Filed for a
  separate session; not blocking D293's resolution.

---

## 5. Verdict — REVERT per locked outcome map. D293 → D293a (single-seed + n_estimators=2 only).

**Composite: B PASS + C PASS + D FAIL → REVERT per doc 155 §2 outcome map.**

### Numerical results

**Gate B-extended (PASS):**
```
rho_B aggregated (n=249 picks pooled across 7 dates): +0.8954  (p<10⁻⁸⁸)
per-date rho_B: 7 of 7 dates >= 0.80
  2026-04-24: 0.867    2026-04-23: 0.882    2026-04-22: 0.862
  2026-04-21: 0.959    2026-04-20: 0.893    2026-04-17: 0.918
  2026-04-16: 0.950
Gate B (rho_B_agg >= 0.85 AND >=6 of 7 per-date >= 0.80): PASS
```

**Gate C-revised (PASS):**
```
N = 24 (aligned old + new for 2026-04-24)
rho_AB (n_est=default vs n_est=2 single, same seed=42): +0.5617
rho_BC (n_est=2 single vs n_est=2 ensemble):            +0.8670
rho_AC (n_est=default vs n_est=2 ensemble):             +0.6504
partial_rho(A, C | B) = +0.3964  (point estimate above 0.30 threshold)
Fisher z = +0.4193, SE(z) = 0.2236 at N=24, k=1
H0: partial_rho <= 0.30
t-stat: +0.4911, one-sided p-value: 0.3117
Gate C (one-sided p > 0.05, cannot reject H0): PASS
```

The decomposition theory holds: at N=24, we cannot statistically
distinguish partial_ρ ≤ 0.30 from partial_ρ ≈ 0.40. The ensemble
adds no information about the OLD-config A beyond what single-seed
B already captures, within sampling noise.

**Gate D-revised (FAIL — but for instructive reasons):**
```
2026-04-24: delta=+0.0130  95% CI=[-0.1301, +0.1283]  FAIL (-0.130 < -0.05)
2026-04-23: delta=+0.0050  95% CI=[-0.0769, +0.0749]  FAIL (-0.077 < -0.05)
2026-04-22: delta=-0.0052  95% CI=[-0.0774, +0.0766]  FAIL (-0.077 < -0.05)
2026-04-21: delta=-0.0063  95% CI=[-0.0786, +0.0772]  FAIL (-0.079 < -0.05)
2026-04-20: delta=+0.0356  95% CI=[-0.0236, +0.0905]  PASS (-0.024 >= -0.05)
2026-04-17: delta=+0.0489  95% CI=[-0.0010, +0.1010]  PASS (-0.001 >= -0.05)
2026-04-16: delta=-0.0148  95% CI=[-0.0656, +0.0305]  FAIL (-0.066 < -0.05)
Gate D (all dates with CI lower bound >= -0.05): FAIL  (2/7 dates)
```

### Why Gate D failed and what it means

Look at the bootstrap CI structure: the point-estimate deltas are all
between −0.015 and +0.049 (essentially zero ± small ensemble lift), and
the CIs are SYMMETRIC around the point estimate with widths of 0.10 to
0.26 per date. **The bootstrap is correctly characterizing per-date
sampling variance: at N=24-55 picks, the per-date Spearman delta has a
standard error of ~0.05, so a symmetric 95% CI inevitably extends to
roughly ±0.10 from the point estimate.**

For 5 of 7 dates the point estimate is small-positive or small-negative
(within ±0.015). With ~±0.10 symmetric CI width, the lower bound
naturally lands around −0.10 to −0.13 — well below the −0.05 gate.

**This is not "the ensemble is bad."** The point estimates are
essentially zero. The CIs simply tell us we don't have enough data per
day to confirm "the ensemble isn't 0.10 worse than mean per-seed" —
even though the point estimate says "the ensemble is 0.005 better, on
average."

**Gate D as locked was effectively requiring the ensemble to be
visibly POSITIVE by ~0.05 Spearman per date with 95% confidence**, not
"non-catastrophic." The threshold and the CI interpretation interacted
in a way the doc 155 §2 framing didn't anticipate. **The gate's
calibration was wrong** — but per Rule 7 I cannot move it after seeing
the data.

### Why this is a genuine REVERT, not "just a methodology issue"

The strongest argument for "ensemble is fine, just keep it":
- Doc 152 E1 PASS verdict on +0.0196 recent-subset Spearman
- Doc 155 Gate B PASS on apples-to-apples (0.895)
- Doc 155 Gate C PASS on partial correlation
- Gate D's failure mode is "insufficient power per date," not "ensemble underperforms"

The strongest argument for REVERT:
- Doc 152 E1's measurement was the ONLY pre-committed evidence that
  ensemble adds production-relevant lift, and it operated at full WF
  scale (n=1797 recent picks pooled). At single-day scale (n=24-55),
  we cannot confirm or refute the ensemble's per-day quality.
- The shadow runner's job IS to operate at single-day scale daily.
- If we ship ensemble and the per-day quality varies meaningfully from
  the WF-aggregate quality, downstream defensive-overlay analysis would
  reach wrong conclusions.

The disciplined position: **the ensemble code may well be functionally
correct, but our single-day verification infrastructure cannot certify
it. Keeping the conditional ship in place would be assuming the WF-
aggregate result generalizes to single-day predictions — which is
exactly the kind of "trust the aggregate, ignore the per-day" pattern
that has produced phantoms (doc 137 VETOED → doc 138 reversal).**

REVERT to single-seed + n_estimators=2 (the production safety fix,
independent of ensemble research).

### Hygiene Rule 10 (NEW, added permanently)

**"Bootstrap CI gates must be FRAMED to match what's actually being
tested. A 'CI lower bound ≥ −ε' gate at small N is a STRONG positive-
direction test (requires effect ≥ +ε with 95% confidence) when the
true effect is approximately zero with symmetric CI of width 2ε.
For 'effect is non-catastrophic' tests, prefer 'CI contains zero AND
|point| ≤ ε' instead. For 'effect is genuinely positive' tests, use
the strict 'CI lower bound > 0' framing and acknowledge it as such."**

Applied retroactively: doc 155 Gate D should have been EITHER:
- "for each date, the bootstrap 95% CI on delta CONTAINS zero AND
  |point delta| ≤ 0.05" — i.e. delta is statistically indistinguishable
  from zero at this N, AND magnitude isn't large
- OR "for the POOLED data across all 7 dates (n=249), bootstrap 95% CI
  on delta has lower bound ≥ −0.05" — pooling gives the power that
  per-date doesn't have

This is the THIRD gate-design failure across docs 154-155 (Rules 9 + 10
both surface from these failures). The failure modes are now well-
characterized; future gate designs in this domain should reference this
appendix.

### What ships this commit

- **REVERT `fit_predict_tabpfn` to single-seed.** Returns single
  np.ndarray (not the tuple of mean/std/n_seeds_used).
- **REVERT shadow output schema** to 7 columns. Drop `tabpfn_pred_std`
  and `n_seeds_used`.
- **KEEP explicit `n_estimators=2`.** This is the compass §Topic 7
  production-safety fix and is independent of the ensemble research.
  Verified via smoke test: 24 picks scored in 5.4 sec on 2026-04-24
  (vs the 75min/seed runtime with default n_estimators that doc 152
  E1 first-run hit).
- **REVERT D293 unit tests.** Drop `test_d293_ensemble_*` (3 tests
  removed). Add `test_d293a_*` (3 new tests):
  1. `test_d293a_n_estimators_pinned`: N_ESTIMATORS=2 locked
  2. `test_d293a_no_ensemble_constants`: N_SEEDS does NOT exist (pin
     against silent re-introduction of ensemble code without D293.8
     verification)
  3. `test_d293a_single_seed_n_estimators_explicit`: single-seed
     contract enforced via mock; ONE TabPFNRegressor call per
     fit_predict_tabpfn; explicit n_estimators=2 in kwargs

All 10 tests in `tests/unit/test_d286_tabpfn_shadow.py` pass.

### What's now D293a (the production-safety part that survives)

D293a = **explicit `n_estimators=2` in `tabpfn_shadow_runner.py`**.
Independent of ensemble research; compass-validated; zero gate
controversy. This is shipped unconditionally and is a meaningful
production fix (75min/seed → 5sec/seed runtime improvement on RTX 5070).

### D293.8 (filed for next session, ensemble re-test)

Pre-commit framework for D293.8 (the next attempt to verify the
ensemble at sufficient scale):

**Trigger condition:** at least 200 picks accumulated in the shadow
data pool. At ~24-50 picks per shadow day, this means ~5-8 weeks of
shadow accumulation from the next launcher run forward.

**Method:** at trigger time:
1. POOL the shadow data across all dates (no per-date analysis)
2. Generate ensemble-equivalent predictions retrospectively by re-fitting
   TabPFN with 5 seeds at n_estimators=2 on each date in the shadow pool
3. Compute pooled bootstrap 95% CI on (ensemble Spearman vs y_true) −
   (single-seed Spearman vs y_true) at n=200+
4. Pre-commit (locked PRE-RUN per Rule 7+10 framing):
   - Pooled CI lower bound ≥ −0.02 (5× tighter than doc 155's −0.05
     because we now have 5× the data)
   - AND pooled point estimate ≥ +0.005 (genuinely positive direction)
   - AND CI does not span zero in the negative direction (consistent
     with "ensemble at least matches single-seed")

**Outcome:** PASS → re-introduce ensemble code as D293b (this time with
proper power), update unit tests, re-ship. FAIL → ensemble is not
production-relevant at our scale; close ensemble thread permanently.

### Files this commit (REVERT)

| Path | Status |
|---|---|
| `docs/research-log/155_v6_d293_6_calibrated_gates.md` | UPDATED (verdict §5) |
| `scripts/ml_v6_d293_6_calibrated.py` | NEW (~270 LOC) — D293.6 verification script |
| `scripts/tabpfn_shadow_runner.py` | UPDATED (REVERT to single-seed; keep n_estimators=2) |
| `tests/unit/test_d286_tabpfn_shadow.py` | UPDATED (drop 3 D293 tests, add 3 D293a tests) |
| `data/models/v6_d293_6_calibrated.json` | NEW (gitignored) — D293.6 measurements |
| `data/polygon_warehouse/derived/tabpfn_shadow/2026-04-24.parquet` | REGENERATED (D293a single-seed schema; gitignored) |

