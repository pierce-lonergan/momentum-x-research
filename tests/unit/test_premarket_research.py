"""
Tue 2026-04-21: tests for src.data.premarket_research (slim API).

History note. The previous version of this file tested a richer API
(`TickerCache`, `CachedNewsItem`, `CachedSECFiling`, `CachedTechnicals`,
`MOMENTUM_UNIVERSE`, `PreMarketCache.save/load`) that was never
committed — only the slim `PreMarketCache(news, sec)` shipped in Sunday's
commit f9592c6. The rich-API tests therefore failed to import for the
entire window between Sun 2026-04-19 19:27 EDT and Tue 2026-04-21 (when
this rewrite landed). During that window the consumer in main.py:1376
silently AttributeError'd on every session start because it expected
`premarket_cache.tickers`, and the outer try/except logged it as
"non-fatal" — pre-market research was dormant the whole time.

This rewrite has two purposes:
  1. Lock in the *actual* slim API so a future schema regression is
     caught at test time, not at session-start time.
  2. Apply rule (e) of the hardened bug-sweep template:
     "Every fail-safe catch block ships with a test that asserts the
      output is non-empty under normal conditions."
     The `try/except` inside `_fetch_news` and `_fetch_sec` returns
     empty values silently. We add positive-case tests that prove
     populated input → populated output, so a future regression of
     either fetcher fails loudly.

If the rich PreMarketCache (TickerCache + technicals + MOMENTUM_UNIVERSE
+ save/load) is restored in Phase C, expand this file accordingly.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.data.premarket_research import PreMarketCache, PreMarketResearch


# ── Cache contract: the fields main.py reads must exist ─────────────


class TestPreMarketCacheSchema:
    """Pin the slim API. Any rename or removal of `news` or `sec`
    breaks main.py:1390-1411 silently — these tests fail loudly first."""

    def test_default_construction(self):
        cache = PreMarketCache()
        assert hasattr(cache, "news"), (
            "PreMarketCache.news is read by main.py:1391 — do not rename"
        )
        assert hasattr(cache, "sec"), (
            "PreMarketCache.sec is read by main.py:1392 — do not rename"
        )
        assert isinstance(cache.news, dict)
        assert isinstance(cache.sec, dict)
        assert cache.news == {}
        assert cache.sec == {}

    def test_news_is_keyed_by_ticker(self):
        cache = PreMarketCache()
        cache.news["NVDA"] = ["headline-stub"]
        assert cache.news["NVDA"] == ["headline-stub"]

    def test_sec_is_keyed_by_ticker(self):
        cache = PreMarketCache()
        cache.sec["NVDA"] = {"dilution_risk": True, "filings": []}
        assert cache.sec["NVDA"]["dilution_risk"] is True

    def test_consumer_pattern_does_not_attribute_error(self):
        """Direct simulation of the main.py:1390-1411 consumption pattern.
        If this test passes, that block won't AttributeError at startup."""
        cache = PreMarketCache()
        cache.news["NVDA"] = ["a", "b"]
        cache.sec["NVDA"] = {"dilution_risk": False}
        cache.news["AMD"] = []  # empty news for AMD
        cache.sec["TSLA"] = {"dilution_risk": True}

        # Replicate the exact derivations main.py does:
        pm_news_keys = set(cache.news.keys())
        pm_sec_keys = set(cache.sec.keys())
        pm_all_tickers = pm_news_keys | pm_sec_keys
        with_news = sum(1 for v in cache.news.values() if v)
        sec_checked = len(pm_sec_keys)

        assert pm_all_tickers == {"NVDA", "AMD", "TSLA"}
        assert with_news == 1   # only NVDA has non-empty news
        assert sec_checked == 2  # NVDA + TSLA


# ── Rule (e): the catch-and-return-empty paths in _fetch_news /
# _fetch_sec must NOT silently swallow correct calls. Positive cases
# below assert populated input produces populated output. ────────────


class TestFetchersPopulateOnHappyPath:
    """If a future bug breaks _fetch_news or _fetch_sec without tripping
    the explicit Exception-raise path (e.g. wrong attribute on a
    response object that returns None instead of raising), these tests
    fail loudly. This is the floor against the next 8-month dormancy."""

    @pytest.fixture
    def fake_clients(self):
        news = AsyncMock()
        news.get_news_for_ticker = AsyncMock(
            return_value=["item-1", "item-2", "item-3"],
        )

        # SEC client returns an object with the attributes
        # check_dilution_risk's return is expected to expose.
        fake_assessment = MagicMock()
        fake_assessment.has_dilution_risk = True
        fake_assessment.filings = [
            MagicMock(form_type="S-3", filed_at="2026-01-15", description="shelf"),
        ]
        sec = AsyncMock()
        sec.check_dilution_risk = AsyncMock(return_value=fake_assessment)

        alpaca = AsyncMock()
        alpaca.get_most_active_tickers = AsyncMock(return_value=["NVDA", "AMD"])

        return news, sec, alpaca

    @pytest.mark.asyncio
    async def test_run_full_prefetch_populates_news(self, fake_clients):
        news, sec, alpaca = fake_clients
        research = PreMarketResearch(news, sec, alpaca)

        cache = await research.run_full_prefetch(universe_limit=10)

        # Positive-case assertion: news WAS populated for both universe tickers.
        # If a future refactor accidentally swallows the news-fetcher result
        # (e.g. wrong field name in `cache.news[ticker] = result or []`),
        # this test fails — not the next live session.
        assert len(cache.news) == 2, (
            f"news must populate for every universe ticker; got "
            f"{list(cache.news.keys())}"
        )
        assert cache.news["NVDA"] == ["item-1", "item-2", "item-3"]
        assert cache.news["AMD"] == ["item-1", "item-2", "item-3"]

    @pytest.mark.asyncio
    async def test_run_full_prefetch_populates_sec(self, fake_clients):
        news, sec, alpaca = fake_clients
        research = PreMarketResearch(news, sec, alpaca)
        cache = await research.run_full_prefetch(universe_limit=10)

        assert len(cache.sec) == 2
        for ticker in ("NVDA", "AMD"):
            payload = cache.sec[ticker]
            assert payload["ticker"] == ticker
            assert payload["dilution_risk"] is True
            assert len(payload["filings"]) == 1
            assert payload["filings"][0]["form_type"] == "S-3"

    @pytest.mark.asyncio
    async def test_empty_universe_returns_empty_cache(self, fake_clients):
        """When the universe is empty, the cache should be empty —
        not undefined, not crashing."""
        news, sec, alpaca = fake_clients
        alpaca.get_most_active_tickers = AsyncMock(return_value=[])
        research = PreMarketResearch(news, sec, alpaca)
        cache = await research.run_full_prefetch()

        assert cache.news == {}
        assert cache.sec == {}

    @pytest.mark.asyncio
    async def test_universe_fetcher_raises_does_not_propagate(
        self, fake_clients,
    ):
        """If alpaca.get_most_active_tickers raises, the fetcher catches
        it and proceeds with an empty universe — a logged degradation,
        not a crashed session."""
        news, sec, alpaca = fake_clients
        alpaca.get_most_active_tickers = AsyncMock(side_effect=RuntimeError("boom"))
        research = PreMarketResearch(news, sec, alpaca)
        cache = await research.run_full_prefetch()

        assert cache.news == {}
        assert cache.sec == {}

    @pytest.mark.asyncio
    async def test_news_fetcher_partial_failure_does_not_drop_others(
        self, fake_clients,
    ):
        """One ticker's news raises; others must still populate."""
        news, sec, alpaca = fake_clients

        async def flaky_news(ticker, **kwargs):
            if ticker == "NVDA":
                raise RuntimeError("transient")
            return ["item-x"]
        news.get_news_for_ticker = AsyncMock(side_effect=flaky_news)
        alpaca.get_most_active_tickers = AsyncMock(
            return_value=["NVDA", "AMD", "TSLA"],
        )

        research = PreMarketResearch(news, sec, alpaca)
        cache = await research.run_full_prefetch()

        # NVDA's news entry should NOT exist (or should be empty);
        # AMD/TSLA should populate.
        assert cache.news.get("NVDA", None) in (None, [], )
        assert cache.news["AMD"] == ["item-x"]
        assert cache.news["TSLA"] == ["item-x"]


# ── enrich_news_dict / get_cached_sec contract pins ─────────────────


class TestEnrichmentContracts:

    def test_enrich_news_dict_dedups_by_headline(self):
        cache = PreMarketCache()
        item_a = MagicMock(headline="A: NVDA up")
        item_b = MagicMock(headline="B: NVDA down")
        cache.news["NVDA"] = [item_a, item_b]

        existing = MagicMock(headline="A: NVDA up")
        live_dict = {"NVDA": [existing]}

        # No clients needed for enrich_news_dict
        research = PreMarketResearch(
            news_client=None, sec_client=None, alpaca_client=None,
        )
        research.enrich_news_dict(cache, live_dict, ["NVDA"])

        # `existing` and `item_a` share headline → not duplicated.
        # `item_b` is novel → added.
        assert len(live_dict["NVDA"]) == 2
        headlines = [getattr(i, "headline", None) for i in live_dict["NVDA"]]
        assert "A: NVDA up" in headlines
        assert "B: NVDA down" in headlines

    def test_get_cached_sec_returns_none_for_unknown(self):
        cache = PreMarketCache()
        cache.sec["NVDA"] = {"dilution_risk": True}
        research = PreMarketResearch(
            news_client=None, sec_client=None, alpaca_client=None,
        )
        assert research.get_cached_sec(cache, "AMD") is None
        assert research.get_cached_sec(cache, "NVDA") == {"dilution_risk": True}
