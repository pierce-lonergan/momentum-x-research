# 210 — Daily Data Ingest Discord metrics fix (doc 209 Bug 6, resolved)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: resolve doc 209 Bug 6 (spawned, not rushed) — the nightly
"✅ Daily Data Ingest OK" Discord embed always showed placeholder metrics:
`max d0 ?` · `rows ?` · `runtime 0s`. A separate process from the trading
bot (the `MomentumX-DataIngest` scheduled task, fires ~17:30 ET); low risk,
cosmetic-but-useful — operators couldn't see whether the lake actually
refreshed.

File touched: `scripts/daily_data_ingest.ps1` (the launcher only). No change
to `src/monitoring/alerts.py` — its signature + rendering were already
correct (confirmed below); the bug was 100% on the PowerShell side.

---

## Root cause — three distinct defects, all in the ps1

### 1. `runtime 0s` — start timestamp captured at the END, duration off the log's atime
`$_ingest_start_ts` was declared at the very END of the script (just before the
report), *after* every pipeline step had already run. Worse, the duration was
never even computed from it — it was `($_ingest_end_ts - (Get-Item $LogFile).
LastAccessTimeUtc).TotalSeconds`, i.e. *now minus the log file's last-access
time*. The script `Add-Content`s to that log on every step, so its atime is
always ~now → `end - atime ≈ 0` → **always `0s`**.

### 2. `max d0 ?` / `rows ?` — THE deeper cause: a Timestamp space breaks the regex
This is more than doc 209's "regex can pass None". The report query is:
```python
print(f'aftermath_strat max_d0={df["max_d0"].iloc[0]}, total_rows={df["n"].iloc[0]}')
```
`MAX(d0)` comes back as a pandas **Timestamp**, which renders as
`2026-05-28 00:00:00` — **with an embedded space**. The extraction regex was:
```
max_d0=(\S+),\s*total_rows=(\d+)
```
`(\S+)` is non-whitespace, so it stops at the space (`2026-05-28`) and the very
next required char is `,` — but the actual next char is ` ` (the start of
` 00:00:00`). **The regex never matches, even on a fully successful run.** That
is why it was *always* `?`, not just on failures. Verified against the live
parquet:
```
$ python -c "...same query..."
aftermath_strat max_d0=2026-05-28 00:00:00, total_rows=21229     # <- the space
PS> $line -match "max_d0=(\S+),\s*total_rows=(\d+)"   ->   False  # proven
```

### 3. Fragility — `$matches` not populated when `$report` is an array
The query runs with `2>&1`. If duckdb ever emits a line on stderr, `$report`
becomes a `string[]`, and PowerShell's `-match` on a **collection** acts as a
*filter* and does **not** populate the automatic `$matches` variable (that only
happens for a scalar LHS). So even a corrected regex could silently yield empty
captures the moment any stderr noise appeared.

---

## The fix (3 edits, `scripts/daily_data_ingest.ps1`)

1. **Pipeline-start timestamp moved to the top.** New `$_ingest_start_ts =
   Get-Date` placed *before STEP 1a* (after the `Invoke-Step` def). Duration is
   now `[int](((Get-Date) - $_ingest_start_ts).TotalSeconds)` — real wall-clock
   of the full ingest. Integer seconds also sidesteps any locale
   decimal-separator surprise when the value is interpolated into `float('...')`.
   The misplaced end-of-script `[DateTime]::UtcNow` assignment and the
   atime-based duration line were removed.

2. **Date sliced to `YYYY-MM-DD` at the source.** `str(df["max_d0"].iloc[0])[:10]`
   in the report `print` → emits `max_d0=2026-05-28` (whitespace-free), so the
   existing `(\S+),` regex captures cleanly. Minimal change — no new quotes to
   escape inside the nested PowerShell/Python here-string.

3. **Robust extraction + explicit sentinel.** Capture `$LASTEXITCODE` of the
   report query into `$_report_exit`; flatten output with
   `($report | Out-String).Trim()` so `-match` always sees a scalar and
   populates `$matches`; gate the match on `$_report_exit -eq 0`. On query
   failure or format mismatch, pass `max_d0 = "PARSE_FAILED"` (a visible
   sentinel) instead of a silent empty string / `None`, so the operator can
   distinguish "lake refreshed but the freshness report didn't parse" from a
   genuine blank.

---

## Verification

- **AST parse**: `[System.Management.Automation.Language.Parser]::ParseFile`
  → **0 errors**, 1249 tokens. (Done per the session rule — a sibling ps1
  shipped broken earlier; doc 209/208 note "both ps1 parse clean".)
- **Extraction logic** (isolated PowerShell harness, 4 cases):
  - normal line → `max_d0=2026-05-28`, `rows=21229` ✓
  - array + stderr noise → still `2026-05-28` / `21229` (Out-String flatten works) ✓
  - query-failed (exit=1) → `PARSE_FAILED`, rows→`None` ✓
  - **old timestamp-with-space line vs old regex → `False`** (root cause proven) ✓
- **End-to-end embed render** (offline; monkeypatched `alerts._post`, nothing
  left the machine):
  | inputs | max d0 | rows | runtime |
  |---|---|---|---|
  | FIXED run (`2026-05-28`, 21229, 247.0) | `` `2026-05-28` `` | **21,229** | 247s |
  | OLD bug (`""`, None, 0.0) | `` `?` `` | ? | ? | *(reproduces what operators saw)* |
  | sentinel (`PARSE_FAILED`, None, 247.0) | `` `PARSE_FAILED` `` | ? | 247s |
- **Parquet source confirmed**: `data/polygon_warehouse/derived/aftermath_strat.parquet`
  is the canonical derived catalog (`DERIVED / "aftermath_strat.parquet"` used by
  15+ scripts — build_intraday_paths, the ml_* models, microstructure builders).
  Present, 1.6 MB, mtime 5/29 17:30, `max_d0=2026-05-28` (the expected T-1 lag the
  script itself documents: today's day_aggs flat file isn't published until
  ~04:00 ET tomorrow).
- **alerts.py confirmed (no change)**: `alert_data_ingest_result` is keyword-only
  (`*`) with `success/aftermath_strat_max_d0/rows/duration_sec/failed_step/
  webhook_url` — every kwarg the ps1 passes matches by name and type. Rendering
  (`alerts.py:868-870`) is correct *given correct inputs*: `max d0 → \`{x or '?'}\``,
  `rows → **{rows:,}** if rows else ?`, `runtime → {d:.0f}s if d else ?`.

---

## Out of scope (noted, not done — scope discipline)

- **No failure-path Discord alert.** The ps1 `exit`s on a critical step failure
  *before* the Discord block, so `alert_data_ingest_result(success=False, ...)` is
  never called — the red "FAILED" embed (and its `failed_step` field) is dead code
  from the launcher's side. Adding failure notification is a feature, not part of
  Bug 6; left for a follow-up.
- **`rows == 0` still renders `?`** in alerts.py (`if rows else "?"`). Only reachable
  with a genuinely empty parquet, which STEP 3's 95%-regression guard reverts (exit
  1) before this block runs — so unreachable in practice. Left untouched to keep
  this change confined to the launcher.

## Appendix — files
- `scripts/daily_data_ingest.ps1` (3 edits: start-ts to top, date slice, robust
  extraction + sentinel).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
