# D218: Premise Verification — Items 2-6

**Date:** 2026-04-11
**Purpose:** Verify each efficiency sweep claim against actual code before implementing.
**Lesson from Item 1:** The architecture document claimed 3 zero-weight agents wasting $360/month. Only 2 were zero-weight, and the skip logic already existed. Verify before implementing.

## Reusable Verification Protocol

Before implementing any workstream based on findings from an architecture document, audit, or post-mortem:

1. List each finding the workstream depends on.
2. Verify each finding against current code — read the actual lines, don't trust the summary.
3. Document the verification result: confirmed, partially confirmed, or false.
4. Only implement findings that survive verification.
5. Correct the source document in place (strikethrough + edit note) for findings that were wrong.

This protocol saved ~3 hours on D218 by preventing implementation of 3 items that were already solved or never existed.

**Section 14 had a 50% false-positive rate (3/6 claims inaccurate). Other audit sections should be verified similarly before implementation.**

---

## Item 2: Reduce ensemble for low-weight agents

**Claim:** "TechnicalAgent (weight=0.05) runs 3x ensemble calls."

**Verification:** **FALSE.** TechnicalAgent is NOT wrapped in EnsembleWrapper. The ensemble wrapping at orchestrator.py lines 262-281 explicitly lists 4 agents: news, fundamental, institutional, deep_search. Technical is excluded — it was deliberately kept out of ensemble because it's a deterministic-style agent. It makes exactly 1 LLM call per evaluation.

**Decision:** **SKIP.** The premise is wrong. Technical already runs 1 call. No savings possible. No code change needed.

---

## Item 3: Extract hardcoded values to config

**Claim:** "10+ hardcoded values should live in config."

**Verification:** **TRUE.** All 9 checked values are confirmed hardcoded:

| Value | File | Line | Hardcoded? | Current Value |
|-------|------|------|-----------|---------------|
| Dashboard update interval | main.py | 1071 | YES | 30 (seconds) |
| Health server port | main.py | 945 | YES | 9091 |
| Metrics server port | main.py | 1028 | YES | 9090 |
| Portfolio max sector positions | main.py | 841 | YES | 4 |
| Portfolio max heat pct | main.py | 842 | YES | 40.0 |
| Scan interval | main.py | 111 | YES | 30 (seconds) |
| Eval batch timeout | orchestrator.py | 1626 | YES | 120.0 |
| Debate gather timeout | debate_engine.py | 146 | YES | 60.0 |
| Smart exit close timeout | main.py | 5183 | YES | 15.0 |
| Phase 4 close timeout | main.py | 6189 | YES | 30.0 |
| Heartbeat queue size | main.py | 95 | YES | 1000 |

**Decision:** **PROCEED.** All values confirmed as hardcoded. Extract to config with identical defaults.

---

## Item 4: Batch Alpaca snapshot calls in Phase 3

**Claim:** "Phase 3 fetches snapshots individually per position."

**Verification:** **FALSE.** Phase 3 already uses batch snapshot calls. All 4 `get_snapshots()` calls in Phase 3 pass a list of tickers, not individual symbols:

| Line | Call | Batch? |
|------|------|--------|
| 3670 | `get_snapshots(_eod_tickers)` — list of all position tickers | YES |
| 4335 | `get_snapshots(_d78_position_tickers)` — `[p.ticker for p in positions]` | YES |
| 4347 | `get_snapshots(_d170_missing)` — list of missing D170 tickers | YES |
| 4871 | `get_snapshots(_d170_watching)` — set of watching tickers | YES |

No individual per-position snapshot calls exist in Phase 3.

**Decision:** **SKIP.** The optimization already exists. No code change needed.

---

## Item 5: Parallelize Phase 3 position checks

**Claim:** "Trailing stop, exit intelligence, and tranche monitoring run sequentially per position."

**Verification:** **TRUE.** Phase 3 has 6 sequential `for pos in positions:` loops, each with I/O-bound await calls:

| Loop | Line | Key Await Calls | Parallelizable? |
|------|------|-----------------|----------------|
| Stop-out detection | 4030 | cancel_order, close_position, close_with_attribution, resubmit | COMPLEX — mutates shared state |
| D63 Trailing stop | 4509 | stop_resubmitter.resubmit() | MAYBE — resubmit is idempotent per ticker |
| D164 Early profit | 4580 | client.submit_order() | MAYBE — order submission is independent |
| D165 Tranche profit | 4703 | submit_order, resubmit, cancel, close_with_attribution | COMPLEX — multi-step with state |
| D163 Software trailing | 4919 | close_position, submit_market_order, cancel, close_with_attribution | COMPLEX — close operations with cascading state |
| D78 Smart exit | 5075+ | evaluate_positions (sync compute), close_position | PARTIALLY — compute is parallelizable, close is not |

**Complication:** Most loops contain operations that mutate shared state (position_manager, stop_resubmitter, tranche_monitor). Parallelizing requires the "compute in parallel, mutate serially" pattern described in the prompt. This is a non-trivial refactor.

**Decision:** **PROCEED WITH CAUTION.** The claim is valid — these ARE sequential. But the parallelization scope is larger than a surgical fix. The snapshot batching (Item 4) was the easy win and it's already done. The remaining gains require careful extraction of the compute-vs-mutate boundary for each loop. Flag as a medium-effort item.

---

## Item 6: Type the orchestrator constructor

**Claim:** "6 parameters typed as Any | None."

**Verification:** **MOSTLY TRUE — actually 7, not 6.**

| Parameter | Current Type | Line |
|-----------|-------------|------|
| settings | Settings | 92 |
| websocket_client | Any \| None | 93 |
| sec_client | Any \| None | 94 |
| prompt_arena | Any \| None | 95 |
| options_provider | Any \| None | 96 |
| data_client | Any \| None | 97 |
| premarket_atr | dict[str, float] \| None | 98 |
| trade_tracker | Any \| None | 99 |
| position_manager | Any \| None | 100 |

7 parameters typed as `Any | None`, 1 as `dict[str, float] | None`, 1 strongly typed (`Settings`).

**Decision:** **PROCEED.** Claim is accurate (underestimated by 1). Type these with concrete types or Protocols.

---

## Summary

| Item | Claim | Verdict | Decision |
|------|-------|---------|----------|
| 1 | 3 zero-weight agents waste $360/month | FALSE (2 agents, skip already existed) | DONE — shipped visibility improvement |
| 2 | Technical runs 3x ensemble | FALSE (1 call, not wrapped) | **SKIP** |
| 3 | 10+ hardcoded values | TRUE (9 confirmed) | **PROCEED** |
| 4 | Individual snapshot calls | FALSE (already batched) | **SKIP** |
| 5 | Sequential position processing | TRUE (6 sequential loops) | **PROCEED WITH CAUTION** |
| 6 | 6 Any\|None constructor params | TRUE (actually 7) | **PROCEED** |

**Items that survive verification:** 3, 5, 6
**Items already solved or false premise:** 1, 2, 4
**Score: 3 of 6 claims were accurate.** The architecture audit had a 50% false-positive rate on efficiency findings.
