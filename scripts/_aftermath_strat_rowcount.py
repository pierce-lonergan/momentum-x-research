"""Tiny utility: print row count of aftermath_strat.parquet.

Used by daily_data_ingest.ps1 to validate that pipeline rebuilds don't
regress the row count. Avoids PowerShell-Python quoting hell with
COUNT(*).

Usage:
    python scripts/_aftermath_strat_rowcount.py
    # prints integer count to stdout, or 0 if file missing
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "data" / "polygon_warehouse" / "derived" / "aftermath_strat.parquet"


def main() -> int:
    if not PATH.exists():
        print(0)
        return 0
    try:
        import duckdb
        con = duckdb.connect()
        n = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{PATH.as_posix()}')"
        ).fetchone()[0]
        print(int(n))
    except Exception as e:
        # Print 0 + log to stderr so caller can detect failure
        print(0)
        print(f"row count failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
