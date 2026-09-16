# doc 255 - daily ROCKET WATCHLIST + collection engine (NO capital, research logging only).
# Scheduled as MomentumX-RocketWatchlist at 19:15, after MomentumX-DataIngest (17:30) refreshes the warehouse.
# --catchup self-heals (re-runs from a couple days back; de-dup makes re-runs safe). Posts the running
# lead-vs-outcome report to the OPS_WATCHLIST Discord channel so the accumulating collection is visible.
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Split-Path -Parent $PSScriptRoot)
$log = "data\research\rocket_watchlist_cron.log"
$rpt = "data\research\rocket_watchlist_report.txt"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"`n===== $ts  rocket-watchlist catchup =====" | Out-File -Append -Encoding utf8 $log
# doc 260: --llm adds the LLM red-flag/fader score per gapper (prospective OOS validation of the Stage-B fader-detector).
& "C:\Python313\python.exe" "scripts\rocket_watchlist_engine_doc255.py" --catchup --llm *>> $log
& "C:\Python313\python.exe" "scripts\rocket_watchlist_engine_doc255.py" --report  *> $rpt
Get-Content $rpt | Out-File -Append -Encoding utf8 $log
& "C:\Python313\python.exe" "scripts\send_discord_file.py" --webhook OPS_WATCHLIST_WEBHOOK_URL --file $rpt --message "📊 Daily rocket watchlist + collection (doc 255) — accumulating the insider/coiled leads toward power." *>> $log
