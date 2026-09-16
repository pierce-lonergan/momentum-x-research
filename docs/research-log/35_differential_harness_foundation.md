# 35 — Differential Testing Harness Foundation

**Status:** module + 10 tests + pre-push wrapper + pre-commit gate shipped 2026-04-25 morning. Per-PR replay comparison (the full pre-patch vs post-patch SHA workflow) deferred until Phase 0 captures a recorded session worth using as canonical input.
**Predecessors:** `25_bug_hunting_playbook.md` §4 (Csmith-pattern differential testing for trading), `34_phase0_instrumentation_mvp_shipped.md` (the dependency that gated this), user's items 27-28 (2026-04-25 next-actions list).
**Tag:** `v2.2-difftest-foundation` (this commit).

---

## §0 — TL;DR

Three deliverables shipped:

1. **`src/testing/differential_harness.py`** (~270 LOC) — `DifferentialHarness` class plus the `OperationCall`, `OperationRecord`, `Divergence`, `DivergenceReport`, and `DifferentialHarnessError` data types. Pure-Python, in-process, unit-testable.

2. **`tests/unit/test_differential_harness.py`** (10 tests, 0.28s) — equivalence detection (3), divergence detection including the canonical Bug-N regression pattern (3), allowlist semantics (2), edge cases including FP-tolerant comparison (2).

3. **`scripts/check_differential_diff.py`** + `.pre-commit-config.yaml` `differential-harness-gate` hook — runs on every commit that touches a gated file (`bridge.py`, `alpaca_executor.py`, `trade_journal.py`, `main.py`, or any harness file itself) and blocks the commit if the harness suite is red.

The full per-PR replay comparison (checkout pre-patch SHA → replay → checkout post-patch SHA → replay → diff) is captured as the next step in §3 and explicitly gated on Phase 0 capturing one recorded session.

---

## §1 — What the harness catches

The canonical use case is the **Bug-N regression pattern**: a patch silently changes a method call that the patch description didn't mention. Bug N specifically was the `get_account_activities` → `get_orders` switch where the destination method didn't exist on the production `AlpacaDataClient`. The patch passed unit tests (which mocked out the data client) but crashed in production the first time the path ran.

Differential testing catches this pattern by replaying the same input sequence through BOTH versions and asserting equivalence. If post-patch raises where pre-patch returned, that's a divergence the harness reports — `assert_equivalent` raises `DifferentialHarnessError` with the structured diff.

The third test (`test_one_raises_other_returns_is_divergence`) pins this exact pattern as a unit test.

### Allowlist semantics

Real patches DO change behaviour intentionally — the allowlist names the operations where divergence is expected. Two tests pin the discipline:

- `test_allowlisted_op_divergence_does_not_fail` — divergence is recorded but `report.passed` returns True.
- `test_mixed_allowlist_only_blocks_unlisted` — a sequence with both allowlisted AND non-allowlisted divergences fails with only the unlisted one in `out_of_allowlist`.

This matches the Csmith discipline: divergence is fine iff documented; undocumented divergence is the bug signal.

### Floating-point tolerance

`approx_floats(rtol=1e-9, atol=1e-12)` returns an equality function for the inevitable case where pandas/numpy operation order produces tiny FP drift between two replay runs. Default equality is `repr(a) == repr(b)` — strict enough to catch all but FP noise, loose enough to handle most non-numeric types out of the box.

---

## §2 — Architecture

```
ReplayInput  → ordered list of OperationCall(name, args, kwargs)
variant_a    → callable(op_name, *args, **kwargs) -> result | raise
variant_b    → same signature
allowlist    → set[str] of op_names where divergence is tolerated

DifferentialHarness(variant_a, variant_b, allowlist, equality_fn)
  .replay(input) → (records_a, records_b)
  .compare(records_a, records_b) → DivergenceReport
  .assert_equivalent(input)
    → DivergenceReport (passed=True) OR raises DifferentialHarnessError
```

The harness is **pure-Python and in-process**. Each variant is a callable. The git-aware "replay across two SHAs" workflow is the responsibility of the wrapper script (`scripts/check_differential_diff.py`) — not the harness itself. This separation keeps the harness unit-testable + the wrapper composable with whatever git workflow the team picks.

### Equivalence semantics

Two operation results are equivalent iff:

- Both raised: same exception class name AND same `str(exc)` representation.
- Neither raised: `equality_fn(result_a, result_b)` returns True.
- Otherwise: divergence (with `reason` describing which case).

Different exception messages are a divergence — same class but different `str(exc)` flags as `"different exception (a=... b=...)"`. This catches the case where a refactor reroutes errors through a wrapper that mangles the message.

---

## §3 — What's NOT in this ship

**Per-PR replay across two git SHAs.** The full workflow:

1. `git stash` working changes.
2. `git checkout origin/develop` (or whatever the base ref is).
3. Run replay against a recorded Phase 0 session.
4. Save records_a.
5. `git checkout PR_BRANCH`.
6. Run replay against the same input.
7. Save records_b.
8. Compare via `DifferentialHarness.compare(records_a, records_b)`.
9. Block merge if `report.passed is False`.

**Why deferred:**

- Step 3-6 require a "recorded Phase 0 session" — a Parquet snapshot of (TradeContextRow, BarContextRow, ChildFillRow, CohortRow) sufficient to drive a replay. Phase 0 just shipped (`f1fd13f` + `0f0bd0f`), so the FIRST recorded session lands tomorrow morning at the earliest.
- Step 6 requires a "replay engine" — a function that takes the recorded session input + the current code and produces a deterministic output sequence. That's a Phase 0+1 deliverable (the equivalent of session_report's playback path for execution).
- Until both prerequisites exist, the foundation gate (this ship) is the right depth: the harness suite must pass for any patch to a gated file. That alone catches harness regressions and makes the per-PR comparison drop-in trivial when Phase 0 produces its first recording.

**What this ship enables when those prereqs land:**

```python
# Future scripts/check_differential_diff.py — full workflow
from src.testing import DifferentialHarness, ReplayInput
from src.testing.replay_engine import build_variant_from_sha, load_recorded_session

input_seq = load_recorded_session("data/instrumentation/.../session_2026-04-26.parquet")
variant_a = build_variant_from_sha("origin/develop")
variant_b = build_variant_from_sha("HEAD")
harness = DifferentialHarness(variant_a, variant_b, allowlist=PATCH_INTENDED_SURFACES)
report = harness.assert_equivalent(input_seq)
```

The harness API is fixed; only the variant builders + the replay engine are missing.

---

## §4 — Pre-commit integration

Hook in `.pre-commit-config.yaml`:

```yaml
- repo: local
  hooks:
    - id: differential-harness-gate
      name: Track D differential harness gate (gated file edit)
      entry: python scripts/check_differential_diff.py
      language: system
      files: ^(src/execution/(bridge|alpaca_executor)\.py|src/analysis/trade_journal\.py|main\.py|src/testing/.*\.py|tests/unit/test_differential_harness\.py)$
      pass_filenames: false
```

What it does:

- Triggers on commits modifying any gated file (the production execution stack OR the harness itself).
- Calls `scripts/check_differential_diff.py origin/develop` (default base ref).
- Wrapper inspects `git diff --name-only base_ref...HEAD` for hits.
- If hit: runs `pytest tests/unit/test_differential_harness.py -q --no-header`.
- Exit 0 = pass; exit 1 = harness suite failed = commit blocked.

**Today's discipline:** the gate proves the harness is operational before any patch can touch the critical-path code. **Tomorrow's discipline:** the gate runs the full per-PR replay comparison.

---

## §5 — Test coverage map (10/10)

| # | Category | Test | What it pins |
|---|----------|------|--------------|
| 1 | Equivalence | `test_identical_variants_produce_zero_divergence` | Same callable → 0 divergence |
| 2 | Equivalence | `test_semantically_equivalent_variants_pass` | Different mechanism, same output → 0 divergence |
| 3 | Equivalence | `test_zero_operations_passes_trivially` | Empty input → 0 divergence (no false positive) |
| 4 | Divergence | `test_different_returns_flagged_with_op_index` | Different output → 1 divergence with `op_index` |
| 5 | Divergence | `test_one_raises_other_returns_is_divergence` | **Bug-N regression pattern** pinned |
| 6 | Divergence | `test_different_exception_classes_is_divergence` | Both raise but different types → divergence |
| 7 | Allowlist | `test_allowlisted_op_divergence_does_not_fail` | Documented divergence → `report.passed=True` |
| 8 | Allowlist | `test_mixed_allowlist_only_blocks_unlisted` | Mixed → only unlisted in `out_of_allowlist` |
| 9 | Edge | `test_approx_floats_tolerates_tiny_drift` | FP-tolerant equality function works |
| 10 | Edge | `test_compare_raises_on_length_mismatch` | Misuse → ValueError, not silent pass |

Suite runs in 0.28s. No external dependencies beyond the harness itself.

---

## §6 — Discovery rate impact

This ship surfaces 0 bugs (it's a foundation). It **enables** future bug-discovery: any regression a patch ships in a gated file now has a structured path to detection (replay + compare + report). Once Phase 0 captures one session, the per-PR full-replay variant ships and Track D goes from "foundation" to "active discovery surface."

**Cumulative discovery infrastructure shipped this session (7 commits, 5 tags):**

| Tag | Capability |
|-----|------------|
| `v2.2-static-analysis-active` | AST + regex bug-class detection on every commit |
| `v2.2-pbt-phase2-active` | Hypothesis state-machine search, R1-R11 + I1-I9, env-driven 200/2000/10000 |
| (untagged 09f3ce4) | Bug AA: oracle bug surfaced + patched via PBT |
| (untagged 5e6a9b9) | QMP signing migration + D260 wire-in |
| `v2.2-phase-0-active` | Phase 0 schemas + writer (foundation) |
| (untagged 0f0bd0f) | Phase 0 wire-in: 6 emit-sites + EOD flush + D261 |
| `v2.2-difftest-foundation` | Differential harness + pre-commit gate |

Discovery rate: 11 bugs surfaced / 0 patches introduced regressions = ∞.

---

## §7 — Knock-on items (next iteration plan)

When Phase 0 has captured 1 full recorded session:

1. **`src/testing/replay_engine.py`** — load Phase 0 Parquet → `ReplayInput`. Build `variant_a` / `variant_b` callables that exercise the production code path against the input.
2. **`scripts/check_differential_diff.py`** v2 — wire the per-PR git-checkout-and-compare workflow.
3. **`docs/research-log/36_differential_replay_first_run.md`** — documenting the first per-PR comparison + the inevitable allowlist-tuning iteration.

These are 3-4 hours of work each. Total: ~1 day to go from "foundation shipped" to "active per-PR replay comparison live."
