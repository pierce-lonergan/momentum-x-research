@echo off
REM doc 293: nightly $0 IV collection drip (existing Polygon entitlement, 5 calls/min, ~4h budget/night).
REM Register with (no elevation needed; Pierce action per doc 293):
REM   powershell -NoProfile -Command "Register-ScheduledTask -TaskName 'MomentumX-IVCollector' -Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c \"%REPO_ROOT%\scripts\iv_collector_nightly.cmd\"') -Trigger (New-ScheduledTaskTrigger -Daily -At '18:30')"
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python scripts\_doc293_iv_collector.py --names auto --budget 1150 >> logs\iv_collector_nightly.log 2>&1
