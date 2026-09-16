# 233 — The realism pass: BET#1's profit edge is a mirage (the prediction is real; the alpha isn't)

**Author**: Claude Opus 4.8
**Date**: 2026-06-02 (Tuesday, late)
**Mandate**: Pierce — "these are fascinating results… let's see if it holds up. Do the realism pass."

It did not hold up. This is a **negative result delivered with full rigor** — and it is one of the
most valuable outcomes of the whole arc, because it stopped us from deploying a mirage.

---

## 0. TL;DR
- The frictionless conviction portfolio looked spectacular: **+931% / Sharpe 7.28**. It is **fake.**
- **The dispositive tell:** a *no-signal random* basket scored **Sharpe 9.68** — higher than the
  signal. When your random baseline has Sharpe ~10, you're measuring an artifact, not skill.
- **Root cause (three artifacts):** (1) untradeable tail — the top-10 of 885 trades = **40% of all
  P&L**, and they're warrants (PIIIW, TALKW, USGOW) and penny rockets (WOK +540%, TDIC +453%) you
  cannot fill at size; (2) **frictionless fills** — you can't sell 8 thin small-caps at the close
  price; (3) a **hot regime** — May 2026 mean +8.7% dominates the sample.
- **Brutal-realism floor** (warrants out, +40% upside cap, 2× slippage, $1 floor): per-trade
  **median net = −1.60%** (the typical tradeable trade loses), and the signal portfolio (+67%)
  **does not beat random (+79%).** Within 38 sessions of noise, **P(continue) shows no reliable
  deployable edge over random selection net of costs.**
- **What survives:** the CPCV result is still true — **P(continue) genuinely predicts 30-min
  continuation (AUC 0.768).** But a real *prediction* is not a deployable *alpha*. The research
  warned of exactly this ("IC ~0.01 is statistically detectable but worthless after spreads").

---

## 1. The mirage (frictionless portfolio)
`scripts/backtest_conviction_portfolio.py` — top-8/day, 15% cap, conviction-weighted, hold-to-EOD,
price-bucketed cost, OOS P(continue), no-look-ahead entry (#3):

| selection | totalRet | meanDay | Sharpe | winDay | maxDD |
|---|---|---|---|---|---|
| P(continue) top-8 | +931% | +7.28% | 7.28 | 71% | −22% |
| MFCS top-8 | +508% | +5.59% | 6.21 | 61% | −9% |
| **no-signal top-8** | **+518%** | **+5.25%** | **9.68** | 66% | −21% |

**+7%/day and Sharpe 7-10 are physically impossible for a real strategy.** The no-signal baseline
being just as good is the proof: the "edge" is the *entry set + regime + frictionless fills*, not the
signal.

## 2. The diagnosis (why it's fake)
Raw hold_ret (entry#3 → EOD, 885 ticker-days): **mean +6.7% but median +0.9%, std 35%.**
- **Tail-driven & untradeable:** max +540%; top-10 trades = **40%** of summed P&L; they include
  **warrants** (`*W` tickers — structurally illiquid) and sub-$1 rockets. Winsorizing the upside at
  +20% collapses the mean from **+6.7% → +1.3%.**
- **Regime-loaded:** per-month mean — Feb +4.8%, Apr +2.9%, **May +8.7%** (514 of 885 days), Jun +9.7%.
  The sample is dominated by a hot small-cap-momentum month.
- **Frictionless fantasy:** "exit at the EOD close price" assumes you can dump 8 thin names at 15:25
  with no impact. On these names real exit slippage dwarfs the 0.4–4% bucketed cost.

## 3. The brutal-realism floor (the honest answer)
Warrants excluded, $1 price floor, capturable upside capped at +40% (you can't realistically bank a
+500% intraday on a name you're flattening at the close), **2× the bucketed slippage**:

| selection | totalRet | meanDay | Sharpe | winDay |
|---|---|---|---|---|
| P(continue) top-8 | +67% | +1.57% | 3.81 | 55% |
| MFCS top-8 | +66% | +1.48% | 4.54 | 50% |
| **no-signal top-8** | **+79%** | **+1.78%** | 4.18 | 55% |

Per-trade net: **mean +1.14%, median −1.60%.** The median tradeable trade **loses** after realistic
cost; the portfolio is positive only on the residual right tail + the hot regime — and **random
selection (+79%) edges out the signal (+67%).** With n=38 sessions this is all within noise, so the
honest claim is precise: **P(continue) provides no reliable edge over random selection once costs and
tradeability are real.**

## 4. What is actually true after all this
- ✅ **P(continue) is a real prediction** — AUC 0.768, CPCV-robust, calibrated. That stands.
- ❌ **It is not a deployable alpha** — neither as an exit trigger (doc 232: caps winners) nor as a
  selection/sizing signal (this doc: doesn't beat random net of cost).
- **Why a 0.768 AUC makes no money:** the profitable continuations are concentrated in untradeable
  names (warrants, sub-$1 spikers); among *tradeable* names the per-trade edge is smaller than the
  round-trip cost. This is the "statistically detectable, economically worthless after spreads"
  failure mode the doc-231 research explicitly flagged (Kinlay on Kronos' IC).
- **The pattern of the entire session holds:** every apparent edge — MFCS (variance), the multi-day
  "skyrocket" (split artifacts), now the conviction portfolio (untradeable tail + frictionless +
  regime) — **evaporates under honest scrutiny.** The one survivor (a calibrated prediction) doesn't
  convert to profit. The durable levers remain the boring structural ones (doc 230): execution
  integrity (kill phantom P&L), trade EARLY not late, FLATTEN at the close (never carry overnight),
  cut catastrophic losers.

## 5. Verdict & what would change it
**Do NOT deploy a conviction/exit strategy on this basis. No live change.** BET #1 produced a real
research artifact (a validated continuation predictor + a clean labeled corpus + a CPCV harness) and a
clear negative on deployable alpha — both worth having.

The bar to revisit is now explicit. A real edge would have to survive **all** of:
1. A **market-impact fill model** keyed to each name's actual intraday $-volume (not a flat bucket) —
   the single biggest missing realism.
2. **Tradeable universe only** (no warrants, a real $-volume floor, borrow/halt checks).
3. A **benchmark vs our ACTUAL live policy on the same days** — absolute returns are meaningless; the
   only question is "does it beat what we already did?"
4. **Out-of-regime** robustness (not just the May-2026 bonanza) and CPCV/PBO on the *policy*, not just
   the classifier.
5. Most promising reframe (if any): the model as a **risk filter** that vetoes the predicted-fade
   catastrophic losers, rather than an alpha source — tested for whether it improves the *loss tail*,
   which is where doc 230 said our real damage is (30% win × 0.98 W/L).

Until those clear, the honest position is: **the prediction is real, the alpha is unproven, and we
don't trade on it.**

**Initiator**: Claude Opus 4.8 (1M ctx). **Basis**: `scripts/backtest_conviction_portfolio.py` + the
diagnostic/brutal-realism passes, on the CPCV-validated OOS classifier. **Predecessors**: 232 (the
conviction-filter finding this deflates), 231 (the validated predictor + the research's spread-warning),
230 (execution is the lever), 213-215 (kill mirages under no-cap/CI — extended here to fills+regime).
