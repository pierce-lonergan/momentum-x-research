# DOC 291 PRE-REGISTRATION — frozen 2026-07-11, BEFORE any stage executes

Committed to develop before any acceptance statistic is computed; sha256 recorded in doc 291. Inherits every
doc-290 invariant (grouped CV, permutation nulls, cross-regime replication, multiplicity ledger, denominator
honesty, ts_et guard, disclose-don't-retro-tune). Standing constraint carried in from doc-290 §6: the 5%
position cap is FROZEN and concentration is not an edge instrument — Stage 1 sizing may only SHRINK exposure.

STATUS OF THE INPUT SIGNAL (honesty clause): the doc-290 rank-ρ≈0.30 volatility signal was a post-hoc
characterization inside a failed prereg. It is treated as an UPPER estimate; every stage below re-derives it
fresh under its own frozen gates. If the fresh estimate is materially lower, that is the number.

## STAGE 0 — DECOMPOSITION: what is the vol signal made of?
Data: doc-290 events.jsonl (875 events / 51 sessions), grouped 5-fold by session, seed 291.
Learner for EVERY rung (fair-baseline lesson of doc 290): HistGradientBoostingRegressor(max_iter=200,
lr=0.05, min_samples_leaf=20, l2=1.0, random_state=291) on the TRAIN-fold RANK-transform of peak_run60.
Metric M = pooled-OOS Spearman(score, peak_run60). Secondary (descriptive): AUC vs stop10_hit.
Nested ladder (features cumulative):
  B0: unconditional (M=0 by construction)
  B1: log_price
  B2: + log_float, float_rotation, miss_float, miss_fr
  B3: + trail_rv20 (per-ticker realized vol of daily closes over ≤20 prior sessions from the minute-bar
       warehouse; miss_trail indicator; coverage documented), trail_atr14_pct
  B4: + ticker_loo_peak_mean (mean peak_run60 of the SAME ticker's OTHER sessions; miss indicator)
  FULL: ∪ of the doc-290 39-feature set and all B1-B4 features.
FROZEN GATES (2 tests, BH at α=0.05, within-session permutation null B=200 with full-pipeline recompute):
  D1: M(FULL) − M(B3) — "does anything beat cheap+small+recently-volatile?"
  D2: M(FULL) − M(B4) — "does anything beat that plus per-ticker history?"
  Each must also REPLICATE (same sign) on both sides of the frozen 2026-05-26 split.
VERDICT LANGUAGE (frozen): if D2 fails or M(FULL)−M(B4) < 0.03, the doc's first-line decomposition verdict is
"the signal reduces to a known volatility-proxy stack" and the transfer thesis is marked accordingly. The
attribution curve (M at every rung) is reported as a table either way.

## STAGE 1 — IN-UNIVERSE DEFENSIVE UTILITY (risk-shaping, not edge creation)
Disclosed substitution: the live paper ledger has n≈10 closed trades (underpowered); Stage 1 therefore runs
on the 875-event book under the fillable policy (enter at decision px, exit at close, net 1.5% floor),
equal-$ per event. Score = the pooled-OOS FULL score from Stage 0. Frozen expected best case: variance /
drawdown reduction at roughly unchanged mean — a portfolio-quality improvement, NOT an edge. Analysis only;
a passing gate emits a config-diff PROPOSAL for Pierce, never an applied change.
Three pre-declared uses:
  U1 exclusion: drop events above the OOS score's top-decile / top-quintile (both thresholds pre-declared).
  U2 inverse-vol sizing (shrink-only under the frozen cap): weight = 1 − score_rank ∈ [0,1], NOT renormalized.
  U3 stop-width: baseline fixed stop −10% (stopped if maxdd60 ≤ −10%, exit at the stop, no overshoot —
      optimistic for both arms equally); conditioned arm: −7% stop for bottom-half score, −13% for top-half.
FROZEN GATES per use: (a) improvement ≥20% relative in session-level max drawdown of the cumulative book OR
in stop-out rate, with session-blocked bootstrap CI95 of the improvement excluding 0; AND (b) net-P&L
non-degradation: session-blocked bootstrap CI95 of Δ(mean net/event) does not exclude 0 from below (i.e., no
demonstrable harm). Per-dollar-deployed and total-book numbers both reported for U1/U2. A null ships as a null.

## STAGE 2 — TRANSFER FEASIBILITY ON LIQUID NAMES (the pivot go/no-go; free data only)
Universe: 151 pre-probed liquid names (large/mid-cap + liquid ETFs), local warehouse minute bars,
2024-01-16..2026-07-09 (median 620 sessions; verified before this prereg froze — no new data spend).
Target: next-day realized volatility; RV_d = sqrt(Σ 1-min RTH squared log returns); modeling in log RV;
QLIKE evaluated on the variance scale: L = V/F − ln(V/F) − 1 with V = RV², F = forecast variance.
Secondary target (descriptive): next-5-day mean RV.
Baselines (the incumbent wall): (a) HAR-RV pooled OLS with ticker fixed effects — log RV_d, log RV_w5,
log RV_m22; (b) GBM on the same 3 HAR features (model-class control — the doc-290 lesson);
(c) reference only: GARCH(1,1) per name (or EWMA λ=0.94 if the GARCH fit is infeasible; disclosed).
Challenger: GBM on [HAR features + transferable features computed IDENTICALLY in this universe]:
|overnight_gap|, day range_pct, first15_ret, first15_range, last_hour_RV_share, vwap_dev_close,
higher_low_frac, vol_slope (2nd-half volume share), log dollar_vol, RV_d/RV_w ratio.
DROPPED IN TRANSLATION (documented): cohort/attention-field features (no cohort exists), float/rotation
(no float data), mfcs/has_news (bot-specific). Path-shape and volume-structure features are the transfer
candidates; that is exactly what Stage 0's D2 measures the novelty of.
Protocol: strict walk-forward — train on all rows with date < t (min 120 sessions of history), refit every
21 sessions, predict one day ahead; no row-level splits; evaluation partitioned into two disjoint halves
H1/H2 by calendar for replication.
FROZEN GATES: the challenger must beat BOTH (a) and (b) on pooled QLIKE with a date-blocked moving-block
bootstrap (block = 10 trading days, B=5000) CI95 of the mean loss differential excluding 0, in BOTH halves;
AND the MINIMUM MEANINGFUL IMPROVEMENT (frozen now, before running): pooled QLIKE reduction ≥ 2.0% vs the
better baseline AND ≥ 1.0% in each half. A statistically-significant sub-2% gain is reported as
"open at the margin, not pivot-worthy". Rank-ρ improvement is descriptive.
CONTEXT (frozen into the doc either way): in liquid markets the tradable benchmark is IMPLIED vol, which is
stronger than HAR-RV. Beating HAR-RV is the NECESSARY condition that justifies buying options data; it is
not sufficient for edge. Losing to HAR-RV is fully sufficient to kill the pivot.

## STAGE 3+ (Pierce-gated) — only if Stage 2 passes: write docs/research-log/291_PROCUREMENT.md (vendor OPTIONS
for historical IV/EOD chains — granularity, depth, scope; broker options-approval path). Execute NOTHING:
no purchase, no subscription, no credentials, no permission changes.

## MULTIPLICITY LEDGER (the denominator): Stage 0 = 2 gate tests (+6 descriptive rungs); Stage 1 = 4 gate
tests (U1×2 thresholds, U2, U3; BH within stage at α=0.05); Stage 2 = 1 gate (intersection of 2 baseline
comparisons — conservative); descriptive curves exempt and labeled. Every number net of the 1.5% floor where
it is a policy claim (Stage 1); Stage 2 is a forecasting claim, no P&L language permitted in its verdict.

## PROPOSED PROGRAM KILL/CONTINUE (for Pierce to ratify — NOT frozen by the agent): the volatility door is
"real" only if Stage 2 passes its frozen gates AND a future paid Stage 3 shows the challenger adds forecast
value over the implied-vol baseline AND a Stage 4 monetization sim clears realistic option spreads. Proposed
program budget language: if Stage 2 fails, no further universes are funded on this signal family without a
new pre-registered hypothesis of comparable novelty (doc-289/290 class).
