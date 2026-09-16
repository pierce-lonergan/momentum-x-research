"""Ticker details backfill v2 — uses raw API response (bypasses TickerRef).

The original `polygon_ticker_details_backfill.py` failed because TickerRef
dataclass only has 13 fields but the script called 11 missing attributes
(silent AttributeError caught by `except Exception: pass`).

This v2 calls the raw HTTP endpoint and extracts fields from the dict
directly. No TickerRef parsing.

Outputs: data/polygon_warehouse/reference/ticker_details.parquet
"""
from __future__ import annotations
import asyncio, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "mx-arena"))

from data_providers.polygon import PolygonClient  # noqa: E402

REF_DIR = REPO / "data" / "polygon_warehouse" / "reference"
REF_DIR.mkdir(parents=True, exist_ok=True)


async def main_async():
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    tickers = con.sql("""
        SELECT DISTINCT ticker FROM read_parquet('data/polygon_warehouse/derived/high_movers_catalog.parquet')
    """).fetchall()
    tickers = [r[0] for r in tickers]
    print(f"Pulling {len(tickers):,} tickers ...")

    client = PolygonClient.from_env()
    rows = []
    n_errors = 0
    async with client:
        t0 = time.time()
        for i, t in enumerate(tickers):
            try:
                resp = await client.get(f"/v3/reference/tickers/{t}")
                results = resp.data.get("results")
                if isinstance(results, list):
                    results = results[0] if results else None
                if not results:
                    continue
                # Extract every field directly from the JSON dict
                addr = results.get("address") or {}
                rows.append({
                    "ticker": t,
                    "name": results.get("name"),
                    "type": results.get("type"),
                    "market": results.get("market"),
                    "locale": results.get("locale"),
                    "primary_exchange": results.get("primary_exchange"),
                    "active": results.get("active"),
                    "currency_name": results.get("currency_name"),
                    "cik": results.get("cik"),
                    "composite_figi": results.get("composite_figi"),
                    "share_class_figi": results.get("share_class_figi"),
                    "market_cap": results.get("market_cap"),
                    "phone_number": results.get("phone_number"),
                    "address_city": addr.get("city"),
                    "address_state": addr.get("state"),
                    "description": (results.get("description") or "")[:500],
                    "sic_code": results.get("sic_code"),
                    "sic_description": results.get("sic_description"),
                    "ticker_root": results.get("ticker_root"),
                    "homepage_url": results.get("homepage_url"),
                    "total_employees": results.get("total_employees"),
                    "list_date": results.get("list_date"),
                    "share_class_shares_outstanding": results.get("share_class_shares_outstanding"),
                    "weighted_shares_outstanding": results.get("weighted_shares_outstanding"),
                    "round_lot": results.get("round_lot"),
                })
            except Exception as e:
                n_errors += 1
            if (i+1) % 200 == 0:
                rate = (i+1) / (time.time() - t0)
                eta = (len(tickers) - i - 1) / rate
                print(f"  {i+1}/{len(tickers)} ({100*(i+1)/len(tickers):.0f}%) "
                      f"rate={rate:.1f}/s eta={eta:.0f}s rows={len(rows)} errors={n_errors}")
    print(f"\nDone in {time.time()-t0:.0f}s. {len(rows)} rows, {n_errors} errors.")

    df = pd.DataFrame(rows)
    if "list_date" in df.columns:
        df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
    out = REF_DIR / "ticker_details.parquet"
    df.to_parquet(out, compression="zstd")
    print(f"Wrote {out} ({len(df)} rows, {out.stat().st_size/1e3:.1f} KB)")

    # Summary
    print(f"\n  with market_cap: {df['market_cap'].notna().sum():,}")
    print(f"  with sic_code:   {df['sic_code'].notna().sum():,}")
    print(f"  with list_date:  {df['list_date'].notna().sum():,}")
    print(f"  with employees:  {df['total_employees'].notna().sum():,}")
    print(f"\n  Top types:")
    print(df["type"].value_counts().head(10).to_string())
    print(f"\n  Top SIC descriptions:")
    print(df["sic_description"].value_counts().head(10).to_string())


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
