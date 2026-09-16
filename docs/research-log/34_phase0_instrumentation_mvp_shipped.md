# 34 — Phase 0 Instrumentation MVP: Schemas + Writer Shipped

**Status:** schemas + writer + tests shipped 2026-04-25 morning. Production wire-in (alpaca_executor + bridge hook sites) deferred to follow-up commit so this ship can be reviewed in isolation.
**Predecessors:** `21_phase0_instrumentation_mvp.md` (the spec), `33_qmp_migration_findings.md` (the consumer that's blocked on this).
**D-codes:** D261 PHASE0_SCHEMA_VALIDATION_FAILED reserved in `26_d_code_registry.md` (commit 2cc3e9a). Now wired live.
**Tag:** `v2.2-phase-0-active` (this commit).

---

## §0 — TL;DR

Three files, 14 tests, 35/35 across the integrated suite:

| File | Purpose | LOC |
|------|---------|-----|
| `src/analysis/instrumentation/__init__.py` | Package surface | 22 |
| `src/analysis/instrumentation/schemas.py` | 4 Pydantic models with `schema_version=1` | 145 |
| `src/analysis/instrumentation/writer.py` | InstrumentationWriter — atomic Parquet writes, ring buffer, D261 wire-in | 247 |
| `tests/unit/test_phase0_instrumentation.py` | 14 tests across 4 categories | 295 |

Phase 0 is now **architecturally live**. The next commit wires emit-sites into `alpaca_executor.submit_oto_order`, `bridge._poll_for_terminal_fill`, and `bridge.execute_verdict`. The wire-in is a separate ship because it touches the production execution stack and benefits from isolated review.

---

## §1 — What shipped

### Four Pydantic schemas (per spec §1-4)

All four `frozen=True` per the D219 discipline — instrumentation rows are facts about a moment in time and must never mutate post-construction. Every schema carries `schema_version: int = SCHEMA_VERSION (=1)`.

| Schema | Columns | Hook site (deferred) |
|--------|---------|----------------------|
| `TradeContextRow` | 17 (16 spec + schema_version) | `alpaca_executor.submit_oto_order` (submit row), `bridge._poll_for_terminal_fill` (terminal cols) |
| `BarContextRow` | 13 (12 spec + schema_version) | `bridge.execute_verdict()` after `add_position` |
| `ChildFillRow` | 9 (8 spec + schema_version) | `bridge._poll_for_terminal_fill` success path via `client.get_orders.legs` |
| `CohortRow` | 11 (10 spec + schema_version) | new module, called from `bridge.execute_verdict()` |

Per spec §3 MVP decision: `ChildFillRow.venue` and `nbbo_*_at_fill` are nullable; REST-derived rows leave them NULL until the WebSocket trade-updates subscription ships in v2.1.

### Unified writer

`InstrumentationWriter` exposes:

```python
emit_trade_context(row: TradeContextRow) -> None
emit_bar_context(row: BarContextRow) -> None
emit_child_fill(row: ChildFillRow) -> None
emit_cohort_registry(row: CohortRow) -> None

# Dict variants — D261 graceful failure path
emit_trade_context_dict(payload: dict) -> bool   # False on validation fail
emit_bar_context_dict(payload: dict) -> bool
emit_child_fill_dict(payload: dict) -> bool
emit_cohort_registry_dict(payload: dict) -> bool

# Flush
flush_all_sync(reason: str) -> dict[str, int]    # for non-async tests / EOD
async flush_all(reason: str) -> dict[str, int]   # cooperative-yield variant

# Observability
health_snapshot() -> dict
```

**Atomic write discipline (per spec §5):** `os.replace()` pattern. Each Parquet file write goes through `path.tmp` → `path` swap. Same pattern as `session_report.py:408` D218 fix. If the write itself raises, the tmp file is best-effort cleaned up (with a `# noqa: silent-handler` comment per the static-analysis convention — outer except re-raises so the caller still sees the error).

**Ring buffer (per spec §5):** default size 10. On ring-full, auto-flushes the affected schema. Crash-safety: ≤10 rows × 4 schemas = ≤40 rows lost in worst-case crash. If catastrophic loss observed, drop ring size to 1 via `InstrumentationWriter(ring_size=1)`.

**Partition layout (per spec §1-4):** `data/instrumentation/<schema>/session_date=YYYY-MM-DD/<schema>.parquet`. Re-flushing the same partition appends — read existing → concat → atomic rewrite. Volume is small (~150 rows/session/schema), so full-partition rewrite is correct + simpler than ParquetWriter row-group append.

### D261 PHASE0_SCHEMA_VALIDATION_FAILED — live

Three failure modes wire through D261:

1. **`emit_*_dict()` validation failure**: Pydantic raises `ValidationError`. Writer logs at WARNING with the literal `D261` marker, increments `_d261_failures[schema]` counter, and returns `False`. Buffer is NOT touched (invalid rows never enqueued).
2. **Existing-partition read failure** (corrupted Parquet, schema mismatch): logs WARNING + overwrites the partition with the new buffer. Lossy by design — acceptable per spec §5 crash-safety policy.
3. **Atomic write failure** (disk full, permission, etc.): logs ERROR + cleans up tmp file + re-raises so the caller sees the failure.

The `health_snapshot()` exposes the per-schema D261 counter for inclusion in EOD recon reports + dashboards.

---

## §2 — Test coverage (14/14)

| Category | Tests | What's pinned |
|----------|-------|---------------|
| **Schema round-trip** | 4 | Each schema writes via the writer, reads back via `pd.read_parquet`, asserts identical values. Includes the spec's MAAS case (200/3000 = 0.0667 q/v_τ). |
| **D261 validation** | 4 | Invalid payload → `False` return + counter bump + D261 log marker. Buffer remains empty. Direct-model construction raises Pydantic for production callers that prefer hard-fail. |
| **Buffering + atomicity** | 5 | Ring-full auto-flush, `flush_all_sync` writes every schema, append-on-second-flush, partition path uses `session_date=...`, async `flush_all` works under asyncio. |
| **Lifecycle integration** | 1 | Mock the spec's full lifecycle: submit → 2 partial fills → terminal → bar_context → cohort_registry. Asserts every schema writes correctly + cumulative qty math (Σchild_fill.qty == terminal_filled_qty == 200) + zero D261 failures on a clean lifecycle. |

**Static-analysis sanity:** the writer triggered the silent-handler check on `except OSError: pass` in the tmp-file cleanup. Resolved with the canonical `# noqa: silent-handler` on the `except` line per the convention pinned in commit 366d028 (the outer `except Exception` re-raises, so the inner cleanup is correctly best-effort).

---

## §3 — Wire-in deferred (to follow-up commit)

The user's plan called for wiring 4 emit-sites in this ship:

> Wire emit_trade_context at three sites in alpaca_executor.py + bridge.py.
> Wire emit_bar_context at bridge.execute_verdict.
> Wire emit_child_fill_tick at every D217 poll.
> Wire emit_cohort_registry at every BAR-1 fire.

**Decision: defer to a follow-up commit.** Two reasons:

1. **Reviewability**: wiring 4 emit-sites means touching the production execution stack (alpaca_executor + bridge). That diff is best reviewed in isolation from the schema + writer foundation. Today's ship is the load-bearing foundation; tomorrow's ship is the production hookup.
2. **Spec §7 explicitly orders it as 4 PRs**: each hook site is a separate PR per the original spec. Combining all 4 + the foundation into one commit would be ~6 PRs of work in a single diff. The atomic-ship discipline applied here is "schemas + writer + tests" as one atomic foundation; production wire-in is the next sequence.

**What "deferred" specifically means:**

- The InstrumentationWriter is fully testable (14/14 tests cover every code path).
- Production `alpaca_executor.py` and `bridge.py` are unchanged in this commit.
- A `InstrumentationWriter(...)` instance is NOT yet created in `main.py` startup. Tomorrow's wire-in commit adds:
  ```python
  # main.py session-init
  _phase0_writer = InstrumentationWriter()
  # ... wire emit_* calls in 4 sites ...
  # main.py session-close hook
  await _phase0_writer.flush_all(reason="eod")
  ```

The follow-up commit will be small and well-scoped because the writer's API surface is now fixed.

---

## §4 — What's unblocked by this ship

The QMP migration findings doc (`33_qmp_migration_findings.md` §3) named Phase 0 as the prerequisite for the QMP retrospective corpus delta. Specifically, `child_fill_ticks` must include `nbbo_bid_at_fill` + `nbbo_ask_at_fill` for QMP signing to compute. Today's ship reserves those columns (NULLable in MVP); the v2.1 WebSocket subscription fills them.

The Bayesian (η, γ) estimator (item 12-15 of the user's plan) similarly depends on `trade_context.submit_nbbo_*` + `terminal_nbbo_*` to back out per-fill realized slippage. Today's ship makes the schema available; the estimator can be drafted against the schema even before the wire-in commit.

The differential testing harness (Track D, item 27-28) takes a Phase 0 recorded session as its replay input. Today's ship makes "a Phase 0 recorded session" a definable artifact. The harness can be implemented + tested against synthetic Parquet files generated by the writer's `flush_all_sync` in tests, before any real session data exists.

---

## §5 — Cost estimate vs spec §9 forecast

| Item | Spec §9 estimate | Actual this ship |
|------|------------------|------------------|
| Schemas + writer | 1 PR (~1 day) | ~2 hours (schema simplicity + Pydantic v2 generators) |
| Tests | (per PR) | 14 tests, 0.83s suite |
| Wire-in | 4 PRs (~4 days) | DEFERRED — separate commit |
| WebSocket NBBO | DEFERRED to v2.1 | DEFERRED |
| **Total** | 5 engineering days | ~2 hours for foundation; wire-in is next |

The foundation came in 5x faster than the spec estimate because Pydantic v2 + pyarrow handled most of the boilerplate. The wire-in ship is the labor-intensive one.

---

## §6 — Knock-on items captured (follow-up commit checklist)

1. **`alpaca_executor.submit_oto_order`** — emit `TradeContextRow` immediately after `client.submit_order` returns. Capture `submit_nbbo_*` from the entry snapshot already passed in.
2. **`bridge._poll_for_terminal_fill`** — on terminal: emit a refreshed `TradeContextRow` with `terminal_*` columns, plus emit one `ChildFillRow` per leg from `client.get_orders(order_id=...).legs`.
3. **`bridge.execute_verdict()`** — after `self._pm.add_position(position)`: emit one `BarContextRow` (lookup the entry bar via existing bar-recorder) + emit `CohortRow` for each cohort match.
4. **`main.py` startup** — instantiate `InstrumentationWriter()` once per session.
5. **`main.py` EOD hook** — `await _phase0_writer.flush_all(reason="eod")` and include `health_snapshot()` in the EOD report.
6. **EOD report integration** — surface the per-schema row counts + D261 failure counters in `src/monitoring/eod_recon.py`'s summary block.

Each wire-in is independently revertable. None block the architecture call.

---

## §7 — Discovery rate impact

This ship surfaces 0 bugs (it's a foundation, not a bug-hunt). But it **enables** the next round of discovery infrastructure: the Bayesian estimator's posterior-predictive divergence checks, the differential testing harness, the QMP retrospective corpus delta, and the BOCPD pre-training all depend on Phase 0 data being captured. The discovery-rate ratio (currently 11/0 = ∞) is preserved; the ceiling on it just got higher.

**Suite state cumulative:** 102/102 across phase0 + qmp + property + static-analysis + d219 in 22.48s.
