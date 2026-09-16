@echo off
rem doc 284 (doc-283 Step 4): daily Kalshi zero-capital shadow — snapshot -> forecast -> score.
rem Scheduled task MomentumX\KalshiShadowDoc284 runs this at 18:00 local daily.
rem NO positions are ever taken; pure collection for the frozen gate (>=200 resolved,
rem fee-adj P&L CI>0 AND Brier(LLM)<Brier(market) -> side-pocket proposal; else CLOSED).
rem Remove with: schtasks /Delete /TN "MomentumX\KalshiShadowDoc284" /F
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python scripts\kalshi_shadow_doc284.py snapshot >> logs\kalshi_shadow_doc284.log 2>&1
python scripts\kalshi_shadow_doc284.py forecast >> logs\kalshi_shadow_doc284.log 2>&1
python scripts\kalshi_shadow_doc284.py score    >> logs\kalshi_shadow_doc284.log 2>&1
