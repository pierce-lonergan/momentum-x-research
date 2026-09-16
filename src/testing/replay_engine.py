"""Differential replay engine — load Phase 0 captured session into a
ReplayInput, build pre-/post-patch variant callables, drive the
DifferentialHarness across two SHAs.

Per `35_differential_harness_foundation.md` §3 + Track D foundation
(commit `e4c32a3`, tag `v2.2-difftest-foundation`). This module
closes the "foundation → active per-PR replay comparison" gap.

Pipeline:
  1. `ReplayInput.from_phase0_partition(base_dir, session_date)`
     → walks the trade_context + child_fill_ticks + bar_context Parquets
     → emits one OperationCall per significant production-code event
     → returns a ReplayInput ready for the harness

  2. `build_inprocess_variant(callable_map)`
     → wraps a dict of {op_name: callable} into the variant signature
     → suitable for synthetic harness tests + future per-SHA replay

  3. `git_replay_compare(base_sha, head_sha, replay_input, allowlist)`
     → checkout base_sha → run replay_input through the SHA's variant
        → save records_a
     → checkout head_sha → run replay_input → save records_b
     → return DivergenceReport
     → restore original branch

For MVP: shipping (1) + (2) and a synthetic-data integration test.
The git_replay_compare wrapper is the v2 enhancement that requires
careful subprocess management + working-copy guards. This module's
load + variant-build surface is the load-bearing piece.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.testing.differential_harness import OperationCall, ReplayInput

logger = logging.getLogger(__name__)


# ── Phase 0 Parquet → ReplayInput ──────────────────────────────


def replay_input_from_phase0(
    base_dir: Path | str,
    session_date: str,
) -> ReplayInput:
    """Walk the day's Phase 0 partitions + emit one OperationCall per event.

    Operations emitted:
      submit_order        — one per TradeContextRow with terminal_status="pending"
      child_fill          — one per ChildFillRow
      add_position        — one per BarContextRow (entry-bar context)
      cohort_match        — one per CohortRow (peer match)
      terminal_fill       — one per TradeContextRow with terminal_status != "pending"

    Each call's args/kwargs carry the row's data verbatim. The variant
    callable decides what to do with them.

    Returns an empty ReplayInput if no partitions exist.
    """
    base = Path(base_dir)
    ops: list[OperationCall] = []

    def _load(schema: str, filename: str) -> "list[dict]":
        path = base / schema / f"session_date={session_date}" / filename
        if not path.exists():
            return []
        try:
            import pandas as pd
            return pd.read_parquet(path).to_dict("records")
        except Exception as e:
            logger.debug("Replay load failed for %s: %s", path, e)
            return []

    trade_rows = _load("trade_context", "orders.parquet")
    fill_rows = _load("child_fill_ticks", "fills.parquet")
    bar_rows = _load("bar_context", "entries.parquet")
    cohort_rows = _load("cohort_registry", "cohorts.parquet")

    # Emit submit_order events first (chronological by submit_ts)
    submit_rows = [r for r in trade_rows if r.get("terminal_status") == "pending"]
    submit_rows.sort(key=lambda r: str(r.get("submit_ts", "")))
    for r in submit_rows:
        ops.append(OperationCall.of("submit_order", **{
            k: r[k] for k in (
                "order_id", "ticker", "side", "requested_qty", "requested_px",
            ) if k in r
        }))

    # Then child fills (chronological by child_fill_ts)
    fill_rows.sort(key=lambda r: str(r.get("child_fill_ts", "")))
    for r in fill_rows:
        ops.append(OperationCall.of("child_fill", **{
            k: r[k] for k in (
                "parent_order_id", "qty", "price", "cumulative_filled_qty",
            ) if k in r
        }))

    # Then add_position events (one per BarContextRow)
    bar_rows.sort(key=lambda r: str(r.get("entry_ts", "")))
    for r in bar_rows:
        ops.append(OperationCall.of("add_position", **{
            k: r[k] for k in (
                "position_id", "ticker", "our_q_shares", "q_over_v_tau",
                "entry_bar_open", "entry_bar_close", "entry_bar_volume",
            ) if k in r
        }))

    # Cohort matches
    cohort_rows.sort(key=lambda r: str(r.get("match_dt", "")))
    for r in cohort_rows:
        ops.append(OperationCall.of("cohort_match", **{
            k: r[k] for k in (
                "cohort_id", "traded_ticker", "cohort_ticker",
                "cohort_entry_ref_px",
            ) if k in r
        }))

    # Finally terminal fills (one per TradeContextRow with non-pending status)
    terminal_rows = [r for r in trade_rows if r.get("terminal_status") != "pending"]
    terminal_rows.sort(key=lambda r: str(r.get("terminal_ts", "")))
    for r in terminal_rows:
        ops.append(OperationCall.of("terminal_fill", **{
            k: r[k] for k in (
                "order_id", "ticker", "terminal_status", "terminal_filled_qty",
            ) if k in r
        }))

    logger.info(
        "Replay engine: built ReplayInput with %d ops from session %s "
        "(%d submits, %d fills, %d bars, %d cohorts, %d terminals)",
        len(ops), session_date,
        len(submit_rows), len(fill_rows), len(bar_rows),
        len(cohort_rows), len(terminal_rows),
    )
    return ReplayInput.from_iterable(ops)


# ── In-process variant builder ──────────────────────────────────


def build_inprocess_variant(
    handlers: dict[str, Callable[..., object]],
) -> Callable[..., object]:
    """Wrap a dict of operation handlers into the variant signature
    expected by DifferentialHarness.

    The variant signature is `(op_name, *args, **kwargs) -> result`.
    Any op_name not in `handlers` raises KeyError (which the harness
    captures as a divergence vs. a variant that handles the op).

    Use case: pre-patch and post-patch versions of the production code
    are represented as two `handlers` dicts; differences in handler
    behavior surface as DivergenceReport entries.
    """
    def _variant(op_name: str, *args, **kwargs):
        if op_name not in handlers:
            raise KeyError(f"variant has no handler for op {op_name!r}")
        return handlers[op_name](*args, **kwargs)
    return _variant


# ── Future: git-aware per-SHA replay ───────────────────────────


@dataclass(frozen=True)
class GitReplayPlaceholder:
    """Stub for the git-aware per-SHA replay wrapper. Tracked here so
    callers that import the symbol get a clear error message rather
    than ImportError.

    Implementation deferred to v2 (`scripts/check_differential_diff.py`
    becomes the orchestrator: stash → checkout base → replay → checkout
    head → replay → restore). Tracked in
    35_differential_harness_foundation.md §3."""

    base_sha: str
    head_sha: str
    replay_input: ReplayInput
    allowlist: frozenset[str]

    def __post_init__(self) -> None:
        raise NotImplementedError(
            "git_replay_compare is the v2 enhancement; today's foundation "
            "ships the loader + variant builder. See "
            "docs/research-log/35_differential_harness_foundation.md §3 for the "
            "design + scripts/check_differential_diff.py for the partial "
            "wrapper."
        )
