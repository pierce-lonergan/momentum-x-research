"""D192: Multi-tier short interest data pipeline for SQUEEZE archetype detection.

### ARCHITECTURAL CONTEXT
Node ID: data.short_interest
Graph Link: docs/memory/graph_state.json → "data.short_interest"

### DESIGN RATIONALE
Short interest is the single most important context signal for the SQUEEZE archetype.
When a heavily-shorted stock gaps up, short sellers are forced to buy to cover losses —
this creates a self-reinforcing feedback loop (the "squeeze") that can sustain a gap
far beyond what fundamentals justify.

The SQUEEZE archetype has inverted risk management rules vs. the standard long:
  - NEVER short against a squeeze (forced covering is directional, not mean-reverting)
  - Wider trailing stops (forced covering creates explosive intraday extensions)
  - Accelerated profit-taking (squeezes often peak violently, then dump)
  - Faller score reduced by 0.30 (gap persistence is structural, not promotional)

### DATA TIERS
Tier 1 (Primary): Alpaca Market Data — screener/most-actives short interest proxy
Tier 2 (Secondary): yfinance — shortPercentOfFloat from stock.info (free, batched)
Tier 3 (Tertiary): Finviz scraping — "Short Float" cell from quote page (anti-scraping)
Tier 4 (Future): FINRA Query API — institutional grade (stub, requires registration)

### DATA LATENCY
Institutional shorts hold positions for weeks/months. 2-3 week old FINRA data
is entirely sufficient for the >30% threshold decision. The key question is
"is this stock heavily shorted?" not "what happened this week?"

### CRITICAL INVARIANTS
1. short_float_pct = short_shares / float_shares × 100 (both needed)
2. >30% short float + gapping up → SQUEEZE (override all standard logic)
3. >20% short float → HIGH_SHORT (caution, wider stops, no shorting)
4. Data latency up to 2-3 weeks is acceptable — not stale, it's structural
5. Cache TTL = 4 hours (bi-monthly data doesn't change intraday)
6. All tiers are tried in order; first non-None short_float_pct wins

Ref: docs/decisions/D192_short_interest.md
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Cache TTL: 4 hours. Short interest is bi-monthly — no reason to re-fetch intraday.
CACHE_TTL_SECONDS = 4 * 3600

# HTTP timeouts per tier (seconds). Finviz is slower/flakier than Alpaca.
ALPACA_TIMEOUT_SECONDS = 5.0
YFINANCE_TIMEOUT_SECONDS = 10.0
FINVIZ_TIMEOUT_SECONDS = 15.0

# Finviz anti-scraping: spoof a real browser session to avoid 403 responses.
FINVIZ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://finviz.com/",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


# ── Classification ────────────────────────────────────────────────────────────


class SqueezeClassification(str, Enum):
    """
    Classification of a stock's squeeze potential.

    Applied after combining short float % (structural) with gap % (trigger).
    The faller detector uses this to adjust score and block shorts.

    Ordering (most → least squeeze risk):
        SQUEEZE > HIGH_SHORT > NORMAL > UNKNOWN
    """

    SQUEEZE = "squeeze"        # >30% short float AND gapping up → forced covering
    HIGH_SHORT = "high_short"  # >20% short float → caution, widen stops, no shorting
    NORMAL = "normal"          # <20% short float → standard logic applies
    UNKNOWN = "unknown"        # Couldn't determine — all tiers failed


# ── Result Model ──────────────────────────────────────────────────────────────


@dataclass
class ShortInterestResult:
    """
    Short interest data for a single ticker from any tier of the pipeline.

    The key metric is short_float_pct. Everything else is supplementary.
    classification is set by ShortInterestProvider.classify() after fetch.
    """

    ticker: str
    short_float_pct: Optional[float] = None  # Primary metric: short shares / float × 100
    short_shares: Optional[int] = None        # Raw short share count (when available)
    float_shares: Optional[int] = None        # Float shares (when available)
    days_to_cover: Optional[float] = None     # Short interest / avg daily volume
    classification: SqueezeClassification = SqueezeClassification.UNKNOWN
    data_source: str = "unknown"              # Which tier provided the data
    data_age_days: Optional[int] = None       # How old is the data (latency context)
    fetch_latency_ms: float = 0.0
    error: Optional[str] = None

    @property
    def is_squeeze_candidate(self) -> bool:
        return self.classification == SqueezeClassification.SQUEEZE

    @property
    def is_high_short(self) -> bool:
        return self.classification in (
            SqueezeClassification.SQUEEZE,
            SqueezeClassification.HIGH_SHORT,
        )

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "short_float_pct": self.short_float_pct,
            "short_shares": self.short_shares,
            "float_shares": self.float_shares,
            "days_to_cover": self.days_to_cover,
            "classification": self.classification.value,
            "data_source": self.data_source,
            "data_age_days": self.data_age_days,
            "fetch_latency_ms": round(self.fetch_latency_ms, 1),
            "error": self.error,
        }


# ── Cache Entry ───────────────────────────────────────────────────────────────


@dataclass
class _CacheEntry:
    result: ShortInterestResult
    fetched_at: float  # time.monotonic() when cached


# ── Provider ─────────────────────────────────────────────────────────────────


class ShortInterestProvider:
    """
    Multi-tier short interest data pipeline with in-memory cache.

    Tier fallback order:
        1. Alpaca Market Data V2 (primary — already authenticated)
        2. yfinance batch extraction (secondary — free, off-hours)
        3. Finviz DOM scraping (tertiary — free, anti-scraping headers)
        4. FINRA Query API (future — stub returning None)

    Cache: 4-hour TTL. Short interest data is bi-monthly — re-fetching
    within a trading session provides no new information.

    Usage:
        provider = ShortInterestProvider(alpaca_api_key=key, alpaca_secret=secret)
        result = await provider.get_short_interest("ITRM")
        if result.is_squeeze_candidate:
            # Block shorting, widen stops, accelerate profit-take
    """

    SQUEEZE_THRESHOLD = 30.0     # >30% short float + gap → SQUEEZE archetype
    HIGH_SHORT_THRESHOLD = 20.0  # >20% short float → HIGH_SHORT caution

    def __init__(
        self,
        alpaca_api_key: Optional[str] = None,
        alpaca_secret: Optional[str] = None,
    ) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._alpaca_key = alpaca_api_key
        self._alpaca_secret = alpaca_secret

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    async def get_short_interest(self, ticker: str) -> ShortInterestResult:
        """
        Get short interest for a single ticker using tiered fallback.

        Returns a ShortInterestResult. If all tiers fail, returns a result
        with classification=UNKNOWN and an error message. Never raises.
        """
        cached = self._get_cached(ticker)
        if cached is not None:
            return cached

        # Tier 1: Alpaca
        result = await self._fetch_alpaca(ticker)
        if result is not None and result.short_float_pct is not None:
            self._store_cache(ticker, result)
            return result

        # Tier 2: yfinance
        result = await self._fetch_yfinance(ticker)
        if result is not None and result.short_float_pct is not None:
            self._store_cache(ticker, result)
            return result

        # Tier 3: Finviz
        result = await self._fetch_finviz(ticker)
        if result is not None and result.short_float_pct is not None:
            self._store_cache(ticker, result)
            return result

        # Tier 4: FINRA (stub — requires separate registration)
        result = await self._fetch_finra_stub(ticker)
        if result is not None and result.short_float_pct is not None:
            self._store_cache(ticker, result)
            return result

        # All tiers failed
        unknown = ShortInterestResult(
            ticker=ticker,
            classification=SqueezeClassification.UNKNOWN,
            data_source="all_tiers_failed",
            error="All data tiers failed to return short interest data",
        )
        self._store_cache(ticker, unknown)
        return unknown

    async def get_batch(self, tickers: list[str]) -> dict[str, ShortInterestResult]:
        """
        Fetch short interest for multiple tickers concurrently.

        Uses asyncio.gather for parallelism. Each ticker is fetched independently
        through the full tier cascade if not already cached.

        Returns: dict mapping ticker → ShortInterestResult.
        """
        tasks = [self.get_short_interest(ticker) for ticker in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, ShortInterestResult] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.warning("D192 batch fetch error for %s: %s", ticker, result)
                output[ticker] = ShortInterestResult(
                    ticker=ticker,
                    classification=SqueezeClassification.UNKNOWN,
                    data_source="batch_error",
                    error=str(result),
                )
            else:
                output[ticker] = result
        return output

    def classify(
        self, result: ShortInterestResult, gap_pct: float
    ) -> SqueezeClassification:
        """
        Classify a stock's squeeze potential given short interest + gap %.

        Args:
            result:  ShortInterestResult from get_short_interest().
            gap_pct: Current gap % from CandidateStock (e.g., 0.15 = 15%).
                     Uses decimal form to match CandidateStock.gap_pct convention.

        Returns:
            SqueezeClassification:
              - SQUEEZE    if short_float_pct >= 30.0 AND gap_pct > 0.05 (5%+)
              - HIGH_SHORT if short_float_pct >= 20.0
              - NORMAL     if short_float_pct < 20.0
              - UNKNOWN    if short_float_pct is None

        Note: gap_pct > 5% threshold filters out small technical gaps.
        A squeeze needs a meaningful gap to trigger forced covering.
        """
        if result.short_float_pct is None:
            return SqueezeClassification.UNKNOWN
        if result.short_float_pct >= self.SQUEEZE_THRESHOLD and gap_pct > 0.05:
            return SqueezeClassification.SQUEEZE
        if result.short_float_pct >= self.HIGH_SHORT_THRESHOLD:
            return SqueezeClassification.HIGH_SHORT
        return SqueezeClassification.NORMAL

    def invalidate(self, ticker: str) -> None:
        """Remove a ticker from the cache (for testing or forced refresh)."""
        self._cache.pop(ticker, None)

    def clear_cache(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    # ─────────────────────────────────────────────────────────────────
    # Tier 1: Alpaca Market Data V2
    # ─────────────────────────────────────────────────────────────────

    async def _fetch_alpaca(self, ticker: str) -> Optional[ShortInterestResult]:
        """
        Tier 1: Alpaca Market Data V2 short interest endpoint.

        Alpaca's /v1beta1/stocks/{ticker}/short-interest endpoint (if available)
        returns FINRA consolidated short interest with settlement date.

        If the endpoint doesn't exist or returns 404, falls through to yfinance.
        Requires ALPACA_API_KEY + ALPACA_SECRET credentials.

        Endpoint: https://data.alpaca.markets/v1beta1/stocks/{ticker}/short-interest
        Auth: APCA-API-KEY-ID + APCA-API-SECRET-KEY headers
        """
        if not self._alpaca_key or not self._alpaca_secret:
            logger.debug("D192 Alpaca tier skipped: no credentials configured")
            return None

        try:
            import httpx

            url = f"https://data.alpaca.markets/v1beta1/stocks/{ticker}/short-interest"
            headers = {
                "APCA-API-KEY-ID": self._alpaca_key,
                "APCA-API-SECRET-KEY": self._alpaca_secret,
            }

            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=ALPACA_TIMEOUT_SECONDS) as client:
                resp = await client.get(url, headers=headers)

            latency_ms = (time.monotonic() - t0) * 1000

            if resp.status_code == 404:
                # Endpoint doesn't exist for this ticker or plan
                logger.debug("D192 Alpaca: 404 for %s — falling to Tier 2", ticker)
                return None
            if resp.status_code == 403:
                logger.debug("D192 Alpaca: 403 (plan restriction) — falling to Tier 2")
                return None
            if resp.status_code != 200:
                logger.warning(
                    "D192 Alpaca: HTTP %d for %s — falling to Tier 2",
                    resp.status_code,
                    ticker,
                )
                return None

            data = resp.json()

            # Response shape: {"short_interest": [{"date": "...", "short_interest": N,
            #                                       "short_exempt_interest": N}]}
            # We need to combine with float shares from asset endpoint to compute short_float_pct.
            # If the API returns short_float_pct directly, use it.
            items = data.get("short_interest") or []
            if not items:
                return None

            latest = items[0]  # Most recent settlement
            short_shares = latest.get("short_interest")
            settlement_date = latest.get("date", "unknown")

            # Try to get float shares from Alpaca assets endpoint for the ratio
            float_shares = await self._fetch_alpaca_float(ticker, headers)
            if short_shares is None:
                return None

            short_float_pct: Optional[float] = None
            if float_shares and float_shares > 0:
                short_float_pct = (short_shares / float_shares) * 100.0

            result = ShortInterestResult(
                ticker=ticker,
                short_float_pct=short_float_pct,
                short_shares=int(short_shares) if short_shares else None,
                float_shares=int(float_shares) if float_shares else None,
                data_source="alpaca",
                fetch_latency_ms=latency_ms,
            )

            # Estimate data age from settlement date
            result.data_age_days = _estimate_age_days(settlement_date)

            logger.info(
                "D192 Alpaca: %s short_float=%.1f%% (settlement=%s) in %.0fms",
                ticker,
                short_float_pct or 0.0,
                settlement_date,
                latency_ms,
            )
            return result

        except Exception as exc:
            logger.debug("D192 Alpaca tier failed for %s: %s", ticker, exc)
            return None

    async def _fetch_alpaca_float(
        self, ticker: str, headers: dict
    ) -> Optional[float]:
        """Fetch float shares from Alpaca asset endpoint (supplementary for Tier 1)."""
        try:
            import httpx

            url = f"https://data.alpaca.markets/v1beta1/stocks/{ticker}/snapshot"
            async with httpx.AsyncClient(timeout=ALPACA_TIMEOUT_SECONDS) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
            # Alpaca snapshot doesn't expose float directly — return None, caller handles
            return data.get("float_shares") or data.get("floatShares")
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────────────
    # Tier 2: yfinance
    # ─────────────────────────────────────────────────────────────────

    async def _fetch_yfinance(self, ticker: str) -> Optional[ShortInterestResult]:
        """
        Tier 2: yfinance — shortPercentOfFloat from Ticker.info.

        yfinance pulls data from Yahoo Finance's unofficial API. The
        shortPercentOfFloat field is a decimal (0.30 = 30%).

        This is the most reliable free tier for short float %. Yahoo Finance
        sources from FINRA/exchange filings with 2-3 week latency.

        Wraps the blocking yfinance call in a thread executor to avoid
        blocking the asyncio event loop.
        """
        try:
            import yfinance as yf

            t0 = time.monotonic()

            def _blocking_fetch() -> dict:
                ticker_obj = yf.Ticker(ticker)
                return ticker_obj.info or {}

            loop = asyncio.get_event_loop()
            info = await asyncio.wait_for(
                loop.run_in_executor(None, _blocking_fetch),
                timeout=YFINANCE_TIMEOUT_SECONDS,
            )

            latency_ms = (time.monotonic() - t0) * 1000

            # shortPercentOfFloat is a decimal: 0.30 = 30%
            raw_pct = info.get("shortPercentOfFloat")
            if raw_pct is None:
                logger.debug("D192 yfinance: shortPercentOfFloat=None for %s", ticker)
                return None

            short_float_pct = float(raw_pct) * 100.0  # Convert decimal to percentage

            # Supplementary fields (when available)
            raw_short_shares = info.get("sharesShort")
            raw_float = info.get("floatShares")
            short_shares = int(raw_short_shares) if raw_short_shares else None
            float_shares = int(raw_float) if raw_float else None

            # Days to cover = sharesShort / avg daily volume
            avg_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day")
            days_to_cover: Optional[float] = None
            if short_shares and avg_volume and avg_volume > 0:
                days_to_cover = short_shares / avg_volume

            result = ShortInterestResult(
                ticker=ticker,
                short_float_pct=round(short_float_pct, 2),
                short_shares=short_shares,
                float_shares=float_shares,
                days_to_cover=round(days_to_cover, 2) if days_to_cover else None,
                data_source="yfinance",
                fetch_latency_ms=latency_ms,
            )

            logger.info(
                "D192 yfinance: %s short_float=%.1f%% dtc=%.1f in %.0fms",
                ticker,
                short_float_pct,
                days_to_cover or 0.0,
                latency_ms,
            )
            return result

        except asyncio.TimeoutError:
            logger.warning("D192 yfinance: timeout for %s — falling to Tier 3", ticker)
            return None
        except Exception as exc:
            logger.debug("D192 yfinance tier failed for %s: %s", ticker, exc)
            return None

    # ─────────────────────────────────────────────────────────────────
    # Tier 3: Finviz scraping
    # ─────────────────────────────────────────────────────────────────

    async def _fetch_finviz(self, ticker: str) -> Optional[ShortInterestResult]:
        """
        Tier 3: Finviz DOM scraping — "Short Float" cell from quote page.

        Finviz displays short float % prominently in their screener table.
        The quote page at /quote.ashx?t={TICKER} has a structured HTML table
        with labeled cells.

        Anti-scraping measures needed:
          - Spoof browser User-Agent and Accept headers
          - Include Referer: https://finviz.com/ to pass hotlink checks
          - Handle 429 (rate limit) gracefully

        CSS selector: table.snapshot-table2 → find td with text "Short Float"
        → next sibling td contains the percentage (e.g., "32.45%").
        """
        try:
            import httpx
            from bs4 import BeautifulSoup

            url = f"https://finviz.com/quote.ashx?t={ticker}"
            t0 = time.monotonic()

            async with httpx.AsyncClient(
                timeout=FINVIZ_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, headers=FINVIZ_HEADERS)

            latency_ms = (time.monotonic() - t0) * 1000

            if resp.status_code == 429:
                logger.warning("D192 Finviz: rate-limited (429) for %s", ticker)
                return None
            if resp.status_code != 200:
                logger.debug(
                    "D192 Finviz: HTTP %d for %s", resp.status_code, ticker
                )
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            short_float_pct = _parse_finviz_short_float(soup, ticker)

            if short_float_pct is None:
                logger.debug("D192 Finviz: could not parse Short Float for %s", ticker)
                return None

            result = ShortInterestResult(
                ticker=ticker,
                short_float_pct=short_float_pct,
                data_source="finviz",
                fetch_latency_ms=latency_ms,
            )

            logger.info(
                "D192 Finviz: %s short_float=%.1f%% in %.0fms",
                ticker,
                short_float_pct,
                latency_ms,
            )
            return result

        except Exception as exc:
            logger.debug("D192 Finviz tier failed for %s: %s", ticker, exc)
            return None

    # ─────────────────────────────────────────────────────────────────
    # Tier 4: FINRA Query API (stub)
    # ─────────────────────────────────────────────────────────────────

    async def _fetch_finra_stub(self, ticker: str) -> Optional[ShortInterestResult]:
        """
        Tier 4: FINRA Query API — institutional-grade short interest data.

        FINRA publishes consolidated short interest twice monthly via their
        Query API (https://api.finra.org/data/group/otcMarket/name/equityShortInterest).

        This tier is a stub. To implement:
          1. Register at https://developer.finra.org/ (free, requires approval)
          2. Obtain OAuth2 client credentials
          3. Query: POST https://api.finra.org/data/group/finra/name/equity_short_interest
             with payload: {"compareFilters": [{"fieldName": "symbolCode",
                                                "compareType": "equal",
                                                "fieldValue": ticker}]}
          4. Parse response: shortInterestQty / totalFloat × 100

        Returns None until implemented (falls back to "all tiers failed").
        """
        logger.debug("D192 FINRA tier: not yet implemented (stub) for %s", ticker)
        return None

    # ─────────────────────────────────────────────────────────────────
    # Cache helpers
    # ─────────────────────────────────────────────────────────────────

    def _get_cached(self, ticker: str) -> Optional[ShortInterestResult]:
        entry = self._cache.get(ticker)
        if entry is None:
            return None
        age = time.monotonic() - entry.fetched_at
        if age > CACHE_TTL_SECONDS:
            del self._cache[ticker]
            return None
        logger.debug("D192 cache hit: %s (age=%.0fs)", ticker, age)
        return entry.result

    def _store_cache(self, ticker: str, result: ShortInterestResult) -> None:
        self._cache[ticker] = _CacheEntry(result=result, fetched_at=time.monotonic())
        # D221 Phase F: forward-only persistence (no-op when env-toggle is off).
        # Captures live fetches as JSONL shards so v3 training has historical data
        # for short_interest, which today has no backfill path.
        from src.data._feature_persistence import persist_feature_row
        persist_feature_row("short_interest", {
            "ticker": ticker,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "short_float_pct": result.short_float_pct,
            "short_shares": result.short_shares,
            "float_shares": result.float_shares,
            "days_to_cover": result.days_to_cover,
            "classification": result.classification.value if result.classification else None,
            "data_source": result.data_source,
            "data_age_days": result.data_age_days,
            "error": result.error,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_finviz_short_float(soup: "BeautifulSoup", ticker: str) -> Optional[float]:  # type: ignore[name-defined]
    """
    Extract "Short Float" value from Finviz quote page HTML.

    Finviz table structure:
        <table class="snapshot-table2">
          <tr>
            <td class="snapshot-td2-cp">Short Float</td>
            <td class="snapshot-td2">32.45%</td>
            ...
          </tr>
        </table>

    Searches all td elements for exact text "Short Float", then reads the
    next sibling td for the percentage value.
    """
    try:
        # Find the label cell
        label_cell = soup.find("td", string="Short Float")
        if label_cell is None:
            # Also try with whitespace variations
            for td in soup.find_all("td"):
                if td.get_text(strip=True) == "Short Float":
                    label_cell = td
                    break

        if label_cell is None:
            logger.debug("D192 Finviz parser: 'Short Float' label not found for %s", ticker)
            return None

        value_cell = label_cell.find_next_sibling("td")
        if value_cell is None:
            return None

        raw_text = value_cell.get_text(strip=True)
        if raw_text in ("-", "", "N/A"):
            return None

        # Strip the % sign and convert
        cleaned = raw_text.replace("%", "").replace(",", "").strip()
        return float(cleaned)

    except (ValueError, AttributeError) as exc:
        logger.debug("D192 Finviz parser: failed to parse '%s' for %s: %s", raw_text if 'raw_text' in dir() else "?", ticker, exc)
        return None


def _estimate_age_days(date_str: str) -> Optional[int]:
    """
    Estimate data age in days from a date string like '2026-03-15'.

    Returns None if the date cannot be parsed.
    """
    try:
        from datetime import date, datetime

        if not date_str or date_str == "unknown":
            return None
        parsed = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        return (date.today() - parsed).days
    except (ValueError, TypeError):
        return None
