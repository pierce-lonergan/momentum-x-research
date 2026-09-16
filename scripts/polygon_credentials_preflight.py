"""One-shot preflight: verify Polygon REST API key + S3 flat-file keys
+ all new endpoint methods are reachable.

USAGE:
    python scripts/polygon_credentials_preflight.py

OUTPUT: clear PASS/FAIL per capability.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "mx-arena"))


def section(name):
    print(f"\n{'='*60}\n{name}\n{'='*60}")


def check_env() -> dict:
    return {
        "POLYGON_API_KEY": bool(os.environ.get("POLYGON_API_KEY", "").strip()),
        "POLYGON_S3_KEY": bool(os.environ.get("POLYGON_S3_KEY", "").strip()),
        "POLYGON_S3_SECRET": bool(os.environ.get("POLYGON_S3_SECRET", "").strip()),
    }


async def check_rest():
    """Verify each endpoint method works against live API."""
    from data_providers.polygon import PolygonClient, PolygonEndpoints
    try:
        client = PolygonClient.from_env()
    except Exception as e:
        print(f"  PolygonClient init: FAIL ({e})")
        return False
    async with client:
        ep = PolygonEndpoints(client)
        checks = [
            ("aggregates(AAPL day)",
                lambda: ep.aggregates("AAPL", 1, "day", "2024-01-02", "2024-01-03")),
            ("ticker_details(AAPL)",
                lambda: ep.ticker_details("AAPL")),
            ("gainers()",                lambda: ep.gainers()),
            ("losers()",                 lambda: ep.losers()),
            ("ticker_types()",           lambda: ep.ticker_types()),
            ("conditions() [first 5]",   lambda: ep.conditions()),
            ("exchanges()",              lambda: ep.exchanges()),
            ("market_status_now()",      lambda: ep.market_status_now()),
            ("market_status_upcoming()", lambda: ep.market_status_upcoming()),
            ("dividends(AAPL)",          lambda: ep.dividends(ticker="AAPL")),
            ("splits(AAPL)",             lambda: ep.splits(ticker="AAPL")),
            ("news(ticker=AAPL, n=5)",   lambda: ep.news(ticker="AAPL", limit=5)),
            ("grouped_daily(2024-01-02)",
                lambda: ep.grouped_daily("2024-01-02")),
        ]
        all_pass = True
        for name, fn in checks:
            try:
                result = await fn()
                n = len(result) if hasattr(result, "__len__") else "OK"
                print(f"  {name:42s}  PASS  ({n} item(s))")
            except Exception as e:
                print(f"  {name:42s}  FAIL  ({e})")
                all_pass = False
        return all_pass


def check_s3():
    """Verify S3 credentials + bucket access."""
    try:
        import boto3, botocore
    except ImportError:
        print("  boto3 not installed: FAIL")
        return False
    key = os.environ.get("POLYGON_S3_KEY", "").strip()
    secret = os.environ.get("POLYGON_S3_SECRET", "").strip()
    if not key or not secret:
        print("  POLYGON_S3_KEY / POLYGON_S3_SECRET not set: FAIL")
        return False
    try:
        endpoint = os.environ.get("POLYGON_S3_ENDPOINT", "https://files.massive.com")
        bucket = os.environ.get("POLYGON_S3_BUCKET", "flatfiles")
        s3 = boto3.session.Session(
            aws_access_key_id=key, aws_secret_access_key=secret,
        ).client(
            "s3", endpoint_url=endpoint,
            config=botocore.client.Config(signature_version="s3v4"),
        )
        # head a known recent key
        from datetime import date, timedelta
        for days_ago in range(1, 7):
            d = date.today() - timedelta(days=days_ago)
            if d.weekday() >= 5: continue
            key_path = f"us_stocks_sip/day_aggs_v1/{d.year}/{d.month:02d}/{d.isoformat()}.csv.gz"
            try:
                resp = s3.head_object(Bucket=bucket, Key=key_path)
                sz = resp["ContentLength"]
                print(f"  HEAD {key_path}: PASS ({sz/1024:.1f} KB)")
                print(f"  endpoint = {endpoint}, bucket = {bucket}")
                return True
            except botocore.exceptions.ClientError as e:
                code = e.response.get("Error", {}).get("Code", "?")
                if code == "NoSuchKey":
                    continue
                print(f"  S3 access: FAIL ({code})")
                return False
        print("  No recent day_aggs_v1 file found in last 7 days: WARN")
        return False
    except Exception as e:
        print(f"  S3 client error: FAIL ({e})")
        return False


def check_imports():
    """Verify all new modules import cleanly."""
    targets = [
        "data_providers.polygon",
        "data_providers.polygon.endpoints",
    ]
    all_ok = True
    for t in targets:
        try:
            __import__(t)
            print(f"  import {t}: PASS")
        except Exception as e:
            print(f"  import {t}: FAIL ({e})")
            all_ok = False
    return all_ok


def main():
    section("ENV CHECK")
    env = check_env()
    for k, v in env.items():
        print(f"  {k:25s}  {'PRESENT' if v else 'MISSING'}")

    section("MODULE IMPORTS")
    imports_ok = check_imports()

    section("REST ENDPOINTS")
    if not env["POLYGON_API_KEY"]:
        print("  SKIP — POLYGON_API_KEY not set")
        rest_ok = False
    else:
        rest_ok = asyncio.run(check_rest())

    section("S3 FLAT FILES")
    if not (env["POLYGON_S3_KEY"] and env["POLYGON_S3_SECRET"]):
        print("  SKIP — POLYGON_S3_KEY / POLYGON_S3_SECRET not set")
        s3_ok = False
    else:
        s3_ok = check_s3()

    section("SUMMARY")
    print(f"  imports        {'OK' if imports_ok else 'FAIL'}")
    print(f"  REST API       {'OK' if rest_ok else 'NOT OK'}")
    print(f"  S3 flat files  {'OK' if s3_ok else 'NOT OK'}")
    if not env["POLYGON_API_KEY"] or not (env["POLYGON_S3_KEY"] and env["POLYGON_S3_SECRET"]):
        print("\n  ACTION REQUIRED:")
        if not env["POLYGON_API_KEY"]:
            print("    - Add POLYGON_API_KEY to ~/momentum-x-secrets.env")
            print("      (Get from https://polygon.io/dashboard ->API Keys)")
        if not env["POLYGON_S3_KEY"] or not env["POLYGON_S3_SECRET"]:
            print("    - Add POLYGON_S3_KEY + POLYGON_S3_SECRET to ~/momentum-x-secrets.env")
            print("      (Get from https://polygon.io/dashboard ->Flat Files ->Access Keys)")
            print("      THESE ARE SEPARATE FROM POLYGON_API_KEY")
        return 1
    return 0 if (imports_ok and rest_ok and s3_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
