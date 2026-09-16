"""Polygon.io REST integration for MOMENTUM-X.

Subscription: Stocks Advanced ($199/mo) — unlimited calls within reason,
real-time SIP, 10y history, NBBO ticks, options.

Public surface:
- PolygonClient: low-level httpx wrapper with cache + rate limiter
- PolygonEndpoints: typed wrappers for the endpoints we use
- Bar, Quote, Trade, TickerRef, FinancialReport: response dataclasses
"""
from .client import PolygonClient, PolygonAPIError, PolygonRateLimitError
from .endpoints import PolygonEndpoints
from .models import Bar, Quote, Trade, TickerRef, FinancialReport

__all__ = [
    "PolygonClient",
    "PolygonAPIError",
    "PolygonRateLimitError",
    "PolygonEndpoints",
    "Bar",
    "Quote",
    "Trade",
    "TickerRef",
    "FinancialReport",
]
