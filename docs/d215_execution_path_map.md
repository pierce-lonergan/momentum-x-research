# D215: Execution Path Map — Source of Truth

**Any change to this map must be accompanied by a commit message explaining why.**

**Last updated:** 2026-04-08

## 7 Entry Paths

| # | Path | Phase | Line | Type | Journal | Recorder | Status |
|---|------|-------|------|------|---------|----------|--------|
| 1 | D207 Aggressive Short | 1.5 | 1879 | SHORT | YES | YES (D215) | **DISABLED 2026-04-08: PF=0.11 over 25 trades** |
| 2 | D161 Faller Short | 2 | 2591 | SHORT | YES | YES (D215) | **DISABLED 2026-04-08: PF=0.11 over 25 trades** |
| 3 | Phase 2 BUY | 2 | 2760 | LONG | YES | YES (D215) | Active |
| 4 | D170 Observation | 3 | 4524 | LONG | FIXED (D215) | YES (D215) | Active |
| 5 | VWAP Breakout | 3 | 5159 | LONG | YES | YES (D215) | Active |
| 6 | Rescan | 3 | 5538 | LONG | YES | YES (D215) | Active |
| 7 | Fast-Path | 1.5 | 1426 | LONG | FIXED (D215) | YES (D215) | Active |

## Path Status History

| Date | Path | Change | Reason |
|------|------|--------|--------|
| 2026-04-08 | 1 (D207) | DISABLED | PF=0.11, 25 trades, -244.6% cumulative. See d215_short_book_postmortem.md |
| 2026-04-08 | 2 (D161) | DISABLED | Same short book. PF=0.11. |
| 2026-04-08 | 4 (D170) | FIXED | Journal fill recording + feature propagation from _obs_scored.candidate |
| 2026-04-08 | 7 (Fast-Path) | FIXED | Feature propagation from fpe.candidate. Was recording None for all features. |
| 2026-04-08 | All | ADDED | ExecutionRecorder wired into all 7 paths |

## Feature Availability per Path

| # | Path | gap_pct | rvol | mfcs | float_shares | market_cap | prior_gap_count |
|---|------|---------|------|------|-------------|-----------|----------------|
| 1 | D207 Short | YES (candidate) | YES | NO (pre-LLM) | YES | YES | YES |
| 2 | D161 Short | YES (candidate) | YES | YES | YES | YES | YES |
| 3 | Phase 2 BUY | YES | YES | YES | YES | YES | YES |
| 4 | D170 Obs | YES (scored.candidate) | YES | YES | YES | YES | YES |
| 5 | VWAP | YES (candidate) | YES | YES | YES | YES | Partial |
| 6 | Rescan | YES (candidate) | YES | YES | YES | YES | Partial |
| 7 | Fast-Path | YES (fpe.candidate) | YES | partial_mfcs | YES | YES | YES |

## Why 7 Paths Exist

Each serves a distinct strategic purpose:
- **Path 3 (Phase 2)**: Standard entry after full LLM evaluation. Highest feature coverage.
- **Path 7 (Fast-Path)**: Speed-critical 9:30:01 entry. Fires before LLM completes.
- **Paths 5-6 (VWAP/Rescan)**: Intraday re-evaluation catches late momentum.
- **Path 4 (D170)**: Deferred entry awaiting price confirmation.
- **Paths 1-2 (Shorts)**: **DISABLED.** Were gap-fade plays. Failed catastrophically.

## Rules

1. No new execution path may be added without updating this map.
2. No disabled path may be re-enabled without a commit message referencing the original disable reason.
3. Every path must call `_exec_recorder.record_execution()` — no exceptions.
4. Per-path P&L must be visible in the session report. If it isn't, add it.
