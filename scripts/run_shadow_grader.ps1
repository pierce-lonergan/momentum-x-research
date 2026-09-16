# doc 262 - daily SAME-DAY shadow-vs-prod grader (NO capital, read-only).
# Scheduled as MomentumX-ShadowGrader at 19:30 ET (after PaperTrading EOD ~16:00, Lottery, FaderShort,
# RocketShadow 19:00, RocketWatchlist 19:15). Fetches same-day prod-trade outcomes via Polygon REST
# (sidesteps the next-day flat-file warehouse lag) and posts the grade to OPS_ALERT Discord.
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Split-Path -Parent $PSScriptRoot)
$log = "data\research\shadow_grader_cron.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"`n===== $ts  shadow-vs-prod grader =====" | Out-File -Append -Encoding utf8 $log
# Skip weekends (no trading session to grade)
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Saturday" -or $dow -eq "Sunday") {
    "Weekend ($dow) - no session to grade. Exiting." | Out-File -Append -Encoding utf8 $log
    exit 0
}
# Load secrets so POLYGON_API_KEY + OPS_ALERT_WEBHOOK_URL are in env
$SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env"
if (Test-Path $SecretsFile) {
    Get-Content $SecretsFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            [Environment]::SetEnvironmentVariable($line.Substring(0,$idx).Trim(), $line.Substring($idx+1).Trim(), "Process")
        }
    }
}
& "C:\Python313\python.exe" "scripts\shadow_vs_prod_grader.py" --discord *>> $log
$graderExit = $LASTEXITCODE
"shadow grader done (exit $graderExit)" | Out-File -Append -Encoding utf8 $log
# doc 269 B3: nightly pipeline health check rides the same slot (one green/red line to Discord).
& "C:\Python313\python.exe" "scripts\pipeline_health_check.py" --discord *>> $log
"health check done (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $log
# doc 272 C2: concurrent-experiment registry rides the same slot (read-only over the durable
# streams, never raises, always exits 0 — logged but never gates the scheduled task).
& "C:\Python313\python.exe" -m src.research.experiment --date (Get-Date -Format "yyyy-MM-dd") *>> $log
"research experiments done (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $log
# doc 272 A2: incident pager rides the same slot (DORMANT-C). Tails data/ops/incidents_<date>.jsonl,
# pages NEW CRITICALs to OPS_ALERT. DRY (logs only) unless INCIDENT_PAGER_ARMED=1 is in the secrets env.
# For true ~60s real-time paging, see the 5-min Task Scheduler loop documented in scripts/incident_pager.py.
& "C:\Python313\python.exe" "scripts\incident_pager.py" --once *>> $log
"incident pager done (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $log
# doc 269 census defect #4: was unconditional `exit 0`, masking grader crashes from Task Scheduler.
exit $graderExit
