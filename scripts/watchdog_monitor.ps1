<#
.SYNOPSIS
    D217: External Watchdog Monitor for Momentum-X

.DESCRIPTION
    Runs as a SEPARATE scheduled task (every 2 minutes during market hours).
    Checks heartbeat file staleness and /health endpoint.
    If stale, captures py-spy dump then kills and restarts.
    Circuit breaker: stops auto-restarting after 3 restarts in 60 minutes.

    Schedule: Task Scheduler -> every 2 minutes, 4:00 AM - 5:00 PM ET

.PARAMETER ProjectRoot
    Path to the momentum-x project root.

.PARAMETER WebhookUrl
    Optional Discord/Slack webhook URL for alerts.
#>

param(
    [string]$ProjectRoot = "$env:USERPROFILE\Documents\GitHub\momentum-x",
    [string]$WebhookUrl = ""
)

$ErrorActionPreference = "Continue"

# doc 270 fleet-assessment fix: NO WEEKEND GUARD — on Sunday 6/7 the watchdog tripped its own circuit
# breaker and logged ALERT every 2 minutes; with the webhook now wired (43a3e02) that becomes ~200
# Discord posts next Saturday. Markets are closed; there is nothing to watch.
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Saturday" -or $dow -eq "Sunday") {
    exit 0
}

# doc 269 census defect #1: the Task Scheduler action passes only -ProjectRoot, so $WebhookUrl was always ""
# and circuit-breaker/kill alerts (the highest-stakes infra alarms) only ever reached the log file.
# Self-load OPS_ALERT_WEBHOOK_URL from the secrets file when not supplied (avoids putting the secret in the
# task definition). Same parse pattern as the other launchers.
if (-not $WebhookUrl) {
    $SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env"
    if (Test-Path $SecretsFile) {
        foreach ($line in Get-Content $SecretsFile) {
            if ($line -match '^\s*OPS_ALERT_WEBHOOK_URL\s*=\s*(.+)\s*$') {
                $WebhookUrl = $matches[1].Trim('"').Trim("'")
                break
            }
        }
    }
}

# ── Configuration ──
$HeartbeatFile = Join-Path $ProjectRoot "data\heartbeat.json"
$HealthUrl = "http://localhost:9091/health"
$WatchdogLog = Join-Path $ProjectRoot "logs\watchdog.log"
$RestartTracker = Join-Path $ProjectRoot "data\watchdog_restarts.json"
# D218 FIX: Use full path — py-spy is installed in user Scripts, not system PATH
$PySpy = Join-Path $env:APPDATA "Python\Python313\Scripts\py-spy.exe"
if (-not (Test-Path $PySpy)) {
    # Fallback to system PATH
    $PySpy = "py-spy"
}

# Staleness thresholds (seconds)
$now_et = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), "Eastern Standard Time")
$hour_et = $now_et.Hour
$minute_et = $now_et.Minute

# Tighter during opening window (9:28-10:00 ET)
# Tue 2026-04-21 fix: doubled both intraday tiers. Today's 10:18 kill
# happened because a single scan iteration with 11.8s/agent latency × N
# candidates exceeded the 120s threshold by 5.5 seconds. The intra-loop
# heartbeat keeper task added in cmd_paper (main.py) pulses every 30s
# independent of the main loop, so any threshold above ~60s will be
# satisfied as long as the event loop itself is alive. The doubled
# values (180s opening, 240s intraday) preserve responsiveness to
# genuinely hung processes (event-loop deadlock) while eliminating the
# false-positive kills from normal-but-slow scan iterations.
if ($hour_et -eq 9 -and $minute_et -ge 28) {
    $MaxStaleness = 180  # opening: 3 min (was 90s, doubled)
} elseif ($hour_et -ge 9 -and $hour_et -lt 16) {
    $MaxStaleness = 240  # intraday: 4 min (was 120s, doubled)
} else {
    $MaxStaleness = 900  # off-hours: 15 min (unchanged)
}

# Circuit breaker: max 3 restarts per 60 minutes
$MaxRestartsPerHour = 3

# ── Logging ──
function Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [WATCHDOG] [$Level] $Message"
    Write-Host $line
    try { Add-Content -Path $WatchdogLog -Value $line } catch {}
}

# ── Discord/Slack Webhook ──
function Send-Alert {
    param([string]$Message)
    Log $Message "ALERT"
    if ($WebhookUrl) {
        try {
            $body = @{ content = "[MomentumX Watchdog] $Message" } | ConvertTo-Json
            Invoke-RestMethod -Uri $WebhookUrl -Method POST -Body $body -ContentType "application/json" -TimeoutSec 5 | Out-Null
        } catch {
            Log "Webhook failed: $_" "WARN"
        }
    }
}

# ── Circuit Breaker Check ──
function Test-CircuitBreaker {
    if (-not (Test-Path $RestartTracker)) { return $false }
    try {
        $tracker = Get-Content $RestartTracker -Raw | ConvertFrom-Json
        $cutoff = (Get-Date).AddHours(-1)
        $recent = @($tracker.restarts | Where-Object { [datetime]$_ -gt $cutoff })
        if ($recent.Count -ge $MaxRestartsPerHour) {
            return $true  # Circuit breaker TRIPPED
        }
    } catch {}
    return $false
}

function Add-RestartRecord {
    try {
        $tracker = @{ restarts = @() }
        if (Test-Path $RestartTracker) {
            $tracker = Get-Content $RestartTracker -Raw | ConvertFrom-Json
        }
        # Keep only last 24 hours
        $cutoff = (Get-Date).AddHours(-24)
        $recent = @($tracker.restarts | Where-Object { [datetime]$_ -gt $cutoff })
        $recent += (Get-Date).ToString("o")
        @{ restarts = $recent } | ConvertTo-Json | Set-Content $RestartTracker
    } catch {}
}

# ── Main Check ──
Log "Watchdog check starting (threshold=${MaxStaleness}s, hour_et=${hour_et}:${minute_et})"

# 1. Check heartbeat file staleness
$isStale = $false
$staleReason = ""

if (-not (Test-Path $HeartbeatFile)) {
    # No heartbeat file — system may not have started yet
    # Only alert during market hours
    if ($hour_et -ge 9 -and $hour_et -lt 16) {
        $isStale = $true
        $staleReason = "No heartbeat file found during market hours"
    } else {
        Log "No heartbeat file (pre-market, not alarming)"
        exit 0
    }
} else {
    try {
        $hb = Get-Content $HeartbeatFile -Raw | ConvertFrom-Json
        # D218 FIX: Use DateTimeOffset to correctly parse UTC timestamps.
        # [datetime] casts UTC timestamps to local time (Kind=Local), then
        # comparing with (Get-Date).ToUniversalTime() creates a phantom
        # offset equal to the timezone difference (4 hours for EDT).
        # This caused the watchdog to always read heartbeats as hours stale
        # and kill healthy processes.
        $hbTime = [DateTimeOffset]::Parse($hb.timestamp)
        $age = ([DateTimeOffset]::UtcNow - $hbTime).TotalSeconds

        if ($age -gt $MaxStaleness) {
            $isStale = $true
            $staleReason = "Heartbeat stale: ${age}s old (limit=${MaxStaleness}s), last_function=$($hb.last_function), phase=$($hb.phase), pid=$($hb.pid)"
        } else {
            Log "Heartbeat OK: age=${age}s, phase=$($hb.phase), positions=$($hb.positions), trades=$($hb.trades_today)"
        }
    } catch {
        $isStale = $true
        $staleReason = "Heartbeat file corrupted: $_"
    }
}

# 2. Also check /health endpoint (if heartbeat is OK, double-check HTTP)
if (-not $isStale) {
    try {
        $resp = Invoke-RestMethod -Uri $HealthUrl -Method GET -TimeoutSec 5
        Log "Health endpoint OK: $($resp | ConvertTo-Json -Compress)"
    } catch {
        # HTTP check failed but heartbeat was fine — log warning but don't restart
        # (server might not be wired yet, or port conflict)
        Log "Health endpoint unreachable (heartbeat OK, non-fatal): $_" "WARN"
    }
    exit 0
}

# ── System is stale — take action ──
Log $staleReason "CRITICAL"

# D218 FIX: Check if the stale heartbeat's PID is actually running.
# If not, the process already died on its own — just clear the stale
# heartbeat and don't count this as a restart (don't trip circuit breaker
# by killing a PID that no longer exists).
$stalePid = $null
if (Test-Path $HeartbeatFile) {
    try {
        $hb = Get-Content $HeartbeatFile -Raw | ConvertFrom-Json
        $stalePid = $hb.pid
    } catch {}
}
if ($stalePid) {
    $procCheck = Get-Process -Id $stalePid -ErrorAction SilentlyContinue
    if (-not $procCheck) {
        Log "Stale heartbeat PID $stalePid is NOT running (already dead). Clearing heartbeat, not counting as restart." "WARN"
        # Clear the stale heartbeat so next check starts fresh
        try { Remove-Item $HeartbeatFile -Force -ErrorAction SilentlyContinue } catch {}
        exit 0
    }
}

# doc 287: the watchdog can only KILL a hung process -- it has NO ability to LAUNCH one. So there are
# two structurally different "stale" cases and the old code conflated them, which is why 7/8 and 7/10
# (preflight aborted at 04:30 -> the bot never started -> no heartbeat file ever existed) produced ~200
# false Discord alerts each: the old code counted a "restart" for a process it never killed, claimed a
# kill + auto-restart that never happened, then tripped the breaker every 2 min claiming "restarted 3
# times" (it restarted zero). Split the cases explicitly:
#   (A) NO process exists  -> nothing to kill; the watchdog cannot fix this; alert the operator ONCE per
#                             hour (deduped) with the truth ("not running; manual start required") and
#                             DO NOT record a phantom restart / trip the breaker.
#   (B) A process is HUNG   -> the designed-for case: py-spy dump, kill, record a REAL restart, and rely
#                             on the launcher relaunch (still operator-owned; see doc 287 recommendation).
# NB: keep this section ASCII-only -- this file is UTF-8 without BOM and Windows PowerShell 5.1 -File
# reads it as Windows-1252, so a non-ASCII char inside an executable string breaks parsing.

# 3. Get PID from heartbeat file (if available) or find the momentum-x Python process
$targetPid = $stalePid
if (-not $targetPid) {
    $proc = Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object {
            try {
                $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
                $cmd -match "main\s+paper" -or $cmd -match "momentum"
            } catch { $false }
        } | Select-Object -First 1
    if ($proc) { $targetPid = $proc.Id }
}

# == Case (A): no process exists at all -- the watchdog cannot relaunch it ==
if (-not $targetPid) {
    Log "No momentum-x process running during market hours. Reason: $staleReason. The watchdog cannot launch the bot; this needs a manual start / launcher relaunch." "CRITICAL"
    # Dedup the Discord alarm to at most once per hour (each watchdog run is a fresh 2-min process, so
    # state must live on disk) -- this is what turned one blip into ~200 posts.
    $noProcTracker = Join-Path $ProjectRoot "data\watchdog_noproc_alert.json"
    $shouldAlert = $true
    if (Test-Path $noProcTracker) {
        try {
            $last = (Get-Content $noProcTracker -Raw | ConvertFrom-Json).last_alert
            if ($last -and ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($last)).TotalMinutes -lt 60) {
                $shouldAlert = $false
            }
        } catch {}
    }
    if ($shouldAlert) {
        Send-Alert "momentum-x is NOT running during market hours ($staleReason). The watchdog cannot start it -- MANUAL START / launcher relaunch required. (Suppressing repeats for 60 min.)"
        try { @{ last_alert = ([DateTimeOffset]::UtcNow).ToString("o") } | ConvertTo-Json | Set-Content $noProcTracker } catch {}
    } else {
        Log "No-process alarm already sent within the last 60 min; suppressing duplicate Discord post." "INFO"
    }
    exit 1
}

# == Case (B): a process exists but is hung -- kill it (the designed-for path) ==
# 4. Circuit breaker only governs REAL restarts (kills). Checked here, after we know a process exists,
#    so a no-process day can never trip it.
if (Test-CircuitBreaker) {
    Send-Alert "CIRCUIT BREAKER TRIPPED: a hung momentum-x process has been killed $MaxRestartsPerHour times in the last hour. NOT killing again. Manual intervention required. Last issue: $staleReason"
    exit 1
}

# 5. Capture py-spy dump BEFORE killing (forensics for next investigation)
$dumpFile = Join-Path $ProjectRoot "logs\pspy_dump_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').txt"
Log "Capturing py-spy dump for PID $targetPid -> $dumpFile"
try {
    & $PySpy dump --pid $targetPid 2>&1 | Out-File -FilePath $dumpFile -Encoding utf8
    Log "py-spy dump captured: $dumpFile"
} catch {
    Log "py-spy dump failed (may not be installed): $_" "WARN"
    try {
        "Process $targetPid thread info:" | Out-File -FilePath $dumpFile -Encoding utf8
        Get-Process -Id $targetPid | Format-List * | Out-File -FilePath $dumpFile -Append -Encoding utf8
    } catch {}
}

# 6. Kill the hung process
$killed = $false
Log "Killing hung process PID $targetPid"
try {
    Stop-Process -Id $targetPid -Force
    Start-Sleep -Seconds 2
    $killed = $true
    Log "Process killed"
} catch {
    Log "Failed to kill PID ${targetPid}: $_" "ERROR"
}

# 7. Record a restart for the circuit breaker ONLY when a kill actually happened
if ($killed) {
    Add-RestartRecord
    # NB: relaunch is still owned by the launcher / Task Scheduler RestartOnFailure -- which was proven
    # NOT to fire on 7/8 and 7/10 (doc 287). The watchdog cannot guarantee the bot comes back; say so.
    Send-Alert "Hung momentum-x process (PID $targetPid) was killed. Reason: $staleReason. Relaunch is expected via the launcher; if no heartbeat returns within ~10 min, MANUAL restart required. py-spy dump saved to logs/."
} else {
    Send-Alert "Hung momentum-x process (PID $targetPid) could NOT be killed. Reason: $staleReason. MANUAL intervention required."
}

Log "Watchdog check complete"
