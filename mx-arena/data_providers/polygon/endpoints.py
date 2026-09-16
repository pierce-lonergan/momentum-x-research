"""Typed endpoint wrappers for the Polygon endpoints we use tonight.

Six endpoints per Block A.2:
- aggregates (minute/day bars)
- quotes (historical NBBO ticks)
- trades (historical trade ticks)
- reference/tickers (universe enumeration)
- reference/tickers/{ticker} (per-ticker ref including market cap)
- snapshots/gainers (intraday gainers)
- reference/financials (per-MAGNA-N quarterly fundamentals)
"""
from __future__ import annotations

import logging
from typing import Optional

from .client import PolygonClient
from .models import Bar, Quote, Trade, TickerRef, FinancialReport

logger = logging.getLogger(__name__)


class PolygonEndpoints:
    """High-level typed API. Wraps PolygonClient and parses responses
    into our dataclasses.
    """

    def __init__(self, client: PolygonClient) -> None:
        self._c = client

    # ── Aggregates (bars) ────────────────────────────────────────

    async def aggregates(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        from_: str,
        to: str,
        *,
        adjusted: bool = True,
        sort: str = "asc",
        limit: int = 50_000,
    ) -> list[Bar]:
        """GET /v2/aggs/ticker/{ticker}/range/{m}/{ts}/{from}/{to}.

        timespan: "minute" | "hour" | "day" | "week" | "month" | "quarter" | "year"
        from_/to: "YYYY-MM-DD" or unix-ms
        """
        endpoint = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_}/{to}"
        params = {
            "adjusted": "true" if adjusted else "false",
            "sort": sort,
            "limit": limit,
        }
        bars: list[Bar] = []
        async for resp in self._c.paginate(endpoint, params, max_pages=200):
            for obj in resp.results:
                bars.append(Bar.from_json(ticker, obj))
        return bars

    # ── Quotes (historical NBBO) ─────────────────────────────────

    async def quotes(
        self,
        ticker: str,
        *,
        timestamp_gte_ns: Optional[int] = None,
        timestamp_lte_ns: Optional[int] = None,
        order: str = "asc",
        limit: int = 50_000,
    ) -> list[Quote]:
        """GET /v3/quotes/{ticker} — historical NBBO.

        Use ns precision timestamps for tight intra-bar windows. Returns
        all quotes via pagination (cap at max_pages=2000 for full session
        of an active ticker).
        """
        endpoint = f"/v3/quotes/{ticker}"
        params: dict = {"order": order, "limit": limit}
        if timestamp_gte_ns is not None:
            params["timestamp.gte"] = timestamp_gte_ns
        if timestamp_lte_ns is not None:
            params["timestamp.lte"] = timestamp_lte_ns
        quotes: list[Quote] = []
        async for resp in self._c.paginate(endpoint, params, max_pages=2000):
            for obj in resp.results:
                quotes.append(Quote.from_json(ticker, obj))
        return quotes

    # ── Trades (historical) ──────────────────────────────────────

    async def trades(
        self,
        ticker: str,
        *,
        timestamp_gte_ns: Optional[int] = None,
        timestamp_lte_ns: Optional[int] = None,
        order: str = "asc",
        limit: int = 50_000,
    ) -> list[Trade]:
        """GET /v3/trades/{ticker} — historical trade ticks."""
        endpoint = f"/v3/trades/{ticker}"
        params: dict = {"order": order, "limit": limit}
        if timestamp_gte_ns is not None:
            params["timestamp.gte"] = timestamp_gte_ns
        if timestamp_lte_ns is not None:
            params["timestamp.lte"] = timestamp_lte_ns
        trades: list[Trade] = []
        async for resp in self._c.paginate(endpoint, params, max_pages=2000):
            for obj in resp.results:
                trades.append(Trade.from_json(ticker, obj))
        return trades

    # ── Reference (tickers + per-ticker details) ─────────────────

    async def list_tickers(
        self,
        *,
        market: str = "stocks",
        type_: Optional[str] = None,
        active: bool = True,
        limit: int = 1000,
    ) -> list[TickerRef]:
        """GET /v3/reference/tickers — universe enumeration.

        type_: e.g. "ETF", "CS" (common stock), None = all
        """
        endpoint = "/v3/reference/tickers"
        params: dict = {"market": market, "active": str(active).lower(), "limit": limit}
        if type_:
            params["type"] = type_
        out: list[TickerRef] = []
        async for resp in self._c.paginate(endpoint, params, max_pages=200):
            for obj in resp.results:
                out.append(TickerRef.from_json(obj))
        return out

    async def ticker_details(self, ticker: str, *, date: Optional[str] = None) -> Optional[TickerRef]:
        """GET /v3/reference/tickers/{ticker} — single ticker ref including
        market_cap + share count.

        date: optional 'YYYY-MM-DD' for as-of fundamentals.
        """
        endpoint = f"/v3/reference/tickers/{ticker}"
        params: dict = {}
        if date:
            params["date"] = date
        resp = await self._c.get(endpoint, params)
        results = resp.data.get("results")
        if not results:
            return None
        # Singular response — `results` is a dict, not a list.
        if isinstance(results, list):
            results = results[0] if results else None
        if not results:
            return None
        return TickerRef.from_json(results)

    # ── Gainers snapshot ─────────────────────────────────────────

    async def gainers(self) -> list[dict]:
        """GET /v2/snapshot/locale/us/markets/stocks/gainers.

        Returns raw dicts (not modeled — used as a cheap proxy for
        intraday-momentum candidates, not core to the EP rebuild).
        """
        endpoint = "/v2/snapshot/locale/us/markets/stocks/gainers"
        resp = await self._c.get(endpoint, bypass_cache=True)
        # Polygon returns "tickers" array, not "results", on this endpoint.
        return resp.data.get("tickers") or []

    # ── Financials (per-MAGNA-N) ─────────────────────────────────

    async def financials(
        self,
        ticker: str,
        *,
        timeframe: str = "quarterly",
        limit: int = 8,
    ) -> list[FinancialReport]:
        """GET /vX/reference/financials.

        timeframe: "annual" | "quarterly" | "ttm"
        limit: number of periods to return (8 = 2y of quarters)
        """
        endpoint = "/vX/reference/financials"
        params = {"ticker": ticker, "timeframe": timeframe, "limit": limit}
        resp = await self._c.get(endpoint, params)
        out: list[FinancialReport] = []
        for obj in resp.results:
            out.append(FinancialReport.from_json(ticker, obj))
        return out

    # ────────────────────────────────────────────────────────────────
    # PHASE-2 EXTENSIONS (per docs/research/polygon_compass_playbook.md)
    # ────────────────────────────────────────────────────────────────

    # ── Snapshots (LIVE, no cache) ──────────────────────────────────

    async def losers(self) -> list[dict]:
        """GET /v2/snapshot/locale/us/markets/stocks/losers.

        Symmetric to gainers(). Returns top-20 daily losers — short-side
        candidate generation (E11 in doc 93, also Compass §A.4).
        """
        endpoint = "/v2/snapshot/locale/us/markets/stocks/losers"
        resp = await self._c.get(endpoint, bypass_cache=True)
        return resp.data.get("tickers") or []

    async def all_tickers_snapshot(self) -> list[dict]:
        """GET /v2/snapshot/locale/us/markets/stocks/tickers.

        Returns last trade, last quote, today's bar, prev day's bar for
        every active equity (~10k tickers, multi-MB JSON).

        Per Compass §A.4: do NOT poll faster than every 5–15s. Server-side
        cost is non-trivial.
        """
        endpoint = "/v2/snapshot/locale/us/markets/stocks/tickers"
        resp = await self._c.get(endpoint, bypass_cache=True)
        return resp.data.get("tickers") or []

    async def universal_snapshot(
        self, tickers: list[str], *, market: str = "stocks",
    ) -> list[dict]:
        """GET /v3/snapshot — multi-symbol bundled snapshot.

        Max 250 symbols per call (Polygon hard cap). Caller MUST chunk.
        Use for the focused 30→300 ticker watch tier.
        """
        if len(tickers) > 250:
            raise ValueError(f"universal_snapshot capped at 250 symbols, got {len(tickers)}")
        endpoint = "/v3/snapshot"
        params = {"ticker.any_of": ",".join(tickers), "market_type": market}
        resp = await self._c.get(endpoint, params, bypass_cache=True)
        return resp.results

    async def single_ticker_snapshot(self, ticker: str) -> dict | None:
        """GET /v2/snapshot/locale/us/markets/stocks/tickers/{T}."""
        endpoint = f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        try:
            resp = await self._c.get(endpoint, bypass_cache=True)
        except Exception:
            return None
        return resp.data.get("ticker")

    # ── News API + Insights ─────────────────────────────────────────

    async def news(
        self,
        ticker: Optional[str] = None,
        *,
        published_utc_gte: Optional[str] = None,
        published_utc_lte: Optional[str] = None,
        order: str = "desc",
        limit: int = 50,
        max_pages: int = 5,
    ) -> list[dict]:
        """GET /v2/reference/news — news articles with LLM-generated insights.

        Note (verified against this account 2026-05-02): the actual path is
        /v2/reference/news. The Compass doc cites /v3 but that path returns
        404 on Stocks Advanced. Schema is identical (publisher, title,
        article_url, tickers, insights[]).

        Per Compass §A.3: latency ~<60s for primary publishers (Benzinga,
        BusinessWire, PR Newswire), 2-10 min for Motley Fool / Reuters.

        Insights schema per article:
          insights: [{ticker, sentiment: positive|neutral|negative,
                      sentiment_reasoning: str}]
        """
        endpoint = "/v2/reference/news"
        params: dict = {"order": order, "limit": limit}
        if ticker:
            params["ticker"] = ticker
        if published_utc_gte:
            params["published_utc.gte"] = published_utc_gte
        if published_utc_lte:
            params["published_utc.lte"] = published_utc_lte
        out: list[dict] = []
        async for resp in self._c.paginate(endpoint, params, max_pages=max_pages):
            for obj in resp.results:
                out.append(obj)
        return out

    # ── Reference: corporate actions ────────────────────────────────

    async def dividends(
        self, *, ticker: Optional[str] = None,
        ex_dividend_date_gte: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """GET /v3/reference/dividends.

        Per Compass §A.6: distinguish 'ex-div drop' from 'pump fade'.
        """
        endpoint = "/v3/reference/dividends"
        params: dict = {"limit": limit, "order": "desc"}
        if ticker:
            params["ticker"] = ticker
        if ex_dividend_date_gte:
            params["ex_dividend_date.gte"] = ex_dividend_date_gte
        resp = await self._c.get(endpoint, params)
        return resp.results

    async def splits(
        self, *, ticker: Optional[str] = None,
        execution_date_gte: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """GET /v3/reference/splits.

        Per Compass §A.6: 'reverse split in last 90 days + gap-up =
        high-probability fade'. Critical microcap fraud filter.
        """
        endpoint = "/v3/reference/splits"
        params: dict = {"limit": limit, "order": "desc"}
        if ticker:
            params["ticker"] = ticker
        if execution_date_gte:
            params["execution_date.gte"] = execution_date_gte
        resp = await self._c.get(endpoint, params)
        return resp.results

    # ── Reference: short data ───────────────────────────────────────

    async def short_interest(
        self, *, ticker: Optional[str] = None, limit: int = 100,
    ) -> list[dict]:
        """GET /v2/market/short-interest/{T} (or /v3 variant).

        FINRA bi-monthly short interest. ~4-day reporting lag. Useful as
        days-to-cover baseline. NOT real-time.
        """
        if ticker:
            endpoint = f"/v3/reference/short-interest/{ticker}"
        else:
            endpoint = "/v3/reference/short-interest"
        params = {"limit": limit}
        resp = await self._c.get(endpoint, params)
        return resp.results

    async def short_volume(
        self, *, ticker: Optional[str] = None, limit: int = 100,
    ) -> list[dict]:
        """GET /v3/reference/short-volume.

        Daily short volume from FINRA (T+1). Per Compass §A.6: leading
        indicator for squeeze setups.
        """
        if ticker:
            endpoint = f"/v3/reference/short-volume/{ticker}"
        else:
            endpoint = "/v3/reference/short-volume"
        params = {"limit": limit}
        resp = await self._c.get(endpoint, params)
        return resp.results

    # ── Reference: classification + free float ──────────────────────

    async def ticker_types(self, *, asset_class: str = "stocks") -> list[dict]:
        """GET /v3/reference/tickers/types.

        Canonical ticker types: CS, ADRC, ADRP, ADRR, ADRW, GDR, NYRS,
        UNIT, RIGHT, PFD, FUND, SP, WARRANT, INDEX, ETF, ETN, OS, BOND,
        BASKET, AGEN, EQLK, LT, OTHER.

        Per Compass §A.6: for microcap gap-up filter to CS + ADRC only.
        """
        endpoint = "/v3/reference/tickers/types"
        params = {"asset_class": asset_class}
        resp = await self._c.get(endpoint, params)
        return resp.results

    async def conditions(self, *, asset_class: str = "stocks") -> list[dict]:
        """GET /v3/reference/conditions.

        Trade & quote condition codes dictionary. Pull once, cache. Critical
        for decoding tick-level data (sweep detection, odd-lot filtering,
        dark-pool flagging).

        Per Compass §A.2: ISO sweep flag (code 15) = institutional aggression.
        """
        endpoint = "/v3/reference/conditions"
        params = {"asset_class": asset_class}
        out: list[dict] = []
        async for resp in self._c.paginate(endpoint, params, max_pages=10):
            for obj in resp.results:
                out.append(obj)
        return out

    async def exchanges(self, *, asset_class: str = "stocks") -> list[dict]:
        """GET /v3/reference/exchanges. Map exchange ID → name."""
        endpoint = "/v3/reference/exchanges"
        params = {"asset_class": asset_class}
        resp = await self._c.get(endpoint, params)
        return resp.results

    # ── Aggregates: grouped (whole-market daily) ────────────────────

    async def grouped_daily(
        self, date: str, *, adjusted: bool = False, include_otc: bool = False,
    ) -> list[dict]:
        """GET /v2/aggs/grouped/locale/us/market/stocks/{date}.

        Returns OHLCV for EVERY US ticker in one call (~9000 rows).
        Replaces 9000 separate /v2/aggs calls.
        """
        endpoint = f"/v2/aggs/grouped/locale/us/market/stocks/{date}"
        params = {
            "adjusted": "true" if adjusted else "false",
            "include_otc": "true" if include_otc else "false",
        }
        resp = await self._c.get(endpoint, params)
        return resp.results

    # ── Market status ───────────────────────────────────────────────

    async def market_status_now(self) -> dict:
        """GET /v1/marketstatus/now."""
        resp = await self._c.get("/v1/marketstatus/now", bypass_cache=True)
        return resp.data

    async def market_status_upcoming(self) -> list[dict]:
        """GET /v1/marketstatus/upcoming. Holiday + early-close calendar.

        Endpoint returns a bare array; client normalizes it into
        {"results": [...]} so resp.results works uniformly.
        """
        resp = await self._c.get("/v1/marketstatus/upcoming")
        return resp.results
