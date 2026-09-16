# Diagnosis — The 13-Gate Cascade

## How a candidate dies

A pre-market gap-up stock travels through this gauntlet between Phase 1 (scan) and Phase 4 (order). Each gate is binary — pass or reject. With 13 gates at average 85% pass rate, compound survival is **0.85^13 ≈ 12%**. With 80% rates that's 5.5%. With one gate at 0% (D112 today), survival is **zero**.

Per-gate reference, in execution order:

| # | Gate | Location | Threshold | Apr 16 Impact |
|---|------|----------|-----------|---------------|
| 1 | EMC gap | `src/scanners/premarket.py:274` | `gap >= 5%` | scanner-level |
| 2 | EMC RVOL | `src/scanners/premarket.py:259` | `rvol >= 3.0` (was 2.0) | scanner-level |
| 3 | EMC price | `src/scanners/premarket.py:236` | `price >= $2.00` (was $1.50) | scanner-level |
| 4 | EMC dolvol | `src/scanners/premarket.py:265` | `>= $2M` | scanner-level |
| 5 | EMC stale gap | `src/scanners/premarket.py:413` | rejects "open already accounts for >70% of gap" | rare |
| 6 | EMC exhaustion | `src/scanners/premarket.py:462` | rejects extreme RVOL with no catalyst | rare |
| 7 | GEX hard filter | `src/core/scan_loop.py:124` | `gex_normalized < 0.05` (skipped if MCap < $500M per D219 Phase 3) | rare for our universe |
| 8 | **D112 router instant reject** | `src/core/adaptive_router.py:129-200` | **rvol≥1, price≥$0.50, float≤200M, gap≥3%** | **65 of 85 rejections today** |
| 9 | D112 router deterministic tier | `src/core/adaptive_router.py:205-221` | strong_pass at MFCS≥0.40 | n/a (blocked at 8) |
| 10 | D101 consensus gate | `src/core/orchestrator.py:1016-1052` | needs ≥1 directional agent OR MFCS≥0.30 | 1 today (was 39 Apr 14) |
| 11 | D124 alignment gate | `src/core/orchestrator.py:1054-1087` | rejects if bearish dominates by 2+ or 1.5x conf | 3 today (was 54 Apr 14) |
| 12 | D101 VWAP bias | `src/core/orchestrator.py:~1315` | rejects if `price < VWAP` by >0.5% | 14 today |
| 13 | MFCS buy threshold | `src/core/orchestrator.py:~2700` | `mfcs >= 0.25` | 0 candidates reached this gate today |

Gates 8, 12, 13 are the current killers. Gates 10–11 used to be the killers but D219 Phase 2 fixed them on April 15.

## Today's evidence (April 16, journal `journal_2026-04-16_113343.jsonl`)

5 unique tickers reached MFCS calculation. Each was evaluated 13 times across the morning's pre-market scans. **All 5 produced MFCS = 0.821** — a score 3.3× the buy threshold. **All 5 were rejected at gate 8.**

```
IMMP  | MFCS=0.821 | NO_TRADE | D112 Router: float 1,178,976,000 > 200,000,000 max
VSA   | MFCS=0.821 | NO_TRADE | D112 Router: float   503,784,000 > 200,000,000 max
QBTS  | MFCS=0.821 | NO_TRADE | D112 Router: float   295,928,000 > 200,000,000 max
HUBC  | MFCS=0.821 | NO_TRADE | D112 Router: price $0.16 < $0.50 floor
XHG   | MFCS=0.821 | NO_TRADE | D112 Router: float 98,972,016,000 > 200,000,000 max
```

Of these, **IMMP gapped 73%, VSA 79%, QBTS 65%** — these are exactly the explosive movers the user wants to trade. The system identified them, scored them as high-conviction buys, and then refused to trade them because their float was above 200M.

The XHG entry shows `float 98,972,016,000` — that's 98 billion, almost certainly a Finnhub data error, but the system has no sanity check.

## D112's intent vs effect

D112 was added in late March (per its docstring) as a **compute optimization** — Gemini-3.1-style "adaptive compute" that skips LLM evaluation for hopeless candidates to save tokens and latency:

> "This changes HOW FAST candidates are evaluated, not WHAT gets traded. MFCS threshold, stops, sizing — all unchanged."

That intent is contradicted by the four `instant_reject_*` thresholds, which DO decide what gets traded. They were copy-pasted from the EMC scanner thresholds at a moment when the scanner had `price_min=0.50` and `float_ideal=10M`. D219 Phase 1 raised the scanner price_min to $2.00 and the float ideal to 5M — but **D112 was never updated**. The router now uses thresholds that the scanner already supersedes for its primary universe, AND it adds a hard float cap that the scanner deliberately removed.

## The VWAP bias gate (gate 12)

14 candidates today were rejected for being slightly below VWAP. Examples from the journal:

```
D101 VWAP bias: price $6.17 is 0.6% below VWAP $6.21
D101 VWAP bias: price $0.67 is 2.6% below VWAP $0.68
D101 VWAP bias: price $0.67 is 3.5% below VWAP $0.69
```

A 0.6% deviation below VWAP is noise — typical bid/ask spread on a small-cap stock. This gate rejects setups where the stock is consolidating just below VWAP before a breakout. The Zarattini ORB research the user cited (Sharpe 2.81) explicitly enters on first move *above* the opening range — which means by definition you may be below VWAP for the first 5-10 minutes. The current gate rules out exactly that pattern.

## The MFCS threshold "gate" (gate 13)

The buy threshold of 0.25 is fine. The problem is that **no candidate reaches this gate** because they're all killed at 8 or 12. If we open the funnel by relaxing 8, then 13 becomes the new bottleneck and we'll need to look at MFCS calibration.

From yesterday's 79-day backfill: **gap >= 50% candidates have 33% close win rate but +27.3% average MFE**. Most close in the red but spike substantially intraday. This means a buy-and-hold-to-close strategy on these is bad, but **a buy-and-exit-at-T+15 strategy is exactly what the data supports** — and the backfill confirmed T+15 is the peak win-rate horizon (54%).

## The bigger pattern: each fix reveals the next bottleneck

Look at the timeline:

- **Pre-D219**: D101 + D124 reject 53% of pipeline. Fix: D219 Phase 2 (April 15 night).
- **April 16 (post-D219)**: D101 + D124 quiet. D112 reject 80% of pipeline. Fix: TBD.
- **Post-D112 fix**: VWAP bias + MFCS calibration likely become bottleneck. Fix: TBD.
- **Post-MFCS fix**: D170 observation window + ORB confirmation likely become bottleneck. Fix: TBD.

This is not a tuning problem. This is a **structural problem**: 13 binary gates in series can never produce trades reliably because each gate that you fix exposes the next one. Even if every gate is "right" individually, the multiplicative dilution kills end-to-end throughput.

The next document (`02_arena_critique.md`) explains why our arena testing has failed to catch this — and why the fix has to come from **collapsing the cascade**, not from tuning thresholds one gate at a time.

## Two specific bugs that should also be fixed

1. **Float sanity check missing.** XHG was reported at 98,972,016,000 shares (98 billion). Even Microsoft has only 7B shares outstanding. The Finnhub enrichment wrapped a bad value (probably a units bug) and we shipped it without validation. Add `if float_shares > 50_000_000_000: float_shares = None` to the D219 enrichment path in `main.py:1424-1471`.

2. **HUBC at $0.16 reached the router.** The EMC scanner's `price_min=$2.00` should have rejected it. Either the scanner has a high-volume override letting it through (D105/D160), or the price changed intraday. Either way, having an inconsistency between scanner and router is bad — the router applies a $0.50 floor when the scanner applies a $2.00 floor. Pick one and align.
