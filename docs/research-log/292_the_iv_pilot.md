# 292 — THE $0 IV PILOT: ambiguous by the frozen map — not subsumed by implied vol, not measurable at this panel size

**Author**: Claude (Fable 5, ultracode; prereg frozen pre-execution `73cc3bc` sha256 `eb83df87…3628`; G2 skeptic verification) | **Date**: 2026-07-12 | **Class**: PRE-REGISTERED PILOT (Stage 3-lite, $0 spend) + forward shadow-ledger (live) + Stage-3-ready ingestion | **Mandate (Pierce)**: "extract every gram of Stage-3-relevant evidence that costs $0, and leave the program one data-drop away from running Stage 3."

> **First line, the pilot verdict and the spend recommendation, plainly: AMBIGUOUS, per the frozen decision map — and the recommendation is a CONDITIONAL YES to the bounded, cheapest-tier Stage-3 pull.** The pilot's two gates split exactly along the line the prereg anticipated: **G1 (incremental QLIKE over an IV-inclusive baseline) FAILS** — +1.05% pooled, below the 2% minimum, and deep inside the panel's measured **±6.3% resolution** (11 correlated series × ~20 effective windows simply cannot see effects of the size that matters). **G2 (encompassing) PASSES** — the challenger carries weight **w=0.42, CI [0.23, 0.77]** beside calibrated IV: it is **not subsumed** by the market's own forecast. Translated: *the information is real enough to keep a seat at the table next to IV; whether it is big enough to pay for data, only a bigger panel can say — and that is precisely what a single-name purchase buys (~30× the cross-section, resolution well under the 2% bar).* The purchase decision remains yours; the pilot's evidence is attached below.

## §0 — Decision intake + housekeeping

No rulings received → defaults applied: Stage-3 spend NOT authorized this session ($0 path only, honored); Stage-1 config-diff HOLD (untouched); kill/continue pending-ratification (nothing closed or opened); 5% cap frozen. **Housekeeping**: lineage clean (`eb4b498` → prereg `73cc3bc`); rocket-gate n=3/30 with zero holes — *correct*, not stalled (no sessions since 7/9; next advance Monday 7/13); targeted tests green. **⚠ `_doc288_apply_catchup_triggers.ps1` is STILL not run — third session pending.** The task shows one trigger; the relaunch-reliability fix is not live until you run it elevated, once.

## §1 — A1: the free data, verified before the prereg froze

All 13 CBOE vol-index series are **alive and current through 2026-07-10** — including the five single-name indices (VXAPL, VXAZN, VXGOG, VXGS, VXIBM), which were *not* discontinued as feared. VIX3M excluded (63-day tenor → ~9 effective windows, unusable). All 11 underlyings (SPY QQQ IWM DIA USO GLD AAPL AMZN GOOGL GS IBM) fully covered by the local warehouse (620 sessions each). Basis mismatches disclosed (index-option IV vs ETF-proxy RV for the four index pairs; single-name/commodity pairs are clean). Files archived under `data/research/doc292/cboe/`. **$0 spent; no signups.**

## §2 — A2: the pilot (frozen gates; QLIKE on annualized variance; walk-forward)

30-day tenor, 11 pairs, 4,620 scored forecasts over 420 evaluation days:

| forecaster | pooled QLIKE |
|---|---|
| raw IV (descriptive — the flattery basis, excluded from gates) | 0.352 |
| **calibrated IV alone (R0)** | **0.266** |
| B: GBM[HAR + calibrated IV] (the gated baseline) | 0.269 |
| C: challenger (B + the 9 transferable features; ratio dropped per doc-291) | 0.266 |

- **G1 FAIL**: pooled improvement **+1.05%** (< 2% MMI), CI [−0.010, +0.023] includes 0, both halves' CIs include 0. The pre-registered power clause bites exactly as written: the empirical resolution is **±6.26%** — the observed effect is unmeasurable at this panel size. This is *the honest reason the pilot cannot certify anything*.
- **G2 PASS**: encompassing regression gives the challenger **w_C = 0.42, CI [0.23, 0.77]** beside the IV-inclusive baseline (w_B = 0.43) — near-equal weights. The challenger is **not subsumed by calibrated IV**.
- **VRP calibration mattered enormously** (raw 0.352 → calibrated 0.266): the prereg's "raw-IV comparisons are flattery" clause is validated — any vendor pitch or paper comparing to raw IV should be discounted on sight.
- **9d replication (SPY-only, descriptive): sign NEGATIVE** (−2.7%, CI incl. 0), and at h=6 both GBM stacks lose badly to calibrated IV alone (0.52/0.54 vs 0.31). Short-tenor claims have no support; Stage 3 stays at the 30d tenor.

**G2 skeptic verification** (the spend argument leans on G2, so it got the hostile treatment — proper HLN encompassing form, block-63 sensitivity, per-series-group decomposition, attribution-lite; full output `data/research/doc292/iv_pilot_skeptic_g2.json`):

- **G2 SURVIVES — not a multicollinearity artifact.** Despite forecast correlation 0.956 (condition number 31.8 — the exact concern), the clean Harvey-Leybourne-Newbold form (y − f_B = a + λ(f_C − f_B)) gives **λ = 0.47, CI95 [0.29, 0.82] at block-42 and [0.31, 0.83] at block-63**. The challenger genuinely carries information beside calibrated IV.
- **But the decomposition redraws the purchase map.** Non-subsumption lives in the **index-ETF group** (λ=0.60, CI [0.24, 1.24], QLIKE improvement **+6.4%**) and commodities (λ=0.62, improvement −0.9%). The **single-name group — the very thing a Stage-3 purchase buys — is the weakest**: λ=0.27 with the block-42 CI barely excluding 0 ([0.03, 0.64]) and the **block-63 CI including 0** ([−0.02, 0.63]); QLIKE improvement **−4.2%**, negative for 4 of 5 names (best: GS +1.6%; worst: AAPL −9.1%).
- *Attribution-lite*: the doc-291 generic carriers alone (last_hour_rv_share + log_dvol) hold λ=0.40 — most of the non-subsumption again; though the remaining features add weight beyond them pooled (λ=0.69, CI [0.47, 0.90]), that pooled result is index-dominated.
- **What this does to the recommendation**: the conditional-yes stands (G2 survived), but the condition hardens — mega-cap single-name IV markets look efficient against our features *at this resolution*, so the purchase's expected value concentrates in (a) the resolution upgrade itself and (b) the mid-cap/less-efficient tail of the 150-name universe that no free index covers. Cheapest path only; a priced pull should be weighed against simply letting the free forward ledger accumulate.

## §3 — The frozen decision map, applied

The prereg pre-declared: AMBIGUOUS = exactly one gate passes → "say ambiguous; itemize what a purchase would and wouldn't resolve." Itemized:

**A bounded single-name purchase WOULD resolve:**
- The G1 question at real resolution: ~150 names × ~480 days ≈ 70K forecasts vs 4.6K here — cross-sectional breadth cuts the CI half-width from ±6.3% to well under the 2% MMI (the Stage-3 draft prereg adds a pre-unblinding power gate to certify this before verdicts are read).
- Whether the doc-291 gain (durable ~3.6% vs HAR) survives an IV-inclusive baseline *on the universe where it was measured* — single names, where IV markets are less efficient than SPX/NDX and where our intraday features have the most room.
- Which features carry any surviving weight (the attribution machinery is built and waiting).

**It would NOT resolve:**
- Monetization net of option spreads (Stage 4, separate prereg, needs quote data).
- Anything about the 9d/short tenor (dead on this evidence).

**Recommendation (mine to give, yours to make): a conditional yes** — the cheapest-tier bounded pull (check the existing Polygon entitlement first; DataShop one-off as fallback, per [291_PROCUREMENT.md](291_PROCUREMENT.md)). Rationale: G2 says the information exists beside IV; G1's failure is a *resolution* failure, not an evidence-of-absence; the doc-291 forward ledger will meanwhile accumulate free confirmation evidence either way. If you prefer $0 until the forward ledger reports, that is also coherent — the ledger's first certification read arrives at n=60 forward sessions (~3 months).

## §4 — Workstream B: the forward shadow-ledger is LIVE

`scripts/_doc292_rv_forward_ledger.py` — scores challenger-vs-GBM[HAR-only] on every new session across the 151-name panel; append-only, T+1 self-repairing, wired into the nightly `post_close_scorecard` and the config-truth recon (measurement-aware freshness, the doc-287 lesson). **Certification bar frozen in the prereg**: n≥60 forward sessions, day-blocked CI95 > 0, pooled ≥2%; review-for-dead at n=120. Seeded with 10 retro rows (148 names each; mixed +16% to −15% days — exactly why n≥60 is the bar), all correctly excluded from certification. **Status: PENDING-COLLECTION; forward collection begins Monday 2026-07-13.** The ledger changes no decision; it collects.

## §5 — Workstream C: Stage-3-ready ingestion (a data-drop away)

Vendor-agnostic EOD-IV canonical store + loaders for the two likeliest vendor shapes (Polygon-style aggregates; ORATS/DataShop CSV with percent-vs-decimal auto-detection), built against **synthetic fixtures only** — 5 round-trip/validation tests green; no live endpoints, no keys, no signups. Stage-3 prereg **DRAFT** written (`_doc292_STAGE3_PREREG_DRAFT.md`) with only source-identification fields open; it adopts the HLN encompassing form and adds a pre-unblinding power gate. The day data lands locally, Stage 3 runs the same session.

## §6 — Multiplicity ledger + artifacts

Pilot: 2 gates (intersection); descriptive: R0, raw-IV, 9d tenor, resolution statement. Skeptic sensitivities disclosed in §2. Workstream B: 0 claims (collection only). Tests: 38 green this session (ledger status logic ×6, IV ingest ×5, recon 27 incl. the new instrument), plus the standard sweeps. Artifacts: `data/research/doc292/` (cboe/ CSVs, iv_pilot_result.json, ledger_seed.log); `data/reports/rv_forward_ledger.jsonl`.

## §7 — Pierce-action list (in order)

1. **Run `scripts/_doc288_apply_catchup_triggers.ps1` elevated — one click, third session pending.** Until then the bot still has no relaunch path on a failed morning start.
2. **The Stage-3 spend decision**, with this pilot's evidence: G2 non-subsumption (skeptic-verified §2) vs G1 unmeasurable-at-$0. Cheapest path first: check whether your existing Polygon plan already includes options/IV history — that may make Stage 3 nearly free.
3. Ratify the kill/continue text (unchanged, pending since doc 291).
4. Config-diff disposition (HOLD honored; no action needed unless you want it wired).
5. Nothing else requires you: the forward ledger runs itself nightly, and the recon watches it.
