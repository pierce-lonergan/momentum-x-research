"""Build news catalyst features per (ticker, d0) using Finnhub.

Per Compass artifact 2 §A.3, news features are leading indicators for
microcap gap continuation. For each (ticker, d0) in aftermath_strat, computes:

  n_articles_24h        - count of articles in (d0 - 24h, d0 09:30 ET)
  n_unique_publishers   - distinct publishers (1 source x 50 syndications != 50 catalysts)
  pct_positive          - fraction with sentiment > 0
  pct_negative          - fraction with sentiment < 0
  weighted_sentiment    - weighted avg of sentiment (publisher-tier weights)
  hours_to_first_article - time gap from market close to first article
  co_mention_count      - avg # other tickers mentioned in same articles
  has_ratings_change    - boolean: any analyst ratings change in window
  has_official_filing    - boolean: any 8-K / earnings / press release

INPUT credentials:
  FINNHUB_API_KEY (free tier: 60 calls/min, 30 days history)

OUTPUT:
  data/polygon_warehouse/derived/news_features.parquet

USAGE:
  # Backfill 30 days (Finnhub free tier limit)
  python scripts/build_news_catalyst_features.py

  # For longer history, add Polygon /v3/reference/news support
  # (see scripts/polygon_news_catalyst.py)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import duckdb
import httpx
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
ENV_PATH = REPO / ".env"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_env_file(path: Path) -> dict:
    """Parse .env file (KEY=VALUE per line, ignore comments)."""
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "=" not in line: continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.split("#", 1)[0].strip()
    return env


# Publisher-tier weights (Compass artifact 2 §A.3 — primary > secondary > opinion)
PUBLISHER_WEIGHTS = {
    "businesswire": 1.0, "pr newswire": 1.0, "globenewswire": 1.0,
    "reuters": 0.95, "bloomberg": 0.95, "ap": 0.9,
    "benzinga": 0.7, "marketwatch": 0.6, "cnbc": 0.6,
    "barrons": 0.6, "wsj": 0.7, "ft": 0.7,
    "seekingalpha": 0.3, "motleyfool": 0.2, "zacks": 0.4,
    "yahoo": 0.4, "investorplace": 0.3,
}


def publisher_weight(name: str) -> float:
    if not name: return 0.5
    n = name.lower()
    for key, w in PUBLISHER_WEIGHTS.items():
        if key in n:
            return w
    return 0.5  # unknown publisher


def fetch_finnhub_news(client: httpx.Client, ticker: str, from_d: str,
                        to_d: str, api_key: str) -> list[dict]:
    """Fetch company news from Finnhub. Returns [] on failure."""
    try:
        r = client.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": from_d, "to": to_d, "token": api_key},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def aggregate_news_for_window(articles: list[dict], window_end_utc: float) -> dict:
    """Compute per-(ticker, d0) features from raw article list.

    window_end_utc: unix timestamp of d0 09:30 ET (market open).
    Considers articles in (window_end - 24h, window_end].
    """
    window_start = window_end_utc - 86400  # 24h
    relevant = [a for a in articles
                  if a.get("datetime", 0) > window_start
                     and a.get("datetime", 0) <= window_end_utc]
    if not relevant:
        return {
            "n_articles_24h": 0, "n_unique_publishers": 0,
            "pct_positive": 0.0, "pct_negative": 0.0,
            "weighted_sentiment": 0.0,
            "hours_to_first_article": -1.0, "co_mention_count": 0.0,
            "has_ratings_change": 0, "has_official_filing": 0,
        }

    publishers = set()
    sentiments: list[float] = []
    weights: list[float] = []
    rating_change = False
    official = False
    earliest_ts = min(a.get("datetime", window_end_utc) for a in relevant)

    for a in relevant:
        pub = (a.get("source") or "").strip()
        publishers.add(pub.lower())
        # Finnhub doesn't provide per-article sentiment — use category as proxy:
        # "company news" = neutral; check headline for keywords
        headline = (a.get("headline") or "").lower()
        sent = 0.0
        if any(w in headline for w in ("upgrade", "raises", "beats", "approves",
                                         "wins", "secures", "launches")):
            sent = 0.7
        elif any(w in headline for w in ("downgrade", "lowers", "misses",
                                           "investigation", "lawsuit", "delisting",
                                           "bankruptcy", "halts")):
            sent = -0.7
        sentiments.append(sent)
        weights.append(publisher_weight(pub))

        if any(w in headline for w in ("upgrade", "downgrade", "rating", "price target")):
            rating_change = True
        if any(w in headline for w in ("8-k", "8k", "earnings", "press release",
                                          "files", "announces", "fda")):
            official = True

    # weighted sentiment
    total_w = sum(weights) or 1.0
    weighted_sent = sum(s * w for s, w in zip(sentiments, weights)) / total_w
    # convert to per-article percentages
    n = len(relevant)
    pct_pos = sum(1 for s in sentiments if s > 0) / n
    pct_neg = sum(1 for s in sentiments if s < 0) / n
    hours_first = (window_end_utc - earliest_ts) / 3600.0

    return {
        "n_articles_24h": int(n),
        "n_unique_publishers": int(len(publishers)),
        "pct_positive": float(pct_pos),
        "pct_negative": float(pct_neg),
        "weighted_sentiment": float(weighted_sent),
        "hours_to_first_article": float(hours_first),
        "co_mention_count": 0.0,  # Finnhub doesn't expose; would need Polygon
        "has_ratings_change": int(rating_change),
        "has_official_filing": int(official),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=30,
                        help="Backfill window (Finnhub free tier: ~30 days max)")
    parser.add_argument("--limit-tickers", type=int, default=0,
                        help="If >0, sample only this many keys for fast smoke test")
    parser.add_argument("--rate-sleep", type=float, default=1.05,
                        help="Seconds between API calls (Finnhub free: 60/min)")
    args = parser.parse_args()

    section("STEP 1 - Load credentials + keys")
    env = load_env_file(ENV_PATH)
    api_key = env.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        print("  ERROR: FINNHUB_API_KEY not set in .env or shell env")
        return 1
    print(f"  Finnhub key loaded ({len(api_key)} chars)")

    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    cutoff = (datetime.utcnow() - timedelta(days=args.days_back)).strftime("%Y-%m-%d")
    print(f"  Aftermath cutoff (days_back={args.days_back}): >= {cutoff}")
    con = duckdb.connect()
    con.sql("SET memory_limit='4GB'")
    limit = f"LIMIT {args.limit_tickers}" if args.limit_tickers > 0 else ""
    keys_df = con.sql(f"""
        SELECT ticker, d0
        FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
          AND d0 >= DATE '{cutoff}'
        ORDER BY d0
        {limit}
    """).df()
    keys_df["d0"] = pd.to_datetime(keys_df["d0"])
    print(f"  {len(keys_df):,} (ticker, d0) keys to enrich")

    if len(keys_df) == 0:
        print("  Nothing to fetch (check days_back range)")
        return 0

    section("STEP 2 - Fetch + aggregate per-(ticker, d0)")
    rows: list[dict] = []
    client = httpx.Client(timeout=10.0)
    last_ticker = None
    cached_articles: list = []
    cache_window: tuple[str, str] = ("", "")
    n_api_calls = 0
    t0 = time.time()
    for i, row in enumerate(keys_df.itertuples()):
        ticker = row.ticker
        d0 = pd.Timestamp(row.d0)
        from_d = (d0 - timedelta(days=2)).strftime("%Y-%m-%d")
        to_d = d0.strftime("%Y-%m-%d")

        if ticker != last_ticker or (from_d, to_d) != cache_window:
            cached_articles = fetch_finnhub_news(client, ticker, from_d, to_d, api_key)
            last_ticker = ticker
            cache_window = (from_d, to_d)
            n_api_calls += 1
            time.sleep(args.rate_sleep)
            if i % 50 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (len(keys_df) - i - 1) / rate if rate > 0 else 0
                print(f"  [{i+1}/{len(keys_df)}] {ticker} {to_d}  "
                      f"api_calls={n_api_calls}  rate={rate:.1f}/s  eta={eta:.0f}s")

        # Window end = d0 09:30 ET = 13:30 UTC = unix timestamp
        d0_utc = d0.replace(tzinfo=None) + timedelta(hours=13.5)  # 09:30 ET = 13:30 UTC (EDT)
        window_end = d0_utc.timestamp()
        agg = aggregate_news_for_window(cached_articles, window_end)
        agg["ticker"] = ticker
        agg["d0"] = d0
        rows.append(agg)

    client.close()
    print(f"\n  done: {n_api_calls} API calls, {len(rows)} (ticker, d0) features in "
          f"{time.time()-t0:.1f}s")

    section("STEP 3 - Persist")
    out = pd.DataFrame(rows)
    out_path = DERIVED / "news_features.parquet"
    out.to_parquet(out_path, compression="zstd")
    print(f"  Wrote {out_path} ({len(out):,} rows, {out_path.stat().st_size/1024:.1f} KB)")

    section("Sample stats")
    print(out[["n_articles_24h", "n_unique_publishers", "weighted_sentiment",
                 "has_official_filing"]].describe().to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
