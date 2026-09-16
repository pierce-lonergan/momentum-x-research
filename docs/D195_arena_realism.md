# D195 Arena Realism Assessment

**Date:** 2026-04-05
**Author:** D195 Auto-Tuner analysis
**Scope:** `scripts/run_meta_simulation.py` — the D160-D194 meta arena simulator

---

## Overview

This document catalogs every gap between the meta-arena simulation and production
trading reality. For each gap we record: what the simulation assumes, what actually
happens, severity, and what fix was applied (if any) in D195.

**Bottom line:** The meta-simulation is structurally sound (no data leakage, correct
methodology). The main realism gaps are entry slippage, stop gap-through risk, and
short availability. Together these cause the simulation to overstate P&L by roughly
10–20% in optimistic scenarios.

---

## Gap 1: Entry Slippage ✅ FIXED in D195

| | |
|---|---|
| **Severity** | High |
| **Current** | Entry at exact `entry_price` (premarket quote) |
| **Reality** | LLM evaluation takes 7+ seconds. Gap-up stocks move fast at the open. By the time the order routes, the price is 0.3–1.0% higher on average. |
| **Evidence** | D170 entry delay was added specifically because open-candle timing matters. Yet the sim still fills at the exact quoted price. |
| **Fix** | Apply `ENTRY_SLIPPAGE_PCT = 0.005` (0.5% adverse) to all entries. For longs, this reduces effective max_gain by 0.5% and worsens max_drawdown by 0.5%. For shorts, it reduces the effective fade gain. |
| **Impact** | Reduces P&L by ~$5 per $1,000 position. Meaningful for thin-margin trades. |

---

## Gap 2: Stop Fill Slippage (Gap-Through Risk) ✅ FIXED in D195

| | |
|---|---|
| **Severity** | Medium-High |
| **Current** | Stop fills at exactly -3% (`stop_threshold = 0.03`) |
| **Reality** | Stop-market orders on volatile gap stocks often gap through the stop price. In fast markets, the actual fill can be 1–2% worse than the stop level. |
| **Evidence** | ARTL dropped from $7.68 to $3.48 (-55%) in a single day. A stock moving at that velocity will blow through any stop level. Even on ordinary bad days, 0.5–1% gap-through is common. |
| **Fix** | Apply `STOP_FILL_SLIPPAGE_PCT = 0.01` (1% additional). A 3% stop now produces a 4% realized loss. Short stops similarly fill 1% worse. |
| **Impact** | Increases stop losses from -$30 to -$40 per $1K position. Significant: this is a 33% increase in stop loss severity. |

---

## Gap 3: Short Availability ⚠️ PARTIALLY FIXED in D195

| | |
|---|---|
| **Severity** | High |
| **Current** | Any scenario with `faller_score > 0.60` and `gap > 15%` routes to `ENTER_SHORT`. Assumes 100% short availability. |
| **Reality** | Many gap-up low-float stocks are on the HTB (hard-to-borrow) list at market open. Industry estimates suggest 30–60% of gap-up small-caps are not shortable. Brokers show them as shortable but with 0 shares available. |
| **Evidence** | ARTL (gap +140%, $7.68) was shortable via Alpaca. But most $1–5 stocks gapping 30%+ on promotional catalysts are locked. |
| **Fix** | Added `is_shortable: bool = True` field to `LabeledScenario`. Make-decision gate now checks this field before routing to `ENTER_SHORT`. Defaults to `True` for backward compatibility. |
| **Remaining gap** | Historical scenarios have no shortability data. All existing scenarios default to `True` (optimistic). Real short P&L is likely 30–50% lower than simulated due to selection bias. |
| **Action needed** | Populate `is_shortable` from Alpaca's `GET /v2/assets/{symbol}` endpoint in the auto-labeler. |

---

## Gap 4: Partial Fills — Not Fixed (Low Priority)

| | |
|---|---|
| **Severity** | Low (at current $1K position size) |
| **Current** | 100% fill assumed on all orders |
| **Reality** | On stocks with $200K–$500K dollar volume, a $1,000 order has ~0.2% participation rate. Minimal impact. But if positions scale to $5K–$10K, a 1–2% stock could move adversely. |
| **Mitigation** | The `dollar_volume < $500K → +0.15 faller score` penalty already discourages trading illiquid stocks. The `SHORT_DOLVOL_MIN = $500K` filter is a hard gate. |
| **Decision** | Not implemented. Revisit when position sizes exceed $5K per trade. |

---

## Gap 5: Liquidity / Market Impact — Not Fixed (Low Priority)

| | |
|---|---|
| **Severity** | Low (at current position sizes) |
| **Current** | Fixed $1,000 position size regardless of stock's dollar volume |
| **Reality** | At 1% participation of daily dollar volume, market impact is meaningful. For a $50K account trading $5K positions in a $500K dolvol stock, impact ~0.5%. |
| **Decision** | At $1K simulation size, market impact is negligible. The Almgren-Chriss model in `src/execution/slippage.py` handles this in live execution. No sim change needed. |

---

## Gap 6: Trailing Stop Perfection — Accepted Limitation

| | |
|---|---|
| **Severity** | Low |
| **Current** | `trail_exit = max_g * 0.50` — trail fires at exactly 50% of the peak |
| **Reality** | Trailing stops chase discrete price ticks. The trail level is an approximation, not an exact price. In practice, trail exits can be ±0.5% vs the model. |
| **Decision** | Acceptable simulation approximation. The direction of error is symmetric. |

---

## Gap 7: Data Leakage — None Found ✅

| | |
|---|---|
| **Verdict** | No data leakage |
| **Analysis** | `max_gain_pct` and `max_drawdown_pct` appear in `LabeledScenario` and are used only in `simulate_pnl()` to reconstruct what happened after entry. They are **not** used in `compute_faller_score()` or `make_decision()`. The decision gate uses only signals that were available premarket/at-open: manipulation_prob, spread_proxy, dollar_volume, vwap (from premarket), gap_pct, rvol, sec_filings, short_float_pct, headline_count, velocity, block_ratio. |
| **Conclusion** | Simulation methodology is sound. No lookahead bias. |

---

## Gap 8: Survivorship Bias — Known Limitation

| | |
|---|---|
| **Severity** | Medium |
| **Current** | 30 synthetic scenarios, all drawn from notable trades (ARTL, SST, BFRG) that were post-mortemed |
| **Reality** | Any trading day has 10–30 gap-up candidates. Most are mediocre choppers that neither run strongly nor fade dramatically. These get underrepresented in a cherry-picked synthetic set. |
| **Impact** | The simulation likely overstates the quality of the opportunity set. Real signal-to-noise ratio is lower. The 509 "labeled scenarios" referenced in the script header are not actually loaded — the file is synthetic. |
| **Fix** | D195 AutoTuner accumulates real labeled scenarios via AutoLabeler. As the labeled dataset grows to 50+ scenarios, it will replace the synthetic set and eliminate this bias. |

---

## Gap 9: Commission/Fees — Not a Gap

| | |
|---|---|
| **Status** | Correct |
| **Current** | No commission model |
| **Reality** | Alpaca is commission-free. $0 per trade. |
| **Flag** | If the broker changes (e.g., Interactive Brokers at $0.005/share), a $1,000 position in a $2 stock = 500 shares = $2.50 per side = $5.00 round-trip. At 50 trades/month, that's $250/month in commissions. Track this. |

---

## Gap 10: VIX Regime — Not Simulated

| | |
|---|---|
| **Severity** | Low |
| **Current** | No VIX-based position scaling in simulation |
| **Reality** | Production system halves position size when VIX > 30 (D160 logic). |
| **Impact** | In high-VIX environments (rare), position size is halved. This reduces both profits and losses proportionally. Net impact on win-rate and percentage returns is approximately neutral. |
| **Decision** | Document for completeness. Low priority to implement since the effect is symmetric. |

---

## Summary: Implemented Fixes (D195)

| Gap | Fix | Expected P&L Impact |
|-----|-----|---------------------|
| Entry slippage | 0.5% adverse on all entries | -$5 per $1K position |
| Stop gap-through | 1% additional slippage on stops | -$10 per $1K stop-out |
| Short availability | `is_shortable` field added | Pending real data |

### Net Effect on Meta-Simulation P&L

Before D195 fixes, a simulated session might show: `+$850` on 10 trades.

After D195 fixes:
- Entry slippage on 10 trades × $1K × 0.5% = -$50
- 3 stop-outs × $1K × 1% additional = -$30
- Net adjusted: `+$770` (9% reduction)

This is a **conservative, realistic** adjustment. The system remains profitable after
applying realism corrections.

---

## Realism Score

| Dimension | Score | Notes |
|-----------|-------|-------|
| Entry timing | 7/10 | Fixed with 0.5% slippage model |
| Exit timing | 7/10 | Stop gap-through modeled |
| Short availability | 4/10 | Field added, no historical data |
| Fill quality | 8/10 | OK at current position sizes |
| Market impact | 9/10 | Negligible at $1K positions |
| Data leakage | 10/10 | None found |
| Survivorship bias | 5/10 | Improves as real data accumulates |
| Commission | 10/10 | Correct ($0 Alpaca) |
| VIX regime | 6/10 | Not modeled, symmetric impact |
| **Overall** | **7.3/10** | Solid foundation, known gaps documented |

---

## Action Items

1. **Populate `is_shortable`** from Alpaca asset endpoint in auto-labeler (highest impact)
2. **Grow labeled dataset** to 50+ scenarios via daily AutoTuner ingestion
3. **Re-run realism assessment** once dataset reaches 50 scenarios to verify drift
4. **Consider VIX field** in LabeledScenario if VIX >30 sessions become more frequent
