# 264 — Account forensics: where the doubling came from, and the honest "turn luck into law" program

**Author**: Claude (Fable 5 era)
**Date**: 2026-06-08 (evening)
**Mandate**: Pierce — "maximize daily profit, 5%/day goal, the system got lucky and doubled the paper account — turn that luck into law, do whatever you need to do." First law of gaming a system: know exactly which game the money came from. So: full forensics on the live account, then the program. **No blind hot-path changes shipped tonight** (4:30 AM session in a few hours); tonight = truth + safe instrumentation; the levers ship flag-gated with replay validation.

## 1. The decomposition — $100,000 → $216,275 (+116.3%), itemized
Ran the live equity curve + every broker fill through the SAME hostile gates every backtest faced (`scripts/account_forensics_doc264.py`, read-only):
| metric | value | read |
|---|---|---|
| median daily return | **+0.000%** | the typical day makes NOTHING |
| daily win rate | **29%** (22/77) | most days lose |
| top-5 days share of total gain | **140%** ($162.5K of $116.3K) | the other 72 days NET LOST ~$46K |
| median ticker P&L | **−$90** (win rate 36%, n=95 closed) | the typical trade loses |
| top-2 tickers | **LASE +$73,140 + CRCA +$40,985 = $114K of $116K** | the doubling IS two lottery tickets |
- **CRCA (+$41K, Feb 26, +42.7% equity day)**: the known **reverse-split paper-account artifact** — accounting luck, not market skill. Unrepeatable.
- **LASE (+$73K, Jun 3, +68.6% equity day)**: a real squeeze, banked. How it was actually won (broker fills): bought 17,110 @ $1.29 → **stopped out 31 SECONDS later** (−$684) → re-bought ~16,000 @ $1.34 → added 16,092 @ $1.32 → **held overnight** → sold all ~26,800 @ $4.08 into the next-morning spike. The biggest win in account history required (a) re-entering after the tight stop churned it, and (b) holding the ticket overnight. *And LASE was red-flag 0.9 ("Nasdaq delist warning, missing 10-Q") on the day it paid.*
**Verdict: the live account fails the identical hostile gate that killed 7 backtest illusions. The doubling is the right tail of the trash-name lottery (one real ticket + one accounting artifact), not a daily process. Median day = 0.000%.** This is not "the system works"; it's "the lottery exposure occasionally pays, and everything around it bleeds."

## 2. The 5%/day math — stated once
5%/day compounded × 252 trading days = **1.05²⁵² ≈ 218,000× per year** ($216K → ~$47 billion). The best sustained track record in market history (Medallion) was ~66%/yr gross. 5%/day is not an aggressive target; it is a category error — and any backtest I could produce "showing" it would be built from exactly the tail-illusions this project spent months learning to kill. **What the data DOES support as the honest maximization target**: (a) kill the ~−$640/day median-bleed (72 non-lump days net −$46K), (b) stop the system from *de-selecting its own lottery tickets* (see §3 — this is where the real money is), (c) bank the lumps when they hit. The result is a lumpy, right-skewed curve that compounds — not a daily 5% law. Nobody can schedule the lottery; we CAN stop tearing up winning tickets.

## 3. THE finding — the system mechanically de-selects its own winners (every layer)
The fill-aware grader (shipped tonight) on 2026-06-08:
- Journal BUYs: 19. **Broker-FILLED: 5 — OCC −3.1%, TNGX +3.1%, GMHS −33.3%, ABAT −5.0%, AIM −15.8% (the faders).**
- **SUBMITTED-UNFILLED: 14, of which SEVEN RAN: NPT +274%, BYAH +121%, MTEN +76%, TDIC +60%, SMTK +36%, RYET +18%, SUGP +16% (the rockets).**
**The dip-limit entry is an adverse-selection machine by construction**: a passive limit below price fills when the stock falls through it (faders) and never fills when it runs away (rockets). On 6/8 the system *identified* 7 monsters and its execution layer declined all 7, then bought the 5 fades. This is the execution-layer mirror of doc-251's "buying gap-ups = buying the fade."
The same anti-tail tuning at every layer, each with evidence:
- **Entry** (above): fills = fades, misses = rockets (6/8, perfectly partitioned).
- **Stops**: the D308/D309 stop-widening shadow (n=14 live decisions since mid-May): ATR-wide stops **+$7,733 vs actual tight stops +$1,778 = +$5,955 left on the table (+$425/decision mean)**. Production stops sit 1.5% below entry on names with 20-50% ranges (CISS row). LASE's −$684-in-31s is the same mechanism.
- **Exits**: LASE was banked only because it was *held overnight* and sold into the spike; the early-tranche shaving (sold 5,340 @ $1.36 minutes after entry) gave away a 3× on those shares.
**The system is tuned like a mean-game (avoid pain, take profits early, enter passively). The live P&L decomposition proves it's a tail-game (all profit = 2 lumps). Every pain-avoidance mechanism is tail-removal.**

## 4. REVERSAL — the doc-262 veto-gate idea is DEAD
Doc 262 recommended wiring the red-flag/meta-scorer veto into prod. **The forensics kill it**: LASE was red-flag **0.9** on the day it made +$73K; AIM was red-flag 0.95 on 6/1 and rocketed +62%. The flagged names are where the VARIANCE clusters (doc 254: prior-runners rocket MORE *and* fade more) — and variance is the only product this system sells. A veto/size-down on flagged names cuts both tails, and since 100% of cumulative profit is right-tail, **it would have cut the only thing that ever paid (LASE alone ≈ 63% of all-time gains)**. The Lottery meta-scorer's 5-week 0-trade stance "avoided the bleed" by avoiding the game — and banked $0 of the $73K. Veto = NOT wired. The red-flag stays what doc 261 made it: a passive research signal (and a *short-side* lead where borrow exists).

## 5. The program — ranked by evidence × dollars (each flag-gated + replay-validated before live)
| # | Lever | Evidence today | Est. value | Next step |
|---|---|---|---|---|
| 1 | **Entry mechanism**: dip-limit → confirmation-triggered MARKETABLE entry (subset: highest-RVOL candidates) | 7 unfilled runners in ONE day (+274/+121/+76/+60/+36/+18/+16%) vs 5 filled faders | dominant | **Pre-registered A/B replay** on every journal BUY since May 1 (collected minute bars): actual dip-limit vs counterfactual marketable-at-trigger, net of realistic slippage, median AND mean, cross-week. The fill_backtest's "marketable −2%/name" measured fixed-exit cost, never the missed-tail asymmetry — this replay prices BOTH sides. |
| 2 | **ATR stops** (replace 1.5%-below-entry noise stops) | Live shadow n=14: +$425/decision, +$5,955 total | ~$2K/week at current pace | Let shadow reach n≥30, validate via `simulate_day.py`, then flag-gated flip. |
| 3 | **Runner/hold policy** (the LASE pattern: strong close + huge volume → hold; else flat EOD) | LASE +$73K banked via overnight hold; doc-258 says blanket multi-day holds are −EV → must be CONDITIONAL | the lumps | Test on collected data: "close>+30% on RVOL>10 → hold to next open" vs flat-EOD, median-gated. |
| 4 | **Measurement integrity**: D164/D165 phantom siblings + OCC journal re-stamp | doc 263 flagged | trust in every number above | Surgical fixes, same Bug-Z pattern. |
| 5 | ~~Veto gate~~ | **REVERSED** (§4) | n/a | Stays passive/research. |

## 6. What I will not do
No backtest engineered to "show" 5%/day (we know exactly how those illusions are built — tail-means, no winsorize, in-sample). No leverage/size-up on a −$174/trade-mu process (that maximizes ruin speed, not profit). No measurement games (the phantom-P&L class as a feature). The honest path to maximum profit is §5 — it attacks the one empirically-proven leak: **the system's own machinery removing its tail.**

## Shipped tonight (safe, no hot-path)
`scripts/account_forensics_doc264.py` (the decomposition, re-runnable); **fill-aware grader v2** (`shadow_vs_prod_grader.py`: journal-BUY vs broker-FILLED split + UNFILLED-THAT-RAN adverse-selection line, runs nightly 19:30 via MomentumX-ShadowGrader); this doc. **Basis**: Alpaca portfolio history + 736 fills + journals + `data/shadow_stops/replay_results.parquet`. **Predecessors**: 262/263 (the ops dive that built the grader), 250-261 (why no selection alpha exists — nothing tonight contradicts it; the levers are EXECUTION/variance, not prediction).
