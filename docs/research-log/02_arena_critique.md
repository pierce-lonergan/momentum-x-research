# Arena Critique — Why Three Arenas Missed the Real Problem

The system has three independent arena infrastructures plus a brand-new backfill dataset. Each arena is well-built. **None of them would have caught the D112 bottleneck that produced this week's zero trades.** This document explains why and proposes a single new arena that closes the gap.

## The three existing arenas

### Strategy Arena — `src/arena/`

**What it does.** Replays historical BUY verdicts (already accepted by the production gate cascade), re-weights agent contributions to the MFCS score, and re-simulates the exit timing. Outputs decision quality scores per regime.

**What it misses.** The arena starts from candidates that **already passed the entire gate cascade in production**. By construction, it tests *exit and weighting* on a pool that has already survived all the gates. If the gate cascade rejects everything (this week's situation), the arena has nothing to operate on — and worse, it gives no signal that the pipeline is empty because it just runs on whatever historical data is in `data/journals/`.

**Last successful run.** `data/arena/` is empty. No recent commits.

### Selection Arena — `src/selection_arena/`

**What it does.** Sweeps the EMC scanner thresholds (price_min, rvol_min, gap_pct_min, dollar_volume_min) against historical "movers" (>20% daily gainers). Computes precision/recall/F2 per filter profile. Identifies which filters are "killing" true positives (FN attribution).

**What it does well.** This arena is the most intellectually honest of the three. It explicitly measures TP/FP/FN/TN. The user's memory note says: "Mar 30: dolvol_min kills 100% of FNs" — that's exactly the kind of finding we need.

**What it misses.** It tests *only the EMC scanner stage*. It does not test D112 (router instant_reject), D101 (consensus), D124 (alignment), D101 VWAP bias, MFCS calibration, or D170 observation window. **Today's D112 bottleneck is invisible to this arena.** When IMMP gaps 73% and the scanner accepts it but D112 rejects it for float >200M, the Selection Arena reports "TP captured" — because from its perspective the scanner did its job. The downstream rejection is unmeasured.

### LLM Arena — `src/llm_arena/`

**What it does.** Evaluates news catalyst classification accuracy (LLM judge vs ground truth labels). Tests prompt variants for the news agent.

**What it misses.** Catalyst quality alone does not produce trades. A perfect news agent score fed into a system where D112 rejects 80% of candidates produces zero trades. The LLM Arena answers "is the news agent calibrated correctly" — a real question, but not the one blocking us.

## The 79-day backfill that no arena uses

Yesterday (April 15-16) we built `data/backfill/`:
- `candidates.jsonl` — 5,862 historical gap-up candidates over 79 trading days (Dec 11 → Apr 14)
- `bar_recordings/<date>/<ticker>.json` — minute bars for the top 500
- `features_labeled.jsonl` — 407 fully-labeled rows with multi-horizon outcomes (T+1, T+5, T+15, T+30, T+60, T+120, close, MFE, MAE, time-to-MFE)
- `arena_scenarios.jsonl` — 407 archetype-tagged scenarios

Key findings from `docs/d219_backfill_findings.md`:

1. **ORB confirmation: +41.6pp WR edge.** ORB-broken: 53% close WR, +25.9% MFE. ORB-held: 11% WR, +4.4% MFE. This is the single largest signal in the data.
2. **T+15 is the peak.** Win rate by horizon: T+1 40%, T+5 45%, **T+15 54%**, T+30 49%, T+60 48%, close 44%. Hold-to-close loses 10pp of WR.
3. **Equal-weight all passers: -19.3% over 79 days, Sharpe -0.36.** Without selection, the universe is unprofitable.
4. **Gap ≥50% candidates: 33% close WR but +27.3% MFE.** They spike then fade — perfect for ORB scalp + tight stop.

**This 407-row labeled dataset has not been run through any of the three arenas.** It's the most valuable artifact in the project and it's sitting unused.

## The structural disconnect

Each arena answers a *local* question (scanner tuning, agent weighting, news quality) but **none answer the global question**: *given today's pre-market candidate pool, will the system actually take a trade and at what expected return?*

The result is a measurement system that confirms each component is locally good while the end-to-end system trades zero times per week. This is the classic optimization-without-integration failure mode.

## What's needed: one arena, end to end

I propose `src/production_arena/` — a single new arena whose contract is:

> **Input:** Raw candidate JSON (e.g. one row from `data/backfill/candidates.jsonl`) plus the actual minute bar replay (from `data/bar_recordings/`).
>
> **Process:** Run the **exact** production pipeline — scanner filter, GEX gate, D112 router, agent dispatch, MFCS scoring, D101 + D124 gates, VWAP check, D170 observation, ORB confirmation, position sizing, paper-fill simulation, exit logic.
>
> **Output:** A `ProductionVerdict` per candidate containing:
> - The exact gate that rejected it (file:line) OR the trade's realized P&L
> - The MFCS score and component breakdown
> - The would-be entry price, exit price, time-to-exit
> - The maximum favorable excursion missed
>
> **Aggregation:** A daily summary table showing — for the supplied date range — total candidates, gate-by-gate survival, BUY count, simulated session P&L, and a counterfactual ("if D112 was disabled, +N trades, +X%; if VWAP gate was 2%, +N trades, +X%; ...").

The 407-row backfill is the perfect input set. Run the new arena against it, capture the gate-by-gate survival distribution, and see which gates are killing the EV.

This kind of arena is what would have caught the D112 bottleneck the moment D219 Phase 2 was committed: the simulation would have shown D101+D124 dropped from 93 to 3, but D112 jumped from 25 to 65. The user would have seen the bottleneck shift in dev rather than in production at 7:30 AM Thursday.

## Concrete spec for the new arena

```
src/production_arena/
  __init__.py
  scenarios.py        # Loads candidates.jsonl + bar_recordings into Scenario objects
  pipeline_runner.py  # Imports the REAL orchestrator, runs it against a Scenario
  verdict.py          # ProductionVerdict dataclass (gate_rejected, mfcs, pnl, ...)
  aggregator.py       # Per-day and per-gate counts, counterfactual sweeps
  cli.py              # `python -m src.production_arena --dates 2026-01-01:2026-04-14`
```

The crucial design constraint: **`pipeline_runner.py` must import and call the production code unchanged.** No stubs, no parallel scoring logic, no "simplified MFCS." If the production orchestrator computes MFCS=0.821 on IMMP today, the arena must compute the same number when fed the same minute-bar replay.

The way to enforce this is to feed the orchestrator's `evaluate_candidate()` async function with a `Scenario`-derived `CandidateStock` and a mocked clock. Everything downstream (agents, GEX, D112, D101, D124, MFCS, ORB, sizing) runs as in production.

## Why this matters for the user's goal

The user's stated goal:

> "we are trying to maximize profit as much as possible with each trade and we are trying to find the most explosive stocks to trade each day"

To do that you need to:
1. Know what your system would have traded over a representative period (not what the scanner would have surfaced — that's stage 1 of 13).
2. Know the realized P&L distribution of those trades.
3. Know the counterfactual — for each gate, what's the marginal contribution to P&L?

The new arena answers all three. The current arenas answer none.

Once the production arena exists, the threshold tuning we'll do tomorrow morning (per `03_immediate_fixes.md`) becomes scientifically justified: not "let's raise D112 max_float and see what happens in live trading," but "the arena shows raising D112 max_float to 1B captures 47 additional trades over 79 days at a +12.4% average MFE, with 3 outlier losses contained by the existing stops."
