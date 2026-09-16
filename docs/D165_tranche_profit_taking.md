# D165: Price-Based Tranche Profit-Taking

## Why Tranches Were Broken

### Root Cause 1: Missing Phase 3 Price-Check Loop

The original tranche system submitted Alpaca **limit sell orders** at entry time and monitored them via WebSocket fills. There was **no fallback** that directly checked whether the current market price had crossed a target level. If limit orders weren't submitted or didn't fill, tranches simply never fired — silently.

### Root Cause 2: D100 OTO Conversion Blocked Non-Shortable Stocks

Alpaca represents bracket orders (OTO) as a single atomic unit. Submitting partial sells while the OTO stop is active returns `422 "cannot be sold short"` for non-shortable stocks like SST and ARTL. D147 (shortability check) fixed the stop protection issue but left tranches completely disabled for those stocks.

### Root Cause 3: Wrong Target Percentages

D142 set targets at **+1% / +2.5% / +5%** — too tight for gap-momentum stocks. ARTL and SST both ran +6–7% intraday before reversing, but neither reached the original targets after accounting for all the above. D165 raises them to **+3% / +6% / +10%**.

### Forensic Evidence (March 30, 2026)

| Ticker | Entry | Intraday High | Gain | D142 T1 | D165 T1 | T1 Fired? |
|--------|-------|---------------|------|---------|---------|-----------|
| ARTL   | $7.68 | $8.22         | +7.0% | $7.76 | $7.91 | ❌ 0 tranches |
| SST    | $3.20 | $3.39         | +5.9% | $3.23 | $3.30 | ❌ 0 tranches |

**With D165**: ARTL would have fired T1 ($7.91) **and** T2 ($8.14). SST would have fired T1 ($3.30) and barely T2 ($3.395). Both positions went on to hit their stop losses at -55% of entry, returning nothing instead of booking partial profits.

---

## How the D165 Tranche System Works

D165 adds a **pure-software, per-cycle price check** that runs every Phase 3 iteration (~10s cycle). It is independent of Alpaca limit order state.

### Target Calculation

```
Long:  T1 = entry × 1.03  (+3%)
       T2 = entry × 1.06  (+6%)
       T3 = entry × 1.10  (+10%)

Short: T1 = entry × 0.97  (-3%)
       T2 = entry × 0.94  (-6%)
       T3 = entry × 0.90  (-10%)
```

### Tranche Size Calculation

```
T1_qty = floor(remaining_qty / 3)
T2_qty = floor(remaining_qty / 3)
T3_qty = remaining_qty - T1_qty - T2_qty   ← all remaining (full exit)
```

`remaining_qty` is used (not original qty) so D164 partial exits are automatically accounted for.

### Firing Rules

1. Tranches are checked in order: T1 → T2 → T3.
2. If T1's target hasn't been crossed, T2 and T3 are skipped (prices are ordered).
3. Multiple tranches can fire **in the same cycle** if price jumped past multiple targets.
4. Once a tranche fires, it is marked in `_fired` and will not fire again.
5. The `_fired` set is synced with `position.tranches_filled` (limit order fills).

### Execution Flow

```
Phase 3 cycle (every ~10s):
  1. Fetch current prices via D78 batch snapshot
  2. D165: for each position, check if price >= target_prices[i]
     → fire market sell for tranche qty
     → resize stop (cancel + resubmit for remaining qty)
     → add to cooldown so D163/D78 don't double-fire
  3. D163: trailing stop check (skips cooldown tickers)
  4. D78:  SMART_EXIT signals (skips cooldown tickers)
```

### Sell Execution

After each tranche triggers:
1. Submit `market sell` for `tranche_qty` shares via `client.submit_order()`
2. Update `position.remaining_qty -= tranche_qty`
3. Update `position.tranches_filled = max(current, tranche_number)`
4. Record realized P&L
5. Cancel existing stop + resubmit for `remaining_qty` (if `d165_adjust_stop_after_partial=True`)
6. Persist to session state (crash recovery)
7. If `remaining_qty == 0`: full cleanup (cancel orphan orders, remove from all trackers)

---

## Configuration

```python
# config/settings.py — ExecutionConfig

# D165: Target percentages (raised from D142's 1%/2.5%/5%)
tranche_t1_pct: float = 0.03   # +3%
tranche_t2_pct: float = 0.06   # +6%
tranche_t3_pct: float = 0.10   # +10%

# D165: Enable/disable the price-based fallback
d165_tranche_enabled: bool = True

# D165: Resize stop qty after each partial sell
d165_adjust_stop_after_partial: bool = True
```

The `D165Config` dataclass (in `src/execution/d165_tranche_taker.py`) accepts:
```python
D165Config(
    enabled=True,
    target_pcts=[0.03, 0.06, 0.10],
    adjust_stop_after_partial=True,
)
```

---

## Interaction with D163, D164, and the OTO Stop

### Execution Priority Chain

```
D164 (T+2min partial, 50%)  →  D165 (price targets)  →  D163 (trailing stop)  →  D78 (SMART_EXIT)
```

Each tier only fires if the one above it didn't handle the position in the same cycle (via `_tranche_cooldown_tickers`).

### D164 Interaction

D164 sells 50% of the position at T+2 minutes if the stock is profitable. After D164:
- `position.remaining_qty` is reduced by ~50%
- D165 computes tranche sizes from `remaining_qty` — automatically adjusting to 50% scale
- Example: 300 shares → D164 sells 150 → D165 T1 sells 50, T2 sells 50, T3 sells 50

### D163 Interaction

D163 (software trailing stop) is skipped for any ticker that had a tranche fire in the same cycle. This prevents D163 from immediately liquidating the remaining position after a partial sell before prices are re-assessed.

### OTO Stop Interaction

The D165 check fires **market orders** not limit orders, so it is unaffected by OTO bracket state. After each partial sell, the stop is resized via `stop_resubmitter.resubmit(new_qty=remaining_qty)`. If stop resubmission fails, a `WARNING` is logged but the tranche sale is NOT rolled back (partial protection is better than no protection).

---

## Backtest Scenarios

### ARTL (March 30, 2026)
- Entry: $7.68 at 9:29 AM
- D165 targets: T1=$7.91, T2=$8.14, T3=$8.45
- Intraday high: $8.22 (10:47 AM)
- **D165 would fire**: T1 at $7.91, T2 at $8.14
- T3 at $8.45 would NOT fire ($8.22 < $8.45)
- Exit: Stop at $3.48 (-55%) — prevented for 2/3 of position

### SST (March 30, 2026)
- Entry: $3.20 at 9:29 AM
- D165 targets: T1=$3.296, T2=$3.392, T3=$3.52
- Intraday high: $3.39 (9:38 AM)
- **D165 would fire**: T1 at $3.296, T2 at ~$3.39 (price barely cleared)
- Exit: Stop at $2.08 (-35%) — prevented for 2/3 of position

---

## Edge Cases

### Price Jumps Past Multiple Targets in One Cycle
The `check()` method returns ALL triggered tranches in one call. The caller (main.py) iterates through them and executes each. If T1 and T2 are both triggered, both fire in the same cycle — T1 first, then T2 on the already-reduced remaining qty.

### Tranche Already Fired by Limit Order
`position.tranches_filled` is checked on every call. If the limit-order WebSocket system already incremented `tranches_filled`, D165 syncs its internal `_fired` set and skips the already-executed tranche.

### Zero Remaining Qty
If `remaining_qty <= 0`, `check()` returns empty immediately. Cannot sell nothing.

### Empty target_prices
If `position.target_prices` is empty (e.g., LLM returned no targets), `check()` returns empty. Tranches only fire with explicit targets.

### Short Positions
Targets are inverted: firing condition is `current_price <= target` instead of `>=`. The sell side is `"buy"` (cover). Logic is otherwise identical.

### Session Recovery After Crash
`tranche_taker.register_fill()` is called once per fill. After a crash recovery, if a position is restored from session state with `tranches_filled=1`, D165 will sync its `_fired` set on the first `check()` call (via the `position.tranches_filled >= t_num` sync path), preventing T1 from re-firing.
