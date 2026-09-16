# 33 — QMP Signing Migration: Module + EOD Wire-In Shipped

**Status:** module + tests + EOD wire-in shipped 2026-04-25 morning. Retrospective corpus delta deferred (see §3 — depends on Phase 0 tick capture).
**Predecessors:** `18_slippage_methodology_application_v2.md` §1.6, `compass_artifact_wf-616ec49c-…md`, `26_d_code_registry.md` D260 reservation.
**Reference paper:** Barber–Huang–Jorion–Odean–Schwarz 2024 (JF), "A (Sub)penny for Your Thoughts."

---

## §0 — TL;DR

Three deliverables shipped:

1. **`src/analysis/qmp_signing.py`** — canonical QMP rule (above mid → +1; below mid → −1; at mid → Lee-Ready tick fallback) plus a pure-function mirror of the existing midpoint baseline (`midpoint_baseline_sign`) plus a corpus-level disagreement audit (`signing_disagreement` returning `DisagreementSummary` + boolean `fires_d260`).

2. **`tests/unit/test_qmp_signing.py`** — 25 tests covering every branch + edge case + the EOD wrapper. 25/25 pass.

3. **`src/monitoring/eod_recon.py:run_eod_signing_audit()`** — D260 SIGNING_DISAGREEMENT wire-in. Imports qmp_signing, runs the audit, logs INFO on rescue-only paths and WARNING with the literal `D260` marker on true direction-flip disagreement above threshold (default 10%).

What's **not** in this ship and why: the retrospective delta over the existing journal corpus. That's a §3 deferral.

---

## §1 — Why "BJZZ → QMP migration" is partly a misnomer in this repo

The user's plan called this a "BJZZ → QMP migration." Inventory revealed the existing implementation in `src/data/order_flow.py:_classify_trade_direction()` is **not** BJZZ — it's a **quote-rule + midpoint hybrid** that returns UNKNOWN at exact midpoint (no Lee-Ready fallback). So this is more accurately a "midpoint-hybrid → QMP migration."

The functional difference, in one line:

> **The legacy classifier loses every trade that prints exactly at midpoint. QMP recovers them via Lee-Ready tick disambiguation.**

In microcap regimes (the MOMENTUM-X universe), at-midpoint prints are common (penny-tick spreads × hidden mid liquidity). The "rescue rate" — fraction of total trades that QMP signs but the baseline left UNKNOWN — is the headline migration metric.

---

## §2 — Algorithm + edge cases (pinned by the test suite)

| Branch | Condition | Output | Test |
|--------|-----------|--------|------|
| `above_mid` | `trade_price > midpoint` | `+1` | `test_above_midpoint_signs_buy` |
| `below_mid` | `trade_price < midpoint` | `−1` | `test_below_midpoint_signs_sell` |
| `lee_ready_uptick` | `trade_price == midpoint`, `prior > trade` | `+1` | `test_at_midpoint_uptick_signs_buy` |
| `lee_ready_downtick` | `trade_price == midpoint`, `prior < trade` | `−1` | `test_at_midpoint_downtick_signs_sell` |
| `lee_ready_zerotick` | `trade_price == midpoint`, `prior == trade` | `0` | `test_at_midpoint_zerotick_returns_unknown` |
| `at_mid_no_prior` | `trade_price == midpoint`, `prior is None` | `0` | `test_at_midpoint_no_prior_returns_unknown` |
| `locked_or_crossed` | `bid >= ask` | `0` | `test_locked_market_returns_unknown` + `test_crossed_market_returns_unknown` |
| `no_quote` | `bid <= 0 or ask <= 0` | `0` | `test_zero_bid_returns_no_quote` + `test_negative_ask_returns_no_quote` |

Subpenny + far-above-ask cases also pinned (`test_subpenny_above_midpoint`, `test_trade_far_above_ask_signs_buy`).

The `RULE_*` constants are kept as importable strings — they're the canonical signal labels for D260 dashboard groupings + future tick-by-tick dashboards. Renaming requires a coordinated update of any subscriber.

---

## §3 — Retrospective corpus delta — DEFERRED

The user's plan asked for a retrospective run of QMP vs baseline over the existing journal corpus, with the headline number being the delta on capacity (per v2.2 §2.3, "likely shifts upward 15-30%").

**Scope discovery:** the journal corpus (`data/journals/journal_*.jsonl`, sampled `journal_2026-02-10_*.jsonl`) stores **trade-decision entries**, not **raw tick-by-tick trade prints**. Each entry holds the orchestration-level snapshot at decision time (technical indicators, news context, agent signals, fill summary). It does NOT contain the per-millisecond tick tape with bid/ask at every trade-print timestamp that QMP signing operates on.

**Where the right data lives:** in MOMENTUM-X today, raw tick data flows through `main.py`'s `_market_ws_client.on_trade` callback into `_trade_buffers[sym]` — but those are **session-scoped in-memory buffers**, never persisted. Phase 0 (item 16-20 of the user's plan, sequenced AFTER QMP) introduces `child_fill_ticks` as a Pydantic schema with atomic Parquet writes. Phase 0 + ~5 trading days of capture is the prerequisite for a meaningful retrospective.

**Decision:** ship the QMP module + tests + EOD wire-in NOW (this commit). Defer the retrospective until Phase 0 captures one full session of tick data. Document the deferral here so the architecture call has a clean answer when it asks "what's the QMP capacity number?"

The migration is **architecturally complete and operationally inert**: every future tick-by-tick analysis can call `qmp_sign(...)`, the EOD recon will fire D260 the moment the corpus contains tick data, and the legacy `midpoint_baseline_sign` lives as a comparator for the deferred retrospective.

---

## §4 — Wire-in details: D260 SIGNING_DISAGREEMENT

`src/monitoring/eod_recon.py:run_eod_signing_audit(trade_corpus, threshold=0.10)`:

* Returns a dict: `{fires_d260, error, summary: {total_trades, qmp_signed, baseline_signed, disagreed, qmp_rescued, baseline_rescued, disagreement_rate, rescue_rate}}`.
* Never raises — malformed input is logged at ERROR with the raised exception threaded through (silent-handler rule satisfied).
* Three log paths:
  - **D260 WARNING** (literal `D260` marker for grep / dashboards): true direction-flip disagreement above threshold.
  - **INFO with rescue count**: zero true disagreement, but QMP rescued at-midpoint trades the baseline missed.
  - **INFO baseline-parity**: zero disagreement, zero rescue.

The audit is **purely additive** — it does NOT alter live signing behaviour in `src/data/order_flow.py`. The legacy classifier remains the production path until the architecture call decides whether to swap. This shipping order is intentional: prove the new rule's correctness in a low-stakes audit before promoting it past the existing live path.

---

## §5 — Knock-on items captured

* `src/data/order_flow.py:_classify_trade_direction()` — unchanged this ship. Future migration to call `qmp_sign(prior_tick_price=…)` requires threading prior-tick state into `OrderFlowAnalyzer.analyze_flow()`. Captured as a follow-up; not blocking the architecture call.
* Phase 0 `child_fill_ticks` schema must include `bid` + `ask` at trade time + a `prior_tick_price` field per symbol so the retrospective can reconstruct the QMP input cleanly.
* Architecture-call agenda update: capacity number from v2.2 §2.3 is now **annotated** with "QMP rescue-rate measurement deferred to Phase 0 tick capture" — the headline number doesn't shift until that data lands.

---

## §6 — Evidence summary

* Module: 199 LOC (`src/analysis/qmp_signing.py`).
* Tests: 25/25 pass in 0.27s (`tests/unit/test_qmp_signing.py`).
* EOD wire-in: ~80 LOC added to `src/monitoring/eod_recon.py`.
* D260 reservation: confirmed live in `26_d_code_registry.md`, next available D262.
* No changes to live signing behaviour. No new dependencies.

Discovery rate: 0 bugs surfaced (the migration didn't introduce new code paths in the live trading loop). This is a **scope-and-guardrail** ship, not a bug-hunt ship.
