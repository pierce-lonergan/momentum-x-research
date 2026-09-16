# RUNBOOK — `D313 HEDGE_VIOLATION` (60-second operator response)

**Last updated:** 2026-05-24 (doc 174)
**Pager source:** Discord `@here D313 HEDGE_VIOLATION` (also `D313 EMERGENCY_STOP_FAILED`)

This runbook gets executed when the Discord OPS channel pings `@here`
with one of three D313 alert classes. **Time-critical**: the L2 watcher
auto-submits an emergency stop at ~60s after the violation; this runbook
covers the cases where that wasn't enough.

---

## Alert taxonomy

| Discord alert | Severity | Auto-action | Operator action required? |
|---|---|---|---|
| `D313 HEDGE_VIOLATION` (single occurrence) | CRITICAL | L2 auto-submits emergency stop | **VERIFY only** — runbook step 1 |
| `D313 EMERGENCY_STOP_FAILED` | CRITICAL | NONE — broker refused | **YES** — runbook steps 1-3 |
| `D313 EMERGENCY_STOP_SUBMITTED` | INFO | Already auto-handled | Note in log; no action |
| `D313.v2 L2_WATCHDOG heartbeat stale` | CRITICAL | NONE — watcher itself sick | **YES** — runbook steps 4-5 |

---

## 60-second response — `D313 HEDGE_VIOLATION`

### Step 1: VERIFY broker actually got the emergency stop (15 sec)

1. Open https://paper.alpaca.markets → Orders
2. Filter by symbol from the alert
3. Look for `SELL STOP` (long position) or `BUY STOP` (short)
4. Expected: order status = `accepted` or `new`, submitted within last 60s

   **If the stop is there**: ✅ L2 saved you. Note the symbol + emergency_stop_price in `docs/incidents/YYYY-MM-DD_hedge_violations.md`. Done.

   **If the stop is NOT there**: go to Step 2.

### Step 2: MANUAL emergency stop (30 sec)

1. Open https://paper.alpaca.markets → Positions
2. Find the symbol from the alert
3. Note current price and avg_entry_price
4. Compute manual stop: `current_price × 0.92` for longs (8% below current — same as L2's emergency target)
5. Submit a SELL STOP order:
   - Type: `Stop`
   - Time-in-force: `GTC`
   - Qty: the FULL position qty
   - Stop price: the value from step 4

   **Click Submit. Wait for confirmation that it's `accepted`.**

### Step 3: Log incident (15 sec)

Append to `docs/incidents/YYYY-MM-DD_hedge_violations.md`:

```
## $YYYY-MM-DDTHH:MM:SS$ D313 EMERGENCY_STOP_FAILED on $SYMBOL$
- Position: qty=$Q$ avg_entry=$$AVG$ current=$$CUR$
- L2 attempted emergency stop at $$AUTO_STOP$ — broker refused with: $REASON$
- Manual stop submitted: $$MANUAL_STOP$, oid=$ORDER_ID$
- Investigate why broker refused (Alpaca rate-limit? wash-sale flag? sub-$1 unreliability?)
```

---

## Response — `D313 EMERGENCY_STOP_SUBMITTED` (auto-handled)

Already done. The watcher submitted an emergency stop. **No action required during market hours.** At EOD:

1. Search the day's log for the emergency-stop trigger context (what unhedged the position in the first place — D310 callback failure? D316? something new?)
2. File a follow-up if root cause unclear

---

## Response — `D313.v2 L2_WATCHDOG heartbeat stale`

The watcher itself is hung. T2 safety net is DOWN. Two failure modes:

### Step 4: Check if the watcher task crashed (15 sec)

In the bot's terminal/log, search for: `D313.v2 L2_WATCHDOG: HedgeIntegrityWatcher task is DONE`

   **If "task is DONE"**: the watcher crashed. Skip to step 5.

   **If "heartbeat N seconds stale"**: the watcher is alive but hung in a network call. Wait 1 more minute — if it recovers (`heartbeat recovered (age=...)` log), no action needed.

### Step 5: Bot restart if watcher dead (45 sec)

```powershell
schtasks /end /tn MomentumX-PaperTrading
# Wait 5s for clean shutdown
schtasks /run /tn MomentumX-PaperTrading
```

Then within 60s confirm the D315 boot self-test message lands in Discord with `L2: OK  L2_WATCHDOG: OK`.

**If positions are open during the restart**: brief gap (~60s) where no watcher is running. Watch broker positions manually during this window.

---

## Response — Repeated HEDGE_VIOLATION (>3 in single session)

The D313.v3 `@here` debounce limits pings to 1 per 5 min, but the alerts themselves keep firing. If you see 3+ HEDGE_VIOLATION in one session:

1. The dead-callback (D310) is firing on more positions than usual, OR
2. L1 standalone-stop submission is failing systematically, OR
3. Alpaca's `get_orders` is returning stale data (false-positive unhedge detection)

**ABORT T2 for the session:**
```powershell
[Environment]::SetEnvironmentVariable('MOMENTUM_T2_ENABLED', '0', 'User')
schtasks /end /tn MomentumX-PaperTrading
schtasks /run /tn MomentumX-PaperTrading
```

Wait for D315 boot self-test to confirm `T2: OFF`. Then investigate root cause before re-enabling Tuesday morning.

---

## What L2 does NOT cover

- **Bot process crash.** If `main.py` itself dies (e.g. doc 167 forward-reference bug), every watcher dies with it. **Filed: external process watchdog** (W). Until that ships, the Watchdog scheduled task (`MomentumX-Watchdog`) is the only external check.
- **Alpaca API outage.** Both the bot AND L2 are blocked. No alerts will fire. If you notice no Discord activity for >30 min during market hours, check Alpaca status page.
- **Stop-trigger reliability on sub-$1 names.** L2 alerts but does NOT auto-submit (Alpaca stop-trigger unreliable below $1.00). Operator must manually intervene for sub-$1 positions.

---

## Anti-patterns — DO NOT do these mid-session

- ❌ **Do not** `git pull` and restart the bot mid-session to "fix" something. Wait for EOD.
- ❌ **Do not** flip `MOMENTUM_T2_ENABLED` between trades — arm assignment is deterministic per `(date, symbol)` so the bot will produce inconsistent decisions if it sees both states in one session.
- ❌ **Do not** manually cancel the L2 emergency stop. That's exactly what created the original NXXT 66h unhedge.
- ❌ **Do not** clear `data/alerts/` mid-session — the spool is the redelivery queue.
- ✅ **DO** note everything in `docs/incidents/YYYY-MM-DD_hedge_violations.md` for the EOD post-mortem.

---

## After-incident checklist (EOD)

1. Read EOD Discord report — confirm `D313-Nviolations` and `D313-Nemerg_stops` D-codes appear
2. Open the day's incident log and reconcile against the EOD numbers
3. If `D313 HEDGE_VIOLATION` count > 0 today: confirm L1 + L2 are healthy for tomorrow's session
4. Pre-commit ratchet criteria check: any HEDGE_VIOLATION today = no sizing bump tomorrow regardless of P&L
