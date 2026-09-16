"""Build per-(ticker, d0) news features from Polygon /v3/reference/news.

This is the v4-ready successor to scripts/build_news_catalyst_features.py
(Finnhub-based). Polygon news has BROADER microcap coverage than Finnhub
(Polygon ingests Benzinga, BusinessWire, PR Newswire, GlobeNewswire,
SeekingAlpha, plus its own Polygon Insights LLM-generated sentiment).

Per Compass artifact 2 §A.3:
  Polygon Insights provides per-article 3-class sentiment (positive/
  neutral/negative) + sentiment_reasoning text. Treat the label as a
  noisy prior; the reasoning text is more useful as a feature.

For each (ticker, d0) in aftermath_strat, computes (window = (d0 - 24h, d0 09:30 ET)):
  n_articles_24h           - raw article count
  n_unique_publishers      - distinct sources
  pct_positive             - fraction of Insights labeled positive
  pct_negative             - fraction labeled negative
  pct_neutral              - fraction labeled neutral
  weighted_sentiment       - sum over (publisher_tier_weight × insight_score)
  weighted_sentiment_norm  - same / total weight
  hours_to_first_article   - time gap from market close to first article
  co_mention_count_avg     - avg # other tickers tagged in articles
  has_official_filing      - boolean: any 8-K / earnings / press release
  has_ratings_change       - boolean: any analyst ratings change
  insights_coverage_pct    - fraction of articles with Insights field present

INPUT credentials: POLYGON_API_KEY (in .env)

OUTPUT:
  data/polygon_warehouse/derived/news_features_polygon.parquet
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

# Note: this client uses /v2/reference/news (the v3 endpoint returns 404
# on this account; the v2 endpoint is current as of the Polygon→Massive
# rebrand). Both api.polygon.io and api.massive.com work.
POLYGON_NEWS_URL = "https://api.polygon.io/v2/reference/news"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_env_file(path: Path) -> dict:
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.split("#", 1)[0].strip()
    return env


# Per Compass artifact 2 §A.3 publisher-tier weights
PUBLISHER_WEIGHTS = {
    # tier 1.0 — primary-source release wires
    "BusinessWire": 1.0,
    "PR Newswire": 1.0,
    "GlobeNewswire": 1.0,
    "Globe Newswire": 1.0,
    "ACCESSWIRE": 1.0,
    # tier 0.95 — major newswires
    "Reuters": 0.95,
    "Bloomberg": 0.95,
    "AP": 0.9,
    # tier 0.7 — paid financial press
    "Benzinga": 0.7,
    "WSJ": 0.7,
    "FT": 0.7,
    # tier 0.6 — financial mass-media
    "MarketWatch": 0.6,
    "CNBC": 0.6,
    "Barron's": 0.6,
    # tier 0.4 — opinion / aggregation
    "Zacks Investment Research": 0.4,
    "Yahoo": 0.4,
    "Investorplace": 0.3,
    # tier 0.2 — retail opinion
    "Seeking Alpha": 0.3,
    "Motley Fool": 0.2,
    "InvestorPlace": 0.2,
}


def publisher_weight(pub_name: Optional[str]) -> float:
    if not pub_name: return 0.5
    p = pub_name.strip()
    # Direct lookup
    if p in PUBLISHER_WEIGHTS:
        return PUBLISHER_WEIGHTS[p]
    # Substring match
    pl = p.lower()
    for key, w in PUBLISHER_WEIGHTS.items():
        if key.lower() in pl:
            return w
    return 0.5


def fetch_polygon_news(client: httpx.Client, ticker: str,
                        from_iso: str, to_iso: str,
                        api_key: str, limit: int = 50,
                        request_timeout: float = 8.0) -> list[dict]:
    """Fetch news articles for ticker in time window.

    Auth via Bearer header (apiKey query param returns 404 on this account).
    Tight timeout (default 8s) to avoid hung connections.
    """
    try:
        r = client.get(POLYGON_NEWS_URL, params={
            "ticker": ticker,
            "published_utc.gte": from_iso,
            "published_utc.lte": to_iso,
            "limit": limit,
            "order": "desc",
        }, headers={"Authorization": f"Bearer {api_key}"}, timeout=request_timeout)
        r.raise_for_status()
        data = r.json()
        return data.get("results", []) or []
    except Exception:
        return []


def aggregate_news(articles: list[dict], window_end_utc: datetime,
                    target_ticker: str) -> dict:
    """Compute per-(ticker, d0) features from raw article list."""
    window_start = window_end_utc - timedelta(hours=24)
    relevant = []
    for a in articles:
        try:
            published = datetime.fromisoformat(a["published_utc"].replace("Z", "+00:00"))
            if window_start <= published.replace(tzinfo=None) <= window_end_utc:
                relevant.append((a, published))
        except Exception as e:
            print(f"WARN: news article timestamp parse failed: {e}")
            continue

    if not relevant:
        return {
            "n_articles_24h": 0, "n_unique_publishers": 0,
            "pct_positive": 0.0, "pct_negative": 0.0, "pct_neutral": 0.0,
            "weighted_sentiment": 0.0, "weighted_sentiment_norm": 0.0,
            "hours_to_first_article": -1.0, "co_mention_count_avg": 0.0,
            "has_official_filing": 0, "has_ratings_change": 0,
            "insights_coverage_pct": 0.0,
        }

    publishers = set()
    weights: list[float] = []
    sentiments: list[float] = []  # numeric: pos=+1, neutral=0, neg=-1
    insight_present: list[int] = []
    co_mentions: list[int] = []
    rating_change = False
    official = False
    earliest = min(p for _, p in relevant)

    for a, _published in relevant:
        publisher = (a.get("publisher") or {}).get("name", "")
        publishers.add(publisher)
        w = publisher_weight(publisher)
        weights.append(w)

        # Insights field: list of {ticker, sentiment, sentiment_reasoning}
        insights = a.get("insights") or []
        target_insight = None
        for ins in insights:
            if (ins.get("ticker") or "").upper() == target_ticker.upper():
                target_insight = ins
                break
        if target_insight is not None:
            insight_present.append(1)
            label = (target_insight.get("sentiment") or "").lower()
            if label == "positive": sentiments.append(1.0)
            elif label == "negative": sentiments.append(-1.0)
            else: sentiments.append(0.0)
        else:
            insight_present.append(0)
            # Fallback: scan title for keywords
            title = (a.get("title") or "").lower()
            if any(w in title for w in ("approves", "upgrade", "raises", "beats",
                                           "wins", "secures", "launches")):
                sentiments.append(0.5)
            elif any(w in title for w in ("downgrade", "lowers", "misses",
                                            "investigation", "lawsuit",
                                            "halts", "delisting", "bankruptcy")):
                sentiments.append(-0.5)
            else:
                sentiments.append(0.0)

        # Co-mentions
        tickers = a.get("tickers") or []
        co_mentions.append(max(0, len(tickers) - 1))

        # Catalyst-type detection
        title_lc = (a.get("title") or "").lower()
        keywords_lc = " ".join(a.get("keywords") or []).lower()
        joined = title_lc + " " + keywords_lc
        if any(w in joined for w in ("upgrade", "downgrade", "rating", "price target")):
            rating_change = True
        if any(w in joined for w in ("8-k", "8k", "earnings", "press release",
                                        "files", "announces", "fda approval")):
            official = True

    n = len(relevant)
    total_w = sum(weights) or 1.0
    weighted_sent = sum(s * w for s, w in zip(sentiments, weights))
    weighted_sent_norm = weighted_sent / total_w
    pct_pos = sum(1 for s in sentiments if s > 0) / n
    pct_neg = sum(1 for s in sentiments if s < 0) / n
    pct_neu = 1.0 - pct_pos - pct_neg
    hours_first = (window_end_utc - earliest.replace(tzinfo=None)).total_seconds() / 3600.0

    return {
        "n_articles_24h": int(n),
        "n_unique_publishers": int(len(publishers)),
        "pct_positive": float(pct_pos),
        "pct_negative": float(pct_neg),
        "pct_neutral": float(pct_neu),
        "weighted_sentiment": float(weighted_sent),
        "weighted_sentiment_norm": float(weighted_sent_norm),
        "hours_to_first_article": float(hours_first),
        "co_mention_count_avg": float(sum(co_mentions) / n),
        "has_official_filing": int(official),
        "has_ratings_change": int(rating_change),
        "insights_coverage_pct": float(sum(insight_present) / n),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=180,
                        help="Backfill window in days (Polygon supports 2+ yrs)")
    parser.add_argument("--limit-tickers", type=int, default=0,
                        help="If >0, sample only this many keys")
    parser.add_argument("--rate-sleep", type=float, default=0.0,
                        help="Per-call sleep (Polygon Stocks Advanced is unlimited)")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel fetch workers (Polygon docs recommend 8-32)")
    parser.add_argument("--request-timeout", type=float, default=8.0,
                        help="Per-request timeout (s); kill hung connections")
    parser.add_argument("--out", type=str, default="news_features_polygon.parquet")
    parser.add_argument("--checkpoint-every", type=int, default=500,
                        help="Persist intermediate parquet every N keys")
    args = parser.parse_args()

    section("STEP 1 - Load credentials + keys")
    env = load_env_file(ENV_PATH)
    api_key = env.get("POLYGON_API_KEY") or os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        print("  ERROR: POLYGON_API_KEY not set in .env or shell env")
        return 1
    print(f"  Polygon API key loaded ({len(api_key)} chars)")

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
        print("  Nothing to fetch")
        return 0

    section("STEP 2 - Resume from checkpoint if present")
    out_path = DERIVED / args.out
    done_keys: set = set()
    rows: list[dict] = []
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing["d0"] = pd.to_datetime(existing["d0"])
        rows = existing.to_dict("records")
        done_keys = {(r["ticker"], pd.Timestamp(r["d0"])) for r in rows}
        print(f"  Loaded {len(rows):,} existing rows from {out_path.name}")
        # Filter keys_df to remaining
        keys_df = keys_df[~keys_df.apply(
            lambda r: (r["ticker"], pd.Timestamp(r["d0"])) in done_keys, axis=1
        )].reset_index(drop=True)
        print(f"  Remaining to fetch: {len(keys_df):,}")
    else:
        print(f"  No checkpoint at {out_path.name}; starting fresh")

    if len(keys_df) == 0:
        print("  Nothing more to fetch")
        return 0

    section(f"STEP 3 - Fetch + aggregate ({args.workers} workers)")
    import concurrent.futures as cf

    def fetch_one(row_t) -> dict:
        ticker = row_t.ticker
        d0 = pd.Timestamp(row_t.d0)
        from_iso = (d0 - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
        to_iso = d0.strftime("%Y-%m-%dT13:30:00Z")
        # Each worker uses its own client to avoid shared-state issues
        with httpx.Client(timeout=args.request_timeout) as c:
            articles = fetch_polygon_news(c, ticker, from_iso, to_iso, api_key,
                                            request_timeout=args.request_timeout)
        d0_market_open = d0.replace(tzinfo=None) + timedelta(hours=13.5)
        agg = aggregate_news(articles, d0_market_open, ticker)
        agg["ticker"] = ticker
        agg["d0"] = d0
        return agg

    n_api_calls = 0
    n_with_news = 0
    t0 = time.time()
    last_print = 0.0
    last_checkpoint = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_one, row) for row in keys_df.itertuples()]
        for i, fut in enumerate(cf.as_completed(futures)):
            try:
                agg = fut.result(timeout=30)
            except Exception as e:
                # Skip this key but keep going
                print(f"WARN: fetch_one future failed: {e}")
                continue
            n_api_calls += 1
            if agg["n_articles_24h"] > 0:
                n_with_news += 1
            rows.append(agg)

            now = time.time()
            if now - last_print > 5.0 or i == len(keys_df) - 1:
                elapsed = now - t0
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (len(keys_df) - i - 1) / rate if rate > 0 else 0
                done_total = len(rows)
                print(f"  [{i+1:>5}/{len(keys_df):,}] (total {done_total:,})  "
                      f"with_news={n_with_news} "
                      f"rate={rate:.1f}/s eta={eta:.0f}s")
                last_print = now

            # Checkpoint
            if (i + 1) - last_checkpoint >= args.checkpoint_every:
                pd.DataFrame(rows).to_parquet(out_path, compression="zstd")
                last_checkpoint = i + 1
                print(f"  [checkpoint] wrote {len(rows):,} rows -> {out_path.name}")

    print(f"\n  done: {n_api_calls} new API calls, "
          f"{n_with_news} keys with news this run ({n_with_news/max(n_api_calls,1)*100:.1f}%)")

    section("STEP 4 - Persist final")
    out_df = pd.DataFrame(rows)
    out_df.to_parquet(out_path, compression="zstd")
    print(f"  Wrote {out_path} ({len(out_df):,} rows, "
          f"{out_path.stat().st_size/1024:.1f} KB)")

    section("Stats")
    cols = ["n_articles_24h", "n_unique_publishers", "weighted_sentiment_norm",
             "insights_coverage_pct", "has_official_filing", "has_ratings_change"]
    print(out_df[cols].describe().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
