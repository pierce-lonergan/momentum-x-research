# Static analysis test suite

AST-driven static analysis as pytest tests. Designed to **catch new only** —
existing tech debt is frozen as a per-file count baseline; tests fail when a
file's count exceeds its baseline, succeed otherwise.

| Kind                | Detector                             | Bug origin |
|---------------------|--------------------------------------|------------|
| `frozen-mutate`     | `obj.attr = X` on `frozen=True` Pydantic / dataclass | D219 — `_ec.float_shares = enrich[...]` killed every candidate evaluation |
| `async-leak`        | `httpx.AsyncClient(...)` outside `async with` and never `aclose()`'d | Pre-D219 hotfix; leaked connection pools per scan |
| `silent-handler`    | `except <X>:` whose body is exactly `pass` / `continue` / `...` / silent `logger.debug` | D218 sweep — silent failures across the codebase |
| `relative-path`     | `Path("data/...")` / `os.path.join("data", ...)` from cwd-relative literals | D217 — heartbeat scope leak when started from stale worktree dir |
| `ps-datetime-utc`   | `[datetime]::Parse(...)` without `AssumeUniversal` within ±3 lines | D218 — watchdog computed 14,400s stale heartbeat from 4-hour TZ offset |
| `ps-pipe-deadlock`  | `2>&1 \| ForEach-Object/Out-File/Tee-Object/Add-Content` | D218 — watchdog froze trading process for 10+ hours via stderr-buffer fill |

## Files

```
tests/static_analysis/
├── README.md                       ← you are here
├── _ast_helpers.py                 ← shared visitor base, baseline I/O, FIX_HINTS
├── _baselines.json                 ← per-file violation counts; committed; sorted; deterministic
├── _update_baseline.py             ← CLI to regenerate (--check mode for drift detection)
├── test_frozen_mutations.py        ← `frozen-mutate`
├── test_async_lifecycle.py         ← `async-leak`
├── test_silent_handlers.py         ← `silent-handler`
├── test_relative_paths.py          ← `relative-path`
├── test_powershell_safety.py       ← `ps-datetime-utc` + `ps-pipe-deadlock`
└── test_whitelist_conventions.py   ← pins the noqa-line-position convention
```

## How a check works (per file, per kind)

1. Walk repo → list of `.py` (or `.ps1`) production files. `tests/`, `.venv`, `data/`, `logs/`, `mx-arena/`, `models/` are excluded.
2. Parse each file → AST → run `NodeVisitor` subclass → emit `Violation(path, line, col, kind, snippet)`.
3. Strip violations on lines containing a matching `# noqa: <kind>` marker (see whitelist convention below).
4. Group by file, compare to `_baselines.json[kind]`.
5. **Fail iff** `current[file] > baseline[file]` for any file.

## The noqa-line-position convention (READ BEFORE WHITELISTING)

> **The `# noqa: <kind>` marker MUST sit on the line that the AST visitor reports.**

This is load-bearing. The `Violation` carries `node.lineno` from the AST node, and the whitelist check is line-exact. Putting the marker on a sibling line silently does nothing — the test still fails.

| Kind                | Reported line                           | Place the noqa here |
|---------------------|------------------------------------------|---------------------|
| `frozen-mutate`     | The `Assign` / `AugAssign` (the `=` line) | Same line as the assignment |
| `async-leak`        | The `Call` to `AsyncClient` / `ClientSession` | Same line as the constructor call |
| `silent-handler`    | The **`except`** keyword (NOT the body)   | **`except Exception:  # noqa: silent-handler`** |
| `relative-path`     | The `Call` to `Path` / `os.path.join`     | Same line as the call |
| `ps-datetime-utc`   | The line containing `[datetime]::Parse(`  | Same line |
| `ps-pipe-deadlock`  | The line containing `2>&1 \| ...`         | Same line |

The `silent-handler` rule is the one that bites people, because the body (`pass`, `continue`) feels like the place to whitelist. It isn't.

```python
# WRONG — does NOT suppress (test still fails)
except asyncio.TimeoutError:
    pass  # noqa: silent-handler

# RIGHT — suppresses
except asyncio.TimeoutError:  # noqa: silent-handler — TimeoutError IS the expected tick signal
    pass
```

This convention is enforced by `test_whitelist_conventions.py::test_noqa_must_be_on_except_line` — if a future refactor changes `node.lineno` semantics, that test alerts loudly.

## When to whitelist vs. fix

Prefer **fix** over **whitelist**. The two patterns where whitelist is the correct call:

1. **`asyncio.TimeoutError` as control-flow signal** — `await asyncio.wait_for(stop_event.wait(), timeout=tick)` raises `TimeoutError` to mean "the tick interval elapsed" (which IS the success path). Logging every tick produces noise without information. Whitelist with explanatory comment.
2. **Hot-path callbacks where logging would itself fail** — extremely rare. Document the reason inline.

For everything else, the canonical fix patterns:

| Kind                | Canonical fix |
|---------------------|---------------|
| `frozen-mutate`     | `inst.model_copy(update={...})` (Pydantic) or `dataclasses.replace(inst, ...)` |
| `async-leak`        | `async with httpx.AsyncClient(...) as c:` or `try/finally: await c.aclose()` |
| `silent-handler`    | `except Exception as e: logger.warning("context: %s", e)` (any logger level OK as long as `e` is in args) |
| `relative-path`     | `_PROJECT_ROOT / "data" / "foo.json"` (define `_PROJECT_ROOT = Path(__file__).resolve().parents[N]`) |
| `ps-datetime-utc`   | `[DateTimeOffset]::Parse(...)` or `[datetime]::Parse(..., [Globalization.DateTimeStyles]::AssumeUniversal)` |
| `ps-pipe-deadlock`  | `2>> $LogFile` (direct file redirect) or `Start-Process -RedirectStandardOutput` |

## Adding a new frozen class

When you declare a new `class X(BaseModel, frozen=True)` or `@dataclass(frozen=True)`:

1. Add `"X"` to `KNOWN_FROZEN_TYPES` in `_ast_helpers.py`.
2. Run `python tests/static_analysis/_update_baseline.py` only if you intentionally added violations. Otherwise the existing baseline is unchanged.

The discovery step inside `test_frozen_mutations.py` also detects per-file frozen classes locally, so adding a frozen class without updating `KNOWN_FROZEN_TYPES` still catches in-file mutations — the project-wide allowlist exists to catch cross-file cases.

## Regenerating the baseline (use sparingly)

```bash
# Refresh _baselines.json from current source (commits the new state of debt)
python tests/static_analysis/_update_baseline.py

# Check whether regeneration would change anything (CI drift detection)
python tests/static_analysis/_update_baseline.py --check
```

Regenerating is a deliberate operator action. **Never** run it just to "make the test pass" — that defeats the entire purpose. Run it only when:
- A frozen class was intentionally removed (e.g. `frozen=True` lifted)
- Existing debt was paid down (counts went DOWN — the diff in the PR shows the cleanup)
- A file was renamed (the old entry is dead; the new one needs counting)

The `_meta` block at the bottom of `_baselines.json` records when and why the baseline was last touched.

## Behavioral safety net

`tests/unit/test_d219_float_enrichment.py` is the behavioral counterpart. Even if every static analysis test is disabled, the D219 regression is pinned by:

- `test_direct_attribute_assignment_is_blocked` — verifies `frozen=True` is still in place on `CandidateStock`.
- `test_model_copy_replaces_frozen_field` — verifies the canonical fix path produces equivalent semantics.
- `test_watchlist_in_place_replacement_pattern` — mirrors `main.py:1430-1466` to catch the exact loop.
- `test_model_copy_is_idempotent_for_cache_hits` — defends repeated enrichment.
- `test_empty_update_is_safe` — defends edge case of partial Finnhub responses.

This is defense in depth — if the AST visitor is bypassed (e.g. by an unusual code generator), the behavioral test still catches today's specific bug.
