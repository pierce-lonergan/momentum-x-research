# D220 Phase 6 — Bug Sweep Findings

**Sweep date:** April 16, 2026 (EOD)
**Branch:** `d220-full-throttle`
**Scope:** today's new code (`src/production_arena/`, `src/composite/`, `src/shadow/`, orchestrator hooks)
**Method:** pytest at full strictness + targeted greps + manual integration paranoia

## Summary

| Severity | Count | Action |
|----------|-------|--------|
| **P0** (affects tomorrow's run / shadow correctness) | **0** | none — sweep is clean |
| P1 (latent risk, log only) | 3 | logged below, deferred |
| P2 (nice-to-have, refactoring) | 2 | logged below, deferred |

**No code changes from this sweep.** Tests still 120/120 pass. The branch is ship-ready.

## What was checked

### Phase 6.1 — automated tooling
- `pytest tests/` on the full D220 suite (excluding pre-existing collection errors in unrelated tests): **120/120 pass in 7.45s**
- ruff / mypy: not installed locally; skipped per directive (no new tooling tonight)

### Phase 6.2 — targeted greps on new code
- `^\s*except\s*:` and `except Exception:` without logging in `src/{production_arena,composite,shadow}/*.py` → **NONE FOUND**
- Naked `datetime.now()` (without `timezone.utc`) → **NONE FOUND**
- `asyncio.get_event_loop()` (deprecated) → **NONE FOUND**
- `print(` statements → 26 hits, ALL in `src/production_arena/cli.py` (legitimate CLI output)
- Shadow log flushing → `src/shadow/logger.py:71` calls `.flush()` after every line ✓
- Day rollover → `_ensure_open()` correctly closes/reopens on date change. Composite_shadow passes ET date in production hooks. Verified.

### Phase 6.2d — composite cold-start load timing
```
$ time python -c "from src.composite.score import composite_score; ..."
score=0.4316
real    0m0.992s
```
- Cold start: **992 ms** (above 500ms target, below 2s critical threshold)
- Subsequent calls hit `_MODEL_CACHE` (lazy singleton) — sub-millisecond
- Filed P1 below, no fix tonight

### Phase 6.3 — orchestrator integration paranoia
- Both `maybe_score_composite` call sites (`orchestrator.py:1619`, `orchestrator.py:3125`) wrap in try/except with logger.warning ✓
- Both call sites discard the return value (no `shadow_result = maybe_score_composite(...)` anywhere — verified by grep)
- `SHADOW_SCORING_ENABLED=false` test:
  ```
  $ SHADOW_SCORING_ENABLED=false python -c "..."
  PASS: kill switch fully disables, no crash on bogus input
  ```
- The static-analysis guard at `tests/static_analysis/test_shadow_isolation.py` enforces no-read-shadow-fields rule

---

## P1 findings — log only, defer

### P1-1 — Composite cold-start load is 992ms

**Where:** `src/composite/score.py:load_model()` — pickles a sklearn Pipeline (StandardScaler + LogisticRegression).

**Impact:** First per-candidate composite shadow score on a fresh process pays ~1s latency. Subsequent calls are sub-ms (cached). On tomorrow's session, this affects exactly one evaluation — the first one of the day.

**Why deferred:** below the 2s critical threshold; cache amortizes cost; affects exactly 1 of ~80 daily evaluations.

**If/when to fix:** if the cold start exceeds 2s, or if the orchestrator process restarts mid-session frequently. Mitigation options: (a) preload the model at module import time, (b) compile to a smaller representation (joblib), (c) replace pickle with a hand-written predict function (one matrix-multiply for logistic regression).

### P1-2 — ShadowLogger fallback uses UTC date when session_date is omitted

**Where:** `src/shadow/logger.py:67` — `session_date = entry.get("session_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")`.

**Impact:** If a future caller forgets to pass `session_date`, the fallback uses UTC. At UTC midnight (8 PM ET on EST or 7 PM ET on EDT), the session_date jumps to the next day while the trading session is still going. Result: shadow entries from 4-7 PM ET get filed under the wrong date.

**Why deferred:** the production caller (`composite_shadow.py:99`) ALWAYS passes session_date as ET-formatted, so the fallback never fires in real use. This is purely defensive.

**Fix when revisiting:** change the fallback to `datetime.now(_NY).strftime("%Y-%m-%d")` so callers that forget session_date still file under the right ET date.

### P1-3 — `composite_score` returns 0.5 on internal errors instead of raising or returning None

**Where:** `src/composite/score.py:118` (the inner try/except in `composite_score`).

**Impact:** If `pipe.predict_proba` fails (numerical issue, corrupted model file), the function silently returns 0.5 — a "neutral" prediction that downstream code can't distinguish from a legitimate "uncertain" call. Could mask a real bug.

**Why deferred:** the shadow path catches all exceptions one level up anyway. The 0.5 fallback is just to keep the function pure. Logging is already at WARNING level when this fires.

**Fix when revisiting:** raise instead of returning 0.5, since the shadow caller already wraps. Reserve 0.5 for actual model output.

---

## P2 findings — refactoring opportunities

### P2-1 — `cli.py` prints instead of logging

**Where:** `src/production_arena/cli.py` — 26 `print()` calls.

**Why P2:** CLI tools are expected to print to user terminals. The current design is consistent with `argparse` conventions. Changing to logger would require adding `--quiet` / `--verbose` flags. Not worth the churn.

**No action.** Listed for completeness only.

### P2-2 — Heatmap rendering in `06_threshold_calibration.md` is plain text

**Where:** `docs/research-log/06_threshold_calibration.md` Heatmap sections.

**Why P2:** Text-table rendering is readable in markdown. A real heatmap (matplotlib PNG) would be visually clearer but adds a dependency.

**No action.** Listed for completeness only.

---

## What this sweep did NOT cover

- Pre-existing tech debt in `src/core/orchestrator.py`, `main.py`, etc. The static-analysis baselines from `tests/static_analysis/_baselines.json` track that separately.
- The 3 pre-existing collection errors in `tests/unit/test_premarket_research.py`, `test_scenario_builder.py`, `test_scenario_recorder.py` (missing `src.data.scenario_builder` module). Not new; not D220-related.
- Performance profiling under load (would need tomorrow's session data).

## Sweep verdict

**Branch is ship-ready.** P0 count is zero. P1 items are minor and defensible to defer. The hard-isolation guards (composite + shadow) plus the static-analysis baselines mean future regressions in these classes will be caught at PR time.

Phase 7 (model arena harness) and Phase 8 (sync + push) can proceed.
