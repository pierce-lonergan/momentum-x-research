# 194 — Wire the post-close scorecard into the launcher (auto-measurement)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(a) wire the scorecard into the launcher's Phase-4 tail so it runs
automatically after every close" (then (b) the higher-fidelity fill model).

---

## 0. Why the launcher tail (not main.py)

The scorecard (doc 193) is post-close *analysis* that fetches the full day's forward bars
(~130 Alpaca calls). The right place is the **launcher tail**, AFTER `python -m main paper`
exits — not inside the trading process — because:
- It **cannot touch the trading hot path** (the bot has already exited).
- It **cannot affect the session exit code** (best-effort, wrapped).
- It runs **even if the bot crashed** (as long as the process exits) — partial-day data
  still scores, gracefully.
- The full day's 1-min bars are settled by the time it runs (~4 PM ET).

This also fixes the doc-192 finding that measurement was a one-off: from Monday on, every
session auto-appends its SELECTION scorecard to `data/reports/measurement_trend.jsonl`.

## 1. The change (`scripts/daily_paper_trade.ps1`)

Inserted, just before `Stop-Transcript`:

```powershell
if ($env:MX_POSTCLOSE_SCORECARD -ne "0") {
    Log "Running post-close measurement scorecard for $Today (best-effort)..."
    $_prevEAP_sc = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        Push-Location $ProjectRoot
        & python scripts/post_close_scorecard.py $Today 2>> $LogFile
        Pop-Location
        Log "Post-close scorecard complete -> data/reports/measurement_trend.jsonl"
    } catch {
        Log "Post-close scorecard failed (non-fatal): $_" "WARN"
    }
    $ErrorActionPreference = $_prevEAP_sc
}
```

- Scores **only `$Today`** (one day's bars, ~130 calls, ~2-3 min), not `--days N` — no
  re-fetching prior days each evening.
- `$ErrorActionPreference = "Continue"` + try/catch ⇒ a scorecard failure is logged and
  ignored; the launcher's `exit $exitCode` is unchanged.
- **Disable** with `$env:MX_POSTCLOSE_SCORECARD = "0"` (no edit needed).
- Validated: the launcher parses cleanly (PowerShell AST `ParseFile`, 0 errors).

## 2. Effect

From Monday's close onward, `measurement_trend.jsonl` grows one row per session — the
running record of selWin% / selMean (headline), the marketable fill edge (2nd order), and
gate-correct % (populates as the doc-182 instrumentation runs live). That trend is the
dataset the SELECTION levers (188/191/184) will be judged on before any live flip.

Deploys with the same Monday 4:30 AM auto-launch (the launcher runs the local checkout;
no manual restart).

## Appendix — files
- `scripts/daily_paper_trade.ps1` — post-close scorecard block before `Stop-Transcript`.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
