@echo off
rem doc 286 one-shot (2026-07-06 16:45 ET): tag today's phantom stop-out rows AFTER the EOD writer finishes.
rem Self-deletes its scheduled task afterward. Safe: data-hygiene write to trade_results.jsonl post-session.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python scripts\_doc286_tag_phantom_rows.py >> logs\doc286_tag_phantom.log 2>&1
schtasks /Delete /TN "MomentumX\Doc286TagPhantomOnce" /F >> logs\doc286_tag_phantom.log 2>&1
