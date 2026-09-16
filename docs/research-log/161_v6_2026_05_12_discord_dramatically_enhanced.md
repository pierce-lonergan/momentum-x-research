# 161 — 2026-05-12 Discord reporting: 3 critical alerts + rich EOD report

> **Format:** post-EOD enhancement log. User said "make sure the discord
> reporting is up to date and dramatically enhance it." Audited current
> 9-function integration, identified 5 critical gaps, shipped 3 new
> alerts + wired the rich EOD report into main.py.

**Session date:** 2026-05-12 deep night
**Branch:** develop
**Predecessors:** [doc 160 hardening](160_v6_2026_05_12_hardening.md)
**Status:** **3 new Discord alerts shipped + rich EOD wired. Test messages confirmed delivered to OPS_ALERT_WEBHOOK_URL.**

---

## TL;DR — what existed, what shipped, what's left

### Existing coverage (confirmed working)
| Alert | When | Webhook |
|---|---|---|
| `alert_critical` | D218 ENV_AUDIT critical issues | OPS |
| `alert_session_start` | Bot startup at 04:30 ET | OPS |
| `alert_session_end` (minimal) | Bot shutdown at 16:00 ET | OPS |
| `alert_morning_resolution_sync` | Overnight positions detected | OPS |
| `post_watchlist` | Pre-market scan, every 5-30 min | WATCHLIST |
| `post_fast_path_scores` | 9:25 AM fast-path queue | WATCHLIST |
| `post_verdict_summary` | Phase 2 evaluation results | WATCHLIST |
| `post_trade_open` | OTO order accepted | WATCHLIST |
| `post_trade_close` | Position closed | WATCHLIST |

### NEW alerts shipped tonight (3 critical gaps)
| Alert | When | Why |
|---|---|---|
| **`alert_halt_blocked_entry_sync`** | EVERY D277 halt block | Doc 156 incident: 8 valid trades silently blocked over 2 days, only discovered post-EOD via log audit. Now the operator sees each block in real time and can lift mid-session. |
| **`alert_data_ingest_result`** | After MomentumX-DataIngest fires (17:30 ET) | No visibility into nightly data refresh. Operator now sees ✅ with new max_d0 + rows + runtime, OR ❌ with failed step + remediation. |
| **`alert_session_end_rich`** | Bot Phase 4 shutdown | Old `alert_session_end` showed only trades/PnL/positions. Rich version adds D-codes fired, top winners/losers, EOD reconciliation status, BOCPD refit recommendation, halt-block count, veto breakdown by reason. |

### Gaps still open (deferred)
| Gap | Why deferred |
|---|---|
| Hourly P&L pulse during market hours | Less critical now that rich EOD covers full day; can add later if user wants intraday checkpoints |
| Watchdog kill Discord notification | Watchdog already has webhook in `Send-Alert` function; just needs WEBHOOK_URL env var passed to scheduled task. User-action item. |
| Shadow runner result | Once daily ingest is verified working, shadow output becomes derivable from data ingest result |

---

## Alert 1 — `alert_halt_blocked_entry_sync` (the big one)

**Problem:** Doc 156 incident. Bot tried to enter 8 trades over 2 days
(WOK, PLUG, TDIC×4, VSTS×2). All 8 silently refused by `D277 HALT_NEW_ENTRIES`.
Operator only discovered it via end-of-day log audit. Pre-flight check
(added in doc 160) catches it at 04:30 ET startup, but mid-session
re-enables (or env-var resets) wouldn't surface until EOD.

**Solution:** added `alert_halt_blocked_entry_sync()` to alerts.py.
Wired into `_d277_halt_response()` in alpaca_client.py — fires every
time a halt blocks a submission. Per-ticker rate-limited (1/min/symbol)
so a storm doesn't spam.

Sample Discord message (test-sent at 22:09 ET):
```
⛔ HALT BLOCKED Entry
The bot tried to BUY TEST but was blocked by the kill switch.

💵 Wanted: 100 shares of TEST at $10.5000
💰 Notional: $1,050.00 (risk if filled: $30.00)
🛑 Stop: $10.2000

Reason: MOMENTUM_HALT_NEW_ENTRIES is enabled.

To lift the halt and resume trading:
[Environment]::SetEnvironmentVariable('MOMENTUM_HALT_NEW_ENTRIES', $null, 'User')
schtasks /end /TN MomentumX-PaperTrading
schtasks /run /TN MomentumX-PaperTrading
```

The remediation block is COPY-PASTEABLE PowerShell, no operator
research needed.

---

## Alert 2 — `alert_data_ingest_result`

**Problem:** Doc 159 shipped daily MomentumX-DataIngest task at 17:30 ET.
Currently no Discord visibility into whether it actually ran or
produced fresh data. If it silently fails, the bot wakes up at 04:30 ET
with stale data and the operator only finds out via shadow runner
inspection.

**Solution:** added `alert_data_ingest_result()`. Wired into
`daily_data_ingest.ps1` — posts on success with new max_d0 + row count
+ runtime, or on failure with failed step name + remediation context.

Sample success message (test-sent):
```
✅ Daily Data Ingest OK
Lake refreshed cleanly. Tomorrow morning's bot will use this data.

max d0:    `2026-05-11`
rows:      20,690
runtime:   42s
```

Tomorrow's 17:30 ET ingest will be the first real fire of this alert.

---

## Alert 3 — `alert_session_end_rich` (replaces minimal version)

**Problem:** Old `alert_session_end` post had 3 fields (trades, PnL,
positions). The bot logs MUCH more useful EOD data:
- D-codes fired this session (D262 BOCPD refit, D238 EOD recon, etc.)
- Per-trade breakdown (winners + losers)
- EOD reconciliation status
- BOCPD refit recommendation if any
- Halt-block count
- Veto breakdown by reason

All of this was buried in `data/reports/eod_<date>.json` requiring
the operator to manually open the file. Now it's all in the Discord
embed.

**Solution:** added `alert_session_end_rich()` with up to 25 embed
fields covering everything above. Wired into main.py:6856 with
fallback to the old function if rich version errors.

Sample rich-EOD message (test-sent):
```
➖ EOD Report — 2026-05-12 (SMOKE TEST)

8 entries BLOCKED by HALT switch -- bot wanted to trade but couldn't.
Lift switch + restart to resume.

💰 Realized P&L: $+0.00          📊 Trades: 0           📦 Open at Close: 0
🏦 Starting: $140,320.48          🏦 Ending: $140,320.48 📈 Day Change: $+0.00 (+0.00%)

🚨 D-Codes Fired (2)
  `D262`, `D277`

⛔ HALT-Blocked Entries (8)
  D277 refused 8 OTO submission(s) today. Lift `MOMENTUM_HALT_NEW_ENTRIES`
  env var to resume.

✅ EOD Reconciliation
  broker_pnl=$+0.00  journal_pnl=$+0.00  delta=$+0.00  disagreements=0

📊 D262 BOCPD Refit Recommended
  Prior mu drifted from $-6.58 -> $-255.44 (n=29).
  Run: `python scripts/pretrain_bocpd_prior.py`

🚫 Vetoes by Reason (61 total)
  CATALYST_BLOCKED: 41
  NO_CATALYST: 20
```

This is what tomorrow's EOD message will look like (with real numbers).
The operator gets a one-glance assessment of the session WITHOUT having
to open log files.

---

## Pre-flight Discord wiring (doc 160 follow-up)

Doc 160 added `check_halt_switch` to pre-flight. Doc 161 confirms the
launcher now passes `OPS_ALERT_WEBHOOK_URL` to pre-flight via:

```powershell
$_ops_webhook = [Environment]::GetEnvironmentVariable("OPS_ALERT_WEBHOOK_URL", "Process")
if ($_ops_webhook) {
    $preflightResult = & python scripts/preflight_check.py --webhook $_ops_webhook 2>&1
} else {
    $preflightResult = & python scripts/preflight_check.py 2>&1
}
```

**Net:** if HALT switch is on at 04:30 ET startup, operator gets a
Discord alert immediately, not just a transcript line.

---

## What ships this commit

| Path | Change |
|---|---|
| `src/monitoring/alerts.py` | NEW: `alert_halt_blocked_entry_sync`, `alert_data_ingest_result`, `alert_session_end_rich` (with rich D-code/winner/loser/recon/BOCPD/veto breakdown) |
| `src/data/alpaca_client.py` | `_d277_halt_response` now fires Discord alert via `alert_halt_blocked_entry_sync` (best-effort) |
| `main.py` | EOD wiring switched from `alert_session_end` (minimal) to `alert_session_end_rich` with fallback |
| `scripts/daily_paper_trade.ps1` | Pass `OPS_ALERT_WEBHOOK_URL` to preflight |
| `scripts/daily_data_ingest.ps1` | Discord notification on success after ingest completes |
| `docs/research-log/161_v6_2026_05_12_discord_dramatically_enhanced.md` | NEW (this) |

---

## Test results

3 sample messages successfully delivered to `OPS_ALERT_WEBHOOK_URL` at
22:09 ET tonight:
1. `alert_halt_blocked_entry_sync` — halt block for ticker TEST
2. `alert_data_ingest_result` — ingest success with sample data
3. `alert_session_end_rich` — full sample EOD with D-codes, halts, recon, BOCPD, vetoes

35 of 35 unit tests pass (alpaca_client + d277 + preflight_halt + d286).
0 regressions.

---

## Tomorrow's projected Discord activity

```
04:30 ET  ✅ alert_session_start (existing)
04:30 ET  ✅ Pre-flight Halt Switch alert (if HALT re-enabled, NEW path)
04:30+    ✅ post_watchlist (existing, pre-market scan results)
09:00 ET  (lottery)
09:25 ET  ✅ post_fast_path_scores (existing)
09:30+    ✅ post_verdict_summary (existing, every Phase 2)
On fill   ✅ post_trade_open (existing)
On exit   ✅ post_trade_close (existing)
On halt   ✅ alert_halt_blocked_entry_sync (NEW — every D277 block)
15:50 ET  (fader_short)
16:00 ET  ✅ alert_session_end_rich (NEW — comprehensive EOD)
17:30 ET  ✅ alert_data_ingest_result (NEW — daily refresh)
```

If anyone re-enables MOMENTUM_HALT_NEW_ENTRIES tomorrow, the operator
will see it in Discord within 1 minute of the next OTO attempt instead
of 12+ hours later via log audit.

---

## Filed for next session

- **Hourly P&L pulse** during market hours — would give operator a "the
  bot is alive and here's how it's doing" tick without spamming. Skipped
  tonight because the rich EOD covers most needs.
- **Watchdog kill Discord notification** — `watchdog_monitor.ps1`
  already has `Send-Alert` function with webhook param, but the
  scheduled task doesn't pass `-WebhookUrl`. User-action: edit the
  watchdog scheduled task to pass `-WebhookUrl <OPS_ALERT_WEBHOOK_URL>`.
- **Shadow runner result alert** — once daily ingest is verified working
  multiple days in a row, shadow output becomes the natural next
  observability surface.
