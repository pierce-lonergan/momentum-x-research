"""Generate the high-mover catalog from the Polygon Parquet warehouse.

Per doc 90 §2: 84% of watchlist days had a >=30% mover. With the new
warehouse we can compute this across the FULL US universe (not just our
30-ticker watchlist).

USAGE:
    # All-time, default thresholds
    python scripts/polygon_high_mover_catalog.py
    # Custom threshold + date range
    python scripts/polygon_high_mover_catalog.py --threshold 0.50 --start 2024-01-01

OUTPUT:
    data/polygon_warehouse/derived/high_movers_catalog.parquet
        columns: date, ticker, open, close, intraday_pct, volume,
                 volume_dollar, transactions, source ('day' or 'minute')
    Plus per-day summary at ..._summary.json
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"
OUT_DIR = WAREHOUSE / "derived"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*70}\n{t}\n{'='*70}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.30,
                         help="Min intraday pct move (default 0.30 = 30%)")
    parser.add_argument("--price-min", type=float, default=0.50)
    parser.add_argument("--price-max", type=float, default=50.0)
    parser.add_argument("--volume-min", type=int, default=100_000)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--source", choices=["day", "minute", "minute_intraday"],
                         default="day",
                         help="day=close-vs-open from day_aggs (cheap); "
                              "minute=high-vs-open from minute_aggs (true MFE); "
                              "minute_intraday=full intraday MFE/MAE per ticker")
    args = parser.parse_args()

    if args.source == "day":
        glob = str(WAREHOUSE / "day_aggs" / "**" / "*.parquet").replace("\\", "/")
        date_clause = ""
        if args.start: date_clause += f" AND CAST(ts_et AS DATE) >= '{args.start}'"
        if args.end:   date_clause += f" AND CAST(ts_et AS DATE) <= '{args.end}'"
        query = f"""
            SELECT CAST(ts_et AS DATE) AS date, ticker,
                   open, high, low, close, volume,
                   ROUND((close - open) / open, 4) AS intraday_pct,
                   transactions,
                   'day' AS source
            FROM read_parquet('{glob}', hive_partitioning=true)
            WHERE open BETWEEN {args.price_min} AND {args.price_max}
              AND volume >= {args.volume_min}
              AND open > 0
              AND (close - open) / open >= {args.threshold}
              {date_clause}
            ORDER BY date DESC, intraday_pct DESC
        """
    elif args.source == "minute":
        glob = str(WAREHOUSE / "minute_aggs" / "**" / "*.parquet").replace("\\", "/")
        date_clause = ""
        if args.start: date_clause += f" AND CAST(ts_et AS DATE) >= '{args.start}'"
        if args.end:   date_clause += f" AND CAST(ts_et AS DATE) <= '{args.end}'"
        query = f"""
            WITH per_day AS (
                SELECT CAST(ts_et AS DATE) AS date, ticker,
                       FIRST(open ORDER BY ts_et) AS open,
                       MAX(high) AS high,
                       MIN(low) AS low,
                       LAST(close ORDER BY ts_et) AS close,
                       SUM(volume) AS volume,
                       SUM(transactions) AS transactions
                FROM read_parquet('{glob}', hive_partitioning=true)
                WHERE 1=1 {date_clause}
                GROUP BY 1, 2
            )
            SELECT date, ticker, open, high, low, close, volume,
                   ROUND((high - open) / open, 4) AS intraday_pct,
                   transactions,
                   'minute' AS source
            FROM per_day
            WHERE open BETWEEN {args.price_min} AND {args.price_max}
              AND volume >= {args.volume_min}
              AND open > 0
              AND (high - open) / open >= {args.threshold}
            ORDER BY date DESC, intraday_pct DESC
        """
    else:
        # minute_intraday: per-ticker MFE + MAE + close return all in one
        glob = str(WAREHOUSE / "minute_aggs" / "**" / "*.parquet").replace("\\", "/")
        date_clause = ""
        if args.start: date_clause += f" AND CAST(ts_et AS DATE) >= '{args.start}'"
        if args.end:   date_clause += f" AND CAST(ts_et AS DATE) <= '{args.end}'"
        query = f"""
            WITH per_day AS (
                SELECT CAST(ts_et AS DATE) AS date, ticker,
                       FIRST(open ORDER BY ts_et) AS open,
                       MAX(high) AS high,
                       MIN(low) AS low,
                       LAST(close ORDER BY ts_et) AS close,
                       SUM(volume) AS volume,
                       SUM(transactions) AS transactions
                FROM read_parquet('{glob}', hive_partitioning=true)
                WHERE 1=1 {date_clause}
                GROUP BY 1, 2
            )
            SELECT date, ticker, open, high, low, close, volume,
                   ROUND((high - open) / open, 4) AS mfe_pct,
                   ROUND((low - open) / open, 4) AS mae_pct,
                   ROUND((close - open) / open, 4) AS close_pct,
                   transactions,
                   'minute_intraday' AS source
            FROM per_day
            WHERE open BETWEEN {args.price_min} AND {args.price_max}
              AND volume >= {args.volume_min}
              AND open > 0
              AND (high - open) / open >= {args.threshold}
            ORDER BY date DESC, mfe_pct DESC
        """

    section(f"BUILDING HIGH-MOVER CATALOG (source={args.source})")
    print(f"  Threshold: >={args.threshold*100:.0f}% intraday move")
    print(f"  Price band: ${args.price_min} - ${args.price_max}")
    print(f"  Min volume: {args.volume_min:,}")
    if args.start: print(f"  Start: {args.start}")
    if args.end: print(f"  End: {args.end}")

    con = duckdb.connect()
    t0 = time.perf_counter()
    df = con.sql(query).fetchdf()
    elapsed = time.perf_counter() - t0
    print(f"\n  Query elapsed: {elapsed:.1f}s")
    print(f"  Catalog rows: {len(df):,}")

    # Persist
    out_path = OUT_DIR / "high_movers_catalog.parquet"
    df.to_parquet(out_path, compression="zstd")
    print(f"  Wrote {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")

    # Summary
    section("CATALOG SUMMARY")
    if len(df) == 0:
        print("  (empty)")
        return 0
    pct_col = "mfe_pct" if "mfe_pct" in df.columns else "intraday_pct"
    print(f"  date range: {df['date'].min()} -> {df['date'].max()}")
    print(f"  unique tickers: {df['ticker'].nunique():,}")
    print(f"  unique dates: {df['date'].nunique():,}")
    print(f"  avg movers per day: {len(df) / df['date'].nunique():.1f}")
    print(f"  pct distribution: median={df[pct_col].median():+.2%}  p75={df[pct_col].quantile(0.75):+.2%}  p95={df[pct_col].quantile(0.95):+.2%}  max={df[pct_col].max():+.2%}")

    summary = {
        "source": args.source,
        "threshold": args.threshold,
        "rows": int(len(df)),
        "unique_tickers": int(df['ticker'].nunique()),
        "unique_dates": int(df['date'].nunique()),
        "avg_movers_per_day": float(len(df) / df['date'].nunique()),
        "pct_median": float(df[pct_col].median()),
        "pct_p75": float(df[pct_col].quantile(0.75)),
        "pct_p95": float(df[pct_col].quantile(0.95)),
        "pct_max": float(df[pct_col].max()),
        "first_date": str(df['date'].min()),
        "last_date": str(df['date'].max()),
    }
    (OUT_DIR / "high_movers_catalog_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {OUT_DIR / 'high_movers_catalog_summary.json'}")

    # Top 20 ticker by appearances
    section("TOP 20 TICKERS BY APPEARANCE COUNT")
    top = df.groupby("ticker").agg(
        n_appearances=("date", "count"),
        avg_pct=(pct_col, "mean"),
        max_pct=(pct_col, "max"),
        last_date=("date", "max"),
    ).sort_values("n_appearances", ascending=False).head(20)
    print(top.to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
