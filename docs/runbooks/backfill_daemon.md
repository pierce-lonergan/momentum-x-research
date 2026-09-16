# Backfill Daemon Runbook

**Purpose:** Operate the D221 backfill agent (`src/backfill_agent/`) in production —
configured tonight, will be enabled after D220 session 2 confirms stable.

**Audience:** Future-Pierce at 6:47 AM. Every command on this page is meant to be
copy-pasteable. No SQL composition under time pressure. No PowerShell composition
under time pressure.

---

## 0. One-time install (run ONCE, requires admin)

The Task Scheduler entry was generated tonight as XML at
`scripts/backfill_daemon_task.xml`. Register it from an **elevated** PowerShell:

```powershell
# Elevated PowerShell (Run as Administrator)
schtasks /Create /TN "MomentumX-Backfill-Daemon" /XML "<repo-root>\scripts\backfill_daemon_task.xml" /F
```

The XML ships with `<Enabled>false</Enabled>`. Verify with:

```powershell
Get-ScheduledTask -TaskName MomentumX-Backfill-Daemon | Format-Table TaskName, State
# Expect: State = Disabled
```

To verify the registration matches the shipped XML byte-for-byte:

```powershell
Get-ScheduledTask -TaskName MomentumX-Backfill-Daemon | Export-ScheduledTask
```

---

## 1. Status (one command)

```powershell
cd "<repo-root>"
python -m src.backfill_agent --status
```

Reads `data/backfill_agent/heartbeat.json` + `queue.db`. Returns in <1 second.
Read-only — safe to run any time.

**What to look at:**
- `Heartbeat: ...` — the timestamp + age. **Stale > 2 minutes** = problem.
- `[!] PAUSED` — sentinel file present, daemon is idling.
- `[!] DRY-RUN MODE` — daemon is not making API calls.
- Queue: `pending` / `completed` / `failed_*` counts.
- `throughput_1h` and `throughput_24h` — items/hour.
- `ETA at current rate` — how long until queue empty.

---

## 2. Pause (one command — sentinel file)

```powershell
cd "<repo-root>"
New-Item -Path data\backfill_agent\PAUSED -ItemType File -Force
```

Daemon polls for this file every 60 seconds. When present:
- Stops claiming new work
- Lets in-flight items finish
- Heartbeat continues to update (so monitoring doesn't go stale)
- Logs `coordinator: PAUSED — sentinel file ... exists. Idling until removed.`

**To resume:**

```powershell
Remove-Item data\backfill_agent\PAUSED -Force
```

The next poll cycle (≤60s) sees the sentinel gone, logs `PAUSE sentinel cleared
— resuming work`, and starts claiming items again.

---

## 3. Falling behind production (raise headroom %)

If `--status` shows the queue is growing faster than throughput, or production's
4:30 AM scan shows latency spikes that correlate with backfill activity, lower
the backfill share.

**Quickest knob — reduce concurrency:**

```powershell
# 1. Disable Task Scheduler entry so the next hourly trigger doesn't restart at high concurrency
Disable-ScheduledTask -TaskName MomentumX-Backfill-Daemon
# 2. Pause the running daemon (it'll finish in-flight then idle)
New-Item -Path data\backfill_agent\PAUSED -ItemType File -Force
# 3. Wait 5-10 min, verify in-flight = 0, then stop the daemon (see section 4)
# 4. Re-enable Task Scheduler with workers=1 (edit scripts/run_backfill_daemon.ps1
#    line containing --workers 3 → --workers 1, save, then Enable-ScheduledTask)
```

**Slower knob — change rate budget:**
Edit `src/backfill_agent/budget.py` `WINDOW_RATE_PER_MIN` dict:
- `OFF_HOURS: 80` → drop to `40` (halves backfill rate during 16:00-04:25 ET)
- `WEEKEND: 150` → drop to `80`
- `LIMITED: 20` → drop to `5` (further reduces during trading hours)

After editing, restart the daemon (section 5).

---

## 4. Stop (graceful shutdown)

The daemon handles SIGINT, SIGTERM (POSIX), and SIGBREAK (Windows Ctrl+Break).
On Windows there is no clean SIGTERM equivalent for a Task-Scheduler-launched
process; use:

```powershell
# Option A: Stop via Task Scheduler (graceful — sends Ctrl+Break to the powershell process)
Stop-ScheduledTask -TaskName MomentumX-Backfill-Daemon
```

If that doesn't terminate within 30s (the in-flight drain timeout):

```powershell
# Option B: Find the daemon process and kill it. Items in_progress at kill time
# will be auto-released back to pending after 10 minutes.
Get-Process powershell, python | Where-Object { $_.CommandLine -like "*backfill_agent*" } | Stop-Process -Force
```

After kill, items mid-fetch are LEFT in_progress for up to 10 minutes; the next
worker run auto-releases them. Bar-file writes are atomic (tempfile + rename) —
no corrupted files possible.

---

## 5. Restart

```powershell
# 1. Make sure no daemon is running
Get-Process python | Where-Object { $_.CommandLine -like "*backfill_agent*" }
# 2. Make sure the PAUSED sentinel is removed
Remove-Item data\backfill_agent\PAUSED -Force -ErrorAction SilentlyContinue
# 3. Trigger immediately (don't wait for the hourly watchdog)
Start-ScheduledTask -TaskName MomentumX-Backfill-Daemon
# 4. Verify it's running within 30 sec
python -m src.backfill_agent --status
```

The Task Scheduler entry has an `IgnoreNew` multiple-instances policy, so
`Start-ScheduledTask` while one is already running is a no-op (safe).

---

## 6. Inspect failed items

Failed items are in `data/backfill_agent/queue.db` with `status = 'failed_permanent'`
or `'failed_transient'`. Use the SQLite CLI or any inspector.

**View recent failures (most recent 20):**

```powershell
python -c "import sqlite3; conn=sqlite3.connect('data/backfill_agent/queue.db'); [print(r) for r in conn.execute('SELECT ticker, date, status, attempt_count, substr(last_error,1,80) FROM work_items WHERE status LIKE ''failed_%'' ORDER BY last_attempt_at DESC LIMIT 20')]"
```

**Count failures by error pattern:**

```powershell
python -c "import sqlite3, re, collections; conn=sqlite3.connect('data/backfill_agent/queue.db'); rows = conn.execute('SELECT last_error FROM work_items WHERE status LIKE ''failed_%'''').fetchall(); c = collections.Counter(re.sub(r'\\d+', 'N', (r[0] or '')[:60]) for r in rows); [print(f'{n:>4}  {k}') for k,n in c.most_common(10)]"
```

**View one specific failure:**

```powershell
python -c "import sqlite3; conn=sqlite3.connect('data/backfill_agent/queue.db'); r = conn.execute('SELECT * FROM work_items WHERE ticker=? AND date=?', ('TICKER_HERE', 'YYYY-MM-DD')).fetchone(); print(r)"
```

---

## 7. Manually mark items `failed_permanent`

Useful when a ticker is permanently delisted, or you want to skip a known-bad date:

```powershell
# Mark a single item permanent
python -c "import sqlite3; conn=sqlite3.connect('data/backfill_agent/queue.db'); conn.execute('UPDATE work_items SET status=''failed_permanent'', last_error=''manual: skipped'' WHERE ticker=? AND date=?', ('TICKER', 'YYYY-MM-DD')); conn.commit(); print('marked')"

# Mark all items for a single date permanent (e.g. SEC outage day)
python -c "import sqlite3; conn=sqlite3.connect('data/backfill_agent/queue.db'); n = conn.execute('UPDATE work_items SET status=''failed_permanent'', last_error=''manual: skipped'' WHERE date=? AND status=''pending''', ('YYYY-MM-DD',)).rowcount; conn.commit(); print(f'marked {n}')"

# Restore a permanent failure back to pending (changed your mind)
python -c "import sqlite3; conn=sqlite3.connect('data/backfill_agent/queue.db'); conn.execute('UPDATE work_items SET status=''pending'', last_error=NULL, attempt_count=0 WHERE ticker=? AND date=?', ('TICKER', 'YYYY-MM-DD')); conn.commit(); print('restored')"
```

---

## Watchdog gap caveat (worth knowing)

The Task Scheduler entry uses an **hourly trigger** + `IgnoreNew` policy. If the
daemon dies at 10:03 AM, the next "start if not running" check fires at 11:00 AM —
**a 57-minute gap with no work happening**. For a backfill that's been cold for 48
hours, this is nothing; for a backfill that's catching up to a new live session,
it can feel anxious.

**This is intentional, not a bug.** The hourly cadence trades off:
- Faster restart (every 5 min) = more retry storms after a real bug
- Slower restart (every 6 hours) = problematic for catch-up scenarios
- Hourly = sweet spot for a non-time-critical backfill

If you actually need faster restart, edit `scripts/backfill_daemon_task.xml` line
`<Interval>PT1H</Interval>` to `<Interval>PT15M</Interval>` and re-import the XML
via the elevated `schtasks /Create /XML ... /F` command from section 0.

---

## What the daemon is NOT doing (that you might expect)

- **Not auto-triggering composite retrains.** Manual gate via
  `./scripts/maybe_retrain_composite.sh` — see `docs/research-log/14_d221_phase_c_validation.md`.
- **Not appending to `features_labeled.jsonl`.** Writes per-day shards to
  `data/backfill/labels_shards/labels_<date>.jsonl` to avoid file contention.
  Consolidate to the single file when you're ready by concatenating all shards.
- **Not running during 04:25-09:35 ET.** Hard-coded PAUSE window for the
  production scan. See `src/backfill_agent/budget.py:WINDOW_RATE_PER_MIN`.
- **Not pulling from any source other than Alpaca.** No Polygon, Ortex, social.
  That's Phase F next week.

---

## Enable for the first time (after D220 session 2 confirms)

```powershell
Enable-ScheduledTask -TaskName MomentumX-Backfill-Daemon
Start-ScheduledTask -TaskName MomentumX-Backfill-Daemon
sleep 30
python -m src.backfill_agent --status   # verify heartbeat is fresh
```

If anything looks wrong in the first hour:

```powershell
New-Item -Path data\backfill_agent\PAUSED -ItemType File -Force
Disable-ScheduledTask -TaskName MomentumX-Backfill-Daemon
```

You're back to a clean stop in two commands.
