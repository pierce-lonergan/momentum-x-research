"""I/O and helper functions for ProductionVerdict.

Companion to ``src/production_arena/types.py``. This module owns:

1. JSONL persistence (write/read) for arena sweep outputs.
2. ``verdict_from_dict`` — type-coerced round-trip from raw JSON.
3. ``journal_to_verdict`` — bridge from live production journal rows
   (``data/journals/journal_*.jsonl``) into ``ProductionVerdict`` so the
   Phase 2.6 divergence-budget analysis can compare arena verdicts vs
   actual production decisions.

Stdlib only (json, dataclasses, pathlib, logging) — see
``docs/research-log/02_arena_critique.md`` for design rationale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable

from src.production_arena.types import ProductionVerdict, VerdictDecision

logger = logging.getLogger(__name__)


# ── Project-root anchor (static-analysis enforces absolute paths) ───────

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
"""Repo root — directory containing main.py, src/, scripts/, data/.

Defined here so callers that pass ``path=None`` get an absolute default
under ``_PROJECT_ROOT / "data" / ...`` rather than a cwd-dependent
``Path("data/...")`` (which would be flagged by
``tests/static_analysis/test_relative_paths.py``).
"""

_DEFAULT_VERDICTS_PATH: Path = _PROJECT_ROOT / "data" / "production_arena" / "verdicts.jsonl"

_VALID_DECISIONS: frozenset[str] = frozenset({"BUY", "NO_TRADE", "ERROR"})

_VERDICT_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in fields(ProductionVerdict))


# ── Internal type-coercion helpers ──────────────────────────────────────


def _as_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        logger.warning("verdict_from_dict: cannot coerce %r to float: %s", v, e)
        return None


def _as_bool_or_none(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y")
    return bool(v)


def _as_decision(v: Any) -> VerdictDecision:
    s = str(v).upper().strip()
    if s in _VALID_DECISIONS:
        return s  # type: ignore[return-value]
    logger.warning("verdict_from_dict: unknown decision %r → coercing to ERROR", v)
    return "ERROR"


def _as_components_or_none(v: Any) -> dict[str, float] | None:
    if v is None:
        return None
    if not isinstance(v, dict):
        logger.warning("verdict_from_dict: mfcs_components is not a dict (%r)", type(v))
        return None
    out: dict[str, float] = {}
    for k, val in v.items():
        f = _as_float_or_none(val)
        if f is not None:
            out[str(k)] = f
    return out or None


# ── Public API: write / read JSONL ──────────────────────────────────────


def write_verdicts(
    verdicts: Iterable[ProductionVerdict],
    path: Path | None = None,
) -> int:
    """Append verdicts to a JSONL file. Creates parent dirs if missing.

    Append-safe (mode="a") so multiple sweep runs can stream into the
    same output file. Returns the number of verdicts written.

    Each verdict is serialized via ``ProductionVerdict.to_dict()`` so the
    ``mfcs_components`` nested dict and any None values are preserved
    cleanly (json.dumps handles both natively).
    """
    target = Path(path) if path is not None else _DEFAULT_VERDICTS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    n_written = 0
    with open(target, mode="a", encoding="utf-8", newline="\n") as f:
        for v in verdicts:
            line = json.dumps(v.to_dict(), ensure_ascii=False, default=str)
            f.write(line + "\n")
            n_written += 1
    return n_written


def _open_jsonl_text(path: Path) -> str:
    """Read JSONL text, handling UTF-8 and the UTF-16 BOM that PowerShell
    stderr redirection occasionally produces."""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as e:
            logger.warning("read_verdicts: UTF-16 decode failed for %s: %s", path, e)
            return raw.decode("utf-8", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        logger.warning("read_verdicts: UTF-8 decode fallback for %s: %s", path, e)
        return raw.decode("utf-8", errors="replace")


def read_verdicts(path: Path) -> list[ProductionVerdict]:
    """Read a JSONL file back into ProductionVerdict objects.

    Skips malformed lines with a ``logger.warning``. Handles both UTF-8
    and UTF-16 encoded files (PowerShell stderr redirection sometimes
    produces UTF-16).
    """
    p = Path(path)
    if not p.exists():
        logger.warning("read_verdicts: file does not exist: %s", p)
        return []

    text = _open_jsonl_text(p)
    out: list[ProductionVerdict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            d = json.loads(stripped)
        except json.JSONDecodeError as e:
            logger.warning("read_verdicts: skipping malformed line %d in %s: %s", lineno, p, e)
            continue
        if not isinstance(d, dict):
            logger.warning("read_verdicts: skipping non-dict line %d in %s", lineno, p)
            continue
        try:
            out.append(verdict_from_dict(d))
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("read_verdicts: skipping un-coercible line %d in %s: %s", lineno, p, e)
            continue
    return out


# ── Public API: dict round-trip ─────────────────────────────────────────


def verdict_from_dict(d: dict) -> ProductionVerdict:
    """Round-trip helper. Coerces types — JSON has no int vs float distinction.

    Required keys: ``ticker``, ``session_date``, ``decision``.
    All other fields are optional and default per the dataclass.
    """
    if "ticker" not in d:
        raise KeyError("verdict_from_dict: missing required field 'ticker'")
    if "session_date" not in d:
        raise KeyError("verdict_from_dict: missing required field 'session_date'")
    if "decision" not in d:
        raise KeyError("verdict_from_dict: missing required field 'decision'")

    # Drop unknown keys defensively — protects against schema drift on
    # old verdict files when new fields are added (or removed).
    return ProductionVerdict(
        ticker=str(d["ticker"]),
        session_date=str(d["session_date"]),
        decision=_as_decision(d["decision"]),
        gate_rejected=(str(d["gate_rejected"]) if d.get("gate_rejected") is not None else None),
        mfcs=_as_float_or_none(d.get("mfcs")),
        mfcs_components=_as_components_or_none(d.get("mfcs_components")),
        would_be_entry_price=_as_float_or_none(d.get("would_be_entry_price")),
        would_be_exit_price=_as_float_or_none(d.get("would_be_exit_price")),
        would_be_exit_time=(
            str(d["would_be_exit_time"]) if d.get("would_be_exit_time") is not None else None
        ),
        would_be_pnl_pct=_as_float_or_none(d.get("would_be_pnl_pct")),
        mfe_pct=_as_float_or_none(d.get("mfe_pct")),
        mae_pct=_as_float_or_none(d.get("mae_pct")),
        orb_confirmed=_as_bool_or_none(d.get("orb_confirmed")),
        elapsed_ms=_as_float_or_none(d.get("elapsed_ms")) or 0.0,
        error_msg=(str(d["error_msg"]) if d.get("error_msg") is not None else None),
        composite_score_prescore=_as_float_or_none(d.get("composite_score_prescore")),
        composite_score_full=_as_float_or_none(d.get("composite_score_full")),
    )


# ── Public API: production-journal bridge ───────────────────────────────


def _is_evaluation_row(row: dict) -> bool:
    """Heuristic: a journal row represents a candidate evaluation iff it
    has a ticker AND a non-empty action field.

    Live journals also contain rows with ``action == ""`` (pre-evaluation
    stubs written when an order fill arrives before the orchestrator
    finished scoring) and may contain telemetry / session-marker rows
    without ``ticker`` or ``action`` keys at all. Both are filtered."""
    if not isinstance(row, dict):
        return False
    if "ticker" not in row or not row.get("ticker"):
        return False
    action = row.get("action")
    if action is None or str(action).strip() == "":
        return False
    return True


def _journal_action_to_decision(action: str) -> VerdictDecision:
    a = action.upper().strip()
    if a == "BUY":
        return "BUY"
    if a == "NO_TRADE":
        return "NO_TRADE"
    # Anything else (SELL, HOLD, unexpected) — flag as ERROR so the
    # divergence-budget analysis can spot the schema mismatch.
    logger.warning("journal_to_verdict: unexpected action %r → ERROR", action)
    return "ERROR"


def journal_to_verdict(journal_entry: dict) -> ProductionVerdict | None:
    """Convert a production journal row into a ``ProductionVerdict``.

    Used by the Phase 2.6 divergence-budget analysis to compare arena
    verdicts vs actual production verdicts. Returns ``None`` if the row
    isn't a candidate evaluation (e.g. session-start markers, telemetry
    pings, or fill-only stubs with empty action).

    Mapping (live journal → ProductionVerdict):
      ticker            → ticker
      session_date      → session_date
      action            → decision           (BUY / NO_TRADE / ERROR)
      rejection_reason  → gate_rejected      (NO_TRADE only)
      mfcs              → mfcs
      component_scores  → mfcs_components
      entry_price       → would_be_entry_price (BUY only)
      pipeline_latency_ms → elapsed_ms
    """
    if not _is_evaluation_row(journal_entry):
        return None

    decision = _journal_action_to_decision(str(journal_entry["action"]))

    gate_rejected: str | None = None
    if decision == "NO_TRADE":
        rr = journal_entry.get("rejection_reason") or journal_entry.get("reasoning_summary")
        gate_rejected = str(rr) if rr else None

    components = _as_components_or_none(journal_entry.get("component_scores"))

    would_be_entry: float | None = None
    if decision == "BUY":
        would_be_entry = _as_float_or_none(journal_entry.get("entry_price"))

    elapsed = _as_float_or_none(journal_entry.get("pipeline_latency_ms")) or 0.0

    error_msg: str | None = None
    if decision == "ERROR":
        # Surface whatever context the journal had so divergence triage
        # has something to grep for.
        error_msg = str(journal_entry.get("rejection_reason") or journal_entry.get("action") or "")

    return ProductionVerdict(
        ticker=str(journal_entry["ticker"]),
        session_date=str(journal_entry.get("session_date", "")),
        decision=decision,
        gate_rejected=gate_rejected,
        mfcs=_as_float_or_none(journal_entry.get("mfcs")),
        mfcs_components=components,
        would_be_entry_price=would_be_entry,
        elapsed_ms=elapsed,
        error_msg=error_msg,
    )
