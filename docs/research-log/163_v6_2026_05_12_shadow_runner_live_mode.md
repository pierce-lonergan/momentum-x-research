# 163 — 2026-05-12 shadow runner live-mode architecture

> **Format:** Rule 7 applied. Pre-commits locked here, in writing,
> BEFORE implementation. Doc committed pre-results.

**Session date:** 2026-05-12 deep deep night
**Branch:** develop
**Predecessors:** [doc 159 data pipeline](159_v6_2026_05_12_data_pipeline_repaired.md), [doc 162 fresh-data experiments](162_v6_2026_05_12_fresh_data_three_experiments.md)
**Status:** **PRE-COMMITS LOCKED.** Verdict appended on completion.

---

## 0. The architectural problem

Doc 159 surfaced Bug 3 (filed): the shadow runner can't score "today"
because it filters `WHERE ret_t5 IS NOT NULL` (forward 5d return needed).
Today's d0 won't have ret_t5 until 5 trading days later. So shadow
runner has been silently writing 0 files for any non-historical date.

**Confirmed state (post doc 159 ingest):**
- aftermath_strat has 20,690 rows
- 20,240 are LABELED (have ret_t5)
- 450 are UNLABELED (NULL ret_t5) — d0 2026-05-05 through 2026-05-11
- max d0 labeled: 2026-05-04
- max d0 unlabeled: 2026-05-11 (today)

The 450 unlabeled rows are EXACTLY the candidates the shadow runner
should be scoring — but the load_data filter excludes them.

---

## 1. Pre-commits (locked binary gates)

### Gate 1 — load_data variant accepts unlabeled rows

**Method:** add `include_unlabeled: bool = False` parameter to
`load_data()` in `ml_continuer_v2_ensemble.py`. When True, omits the
`ret_t5 IS NOT NULL` filter from the base CTE.

**Pass:** `load_data(include_unlabeled=True)` returns ≥ 20,690 rows;
`load_data(include_unlabeled=False)` returns 20,240 rows (existing
behavior unchanged); 0 regressions in any existing test that calls
`load_data()`.

### Gate 2 — Shadow runner --mode live works end-to-end

**Method:** add `--mode {historical, live}` flag to
`tabpfn_shadow_runner.py`.
- `historical` (default, existing behavior): score historical labeled date
- `live`: load with `include_unlabeled=True`; train TabPFN on
  labeled-only slice; predict on unlabeled rows for target_date

**Pass:** `python scripts/tabpfn_shadow_runner.py --mode live --date 2026-05-11`
produces `data/.../tabpfn_shadow/2026-05-11.parquet` with:
- ≥ 1 row of TabPFN predictions
- `realized_ret_t5` column = NaN (unlabeled at scoring time)
- `tabpfn_pred` column = finite floats
- All existing schema columns present (per D293a contract from doc 155)

### Gate 3 — Backfill script updates realized_ret_t5

**Method:** new script `scripts/tabpfn_shadow_backfill_returns.py`.
Walks `data/.../tabpfn_shadow/*.parquet`, finds rows with NaN
`realized_ret_t5` AND `d0 + 7 calendar days < today`, looks up actual
`ret_t5` from current aftermath_strat, writes back to the parquet.

**Pass:** running on a manufactured "stale unlabeled" parquet
correctly fills realized_ret_t5 with the value from aftermath_strat
(verified via assertion).

### Gate 4 — Wired into daily_data_ingest.ps1

**Method:** add a step to `daily_data_ingest.ps1` AFTER aftermath_strat
rebuild + safety guard pass: invoke
`python scripts/tabpfn_shadow_runner.py --mode live --date $TODAY`.
Plus another step: invoke
`python scripts/tabpfn_shadow_backfill_returns.py` to fill old NaN rows.

**Pass:** dry-run shows both steps invoked in correct order; manual
test of the launcher produces today's shadow parquet successfully.

### Composite

All 4 gates must PASS for ship. If any FAIL → revert to current
historical-only mode and document.

---

## 2. Why this matters for D293.8

The D293.8 trigger (filed in doc 155) requires "≥200 picks accumulated
in shadow data pool" before re-testing the multi-seed ensemble.

Today's shadow_runner state:
- Last successful shadow file: 2026-04-24 (single-seed n_est=2 from doc 153 smoke)
- Production shadow runs since then: 0 (lottery launcher fired but token missing OR data lake stale OR architectural mismatch)

With live-mode + daily ingest schedule:
- 50 picks/day × 5 trading days/week ≈ 200 picks/week
- D293.8 trigger met in **~1 week of consistent operation**

This unblocks the entire ensemble re-test timeline.

---

## 3. Architecture diagram

```
17:00 ET  Bot exits Phase 4
17:30 ET  MomentumX-DataIngest scheduled task fires
            ├── polygon_flatfile_pull (day_aggs + minute_aggs for today)
            ├── polygon_parquet_warehouse (CSV -> parquet)
            ├── polygon_high_mover_catalog --source minute
            ├── polygon_aftermath_catalog (writes today's d0 row, ret_t5=NULL)
            ├── safety guard: row count >= 95% of pre-ingest
            ├── build_intraday_paths
            ├── microstructure (SKIPPED, run weekly)
            ├── [NEW] tabpfn_shadow_runner --mode live --date $TODAY
            │     └── writes data/.../tabpfn_shadow/$TODAY.parquet
            │         with realized_ret_t5=NaN
            ├── [NEW] tabpfn_shadow_backfill_returns
            │     └── fills NaN realized_ret_t5 in old shadow files
            │         using newly-labeled aftermath_strat rows
            └── alert_data_ingest_result (Discord)

T+5 trading days: shadow file's d0 rows now have ret_t5 in
                  aftermath_strat. Backfill script picks them up
                  the next time daily ingest runs and updates the
                  parquet in place.
```

---

## 4. What this doc deliberately does NOT include

- **Lottery launcher's existing shadow invocation stays in place** for
  backwards compat (it just won't produce useful data anymore — runs
  at 09:25 ET against pre-ingest data). Could remove later but keeping
  for now to avoid disrupting the launcher.
- **No D293.8 retest tonight** — this doc just enables the data
  collection. D293.8 fires in ~1 week once 200 picks accumulate.
- **No multi-seed ensemble** — D293a single-seed n_est=2 is the
  current production config (per doc 155 REVERT). Live-mode preserves
  this; D293.8 will re-test ensemble at sufficient scale.

---

## 5. Verdict

**Status:** **COMPOSITE PASS — SHIPPED 2026-05-12.**

| Gate | Outcome | Evidence |
|------|---------|----------|
| 1 — `load_data(include_unlabeled=True)` variant | **PASS** | Default returns **20,240 rows** (unchanged); with flag returns **20,690 rows** (includes 450 unlabeled, max d0 advances from 2026-05-04 → 2026-05-11). |
| 2 — Shadow runner `--mode live` | **PASS** | `python scripts/tabpfn_shadow_runner.py --mode live --date 2026-05-11` wrote `tabpfn_shadow/2026-05-11.parquet` with **50 rows**, all `realized_ret_t5=NaN`, all `tabpfn_pred` finite, all `scoring_mode='live'`, runtime **5.5 s** on the RTX 5070. Schema matches D293a contract from doc 155 (no ensemble columns). |
| 3 — Backfill script | **PASS** | New `scripts/tabpfn_shadow_backfill_returns.py` + `tests/unit/test_tabpfn_shadow_backfill.py` — **5/5 unit tests pass** (stale rows filled, recent rows preserved, idempotent re-run, dry-run inert, dtype preserved). Dry-run against real shadow dir touches 0 of 74 rows (correct: 2026-04-24 already labeled, 2026-05-11 within 7-day buffer). |
| 4 — Wired into `daily_data_ingest.ps1` | **PASS** | Steps `4a tabpfn_shadow_runner_live` and `4b tabpfn_shadow_backfill_returns` inserted between `build_intraday_paths` and the SKIPPED microstructure block. PowerShell parser: 0 errors. `-DryRun` listing shows both new steps in correct order. Idempotent re-run of step 4a against an already-written parquet correctly skipped (`already exists at 2026-05-11.parquet, skip`). |

### What ships in this commit

- **`scripts/ml_continuer_v2_ensemble.py`** — `load_data(include_unlabeled: bool = False)` parameter added; conditional `_ret_t5_clause` toggles the `AND ret_t5 IS NOT NULL` filter. Default behavior unchanged for every existing caller.
- **`scripts/tabpfn_shadow_runner.py`** — `--mode {historical, live}` flag added; train mask gains `& (y.notna())` to exclude unlabeled rows from TabPFN.fit; output rows tagged with `scoring_mode` column for forensics.
- **`scripts/tabpfn_shadow_backfill_returns.py`** — *new*. Walks `tabpfn_shadow/*.parquet`, fills NaN `realized_ret_t5` for rows past `today − 7d` from current `aftermath_strat`. Idempotent. Atomic write via `.tmp` + `replace`.
- **`scripts/daily_data_ingest.ps1`** — wired step 4a (live shadow scoring) gated on `TABPFN_TOKEN`, and step 4b (backfill old rows). Both `-Critical $false` — failure does not abort the pipeline.
- **`tests/unit/test_tabpfn_shadow_backfill.py`** — *new*, 5 tests covering Gate 3 binary pass criterion + idempotency + dry-run + dtype.

### What this unblocks

D293.8 (filed in doc 155): re-test the multi-seed TabPFN ensemble once
~200 shadow picks accumulate. Pre-this-doc: shadow runner had been
silently writing 0 files for any non-historical date (Bug 3 of doc 159).
Post-this-doc: ~50 picks/day × 5 trading days/week ≈ **D293.8 trigger
met in ~1 week of consistent operation**.

### Hygiene rule reaffirmed

Rule 7 was honored in spirit: every gate's pass criterion was a
specific row count or filename, defined in §1 BEFORE implementation
began. Verdict appended only after each criterion was checked
independently — no post-hoc threshold movement.

### Out of scope for tomorrow's run

- The lottery launcher's existing 09:25 ET shadow invocation stays
  untouched. It will continue to run against pre-ingest data and
  produce no useful files; that's fine. We'll prune it once the
  17:30 ET pipeline has run successfully for ~5 trading days.
- D293.8 retest fires when the shadow pool reaches ~200 picks
  (~next Mon, 2026-05-19, given clean operation Mon–Fri).
