"""
Alternative Data Enrichment for Outlier Prediction.

Sprint 19: Collect features that may predict which gap-up stocks will
produce outlier bar-1 spikes (+3%+ return in the first minute).

Features collected:
1. Short interest % of float (from Finnhub or fallback estimate)
2. Float size (shares outstanding from Alpaca assets API)
3. Pre-market volume acceleration (volume growth rate 04:00-09:30)
4. Social mention velocity (from Finnhub social sentiment, if available)
5. Options-implied metrics (from Alpaca options if available)

After 200+ enriched trades, these features enable an ML predictor
that could triple outlier frequency from 6.3% to 15-20%.

Usage:
    enricher = CandidateEnricher(alpaca_client, finnhub_key)
    enrichment = await enricher.enrich(ticker, current_price, premarket_volume)
    # Returns: {short_interest_pct, float_shares, social_velocity, ...}
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentData:
    """Alternative data features for one candidate."""
    ticker: str
    timestamp: str = ""

    # Float & share structure
    float_shares: int | None = None         # Free float (tradeable shares)
    shares_outstanding: int | None = None   # Total shares outstanding
    market_cap: float | None = None         # Market capitalization

    # Short interest (predictive of squeeze dynamics)
    short_interest_pct: float | None = None  # Short interest as % of float
    short_interest_shares: int | None = None # Absolute short shares
    days_to_cover: float | None = None       # Short interest / avg daily volume

    # Pre-market volume dynamics
    premarket_volume: int = 0               # Total pre-market volume
    premarket_vol_acceleration: float | None = None  # Rate of volume increase
    # Computed as: volume_last_30min / volume_first_30min
    # > 2.0 = accelerating (bullish), < 0.5 = decelerating

    # Social/sentiment velocity
    social_velocity_30m: float | None = None  # Mentions per hour, last 30 min
    social_acceleration: float | None = None  # Rate of mention growth
    sentiment_score: float | None = None      # Aggregate sentiment (-1 to +1)

    # Options-implied signals
    options_call_oi: int | None = None       # Call open interest near strike
    options_put_oi: int | None = None        # Put open interest near strike
    options_pc_ratio: float | None = None    # Put/Call OI ratio (< 0.5 = bullish)

    # Computed quality score (filled by the predictor later)
    outlier_probability: float | None = None  # ML-predicted P(bar1_return > 3%)

    # D212: Sector/industry from Finnhub profile
    sector: str | None = None
    industry: str | None = None

    # D212: Gap history — serial gapper detection
    prior_gap_count: int | None = None    # 5%+ gap days in last 20 sessions
    is_day2_runner: bool = False           # Yesterday was also a gap day

    def to_dict(self) -> dict:
        """Serialize for journal storage."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


class CandidateEnricher:
    """
    Enriches candidates with alternative data features.

    Calls multiple data sources in parallel. Gracefully degrades
    if any source is unavailable (returns None for missing fields).

    Rate limits: Finnhub free tier = 60 calls/min, 30 calls/sec.
    With 15 candidates x 3 endpoints = 45 calls. Under limit if
    we serialize per-candidate (3 calls/candidate, 1s spacing).
    """

    # Finnhub free tier: 60 calls/min, 30 calls/sec
    _FINNHUB_CALL_SPACING = 0.1  # 100ms between calls = max 10/sec (safe margin)
    _last_finnhub_call: float = 0

    def __init__(
        self,
        alpaca_client=None,
        finnhub_api_key: str = "",
        options_provider=None,
        http_client=None,
    ):
        self._alpaca = alpaca_client
        self._finnhub_key = finnhub_api_key or os.environ.get("FINNHUB_API_KEY", "")
        self._options = options_provider  # AlpacaOptionsProvider for OI data
        self._http = http_client
        self._finnhub_base = "https://finnhub.io/api/v1"

    async def _finnhub_throttle(self) -> None:
        """Rate limit Finnhub calls to stay under free tier limits."""
        import time
        now = time.monotonic()
        elapsed = now - CandidateEnricher._last_finnhub_call
        if elapsed < self._FINNHUB_CALL_SPACING:
            await asyncio.sleep(self._FINNHUB_CALL_SPACING - elapsed)
        CandidateEnricher._last_finnhub_call = time.monotonic()

    async def enrich(
        self,
        ticker: str,
        current_price: float = 0,
        premarket_volume: int = 0,
        premarket_bars: list[dict] | None = None,
    ) -> EnrichmentData:
        """
        Collect all available enrichment data for a candidate.

        Runs data fetches in parallel. Returns whatever is available.
        Never blocks or fails — missing data returns None.
        """
        data = EnrichmentData(
            ticker=ticker,
            timestamp=datetime.now(timezone.utc).isoformat(),
            premarket_volume=premarket_volume,
        )

        # Run all enrichments in parallel
        tasks = []
        tasks.append(self._enrich_float(data))                              # Finnhub profile2 (free)
        tasks.append(self._enrich_short_interest(data))                     # Finnhub SI (premium, graceful skip)
        tasks.append(self._enrich_short_interest_yfinance(data))            # yfinance SI (free, ~2000/hr)
        tasks.append(self._enrich_social(data))                             # Finnhub social (premium, graceful skip)
        tasks.append(self._enrich_news_sentiment(data))                     # Finnhub company-news (free)
        tasks.append(self._enrich_premarket_acceleration(data, premarket_bars))
        tasks.append(self._enrich_options_oi(data, current_price))          # Alpaca options (free)
        tasks.append(self._enrich_gap_history(data))                        # D212: Alpaca daily bars (free)

        await asyncio.gather(*tasks, return_exceptions=True)

        logger.debug(
            "Enriched %s: float=%s short=%.1f%% social=%.1f accel=%.1f",
            ticker,
            data.float_shares,
            (data.short_interest_pct or 0) * 100,
            data.social_velocity_30m or 0,
            data.premarket_vol_acceleration or 0,
        )

        return data

    async def _enrich_float(self, data: EnrichmentData) -> None:
        """Get float and share structure.

        Alpaca /v2/assets/{symbol} does NOT provide shares_outstanding or
        market_cap. Use Finnhub /stock/profile2 instead (free tier, provides
        shareOutstanding and marketCapitalization).
        """
        if not self._finnhub_key:
            return
        try:
            import httpx
            await self._finnhub_throttle()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._finnhub_base}/stock/profile2",
                    params={"symbol": data.ticker, "token": self._finnhub_key},
                )
                if resp.status_code == 200:
                    profile = resp.json()
                    so = profile.get("shareOutstanding")  # In millions
                    mc = profile.get("marketCapitalization")  # In millions
                    if so and so > 0:
                        data.shares_outstanding = int(so * 1_000_000)
                        # Estimate float: small-caps typically 70-90% of outstanding
                        data.float_shares = int(data.shares_outstanding * 0.80)
                    if mc and mc > 0:
                        data.market_cap = mc * 1_000_000
                    # D212: Extract sector/industry from Finnhub profile
                    _fh_industry = profile.get("finnhubIndustry", "")
                    if _fh_industry:
                        data.sector = _fh_industry
                        data.industry = _fh_industry
        except Exception as e:
            logger.debug("Float enrichment failed for %s: %s", data.ticker, e)

    async def _enrich_short_interest(self, data: EnrichmentData) -> None:
        """Get short interest — PREMIUM ONLY on Finnhub free tier.

        /stock/short-interest returns 403 on free tier (tested 2026-03-28).
        This method is a no-op on free tier. When premium is available,
        it will automatically activate.

        Alternative free sources for future: Finviz scraping, SEC EDGAR.
        """
        if not self._finnhub_key:
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._finnhub_base}/stock/short-interest",
                    params={
                        "symbol": data.ticker,
                        "token": self._finnhub_key,
                        "from": "2026-01-01",
                        "to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    si_list = result.get("data", result) if isinstance(result, dict) else result
                    if isinstance(si_list, list) and len(si_list) > 0:
                        latest = si_list[-1]
                        si_shares = latest.get("shortInterest")
                        if si_shares and si_shares > 0:
                            data.short_interest_shares = int(si_shares)
                            if data.float_shares and data.float_shares > 0:
                                data.short_interest_pct = si_shares / data.float_shares
                            dtc = latest.get("daysToCover")
                            if dtc:
                                data.days_to_cover = float(dtc)
                # 403 = premium only, don't log warning (expected on free tier)
        except Exception as e:
            logger.debug("Short interest failed for %s: %s", data.ticker, e)

    async def _enrich_social(self, data: EnrichmentData) -> None:
        """Get social sentiment — PREMIUM ONLY on Finnhub free tier.

        /stock/social-sentiment returns 403 on free tier (tested 2026-03-28).
        No-op on free tier. When premium available, provides Reddit/Twitter
        mention velocity and sentiment scores.
        """
        if not self._finnhub_key:
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._finnhub_base}/stock/social-sentiment",
                    params={
                        "symbol": data.ticker,
                        "token": self._finnhub_key,
                        "from": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    },
                )
                if resp.status_code != 200:
                    return

                result = resp.json()
                if not isinstance(result, dict):
                    return

                reddit = result.get("reddit", []) or []
                twitter = result.get("twitter", []) or []
                all_items = reddit + twitter

                if not all_items:
                    return

                # Compute velocity (avg mentions per data point)
                recent_items = all_items[-10:]  # Last 10 data points
                total_mentions = sum(item.get("mention", 0) for item in recent_items)
                total_score = sum(item.get("score", 0) for item in recent_items)
                count = len(recent_items)

                if count > 0:
                    data.social_velocity_30m = total_mentions / count
                    data.sentiment_score = round(total_score / count, 4)

                # Acceleration: compare last 5 vs prior 5
                if len(all_items) >= 10:
                    recent_5 = sum(item.get("mention", 0) for item in all_items[-5:])
                    prior_5 = sum(item.get("mention", 0) for item in all_items[-10:-5])
                    if prior_5 > 0:
                        data.social_acceleration = round(recent_5 / prior_5, 3)
        except Exception as e:
            logger.debug("Social enrichment failed for %s: %s", data.ticker, e)

    async def _enrich_short_interest_yfinance(self, data: EnrichmentData) -> None:
        """Get short interest from yfinance (free, ~2000 calls/hr).

        yfinance ticker.info provides: sharesShort, shortRatio,
        shortPercentOfFloat, sharesShortPriorMonth, floatShares.
        Data is 7-14 days stale (FINRA bi-monthly reporting) but
        still valuable for squeeze setup identification.
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(data.ticker)
            info = ticker.info

            # Short interest
            si_pct = info.get("shortPercentOfFloat")
            if si_pct and si_pct > 0:
                data.short_interest_pct = round(si_pct, 4)
            si_shares = info.get("sharesShort")
            if si_shares and si_shares > 0:
                data.short_interest_shares = int(si_shares)
            dtc = info.get("shortRatio")
            if dtc and dtc > 0:
                data.days_to_cover = round(dtc, 2)

            # Float (backup for Finnhub)
            yf_float = info.get("floatShares")
            if yf_float and yf_float > 0 and data.float_shares is None:
                data.float_shares = int(yf_float)

            # Shares outstanding (backup)
            yf_so = info.get("sharesOutstanding")
            if yf_so and yf_so > 0 and data.shares_outstanding is None:
                data.shares_outstanding = int(yf_so)
        except Exception as e:
            logger.debug("yfinance short interest failed for %s: %s", data.ticker, e)

    async def _enrich_news_sentiment(self, data: EnrichmentData) -> None:
        """Get news headlines from Finnhub /company-news (free tier).

        Counts recent headlines as catalyst proxy. Scores keywords
        for bullish/bearish lean. After FinBERT integration (future),
        this will provide proper NLP sentiment scores.
        """
        if not self._finnhub_key:
            return
        try:
            import httpx
            await self._finnhub_throttle()

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            yesterday = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=2)).strftime("%Y-%m-%d")

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._finnhub_base}/company-news",
                    params={
                        "symbol": data.ticker,
                        "from": yesterday,
                        "to": today,
                        "token": self._finnhub_key,
                    },
                )
                if resp.status_code != 200:
                    return

                articles = resp.json()
                if not isinstance(articles, list):
                    return

                # Count headlines as catalyst proxy
                headline_count = len(articles)

                # Simple keyword sentiment scoring (pre-FinBERT)
                bullish_keywords = {
                    "fda", "approval", "breakthrough", "contract", "award",
                    "earnings beat", "upgrade", "partnership", "acquisition",
                    "revenue growth", "patent", "launch", "expansion",
                }
                bearish_keywords = {
                    "lawsuit", "fraud", "dilution", "offering", "downgrade",
                    "recall", "investigation", "bankruptcy", "default", "loss",
                }

                bull_count = 0
                bear_count = 0
                for article in articles[:20]:  # Cap at 20 headlines
                    headline = (article.get("headline", "") or "").lower()
                    summary = (article.get("summary", "") or "").lower()
                    text = headline + " " + summary
                    if any(kw in text for kw in bullish_keywords):
                        bull_count += 1
                    if any(kw in text for kw in bearish_keywords):
                        bear_count += 1

                total = bull_count + bear_count
                if total > 0:
                    data.sentiment_score = round(
                        (bull_count - bear_count) / total, 3
                    )

                # Store headline count as velocity proxy
                if headline_count > 0:
                    data.social_velocity_30m = float(headline_count)

        except Exception as e:
            logger.debug("News sentiment failed for %s: %s", data.ticker, e)

    async def _enrich_premarket_acceleration(
        self,
        data: EnrichmentData,
        premarket_bars: list[dict] | None,
    ) -> None:
        """Compute pre-market volume acceleration from bar data."""
        if not premarket_bars or len(premarket_bars) < 6:
            return
        try:
            # Split bars into first half and second half
            mid = len(premarket_bars) // 2
            first_half_vol = sum(
                b.get("v", b.get("volume", 0)) for b in premarket_bars[:mid]
            )
            second_half_vol = sum(
                b.get("v", b.get("volume", 0)) for b in premarket_bars[mid:]
            )

            if first_half_vol > 0:
                data.premarket_vol_acceleration = second_half_vol / first_half_vol
                # > 2.0 = volume doubling in second half (accelerating)
                # < 0.5 = volume halving (decelerating)
        except Exception as e:
            logger.debug("Premarket acceleration failed for %s: %s", data.ticker, e)

    async def _enrich_options_oi(
        self,
        data: EnrichmentData,
        current_price: float,
    ) -> None:
        """Get options open interest from AlpacaOptionsProvider.

        Computes call/put OI near the current strike price.
        Put/Call ratio < 0.5 = bullish (more call buying).
        High call OI near strike = potential gamma squeeze (market makers
        hedging by buying shares creates additional buy pressure at open).
        """
        if self._options is None or current_price <= 0:
            return
        try:
            # Get options chain from the existing provider
            chain = await self._options.get_chain(data.ticker)
            if not chain:
                return

            # Find strikes near current price (within 10%)
            near_calls = []
            near_puts = []
            for contract in chain:
                strike = getattr(contract, "strike", 0)
                if strike <= 0:
                    continue
                # Within 10% of current price
                if abs(strike - current_price) / current_price > 0.10:
                    continue

                cp = getattr(contract, "type", getattr(contract, "option_type", ""))
                oi = getattr(contract, "open_interest", 0) or 0

                if cp in ("call", "C"):
                    near_calls.append(oi)
                elif cp in ("put", "P"):
                    near_puts.append(oi)

            total_call_oi = sum(near_calls)
            total_put_oi = sum(near_puts)

            data.options_call_oi = total_call_oi
            data.options_put_oi = total_put_oi

            if total_call_oi > 0:
                data.options_pc_ratio = round(total_put_oi / total_call_oi, 3)
            elif total_put_oi > 0:
                data.options_pc_ratio = 99.0  # All puts, no calls

            logger.debug(
                "Options OI %s: call=%d put=%d pc_ratio=%.2f",
                data.ticker, total_call_oi, total_put_oi,
                data.options_pc_ratio or 0,
            )
        except Exception as e:
            logger.debug("Options OI failed for %s: %s", data.ticker, e)

    async def _enrich_gap_history(self, data: EnrichmentData) -> None:
        """D212: Fetch 20 daily bars to detect serial gappers and day-2 runners.

        Serial gappers (3+ gap days in 20 sessions) are promotional pump patterns.
        Day-2 runners without catalyst are likely exhausting.
        """
        if self._alpaca is None:
            return
        try:
            bars = await self._alpaca.get_bars(
                data.ticker, timeframe="1Day", limit=20,
            )
            if not bars or len(bars) < 2:
                return

            # Count gap days (open vs prior close > 5%)
            gap_count = 0
            yesterday_was_gap = False
            for i in range(1, len(bars)):
                prev_close = bars[i - 1].get("c", bars[i - 1].get("close", 0))
                curr_open = bars[i].get("o", bars[i].get("open", 0))
                if prev_close > 0:
                    gap_pct = (curr_open - prev_close) / prev_close
                    if gap_pct > 0.05:
                        gap_count += 1
                        if i == len(bars) - 1:
                            yesterday_was_gap = True

            data.prior_gap_count = gap_count
            data.is_day2_runner = yesterday_was_gap

            if gap_count >= 3:
                logger.info(
                    "D212 SERIAL GAPPER: %s — %d gap days in last %d sessions",
                    data.ticker, gap_count, len(bars),
                )
            if yesterday_was_gap:
                logger.info(
                    "D212 DAY-2 RUNNER: %s — yesterday was also a gap day",
                    data.ticker,
                )
        except Exception as e:
            logger.debug("D212 Gap history failed for %s: %s", data.ticker, e)


async def enrich_candidates_batch(
    tickers: list[str],
    alpaca_client=None,
    finnhub_key: str = "",
    options_provider=None,
    premarket_data: dict | None = None,
) -> dict[str, EnrichmentData]:
    """
    Enrich a batch of candidates in parallel.

    Returns {ticker: EnrichmentData} for all tickers.
    """
    enricher = CandidateEnricher(
        alpaca_client=alpaca_client,
        finnhub_api_key=finnhub_key,
        options_provider=options_provider,
    )

    results = {}
    tasks = []
    for ticker in tickers:
        pm_bars = None
        pm_vol = 0
        if premarket_data:
            pm_info = premarket_data.get(ticker, {})
            pm_bars = pm_info.get("premarket_bars", [])
            pm_vol = pm_info.get("premarket_volume", 0)

        tasks.append(enricher.enrich(ticker, premarket_volume=pm_vol, premarket_bars=pm_bars))

    enriched = await asyncio.gather(*tasks, return_exceptions=True)

    for ticker, result in zip(tickers, enriched):
        if isinstance(result, EnrichmentData):
            results[ticker] = result
        else:
            logger.debug("Enrichment failed for %s: %s", ticker, result)
            results[ticker] = EnrichmentData(ticker=ticker)

    logger.info(
        "Enriched %d/%d candidates (short_interest: %d, social: %d, float: %d)",
        len(results), len(tickers),
        sum(1 for r in results.values() if r.short_interest_pct is not None),
        sum(1 for r in results.values() if r.social_velocity_30m is not None),
        sum(1 for r in results.values() if r.float_shares is not None),
    )

    return results
