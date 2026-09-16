# D163: Software-Managed Trailing Stop

**Status:** Implemented
**Decision date:** 2026-04-01
**Files:** `src/execution/trailing_stop.py`, `tests/unit/test_d163_trailing_stop.py`

---

## Problem

Backtest analysis of Mar 30 trades revealed a recurring loss pattern:

> **14 of 18 trades hit their fixed stop at the 30-minute mark.**

The stops were set wide (Chandelier Exit: 4× ATR below peak), but the damage was not
from holds — it was from **early gains that fully reversed**:

| Trade | Entry | Peak | Peak Gain | Hard Stop | P&L at Stop |
|-------|-------|------|-----------|-----------|-------------|
| ARTL  | $7.68 | $8.22 | +7%       | $3.48     | −55%        |
| SST   | $3.20 | $3.39 | +6%       | $2.08     | −35%        |

Both stocks had clean initial momentum, peaked within the first 2–5 minutes, then
reversed all the way through the fixed stop. A trailing stop would have locked in the
early gain.

---

## Design

### Two separate trailing systems

| System | Mechanism        | Activation | Trail method         | Fires                       |
|--------|-----------------|------------|---------------------|-----------------------------|
| D63    | Ratchets Alpaca stop order | +4% | Chandelier Exit (4× ATR below peak) | Broker stop fills |
| **D163** | **Software check each cycle** | **+2%** | **50% of max gain** | **Market EXIT in Phase 3** |

D163 fires earlier (+2% vs +4%) and uses simpler math. Both systems run in parallel —
whichever fires first wins.

### Trail level formula

**For longs:**

```
trail_level = peak_price - 50% × (peak_price − entry_price)
            = midpoint(entry_price, peak_price)
```

Constraints applied to the computed trail:
- **Min distance:** `trail ≤ current × (1 − 2%)` — never tighter than 2% from current (prevents noise-triggered exits)
- **Max distance:** `trail ≥ current × (1 − 35%)` — never wider than 35% from current (prevents a trail that never fires)
- **Ratchet:** trail only moves UP (enforced via `max(new_trail, current_trail)`)

**For shorts (mirror):**

```
trail_level = trough_price + 50% × (entry_price − trough_price)
            = midpoint(trough_price, entry_price)
```

- **Min distance:** `trail ≥ current × (1 + 2%)`
- **Max distance:** `trail ≤ current × (1 + 35%)`
- **Ratchet:** trail only moves DOWN

**EXIT condition:**
- Long: `current_price ≤ trail_level`
- Short: `current_price ≥ trail_level`

### Worked example: ARTL

```
Entry:         $7.68
Peak:          $8.22   (+7.0%)
Trail at peak: $7.95   [8.22 - 0.5*(8.22-7.68) = midpoint = $7.95]

When price drops below $7.95 → D163 EXIT fires.
Old hard stop: $3.48

Savings: ($7.95 − $3.48) × 333 shares = ~$1,488
```

### Worked example: SST

```
Entry:         $3.20
Peak:          $3.39   (+5.9%)
Trail at peak: $3.295  [3.39 - 0.5*(3.39-3.20)]

When price drops to $3.28 → D163 EXIT fires ($3.28 < $3.295).
Old hard stop: $2.08

Savings: ($3.295 − $2.08) × 1,249 shares = ~$1,518
```

Combined estimated improvement on just these two Mar 30 trades: **+$3,006**.

---

## Configuration

In `config/settings.py` → `TrailingStopConfig` (env prefix: `TRAIL_`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | `True` | Master switch. Set `False` to disable without touching D63. |
| `activation_threshold_pct` | `0.02` | Activate when gain ≥ +2% (longs) or drop ≥ −2% (shorts). |
| `trail_pct_of_gain` | `0.50` | Trail distance = 50% of max gain from peak/trough. |
| `min_trail_distance_pct` | `0.02` | Trail never tighter than 2% from current price. |
| `max_trail_distance_pct` | `0.35` | Trail never wider than 35% from current price. |

All values configurable via environment variables (`TRAIL_ACTIVATION_THRESHOLD_PCT`, etc.)
or `.env` file at runtime.

---

## Integration with existing systems

```
Phase 3 monitoring cycle (every ~60s):
  │
  ├─ D63: Chandelier trailing stop
  │    └─ Ratchets the ALPACA stop order (broker-side safety net)
  │    └─ Activates at +4% gain
  │
  ├─ D163: Software trailing stop  ◄── NEW
  │    └─ Checks trail in software, fires market EXIT if breached
  │    └─ Activates at +2% gain
  │    └─ Whichever fires first wins
  │
  └─ D78: SMART_EXIT intelligence
       └─ 13-signal composite urgency score
       └─ EXIT if urgency ≥ 0.40
```

**Position lifecycle:**

| Event | D163 action |
|-------|-------------|
| Entry fill confirmed | `register_position()` called lazily on first Phase 3 cycle |
| Each price check | `update_price()` → HOLD or EXIT |
| Bar-1 exit (D146) | `remove_position()` called before close |
| D98 stop-out detected | `remove_position()` called before close |
| D78 SMART_EXIT | `remove_position()` called before close |
| EOD forced close (D76) | `remove_position()` called before close |
| D163 EXIT fires | `remove_position()` called, then market sell + attribution |

**The OTO hard stop is never touched.** D163 fires independently as a software check.
The hard stop remains the ultimate safety net if both D163 and D63 are delayed.

---

## Edge cases

| Scenario | Behavior |
|----------|----------|
| Price exactly at trail | `current ≤ trail` → EXIT (inclusive) |
| Price gaps below trail (thin market) | EXIT fires on the next price check cycle |
| Tranche fills during trail activation | D93 cooldown suppresses D163 EXIT for that cycle |
| Crash recovery restart | `register_position()` is idempotent; existing state preserved |
| Short position | Mirror logic: trail moves down, EXIT fires when price rises above trail |
| `enabled=False` | `update_price()` always returns HOLD; D63 and D78 unaffected |
| Entry has no gain (flat) | Trail uses `min_trail_distance_pct` as floor distance from current |
| No price data for symbol | `update_price()` returns HOLD, skips cycle |

---

## Walk-forward note

This was identified as the **#1 priority optimization** in the profitability assessment.
The Chandelier stop (D63) is correct in principle but fires too late for the specific
loss pattern observed (spike-and-reverse in the first 2–5 minutes). D163's simpler
"lock in half the gain" math is more conservative and activates earlier.

**Expected improvement:** Based on Mar 30 analysis (~14 stop-outs out of 18 trades that
had prior favorable moves), D163 is expected to convert most stop-outs into profitable
or breakeven exits on days with the spike-and-reverse pattern. Walk-forward validation
required over 20+ live sessions before tuning parameters.
