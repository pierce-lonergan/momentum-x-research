# D199: Tiered Stock Universe — Mid-Cap Catalyst Expansion

## Problem Statement

Every stock traded in MOMENTUM-X to date has been a low-float gap-up: $1–$5 price, tiny dollar volume, extreme RVOL, promotional schemes. **0% win rate.** These are the hardest stocks to trade profitably.

The system was designed for them, but their characteristics work against consistent profitability:
- No institutional ownership → no price support after the gap
- Illiquid → 5–10% spreads eat every entry
- Promotional → designed to be sold to retail, not to hold

## Solution: CATALYST Tier

Mid-cap stocks ($1B–$50B market cap) gapping on real earnings beats, FDA approvals, or contract wins behave fundamentally differently:

| Property | MOMENTUM (Low-Float) | CATALYST (Mid-Cap) |
|---|---|---|
| Market cap | < $1B | $1B–$50B |
| Price range | $1.50–$50 | $5–$200 |
| Gap threshold | 5%+ | 3%+ |
| RVOL threshold | 2x | 1.5x |
| Dollar volume | $2M+ | $10M+ |
| Gap driver | Promotional pump | Real catalyst |
| Institutional ownership | Minimal | Significant |
| Price support | None | Institutional bids |
| Short selling | Enabled (D161) | Disabled |
| Faller rejection | 0.60 | 0.70 (more permissive) |
| Observation window | 15 min | 5 min (moves fast on real news) |

## Architecture

### Files Changed

| File | Change |
|---|---|
| `src/data/universe_tiers.py` | New — `UniverseTier`, `TierConfig`, `TIER_CONFIGS`, `UniverseClassifier` |
| `config/settings.py` | New — `UniverseConfig`, wired into `Settings.universe` |
| `src/scanners/premarket.py` | CATALYST parallel filter path in `scan_premarket_gappers()` |
| `src/core/scan_loop.py` | Passes `universe_config` to `scan_premarket_gappers()` |
| `main.py` | Tier classification + parameter overrides at BUY verdict evaluation |
| `tests/unit/test_d199_universe_tiers.py` | 11 unit tests |

### Classification Logic

`UniverseClassifier.classify()` checks CATALYST first (higher quality), then MOMENTUM:

```
CATALYST: mcap $1B-$50B AND price $5-$200 AND gap≥3% AND rvol≥1.5x AND dolvol≥$10M
MOMENTUM: price $1.50-$50 AND gap≥5% AND rvol≥2x AND dolvol≥$2M
UNKNOWN:  doesn't fit either active tier
```

Unknown market cap → only MOMENTUM path (mid-cap classification requires confirmed mcap).

### Scanner Integration

`scan_premarket_gappers()` now accepts `universe_config: UniverseConfig | None`:

```python
combined_filter = _momentum_filter | _catalyst_filter   # OR — both tiers run
filtered = enriched.filter(combined_filter)
```

This extends the price ceiling from $50 to $200 for CATALYST stocks and lowers the gap/RVOL thresholds for them. Market cap is sourced from Alpaca snapshots (already populated in `CandidateStock.market_cap`).

### Evaluation Loop Integration (main.py)

At every BUY verdict, before the D160 faller gate:

1. **Classify** the candidate into its tier using `UniverseClassifier.classify()`
2. **If CATALYST**: set `_d199_faller_threshold_override = 0.70` and `_d199_short_enabled_override = False`
3. **Faller gate**: if score is between 0.60 and 0.70 AND tier is CATALYST, override `_effective_reject = False`
4. **Short path (D161)**: CATALYST stocks bypass short evaluation (`_d199_short_allowed = False`)
5. **D170 observation**: logs that CATALYST prefers 5min window (full per-candidate override is future work)

## Configuration

`settings.universe` (env prefix `UNIVERSE_`):

```
UNIVERSE_MOMENTUM_ENABLED=true       # Enable/disable MOMENTUM tier
UNIVERSE_CATALYST_ENABLED=true       # Enable/disable CATALYST tier
UNIVERSE_CATALYST_GAP_MIN_PCT=0.03   # 3% minimum gap for CATALYST
UNIVERSE_CATALYST_RVOL_MIN=1.5       # 1.5x RVOL for CATALYST
UNIVERSE_CATALYST_MARKET_CAP_MIN=1000000000   # $1B floor
UNIVERSE_CATALYST_DOLLAR_VOLUME_MIN=10000000  # $10M floor
UNIVERSE_CATALYST_PRICE_MAX=200.0    # $200 price ceiling
```

## Trade-offs and Limitations

1. **Market cap from Alpaca**: Alpaca's snapshot endpoint returns `market_cap` for most stocks but not all. When `market_cap=None`, the stock can only be classified as MOMENTUM. This is conservative — we never accidentally treat an unknown stock as institutional quality.

2. **D170 observation window**: The CATALYST tier prefers a 5-minute observation window vs the 15-minute global default. The `EntryDelayManager` currently uses a single global window — per-candidate overrides require a future extension. Current behavior: CATALYST stocks get the 15-minute window with a log noting the mismatch.

3. **Stop distance override**: `TierConfig.stop_distance_pct = 0.08` for CATALYST is logged but not yet wired into the execution layer stop calculation (which uses the global `execution.stop_loss_pct`). Future work: bridge execution stop to tier config.

## Log Markers

| Marker | Meaning |
|---|---|
| `D199 CATALYST TIER: TICKER mcap=$X.XB gap=Y% rvol=Zx` | Stock classified as CATALYST |
| `D199 CATALYST OVERRIDE: TICKER faller_score=0.63 < CATALYST threshold=0.70` | Faller reject overridden |
| `D199-CATALYST FALLER GATE BLOCKED: TICKER (score=0.72 > 0.70)` | CATALYST reject at 0.70 |
| `D199 CATALYST OBS: TICKER would prefer 5min window (global=15min)` | Observation override logged |
| `D199 MOMENTUM TIER: TICKER (price=$X.XX, mcap=$YM)` | Stock stays on original path |
