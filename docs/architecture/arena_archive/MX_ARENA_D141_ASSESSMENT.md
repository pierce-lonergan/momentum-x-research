# mx-arena D141 Assessment: 8.5/10 (Unchanged From D137)

**Date**: March 28, 2026
**Self-assessed**: 9.5 | **Externally corrected**: 8.5

## Score Unchanged Because

The innovations added breadth but not depth. None produced findings that survived
statistical scrutiny beyond what D135-D137 established.

## Finding Validity Analysis

| Finding | Validity | Issue |
|---------|----------|-------|
| -99.3% phantom P&L | **Unimpeachable** | Reproducible, deterministic, verified by golden regression |
| LLM agents = zero value | **Artifact** | Decision replay uses journal signals as-is. Gap momentum dominates MFCS at 0.30 weight. Tests "does removing news change MFCS enough to flip BUY?" — not "do LLM agents help identify catalysts the system would otherwise miss" |
| +1072% pipeline inversion | **Directionally correct, magnitude unreliable** | Sequential baseline near zero ($0.09), making any improvement look astronomical. Absolute delta ($0.95) more meaningful but still 14 dates with wide CIs |
| 15-19% capture ratio | **Most important, most underexploited** | 80-85% of MFE left on table. Getting from 15% to 30% doubles P&L. Tranche targets (+5/+10/+20) are swing-trade levels on intraday stocks |
| 3.43x walk-forward overfit | **Correctly reported, incorrectly resolved** | 524 synthetic candidates are correlated observations from a 1/50 signal model, not 524 independent samples |

## Why Self-Score of 9.5 Is Wrong

1. Gap 1 (1/50 signals) claimed closed in header but acknowledged open in Part 7
2. Gap 4 (49 trades noise) "closed" by synthetic candidates that lack real-trade statistical properties
3. Innovations 7-10 built but have zero reported findings, zero tests, zero validation

Honest trajectory: D129(7.0) -> D133(8.0) -> D135(8.5) -> D137(8.5) -> D141(8.5)

## Three Concrete Actions for Profitability

### Action 1: Tighten tranche targets (highest dollar impact, 1-day project)
Current targets +5/+10/+20 are swing-trade levels. MFE peaks at +8% but T2/T3 never
fill. Reset to +2/+4/+7 and re-run. If capture ratio jumps 15% -> 25%, deploy.
Alternatively: compute MFE 25th/50th/75th percentiles, set T1/T2/T3 to those.

### Action 2: Phase 0 ultra-tight stop for pipeline inversion safety (30s)
Pipeline inversion enters positions before LLM vetting. During 15-30s LLM delay,
holding unvetted positions on volatile gap-ups. Add 0.5-1% ultra-tight Phase 0 stop
that widens after LLM confirmation (or after 30s if no response).

### Action 3: Daily retrospective comparison loop
Each morning: replay yesterday in arena, compare predicted vs actual P&L. After 20
days, compute correlation. If >70%, arena predictions are trustworthy. This is the
real path to 10.0 — not engineering completeness but demonstrated prediction accuracy.
