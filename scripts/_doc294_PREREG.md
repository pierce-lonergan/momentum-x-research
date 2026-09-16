# DOC 294 PRE-REGISTRATION — frozen 2026-07-12, BEFORE any event-join or gate statistic is computed

Committed pre-execution; sha256 recorded in doc 294. Inherits every program invariant (TARGET.md filter,
ATTEMPTS_LEDGER, disclose-don't-retro-tune, denominator honesty). BOTH instruments below are armed with
PRE-UNBLINDING gates: until coverage/power certifies, only plumbing metrics (coverage counts, join
integrity) may be computed — no P&L, no QLIKE, no gate statistic.

## A. SEVP — scheduled-event variance-risk-premium kill test (the doc-293 BUILD-NEXT #1)

UNIVERSE: the doc-291 151-name panel ∩ names with collected option-price coverage (the $0 collector).
EVENTS: quarterly earnings announcements, 2024-09-15..panel end. Source (both $0, verified 2026-07-12):
yfinance `get_earnings_dates` PRIMARY (timestamped, so BMO/AMC resolve naturally); Polygon
`vX/reference/financials` filing dates as sanity cross-check (event kept only if filing follows within
+10 days when a filing exists); events with ambiguous timing are DROPPED AND COUNTED.

TRADE SHAPE (frozen): short 1 ATM straddle. Enter at the close of the last session strictly BEFORE the
announcement timestamp (T−1); exit at the close of the first session strictly AFTER (T+1). Contract pair =
the collected store's pair covering the event with tenor 5–40 DTE at entry; if several, smallest |DTE−21|.
Prices = option EOD closes from the price store (collector-stored; reconstructed by re-pull for contracts
collected before this prereg).

ECONOMICS per event: net = (premium_T−1 − premium_T+1)/premium_T−1 − cost. COST LINES (frozen, both must
be reported, gates evaluated at both): 5% and 10% of premium round-trip (EOD-quote-width proxy; we hold no
spread data — that is exactly what these two lines bracket, disclosed).

GATES (frozen):
  G1 UNCONDITIONAL CARRY: mean net/event > 0 with event-blocked bootstrap (B=5000) CI95 excluding 0 at
     BOTH cost lines, AND median net > 0, AND sign-replication with CI95 excluding 0 (at the 5% line) in
     both calendar halves of the event sample.
  G2 CONDITIONER SUB-GATE (does OUR signal add?): events gated by the doc-291 RV forecast (skip when
     forecast RV ≥ implied move) must beat the complement on mean net with bootstrap CI95 excluding 0.
     G2 failure does not kill G1; it kills the "our-signal-adds" claim.
  FAT-TAIL HONESTY RULE: report max single-event loss and |max loss| / cumulative net; if any single event
     exceeds 50% of cumulative net in magnitude, the best available verdict is FRAGILE-PASS.
PRE-UNBLINDING COVERAGE GATE: N ≥ 300 events with full two-leg price coverage. Below N: status
PENDING-COLLECTION, gates stay blind. DEATH DATE: 2026-09-01 → report UNRESOLVED to Pierce.
VERDICT MAP: G1 pass at both lines + fat-tail clean → SEVP-PASS (proceed to forward shadow ledger, n≥60,
before any Pierce-gated paper trading); G1 fail at the 10% line only → MARGINAL (report, The operator decides);
G1 fail at the 5% line → SEVP-DEAD, enters ATTEMPTS_LEDGER closed list.

## B. STAGE-3 DATED ACCEPT/KILL — the volatility door resolved at real resolution (BUILD-NEXT #2)

Frozen from `_doc292_STAGE3_PREREG_DRAFT.md` with source fields filled:
DATA: IV store rows source=`polygon_bs_inverted`, kind=atm, tenor 21–45d (nearest-30 per name-day);
names = all collected; underlying RV from the minute-bar warehouse (doc-291 builder unchanged).
MODELS/GATES (unchanged from draft): B = GBM[HAR(3) + log F_cal]; C = B + the 9 transferable features
(rv_ratio_dw permanently dropped); G1 QLIKE(C)<QLIKE(B), block-42 date bootstrap CI95 excl 0, pooled AND
both halves, AND pooled improvement ≥ 2.0%; G2 HLN λ (y−f_B on f_C−f_B), block-bootstrap CI95 excl 0.
PRE-UNBLINDING POWER GATE: the null-resolution (bootstrap CI half-width as % of baseline QLIKE, computed
WITHOUT reading any gate direction) must be ≤ 4.0% (2×MMI). Below power: stay blind, keep collecting.
DEATH DATE: 2026-08-15 → UNRESOLVED-UNDERPOWERED report to Pierce; the door's disposition becomes his call
with the resolution number attached.
VERDICT MAP: PASS both → Stage 4 (spread-aware monetization sim, separate prereg) + the doc-293 VRP-ladder
family unqueues. FAIL both → THE VOLATILITY DOOR CLOSES (recommended; The operator ratifies per doc-291 text).
Split → AMBIGUOUS-AT-RESOLUTION, itemized.

## Multiplicity ledger (this doc): SEVP = 2 gates + 1 sub-gate × 2 cost lines (intersection); Stage-3 =
2 gates (intersection) + 1 power pre-gate. Collector/plumbing metrics are not tests. Every sensitivity any
future skeptic runs gets disclosed and counted in doc 294's final table.
