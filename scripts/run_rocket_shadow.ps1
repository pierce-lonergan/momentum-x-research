# doc 246 -- daily forward rocket PAPER SHADOW (NO capital, research logging only).
# Scheduled as MomentumX-RocketShadow at 19:00, after MomentumX-DataIngest (17:30) refreshes the warehouse.
# --catchup self-heals (re-scores from a couple days back; de-dup makes re-runs safe) so late ingests are fine.
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Split-Path -Parent $PSScriptRoot)
$log = "data\research\rocket_shadow_cron.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"`n===== $ts  rocket-shadow catchup =====" | Out-File -Append -Encoding utf8 $log
& "C:\Python313\python.exe" "scripts\rocket_shadow_doc246.py" --catchup  *>> $log
& "C:\Python313\python.exe" "scripts\rocket_shadow_doc246.py" --report   *>> $log
