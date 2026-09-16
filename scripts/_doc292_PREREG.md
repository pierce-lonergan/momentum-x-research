# DOC 292 PRE-REGISTRATION — frozen 2026-07-12, BEFORE the pilot executes

Committed pre-execution; sha256 recorded in doc 292. Inherits every doc-290/291 invariant. §0 intake
(defaults applied — no Pierce rulings received): Stage-3 spend NOT authorized ($0 path only); Stage-1
config-diff HOLD; kill/continue pending-ratification (nothing this session closes or opens the family);
5% cap frozen per doc-290 §6.

## Workstream A — the $0 IV pilot (Stage 3-lite)
QUESTION: on universes where implied-vol benchmarks are FREE, does the doc-291 challenger add anything
over an IV-INCLUSIVE baseline? A directional read on the Stage-3 prior; it cannot certify the door.

DATA (verified before this freeze; archived data/research/doc292/cboe/, fetched 2026-07-12, all series
current through 2026-07-10): CBOE daily index closes — VIX, VIX9D, VXN, RVX, VXD, OVX, GVZ, VXAPL, VXAZN,
VXGOG, VXGS, VXIBM. VIX3M EXCLUDED (63-trading-day tenor → ~9 effective windows, unusable). Single-name
indices verified ALIVE (not discontinued). Underlying RV from the local minute-bar warehouse
(2024-01-16..2026-07-09, 620 sessions/underlying, all 11 underlyings covered incl. USO/GLD).

PANEL: 30-day tenor (h=21 trading days): VIX→SPY, VXN→QQQ, RVX→IWM, VXD→DIA, OVX→USO, GVZ→GLD,
VXAPL→AAPL, VXAZN→AMZN, VXGOG→GOOGL, VXGS→GS, VXIBM→IBM (11 pairs). 9-day tenor (h=6): VIX9D→SPY only —
REPLICATION check (sign agreement), NOT a hard gate (single series; power).
DISCLOSED BASIS MISMATCHES: VIX/VXN/RVX/VXD price INDEX options vs ETF-proxy RV (SPY≈SPX/10 etc.);
single-name and commodity indices price the underlying's own options (cleaner).

DEFINITIONS (frozen): daily rv_d = sqrt(Σ 1-min RTH squared log returns) (doc-291 estimator).
Target V(t) = (252/h)·Σ_{i=1..h} rv_d²(t+i) — annualized forward variance. IV variance = (close/100)².
CALIBRATED IV (raw-IV comparisons are flattery and excluded from gates): per series, rolling OLS
log V ~ a + b·log(IV²) on the trailing 252 rows whose target windows are FULLY REALIZED by t (row date
≤ t−h), min 100 rows; F_cal = exp(fitted) (Jensen transform shared by all models).

MODELS (GBM hyperparams = doc-291 frozen set; pooled across series; walk-forward refit every 21 sessions;
train rows require fully-realized targets — row date ≤ refit−h; min 150 train rows):
  R0 (reference, descriptive): calibrated IV alone.
  B  (baseline): GBM[ log rv_d, log rv_w5, log rv_m22, log F_cal ].
  C  (challenger): B's features + the 9 transferable features (doc-291 set MINUS rv_ratio_dw, dropped per
     the doc-291 attribution finding, disclosed): |overnight_gap|, range_pct, first15_ret, first15_range,
     last_hour_rv_share, vwap_dev_close, higher_low_frac, vol_slope, log_dvol.

FROZEN GATES (30d panel; QLIKE on annualized variance):
  G1 incremental value: QLIKE(C) < QLIKE(B) with moving-block bootstrap by date (block=42 ≈ 2×h, B=5000)
     CI95 of the mean loss differential excluding 0, POOLED AND in BOTH calendar halves; AND pooled
     improvement ≥ 2.0% (the doc-291 MMI, carried).
  G2 encompassing: pooled-OOS OLS log V ~ w0 + w1·log F_B + w2·log F_C; block-bootstrap (42) CI95 of w2
     excluding 0 — the challenger must carry weight BESIDE the IV-inclusive baseline.
  9d tenor: same pipeline, SPY-only; replication = same sign of the G1 differential (reported, not gating).

PRE-DECLARED DECISION MAPPING (frozen):
  PILOT-PASS  = G1 AND G2            → recommendation FOR the bounded Stage-3 data pull.
  PILOT-FAIL  = G1 fails AND G2 w2 CI includes 0 → recommendation AGAINST Stage-3 spend. The pilot does
                NOT formally close the family (index/mega-cap-level ≠ the full single-name universe) —
                that ruling stays Pierce's per the doc-291 kill/continue text.
  AMBIGUOUS   = exactly one gate passes → say ambiguous; itemize what a purchase would and wouldn't resolve.
POWER HONESTY: overlapping h=21 windows → ~28 effective windows/series × 11 correlated series. The
empirical resolution (null-bootstrap CI half-width) is computed and stated in the doc; if the observed
improvement is inside resolution, that is AMBIGUOUS regardless of point sign.

MULTIPLICITY: 2 gates (intersection = conservative); R0/9d/raw-IV comparisons descriptive and labeled.

## Workstream B — the RV forward shadow-ledger (certification bar frozen NOW)
Nightly append-only instrument scoring challenger-vs-GBM[HAR-only] on each new session across the
151-name doc-291 panel (rocket-gate durability pattern: T+1 self-repair, measurement-aware freshness).
FROZEN CERTIFICATION BAR: the forward ledger may be cited as confirmation of the doc-291 result ONLY at
n ≥ 60 forward sessions with day-blocked bootstrap QLIKE-improvement CI95 excluding 0 AND pooled
improvement ≥ 2.0%. Review-for-dead at n=120 if the CI still includes 0. Status: PENDING-COLLECTION.
The ledger changes no decision this session; it only collects.

## Workstream C — Stage-3-ready ingestion (fixtures only)
Vendor-agnostic EOD-IV schema + loaders for the two likeliest shapes, built against SYNTHETIC fixtures
(no live endpoints, no keys, no signups); Stage-3 prereg DRAFT (explicitly not frozen).
