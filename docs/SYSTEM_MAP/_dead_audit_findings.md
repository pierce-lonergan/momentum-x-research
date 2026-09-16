# Dead-code audit findings -- 2026-05-24

First full run of `_dead_audit.py` against src/ + main.py + scripts/ +
config/. **23 findings**: 13 broken intra-repo imports, 0 deprecated
D-code uses, 10 suspect comment/docstring references.

Headline: `selection_arena/` package is half-deleted and crashes at
import. ADR-025 (Phase 3 backtest) CLI commands in main.py reference
modules removed from disk. Several docstrings promise APIs (`fit_eta_
gamma_from_corpus`, `AlpacaExecutor.submit_entry`, `skip_entry`,
`flat_all_and_halt`, `ReplayInput.from_phase0_partition`,
`git_replay_compare`) that were never implemented or were renamed
without docstring updates.

None of these touch the production T2 path. All are "ghost machinery"
exactly per Pierce's TrailingStopManager warning.

Triage status: documented. Fixes deferred until after T2 stabilization
per code-freeze window.

---

## Tier-A: broken intra-repo imports (crash at import time)

These would raise `ModuleNotFoundError` the moment anyone imports the
affected file. They don't break production today because production
doesn't touch them, but they are dead-on-arrival for any new contributor
or backtest run.

### selection_arena: half-deleted package

`src/selection_arena/__init__.py` imports three modules that don't
exist on disk:

| Line | Module                                  | Names imported              |
|------|-----------------------------------------|-----------------------------|
| 35   | `src.selection_arena.filter_replay`     | `FilterReplay`              |
| 37   | `src.selection_arena.market_movers`     | `MarketMoversDB`            |
| 38   | `src.selection_arena.backtest`          | `BacktestRunner`            |

`scripts/run_selection_arena.py` references three more:

| Line | Module                                  | Names imported                          |
|------|-----------------------------------------|-----------------------------------------|
| 51   | `src.selection_arena.backtest`          | `BacktestConfig`, `BacktestRunner`      |
| 52   | `src.selection_arena.market_movers`     | `MarketMoversDB`, `build_records_from_dicts` |
| 60   | `src.selection_arena.report`            | `generate_day_detail`, `generate_text_report`, `save_report` |

**Files present on disk in `src/selection_arena/`**:
`__init__.py`, `models.py`, `profiles.py`, `short_analyzer.py`.

**Decision required**: either restore the missing modules from git
history (was this work-in-progress that got lost?) or delete the
package + the script. Filed as backlog item `Selection_arena_cleanup`.

### ADR-025 backtest commands in main.py

| Line | Module                              | Used by              |
|------|-------------------------------------|----------------------|
| 7619 | `src.data.scenario_builder`         | `cmd_build_scenarios` |
| 7671 | `src.data.scenario_builder`         | `cmd_record_scenarios` |
| 7672 | `src.data.scenario_recorder`        | `cmd_record_scenarios` |
| 7753 | `src.data.scenario_recorder`        | `cmd_record_scenarios` |

These are CLI subcommands referenced by argparse (likely `--build-
scenarios` and `--record-scenarios`). They crash on invocation today.
ADR-025 (Phase 3 Backtesting Validation) appears to have been
abandoned. Decision required: re-build the missing modules or delete
the CLI commands.

### scripts/backtest.py: graceful but dead

| Line | Module                       | Used by                |
|------|------------------------------|------------------------|
| 545  | `src.scanners.universe`      | universe build (guarded) |
| 940  | `src.scanners.universe`      | universe build (guarded) |
| 1111 | `src.scanners.universe`      | universe build (guarded) |

All three are wrapped in `try / except Exception: universe = []` so
they don't crash -- they silently fall back to a static list and emit
"WARNING: No scan universe found. Using static small-cap list." This
is the worst kind of dead code: it pretends to work and emits a
warning that nobody reads. Either implement `src/scanners/universe.py`
with `get_scan_universe()` or remove the try/import/fallback dance.

---

## Tier-B: docstring lies (API contract drift)

Comments and docstrings that reference symbols that don't exist
anywhere in src/, config/, main.py, or scripts/. Future-bug
magnets per Pierce's TrailingStopManager pattern.

### Real findings (action required)

**`src/analysis/slippage_calibration.py:25`**
```
Wire-in: EOD job runs `fit_eta_gamma_from_corpus()` against the Phase 0
```
Actual function is `fit_eta_gamma()` (defined at L95 in the same file).
The wrapper `fit_eta_gamma_from_corpus()` was never built. The EOD
runner at `src/analysis/bayesian_eod_runner.py` calls `fit_eta_gamma`
directly. **Fix**: update docstring to say "EOD job runs `fit_eta_gamma()`
via `bayesian_eod_runner`" -- or build the wrapper if the corpus-
abstraction was intentional. (Cheap fix, no production risk.)

**`src/analysis/stop_decision_log.py:50`**
```
Used by ``AlpacaExecutor.submit_entry`` to record the full set of
```
No `submit_entry` method on `AlpacaExecutor`. Worth a git blame to see
what it was renamed to or whether the call path was deleted. The
docstring is a load-bearing comment for anyone trying to understand
how stop decisions get logged.

**`src/monitoring/recon_daemon.py:34, 36`**
```
    skip_entry()
    await flat_all_and_halt()
```
These appear in a docstring usage block but neither function exists
anywhere in the codebase. The recon daemon docstring promises
`skip_entry()` (presumably to flag a new entry to skip) and
`flat_all_and_halt()` (presumably the panic button). **Either build
these or fix the docstring** -- right now the docstring is a contract
the code doesn't honor.

**`src/testing/replay_engine.py:10, 19`**
```
1. `ReplayInput.from_phase0_partition(base_dir, session_date)`
3. `git_replay_compare(base_sha, head_sha, replay_input, allowlist)`
```
The replay_engine docstring lays out a 3-step API. `ReplayInput`
exists (defined in `src/testing/differential_harness.py:110`) but
neither `from_phase0_partition` (classmethod) nor `git_replay_compare`
(top-level) is implemented. The file at line 187 explicitly says
"`git_replay_compare` is the v2 enhancement; today's foundation
[doesn't have it]" -- so this is documented future work, but the
docstring presents it as a working contract. **Fix**: prefix the
step-3 line with "(planned v2)" or move the API description into a
TODO block.

### False positives (whitelist or context)

**`src/analysis/trade_journal.py:952`**
```
`client.get_account_activities()` which AlpacaDataClient does NOT
```
The comment is semantically correct -- it's saying that
`AlpacaDataClient` does NOT have this method (and warning a future
reader). The audit can't read intent. Acceptable noise.

**`src/execution/entry_delay.py:183`**
```
for ticker, state in manager.get_state_updates(prices_dict, now):
```
Bare `manager.get_state_updates(...)` reference -- `manager` is a
variable of unknown type. Verified no `get_state_updates` defined
anywhere in repo. May be a stale docstring example (similar pattern
to recon_daemon), but lower confidence -- could also be a planned
method on a type the audit can't infer. Flag for human review.

**`src/selection_arena/short_analyzer.py:22`**
```
db = MarketMoversDB()
```
This is the same class referenced by the broken import above. Caught
twice -- once as broken import (Tier-A) and once as docstring example.
Subsumed by the selection_arena cleanup decision.

---

## Tier-C: deprecated D-code references

**0 findings.** No production code currently references any D-code
whose registry status is DEPRECATED, REVERTED, or DELETED. This is a
clean result and worth keeping that way: any future revert should
also delete or rename the affected code paths.

(Note: this check has a fairly small registry today -- only D26 +
D164 + D293a are flagged. As d_codes.md gets fleshed out, this check
becomes more valuable.)

---

## Recommended actions

| When                       | Action                                                       |
|----------------------------|--------------------------------------------------------------|
| After T2 ratchet (Thu 5/28) | Decide: restore or delete `src/selection_arena/` half-deletion |
| After T2 ratchet (Thu 5/28) | Decide: restore or delete `cmd_build_scenarios` / `cmd_record_scenarios` |
| Anytime (no production risk) | Fix 4 real docstring lies above (slippage, stop_decision_log, recon_daemon, replay_engine) |
| Monday morning             | Wire `_dead_audit.py --strict` into CI as a warning-only check |
| When stable                | Promote to blocking CI check                                 |

## Tool maintenance

- The audit runs in ~3 seconds against the full 425-file scan -- safe
  to run on every commit.
- Symbol pool size: 2092 functions, 462 classes, 2499 attributes.
- New false positives can be suppressed via PYTHON_BUILTINS /
  PROSE_WHITELIST / FILE_EXTENSIONS / DOCSTRING_EXAMPLE_NAMES in
  `_dead_audit.py`. Each addition should be commented with why.
- For machine-readable output: `python docs/SYSTEM_MAP/_dead_audit.py --json`
- For strict mode (exit 1 on any finding): add `--strict`.
