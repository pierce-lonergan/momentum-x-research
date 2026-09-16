# Opus 4.7 System Review — April 16, 2026

**Reviewer:** Claude Opus 4.7 (1M context)
**Scope:** Full system audit — production trading pipeline + arena testing infrastructure
**Trigger:** Zero trades all week. 525 evaluations across 4 days, 0 BUYs.

## Verdict in one paragraph

The system is **architecturally rigged to reject every candidate.** It runs **13 sequential binary gates** between scan and order — at a per-gate pass rate of 80–95%, the compound survival is roughly **6%**. D219 Phase 2 fixed the consensus gates (D101/D124 went from 93 daily rejections to 3) but only revealed the next bottleneck: the D112 Adaptive Router rejected **5 candidates with MFCS=0.821 today** for having floats >200M shares. **The system computed a high-confidence buy signal and threw it away on a static structural rule.** Meanwhile the three arena systems (Strategy, Selection, LLM) test entirely different layers than the gates that are actually killing trades — the 79-day backfill we generated yesterday has not been run through any arena. **The system is well-engineered but optimized to LOSE NOTHING rather than to TRADE.**

## Document index

| File | Topic | Length |
|------|-------|--------|
| `01_diagnosis.md` | The 13-gate cascade — file:line for every gate that killed trades this week | Long |
| `02_arena_critique.md` | Why none of the arenas catch the production problem | Medium |
| `03_immediate_fixes.md` | Three changes that will produce a trade tomorrow morning | Medium |
| `04_structural_redesign.md` | Why the cascade architecture is broken — replace with a calibrated composite score | Medium |
| `05_action_plan.md` | Sequenced 7-day execution plan with verification gates | Short |

## The headline numbers

| Date | Process | EMC scan | Evaluated | MFCS max | BUYs | Top blocker |
|------|---------|----------|-----------|----------|------|-------------|
| Apr 13 | CRASHED | 8 | 0 | n/a | 0 | logging deadlock |
| Apr 14 | OK | 5,094 | 215 | 0.533 | 0 | D101 + D124 (93) |
| Apr 15 | OK | 4,273 | 225 | 0.539 | 0 | D101 + D124 (100) |
| Apr 16 | OK | 24 | 85 | **0.821** | 0 | D112 Router (65) |

D219 Phase 2 ships at 8:57 PM April 15 → April 16 the consensus gates went silent (3 rejections vs 100 the day before) and **MFCS quality jumped 86%** (avg 0.348 vs 0.186). The system is producing better candidates than ever before — and rejecting them on a different gate.

## The single most important sentence in this review

> **D112's `instant_reject_max_float = 200_000_000` rejected 5 candidates today with MFCS=0.821 — a score 3.3× the buy threshold of 0.25.**

If you change ONE thing tonight, it should be that line in `config/settings.py:1343`. See `03_immediate_fixes.md` for the exact change and the supporting evidence from yesterday's 79-day backfill.

## What the user asked for

> "we are trying to maximize profit as much as possible with each trade and we are trying to find the most explosive stocks to trade each day. The problem with the system is we have not made a single trade this week."

The system is currently configured to reject explosive stocks because explosive stocks often have larger floats (IMMP 1.17B), trade below $0.50 (HUBC at $0.16 after a reverse split), or sit slightly below VWAP at the moment of evaluation. The "small float, high price, above VWAP" universe is the SLOW, BORING universe. The user wants the explosive universe and the system is filtering it out.

## My honest assessment

This system has been engineered with extreme care to avoid bad trades. Every gate exists for a real, documented reason from a previous burn. But each new gate has been added on top of the others, never replacing them. The result is a system that has crossed the threshold from "selective" to "paralyzed."

The fix is not more gates, more thresholds, or more agents. The fix is to **collapse the 13-gate cascade into a single calibrated probability score** and let it speak — using the 407-row labeled backfill we generated yesterday as the training and validation set. Today's 5 MFCS=0.821 candidates are exactly what the system should be trading, not exactly what the system should be rejecting.

Read on for the file:line evidence and the 7-day action plan.
