# D164: Time-Based Early Profit Take

**Node ID:** execution.early_profit_take
**File:** `src/execution/early_profit_take.py`
**Config:** `settings.early_profit` (EarlyProfitTakeConfig)
**Tests:** `tests/unit/test_d164_early_profit_take.py`

---

## Problem Statement

Arena data shows **MFE (max favorable excursion) peaks at bar 1** — the first minute after
entry. Most gap stocks give a brief profit window before reversing:

- ARTL (Mar 30): entry $7.68 → peak $8.22 (+7%) → reversed to -55% stop
- SST  (Mar 30): entry $3.20 → peak $3.39 (+6%) → reversed to -35% stop

D146 (bar-1 exit) addresses this aggressively: sell 100% at T+60s regardless. That eliminates
the -88% remainder, but also exits positions that could have continued running.

D164 is a softer approach: **sell 50% at T+2 minutes if profitable**, keep the other 50% for the
trailing stop (D163) to manage. This locks in partial profit on the spike while preserving
upside exposure on stocks that continue running.

---

## How It Works

### Timeline

```
T+0s    Fill confirmed
  │      → register_fill() called
  │      → asyncio task created (Phase 2) or Phase 3 loop will check (Phase 3)
  │
T+120s  Trigger time (2 minutes)
  │      → check(symbol, current_price, now)
  │
  ├─ price >= entry × 1.005 (+0.5%)?
  │     YES → TAKE_PROFIT: sell floor(qty × 50%) at market
  │           remaining 50% continues with D163 trailing stop
  │     NO  → SKIP: let hard stop / D163 / D78 manage full position
  │
T+300s  Deadline (5 minutes)
         → Any call past this always returns SKIP (window closed)
```

### Profitability Check

For **longs**: `profit_pct = (current_price - entry_price) / entry_price`
For **shorts**: `profit_pct = (entry_price - current_price) / entry_price`

Trigger fires when `profit_pct >= min_profit_pct` (default: +0.5%).

### Quantity Calculation

```python
qty_to_sell = floor(original_qty * exit_pct)

# Examples:
# 1000 shares × 50% = 500
# 333  shares × 50% = 166  (floor, not round)
# 1249 shares × 50% = 624
```

`original_qty` is captured at fill time. If tranches have already reduced the position,
the caller clamps `qty_to_sell = min(qty_to_sell, pos.remaining_qty)` before submitting.

---

## Configuration

```python
class EarlyProfitTakeConfig:
    enabled: bool = True
    delay_seconds: float = 120.0     # T+2 minutes after fill
    exit_pct: float = 0.50           # Sell 50% of position
    min_profit_pct: float = 0.005    # Must be at least +0.5% profitable
    max_delay_seconds: float = 300.0 # Window closes at T+5 minutes
    apply_to_shorts: bool = True     # Also applies to short positions
```

Environment variable overrides use the `EARLY_PROFIT_` prefix:

```bash
EARLY_PROFIT_ENABLED=false          # Disable
EARLY_PROFIT_DELAY_SECONDS=90       # T+1.5min
EARLY_PROFIT_MIN_PROFIT_PCT=0.01    # Require +1%
```

---

## Integration With Other Systems

### D146 (Bar-1 Exit)

D146 sells 100% at T+60s. If bar-1 fires, the position is fully closed before D164's T+2
trigger arrives. `check()` returns `NOT_TRACKED` (position removed from tracking). No conflict.

**Both enabled:** D146 fires first (T+60s < T+120s). D164 never fires. This is by design —
D146 is the primary exit on day-1. D164 acts as a complement when D146 is disabled or for
Phase 3 intraday entries that don't go through the bar-1 exit path.

**D146 disabled, D164 enabled:** D164 fires at T+2min, keeping 50% for D163 to trail.

### D163 (Software Trailing Stop)

After D164's partial sell, D163 continues managing the **remaining 50%**. D163 stores
`max_favorable_price`, `entry_price`, and `direction` — no quantity. The trailing trail is
unchanged by D164. Effectively, D163 gets a reduced-risk position at the same entry cost.

### D78 (Smart Exit Intelligence)

If D78 exits before T+2min, position is closed and D164's `check()` returns `NOT_TRACKED`.
If D164 fires first, D78 continues evaluating the remaining 50%.

### Tranches (D142)

If tranche fills have already reduced `remaining_qty`, the caller clamps:
```python
qty_to_sell = min(early_profit_taker.qty_to_sell(ticker), pos.remaining_qty)
```
`original_qty` from the fill registration is used for the 50% calculation (not current qty).

---

## Decision Tree

```
Phase 2 fill (9:30-10 AM)
  └─ register_fill() + asyncio.create_task(_d164_early_profit_task())
       └─ await asyncio.sleep(120)
           └─ get snapshot price
               └─ check() → TAKE_PROFIT / SKIP / NOT_TRACKED

Phase 3 monitoring loop (10 AM+)
  For each position, each cycle:
  └─ check(symbol, price, now_utc)
      ├─ NOT_TRACKED  → skip (position closed upstream)
      ├─ ALREADY_TAKEN → skip (partial sell already done)
      ├─ WAIT         → skip (< T+2min)
      ├─ SKIP         → skip (not profitable at T+2min, or past T+5min)
      └─ TAKE_PROFIT  → submit partial sell
                        → update remaining_qty
                        → record partial PnL
                        → mark_executed()
                        → log D164 EARLY PROFIT TAKE
                        → journal exit_reason="EARLY_PROFIT_TAKE"
```

---

## Race Prevention (D160 Pattern)

The Phase 2 asyncio task and Phase 3 monitoring loop can both see `TAKE_PROFIT` for the same
position. To prevent a double-sell:

1. **Before the first `await`**, set `state.skipped = True` (marks as in-progress).
2. If submit succeeds: reset `state.skipped = False`, call `mark_executed()` → sets `executed = True`.
3. Subsequent calls to `check()` return `ALREADY_TAKEN` (executed=True takes priority).
4. If submit fails: `un_skip()` resets `state.skipped = False` so Phase 3 can retry once.

```
asyncio is single-threaded — no true concurrency. But it yields at every `await`.
Setting the flag synchronously before the first `await` is sufficient.
```

---

## Backtest Validation

### ARTL (Mar 30)

| Parameter | Value |
|-----------|-------|
| Entry price | $7.68 |
| Qty | 333 shares |
| Price at T+2min | ~$8.00 |
| Profit % | +4.2% |
| Action | **TAKE_PROFIT** |
| Qty sold | 166 (floor(333 × 0.50)) |
| Partial PnL | $(8.00 - 7.68) × 166 = **$53.12** |
| Remaining | 167 shares — D163 continues trailing |

### SST (Mar 30)

| Parameter | Value |
|-----------|-------|
| Entry price | $3.20 |
| Qty | 1249 shares |
| Price at T+2min | ~$3.30 |
| Profit % | +3.1% |
| Action | **TAKE_PROFIT** |
| Qty sold | 624 (floor(1249 × 0.50)) |
| Partial PnL | $(3.30 - 3.20) × 624 = **$62.40** |
| Remaining | 625 shares — D163 continues trailing |

Both stocks later reversed significantly. D164 locks in a guaranteed partial profit while D163
manages the rest. In the worst case (total reversal on remaining 50%), the locked-in gain
partially offsets the loss.

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Bar-1 exit fires before T+2 | Position removed; `NOT_TRACKED` |
| Trailing stop fires before T+2 | Position removed; `NOT_TRACKED` |
| Position not profitable at T+2 | `SKIP` — full position managed by stops |
| Exactly at entry (0% P&L) | `SKIP` (must exceed `min_profit_pct`) |
| Past 5-minute window | `SKIP` (deadline exceeded) |
| Tranche fills reduced qty | Caller clamps to `pos.remaining_qty` |
| Submit order fails (422 error) | Log error, `mark_skipped()`, no retry |
| qty_to_sell = 0 (1 share × 50%) | `mark_skipped()`, skip silently |
| Short position, price above entry | `SKIP` (not profitable for short) |
| Duplicate `register_fill()` | No-op (idempotent, crash-recovery safe) |
| `apply_to_shorts=False` | Short positions not registered, `NOT_TRACKED` |

---

## Log Format

```
D164 EARLY PROFIT TAKE: ARTL sold 166 of 333 at $8.0000 (+4.17%) T+120s — remaining=167
D164 EARLY PROFIT TAKE (P3): SST sold 624 shares at $3.3000 (+3.13%) T+122s — remaining=625
D164 EARLY PROFIT SKIP: BFRG — not profitable at T+120s (profit=-1.20%, min=0.50%)
D164 EARLY PROFIT SKIP: PTLE — past deadline (T+312s > max=300s)
```
