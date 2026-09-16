# Backfill aftermath — two small items deferred

**Date filed:** 2026-04-18 (during D221 Phase D loader fix)
**Status:** Known, not fixed. Low priority.
**Audience:** Future-Pierce, the next time someone is already inside either
surface area mentioned below. Fix incidentally; do not schedule dedicated
time.

---

## 1. `day_close` serializes as int in base file, float in shards

**Observation.** A single value-type divergence between
`data/backfill/features_labeled.jsonl` and
`data/backfill/labels_shards/labels_*.jsonl`:

```
BASE  row key 'day_close': <class 'int'>   (e.g. 5)
SHARD row key 'day_close': <class 'float'> (e.g. 5.0)
```

All 29 other top-level keys have matching types. Only `day_close` diverges.

**Impact today.** Zero. Both round-trip to numbers under `json.loads`. The
loader doesn't compare row dicts by identity, and `_build_premarket_features`
reads `day_close` through a non-type-checking path. All downstream math
treats them as numeric.

**Why it matters later.** If anyone ever does strict equality comparison on
row dicts across the two sources (e.g. to verify the two writers produce
identical rows for the same ticker/date) this will fire a false positive.
Similarly, if a feature engineer ever adds `day_close` to a set-based
grouping or deduplication by raw value, `5 != 5.0` under `is` but `==` under
`==`, and that inconsistency could silently change a group count.

**Suspected cause.** `compute_features_and_outcomes()` in
`scripts/backfill_features.py` likely returns `day_close` as a numpy scalar
in one code path and a Python float in the other, and `json.dumps` renders
them differently. Plausible split: the old legacy path may preserve the raw
candidate's `volume` as an int, while the daemon's recompute path runs
everything through `float()`.

**Fix, when someone is already in `compute_features_and_outcomes`.** Coerce
`day_close` to `float` explicitly at the end of the function:

```python
row["day_close"] = float(row["day_close"])
```

Cheap. One line. Don't refactor the whole function for this.

---

## 2. `test_missing_bars_skip` has a stale dataset assumption

**Observation.** `tests/unit/test_arena_scenarios.py::test_missing_bars_skip`
asserts:

```python
assert len(without_constraint) > len(with_bars), (
    "expected some candidates without bar recordings in this window"
)
```

Post-D221 backfill drain (2026-04-18), this is false for the specific window
the test uses (`2026-01-15..2026-01-20`, 110 candidates). The daemon
back-filled every one of them, so `with_bars == without_constraint == 110`
and the strict `>` fails.

**Impact today.** The test fails locally. CI will go red the next time the
full suite runs. This happened by the daemon doing its job — the test wasn't
wrong when it was written, just now overtaken by events.

**What the test is actually trying to verify.** That `require_bars=False`
returns ≥ the scenarios `require_bars=True` returns, and that the mechanism
drops rows without bars. The first half is already covered by the earlier
assertion (`len(without_constraint) >= len(with_bars)`). The second half
needs a window that still has missing bars post-drain, or a synthetic setup.

**Fix.** Pick either:
- **A (minimal).** Widen the window so it's guaranteed to include
  outside-top-500 days (e.g. March to April, when single-day candidate
  counts exceeded the top-500 bar-recording cap).
- **B (robust).** Replace the disk-based assertion with a `tmp_path`
  fixture that writes 3 candidates and 2 bar files, then asserts
  `with_bars=2, without=3`. Decouples the test from backfill coverage.

Prefer B long-term. A is ~5 minutes and defers the rewrite.

**Scope constraint.** Do NOT fix this as part of the current Phase D loader
patch. That patch is one-purpose (union base + shards). This test failure
is pre-existing-by-events and gets its own tiny PR.

---

## 3. `src/composite/train.py` overwrites the v0 doc-output for any version

**Observation.** `python -m src.composite.train --output-version v99
--output-dir models/diagnostics ...` writes its training-report markdown
to `docs/research-log/06_composite_v0_training.md` — clobbering v0's report
with v99's content (mismatched title, wrong .pkl paths, wrong row count).
Caught during the D221 diagnostic run on 2026-04-18; restored from git.

**Cause.** `--doc-output` defaults to a hardcoded path with `v0` baked in,
regardless of the `--output-version` argument.

**Impact today.** Zero — git restored the original. But anyone running
the trainer with a non-default `--output-version` will silently destroy
the corresponding existing report unless they also pass `--doc-output`.

**Fix.** In `src/composite/train.py`, derive the doc-output default from
the resolved version, e.g.:

```python
default_doc = f"docs/research-log/06_composite_{version}_training.md"
doc_output = args.doc_output or default_doc
```

Cheap. ~5 lines. Add a unit test that calls the trainer with
`--output-version v99` and asserts the v0 report is untouched.

**Workaround until then.** Always pass `--doc-output` explicitly when
training a non-v0 version. The diagnostic wrote `v0_training.md` under
`v99` — a check-then-restore-from-git is the recovery.
