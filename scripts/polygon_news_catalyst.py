"""Polygon News API + Insights → catalyst features per ticker.

Per Compass §A.3 + §A.5: pulls /v3/reference/news for each ticker, computes:
  - n_articles_24h        raw article count
  - n_unique_publishers   diversity proxy (1 publisher × 50 syndications ≠ 50 catalysts)
  - pct_positive          fraction of insights labeled positive
  - pct_negative          fraction labeled negative
  - weighted_sentiment    sum over (publisher_tier × insight_score)
  - first_article_local_hour_after_close  late-PR pump indicator (8-10 PM ET)
  - co_mention_count      max # of OTHER tickers tagged in any article
  - latest_publisher_tier 1.0 (BusinessWire/PR Newswire) > 0.7 (Benzinga) > 0.2 (opinion)

USAGE:
    python scripts/polygon_news_catalyst.py --ticker HCAI
    python scripts/polygon_news_catalyst.py --tickers HCAI,RPGL,RYOJ,XRX
    python scripts/polygon_news_catalyst.py --ticker HCAI --hours 4

Used by:
  - lottery_runner (selection-time gating per E7 in doc 93)
  - capture analysis (which Friday picks had real news vs. didn't)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "mx-arena"))

from data_providers.polygon import PolygonClient, PolygonEndpoints  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("news")

# Per Compass §A.3: tier weights for catalyst quality
PUBLISHER_TIER = {
    # Tier 1.0: primary-source releases
    "businesswire": 1.0,
    "business wire": 1.0,
    "pr newswire": 1.0,
    "prnewswire": 1.0,
    "globenewswire": 1.0,
    "globe newswire": 1.0,
    # Tier 0.7: financial wires
    "benzinga": 0.7,
    "marketwatch": 0.6,
    "reuters": 0.7,
    "zacks": 0.5,
    # Tier 0.2: opinion / aggregation
    "motley fool": 0.2,
    "seeking alpha": 0.3,
    "the fly": 0.4,
}


def publisher_tier(name: str) -> float:
    n = (name or "").lower()
    for k, v in PUBLISHER_TIER.items():
        if k in n:
            return v
    return 0.5  # unknown publisher


def insight_score(sent: str) -> float:
    return {"positive": 1.0, "neutral": 0.0, "negative": -1.0}.get(
        (sent or "").lower(), 0.0,
    )


def hour_after_close_et(published_utc: str) -> int | None:
    """Return ET hour of the article — useful for pump-pattern detection.
    Returns 0-23. 'After close' is 16-23 ET (4 PM - midnight)."""
    try:
        from zoneinfo import ZoneInfo
        ts = datetime.fromisoformat(published_utc.rstrip("Z")).replace(tzinfo=timezone.utc)
        ts_et = ts.astimezone(ZoneInfo("America/New_York"))
        return ts_et.hour
    except Exception:
        return None


def compute_features(articles: list[dict], ticker: str) -> dict:
    """Compute per-ticker catalyst features from a list of news articles."""
    if not articles:
        return {
            "ticker": ticker,
            "n_articles": 0,
            "n_unique_publishers": 0,
            "pct_positive": None,
            "pct_negative": None,
            "weighted_sentiment": 0.0,
            "earliest_article_hour_et": None,
            "latest_article_hour_et": None,
            "max_co_mention_count": 0,
            "latest_publisher": None,
            "latest_publisher_tier": None,
            "latest_title": None,
        }

    publishers = []
    sent_pos = 0; sent_neg = 0; sent_n = 0
    weighted = 0.0
    co_mentions = []
    hours_et = []

    for a in articles:
        pub = (a.get("publisher") or {}).get("name") or ""
        publishers.append(pub)
        tier = publisher_tier(pub)
        # Find this ticker's insight
        for insight in a.get("insights") or []:
            if (insight.get("ticker") or "").upper() != ticker.upper():
                continue
            s = insight.get("sentiment") or ""
            sent_n += 1
            if s.lower() == "positive": sent_pos += 1
            elif s.lower() == "negative": sent_neg += 1
            weighted += tier * insight_score(s)
            break
        # Co-mentions: how many OTHER tickers tagged?
        all_t = a.get("tickers") or []
        co = len([t for t in all_t if t.upper() != ticker.upper()])
        co_mentions.append(co)
        # Hour
        h = hour_after_close_et(a.get("published_utc") or "")
        if h is not None:
            hours_et.append(h)

    return {
        "ticker": ticker,
        "n_articles": len(articles),
        "n_unique_publishers": len(set(publishers)),
        "pct_positive": (sent_pos / sent_n) if sent_n else None,
        "pct_negative": (sent_neg / sent_n) if sent_n else None,
        "weighted_sentiment": weighted,
        "earliest_article_hour_et": min(hours_et) if hours_et else None,
        "latest_article_hour_et": max(hours_et) if hours_et else None,
        "max_co_mention_count": max(co_mentions) if co_mentions else 0,
        "latest_publisher_tier": publisher_tier(publishers[0]) if publishers else None,
        "latest_publisher": publishers[0] if publishers else None,
        "latest_title": (articles[0].get("title") or "")[:120],
    }


async def main_async(args):
    try:
        client = PolygonClient.from_env()
    except RuntimeError as e:
        log.error(str(e))
        return 1

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    since_utc = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    async with client:
        ep = PolygonEndpoints(client)
        log.info("Fetching news for %d ticker(s), last %d hours",
                 len(tickers), args.hours)
        results = []
        for t in tickers:
            articles = await ep.news(
                ticker=t, published_utc_gte=since_utc,
                limit=50, max_pages=2,
            )
            feat = compute_features(articles, t)
            results.append(feat)
            log.info("\n=== %s ===", t)
            log.info("  n_articles:           %d", feat["n_articles"])
            log.info("  unique publishers:    %d", feat["n_unique_publishers"])
            log.info("  pct_positive:         %s", feat["pct_positive"])
            log.info("  weighted_sentiment:   %+.2f", feat["weighted_sentiment"])
            log.info("  latest publisher:     %s (tier=%s)",
                     feat["latest_publisher"], feat["latest_publisher_tier"])
            log.info("  earliest hour ET:     %s", feat["earliest_article_hour_et"])
            log.info("  max co-mentions:      %d", feat["max_co_mention_count"])
            if feat["latest_title"]:
                log.info("  latest title:         %s", feat["latest_title"])
            if args.show_articles:
                for i, a in enumerate(articles[:5], 1):
                    log.info("    [%d] %s | %s",
                             i, (a.get("publisher") or {}).get("name"),
                             (a.get("title") or "")[:100])

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str))
        log.info("\nWrote %s", args.json_out)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--tickers", type=str, default=None,
                         help="Comma-sep list (overrides --ticker)")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--show-articles", action="store_true")
    parser.add_argument("--json-out", type=str, default=None)
    args = parser.parse_args()
    if not args.tickers:
        if not args.ticker:
            parser.error("--ticker or --tickers required")
        args.tickers = args.ticker
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
