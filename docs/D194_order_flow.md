# D194: Order Flow Analysis — Institutional vs Retail Classification

## Summary

D194 adds a microstructure signal to the faller detection pipeline by analyzing
time-and-sales (T&S) data to detect institutional accumulation or distribution
before position entry.

The signal is **deterministic** — pure math on trade data, no LLM calls.

## Signal logic

### Trade direction classification (quote rule)

- Trade at or above ask → **BUY** (hitting the offer)
- Trade at or below bid → **SELL** (hitting the bid)
- Inside spread, above midpoint → lean **BUY**
- Inside spread, below midpoint → lean **SELL**

### Block trade detection

A trade is classified as a "block" if:
- `size >= 1,000 shares` **OR**
- `price × size >= $10,000`

Blocks dominating volume = institutional footprint.

### ISO (Intermarket Sweep Order) detection

Condition code `"F"` in the SIP feed indicates an ISO — an aggressive institutional
order sweeping multiple price levels simultaneously. High ISO ratio confirms smart-money
urgency.

### Autocorrelation

Lag-1 Pearson autocorrelation of the direction sequence (BUY=+1, SELL=-1):
- `> +0.2` → persistent flow (institutional momentum sweep)
- `< -0.2` → alternating (retail scalping or market-maker)

### Classification rules (priority order)

| Signal | Condition |
|--------|-----------|
| `INSTITUTIONAL_ACCUMULATION` | `net_flow_ratio >= 0.30` AND (`block_ratio >= 0.40` OR `iso_ratio >= 0.15`) |
| `INSTITUTIONAL_DISTRIBUTION` | `net_flow_ratio <= -0.30` AND `block_ratio >= 0.35` |
| `RETAIL_DOMINATED` | `block_ratio <= 0.15` AND `\|net_flow_ratio\| <= 0.20` |
| `MIXED` | everything else |
| `INSUFFICIENT_DATA` | fewer than 5 trades |

## Faller score adjustments

| Signal | Faller Δ | Interpretation |
|--------|----------|----------------|
| `INSTITUTIONAL_ACCUMULATION` | **-0.20** | Smart money buying → runner confidence ↑ |
| `INSTITUTIONAL_DISTRIBUTION` | **+0.20** | Smart money selling → fade risk ↑ |
| `RETAIL_DOMINATED` | **+0.10** | No institutional footprint → promotional risk |
| `MIXED` | 0.00 | Inconclusive |
| `INSUFFICIENT_DATA` | 0.00 | Neutral |

The adjustment is scaled by `cfg.weight_order_flow` (default `1.0`).
Set to `0` in `settings.py` to disable without touching `faller_detection.py`.

## Integration

### faller_detection.py

```python
assessment = detector.score(
    candidate, scored, indicators,
    sec_result=sec_result,
    short_interest_result=si_result,
    sentiment_velocity_result=sv_result,
    order_flow_result=of_result,   # NEW — D194
)
```

The result is stored in `FallerAssessment.order_flow_signal` and
`FallerAssessment.order_flow_adjustment` for arena replay.

### Alpaca integration (main.py)

```python
from src.data.order_flow import OrderFlowAnalyzer, trades_from_alpaca

analyzer = OrderFlowAnalyzer()
raw_trades = await alpaca_client.get_trades(ticker, lookback_minutes=5)
of_result = analyzer.analyze_trades(
    ticker,
    trades=trades_from_alpaca(raw_trades),
    bid=snapshot.bid_price,
    ask=snapshot.ask_price,
)
```

## Meta Arena results

Run `python scripts/run_meta_simulation.py` to replay all labeled scenarios
through the full D160–D194 pipeline and compare vs. historical outcomes:

```
=== META ARENA SIMULATION ===
Historical (actual):    18 trades, 0 wins, -$12,306 P&L
Optimized (simulated):  X trades, Y wins, $Z P&L
...
    D194 order flow:      detected N institutional accumulations
```

## Configuration

```toml
[faller]
weight_order_flow = 1.0   # Multiplier on raw adjustment (-0.20/+0.20/+0.10)
```

## Files

| File | Purpose |
|------|---------|
| `src/data/order_flow.py` | `OrderFlowAnalyzer`, `FlowSignal`, `TradeEvent`, `OrderFlowResult` |
| `src/execution/faller_detection.py` | D194 scoring block in `score()` |
| `config/settings.py` | `weight_order_flow` field in `FallerDetectionConfig` |
| `scripts/run_meta_simulation.py` | Full pipeline P&L replay across 509 scenarios |
| `tests/unit/test_order_flow.py` | 25+ unit tests |

## References

- D160: Faller Detection design
- D162: Threshold calibration
- D191: SEC EDGAR pre-fetcher
- D192: Short interest squeeze classification
- D193: Sentiment velocity (Hawkes process)
