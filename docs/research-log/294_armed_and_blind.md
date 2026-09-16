# 294 — ARMED AND BLIND: the SEVP kill test and the Stage-3 accept/kill, frozen, built, and refusing to peek

**Author**: Claude (Fable 5, ultracode; prereg frozen pre-execution `5c2b0e7` sha256 `ae62ea59…f098fe`) | **Date**: 2026-07-12 | **Class**: PREREG FREEZE + PIPELINE ARM (no gate unblinded; plumbing metrics only) | **Mandate (Pierce)**: "Lets do it. Full send" — on the doc-293 queue: SEVP + the Stage-3 dated kill.

> **First line: both BUILD-NEXT instruments are frozen, built end-to-end, validated on live plumbing, and armed behind pre-unblinding gates that refuse to compute a single P&L or QLIKE number until coverage/power certifies.** No gate was read this session — by design. The distance-to-target moves when the gates open; what this session bought is that they open on data already flowing at $0, with the verdict maps written before anyone could see the answer. **Unblind ETA: ~2 nights of collection after you register the nightly task.**

## §1 — The frozen preregs (`5c2b0e7`, sha256 `ae62ea59…`)

**A. SEVP** (the only doc-293 family clearing the TARGET filter at its own pessimistic line): short ATM straddle, T−1 close → T+1 close around quarterly earnings. Economics on option **prices** (not IV), two frozen cost lines (5%/10% of premium round-trip). Gates: **G1** unconditional carry (mean>0, CI95 excl 0 at both lines, median>0, both halves replicate) — **G2** sub-gate (does the doc-291 RV forecast add?) — **fat-tail honesty rule** (one event >50% of cumulative net caps the verdict at FRAGILE-PASS). **Pre-unblinding: N≥300 two-leg-covered events. Death: 2026-09-01.** Verdict map frozen: pass→forward shadow ledger (n≥60) before any Pierce-gated paper trade; fail at the 5% line→SEVP-DEAD, ledger.

**B. Stage-3 dated accept/kill** (from the doc-292 draft, sources filled): challenger vs GBM[HAR+calibrated-IV] on the collected single-name IV panel; G1 QLIKE ≥2% (block-42, pooled+halves), G2 HLN λ. **Pre-unblinding power gate: null-resolution ≤4% of baseline QLIKE, computed sign-randomized — direction unreadable. Death: 2026-08-15** → UNRESOLVED-UNDERPOWERED goes to you with the resolution number attached.

## §2 — Data verified, pipelines built, plumbing green

- **Earnings dates at $0**: Finnhub's free tier serves no history (0 rows on known windows — probed); **yfinance does** (timestamped, 12 quarters, BMO/AMC resolve naturally) with Polygon filing dates as the cross-check. 43 events cached for the first 10 names.
- **The collector now stores option prices** alongside IV (SEVP's raw material); contracts pulled before this change are being back-filled by re-pull (399 price rows landed mid-session and climbing).
- **`_doc294_sevp_pipeline.py`**: events → two-leg join → coverage counter. Live: **3/300 events covered** (AAPL, from partial backfill), status PENDING-COLLECTION, `--run` refuses with the prereg hash cited.
- **`_doc294_stage3_runner.py`**: panel assembly (21-45d nearest-30 IV, per-name rolling MZ calibration on fully-realized rows) → **power NOT-COMPUTABLE (2 of 20 minimum names)** → stays blind, `--run` refuses. Correct.

## §3 — What opens the gates (all $0, no decisions pending except yours)

| gate | needs | ETA at current drip |
|---|---|---|
| SEVP N≥300 | ~25-30 collected names × ~11 covered events each | **~2 nights** of the registered nightly task (1,150 calls/night ≈ 17 names) |
| Stage-3 power | ≥20 names ≥120 IV-days + resolution ≤4% | **~1-2 nights** (same drip) |

In-session tranches collected AAPL (complete, 452d) + MSFT (247d); the nightly task is the vehicle for the rest — **its registration is your one-liner** (header of `scripts/iv_collector_nightly.cmd`).

## §4 — Multiplicity + housekeeping

Tests run this session: **0 gate statistics** (by design); plumbing metrics only (2 event-source probes, coverage counts, panel assembly). Preregs add: SEVP 2 gates + 1 sub-gate × 2 cost lines; Stage-3 2 gates + 1 power pre-gate — all counted in the ATTEMPTS_LEDGER when they run. Housekeeping: rocket-gate n=3/30 (advances tomorrow), forward RV ledger forward-collection starts tomorrow, targeted tests green, lineage `545acce`→`03b0432`→`e96c7d3`→`5c2b0e7`.

## §5 — Pierce-action list (two clicks and the machine runs itself)

1. **`_doc288_apply_catchup_triggers.ps1` elevated — FOURTH session pending.** The relaunch fix isn't live until this runs.
2. **Register the IV-collector nightly task** (one-liner in `scripts/iv_collector_nightly.cmd`; no elevation). This is now the binding constraint on BOTH armed gates.
3. Standing items unchanged: kill/continue ratification; Stage-1 config-diff HELD; paid-tier-for-speed optional.

**Closing distance statement**: unchanged this session — best measured = 27% of requirement, certified = 0 — *because nothing was measured*: the session converted the two best candidates from ideas into armed instruments that cannot be peeked at, wired to free data that accumulates nightly. The next movement of the distance number happens when the gates open, and the answer is already committed to be reported whichever way it falls.
