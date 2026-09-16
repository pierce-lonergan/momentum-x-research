@echo off
REM DOC 298 - forward option-NBBO collection. The dataset that cannot be backfilled.
REM
REM Historical option NBBO is NOT served on this plan (doc 297 verified: 404 on every endpoint shape).
REM Daily bars carry no bid/ask. So spread-realistic option data exists only from the day collection
REM starts, and every missed session is permanently missing. Run this once per trading day, near the
REM close, so snapshots are comparable session to session.
REM
REM Read-only against the broker (GET endpoints only; this path contains no order code).
REM
REM PIERCE - register it once with this one-liner in an ELEVATED PowerShell:
REM
REM   Register-ScheduledTask -TaskName "momentum-option-nbbo" -Trigger (New-ScheduledTaskTrigger -Daily -At 3:50PM) -Action (New-ScheduledTaskAction -Execute "%REPO_ROOT%\scripts\option_nbbo_nightly.cmd") -Description "doc 298 forward option NBBO snapshot"
REM
REM Cost: ~40-60 GET calls per session against a 10,000/min limit. Effectively free.

cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python scripts\option_nbbo_collector.py --collect --dte-max 45 --max-calls 400 >> data\option_nbbo\collector.log 2>&1
exit /b 0
