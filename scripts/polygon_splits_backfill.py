"""Splits + dividends REST backfill + reverse-split fraud filter.

Pulls /v3/reference/splits and /v3/reference/dividends for every
ticker in the high-mover catalog (3,501 unique). Cross-references against
the catalog to produce a CLEAN reverse-split fraud flag (replaces the
heuristic flag from polygon_aftermath_catalog.py).

Outputs:
  data/polygon_warehouse/reference/splits.parquet
  data/polygon_warehouse/reference/dividends.parquet
  data/polygon_warehouse/derived/aftermath_catalog_clean.parquet
       (re-flagged with rigorous CA contamination)
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "mx-arena"))

from data_providers.polygon import PolygonClient, PolygonEndpoints  # noqa: E402

REF_DIR = REPO / "data" / "polygon_warehouse" / "reference"
REF_DIR.mkdir(parents=True, exist_ok=True)
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


async def main_async():
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    # Get all tickers in the catalog
    section("STEP 1 - Pull catalog tickers")
    tickers = con.sql("""
        SELECT DISTINCT ticker FROM read_parquet('data/polygon_warehouse/derived/high_movers_catalog.parquet')
    """).fetchall()
    tickers = [r[0] for r in tickers]
    print(f"  {len(tickers):,} unique tickers")

    section("STEP 2 - REST pull splits + dividends per ticker")
    client = PolygonClient.from_env()
    splits_rows = []
    divs_rows = []
    async with client:
        ep = PolygonEndpoints(client)
        t0 = time.time()
        for i, t in enumerate(tickers):
            try:
                splits = await ep.splits(ticker=t, limit=20)
                for s in splits:
                    splits_rows.append({"ticker": t, **s})
                divs = await ep.dividends(ticker=t, limit=20)
                for d in divs:
                    divs_rows.append({"ticker": t, **d})
            except Exception as e:
                print(f"WARN: splits/dividends fetch failed for {t}: {e}")
            if (i+1) % 100 == 0:
                rate = (i+1) / (time.time() - t0)
                eta_s = (len(tickers) - i - 1) / rate
                print(f"  Progress: {i+1}/{len(tickers)} ({100.0*(i+1)/len(tickers):.0f}%) "
                      f"rate={rate:.1f}/s eta={eta_s:.0f}s splits={len(splits_rows)} divs={len(divs_rows)}")
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.0f}s. {len(splits_rows)} splits, {len(divs_rows)} dividends.")

    # Write reference tables
    section("STEP 3 - Persist reference tables")
    if splits_rows:
        df = pd.DataFrame(splits_rows)
        # Normalize types
        for col in ("execution_date",):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        out = REF_DIR / "splits.parquet"
        df.to_parquet(out, compression="zstd")
        print(f"  Wrote {out} ({len(df)} rows, {out.stat().st_size/1e3:.1f} KB)")
    if divs_rows:
        df = pd.DataFrame(divs_rows)
        for col in ("ex_dividend_date", "declaration_date", "record_date", "pay_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        out = REF_DIR / "dividends.parquet"
        df.to_parquet(out, compression="zstd")
        print(f"  Wrote {out} ({len(df)} rows, {out.stat().st_size/1e3:.1f} KB)")

    section("STEP 4 - Apply rigorous CA contamination flag to aftermath catalog")
    splits_path = (REF_DIR / "splits.parquet").as_posix()
    aftermath = "data/polygon_warehouse/derived/aftermath_catalog.parquet"
    con.sql(f"""
        CREATE OR REPLACE TABLE clean AS
        WITH s AS (
            SELECT ticker, execution_date,
                   CAST(split_from AS DOUBLE) AS sf,
                   CAST(split_to AS DOUBLE) AS st,
                   CAST(split_from AS DOUBLE) / CAST(split_to AS DOUBLE) AS ratio
            FROM read_parquet('{splits_path}')
            WHERE split_from IS NOT NULL AND split_to IS NOT NULL
              AND CAST(split_to AS DOUBLE) > 0
        ),
        flagged AS (
            SELECT a.*,
                   CASE WHEN s.execution_date IS NOT NULL AND s.ratio > 1
                        THEN 'reverse_split_within_window'
                        ELSE NULL END AS reverse_split_flag,
                   s.execution_date AS reverse_split_date,
                   s.ratio AS reverse_split_ratio
            FROM read_parquet('{aftermath}') a
            LEFT JOIN s ON a.ticker = s.ticker
                       AND s.execution_date BETWEEN a.d0 - INTERVAL 90 DAY
                                            AND a.d0 + INTERVAL 5 DAY
                       AND s.ratio > 1
        )
        SELECT * FROM flagged
    """)
    n_total = con.sql("SELECT COUNT(*) FROM clean").fetchone()[0]
    n_flagged = con.sql("SELECT COUNT(*) FROM clean WHERE reverse_split_flag IS NOT NULL").fetchone()[0]
    print(f"  Total rows: {n_total:,}")
    print(f"  Flagged as reverse_split_within_window: {n_flagged:,} ({100.0*n_flagged/n_total:.2f}%)")

    # Distribution of flagged vs catalog returns
    rows = con.sql("""
        SELECT
          CASE WHEN reverse_split_flag IS NULL THEN 'clean (no reverse split nearby)'
               ELSE 'reverse_split_window' END AS bucket,
          COUNT(*) AS n,
          ROUND(AVG(intraday_pct)*100, 1) AS avg_intra,
          ROUND(AVG(ret_t5)*100, 1) AS avg_t5,
          ROUND(MAX(intraday_pct)*100, 1) AS max_intra
        FROM clean
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"\n  By flag:")
    print(f"    {'bucket':<35} {'n':>8} {'avg_intra':>10} {'avg_t5':>9} {'max_intra':>10}")
    for r in rows:
        print(f"    {r[0]:<35} {r[1]:>8,} {r[2]:>+9.1f}% {r[3]:>+8.1f}% {r[4]:>+9.1f}%")

    out_path = DERIVED / "aftermath_catalog_clean.parquet"
    con.sql(f"COPY (SELECT * FROM clean) TO '{out_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"\n  Wrote {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
