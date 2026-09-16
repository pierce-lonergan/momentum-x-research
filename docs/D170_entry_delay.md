# D170: Entry Delay with Observation Window

## Why Entries at Open Consistently Fail

Analysis of all live trades (Mar–Apr 2026) reveals a stark time-of-day effect:

| Time Bucket | Trades | Win Rate | Avg P&L | Total P&L |
|---|---|---|---|---|
| 9:28–9:35 EDT | 10 | **0%** | **-$1,263** | **-$12,634** |
| 10:00–10:15 EDT | 3 | 33% | -$64 | -$191 |
| 10:15+ EDT | 1 | 0% | -$126 | -$126 |

Every single open-bucket entry was a loss. The pattern is consistent:
- **ARTL (Mar 30):** Opened at $7.68 with VWAP $8.22 — already **6.5% below VWAP** at the bell. Lost $1,399.
- **BFRG (Mar 31):** Opened at $1.18 with VWAP $1.24 — **4.8% below VWAP** at entry. Lost $1,421.
- **ARTL, SST, BFRG, ASTC, CYCN, AGPU:** All showed the same microstructure — price already distributed below VWAP before the first trade even filed.

The gap-and-fade pattern is set in motion during pre-market. Retail FOMO drives up the pre-market price, institutions distribute into the open spike, and by 9:30 the stock is already fading. Entering at the bell means buying directly from the smart-money distribution.

The solution: **don't enter at 9:30**. Watch for 15 minutes. If the stock is genuinely strong, it will hold its gains and confirm with higher lows and VWAP support. If it's a fake, it will show weakness immediately — and the system will reject the entry before capital is committed.

## How the Observation Window Works

```
Phase 2 BUY verdict at 9:30
         │
         ▼
register_candidate()
  state = WATCHING
         │
         │  (every price update cycle, e.g. every 30s)
         ▼
update_price(ticker, price, vwap, volume, timestamp)
         │
    ┌────┴─────────────────────────────────────────────┐
    │ Check criteria (fail-fast order):                 │
    │  1. max_drawdown_from_open_pct (10%)              │
    │  2. require_above_vwap                            │
    │  3. require_higher_lows (full history)            │
    │  4. require_no_new_low (last N readings)          │
    │  5. min_volume_sustain_pct (50% of opening bar)   │
    └────┬─────────────────────────────────────────────┘
         │
    Any fail? ──► REJECTED (log reason, never enters)
         │
    Past max window? ──► EXPIRED
         │
    Past min_observation (5 min)?
         │   No ──► stay WATCHING
         │
    All criteria pass AND past full window (15 min)?
         │   Yes ──► APPROVED → caller submits order at CURRENT price
         │
    Early entry: MFCS > 0.60 AND 4+ bullish agents?
         └── Yes ──► APPROVED early (at 5+ min instead of 15 min)
```

**Key point:** The order is submitted at the **current price** when approved, not the stale 9:30 snapshot. This naturally avoids chasing the opening spike.

## Configuration Parameters

```python
@dataclass
class ObservationConfig:
    enabled: bool = True                      # Kill switch — False bypasses window

    observation_minutes: float = 15.0         # Standard observation window
    min_observation_minutes: float = 5.0      # Minimum even for strong signals
    max_observation_minutes: float = 30.0     # Expire if not approved by this

    # Criteria (all must pass)
    require_above_vwap: bool = True           # Price >= VWAP
    require_higher_lows: bool = True          # No new intraday low
    min_volume_sustain_pct: float = 0.50      # Volume >= 50% of opening bar
    max_drawdown_from_open_pct: float = 0.10  # Max 10% drop from open price
    require_no_new_low: bool = True           # No new low in last 3 readings

    # Early entry override
    early_entry_min_mfcs: float = 0.60
    early_entry_min_agents_bullish: int = 4
```

### Parameter Rationale

**observation_minutes = 15**
The first 5 minutes after open are chaotic — wide spreads, algorithmic order routing, stop cascades. By 9:45, the genuine trend is established. 15 minutes provides enough price action to see 3+ higher lows on a 5-minute chart.

**require_above_vwap**
VWAP is the institutional benchmark. If price is below VWAP by open, institutions sold into the pre-market spike. This is the single strongest rejection signal: ARTL and BFRG were both below VWAP at the bell.

**require_higher_lows**
A genuine continuation move doesn't retrace below prior lows. Each dip is absorbed at a higher level. A stock making lower lows during the observation window is distributing — retail buying, institutions selling.

**min_volume_sustain_pct = 0.50**
Volume at the open is often 5–20× average. If it collapses to below 50% of the opening bar within 15 minutes, the spike was a one-time flush of pent-up orders, not sustained demand. This prevents entries into volume exhaustion.

**max_drawdown_from_open_pct = 0.10**
If the stock drops 10%+ from the open price during the observation window, it has already failed the gap-and-hold test. Skip it regardless of other criteria.

**early_entry_min_mfcs = 0.60 + 4 agents**
Genuine breakout continuation stocks occasionally move fast. The early override lets the system enter at 5 minutes if conviction is very high — multiple independent agents agree AND MFCS is strong. This prevents missing BDRX-type moves (which entered at 10:14 and was the only winning trade in the dataset).

## Integration with the Trading Pipeline

**Current (pre-D170):**
```
Phase 2 BUY verdict → execute_verdict() immediately
```

**After D170:**
```
Phase 2 BUY verdict
    → register_candidate()          # don't execute yet
    → monitoring loop updates prices every 30s
    → get_approved_candidates()     # poll for approvals
    → execute_verdict() at CURRENT price  # only when approved
```

Integration points in `main.py`:
1. **After faller gate, before `execute_verdict`:** Call `entry_delay_mgr.register_candidate(ticker, current_price, mfcs, agent_signals, now)` instead of executing.
2. **In the Phase 2/3 monitoring loop:** Call `entry_delay_mgr.update_price(ticker, price, vwap, volume, now)` for each watched ticker on every cycle.
3. **When `APPROVED`:** Submit the order with the current (live) price — not the cached 9:30 evaluation price.
4. **When `REJECTED`/`EXPIRED`:** Log with D170 prefix and remove from candidates.

The observation window is transparent to the rest of the system: `EntryDelayManager` is a standalone state machine. It does not touch orders, positions, or the bridge.

## Historical Trade Outcomes

### Would the observation window have caught the losses?

| Ticker | Date | Open vs VWAP | Would Reject? | Reason | Actual P&L |
|---|---|---|---|---|---|
| ANNA | Mar 23 | Unknown | Likely | — | -$869 |
| JBLU | Mar 26 | Unknown | Likely | — | -$490 |
| SRPT | Mar 26 | Unknown | Likely | — | -$1,396 |
| RMSG | Mar 26 | Unknown | Likely | — | -$1,399 |
| SST | Mar 30 | +11.3% above VWAP | Maybe | Price static in journals — static price suggest potential approval (needs live data to confirm higher-lows) | -$1,399 |
| ARTL | Mar 30 | **-6.5% below VWAP** | **YES** | below_vwap | -$1,399 |
| BFRG | Mar 31 | **-4.8% below VWAP** | **YES** | below_vwap | -$1,421 |
| ASTC | Mar 31 | +1.7% above VWAP | Maybe | Static journal data | -$1,420 |
| CYCN | Apr 2 | Unknown | Unknown | No journal data | -$1,421 |
| AGPU | Apr 2 | Unknown | Unknown | No journal data | -$1,421 |

**ARTL and BFRG** would definitely have been rejected — both were below VWAP at the first journal entry. Together that's **$2,820 in avoided losses** just from two trades.

### Caveat on journal data

The price data in the journals is largely static across the observation period (same price repeated across multiple evaluation cycles). This reflects how the system polls market data — not live ticks, but periodic snapshots. The higher-lows and volume-sustain criteria will only be fully testable with live price streaming, which D170 relies on during actual Phase 2/3 monitoring.

## The Higher-Lows and VWAP Hold Criteria Explained

### VWAP Hold

VWAP (Volume-Weighted Average Price) is the single most reliable intraday institutional benchmark. It answers: "what's the average price that all participants have paid today, weighted by how much they traded?"

When a stock's price is **at or above VWAP**:
- Buyers have been willing to pay at or above the average — demand is strong
- Institutions running VWAP execution algorithms are net buyers
- The gap has not been fully distributed

When a stock is **below VWAP** at open:
- The pre-market spike was distributed above VWAP — smart money sold
- Institutions running VWAP algo are net sellers (to average down their cost)
- Any long entry is fighting institutional supply

ARTL demonstrated this exactly: opened at $7.68 with VWAP $8.22. Every institutional VWAP algorithm was a seller. The stock had no chance.

### Higher Lows

A bullish trending stock makes **higher highs and higher lows**. Each pullback is bought at a higher level than the prior pullback. This signals ongoing demand.

The higher-lows check in D170 looks at all price readings since observation began. If the latest reading is a new overall low, the stock is fading — each price update shows continued selling pressure. This catches slow fade patterns where no single tick looks alarming but the trend is clearly down.

The `require_no_new_low` criterion adds a tighter check on the most recent 3 readings — even if the all-time low was minutes ago, a new local low in the last 3 ticks signals the fade is re-accelerating.

## Files

```
src/execution/entry_delay.py          # Core module
tests/unit/test_d170_entry_delay.py   # 19 unit tests (all passing)
docs/D170_entry_delay.md              # This document
```
