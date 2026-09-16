"""Polygon response dataclasses.

Frozen + slotted for memory efficiency on tick-level pulls (millions of
rows). Each model maps a Polygon JSON object to a typed Python record;
parsing happens in `endpoints.py` via `from_json` classmethods.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class Bar:
    """Aggregate bar (1m, 5m, 1d). Polygon `/v2/aggs/ticker/{t}/range/...`.

    Fields per Polygon schema:
    - t: Unix ms epoch (start of bar)
    - o, h, l, c: OHLC floats
    - v: total volume
    - vw: volume-weighted avg price (may be None on illiquid bars)
    - n: trade count (may be None)
    """
    ticker: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float]
    trade_count: Optional[int]

    @classmethod
    def from_json(cls, ticker: str, obj: dict) -> "Bar":
        return cls(
            ticker=ticker,
            timestamp_ms=int(obj["t"]),
            open=float(obj["o"]),
            high=float(obj["h"]),
            low=float(obj["l"]),
            close=float(obj["c"]),
            volume=float(obj["v"]),
            vwap=float(obj["vw"]) if obj.get("vw") is not None else None,
            trade_count=int(obj["n"]) if obj.get("n") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class Quote:
    """NBBO quote tick. Polygon `/v3/quotes/{ticker}`.

    sip_timestamp is canonical (consolidated tape); participant_timestamp
    is exchange-side (often slightly earlier). Both nanosecond ints.
    """
    ticker: str
    sip_timestamp_ns: int
    participant_timestamp_ns: Optional[int]
    bid_price: float
    bid_size: int
    bid_exchange: int
    ask_price: float
    ask_size: int
    ask_exchange: int
    conditions: tuple[int, ...]

    @classmethod
    def from_json(cls, ticker: str, obj: dict) -> "Quote":
        return cls(
            ticker=ticker,
            sip_timestamp_ns=int(obj["sip_timestamp"]),
            participant_timestamp_ns=int(obj["participant_timestamp"])
                if obj.get("participant_timestamp") is not None else None,
            bid_price=float(obj.get("bid_price") or 0.0),
            bid_size=int(obj.get("bid_size") or 0),
            bid_exchange=int(obj.get("bid_exchange") or 0),
            ask_price=float(obj.get("ask_price") or 0.0),
            ask_size=int(obj.get("ask_size") or 0),
            ask_exchange=int(obj.get("ask_exchange") or 0),
            conditions=tuple(int(c) for c in (obj.get("conditions") or [])),
        )

    @property
    def midpoint(self) -> float:
        """NBBO mid. Returns 0.0 if either side is missing/zero."""
        if self.bid_price > 0 and self.ask_price > 0:
            return (self.bid_price + self.ask_price) / 2.0
        return 0.0


@dataclass(frozen=True, slots=True)
class Trade:
    """Trade tick. Polygon `/v3/trades/{ticker}`."""
    ticker: str
    sip_timestamp_ns: int
    participant_timestamp_ns: Optional[int]
    price: float
    size: int
    exchange: int
    conditions: tuple[int, ...]
    trade_id: str

    @classmethod
    def from_json(cls, ticker: str, obj: dict) -> "Trade":
        return cls(
            ticker=ticker,
            sip_timestamp_ns=int(obj["sip_timestamp"]),
            participant_timestamp_ns=int(obj["participant_timestamp"])
                if obj.get("participant_timestamp") is not None else None,
            price=float(obj["price"]),
            size=int(obj["size"]),
            exchange=int(obj.get("exchange") or 0),
            conditions=tuple(int(c) for c in (obj.get("conditions") or [])),
            trade_id=str(obj.get("id") or ""),
        )


@dataclass(frozen=True, slots=True)
class TickerRef:
    """Reference data per ticker. Polygon `/v3/reference/tickers[/...]`."""
    ticker: str
    name: str
    market: str
    locale: str
    primary_exchange: Optional[str]
    type: Optional[str]
    active: bool
    currency_name: str
    cik: Optional[str]
    composite_figi: Optional[str]
    share_class_figi: Optional[str]
    market_cap: Optional[float]
    weighted_shares_outstanding: Optional[float]

    @classmethod
    def from_json(cls, obj: dict) -> "TickerRef":
        return cls(
            ticker=str(obj["ticker"]),
            name=str(obj.get("name") or ""),
            market=str(obj.get("market") or ""),
            locale=str(obj.get("locale") or ""),
            primary_exchange=obj.get("primary_exchange"),
            type=obj.get("type"),
            active=bool(obj.get("active", True)),
            currency_name=str(obj.get("currency_name") or "usd"),
            cik=obj.get("cik"),
            composite_figi=obj.get("composite_figi"),
            share_class_figi=obj.get("share_class_figi"),
            market_cap=float(obj["market_cap"]) if obj.get("market_cap") is not None else None,
            weighted_shares_outstanding=float(obj["weighted_shares_outstanding"])
                if obj.get("weighted_shares_outstanding") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class FinancialReport:
    """Quarterly/annual financial report. Polygon `/vX/reference/financials`.

    Subset of fields needed for MAGNA-N rubric; full schema is much larger.
    """
    ticker: str
    fiscal_period: str
    fiscal_year: str
    start_date: str
    end_date: str
    timeframe: str
    revenues: Optional[float]
    net_income_loss: Optional[float]
    operating_income_loss: Optional[float]
    diluted_earnings_per_share: Optional[float]

    @classmethod
    def from_json(cls, ticker: str, obj: dict) -> "FinancialReport":
        fin = obj.get("financials") or {}
        income = fin.get("income_statement") or {}

        def _val(node: dict, key: str) -> Optional[float]:
            v = (node.get(key) or {}).get("value")
            return float(v) if v is not None else None

        return cls(
            ticker=ticker,
            fiscal_period=str(obj.get("fiscal_period") or ""),
            fiscal_year=str(obj.get("fiscal_year") or ""),
            start_date=str(obj.get("start_date") or ""),
            end_date=str(obj.get("end_date") or ""),
            timeframe=str(obj.get("timeframe") or ""),
            revenues=_val(income, "revenues"),
            net_income_loss=_val(income, "net_income_loss"),
            operating_income_loss=_val(income, "operating_income_loss"),
            diluted_earnings_per_share=_val(income, "diluted_earnings_per_share"),
        )
