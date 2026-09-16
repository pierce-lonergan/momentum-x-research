# 293 — THE 0.5%/DAY BAR: doc 292 closed, the target frozen as arithmetic, and Stage 3 unlocked at $0

**Author**: Claude (Fable 5, ultracode) | **Date**: 2026-07-12 | **Class**: TARGET RATIFICATION AS ENGINEERING + doc-292 close-out + the $0 Stage-3 unlock + the candidate hunt | **Mandate (Pierce)**: "the program's target is now 0.5%+/day… your first duty is arithmetic honesty about what it requires."

> **First line, both verdicts plainly. Doc 292's final recommendation, post-skeptic: the G2 non-subsumption SURVIVED the HLN attack (λ=0.47, robust at both block sizes — not a multicollinearity artifact), so the conditional-yes stands — hardened by the decomposition: the single-name group a purchase would buy is the weakest (λ block-63 CI includes 0; QLIKE −4.2%). And the purchase question then collapsed, because this session's entitlement probe found the data is FREE: historical option EOD aggregates are already entitled (~2-year window, 5 calls/min), IV snapshot alone is paywalled — so IV is computable at $0 by Black-Scholes inversion. The collector is built, validated, and dripping now. Stage 3 does not need a spend decision; it needs ~9 nights of patience.** The vol door's verdict at real resolution therefore arrives on the frozen Stage-3 gates when the panel lands — with the honest prior set by the pilot: index-level non-subsumption real, single-name subgroup discouraging.

## §0 — Doc 292: shipped (`545acce`)

The skeptic verdict filled the placeholder: pooled HLN λ=0.47 CI [0.29, 0.82] (block-42) / [0.31, 0.83] (block-63) — G2 survives; per-group, index-ETF λ=0.60 (+6.4% QLIKE) vs **single-name λ=0.27 with block-63 CI spanning 0 and QLIKE −4.2%** (4 of 5 names negative). Recommendation as shipped: conditional yes, cheapest path only — now superseded in the best way by §2 below.

## §1 — TARGET.md + ATTEMPTS_LEDGER.md (committed `03b0432`)

**0.5%/day × 252 = +251%/yr** — ~2.5× the best-documented sustained gross performance in finance history. The requirements table (net; the frozen 5% cap in force):

| frequency | @5% cap | @25% | @100% |
|---|---|---|---|
| daily | **+10.0%/ticket net** | +2.0% | +0.50% |
| weekly | +50.5% | +10.1% | +2.53% |
| monthly | +220.8% | +44.2% | +11.04% |

**Distance statement**: best measured configuration ever = +2.7%/ticket net (post-hoc, unstable) = **27% of requirement**; certified edges = **none** = 0%. The live book measures ~−0.2%/day. **Program filter now in force: nothing below ≥10% of requirement (≥+1.0%/ticket net daily at the cap) earns a build.** Standing constraints the target does not override: the 5% cap stays frozen until your written change; leverage/margin/options approvals are yours alone. The arithmetic admits exactly three shapes (TARGET.md §5): high-frequency small-net on liquids, convexity, capital-turn compression — daily-bar cash-equity signals at 5% are pre-filtered. `ATTEMPTS_LEDGER.md` freezes the program-wide closed list (16 families) so no door is silently re-litigated.

## §2 — Stage 3 unlocked at $0 (the session's discovery)

Read-only probes with the existing key (`data/research/doc293/entitlement_probe*.json`):

| endpoint | result |
|---|---|
| reference option contracts (incl. historical `as_of`) | **200, entitled** |
| per-contract EOD aggregates | **200 with real bars — ~2-year rolling window** (bars at 22 months; 403/empty beyond) |
| IV snapshot | 403 — paywalled |
| rate limit | **5 calls/minute** |

So the IV panel Stage 3 needs is free but must be *computed*: **`_doc293_iv_collector.py`** — per name-month, resolve the ~30d-tenor ATM call+put via historical reference (spot from our own warehouse, zero API cost), pull each contract's full daily history (1 call), invert Black-Scholes on every close (r=4.5% flat, q=0 — disclosed approximations, immaterial at ATM-30d scale), average call/put, upsert into the doc-292 canonical IV store. **Validated live**: AAPL median 30d ATM IV 25.9% (correct scale), 218 rows and climbing as the first 8-name tranche drips in the background. Resumable state file; polite 12.5s spacing; bounded budgets. Full 150-name × 22-month panel ≈ 9 nights at a 4h/night budget; the 30-name core ≈ 2 nights. **Stage 3 runs on the frozen-draft gates (HLN form + pre-unblinding power gate) the day the power gate certifies resolution — no purchase, no signup, no plan change.** The [procurement memo](291_PROCUREMENT.md) is updated: a paid tier now buys *speed*, not access.

*Denied and respected*: the auto-mode classifier blocked registering the nightly continuation task — a persistent prod-surface change you haven't explicitly blessed. The wrapper is written (`scripts/iv_collector_nightly.cmd`, registration one-liner in its header); registering it is a 10-second item on your list. Until then I run tranches in-session.

## §3 — The candidate hunt (six lenses, hard arithmetic constraints, ruthless triage)

Six lenses proposed under the TARGET.md constraints (arithmetic chain to ≥10% of requirement stated, closed-list checked, kill test + cost named); a triage agent then **recomputed every chain** and corrected the inflations. The validated table:

| # | family | shape | claimed → validated %-of-req | kill-test cost | verdict |
|---|---|---|---|---|---|
| 1 | **event-vol (SEVP)**: short scheduled-event vol carry (T−1→T+1 earnings/macro, defined-risk, 151 names) | convexity | 100% → **~70% ceiling / ~18% pessimistic** — the only family clearing the filter at its own *pessimistic* line | **$0** (~1,200 events on the IV panel; verify T±1 backfill coverage first) | **BUILD-NEXT** |
| 2 | **Stage-3 dated accept/kill** (the adversary lens's salvaged payload: freeze the 10–45d RV-vs-IV gate with death dates; its "null certificate" was REJECTED as premature — it had missed #1's channel) | process | n/a — it *is* the gate #3 queues behind | $0 | **BUILD-NEXT** (prereg only) |
| 3 | vol-monetizer: conditional VRP ladder (21–30d delta-hedged straddles gated by our RV forecast) | convexity | 93% → **~13%** (44% only *conditional on Stage-3 passing*; the 4-vol-pt gap was unsupported, the 21-position ladder infeasible, the spread haircut light) | $0 | QUEUE (behind Stage 3; not independent) |
| 4 | liquid-hf: LETF/index close-window rebalance-flow harvest | high-freq | 12.5% ceiling confirmed; **central <5%** (post-2014 decay) | $0, ~1 day | QUEUE (optional; mandatory flow-vs-raw-return rank-calibration — it is intraday momentum in disguise otherwise) |
| 5 | turns-compressor: RV-gated passive liquidity provision | turns | 10% → **4–8%** (threshold-exact only with every assumption stacked) | $0 | **REJECT — fails the ≥10% filter** |
| 6 | market-neutral: 3σ residual-reversion spreads | turns | 72% → ≤20%, moot | — | **REJECT — closed-family variant** (per-name direction with a beta hedge bolted on; reopening needs your sign-off per the ledger) |

**Panel recommendation (adopted as the next-session queue):** freeze two $0 preregs — (1) the **SEVP event-vol backtest** (G1 unconditional carry at two frozen cost lines, G2 conditioner sub-gate, fat-tail honesty rule), after verifying the entitled option backfill covers the event dates; (2) the **Stage-3 dated accept/kill** (frozen HLN λ + QLIKE + pre-unblinding power gate, death dates on the collectors) — which simultaneously resolves the open Volatility-door entry and is the sole gate the VRP ladder queues behind. Both run with zero spend, zero options approval, zero Pierce gates *to test* (only to *trade*). Turns-compressor and market-neutral enter the attempts ledger as filtered-out/closed-variant without a build. All six proposals + the triage corrections are recorded in the ledger (multiplicity: 6 proposals, 1 triage, 0 kill tests yet run).

## §4 — Housekeeping

Rocket-gate n=3/30, zero holes (correct — no sessions since 7/9; first advance Monday). Forward RV ledger: PENDING-COLLECTION, n_forward=0, first forward rows land Monday 7/13 (10 retro rows seeded and excluded). Lineage `eb4b498` → `73cc3bc` → `545acce` → `03b0432`; targeted sweeps green throughout; secrets scans clean.

## §5 — Pierce-action list

1. **`_doc288_apply_catchup_triggers.ps1` — FOURTH session pending.** One elevated click; the relaunch fix is not live until then.
2. **Register the IV-collector nightly task** (10 seconds, no elevation): the one-liner is in the header of `scripts/iv_collector_nightly.cmd`. Without it I drip only when a session is open.
3. The Stage-3 *purchase* decision is now moot at the base tier — decide only if you want **speed** (vendor IV in days instead of ~9 nights).
4. Kill/continue ratification (pending since doc 291) and the Stage-1 config-diff disposition (HELD) — unchanged.
5. The 5% cap remains frozen per your standing word in TARGET.md §4.

## §6 — The closing distance statement (cold numbers)

The program's best measured configuration delivers **+2.7%/ticket net, post-hoc and unstable — 27% of the +10%/ticket that 0.5%/day demands at the frozen cap**; its certified inventory stands at **zero**; its live book measures **−0.2%/day**. The gap to the target is therefore not a tuning gap but a **category gap**: it requires either an instrument class we do not yet trade (convexity — the one asset we hold there is a verified but generic RV-forecast advantage, now being priced against IV at $0), or a cost structure we have never operated in (liquid high-frequency), or an evidence standard none of our 60+ gated hypotheses has met. That distance is the metric. This session moved it by: unlocking Stage 3 without spend, freezing the bar so nothing weaker gets built, and putting six constrained candidates through triage. Every future session either shrinks the distance or reports it unshrunk — in these units.
