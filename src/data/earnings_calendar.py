"""
D216: Finnhub Earnings Calendar Integration

Identifies earnings-day gap-ups WITHOUT requiring headlines. If a stock
gaps +10% on the same day as scheduled earnings, it's almost certainly
an earnings beat — the strongest catalyst type for momentum.

Usage:
    cal = EarningsCalendar(finnhub_api_key="...")
    await cal.refresh()  # Fetch this week's earnings
    is_earnings, details = cal.check_ticker("AAPL")
    # (True, {"date": "2026-04-30", "epsEstimate": 1.98, "hour": "amc"})
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class EarningsCalendar:
    """Finnhub earnings calendar — free tier, 60 calls/min."""

    def __init__(self, finnhub_api_key: str = "") -> None:
        self._key = finnhub_api_key or os.environ.get("FINNHUB_API_KEY", "")
        self._earnings: dict[str, dict] = {}  # ticker -> {date, epsEstimate, ...}
        self._last_refresh: datetime | None = None

    async def refresh(self, lookback_days: int = 1, lookahead_days: int = 1) -> int:
        """Fetch earnings calendar. Call once at session start (Phase 0).

        Returns: number of earnings events loaded.
        """
        if not self._key:
            return 0

        try:
            import httpx
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            end = (datetime.now(timezone.utc) + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://finnhub.io/api/v1/calendar/earnings",
                    params={"from": start, "to": end, "token": self._key},
                )

            if r.status_code != 200:
                logger.warning("D216: Earnings calendar fetch failed: HTTP %d", r.status_code)
                return 0

            data = r.json()
            events = data.get("earningsCalendar", [])

            self._earnings.clear()
            for e in events:
                sym = e.get("symbol", "")
                if sym:
                    self._earnings[sym] = {
                        "date": e.get("date", ""),
                        "epsEstimate": e.get("epsEstimate"),
                        "epsActual": e.get("epsActual"),
                        "revenueEstimate": e.get("revenueEstimate"),
                        "revenueActual": e.get("revenueActual"),
                        "hour": e.get("hour", ""),  # "bmo" (before market open) or "amc" (after market close)
                        "quarter": e.get("quarter"),
                    }

            self._last_refresh = datetime.now(timezone.utc)
            logger.info(
                "D216: Earnings calendar loaded — %d events (%s to %s)",
                len(self._earnings), start, end,
            )
            return len(self._earnings)

        except Exception as e:
            logger.warning("D216: Earnings calendar error: %s", e)
            return 0

    def check_ticker(self, ticker: str) -> tuple[bool, dict | None]:
        """Check if a ticker has earnings today.

        Returns: (is_earnings_day, details_dict_or_None)
        """
        entry = self._earnings.get(ticker)
        if entry is None:
            return False, None

        # Use ET timezone — Finnhub dates are in the company's trading timezone
        try:
            from zoneinfo import ZoneInfo
            _et = ZoneInfo("America/New_York")
        except ImportError:
            _et = timezone(timedelta(hours=-4))  # Fallback to EDT
        _now_et = datetime.now(_et)
        today = _now_et.strftime("%Y-%m-%d")
        yesterday = (_now_et - timedelta(days=1)).strftime("%Y-%m-%d")

        # Match today or yesterday (for "amc" earnings reported after yesterday's close)
        if entry["date"] in (today, yesterday):
            return True, entry

        return False, None

    def get_earnings_tickers(self) -> set[str]:
        """Get all tickers with earnings today/yesterday."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            sym for sym, e in self._earnings.items()
            if e["date"] in (today, yesterday)
        }

    @property
    def loaded(self) -> bool:
        return self._last_refresh is not None and len(self._earnings) > 0
