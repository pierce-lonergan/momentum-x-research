# D218: Efficiency Sweep

**Status:** IN PROGRESS
**Branch:** develop

---

## Item 1: Disable Zero-Weight Agent Ensemble Wrapping

**Date:** 2026-04-11
**Files changed:** src/core/orchestrator.py

### What shipped

The zero-weight agent dispatch skip logic already existed at orchestrator.py lines 2118-2124 (D100). Coroutines for agents with weight=0 are created then immediately closed without awaiting — no LLM calls fire.

**New in D218:** The ensemble wrapping step (lines 249-287) now also checks agent weight before wrapping. Previously, institutional_agent and deep_search_agent were wrapped in EnsembleWrapper (creating 3x call infrastructure) even though the dispatch skip would prevent execution. Now:

- Agents with weight > 0: wrapped in EnsembleWrapper (3x calls, quorum=2)
- Agents with weight = 0: NOT wrapped — no EnsembleWrapper object created

Added two startup log lines for full visibility:
```
D202: Ensemble enabled — 3x calls (quorum=2). Wrapped: [news, fundamental]. Skipped (weight=0): [institutional, deep_search]
D218 AGENT DISPATCH: active=[news(0.55), technical(0.05), fundamental(0.15), risk(0.25), manipulation(1.00)] skipped=[institutional, deep_search]
```

### Architecture document correction

Section 14.1 of system_architecture_master.md stated "3 zero-weight agents" (FundamentalAgent, InstitutionalAgent, DeepSearchAgent). Investigation revealed:

- FundamentalAgent maps to `float_structure` weight = 0.15 (NOT zero — it contributes)
- InstitutionalAgent weight = 0.0 (zero — correctly skipped)
- DeepSearchAgent weight = 0.0 (zero — correctly skipped)

**Only 2 agents are zero-weight, not 3.** The $360/month estimate was overstated. Correct savings: ~$240/month (2 agents × 3 ensemble calls × 10 candidates/cycle × ~$0.01/call × 20 cycles/day × 20 days/month).

### Verification

- Dry-run: startup log shows `skipped=[institutional, deep_search]` ✅
- Compile: `py_compile` passes ✅
- Regression: 33 tests pass (health server + signal history + feature logger) ✅
- No behavior change: active agents produce identical signals ✅

### Measured savings

| Metric | Before D218 | After D218 |
|--------|-------------|------------|
| Ensemble objects created at startup | 4 (news, fund, inst, deep) | 2 (news, fund) |
| LLM calls per candidate (zero-weight agents) | 0 (already skipped at dispatch) | 0 (now also skipped at wrapping) |
| Startup log visibility | Generic "Ensemble enabled" | Explicit active/skipped agent list |

**Net new LLM cost savings: $0/month** — the dispatch skip was already preventing calls. The improvement is structural cleanliness (no unnecessary EnsembleWrapper objects) and operational visibility (startup log shows exactly which agents run).

---

## Item 2: Reduce Ensemble for Low-Weight Agents — SKIPPED

**Premise verification result:** FALSE. TechnicalAgent is NOT wrapped in EnsembleWrapper. It was deliberately excluded from ensemble wrapping and makes exactly 1 LLM call per evaluation. No reduction possible. See d218_premise_verification.md.

---

## Item 3: Extract Hardcoded Values to Config

**Date:** 2026-04-11
**Files changed:** config/settings.py, main.py, src/core/orchestrator.py, src/agents/debate_engine.py

### What shipped

Created two new config classes:
- `ServerConfig` (env_prefix="SERVER_"): health_port (9091), metrics_port (9090)
- `OperationalConfig` (env_prefix="OPS_"): 9 operational parameters

Replaced all 9 verified hardcoded values at their call sites with config lookups. Every default matches the previously-hardcoded value exactly — pure extraction, zero behavior change.

| Value | Config Path | Default | File:Line Changed |
|-------|-------------|---------|-------------------|
| Health server port | settings.server.health_port | 9091 | main.py:945 |
| Metrics server port | settings.server.metrics_port | 9090 | main.py:1028 |
| Dashboard interval | settings.ops.dashboard_interval_seconds | 30 | main.py:1071 |
| Max sector positions | settings.ops.max_sector_positions | 4 | main.py:841 |
| Max portfolio heat | settings.ops.max_portfolio_heat_pct | 40.0 | main.py:842 |
| Eval batch timeout | settings.ops.eval_batch_timeout_seconds | 120.0 | orchestrator.py:1626 |
| Debate gather timeout | settings.ops.debate_gather_timeout_seconds | 60.0 | debate_engine.py:146 |
| Smart exit close timeout | settings.ops.smart_exit_close_timeout_seconds | 15.0 | main.py:5183 |
| Phase 4 close timeout | settings.ops.eod_close_timeout_seconds | 30.0 | main.py:6190 |

**Not extracted (deferred):**
- Log queue size (1000): `setup_logging()` runs before settings load. Would require init reordering.
- Scan CLI interval (30): argparse default for standalone `scan` command. Rarely used mode.

### Verification

- All 4 files compile clean ✅
- All 9 config values load with correct defaults ✅
- `grep` confirms hardcoded values removed from call sites ✅
- Dry-run: system starts, ports match, intervals match ✅
- 33 regression tests pass ✅

---

## Item 4: Batch Alpaca Snapshot Calls — SKIPPED

**Premise verification result:** FALSE. Phase 3 already uses batch `get_snapshots()` calls at lines 3670, 4335, 4347, 4871. All pass lists of tickers, not individual symbols. No optimization needed. See d218_premise_verification.md.

---

## Items 5-6: PENDING

| Item | Description | Status |
|------|-------------|--------|
| 5 | Parallelize Phase 3 position checks | PENDING (verified valid, needs 5-question audit per loop) |
| 6 | Type the orchestrator constructor | PENDING (verified: 7 Any\|None params) |

---

## Deferred (noticed during sweep, not addressed)

1. **FundamentalAgent weight label mismatch**: Config field is `float_structure` but the agent is called `fundamental_agent`. The naming is confusing — the fundamental agent evaluates float structure data. Consider renaming the config field to `fundamental` or the agent to `FloatStructureAgent` for clarity. (orchestrator.py:2110)

2. **ManipulationClassifier not in ensemble**: manipulation_classifier runs as a single LLM call (no ensemble wrapping) despite being a Tier 1 reasoning task. This is probably intentional (it gates entry parameters, not MFCS) but should be documented. (orchestrator.py:294)

3. **Ensemble wrapping happens at `__init__` time**: The weight-based skip uses settings from construction time. If weights change via .env mid-session (hot reload), the ensemble wrapping won't update. This is acceptable for current architecture (single session = single config) but would need fixing if hot-reload is ever added.
