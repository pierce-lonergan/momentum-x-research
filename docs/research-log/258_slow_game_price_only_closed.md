# 258 — The slow game, first bricks: price/volume is closed in BOTH corners (lottery = uncapturable, liquid = efficient). The only door left is information synthesis.

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — "full send, run it" — the new game's first test: does the one real signal (the candidacy/dilution signature) pay on a SLOWER horizon where the spread can't eat it?

## TL;DR
Two probes, decisive together. **(1)** The candidacy/dilution signal on a **multi-day gapper hold** *looks* like a cross-regime edge (FRESH names +6/+5/+24% at 10d, long-short net +2.5/+8.9/+8.5%) — but **dies on the hostile winsorize/median check**: the median FRESH gapper *loses*, win-rate < 50%, winsorizing flips it negative, top-10% of trades carry 175-259% of P&L. **Same fat-tail lottery, just slower** — the signal clusters the rockets but clustering isn't capturable. **(2)** A **non-lottery liquid universe** (5,825 tickers, 1.6M stock-days) cross-sectional **momentum** factor has *sane medians* but is **~flat and regime-flipping** (L-S median +0.36/+0.64/−0.26% by year — it inverts to reversal in 2026): **efficient, competed away.** Net: **there is no median-positive, cross-regime-robust price/volume edge in either corner** — the lottery is uncapturable, the liquid is arbitraged. The only un-falsified lever is **information synthesis** (a different build, not a price backtest), where our actual structural advantage lives.

## Probe 1 — multi-day candidacy/dilution (the slow version of the one real signal)
Gap-up candidacy universe (10,391 stock-days), enter at gap-day close, hold 10-20 days. The dilution finding *holds at the mean*: **FRESH** (no recent reverse-split, not exhausted) names drift up, **BEARISH** (reverse-split or exhausted prior-runner) fade.
| 10-day fwd mean | FRESH | BEARISH |
|---|---|---|
| 2024 / 2025 / 2026 | +6.2 / +5.0 / +23.7% | +2.5 / −5.1 / +14.0% |
Long(FRESH)/short(BEARISH) net of cost is positive in all 3 regimes (+2.5/+8.9/+8.5%, CI-separated in 2025). **But the hostile check kills it** (FRESH long-only, 10-day):
| | mean | **median** | win% | **winsor@+30%** | top-10% share |
|---|---|---|---|---|---|
| 2024 | +6.1% | **−1.5%** | 46% | **−2.0%** | 181% |
| 2025 | +4.9% | **−4.2%** | 40% | **−4.8%** | 259% |
| ALL | +7.6% | **−1.8%** | 45% | **−2.7%** | 175% |
The +mean is **entirely a handful of monsters** (median loses, win < 50%, winsorize flips negative, top-10% carries 175-259% of P&L). Only 2026 (thin/favorable) has a positive median. **It's the gapper lottery on a 10-day clock** — the candidacy signal correctly says *where the rockets cluster* (FRESH > BEARISH, the dilution-distribution finding is real and replicates), but **clustering is not a capturable trade.** The 6th false-positive the winsorize/median discipline has caught this session.

## Probe 2 — non-lottery liquid momentum (does a median edge exist anywhere in price?)
Liquid universe ($5-$500, ADV≥$5M, ETFs excluded): 5,825 tickers, 1.6M stock-days. Cross-sectional 60-day momentum → forward 20-day return. The medians are now **broad and sane** (win-rates ~50%, not tail-dominated), so the verdict is clean:
| L-S (top−bottom mom), 20-day | 2024 | 2025 | 2026 | ALL |
|---|---|---|---|---|
| mean | −0.26% | +0.51% | −1.61% | +0.06% |
| **median** | +0.36% | +0.64% | **−0.26%** | +0.52% |
The momentum factor is **~flat and regime-dependent** — ±0.5%/20d that *inverts to reversal in 2026*. The well-known factor is **competed away** on liquid names. Efficient.

## The decisive conclusion
**Price/volume alone has no robust, capturable, cross-regime edge for us — in either corner:**
- **Lottery corner** (gappers, low-float): the fat right tail is real but **ex-ante random + uncapturable** (median loses at every horizon — intraday docs 250-252, multi-day this doc).
- **Liquid corner** (large/mid-cap factors): broad and median-sane but **efficient/arbitraged** (momentum ≈ 0, regime-flipping).
We have now proven it from *both* ends. The reason is structural (doc-257 thesis): we have **no speed edge** (lose to HFT in the liquid corner) and **no capture edge** (taker paying spread/borrow in the lottery corner). Price is efficient exactly where we can reach it.

## The only door left — and it's a different build
Per the structural-edge thesis (doc 257 §"where's the game"): our genuine asymmetries are **patience + information synthesis at scale + small size**. The only un-falsified lever — and the one place we *did* find real signal (the candidacy/dilution signature is ex-ante observable in *filings/structure*, doc 253) — is **information synthesis**: an LLM reading 10-Ks/8-Ks/trial registries/FDA calendars/transcripts to **handicap catalysts and fundamentals better than consensus**, on slow horizons, in capacity-constrained names, sized by conviction with the candidacy/dilution signal as the *risk filter*. **This is NOT a price-only backtest** — it requires building the fundamental/event + LLM-handicapping layer. It is the only game aligned with what we are.

## Honest status
**No live change.** The price-only door is closed cleanly from both corners — a complete, rigorous negative that ends the search for a *price* edge. The durable assets stand: the data warehouse, the agent swarm, and the **falsification engine** that has now caught six false positives this session and converged the same answer from a dozen angles. The genuine forward bet is the **information-synthesis** game (a real build, modest realistic prize, but the only one where our advantage is structural) — or to recognize the research engine itself as the asset and point it at a domain where information synthesis creates value.
**Basis**: `scripts/multi_day_candidacy_signal_doc258.py` + the liquid-momentum probe (day_aggs, 1.6M stock-days). **Predecessors**: 250-252 (intraday price closed), 253-254 (the candidacy signal is a watchlist not a predictor), 257 (the structural-edge thesis). **Memory**: [[gapper-universe-no-edge]].
