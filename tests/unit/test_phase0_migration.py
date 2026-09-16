"""Phase 0 schema migrator tests — forward-compat foundation.

Per `34_phase0_instrumentation_mvp_shipped.md` §6 follow-up #4. The
SCHEMA_VERSION=1 schema is locked in production; this module is the
foundation for v2+ transitions WITHOUT breaking v1 partition readers.

10 tests:
  - register_migration validation (3)
  - migrate_row identity / multi-step / errors (4)
  - read_with_migration round-trip + Pydantic validation (3)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.analysis.instrumentation import (
    MIGRATIONS,
    SCHEMA_VERSION,
    TradeContextRow,
    migrate_row,
    read_with_migration,
    register_migration,
)


# ── register_migration ─────────────────────────────────────────


class TestRegisterMigration:

    def setup_method(self) -> None:
        # Snapshot then clear the registry per-test; restore in teardown
        self._saved_migrations = dict(MIGRATIONS)
        MIGRATIONS.clear()

    def teardown_method(self) -> None:
        MIGRATIONS.clear()
        MIGRATIONS.update(self._saved_migrations)

    def test_register_valid_increment(self) -> None:
        def fn(row): return row
        register_migration(1, 2, fn)
        assert (1, 2) in MIGRATIONS
        assert MIGRATIONS[(1, 2)] is fn

    def test_register_skip_invalid(self) -> None:
        def fn(row): return row
        with pytest.raises(ValueError, match="must increment by exactly 1"):
            register_migration(1, 3, fn)

    def test_register_overwrites_existing(self) -> None:
        def fn1(row): row["v"] = 1; return row
        def fn2(row): row["v"] = 2; return row
        register_migration(1, 2, fn1)
        register_migration(1, 2, fn2)  # overwrite
        result = MIGRATIONS[(1, 2)]({})
        assert result == {"v": 2}


# ── migrate_row ────────────────────────────────────────────────


class TestMigrateRow:

    def setup_method(self) -> None:
        self._saved_migrations = dict(MIGRATIONS)
        MIGRATIONS.clear()

    def teardown_method(self) -> None:
        MIGRATIONS.clear()
        MIGRATIONS.update(self._saved_migrations)

    def test_identity_when_already_at_target(self) -> None:
        row = {"schema_version": 1, "x": "value"}
        out = migrate_row(row, target_version=1)
        assert out == row

    def test_single_step_v1_to_v2(self) -> None:
        def add_field(row):
            row["new_field"] = "default"
            return row
        register_migration(1, 2, add_field)
        row = {"schema_version": 1, "x": "value"}
        out = migrate_row(row, target_version=2)
        assert out["schema_version"] == 2
        assert out["new_field"] == "default"
        assert out["x"] == "value"
        # Original not mutated
        assert "new_field" not in row

    def test_multi_step_v1_to_v3(self) -> None:
        def to_v2(row):
            row["v2_field"] = "v2"
            return row
        def to_v3(row):
            row["v3_field"] = "v3"
            return row
        register_migration(1, 2, to_v2)
        register_migration(2, 3, to_v3)
        row = {"schema_version": 1}
        out = migrate_row(row, target_version=3)
        assert out["schema_version"] == 3
        assert out["v2_field"] == "v2"
        assert out["v3_field"] == "v3"

    def test_missing_intermediate_step_raises(self) -> None:
        def to_v3(row):
            return row
        register_migration(2, 3, to_v3)  # only v2→v3, no v1→v2
        row = {"schema_version": 1}
        with pytest.raises(ValueError, match="no migration registered for v1"):
            migrate_row(row, target_version=3)

    def test_down_migration_raises(self) -> None:
        row = {"schema_version": 5}
        with pytest.raises(ValueError, match="no down-migrations supported"):
            migrate_row(row, target_version=2)


# ── read_with_migration ────────────────────────────────────────


class TestReadWithMigration:

    def setup_method(self) -> None:
        self._saved_migrations = dict(MIGRATIONS)
        MIGRATIONS.clear()

    def teardown_method(self) -> None:
        MIGRATIONS.clear()
        MIGRATIONS.update(self._saved_migrations)

    def test_reads_v1_partition_at_current_version(self, tmp_path: Path) -> None:
        """Round-trip: v1 row → write → read_with_migration(target=1)."""
        path = tmp_path / "orders.parquet"
        row = {
            "schema_version": SCHEMA_VERSION,
            "order_id": "o1", "ticker": "AAPL", "side": "buy",
            "requested_qty": 100, "requested_px": 5.0,
            "submit_ts": datetime(2026, 4, 26, tzinfo=timezone.utc),
            "submit_nbbo_bid": None, "submit_nbbo_ask": None,
            "first_fill_ts": None,
            "first_fill_nbbo_bid": None, "first_fill_nbbo_ask": None,
            "terminal_ts": None,
            "terminal_nbbo_bid": None, "terminal_nbbo_ask": None,
            "terminal_status": "pending", "terminal_filled_qty": 0,
        }
        pd.DataFrame([row]).to_parquet(path, index=False)
        results = read_with_migration(
            path, TradeContextRow, target_version=SCHEMA_VERSION,
        )
        assert len(results) == 1
        assert isinstance(results[0], TradeContextRow)
        assert results[0].order_id == "o1"

    def test_returns_empty_for_missing_path(self, tmp_path: Path) -> None:
        results = read_with_migration(
            tmp_path / "missing.parquet", TradeContextRow, target_version=1,
        )
        assert results == []

    def test_drops_invalid_rows_with_warn(self, tmp_path: Path, caplog) -> None:
        """Rows that fail Pydantic post-migration are dropped (D261-style)."""
        import logging
        path = tmp_path / "bad.parquet"
        rows = [
            # Valid
            {"schema_version": SCHEMA_VERSION,
             "order_id": "good", "ticker": "X", "side": "buy",
             "requested_qty": 1, "requested_px": 1.0,
             "submit_ts": datetime(2026, 4, 26, tzinfo=timezone.utc),
             "submit_nbbo_bid": None, "submit_nbbo_ask": None,
             "first_fill_ts": None,
             "first_fill_nbbo_bid": None, "first_fill_nbbo_ask": None,
             "terminal_ts": None,
             "terminal_nbbo_bid": None, "terminal_nbbo_ask": None,
             "terminal_status": "pending", "terminal_filled_qty": 0},
            # Invalid (empty order_id)
            {"schema_version": SCHEMA_VERSION,
             "order_id": "", "ticker": "X", "side": "buy",
             "requested_qty": 1, "requested_px": 1.0,
             "submit_ts": datetime(2026, 4, 26, tzinfo=timezone.utc),
             "submit_nbbo_bid": None, "submit_nbbo_ask": None,
             "first_fill_ts": None,
             "first_fill_nbbo_bid": None, "first_fill_nbbo_ask": None,
             "terminal_ts": None,
             "terminal_nbbo_bid": None, "terminal_nbbo_ask": None,
             "terminal_status": "pending", "terminal_filled_qty": 0},
        ]
        pd.DataFrame(rows).to_parquet(path, index=False)
        with caplog.at_level(logging.WARNING, logger="src.analysis.instrumentation.migration"):
            results = read_with_migration(
                path, TradeContextRow, target_version=SCHEMA_VERSION,
            )
        # Only the valid row survives
        assert len(results) == 1
        assert results[0].order_id == "good"
        assert any("validation failure" in r.message for r in caplog.records)
