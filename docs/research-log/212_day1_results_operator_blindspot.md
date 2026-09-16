# 212 — Day 1 results (6/1) + the Operator's first blind spot

**Author**: Claude Opus 4.8
**Date**: 2026-06-01 (Monday, post-close)
**Mandate**: Pierce — "check out today's results."

---

## 0. The honest scorecard (broker truth, NOT the journal)

| | Value |
|---|---|
| Journal says | **+$304.20 "profitable"** — STG PHANTOM, fiction |
| Broker realized | STG ghost force-closed (D242) ≈ +$67; **CMND −$949.74; OPTU −$487.48** |
| Real day | **a LOSS (≈ −$1,370 realized)** |
| Carried overnight (UNINTENDED) | **CMND (−$1,508 unrl), OPTU (−$208 unrl)** + STG mark |
| Equity | Fri $158,785 (incl STG mark) → **$147,045 now** |
| Open at 17:23 | CMND 3282 (avail=0), OPTU 6954 (avail=0), STG 3382 |

The day was a loss the bot's own journal labeled "+$304 profitable." (The doc-209 EOD fix —
running today via fc9b4c0 — should make the *displayed* Discord report read the honest
broker P&L, not the journal phantom. Verify tonight's EOD embed.)

## 1. Three real trading problems (the recurring phantom/ghost/carry class)

1. **STG phantom poisoned the journal.** Journal booked STG +$304.20 via a close path that
   never called `record_close()` → `D222 PNL_RECON DIVERGENCE` + the broker had a 3382-share
   **GHOST** (`D231 RECON_HARD_BLOCK QTY STG: internal_qty=MISSING broker_qty=3382`) firing
   **every 30s all afternoon**. D242 EOD force-close eventually flattened it (cancel-stops →
   retry attempt 2 — my doc-209 mechanism WORKING) but the journal P&L was already fiction.
2. **EOD close fires too late → unintended overnight carry.** The smoking gun:
   `16:00:31 [Phase 4] D90: Market is CLOSED at 16:00 ET. Skipping position close to avoid
   unfillable orders. Positions will carry overnight: ['CMND','OPTU'].` The 15:55 D76 close
   didn't flatten CMND/OPTU (the same `avail=0` held_for_orders 403 as STG), and Phase 4 at
   16:00 is past the bell → it deliberately skips → 2 losers carry overnight unintentionally.
3. **`avail=0` held_for_orders on CMND+OPTU** — the same reservation-by-protective-stop
   that 403s the close. STG got the D242 ghost path; CMND/OPTU didn't and carried.

## 2. THE headline finding — the Operator's first blind spot (the meta-anti-selection check, live)

At 16:10 close-out the Operator reported: **"INCIDENTS unresolved: 0 · nothing requires
action — correct no-op · score 25."** Simultaneously the broker had a $3K+ ghost/phantom/
overnight-carry mess and `D231 RECON_HARD_BLOCK` was firing every 30s. **The Operator was
blind to a real, ongoing incident — and scored itself +25 for "restraint" while sitting on
it.** This is EXACTLY the failure Pierce's design critique predicted: "the Operator may be
most confident exactly when it's most wrong."

Root cause: `incident_synth` (doc 208) only reads `recon_status.json` + greps the log for a
narrow pattern set, and **missed** the loudest real signals — `D231 RECON_HARD_BLOCK`,
`D222 PNL_RECON DIVERGENCE`, `D242 EOD_FORCE_CLOSE`, and the `D90 overnight carry`. The
observe Operator's "all clear ✅" was a FALSE NEGATIVE. Day 1 already justified the
observe-first discipline: we did NOT arm T1, so a blind Operator did no harm — it just
mis-scored. **Before T1 is armed, incident_synth must detect these.**

## 3. Fixes (next, prioritized)

- **P0 — teach `incident_synth` the real signals** (mechanical, safe, Operator-side only):
  emit incidents for `D231 RECON_HARD_BLOCK` (ghost), `D222 PNL_RECON DIVERGENCE` (phantom),
  `D242 EOD_FORCE_CLOSE`, `D90 ... carry overnight`, and a journal-vs-broker delta from the
  EOD json. Then a blind "all clear" becomes a true "⚠️ ghost + phantom + 2 carries". Add a
  calibration note: the Operator's "0 incidents / score 25" today was WRONG — log it as the
  first confidence-vs-outcome miscalibration data point.
- **P1 — EOD close timing**: the real close must complete BEFORE the bell. Move the D76/D90
  flatten earlier (e.g. 15:50) and/or apply the doc-209 cancel-settle path to CMND/OPTU the
  way D242 does for ghosts, so `avail=0` positions actually close intraday instead of
  carrying. (Live-behavior — verify carefully; this is the dominant P&L leak today: 2
  losers carried overnight.)
- **P1 — the phantom root**: STG's journal +$304 with a broker ghost is the doc-185 B1
  event-sourced-ledger class (book P&L only on confirmed broker fills). Strategic.

## 4. What WORKED on day 1
- Operator schedule: all 10 pulses fired on time, auto-labeled, Discord heartbeats posted.
- doc-209 cancel-settle / D248 cancel-blocking-stops: STG ghost WAS force-closed (not left
  naked) — `D246 RETRY succeeded on attempt 2`. The mechanism is sound; it just needs to run
  for CMND/OPTU pre-bell too.
- The EOD recon (`broker_truth_recon`, `D222`, `D231`) correctly DETECTED every problem —
  the data to fix this is all there; the Operator just wasn't reading it.

## Appendix — evidence
- `data/reports/eod_2026-06-01.json` (broker_total_pnl=0, journal=+304.2, delta=-304.2,
  qty_drift=1, equity_within_tolerance=False).
- `logs/momentum_2026-06-01.log` (D231 every 30s; D222 at 16:00:31; D90 carry; D242 STG).
- `docs/research-log/operator_log_2026-06-01.md` (the "0 incidents / score 25" false negative).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
