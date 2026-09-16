# DOC 292 — STAGE 3 PREREG **DRAFT** (NOT FROZEN — freeze+hash only when purchased data lands)

Status: DRAFT. Only the source-identification fields (marked ⟨OPEN⟩) may change at freeze time; changing any
gate after seeing data is a protocol violation. Informed by the doc-292 $0 pilot (AMBIGUOUS: G2 encompassing
passed — the challenger is not subsumed by calibrated IV — but G1 was inside a ±6.3% resolution).

## Question
On the ~150-name liquid single-name universe, does the doc-291 challenger add forecast value over an
IV-INCLUSIVE baseline — with enough resolution to give a definitive answer the index pilot could not?

## Data (⟨OPEN⟩ until purchase)
- Vendor: ⟨OPEN — per 291_PROCUREMENT.md options⟩; EOD constant-maturity 30d IV (or ATM), ≥2 years,
  for the doc-291 151-name panel (accept ≥120 names covered); ingested via `_doc292_iv_ingest.py` into the
  canonical store; loader round-trip tests must pass before any statistic is computed.
- Underlying RV: the existing warehouse minute-bar panel (doc-291 builder, unchanged).

## Frozen design (values final unless marked open)
- Tenor: h = 21 trading days (30-calendar CM IV). Target V(t) = (252/h)·Σ rv_d²(t+1..t+h).
- Calibrated IV per name: rolling MZ log-regression, trailing 252 fully-realized rows, min 100 (doc-292 §A).
- Models (GBM = doc-291 frozen hyperparams; walk-forward refit 21; train rows fully realized):
  B = GBM[HAR(3) + log F_cal]; C = B + the 9 transferable features (rv_ratio_dw permanently dropped).
- G1: QLIKE(C) < QLIKE(B), moving-block bootstrap by date (block=42, B=5000), CI95 excl 0, pooled AND both
  calendar halves, AND pooled improvement ≥ 2.0% (MMI carried from doc-291/292).
- G2: HLN encompassing — y − f_B = a + λ(f_C − f_B); block-bootstrap CI95 of λ excluding 0. (HLN form
  adopted from the doc-292 skeptic's critique of naive two-forecast regressions.)
- Power gate (pre-run): compute the null-resolution (bootstrap CI half-width) on the purchased panel BEFORE
  unblinding gates; if resolution > 2× MMI the test is declared UNDERPOWERED and does not proceed to verdict.
- PASS = G1 AND G2 → Stage 4 (monetization sim net of option spreads — separate prereg).
  FAIL = both gates fail → THE VOLATILITY DOOR CLOSES per the doc-291 kill/continue text (The operator ratifies).
- Multiplicity: 2 gates, intersection; every sensitivity disclosed and counted.
