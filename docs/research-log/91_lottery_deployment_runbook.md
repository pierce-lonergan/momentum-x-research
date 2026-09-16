# 91 — Lottery Deployment Runbook (D-Day: 2026-05-01)

**Date prepared:** 2026-04-30 evening
**First fire:** 2026-05-01 09:00 ET (Friday) via `MomentumX-Lottery` scheduled task
**Account:** Alpaca PA3A2I4TN9AZ paper, $140,104.88 equity
**Mode:** LIVE (not dry-run). Notional capped at $2,500/day total.

---

## §1 — What ships tomorrow

A standalone process that:
1. Wakes at 09:00 ET via Windows Task Scheduler
2. Sleeps until 09:25 ET
3. Pulls top 30 gainers from Alpaca's screener
4. Filters to $1.50 ≤ price ≤ $20
5. Splits into FRESH (ticker not in last 30 days of `data/bar_recordings/` and never traded by lottery before) vs RECURRING
6. Selects up to 10 picks: prioritize FRESH, fill remainder with RECURRING
7. At 09:30 ET, submits market BUY for each pick (~$250 notional each)
8. After each fill, submits a native Alpaca trailing-stop SELL with **trail_percent=15**
9. Heartbeats every 60s logging open positions and unrealized P&L
10. At 15:55 ET, force-closes any remaining open positions via market sell
11. Writes session report `data/lottery/session_report_2026-05-01.json`
12. Exits

**Total deployment cap:** 10 positions × $250 = **$2,500 max risk per day** (much less than $140K equity).

**Expected behavior:** if backtest holds, this should produce a daily return between −5% and +10% on the deployed $2,500 (i.e., $-125 to +$250 per session, with positive expectancy).

---

## §2 — Files shipped

| Path | Purpose |
|------|---------|
| `scripts/lottery_runner.py` | The runner (Python, 350 LOC, stdlib + httpx) |
| `scripts/lottery_paper_trade.ps1` | The launcher (PowerShell, weekday gate, secrets loader) |
| `scripts/_lottery_smoke.py` | Connectivity smoke test (devs only) |
| Scheduled task `MomentumX-Lottery` | Daily 09:00 ET, non-elevated, weekday-skip in launcher |
| `data/lottery/lottery_traded_tickers.json` | Persistent freshness-history (created on first run) |
| `data/lottery/session_report_YYYY-MM-DD.json` | Per-session P&L and trade detail |
| `logs/lottery_YYYY-MM-DD.log` | Runner internal log (UTF-8) |
| `logs/lottery_launcher_YYYY-MM-DD.log` | Launcher log |
| `logs/lottery_transcript_YYYY-MM-DD.log` | PowerShell transcript |

---

## §3 — Operator commands (cheat sheet)

### §3.1 — HALT the lottery (UAC-safe)
```powershell
[Environment]::SetEnvironmentVariable("MOMENTUM_LOTTERY_HALT", "1", "User")
```
Effect: launcher will set `MOMENTUM_LOTTERY_HALT=1` for the runner process. Runner will skip ALL order submissions and operate in observation-only mode. Logs will still be produced. The trailing-stop force-close is also skipped, but no orders means no positions to close.

To clear:
```powershell
[Environment]::SetEnvironmentVariable("MOMENTUM_LOTTERY_HALT", "", "User")
```

### §3.2 — Disable the scheduled task entirely
```powershell
Disable-ScheduledTask -TaskName "MomentumX-Lottery"
```
Re-enable:
```powershell
Enable-ScheduledTask -TaskName "MomentumX-Lottery"
```

### §3.3 — Run on-demand right now (for testing)
```powershell
Start-ScheduledTask -TaskName "MomentumX-Lottery"
```
Or:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Documents\GitHub\momentum-x\scripts\lottery_paper_trade.ps1"
```

### §3.4 — Run in dry-run mode (logs only, no orders)
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Documents\GitHub\momentum-x\scripts\lottery_paper_trade.ps1" -DryRun
```

### §3.5 — Check today's status
```powershell
Get-Content "$env:USERPROFILE\Documents\GitHub\momentum-x\logs\lottery_$(Get-Date -Format yyyy-MM-dd).log" -Tail 30
```

### §3.6 — See today's session report
```powershell
Get-Content "$env:USERPROFILE\Documents\GitHub\momentum-x\data\lottery\session_report_$(Get-Date -Format yyyy-MM-dd).json"
```

### §3.7 — Check Alpaca paper account live
```powershell
$h = @{
    "APCA-API-KEY-ID" = $env:ALPACA_API_KEY
    "APCA-API-SECRET-KEY" = $env:ALPACA_SECRET_KEY
}
# Account
Invoke-RestMethod -Uri "https://paper-api.alpaca.markets/v2/account" -Headers $h
# Open positions
Invoke-RestMethod -Uri "https://paper-api.alpaca.markets/v2/positions" -Headers $h
```

---

## §4 — Tunable env vars (defaults are conservative)

Set as User-scope env vars before next run. None require restart.

| Variable | Default | Effect |
|----------|---------|--------|
| `MOMENTUM_LOTTERY_HALT` | unset | If `1`, no orders are submitted. Observation-only. |
| `LOTTERY_DRY_RUN` | unset | If `1`, logs intended orders but does not submit (set via `-DryRun` switch on launcher). |
| `LOTTERY_NOTIONAL_USD` | `250` | $/ticker. Total daily deployment = this × MAX_TICKERS. |
| `LOTTERY_MAX_TICKERS` | `10` | Max simultaneous positions. |
| `LOTTERY_TRAIL_PCT` | `15.0` | Trailing-stop width. The backtest's optimal value. |
| `LOTTERY_PRICE_MIN` | `1.50` | Lower price band. Filters out warrants/sub-penny. |
| `LOTTERY_PRICE_MAX` | `20.0` | Upper price band. Keeps universe in micro-cap territory. |
| `LOTTERY_FRESHNESS_DAYS` | `30` | Lookback window for freshness check. |
| `LOTTERY_MOVERS_LIMIT` | `30` | Top-N gainers requested from Alpaca screener. |

Examples:
```powershell
# More conservative (smaller deployment)
[Environment]::SetEnvironmentVariable("LOTTERY_NOTIONAL_USD", "100", "User")
[Environment]::SetEnvironmentVariable("LOTTERY_MAX_TICKERS", "5", "User")

# Tighter trail (capture less but lose less)
[Environment]::SetEnvironmentVariable("LOTTERY_TRAIL_PCT", "10.0", "User")
```

---

## §5 — Coexistence with the main bot

The main bot runs at 04:30 ET via `MomentumX-PaperTrading` task. It currently has `MOMENTUM_HALT_NEW_ENTRIES=1` set (User scope), so it will NOT open new positions — only manage existing ones (which are zero per yesterday's close).

The lottery runs at 09:00 ET. They share:
- Same Alpaca paper account
- Same secrets file
- Same Python install
- Same logs/ directory

They do NOT share:
- State files (lottery uses `data/lottery/`; bot uses `data/state/`)
- Halt switches (`MOMENTUM_HALT_NEW_ENTRIES` vs `MOMENTUM_LOTTERY_HALT`)
- Order placement code paths

If both somehow run simultaneously (e.g., bot opens positions despite halt), they'll just compete for cash on the same paper account — no order conflicts because tickers won't overlap (bot uses internal scanner; lottery uses Alpaca screener).

**Pierce's existing user-scope halt remains correct:**
- `MOMENTUM_HALT_NEW_ENTRIES=1` (bot halted)
- `MOMENTUM_LOTTERY_HALT` unset (lottery active)

---

## §6 — What success looks like (tomorrow EOD)

After tomorrow's session, check `data/lottery/session_report_2026-05-01.json`. Expected fields:

```json
{
  "date": "2026-05-01",
  "n_positions": 5-10,
  "config": {"notional": 250.0, "max": 10, "trail_pct": 15.0, ...},
  "positions": [
    {"ticker": "...", "qty": ..., "entry_price": ..., "exit_price": ..., "exit_reason": "trail|time_stop", "realized_pnl": ...},
    ...
  ],
  "total_realized_pnl_usd": <number>
}
```

**Acceptable outcomes for day 1:**
- Total realized PnL between $-200 and +$300 (single-day variance is wide)
- 5-10 positions opened (depending on screener output and freshness)
- All positions either trail-stopped or time-stopped (none orphaned)
- No errors in `logs/lottery_2026-05-01.log`

**Red flags to investigate same day:**
- Total PnL beyond $±500 (something unexpected happened)
- Any position with no exit_reason (orphaned position — must close manually)
- Any error in the log
- Filled BUY without corresponding trailing-stop SELL

**30-day target:**
- Compound between −10% and +30% on the deployed capital
- At least 5 of 20 trading days with a position that hits ≥+30% pnl_pct (matches doc 90's 84% high-mover rate)
- Drawdown ≤ −15% (matches backtest max DD)

---

## §7 — Failure modes and mitigations

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Screener returns empty | `n=0 picks` in log | Fall back to MAX_TICKERS open positions across whatever was returned. If 0, exits cleanly. |
| Buy submitted but not filled | `BUY %s not filled yet` warning | Trail-stop is SKIPPED for unfilled orders. The unfilled buy will time out at EOD per Alpaca's `time_in_force: day`. |
| Trailing-stop submission fails | Error log | Position is naked — but force-close at 15:55 will close it. |
| Force-close itself fails | Error log | Position carries overnight. Manual intervention next morning. |
| Alpaca API down | `httpx` exception | Runner exits with code 1. Scheduled task records LastTaskResult ≠ 0. |
| Bot's `daily_paper_trade.ps1` ate today's lock file | N/A — lottery doesn't use lock | No conflict. |
| Both bot AND lottery halt-switches got cleared accidentally | Bot might open positions | Re-set `MOMENTUM_HALT_NEW_ENTRIES=1` immediately. |

---

## §8 — Backtest provenance

The exact strategy parameters in production are anchored to doc 90:

| Setting | Backtest finding | Production default |
|---------|------------------|---------------------|
| Trail width | 15% gave +51.9% compound on freshness-tilted | **15.0%** |
| Universe | First-appearance prioritized, recurring filled in | Same |
| Entry time | 10:00 ET in backtest (next bar after 09:30 + 30 min) | **09:30 ET** (we don't have a "wait 30 min" step — doing the simpler thing) |
| Exit | EOD or trail | trail + 15:55 force-close |
| Position count | Universal across watchlist (~67/day) | **Capped at 10/day** for risk management |
| Notional | Equal-weight | **Equal-weight at $250/ticker** |

**Differences from backtest** (intentional simplifications for v1):
1. **Entry at 09:30 vs 10:00**: backtest used 10:00 to align with "first 30 min of momentum data observed." Production uses 09:30 because: (a) we use Alpaca's screener directly so we don't need the 30-min observation, (b) earlier entry captures more of the day's volatility, (c) simpler. Risk: this may degrade vs backtest. Will measure forward.
2. **Position cap at 10**: backtest used the full daily watchlist (avg 67 tickers). We're capping at 10 for risk control during the validation period. This may reduce the fat-tail capture rate. If 30-day forward looks stable, we lift the cap.
3. **Manual freshness windowing**: backtest defined "first" as never-before-seen in the 88-day window. Production uses 30-day rolling window. More conservative (more tickers count as "fresh").

---

## §9 — Decision tree for next 30 days

Day-by-day after tomorrow:

- **Day 1 (5/1)**: log review only. Verify trade flow worked end-to-end.
- **Days 2-7**: track daily P&L. Watch for max-DD breach (-15%). If breach, halt and investigate.
- **Day 7 review**: compute weekly compound, win-day rate. If forward week is positive: continue. If 2 consecutive losing weeks: halt and re-examine.
- **Days 14, 21**: same checkpoints.
- **Day 30 review**: full forward backtest comparison. If forward compound ≥ 0% and no operational issues: lift position cap to 25. Move toward backtest's full universe.
- **Day 60**: if still positive, scale notional from $250 → $1000. Evaluate moving to live broker.

If at any point a single day exceeds −10% portfolio DD: HALT immediately (set `MOMENTUM_LOTTERY_HALT=1`) and write a finding doc.

---

## §10 — Tomorrow's expected timeline (in ET)

| Time | Event | Where to look |
|------|-------|---------------|
| 04:30 | `MomentumX-PaperTrading` fires (main bot, halted) | `logs/momentum_2026-05-01.log` |
| 09:00 | `MomentumX-Lottery` fires (this runbook) | `logs/lottery_launcher_2026-05-01.log` |
| 09:00:01 | Launcher loads secrets, validates Python, starts runner | same |
| 09:00:02 | Runner: account preflight, sleeps until 09:25 | `logs/lottery_2026-05-01.log` |
| 09:25 | Runner: fetch screener, build watchlist, log picks | same |
| 09:25-09:30 | Runner: sleeps 5 min until open | same |
| 09:30:00 | Runner: market BUY orders fired | same; Alpaca dashboard |
| 09:30:03 | Runner: trailing-stop SELL orders fired (per filled buy) | same |
| 09:31 → 15:55 | Runner: 60s heartbeat with positions/PnL | same; tail the log |
| 15:55 | Runner: force-close any remaining positions | same |
| 16:00 | Runner: writes session report and exits | `data/lottery/session_report_2026-05-01.json` |

---

## §11 — Stop conditions

| Question | Result |
|----------|--------|
| Did dry-run end-to-end pass? | **YES** — exit 0, 8 picks, all flow steps logged |
| Is scheduled task registered for 9:00 ET 5/1? | **YES** — verified via `Get-ScheduledTaskInfo` |
| Is the lottery non-elevated (UAC-safe)? | **YES** — RunLevel=Limited |
| Is the main bot halted? | **YES** — `MOMENTUM_HALT_NEW_ENTRIES=1` (User scope) |
| Is the lottery un-halted? | **YES** — `MOMENTUM_LOTTERY_HALT` unset |
| Risk capped per day? | **YES** — $250 × 10 = $2,500 max deployment |
| Has the operator green-lit deployment? | **YES** — "Lets test our new strat tomorrow. Lets full send it. put it in prod." |

---

## §12 — Acknowledgments

This runbook ships in 1 session against a hard deadline (tomorrow's open). Tradeoffs accepted:

- v1 entry at 09:30 (not 10:00) — may degrade vs backtest. Will measure forward.
- v1 capped at 10 positions (not full universe). Will lift after 30 days.
- v1 uses Alpaca's screener directly (not the bot's scan_loop). Simpler, but may select different tickers than backtest used.
- v1 has NO redundant kill-switch beyond env var. Adding one would take another session.

The strategy survives walk-forward in backtest. Tomorrow we begin learning whether it survives forward time.

> The cosmos is a market. Tomorrow we participate.
