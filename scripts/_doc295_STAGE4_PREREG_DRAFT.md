# DOC 295 — STAGE-4 MONETIZATION PREREG **DRAFT** (NOT FROZEN — inputs do not exist yet)

Status: DRAFT for the two convexity families (SEVP if its backtest passes; the VRP ladder if Stage-3 passes).
Freeze+hash only when (a) the upstream gate has passed AND survived its skeptic fleet, and (b) the quote data
named below exists locally. Only ⟨OPEN⟩ fields may change at freeze.

## What Stage 4 must prove
A gate-passing signal is not an edge until it clears EXECUTION: option spreads, assignment/pin risk, and
capacity. Stage 4 is the spread-aware simulation whose output is stated directly in TARGET.md units
(net %/ticket at the frozen 5% cap → % of the +10%/ticket daily requirement).

## Inputs this draft is blocked on (procurement fields — Pierce)
- **Option QUOTES, not closes**: EOD NBBO bid/ask (minimum) for the traded contracts; the entitled
  aggregates give trade closes only. Every cost in Stages 2-3 was a stated haircut; Stage 4's whole job is
  to replace haircuts with quoted spreads. Vendor: ⟨OPEN⟩ (per 291_PROCUREMENT.md — DataShop one-off is the
  bounded-cost shape; verify current pricing).
- Corporate-action-adjusted underlying closes (owned) and earnings timestamps (owned, yfinance cache).

## Frozen-shape design (values final unless ⟨OPEN⟩)
- Fill rule: SELL at bid, BUY at ask (no mid-fills, no improvement); one sensitivity at mid±25% of half-spread,
  labeled sensitivity.
- SEVP arm: the frozen doc-294 trade shape; entry/exit at T−1/T+1 quoted EOD; assignment risk: any ITM short
  leg at T+1 close is bought back at ask (no exercise modeling beyond that — disclosed simplification);
  pin risk: events with |S−K|/S < 0.5% at T+1 flagged and reported separately.
- VRP-ladder arm (only if Stage-3 passed): 21–30d delta-hedged straddles per the doc-293 spec; daily hedge at
  the underlying close, hedge cost 1bp/turn ⟨OPEN: replace with measured⟩.
- Gates: mean net %/ticket at quoted spreads > the TARGET floor for the family's claimed frequency
  (SEVP daily-resolving ladder: ≥ +1.0%/ticket net = 10% of requirement, the program filter), event/date-blocked
  bootstrap CI95 excluding 0, both calendar halves; capacity statement (size at which quoted depth is exceeded)
  mandatory in the doc.
- Multiplicity: 1 gate per arm + declared sensitivities; skeptic fleet mandatory on any pass.
