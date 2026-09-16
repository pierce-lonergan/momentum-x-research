"""
Pre-Market Research Engine (Phase 0)

Prefetches news and SEC filings for the universe of tickers before market open.
Results are cached in PreMarketCache and used to enrich Phase 1/2/3 evaluation.

Node ID: data.premarket_research
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("momentum_x")


# ── Bug U fix (2026-04-23) ─────────────────────────────────────────
# MOMENTUM_UNIVERSE is the screener-fallback ticker list. Originally
# defined in src.data.scenario_builder (deleted in a prior refactor),
# its absence produced 7 silent ImportError fall-throughs on the
# 2026-04-23 session every time Alpaca's screener API timed out.
#
# Intentional design choice: fail closed (empty list) rather than
# trade a stale baked-in universe. When the screener fails, the
# fallback returns zero candidates → next iteration retries the
# screener → eventually recovers. Trading a stale universe under
# screener outage would feed the agent pipeline tickers unrelated
# to today's actual gap-up cohort, biasing all downstream signals.
#
# If a future operator needs a real fallback list, replace [] below
# with a curated set of high-volatility microcaps. The fail-closed
# default is the safer production posture.
MOMENTUM_UNIVERSE: list[str] = []


@dataclass
class PreMarketCache:
    """
    Cached pre-market data for a set of tickers.
    Produced by PreMarketResearch.run_full_prefetch().
    """

    news: dict[str, list[Any]] = field(default_factory=dict)   # ticker -> list[NewsItem]
    sec: dict[str, dict[str, Any]] = field(default_factory=dict)  # ticker -> sec payload


class PreMarketResearch:
    """
    Runs Phase 0 pre-market data prefetch for the scan universe.

    Fetches news + SEC filings in parallel for all universe tickers so that
    downstream agents (Phase 1/2/3) can use cached data without extra latency.
    """

    def __init__(self, news_client: Any, sec_client: Any, alpaca_client: Any) -> None:
        self._news = news_client
        self._sec = sec_client
        self._alpaca = alpaca_client
        self._universe: list[str] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_full_prefetch(self, universe_limit: int = 50) -> PreMarketCache:
        """
        Fetch news + SEC filings for the most-active universe in parallel.
        Returns a PreMarketCache that can be passed to enrich_news_dict / get_cached_sec.
        """
        # 1. Resolve universe
        try:
            self._universe = await self._alpaca.get_most_active_tickers(limit=universe_limit)
        except Exception as exc:
            logger.warning("PreMarketResearch: failed to fetch universe — %s", exc)
            self._universe = []

        if not self._universe:
            logger.warning("PreMarketResearch: empty universe, skipping prefetch")
            return PreMarketCache()

        logger.info(
            "PreMarketResearch: prefetching %d tickers (news + SEC)…",
            len(self._universe),
        )

        # 2. Parallel fetch
        news_tasks = [self._fetch_news(t) for t in self._universe]
        sec_tasks = [self._fetch_sec(t) for t in self._universe]
        news_results, sec_results = await asyncio.gather(
            asyncio.gather(*news_tasks, return_exceptions=True),
            asyncio.gather(*sec_tasks, return_exceptions=True),
        )

        cache = PreMarketCache()
        for ticker, result in zip(self._universe, news_results):
            if isinstance(result, Exception):
                logger.debug("PreMarketResearch news fetch failed %s: %s", ticker, result)
            else:
                cache.news[ticker] = result or []

        for ticker, result in zip(self._universe, sec_results):
            if isinstance(result, Exception):
                logger.debug("PreMarketResearch SEC fetch failed %s: %s", ticker, result)
            elif result:
                cache.sec[ticker] = result

        logger.info(
            "PreMarketResearch: cache built — %d news, %d SEC",
            sum(1 for v in cache.news.values() if v),
            len(cache.sec),
        )
        return cache

    def enrich_news_dict(
        self,
        cache: PreMarketCache,
        news_dict: dict[str, list[Any]],
        tickers: list[str],
    ) -> None:
        """
        Merge cached pre-market news into news_dict (in-place).
        Only adds items not already present (dedup by headline).
        """
        for ticker in tickers:
            cached = cache.news.get(ticker)
            if not cached:
                continue
            existing = news_dict.get(ticker) or []
            existing_headlines = {getattr(i, "headline", None) for i in existing}
            new_items = [
                item for item in cached
                if getattr(item, "headline", None) not in existing_headlines
            ]
            if new_items:
                news_dict[ticker] = existing + new_items

    def get_cached_sec(
        self,
        cache: PreMarketCache,
        ticker: str,
    ) -> dict[str, Any] | None:
        """Return cached SEC payload for ticker, or None if not available."""
        return cache.sec.get(ticker)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_news(self, ticker: str) -> list[Any]:
        try:
            return await self._news.get_news_for_ticker(ticker, lookback_hours=18, max_items=10)
        except Exception as exc:
            logger.debug("PreMarketResearch._fetch_news(%s): %s", ticker, exc)
            return []

    async def _fetch_sec(self, ticker: str) -> dict[str, Any] | None:
        try:
            assessment = await self._sec.check_dilution_risk(ticker)
            if assessment is None:
                return None
            # Normalize to a plain dict that downstream agents expect
            filings = getattr(assessment, "filings", None) or []
            return {
                "ticker": ticker,
                "dilution_risk": getattr(assessment, "has_dilution_risk", False),
                "filings": [
                    {
                        "form_type": str(getattr(f, "form_type", "")),
                        "filed_at": str(getattr(f, "filed_at", "")),
                        "description": getattr(f, "description", ""),
                    }
                    for f in filings
                ],
            }
        except Exception as exc:
            logger.debug("PreMarketResearch._fetch_sec(%s): %s", ticker, exc)
            return None
