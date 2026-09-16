# 284 — FRONTIER EXECUTION: the doc-283 path, built, validated, and one door already tombstoned

**Author**: Claude (Fable 5, ultracode; 3 builders + 3 adversarial validators, wf_f9b12f22, ~2.5M tokens) | **Date**: 2026-07-05 | **Class**: RESEARCH TOOLING + ONE GATE VERDICT (no trading-path change; the doc-282 bleed-cut rides Monday untouched) | **Mandate (Pierce)**: "Your plan is green lit… implement, and double check and validate. Full send."

> All three buildable workstreams of the doc-283 path were built tonight, each adversarially validated before being believed — and the validators caught and fixed **two GATE-CORRUPTING bugs** before either could poison a verdict. The headline: **Step 1 ran to completion and the short door is TOMBSTONED** — measured on our own 2,167-ticket ledger with real IBKR borrow data at full census, fillable-net is **−1.205%/ticket (CI95 [−1.571, −0.822], excludes zero on the negative side)**, landing inside doc-283's predicted band at the *optimistic* bound. "The fee is the fade" is no longer an inference; it is a measurement (GLXG's borrow repriced 35%→776% APR exactly as it entered our BUY ledger). Per the iron rule, this is a success: a seductive door killed for $0 before $30K and weeks of engineering. The other two instruments are live and collecting: the rocket-gate forward ledger (frozen prereg, window opens Monday) and the Kalshi zero-capital shadow (1,206 markets snapshotted, 15 real LLM forecasts at $0.003, daily task scheduled).

## §1 — Workstream A: PRICE THE BORROW → **GATE FAILED — TOMBSTONE** (the decider, decided)

`scripts/_doc284_borrow_pricer.py` + `data/research/doc284/borrow_pricing.json` (+196-symbol cache making reruns free).

- **Ledger**: 2,167 BUY tickets = 275 ticker-days / 186 tickers over 20 dates (2026-06-01→07-02); pooled-gross self-check +1.334% (matches the +1.33% headline). **Full census — the sampling clause went unused; 196/196 symbols fetched, 0 failures.**
- **Fillability**: 68.9% of tickets had an IBKR borrow record that day (clears the 30% bar); 28.4% absent from the shortable list — matching the live 0-for-94 prior.
- **Fees on the fillable**: median **103% APR** (0.41%/day), p75 300%, p95 **812%**, max 971%.
- **NET/ticket** (gross − fee/252 − 0.75% friction − 0.90% overshoot, the doc-283 pre-registered stack): **−1.205%**, day-blocked bootstrap CI95 **[−1.571, −0.822]**. Per-week: −1.76/−1.16/−1.72/**+0.21**/−1.63 — only the freak +2.48%-gross week netted positive.
- **A fortiori**: IBKR APR/252 *underprices* specialist day-of locate fees (1–3% of notional), so the true net is worse; the ±3-day lenient sensitivity raises fillability to 94% but makes net *more* negative (−1.304%). The tombstone over-survives.
- **Validator (SOUND-WITH-FIXES)**: independently recomputed net + CI from raw rows; hardened the cache layer (stale ERROR envelopes could bypass the coverage guard); confirmed the verdict string matches the pre-registered thresholds verbatim.

**Consequences**: doc-283 Step 2 (TradeZero $2.5K quote-shadow) is **cancelled — unnecessary**; the short door closes permanently; the frontier center falls to ~0.07%/day pending the two live collections; the program never re-proposes shorting this universe (the doc-283 stop-believing list now carries a measured number).

## §2 — Workstream B: the rocket-gate Stage-2 forward ledger → PENDING-COLLECTION (window opens Monday)

- **Frozen prereg** ([284_rocket_gate_prereg.md](284_rocket_gate_prereg.md), its own timestamped commit `7f003f6` *before* the window): gate = entry-time rvol>100 AND hour-9-ET (America/New_York from UTC), BUY rows only; posture = hold-to-close + 15% disaster stop, never overnight; unit = per-session $ delta (doc-280 arms, $8K/ticket); acceptance = **forward-only ≥ 2026-07-06**, day-blocked CI-lo > 0 AND both chronological halves positive at n≥30 gated sessions; **DEAD at n=60; any re-tune = automatic kill**; >20% unmeasured gated tickets → BLOCKED-COVERAGE.
- **Instrument**: `scripts/_doc284_rocket_gate_ledger.py` (reuses the doc-280 arena fill machinery verbatim; per-session deterministic seeds; append-only) + nightly best-effort wiring in `post_close_scorecard.py` (the doc-282 pattern). Seeded 56 retro sessions (39 gated-measurable, retro median +$11,807/session — *retrospective, excluded from acceptance by design*). 28 tests green.
- **Validator (SOUND-WITH-FIXES)**: caught a **GATE-CORRUPTING coverage bypass** — `acceptance()` dropped whole-session warehouse holes *before* computing the unmeasured fraction, so missing data could have silently passed the coverage clause; fixed + 9 new acceptance tests. Also froze `SUBMIT_OFF_MIN`/`FILL_WINDOW_MIN` as tripwired constants and corrected a stale median in the prereg with a dated note.

## §3 — Workstream C: the Kalshi zero-capital shadow → PENDING-COLLECTION (day 0 of 60)

- **Rig**: `scripts/kalshi_shadow_doc284.py` (snapshot / forecast / score / plan) + `scripts/kalshi_shadow_daily.cmd` + scheduled task **MomentumX\KalshiShadowDoc284** (daily 18:00; first run Mon 7/6; delete = one schtasks line). **No position is ever taken.**
- **Ran tonight, real**: snapshot = 34 pages, 6,650 open events → **1,206 kept markets** (Economics 572, Climate 256, Politics 216, SciTech 115; <24h-to-close and one-sided quotes excluded; per-event volume cap). Forecast = 12/12 LLM probabilities parsed, **$0.0026 total cost** (60-day collection ≈ $0.34). Score = handles the empty-resolution day gracefully.
- **Frozen gate** (in the script header, enforced in `score`): ≥200 resolved forecasts, fee-adjusted divergence-rule P&L (buy at ask/bid, taker fees) day-blocked CI > 0 **and** Brier(LLM) < Brier(market) → propose the $5–20K side-pocket; else CLOSED.
- **Validator (SOUND-WITH-FIXES)**: caught a **GATE-CORRUPTING fee error** — the sim used the fractional 0.07·p·(1−p) formula, but Kalshi's published schedule (eff. 2026-06-29) **rounds up to the next cent**; fixed to ceil (also the conservative direction). Added a 12h snapshot-staleness lookahead guard, un-tombstoned transiently-empty resolutions, 39 adversarial tests (13 in-repo, all green after syncing three stale fee expectations to the ceil behavior).

## §4 — Scoreboard of the doc-283 path after tonight

| Step | Status | Verdict/next |
|---|---|---|
| 0 — collector fix | ✅ done (doc 282) | red-flag evidence accruing |
| **1 — price the borrow** | ✅ **RAN — GATE FAILED → TOMBSTONE** | short door closed permanently |
| 2 — TradeZero quote-shadow | ❌ **cancelled** | made unnecessary by Step 1 |
| 3a — live short pilot | ❌ dead with the door | — |
| 3b — rocket-gate Stage-2 | 🕐 collecting (forward window opens Mon) | verdict at n≥30 gated sessions |
| 4 — Kalshi shadow | 🕐 collecting (day 0/60, task scheduled) | verdict at ≥200 resolved |
| Standing — bleed-cut | ✅ live for Monday | sizing restores on scoreboard n≥30 |

**The honest frontier after tonight**: center **~0.07%/day** (bleed-cut regime) with two live options that can raise it — the rocket gate (episodic, regime-dependent) and the Kalshi side-pocket (uncorrelated) — each with a frozen gate that decides *for* us. Both collections cost ≈ $0/day to run. **Nothing was bent; one door died cheaply; two doors are being measured properly.** That is what chasing it with vengeance looks like under the iron rule.

**Operator-owned residue**: nothing required this week. If the Kalshi gate someday passes: account + API keys + $5–20K. If the rocket gate passes: a flag-gated ship proposal comes to you with the evidence attached.
