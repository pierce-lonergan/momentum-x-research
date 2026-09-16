# D192: Short Interest Data Integration — SQUEEZE Archetype

## Problem

The trading system had no awareness of short interest data. Stocks with
>30% short float that gap up on a catalyst are qualitatively different from
promotional pump-and-dumps. The forced covering mechanics create a self-
reinforcing feedback loop that can sustain a gap far beyond what fundamentals
justify. Without short interest data:

- The system could short a squeeze, entering directly into forced covering
- Stop widths were sized for normal mean-reversion, not squeeze mechanics
- Faller scores penalized these stocks despite their structural gap persistence

## Solution

Multi-tier short interest pipeline with SQUEEZE archetype classification:

```
SQUEEZE = short_float_pct > 30% AND gap_pct > 5%
HIGH_SHORT = short_float_pct > 20%
NORMAL = short_float_pct < 20%
```

## Architecture

### 4-Tier Data Pipeline (`src/data/short_interest.py`)

| Tier | Source | Method | Latency | Reliability |
|------|--------|--------|---------|-------------|
| 1 | Alpaca Market Data V2 | REST `v1beta1/stocks/{ticker}/short-interest` | ~5ms | Requires paid plan |
| 2 | yfinance | `Ticker.info["shortPercentOfFloat"]` | ~2-10s | High (Yahoo Finance) |
| 3 | Finviz scraping | BeautifulSoup DOM parse + anti-scraping headers | ~1-2s | Medium (rate limits) |
| 4 | FINRA Query API | Stub (requires developer registration) | TBD | Institutional grade |

The first tier that returns a non-None `short_float_pct` wins. Results are
cached for 4 hours (short interest is bi-monthly; re-fetching intraday adds
no information).

### SQUEEZE Archetype Overrides

When `classification == SQUEEZE`, the following rules apply:

1. **Block shorting** — `assessment.shorting_blocked_by_squeeze = True`. The
   orchestrator must check this flag before routing to the short path. Shorting
   into forced covering is guaranteed to lose.

2. **Reduce faller score by 0.30** — Gap persistence is structural (shorts are
   mechanically forced to buy), not promotional. The faller gate's bearish
   signals assume a pump-and-dump model that doesn't apply to squeezes.

3. **Widen trailing stops** — Squeezes produce violent intraday extensions.
   Multiply stop distance by 3-4× ATR (implemented at the position level).
   *(Stop widening is a configuration concern — set via TrailingStopConfig.)*

4. **Accelerate profit-taking** — D164 early profit take fires at T+1min instead
   of T+2min for SQUEEZE stocks. The squeeze peak is sharp; take partial
   profits earlier. *(Implemented at the orchestrator level using squeeze_classification.)*

### HIGH_SHORT Caution

When `classification == HIGH_SHORT` (20-30% short float):

- Block shorting (don't enter a short against a potential squeeze setup)
- Reduce faller score by 0.15 (half the SQUEEZE reduction)
- Standard trailing stop widths (no mechanical squeeze yet)

## Key Concepts

### Short Interest vs Short Volume

**Only bi-monthly consolidated short interest is used.** Daily "short volume"
(e.g., from Nasdaq/FINRA daily files) is mostly market-maker hedging activity
and is not predictive of squeeze potential. The bi-monthly FINRA consolidated
data reflects actual short positions held by institutions.

### Data Latency Is Acceptable

Institutional shorts hold positions for **weeks to months**. 2-3 week old
FINRA short interest data is sufficient for the >30% threshold decision. The
question is "is this stock heavily shorted?" — that doesn't change week to week
for structural short targets.

### Why Short Float %, Not Short Shares?

Short shares alone is meaningless without the denominator (float). A stock with
5M shares short on a 10M share float is 50% short — extremely squeeze-prone.
The same 5M shares short on a 500M share float is 1% — irrelevant.

`short_float_pct = short_shares / float_shares × 100`

## Integration

### `src/execution/faller_detection.py`

`FallerRiskDetector.score()` now accepts an optional `short_interest_result`
parameter:

```python
from src.data.short_interest import ShortInterestProvider, SqueezeClassification

provider = ShortInterestProvider(alpaca_api_key=..., alpaca_secret=...)
si_result = await provider.get_short_interest(candidate.ticker)
si_result.classification = provider.classify(si_result, gap_pct=candidate.gap_pct)

assessment = detector.score(candidate, scored, indicators,
                            sec_result=sec_result,
                            short_interest_result=si_result)

if assessment.shorting_blocked_by_squeeze:
    # Do NOT route to short path
    continue
```

`FallerAssessment` has two new fields:
- `squeeze_classification: str` — the SqueezeClassification value
- `shorting_blocked_by_squeeze: bool` — True for SQUEEZE or HIGH_SHORT

### `config/settings.py`

Two new fields on `FallerDetectionConfig`:
- `weight_squeeze_faller_reduction: float = 0.30`
- `squeeze_gap_threshold: float = 0.05`

## Tests

14 tests in `tests/unit/test_short_interest.py`:

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_alpaca_short_interest_fetch` | Tier 1 HTTP 200 → short_shares populated |
| 2 | `test_yfinance_fallback` | Tier 2 info dict → short_float_pct computed |
| 3 | `test_finviz_fallback` | Tier 3 HTML parse → short_float_pct extracted |
| 4 | `test_tier_cascade_alpaca_fails` | Tier 1 returns None → Tier 2 called |
| 5 | `test_all_tiers_fail` | All tiers None → UNKNOWN result |
| 6 | `test_squeeze_classification_above_30` | 35% + 45% gap → SQUEEZE |
| 7 | `test_high_short_classification_above_20` | 25% + 3% gap → HIGH_SHORT |
| 8 | `test_normal_classification_below_20` | 10% → NORMAL |
| 9 | `test_squeeze_blocks_shorting` | assessment.shorting_blocked_by_squeeze=True |
| 10 | `test_squeeze_widens_faller_score` | Faller score reduced by 0.30 |
| 11 | `test_cache_hit` | Second call uses cache (fetch called once) |
| 12 | `test_batch_fetch` | Multiple tickers → all returned |
| 13 | `test_itrm_scenario` | Full pipeline: yfinance → classify → SQUEEZE |
| 14 | `test_finviz_anti_scraping_headers` | Finviz request has User-Agent + Referer |

## Files Changed

```
src/data/short_interest.py          — New: 4-tier pipeline, ShortInterestProvider
src/execution/faller_detection.py   — Updated: score() accepts short_interest_result
config/settings.py                  — Updated: squeeze weights on FallerDetectionConfig
tests/unit/test_short_interest.py   — New: 14 mocked tests
docs/D192_short_interest.md         — This document
```
