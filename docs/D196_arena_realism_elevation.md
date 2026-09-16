# D196: Arena Realism Elevation to 9.5+

**Date:** 2026-04-05
**Branch:** `claude/fervent-ellis`
**Assessment delta:** 8.5 → 9.5+ (target)

## Overview

Five components that close the remaining gap between simulation and reality in the Meta Arena Simulator. Each addresses a specific inaccuracy that causes the simulator to over- or under-estimate P&L relative to live trading.

---

## Component 1: Minute-Bar Recording + Replay Engine (~1.0 point)

**File:** `src/data/bar_recorder.py`

### Problem
The meta simulation uses `max_gain_pct` and `max_drawdown_pct` summary statistics to compute P&L. This misses the actual shape of the price path: a stock that hit +10% then faded to -5% is recorded identically whether it hit +10% in minute 1 or minute 28, whether it faded slowly or crashed. The exit logic (trailing stop, early profit, tranches) produces very different P&L depending on the path.

### Solution
`BarSeries` stores the complete 1-minute OHLCV path and provides four replay methods:

| Method | Replays |
|--------|---------|
| `simulate_trailing_stop()` | D163 — 50% of gain trail, bar-by-bar |
| `simulate_early_profit()` | D164 — sell 50% at +0.5% within 2 min |
| `simulate_tranches()` | D165 — tranche exits at 3%, 6%, 10% |
| `simulate_observation_window()` | D170 — open candle green/stable check |

`BarRecorder` records bars during live sessions to `data/bar_recordings/YYYY-MM-DD/TICKER.json` and loads them for replay.

### Usage
```python
from src.data.bar_recorder import BarRecorder, BarSeries
from datetime import date

recorder = BarRecorder("data/bar_recordings")
series = recorder.load("BFRG", date(2026, 1, 15))
exit_price, minute = series.simulate_trailing_stop(entry_price=8.50)
hits = series.simulate_tranches(entry_price=8.50)
approved, reason = series.simulate_observation_window(minutes=15)
```

---

## Component 2: Dynamic Slippage Model (~0.3 points)

**File:** `src/execution/slippage_model.py`

### Problem
The meta simulation uses flat `ENTRY_SLIPPAGE_PCT = 0.005` (0.5%) for all stocks. This systematically underestimates slippage for the micro-cap / low-dolvol stocks that make up the bulk of the trade universe.

### Solution
`SlippageModel` tiers slippage by daily dollar volume:

| Tier | Daily Dollar Volume | Slippage Range |
|------|---------------------|----------------|
| High | ≥ $50M | 0.05–0.1% |
| Medium | $5M–$50M | 0.2–0.5% |
| Low | $1M–$5M | 1–3% |
| Micro | < $500K | 3–5%+ |

Formula: `slippage = spread/2 + (position_dollars / dolvol) × impact_coeff`

`estimate_stop_slippage()` applies a 2× urgency premium for stop fills, reflecting gap-through risk on illiquid stocks.

### Usage
```python
from src.execution.slippage_model import SlippageModel

model = SlippageModel()
entry = model.estimate_entry_slippage(position_dollars=5_000, daily_dollar_volume=800_000)
print(f"{entry.tier}: {entry.slippage_pct:.1%} slippage")
round_trip = model.estimate_total_round_trip(5_000, 800_000)
```

---

## Component 3: Historical Shortability Enrichment (~0.3 points)

**File:** `scripts/enrich_shortability.py`

### Problem
The `is_shortable` field in labeled scenarios defaults to `True`, allowing the simulator to take short positions on stocks that were never locatable. This overstates short-side P&L on micro-cap promotionals where the broker can't find borrows.

### Solution
Two-pass enrichment:
1. **Live API:** For current/active tickers, calls `GET /v2/assets/{symbol}` → `shortable AND easy_to_borrow`
2. **Heuristic fallback:** For delisted/unavailable stocks, applies rule: `price < $5 AND dolvol < $1M → not shortable`

### Usage
```bash
# Enrich all scenarios with live Alpaca data
python scripts/enrich_shortability.py --scenarios data/labeled_scenarios.json

# Preview changes without writing
python scripts/enrich_shortability.py --dry-run

# Heuristic only (no API calls)
python scripts/enrich_shortability.py --no-alpaca
```

---

## Component 4: Walk-Forward Validation (~0.2 points)

**File:** `scripts/run_meta_simulation.py` — `run_walk_forward()`

### Problem
The meta simulation reports metrics on the same data used to tune signal weights. This in-sample evaluation overstates expected live performance.

### Solution
`run_walk_forward()` sorts scenarios chronologically, trains on the first 75%, evaluates on the last 25%, and reports both alongside an **overfit ratio** (train_accuracy / test_accuracy > 1.5 = warning).

### Usage
```bash
python scripts/run_meta_simulation.py --walk-forward
python scripts/run_meta_simulation.py --walk-forward --train-ratio 0.80
```

### Output
```
WALK-FORWARD VALIDATION  (D196)
  Train set: 38 trades up to 2026-03-15  → accuracy=72%  P&L=+$12,450
  Test  set: 11 trades from 2026-03-15   → accuracy=64%  P&L=+$3,200
  Overfit ratio: 1.13  (OK)
```

---

## Component 5: Bootstrap Confidence Intervals (~0.2 points)

**File:** `scripts/run_meta_simulation.py` — `bootstrap_pnl_ci()`

### Problem
The simulator reports a point estimate of total P&L with no sense of how much it might vary from the historical sample size (typically 30–100 scenarios). With 49 trades, the noise on P&L is substantial.

### Solution
`bootstrap_pnl_ci()` resamples per-trade P&L 1000× with replacement to estimate the 95% confidence interval on total P&L. Requires `numpy`.

### Usage
```bash
python scripts/run_meta_simulation.py --bootstrap
```

### Output
```
Total P&L: $167,075  [95% CI: $142,300 — $191,850]
Improvement: +$14,401  [based on 49 simulated trades]
```

---

## Tests

**File:** `tests/unit/test_d196_realism.py` — 47 tests

| Class | Coverage |
|-------|----------|
| `TestBarSeriesTrailingStop` | Trail fires / doesn't fire / ratchet / edge cases |
| `TestBarSeriesEarlyProfit` | Triggered in window / missed / window boundary |
| `TestBarSeriesTranches` | All 3 hit / partial / none / empty |
| `TestBarSeriesObservationWindow` | Approved / red bar / below open / reversal |
| `TestBarSeriesDerivedMetrics` | max_gain / max_drawdown / price_at_minute |
| `TestBarSeriesSerialisation` | roundtrip dict / JSON / BarRecorder save-load / list_available |
| `TestSlippageModel` | High/medium/low/micro tiers / stop > entry / round-trip |
| `TestWalkForward` | Chronological sort / split ratio / overfit_ratio / keys / empty |
| `TestBootstrapCI` | Empty / CI ordering / constant / noisy > uniform / single / 90<95 |

---

## Files Changed

```
src/data/bar_recorder.py          NEW  (~280 lines)
src/execution/slippage_model.py   NEW  (~160 lines)
scripts/enrich_shortability.py    NEW  (~155 lines)
scripts/run_meta_simulation.py    MOD  (+140 lines: bootstrap, walk-forward, --bootstrap, --walk-forward flags)
tests/unit/test_d196_realism.py   NEW  (~370 lines, 47 tests)
docs/D196_arena_realism_elevation.md  NEW  (this file)
```
