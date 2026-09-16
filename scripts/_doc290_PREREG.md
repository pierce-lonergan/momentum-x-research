# DOC 290 PRE-REGISTRATION — frozen 2026-07-11, BEFORE Stage A / Workstream B execute

Committed to develop before any acceptance statistic is computed. sha256 of this file recorded in doc 290.
Per doc-275/277 discipline: no post-hoc criterion edits; misleading verdict strings may be corrected, frozen
parameters may not (disclose, don't retro-tune).

## Data (Stage 0, built with no claims)
`data/research/doc290/events.jsonl`: 875 events, 51 sessions (2026-04-14..07-09), one event per name-session.
t0 = first morning snapshot; observation window [t0, t0+15min); DECISION at t0+15; features strictly
pre-decision; outcomes strictly post-decision from the minute-bar warehouse. ICC(peak_run60)=0.0026,
effective N≈840. Regime split (frozen) = 2026-05-26 (early 414 / late 461). Missing float 53.9% →
median-impute + missingness indicators. Cost floor (frozen) = 1.5% round-trip per ticket.

## Stage A — H-LOCAL existence test (go/no-go for the entire vector-DB architecture)
HYPOTHESIS H-LOCAL: conditional payoff structure is locally organized — a k-NN/kernel estimate in feature
space carries payoff information beyond a strong global model.

- FEATURES (frozen list): absolute {gap_pct, rvol, log_price, log_float, log_pmv, log_mcap, has_news, mfcs,
  float_rotation, miss_float, miss_fr, miss_mcap} + attention {pm_dvol_share, cohort_pm_herfindahl,
  cohort_size, cohort_sd_gap_pct, cohort_sd_rvol, cohort_sd_float_rotation, centroid_dist, z_gap_pct, z_rvol,
  z_price, z_mfcs, z_float_rotation, rank_gap_pct, rank_rvol, rank_price, rank_mfcs, rank_float_rotation,
  dvol15_share} + path {r5, r10, r15, maxdrawup15, maxdd15, range15, vwap_dev15, higher_low_frac, vol_slope15}.
  Impute = train-fold median; scale = train-fold z-score.
- OUTCOMES: primary info outcome = peak_run60; policy outcome = ret_close (fillable: buy at decision px,
  exit at session close; peak is NOT fillable and is never a policy metric).
- MODELS: (i) unconditional mean; (ii) GLOBAL = HistGradientBoostingRegressor(max_iter=200,
  learning_rate=0.05, min_samples_leaf=20, l2_regularization=1.0, random_state=290); (iii) LOCAL = kNN
  regressor (uniform weights, k selected from {5,10,25,50,100,200} by inner grouped 3-fold on train);
  (iv) HYBRID = (ii) + kNN on train residuals (same k grid, inner-selected).
- CV: grouped 5-fold by session, seed 290. No row-level splits.
- METRICS: M1 = pooled-OOS Spearman(score, peak_run60). M2 = mean net ret_close of the pooled-OOS top decile
  by score, minus the 1.5% floor.
- GATES (all frozen):
  G1: Δ = M1(local) − M1(GLOBAL) for local ∈ {kNN, HYBRID}. Null = shuffle outcomes (rows) WITHIN session,
      B=200, full-pipeline recompute. PASS iff p < 0.01 after Bonferroni ×2 (resolution: 0/200 or 1/200
      exceedances required).
  G2: Δ > 0 (same sign) on BOTH sides of the 2026-05-26 split (independent grouped CV per side).
  G3: M2(best local model) > 0 AND session-blocked bootstrap (B=5000) CI95-lo > 0.
  STAGE A PASSES iff G1 AND G2 AND G3. Fail → the vector-DB / micro-strategy-library architecture is DEAD
  for this universe; ship the tombstone with the information-vs-scale curve.
- CURVE (reported either way): pure-kNN OOS Spearman at each fixed k ∈ {5,10,25,50,100,200,500} — the
  empirical answer to "how many micro-trends does the data support". SELECTIVITY curve (reported either
  way): net-of-floor mean ret_close at top 2/5/10/20% by best-local and by GLOBAL score — the honest answer
  to "one trade per month".
- Stages B (clustering), C (per-regime policies), D (vector DB) execute ONLY if Stage A passes.

## Workstream B — attention-coupling regime cells (closes the doc-289 crack)
QUESTION: is there any pre-declared regime cell where the attention field couples to price?

- CELL FAMILIES (frozen; 6 families, 16 cells):
  F1 cohort premarket-$vol Herfindahl quartiles (pre-open observable) — 4 cells
  F2 cohort catalyst-fraction ≥ median vs < median — 2 cells
  F3 cohort max float-rotation top quartile vs bottom quartile — 2 cells
  F4 SPY overnight gap sign (warehouse minute bars; session 9:30 open vs prior session close) — 2 cells
  F5 outcome horizon windows: peak_run15 / peak_run30 / peak_run60 / peak_run_full — 4 cells
  F6 regime split early vs late — 2 cells
- STATISTICS per cell (2): S1 = pooled within-cohort Spearman(pm_dvol_share rank, outcome rank);
  S2 = winner-coincidence rate P(argmax pm_dvol_share == argmax outcome) vs within-cohort random-pick null.
  Outcome = peak_run60 for F1-F4, F6; per-window peak for F5. Null = within-cohort permutation, B=2000.
- MULTIPLICITY: Bonferroni across the 6 families (family-level α = 0.05/6), Benjamini-Hochberg within each
  family across its cells×statistics.
- A cell "COUPLES" only if: survives correction AND replicates (same sign, nominal p<0.05) on both sides of
  the 2026-05-26 split (F6 exempt from its own split; flagged descriptive) AND its implied policy (buy the
  cell's pm-$vol leader at decision, hold to close, net 1.5%) has session-blocked bootstrap CI95-lo > 0.
  Anything less = characterized noise. Either way the doc-289 crack is CLOSED.

## Multiplicity ledger (the denominator of attempts, reported in doc 290)
Stage A: 2 gate hypotheses (kNN, HYBRID) + 7 curve points + 8 selectivity points (curve/selectivity are
descriptive, not gates). Workstream B: 16 cells × 2 statistics = 32 tests. Families: 2 (Stage-A gates;
Workstream-B cells). Every number reported net of the 1.5% floor where it is a policy claim.
