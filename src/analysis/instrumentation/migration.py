"""Phase 0 schema migration — v1 → v2 forward-compat.

Per `34_phase0_instrumentation_mvp_shipped.md` §6 and `21_phase0_instrumentation_mvp.md`:
schemas carry `schema_version: int = SCHEMA_VERSION` for future migrations.
Currently SCHEMA_VERSION=1. This module is the foundation for v2:

  * `migrate_row(row, target_version)` — apply registered migration steps
    to bring `row` (a dict from a Parquet partition) up to `target_version`.
  * `register_migration(from_version, to_version, fn)` — declare a migration
    step. The migrator chains them automatically (v1→v2→v3…).
  * `read_with_migration(parquet_path, schema_class, target_version)` —
    convenience: load a Parquet partition, migrate every row to
    `target_version`, validate against `schema_class`, return list.

The MIGRATIONS registry starts empty (only v1 exists). When v2 ships:
  1. Bump SCHEMA_VERSION in schemas.py to 2.
  2. Add the new field with a default (or compute it from existing fields).
  3. Register a v1→v2 migrator here that adds the field.
  4. Existing partitions remain readable via `read_with_migration`.

This is the "forward-compat foundation" the Phase 0 MVP findings doc
named — we're building it BEFORE we need it so the v2 transition is
zero-downtime.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Migration registry ─────────────────────────────────────────


# Each migration is a function (row_dict) → row_dict that adds new fields,
# defaults old ones, or transforms shape. Indexed by (from_version, to_version).
MigrationFn = Callable[[dict], dict]
MIGRATIONS: dict[tuple[int, int], MigrationFn] = {}


def register_migration(from_version: int, to_version: int, fn: MigrationFn) -> None:
    """Declare a migration step. Re-registering the same (from, to) pair
    overwrites the previous (useful for testing)."""
    if to_version != from_version + 1:
        raise ValueError(
            f"migration must increment by exactly 1 "
            f"(got {from_version}→{to_version})"
        )
    MIGRATIONS[(from_version, to_version)] = fn
    logger.debug(
        "Registered migration v%d → v%d (total registered: %d)",
        from_version, to_version, len(MIGRATIONS),
    )


def migrate_row(row: dict, target_version: int) -> dict:
    """Apply registered migration steps to bring `row` up to `target_version`.

    Returns the migrated row dict. Raises ValueError if a required
    intermediate migration step is unregistered.
    """
    current = int(row.get("schema_version", 1))
    if current == target_version:
        return row
    if current > target_version:
        raise ValueError(
            f"row schema_version={current} > target={target_version} "
            f"(no down-migrations supported)"
        )
    out = dict(row)  # copy so we don't mutate caller's row
    while current < target_version:
        next_v = current + 1
        key = (current, next_v)
        if key not in MIGRATIONS:
            raise ValueError(
                f"no migration registered for v{current} → v{next_v}; "
                f"cannot reach v{target_version}"
            )
        out = MIGRATIONS[key](out)
        out["schema_version"] = next_v
        current = next_v
    return out


def read_with_migration(
    parquet_path: Any,
    schema_class: Any,
    *,
    target_version: int,
) -> list[Any]:
    """Load a Parquet partition, migrate every row to `target_version`,
    validate via `schema_class.model_validate`, return list of model
    instances.

    Args:
      parquet_path:    Path to .parquet file
      schema_class:    Pydantic model class (e.g. TradeContextRow)
      target_version:  Target schema version (typically the current
                       SCHEMA_VERSION)

    Rows that fail Pydantic validation post-migration are dropped with
    a WARN log (D261-style discipline — never crash the read path).
    """
    import pandas as pd
    from pathlib import Path
    p = Path(parquet_path)
    if not p.exists():
        return []
    try:
        df = pd.read_parquet(p)
    except Exception as e:
        logger.warning("Phase 0 migration read failed for %s: %s", p, e)
        return []

    out: list[Any] = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        try:
            migrated = migrate_row(row_dict, target_version)
        except ValueError as e:
            logger.warning(
                "Phase 0 migration: skipping row (no migration path): %s", e,
            )
            continue
        try:
            instance = schema_class.model_validate(migrated)
            out.append(instance)
        except Exception as e:
            logger.warning(
                "Phase 0 migration: skipping row (validation failure): %s", e,
            )
            continue
    return out


# ── Future migrations register here ────────────────────────────


# When v2 ships, uncomment + populate. Example pattern:
#
#   def _migrate_trade_context_v1_to_v2(row: dict) -> dict:
#       """v2 adds `client_order_id` field. Default to '' for v1 rows."""
#       row.setdefault("client_order_id", "")
#       return row
#
#   register_migration(1, 2, _migrate_trade_context_v1_to_v2)
