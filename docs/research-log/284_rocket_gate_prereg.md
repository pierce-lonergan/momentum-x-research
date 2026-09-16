# 284 — THE ROCKET-GATE STAGE-2 PRE-REGISTRATION (frozen; forward-only)

**Author**: Claude (Fable 5, workstream B of the doc-283 build wave) | **Date**: 2026-07-05 (frozen at commit time) | **Class**: PREREG (no live change; a measurement instrument + its acceptance contract) | **Parent**: doc 283 §5 Step 3b (chip `task_d413c22e`) | **Lineage**: doc 264 (the conditional gate, unfalsified) → doc 280 (blanket posture flip NO-SHIP; the arm definitions + instrument) → doc 281 §2 #4 (the cell graded D+: threshold-mined, frozen-OOS fails, book-negative deployable estimand — verdict: **freeze a prereg, evaluate FORWARD ONLY**).

> This document freezes, before the first forward session, every number the rocket-gate Stage-2 test is allowed to use. The iron rule applies: **a gate that fails is a success** — it closes the door cheaply. **Re-tuning any frozen parameter = automatic kill of the gate.** There is no "we adjusted the threshold and now it passes." A different gate is a different pre-registration with a forward window that starts after ITS freeze date.

## §1 — The hypothesis under test

In rocket-rich conditions, holding the entry to the close beats the production bar1 exit. Specifically: on sessions where the bot's own BUY candidates include tickets with **entry-time rvol > 100 scanned during the 9:00–9:59 ET hour**, the hold-to-close posture applied to exactly those tickets has positive expected per-session dollar value versus the bar1_legacy exit. Everything upstream (doc 280/281) says the *blanket* version of this is null-to-negative and the *retrospective* gated version is mined; this prereg exists because the conditional claim (doc 264) was never falsified forward.

## §2 — The frozen gate and posture

All constants live in `scripts/_doc284_rocket_gate_ledger.py` and are tripwired by `tests/unit/test_rocket_gate_ledger.py` (a re-tune breaks the test loudly).

| Parameter | Frozen value | Notes |
|---|---|---|
| Candidate universe | `final_action == "BUY"` rows of `data/features/features_{date}.jsonl` | the bot's own entry list — the deployable population |
| Unit of account | the features **ROW** (doc-280 convention) | a ticker scanned N times in hour 9 counts N times; no position cap, no dedup — this is a per-ticket arm instrument, NOT a book simulation |
| Same-session guard | first local bar within **390 min** of entry, else the ticket is **UNMEASURED** | a warehouse hole hands back next-session bars (observed: 2026-05-29, 2026-06-30); holes must never masquerade as $0 outcomes |
| Gate: rvol | entry-time `rvol` **> 100** (strict) | from the features row, i.e. what the live scanner knew at entry time |
| Gate: hour | entry timestamp hour **== 9 ET** | features timestamps are UTC (13:xx UTC = 9:xx EDT); converted via `America/New_York` so EST sessions stay correct |
| Missing fields | row **excluded** | never guessed |
| Posture | hold-to-close, **15% disaster stop**, **NEVER overnight** | the shippable form |
| Pricing | doc-280 arm machinery: validated arena fill model entry (marketable limit, 2-min window, submit +45 s), LOCAL minute bars, **0.5% hostile one-way exit haircut** | `_doc280_posture_backtest._load_local_bars` / `_arm_returns`, reused verbatim |
| Unit | per-SESSION dollar delta = Σ over gated filled tickets of **(B_hold_close − A_bar1) × $8,000** | the doc-280 arm definitions; primary acceptance metric |
| Secondary (recorded, NO acceptance role) | Σ (D_hold_stop15 − A_bar1) × $8,000 | detects whether the disaster stop binds; any post-pass ship decision consults it, the acceptance test does not |
| Determinism | fill RNG seeded `"doc284:{date}"` per session | recompute-stable ledger lines |
| Unfillable gated tickets | contribute **$0** and the session stays in the sample | the deployable estimand includes days the model can't fill |

## §3 — The instrument

`scripts/_doc284_rocket_gate_ledger.py` appends one JSON line per session to `data/reports/rocket_gate_ledger.jsonl`: `{date, n_gated, n_filled, delta_hold_vs_bar1_usd, d_stop15_vs_bar1_usd, tickers, no_bar_data, retro, ...}`. Nightly append is wired into `scripts/post_close_scorecard.py` as a best-effort block (same pattern as the doc-282 posture scoreboard: subprocess, never fatal). The ledger is **append-only**; an existing date is never silently recomputed (`--force` replaces loudly and is for retro corrections only — using it on a forward row outside a documented data-repair is a protocol violation).

## §4 — The frozen acceptance test

- **Forward window**: sessions dated **≥ 2026-07-06**. **All history before 2026-07-06 is RETROSPECTIVE SEEDING** (`retro: true`), reported for context and **EXCLUDED from the acceptance test** — those are the very sessions the thresholds were mined on (doc 281), and they are not evidence.
- **Acceptance sample**: forward sessions with `n_gated ≥ 1` and same-session local bar data. Zero-fill gated sessions stay in at $0 (the deployable estimand includes unfillable days). Sessions flagged `no_bar_data` (warehouse hole / non-session scan) are excluded but must be **counted and reported** as coverage holes — denominator honesty (doc 278 rule).
- **Coverage-integrity clause** (the doc-274 window-integrity guard, instantiated): if unmeasured gated tickets exceed **20%** of gated tickets across the forward window, the test **cannot PASS** (`BLOCKED-COVERAGE`) until the warehouse is repaired and the affected rows recomputed via a documented `--force` data-repair. A PASS on a holey window is not a PASS.
- **Evaluation point**: at **n ≥ 30** gated forward sessions.
- **PASS requires BOTH**:
  1. **Day-blocked bootstrap** (resample unit = session; B = 10,000; seed = 284; percentile method) **95% CI of the mean per-session delta has lower bound > 0**;
  2. **Both chronological halves positive**: split the gated forward sessions in date order into first ⌈n/2⌉ and the rest; total delta > $0 in each half.
- **KILL horizon**: not PASSED by **n = 60** gated forward sessions → the gate is **DEAD**. No extension, no re-tune, no "one more month."
- **Automatic-kill clauses**: any change to any §2/§4 constant; any re-derivation of the gate on data that includes the forward window; any swap of the acceptance metric to the secondary D-arm after seeing forward data.
- Machine-checkable: `python scripts/_doc284_rocket_gate_ledger.py --acceptance` emits the verdict from the ledger alone.

## §5 — Retrospective seeding (context, NOT evidence — and loudly not money)

Seeded over all available history (features 2026-04-14 → 2026-07-05, 56 session rows, all `retro: true`):

- **39 gated measurable retro sessions** (+2 warehouse-hole sessions 2026-05-29/2026-06-30 with 82 unmeasured gated tickets, flagged `no_bar_data`; +15 sessions with zero gated candidates, incl. 3 weekend scan files). 1,380 filled tickets.
- **Retro total Δ(hold_close − bar1) = +$854,578** at $8K/ticket row-level; mean **+$21,912/gated session**, median +$11,807, positive sessions **31/39**. *(Validation correction 2026-07-05, pre-window: the median previously printed here, +$10,839, was the stale pre-hardening 41-session value with the two hole sessions booked as $0; §2/§4 frozen parameters untouched.)* Secondary D-arm (with the 15% stop): +$678,932 — the stop binding costs $175,647 of the pure-hold delta.
- **Tail-driven, as expected of rockets**: top session (2026-06-04, SDOT ~$5.95 → 11.37, tape-verified low 5.88/high 14.62) = 15.4% of the total; top 3 sessions = 37.5%.
- **Gate thinness, honestly**: NOT thin at the ticket level — ~30% of all BUY rows gate in (~35 gated tickets per gated session; 39 of 54 trading sessions gated). The gate's selectivity is the *hour × rvol regime*, not rarity of tickets. Duplicate rows per ticker inflate the row count (SDOT alone contributed ≥6 rows on 06-04).
- **Why this number is not money**: it is the row-level mined ceiling measured by the arm instrument — no position cap (live book caps at 8), no capacity, duplicate rows compound single names, model fills, and these are the exact sessions the thresholds were mined on. Doc-281's deployable estimand of this same cell was **book-negative**. The retro column exists so the forward column has something to be compared against — nothing else.
- Regime shape matches doc-280: Apr/May-heavy positive, June mixed (−$31K on 06-17), early July negative (−$1.4K, −$6.0K). The forward window opens into the regime that has been *off* — if the gate passes anyway, that is real information.

## §6 — Stated biases (declared now, so they cannot become excuses later)

Entry fills are model fills (validated arena model, doc 270/280), not broker fills. `rvol` is whatever the live scanner wrote in the features row — deployable-consistent by construction, but not an independent measurement. $8K/ticket is fixed (no compounding, no sizing interaction). Long-only; no borrow anywhere in the path. The candidate universe inherits the scanner's regime footprint: a scanner change alters the gated population — a **material scanner change during the forward window must be logged in the ledger doc but does NOT reset the window** (frozen now to prevent discretionary restarts).

Also declared: the **row-level unit** means one runaway ticker scanned repeatedly can dominate a session's delta (SDOT 06-04); the day-blocked bootstrap absorbs the intra-session correlation, but a PASS driven by two names across 30 sessions will be visible in the ledger's `tickers` field — read it before believing a pass.

**Bottom line**: doors get closed by numbers, not by fatigue. By roughly session 30–60 of gated forward data, this cell either shows a CI-positive, both-halves-positive dollar edge under the exact frozen definitions above — or it dies, and doc 264's last unfalsified conditional claim dies with it.
