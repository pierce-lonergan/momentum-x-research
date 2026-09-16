"""InstrumentationWriter — single source of truth for Phase 0 emission.

Per `docs/research-log/21_phase0_instrumentation_mvp.md` §5. All four schemas
land via this writer; each session's data partitions by `session_date`
under `data/instrumentation/<schema>/session_date=YYYY-MM-DD/`.

Design contract:
  * In-memory ring buffer per schema (default size 10) — flushed on
    (a) ring full, (b) explicit `flush_all`, (c) EOD via session-close hook.
  * Atomic writes via `os.replace()` pattern — `path.tmp` → `path` swap
    so a partial write never produces a corrupted Parquet file (the
    canonical D218 fix pattern from `session_report.py:408`).
  * D261 PHASE0_SCHEMA_VALIDATION_FAILED: any emit_* that fails Pydantic
    validation logs at WARNING with the exception threaded through and
    increments a counter on the writer itself.
  * Crash-safety: ring buffer loss is acceptable (≤10 rows × 4 schemas
    = ≤40 rows per session). If catastrophic loss observed, drop ring
    size to 1 (= sync write) by passing `ring_size=1` at construction.

Concurrency: this writer is NOT thread-safe. The expected call pattern
is from the asyncio event loop's main thread (each emit + flush is
synchronous; the only awaitable is `flush_all` which yields to the loop
between schema writes to avoid blocking the trading hot path).

Wire-in (deferred to follow-up commit):
  bridge.execute_verdict()   → bar_context, cohort_registry
  alpaca_executor.submit_oto → trade_context (submit row)
  bridge._poll_for_terminal  → trade_context (terminal cols), child_fill_ticks
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, ValidationError

from src.analysis.instrumentation.schemas import (
    BarContextRow,
    ChildFillRow,
    CohortRow,
    DecisionRow,
    TradeContextRow,
)


logger = logging.getLogger(__name__)


class SchemaValidationError(Exception):
    """Raised when an emit_* call's payload fails Pydantic validation.

    The writer logs D261 + counts the failure and re-raises; the caller
    decides whether to swallow (silently lose the row) or propagate.
    Default helper methods like `emit_trade_context_dict` swallow + log.
    """


class InstrumentationWriter:
    """Single source of truth for Phase 0 row emission.

    Args:
      base_dir:    Root directory for instrumentation output. Defaults to
                   `data/instrumentation/` relative to repo root.
      session_date: The trading-session date used for partition naming.
                    Defaults to today UTC.
      ring_size:   Per-schema buffer size before auto-flush. Default 10.
                   Pass 1 for sync-on-emit (zero-row crash loss).
    """

    SCHEMA_PATHS: ClassVar[dict[str, str]] = {
        "trade_context": "trade_context",
        "bar_context": "bar_context",
        "child_fill_ticks": "child_fill_ticks",
        "cohort_registry": "cohort_registry",
        # Tier 4 #15 (Bug AO 2026-04-27): per-orchestrator-verdict capture
        "decision_row": "decision_row",
    }
    SCHEMA_FILES: ClassVar[dict[str, str]] = {
        "trade_context": "orders.parquet",
        "bar_context": "entries.parquet",
        "child_fill_ticks": "fills.parquet",
        "cohort_registry": "cohorts.parquet",
        "decision_row": "decisions.parquet",
    }

    def __init__(
        self,
        base_dir: Path | str = "data/instrumentation",
        session_date: str | None = None,
        ring_size: int = 10,
    ) -> None:
        self.base_dir: Path = Path(base_dir)
        self.session_date: str = session_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.ring_size: int = max(1, int(ring_size))
        self._buffers: dict[str, list[BaseModel]] = {
            k: [] for k in self.SCHEMA_PATHS
        }
        # D261 counters — bump on validation failure; flush + show in EOD report
        self._d261_failures: dict[str, int] = {k: 0 for k in self.SCHEMA_PATHS}

    # ── Path helpers ──────────────────────────────────────────────

    def _partition_dir(self, schema: str) -> Path:
        return (
            self.base_dir
            / self.SCHEMA_PATHS[schema]
            / f"session_date={self.session_date}"
        )

    def _output_path(self, schema: str) -> Path:
        return self._partition_dir(schema) / self.SCHEMA_FILES[schema]

    # ── Emit (one row at a time) ──────────────────────────────────

    def emit_trade_context(self, row: TradeContextRow) -> None:
        """Emit a validated TradeContextRow. Auto-flushes on ring full."""
        self._enqueue("trade_context", row)

    def emit_bar_context(self, row: BarContextRow) -> None:
        self._enqueue("bar_context", row)

    def emit_child_fill(self, row: ChildFillRow) -> None:
        self._enqueue("child_fill_ticks", row)

    def emit_cohort_registry(self, row: CohortRow) -> None:
        self._enqueue("cohort_registry", row)

    def emit_decision_row(self, row: DecisionRow) -> None:
        """Tier 4 #15: emit a per-orchestrator-verdict DecisionRow.

        Wire site: orchestrator's verdict-finalization paths
        (_build_no_trade_verdict, _build_buy_verdict, etc.). Auto-flushes
        on ring full like the other emit methods."""
        self._enqueue("decision_row", row)

    # ── Dict variants — graceful on validation failure (D261) ─────

    def emit_trade_context_dict(self, payload: dict) -> bool:
        return self._enqueue_from_dict("trade_context", TradeContextRow, payload)

    def emit_bar_context_dict(self, payload: dict) -> bool:
        return self._enqueue_from_dict("bar_context", BarContextRow, payload)

    def emit_child_fill_dict(self, payload: dict) -> bool:
        return self._enqueue_from_dict("child_fill_ticks", ChildFillRow, payload)

    def emit_cohort_registry_dict(self, payload: dict) -> bool:
        return self._enqueue_from_dict("cohort_registry", CohortRow, payload)

    def emit_decision_row_dict(self, payload: dict) -> bool:
        """Tier 4 #15: dict variant for orchestrator hook (graceful on
        Pydantic ValidationError — D261 counter increments + log)."""
        return self._enqueue_from_dict("decision_row", DecisionRow, payload)

    # ── Internal enqueue ──────────────────────────────────────────

    def _enqueue(self, schema: str, row: BaseModel) -> None:
        self._buffers[schema].append(row)
        if len(self._buffers[schema]) >= self.ring_size:
            self._flush_one(schema, reason="ring_full")

    def _enqueue_from_dict(
        self, schema: str, model_cls: type[BaseModel], payload: dict,
    ) -> bool:
        """Construct + validate a row from a dict; on failure, log D261
        + bump counter + return False. Never raises."""
        try:
            row = model_cls(**payload)
        except ValidationError as e:
            self._d261_failures[schema] += 1
            logger.warning(
                "D261 PHASE0_SCHEMA_VALIDATION_FAILED schema=%s payload_keys=%s "
                "errors=%s",
                schema, sorted(payload.keys()), e.errors()[:3],
            )
            return False
        self._enqueue(schema, row)
        return True

    # ── Flush ─────────────────────────────────────────────────────

    def _flush_one(self, schema: str, reason: str = "manual") -> int:
        """Flush a single schema's buffer to disk atomically.

        Returns the row count written (0 if buffer was empty)."""
        buf = self._buffers[schema]
        if not buf:
            return 0

        out_path = self._output_path(schema)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

        # Build the new dataframe from the buffer
        new_df = pd.DataFrame([m.model_dump(mode="json") for m in buf])

        # If a Parquet file already exists for this partition, append by reading
        # + concatenating + rewriting. (Parquet doesn't support in-place append
        # without partition-level rewrite — the volume here is small enough that
        # full rewrite is correct + simpler than ParquetWriter row-group append.)
        if out_path.exists():
            try:
                existing = pd.read_parquet(out_path)
                combined = pd.concat([existing, new_df], ignore_index=True)
            except Exception as e:
                logger.warning(
                    "D261 PHASE0_SCHEMA_VALIDATION_FAILED: failed to read existing "
                    "%s — overwriting (%s rows lost): %s",
                    out_path, "unknown", e,
                )
                combined = new_df
        else:
            combined = new_df

        # Atomic write via tmp + os.replace (D218 pattern)
        try:
            combined.to_parquet(tmp_path, index=False)
            os.replace(tmp_path, out_path)
        except Exception as e:
            # Clean up tmp file if write failed
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:  # noqa: silent-handler — best-effort cleanup; outer except re-raises
                    pass
            logger.error(
                "D261 PHASE0_SCHEMA_VALIDATION_FAILED: atomic write failed for "
                "%s (reason=%s): %s",
                out_path, reason, e,
            )
            raise

        n_written = len(buf)
        self._buffers[schema] = []
        logger.info(
            "PHASE0 FLUSH schema=%s rows=%d reason=%s path=%s",
            schema, n_written, reason, out_path,
        )
        return n_written

    async def flush_all(self, reason: str = "manual") -> dict[str, int]:
        """Flush every schema's buffer. Yields between schemas to avoid
        blocking the asyncio loop on a multi-schema flush.

        Returns a dict mapping schema name → rows written.
        """
        import asyncio
        counts: dict[str, int] = {}
        for schema in self.SCHEMA_PATHS:
            counts[schema] = self._flush_one(schema, reason=reason)
            await asyncio.sleep(0)  # cooperative yield
        return counts

    def flush_all_sync(self, reason: str = "manual") -> dict[str, int]:
        """Synchronous flush variant — use from non-async contexts (tests,
        EOD batch hooks). Behaviourally equivalent to `flush_all` minus
        the cooperative yield."""
        return {
            schema: self._flush_one(schema, reason=reason)
            for schema in self.SCHEMA_PATHS
        }

    # ── Health / observability ────────────────────────────────────

    def health_snapshot(self) -> dict[str, Any]:
        """One-shot snapshot of writer state — for EOD report inclusion."""
        return {
            "session_date": self.session_date,
            "ring_size": self.ring_size,
            "buffer_lengths": {k: len(v) for k, v in self._buffers.items()},
            "d261_failures": dict(self._d261_failures),
            "base_dir": str(self.base_dir),
        }
