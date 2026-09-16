# 242 — BET#3 step 1: the TAPE adds rocket signal. First feature improvement that points the right way.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "start the audit and build it." (Acquire the rocket-vs-fader discriminating features
doc 241 said we lack, and test whether they crack the precision wall.)

## Audit — `trades_v1` (451 GB raw tape)
- Partitioned **year/month/day/ticker** → one ticker-day's trades read via its exact partition path (no
  451GB scan); ~93% partition coverage on covered days.
- **Trades-only (no quotes)** → OFI via the **tick-rule** (uptick=buy/downtick=sell signed volume); true
  book-imbalance OFI would need L2 quotes we don't have.
- **Coverage: 2025-08 → 2026-04 (~9 months)** → a *within-window* test; full cross-regime needs more history.

## Built — ex-ante tick-feature layer (`scripts/build_tick_features_doc242.py`)
Per tick-covered gapper ticker-day, premarket(04:00-09:30)+9:30-9:50 only (ex-ante at the 9:50 entry):
tick-rule OFI (full + late-half), premarket volume/$-vol, large-print (≥5k sh, block) ratio, trade
intensity, mean trade size, tick-VWAP distance, odd-lot ratio. → `data/research/rocket_tick_features.parquet`
(1,783 ticker-days with ≥3 early trades; the ~49% dropped are illiquid <3-trade names — a liquidity skew
toward the tradeable ones, which is fine).

## THE RESULT — tick microstructure adds rocket-vs-fader discrimination
Rocket-classifier (GBM, class-balanced), CPCV OOS within 2025-08..2026-04, bootstrap 95% CI on the
AUC lift (micro+tick − micro-only), across rocket definitions:
| rocket def | rockets | micro-only AUC | micro+TICK AUC | TICK-only | **lift (95% CI)** |
|---|---|---|---|---|---|
| eod ≥ +20% | 73 | 0.635 | 0.689 | — | **+0.054 [+0.002, +0.104]** ✓ excl 0 |
| eod ≥ +30% | 34 | 0.612 | 0.681 | — | **+0.070 [+0.007, +0.137]** ✓ excl 0 |
| +30% & sustained | 33 | 0.598 | 0.662 | 0.636 | +0.065 [−0.005, +0.136] (edge) |
- **Consistent +0.05–0.07 AUC lift; CI excludes 0 at the eod≥20%/30% labels** (borderline at the strictest
  small-n sustain cut). **TICK-only (0.636) > OHLCV-micro-only (0.598)** — the tape carries *more* rocket
  signal than 5-min OHLCV. Top-slice (noisy, ~4 rockets/slice): micro+tick top-2% precision **0% → 11%**
  (≈ the ~12% break-even bar), top-slice return **−2.5% → +3.1%**.
- **This is the first time in the whole arc that adding features improved discrimination AND cleared
  significance.** The doc-241/doc-187 hypothesis — the rocket-vs-fader discriminator lives in the
  microstructure/tape, not the 5-min OHLCV — is *supported*.

## Honest limits (what this is NOT, yet)
1. **Underpowered & within-window:** 9-month tick coverage, 34–73 rockets; the lift is *borderline*
   (edge-of-significance under the strictest label). Not a cross-regime (2024/2025-H1) test.
2. **Tick-rule OFI is a proxy** for the doc-187-validated *book-imbalance* OFI (which needs L2 quotes) —
   the true signal is likely *stronger* than what we measured.
3. **AUC ≠ P&L** (the iron law): the top-slice median is still ~0/negative; ranking-lift hasn't been shown
   to convert to a positive *traded-slice return cross-regime*. That remains the binding gate.
4. Liquidity skew (illiquid <3-trade names dropped) — fine for tradeability, but note it.

## Verdict & the justified investment
**Green shoot, not a confirmed edge.** For the first time there is a real, significance-clearing signal to
amplify and a concrete data path to confirm it. The investment doc 241 called for is now *justified by
evidence*:
1. **More tick history** — acquire 2024 + 2025-H1 + 2026-05+ trades (Polygon flat-files) → 3–4× more
   rockets → power for a **cross-regime, pre-registered** rocket test (decision = realized traded-slice
   return positive in ALL regimes, not AUC).
2. **L2 QUOTE data** — enables *true* OFI / real-vs-hollow bid-wall / VWAP-reclaim (doc-187's validated,
   stronger discriminators). The single highest-expected-value feature add.
3. **Float + premarket/float-rotation + kappa-validated catalyst** — the remaining doc-187 discriminators.
Then re-run the rocket-classifier pre-registered cross-regime; if the traded-slice return is positive in
every regime, BET#3 becomes the SOTA rocket-finder. Honest prior: still hard, but this is the first result
that earns the next investment instead of closing.

**No live change.** **Basis**: `scripts/build_tick_features_doc242.py` (1,783 tick-covered gapper
ticker-days, 2025-08..2026-04). **Predecessors**: 241 (rockets rankable but feature-gated), 187 (the
microstructure discriminators), 235 (the AUC≠P&L / cross-regime bar this must still clear).
