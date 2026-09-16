"""Phase 0 instrumentation package — schemas + writer.

Per `docs/research-log/21_phase0_instrumentation_mvp.md`. The four Pydantic
schemas and the unified InstrumentationWriter that lands them as
Parquet files atomically.

Production wire-in (alpaca_executor + bridge hook sites) ships in a
follow-up commit — this package is the load-bearing foundation.
"""
from src.analysis.instrumentation.schemas import (
    BarContextRow,
    ChildFillRow,
    CohortRow,
    TradeContextRow,
    SCHEMA_VERSION,
)
from src.analysis.instrumentation.writer import (
    InstrumentationWriter,
    SchemaValidationError,
)
from src.analysis.instrumentation.migration import (
    MIGRATIONS,
    migrate_row,
    read_with_migration,
    register_migration,
)

__all__ = [
    "BarContextRow",
    "ChildFillRow",
    "CohortRow",
    "TradeContextRow",
    "SCHEMA_VERSION",
    "InstrumentationWriter",
    "SchemaValidationError",
    "MIGRATIONS",
    "migrate_row",
    "read_with_migration",
    "register_migration",
]
