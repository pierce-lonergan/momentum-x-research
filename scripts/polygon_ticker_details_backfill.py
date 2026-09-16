"""Ticker details backfill (sector, market_cap, SIC code) for catalog tickers.

Per doc 102 §Q5: sector dummies could capture systematic continuer-rate
differences across sectors (biotech vs SPAC vs IPO vs penny tech).

Pulls /v3/reference/tickers/{T} for every ticker in the catalog (3,501).
Same pattern as polygon_splits_backfill.py.

Outputs:
  data/polygon_warehouse/reference/ticker_details.parquet
"""
from __future__ import annotations
import asyncio
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "mx-arena"))

from data_providers.polygon import PolygonClient, PolygonEndpoints  # noqa: E402

REF_DIR = REPO / "data" / "polygon_warehouse" / "reference"
REF_DIR.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


async def main_async():
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    section("STEP 1 - Pull catalog tickers")
    tickers = con.sql("""
        SELECT DISTINCT ticker FROM read_parquet('data/polygon_warehouse/derived/high_movers_catalog.parquet')
    """).fetchall()
    tickers = [r[0] for r in tickers]
    print(f"  {len(tickers):,} unique tickers")

    section("STEP 2 - REST pull /v3/reference/tickers/{T}")
    client = PolygonClient.from_env()
    rows = []
    async with client:
        ep = PolygonEndpoints(client)
        t0 = time.time()
        for i, t in enumerate(tickers):
            try:
                ref = await ep.ticker_details(t)
                if ref:
                    rows.append({
                        "ticker": t,
                        "name": ref.name,
                        "type": ref.type,
                        "market": ref.market,
                        "primary_exchange": ref.primary_exchange,
                        "currency_name": ref.currency_name,
                        "cik": ref.cik,
                        "composite_figi": ref.composite_figi,
                        "share_class_figi": ref.share_class_figi,
                        "market_cap": ref.market_cap,
                        "phone_number": ref.phone_number,
                        "address_city": (ref.address or {}).get("city") if ref.address else None,
                        "address_state": (ref.address or {}).get("state") if ref.address else None,
                        "description": ref.description,
                        "sic_code": ref.sic_code,
                        "sic_description": ref.sic_description,
                        "ticker_root": ref.ticker_root,
                        "homepage_url": ref.homepage_url,
                        "total_employees": ref.total_employees,
                        "list_date": ref.list_date,
                        "share_class_shares_outstanding": ref.share_class_shares_outstanding,
                        "weighted_shares_outstanding": ref.weighted_shares_outstanding,
                        "round_lot": ref.round_lot,
                    })
            except Exception as e:
                print(f"WARN: ticker_details fetch failed for {t}: {e}")
            if (i+1) % 100 == 0:
                rate = (i+1) / (time.time() - t0)
                eta_s = (len(tickers) - i - 1) / rate
                print(f"  Progress: {i+1}/{len(tickers)} ({100.0*(i+1)/len(tickers):.0f}%) "
                      f"rate={rate:.1f}/s eta={eta_s:.0f}s rows={len(rows)}")
    print(f"  Done in {time.time()-t0:.0f}s. {len(rows)} ticker_details rows.")

    section("STEP 3 - Persist")
    df = pd.DataFrame(rows)
    if "list_date" in df.columns:
        df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
    out = REF_DIR / "ticker_details.parquet"
    df.to_parquet(out, compression="zstd")
    print(f"  Wrote {out} ({len(df)} rows, {out.stat().st_size/1e3:.1f} KB)")

    # Summary stats
    section("STEP 4 - Summary")
    print(f"  Total tickers: {len(df)}")
    print(f"  With market_cap: {df['market_cap'].notna().sum()}")
    print(f"  With sic_code: {df['sic_code'].notna().sum()}")
    print(f"  With list_date: {df['list_date'].notna().sum()}")
    print(f"\n  Top types:")
    print(df["type"].value_counts().head(10).to_string())
    print(f"\n  Top SIC descriptions:")
    print(df["sic_description"].value_counts().head(10).to_string())
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
