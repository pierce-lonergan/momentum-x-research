"""
MOMENTUM-X News Data Client

### ARCHITECTURAL CONTEXT
Node ID: data.news_client
Graph Link: docs/memory/graph_state.json → "data.news_client"

### RESEARCH BASIS
Implements multi-source news aggregation per ADR-002 §3.
News sentiment is the #1 driver of +20% single-day moves (MOMENTUM_LOGIC.md §5: w=0.30).
OPT model achieves 74.4% accuracy on financial news sentiment (REF-003).

### CRITICAL INVARIANTS
1. Deduplication by headline similarity (>90% match = duplicate) — ADR-002 §3.
2. All news items normalized to NewsItem model with source attribution.
3. Rate-limited polling for REST sources (Finnhub: 60 req/min).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants (justified) ────────────────────────────────────────────
# Dedup threshold: ADR-002 §3 specifies >90% fuzzy match
DEDUP_SIMILARITY_THRESHOLD = 0.90
# Finnhub rate limit: 60 req/min (DATA-005)
FINNHUB_RATE_LIMIT = 60
# Alpaca news API max results per request
ALPACA_NEWS_MAX_LIMIT = 50


@dataclass(frozen=True)
class NewsItem:
    """
    Normalized news item from any source.
    Immutable to prevent downstream mutation.

    Node ID: data.news_client.NewsItem
    """

    headline: str
    summary: str
    source: str
    url: str
    published_at: datetime
    tickers: list[str] = field(default_factory=list)
    raw_sentiment: float | None = None  # Provider sentiment if available
    provider: str = ""  # "alpaca", "finnhub"


class NewsClient:
    """
    Multi-source news aggregator with deduplication.

    Node ID: data.news_client
    Graph Link: docs/memory/graph_state.json → "data.news_client"

    Aggregates news from:
    - Alpaca News API (DATA-001): Real-time, ticker-specific
    - Finnhub News API (DATA-005): Company news + general market

    Ref: ADR-002 §3 (multi-source aggregation)
    Ref: REF-003 (LLM sentiment on financial news)
    """

    def __init__(
        self,
        alpaca_api_key: str = "",
        alpaca_secret_key: str = "",
        finnhub_api_key: str = "",
    ) -> None:
        self._alpaca_headers = {
            "APCA-API-KEY-ID": alpaca_api_key,
            "APCA-API-SECRET-KEY": alpaca_secret_key,
        }
        self._finnhub_key = finnhub_api_key

        # D85: Persistent HTTP client with connection pooling
        self._http_client: httpx.AsyncClient | None = None

    async def get_news_for_ticker(
        self,
        ticker: str,
        lookback_hours: int = 24,
        max_items: int = 20,
        as_of: str | None = None,
    ) -> list[NewsItem]:
        """
        Fetch, deduplicate, and merge news from all sources for a ticker.

        Args:
            ticker: Stock symbol (e.g., "NVDA")
            lookback_hours: How far back to search (default 24h)
            max_items: Maximum items to return after dedup
            as_of: ISO date string (e.g., "2023-11-28") to fetch news
                   around that historical date instead of now. When set,
                   news is fetched from (as_of - lookback_hours) to
                   (as_of + 1 day). Default: None (fetch from now).

        Returns:
            Deduplicated, time-sorted list of NewsItem

        Ref: ADR-002 §3
        """
        # ── Parallel fetch from all sources ──
        tasks = []
        tasks.append(self._fetch_alpaca_news(ticker, lookback_hours, as_of=as_of))
        if self._finnhub_key:
            tasks.append(self._fetch_finnhub_news(ticker, lookback_hours, as_of=as_of))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── Merge all items ──
        all_items: list[NewsItem] = []
        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            elif isinstance(result, Exception):
                logger.warning("News source failed: %s", result)

        # ── D117: Filter stale news from previous trading sessions ──
        # Bug: MOBX Mar 20 — same "Anti-Drone Smart Munitions" headline from
        # Mar 19 returned via 24h lookback, triggering a false BULL signal on
        # a stock that had already reacted (-9.4% loss).
        # Fix: In live mode (no as_of), exclude news published before the
        # previous market close (4 PM ET). After-hours / overnight / pre-market
        # news still counts; only prior-session news is filtered.
        if not as_of:
            all_items = self._filter_stale_session_news(all_items)

        # ── Deduplicate by headline similarity ──
        deduped = self._deduplicate(all_items)

        # ── Sort by published_at descending (newest first) ──
        deduped.sort(key=lambda x: x.published_at, reverse=True)

        return deduped[:max_items]

    async def get_market_news(
        self,
        lookback_hours: int = 6,
        max_items: int = 30,
    ) -> list[NewsItem]:
        """
        Fetch general market news (not ticker-specific).
        Used for broad market sentiment assessment.
        """
        items = await self._fetch_alpaca_news("", lookback_hours)
        items.sort(key=lambda x: x.published_at, reverse=True)
        return items[:max_items]

    # ── Alpaca News API ──────────────────────────────────────────────

    async def _fetch_alpaca_news(
        self, ticker: str, lookback_hours: int, as_of: str | None = None
    ) -> list[NewsItem]:
        """
        Fetch from Alpaca News API.

        Endpoint: GET https://data.alpaca.markets/v1beta1/news
        Ref: DATA-001

        Args:
            ticker: Stock symbol.
            lookback_hours: How far back to search from reference time.
            as_of: ISO date string for historical lookups. When set,
                   fetches news from (as_of - lookback_hours) to (as_of + 1 day).
        """
        if as_of:
            # Historical mode: anchor around the scenario date
            # Use Eastern Time for US market hours (DST-aware).
            # 9:30 AM ET is market open — anchor news queries around this.
            from zoneinfo import ZoneInfo
            _et = ZoneInfo("America/New_York")
            _y, _m, _d = (int(x) for x in as_of.split("-"))
            ref_time = datetime(_y, _m, _d, 9, 30, tzinfo=_et)
            end_time = ref_time + timedelta(hours=24)
        else:
            ref_time = datetime.now(timezone.utc)
            end_time = None

        start = ref_time - timedelta(hours=lookback_hours)
        params: dict[str, Any] = {
            "start": start.isoformat(),
            "limit": ALPACA_NEWS_MAX_LIMIT,
            "sort": "desc",
        }
        if end_time:
            params["end"] = end_time.isoformat()
        if ticker:
            params["symbols"] = ticker

        try:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(
                    timeout=15,
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                )
            resp = await self._http_client.get(
                "https://data.alpaca.markets/v1beta1/news",
                headers=self._alpaca_headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            # D279 (2026-05-05): production "Alpaca news fetch failed:" log
            # had EMPTY exception detail because some httpx exceptions
            # render as "" via str(). Use repr() to always show the class
            # name, and include status_code / response.text when available.
            _detail = repr(e)
            _resp = getattr(e, "response", None)
            if _resp is not None:
                _detail = f"{_detail} status={_resp.status_code} body={_resp.text[:200]!r}"
            logger.error("Alpaca news fetch failed: %s", _detail)
            return []

        items = []
        for article in data.get("news", []):
            items.append(
                NewsItem(
                    headline=article.get("headline", ""),
                    summary=article.get("summary", ""),
                    source=article.get("source", ""),
                    url=article.get("url", ""),
                    published_at=datetime.fromisoformat(
                        article.get("created_at", "2026-01-01T00:00:00Z")
                        .replace("Z", "+00:00")
                    ),
                    tickers=article.get("symbols", []),
                    provider="alpaca",
                )
            )
        return items

    # ── Finnhub News API ─────────────────────────────────────────────

    async def _fetch_finnhub_news(
        self, ticker: str, lookback_hours: int, as_of: str | None = None
    ) -> list[NewsItem]:
        """
        Fetch from Finnhub Company News API.

        Endpoint: GET https://finnhub.io/api/v1/company-news
        Ref: DATA-005

        Args:
            ticker: Stock symbol.
            lookback_hours: How far back to search from reference time.
            as_of: ISO date string for historical lookups.
        """
        if as_of:
            # Use Eastern Time for US market hours (DST-aware).
            # 9:30 AM ET is market open — anchor news queries around this.
            from zoneinfo import ZoneInfo
            _et = ZoneInfo("America/New_York")
            _y, _m, _d = (int(x) for x in as_of.split("-"))
            ref_time = datetime(_y, _m, _d, 9, 30, tzinfo=_et)
            end = ref_time + timedelta(hours=24)
        else:
            end = datetime.now(timezone.utc)
        start = end - timedelta(hours=lookback_hours)

        try:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(
                    timeout=15,
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                )
            resp = await self._http_client.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": ticker,
                    "from": start.strftime("%Y-%m-%d"),
                    "to": end.strftime("%Y-%m-%d"),
                    "token": self._finnhub_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("Finnhub news fetch failed: %s", e)
            return []

        items = []
        for article in data if isinstance(data, list) else []:
            items.append(
                NewsItem(
                    headline=article.get("headline", ""),
                    summary=article.get("summary", ""),
                    source=article.get("source", ""),
                    url=article.get("url", ""),
                    published_at=datetime.fromtimestamp(
                        article.get("datetime", 0), tz=timezone.utc
                    ),
                    tickers=[ticker] if ticker else [],
                    raw_sentiment=article.get("sentiment"),
                    provider="finnhub",
                )
            )
        return items

    # ── Stale News Filter ────────────────────────────────────────────

    @staticmethod
    def _filter_stale_session_news(items: list[NewsItem]) -> list[NewsItem]:
        """
        D117: Remove news from previous trading sessions.

        Session boundary: previous trading day's market close (4:00 PM ET).
        News published AFTER previous close counts for the current session
        (after-hours, overnight, pre-market). News published DURING or BEFORE
        the previous session is stale — the stock already reacted to it.

        Example: System runs at 9:30 AM ET Mar 20.
        - Boundary: 4:00 PM ET Mar 19
        - "MOBX Anti-Drone" published 8:30 AM ET Mar 19 → FILTERED (stale)
        - Earnings release at 4:30 PM ET Mar 19 → KEPT (after-hours)
        - Pre-market rumor at 7:00 AM ET Mar 20 → KEPT (current session)
        """
        if not items:
            return items

        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        now_et = datetime.now(et)

        # Previous close boundary: today at 4 PM ET if we're past 4 PM,
        # otherwise yesterday at 4 PM ET.
        if now_et.hour >= 16:
            # After today's close — boundary is today's close
            close_boundary = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        else:
            # Before today's close — boundary is previous day's close
            close_boundary = (now_et - timedelta(days=1)).replace(
                hour=16, minute=0, second=0, microsecond=0
            )
            # Skip weekends: if boundary lands on Saturday, go back to Friday
            while close_boundary.weekday() >= 5:  # 5=Saturday, 6=Sunday
                close_boundary -= timedelta(days=1)

        close_boundary_utc = close_boundary.astimezone(timezone.utc)

        filtered = []
        for item in items:
            pub = item.published_at
            # Ensure timezone-aware comparison
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub >= close_boundary_utc:
                filtered.append(item)
            else:
                logger.debug(
                    "D117 stale news filtered: %s (published %s, boundary %s)",
                    item.headline[:60],
                    pub.isoformat(),
                    close_boundary_utc.isoformat(),
                )

        if len(filtered) < len(items):
            logger.info(
                "D117: Filtered %d stale news items (before %s ET)",
                len(items) - len(filtered),
                close_boundary.strftime("%Y-%m-%d %H:%M"),
            )

        return filtered

    # ── Deduplication ────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(items: list[NewsItem]) -> list[NewsItem]:
        """
        Remove duplicate news items by headline similarity.
        Uses SequenceMatcher with threshold of 0.90 per ADR-002 §3.

        When duplicates are found, prefer the version with more detail
        (longer summary) or from the more reliable source (Alpaca > Finnhub).
        """
        if not items:
            return []

        # Sort by summary length descending — prefer more detailed version
        sorted_items = sorted(items, key=lambda x: len(x.summary), reverse=True)

        deduped: list[NewsItem] = []
        seen_headlines: list[str] = []

        for item in sorted_items:
            is_dup = False
            for seen in seen_headlines:
                similarity = SequenceMatcher(
                    None, item.headline.lower(), seen.lower()
                ).ratio()
                if similarity >= DEDUP_SIMILARITY_THRESHOLD:
                    is_dup = True
                    break

            if not is_dup:
                deduped.append(item)
                seen_headlines.append(item.headline)

        return deduped
