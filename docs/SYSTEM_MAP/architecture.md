# architecture.md — momentum-x layered pipeline

Per-layer responsibilities, inputs/outputs, and which D-codes touched each
layer in the last 90 days. **For per-model details (calibrations, training
sets, model types), see [`models.md`](models.md). For alert + watcher
specifics, see [`monitoring.md`](monitoring.md).**

---

## The 11-layer pipeline

```
RAW UNIVERSE (Alpaca screener + Polygon flat-files)
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  1. PRE-MARKET SCANNER (src/scanners/premarket.py)                   │
│     RULE-BASED · gap_pct, rvol, dollar_volume, ATR ratio             │
│     OUT: CandidateStock                                              │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  2. ENRICHMENT                                                       │
│     float / dilution flags / SEC filings / sector / catalyst class   │
│     Touches: SEC EDGAR (sec_client.py), ticker_details.parquet       │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  3. AGENT LAYER (6 parallel signals, max 30s wall-clock)             │
│     news (LLM 0.30) · technical (rule 0.20) · risk (rule 0.20)       │
│     institutional (LLM 0.10) · fundamental (LLM 0.15) · deep (0.05)  │
│     D-codes: D91 fallback chain · D92 emergency tier ·               │
│              D121 error guards · D126 deterministic mode ·           │
│              D216-D221 catalyst/debate                               │
│     OUT: AgentSignal list                                            │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  4. MFCS — Multi-Factor Composite Score                              │
│     PURE MATH · weighted-linear of 6 AgentSignals                    │
│     D-codes: D101 bipolar range · D116 confidence floor 0.20         │
│     OUT: ScoredCandidate (mfcs ∈ [-1, +1])                           │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  5. DEBATE (conditional — fires if MFCS ≥ 0.60)                      │
│     Bull / Bear / Judge using DeepSeek R1-32B                        │
│     D-codes: D217 timeout · D218 efficiency                          │
│     OUT: DebateResult (divergence ∈ [0,1], position_size modifier)   │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  6. META-SCORER ★ PRODUCTION DECISION PATH ★                         │
│     XGBoost 16-fold + TCN + Ising regime + conformal calibration     │
│     D-codes: D281 baseline · D290 adaptive Kelly · D291.5 HIGH raise │
│              D293 ensemble REVERTED · D293.8 filed for retest        │
│     OUT: tier ∈ {ELITE 60%+, HIGH 50%+, VETOED 30%, BROAD, SKIP}     │
│          kelly_frac ∈ [0, 0.50] via D290 adaptive schedule           │
│     CALIBRATION: ELITE win ~72%, HIGH ~41% (recent 60d, n=56)        │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  7. CONTINUER v2 ENSEMBLE — SHADOW ONLY (decision 2026-06-25)        │
│     Stacked: XGB + LGBM + CatBoost + LR + RF → LR meta               │
│     +4.05%/trade WF on T+5 forward return                            │
│     See experiments.md → [Continuer_v2]                              │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  8. BOCPD — SHADOW ONLY (decision 2026-06-15)                        │
│     Bayesian online change-point on rolling P&L stream               │
│     Trigger: P(break) > 0.85 (planned: halve Kelly, not full halt)   │
│     See experiments.md → [BOCPD_kill_switch]                         │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  9. KELLY TIER CLASSIFIER (src/core/kelly_tier.py)                   │
│     RULE-BASED cascade Tier 1→4 (STANDARD/HIGH/EXCEPT/OUTLIER)       │
│     risk_per_trade × max_position_pct, daily 10% loss cap            │
│     D-codes: D281, D290, D291.5                                      │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 10. COMPOSITE v0 — SHADOW ONLY (decision 2026-06-30)                 │
│     sklearn LR on 407-row train set; agreement audit logging         │
│     See experiments.md → [Composite_v0_shadow]                       │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 11. EXECUTION ★ PRODUCTION ORDER PATH ★                              │
│     D310.T2 wide/tight arm split → Alpaca OTO / standalone STOP      │
│     Wide arm (50%): L1 standalone-stop bypasses dead TrailingStop    │
│     Tight arm (50%): D142 Phase 1 1.5% override (legacy, frozen)     │
│     D-codes: D277 halt switch · D294 stop floor · D295 D91 journal · │
│              D297 OTO stop journal · D310 T2 arm assignment          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Defense layer wraparound (the 30% the diagram doesn't show)

```
┌────────────────────────────────────────────────────────────────┐
│ ALERTS  D304 schema · D312 veto_summary · D314 spool ·         │
│         D315 boot self-test                                    │
│         → see monitoring.md + RUNBOOK_HEDGE_VIOLATION.md       │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ RECONCILIATION  D222 PNL recon · D238 EOD broker-truth ·       │
│                 D311 after-filter fix · 90-day audit ledger    │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ SAFETY  D277 halt · D294 stop floor · D295/D297 journal ·      │
│         D310-D315 T2 safety stack:                             │
│           L1 standalone-stop (bypasses dead TrailingStop)      │
│           L2 hedge invariant watcher (20s poll, 60s tol)       │
│           L2_WATCHDOG (watcher-of-watchers, 90s freshness)     │
│           D313.v2/v3 correctness band 0.65 + @here debounce    │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ KILL SWITCHES  D277 MOMENTUM_HALT_NEW_ENTRIES env var          │
│                BOCPD P(break) > 0.85 (planned wire-in)         │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ DATA QUALITY  polygon_flatfile + parquet_warehouse +           │
│               aftermath_strat + 95% safety guard on rebuild    │
│               D293a, D311, D194 (see data.md)                  │
└────────────────────────────────────────────────────────────────┘
```

---

## Production vs shadow paths

**Production (hot path):**
Scanner → Agents (6 parallel) → MFCS → [Debate if MFCS ≥ 0.60] → Risk Veto → **Meta-Scorer (tier + Kelly)** → **Execution (D310.T2 arm split)** → broker order.

**Shadow (write-only, non-blocking):**
- Composite v0 (`src/shadow/composite_shadow.py`) — agreement audit
- Continuer v2 (`scripts/ml_continuer_v2_ensemble.py`) — +4%/trade WF predictor
- BOCPD (`src/analysis/bocpd*.py`) — regime break detector
- TabPFN single-seed shadow (`scripts/tabpfn_shadow_runner.py`)
- Stop-decision log (D308) — feeds T1 replayer

Every shadow system has a `decision_date` in `experiments.md`. None will live in shadow indefinitely.

---

## End-to-end latency budget

Per ADR-001: < 90s per candidate. Critical path:

| Stage | Budget | Actual (typical) |
|---|---|---|
| Scanner | 5s | ~2s |
| Enrichment | 5s | ~3s |
| Agents (parallel) | 30s | ~20s |
| MFCS | <1s | instant |
| Debate (if fires) | +30s | ~25s |
| Meta-Scorer | 1s | <500ms cached |
| Kelly + Execution | 2s | <1s |
| **Total worst-case (with debate)** | **~75s** | ~50s |

---

## Most-touched subsystems (D-code reference count)

| Subsystem | D-code refs | Top codes |
|---|---|---|
| Agents / LLM | 100+ | D91, D92, D121, D126, D216-D221 |
| Executor | 65+ | D163-D165, D142, D146, D147, D308 |
| Orchestrator | 60+ | D310, D277, D278, D280, D122 |
| Monitoring | 35+ | D304, D308, D312, D279, D314 |
| Reconciliation | 25+ | D222, D56, D295, D311 |

The Agents subsystem is the most heavily-instrumented because LLM
calls fail in interesting ways. The Executor subsystem is next
because that's where actual money moves.

---

## See also

- [`models.md`](models.md) — per-layer model details (XGBoost folds,
  TCN architecture, agent weights, calibration data)
- [`monitoring.md`](monitoring.md) — alert taxonomy + watcher
  inventory + runbook references
- [`data.md`](data.md) — lake structure (polygon_warehouse,
  aftermath_strat, shadow_stops, alerts) + pipeline diagrams
- [`d_codes.md`](d_codes.md) — every D-code with provenance
- [`experiments.md`](experiments.md) — what's been tested + decision dates
