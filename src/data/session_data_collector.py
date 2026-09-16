"""D210: Comprehensive Session Data Collector

Records ALL available data for EVERY watchlist stock during each session,
regardless of whether the stock was traded. This creates a complete data
lake for post-session analysis, ML training, and arena replay.

Data captured per ticker:
  - Scanner metadata (gap, RVOL, dolvol, mcap, float)
  - Agent evaluation results (MFCS, signals, debate outcome)
  - SEC filing status (filing type, dilution risk)
  - Short interest (float short %, days-to-cover)
  - Sentiment velocity (headline count, velocity score)
  - Rejection reasons (why each NO_TRADE was issued)
  - Trade result (entry, exit, P&L, stop details)
  - Minute bars (full session OHLCV for price-path replay)

Storage layout:
  data/historical_collection/YYYY-MM-DD/TICKER.json   ← per-ticker record
  data/historical_collection/YYYY-MM-DD/session_summary.json ← day summary
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_COLLECTION_ROOT = Path("data/historical_collection")


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class TickerSessionData:
    """Complete data record for one ticker in one session."""

    # ── Identity ──────────────────────────────────────────────────────────────
    ticker: str
    session_date: str                           # "YYYY-MM-DD"
    recorded_at: str = ""                       # ISO UTC when record was last written

    # ── Scanner metadata ──────────────────────────────────────────────────────
    gap_pct: float | None = None
    rvol: float | None = None
    dolvol: float | None = None                 # dollar volume (price × volume)
    market_cap: float | None = None
    float_shares: float | None = None
    current_price: float | None = None
    previous_close: float | None = None
    bid: float | None = None
    ask: float | None = None
    premarket_volume: int | None = None

    # ── Agent evaluation ──────────────────────────────────────────────────────
    mfcs: float | None = None                   # Multi-Factor Composite Score
    mfcs_components: dict[str, float] = field(default_factory=dict)
    qualifies_debate: bool = False
    debate_outcome: str | None = None           # "BUY" / "NO_TRADE" / None
    debate_confidence: float | None = None
    final_verdict: str | None = None            # "BUY" / "NO_TRADE" / "SKIP"
    agent_signals: list[dict[str, Any]] = field(default_factory=list)
    faller_risk_score: float | None = None
    faller_classification: str | None = None    # "safe" / "mild" / "severe"

    # ── SEC filing status ─────────────────────────────────────────────────────
    sec_has_recent_filing: bool | None = None
    sec_filing_type: str | None = None          # "S-3", "424B", "8-K", etc.
    sec_dilution_risk: str | None = None        # "high" / "low" / "none"
    sec_filing_date: str | None = None

    # ── Short interest ────────────────────────────────────────────────────────
    short_interest_pct: float | None = None     # % of float short
    days_to_cover: float | None = None
    short_interest_source: str | None = None

    # ── Sentiment velocity ────────────────────────────────────────────────────
    sentiment_score: float | None = None
    sentiment_velocity: float | None = None     # change in score per hour
    headline_count: int | None = None
    sentiment_source: str | None = None

    # ── Rejection reasons ────────────────────────────────────────────────────
    rejection_reasons: list[str] = field(default_factory=list)
    rejection_agent: str | None = None          # which agent issued the veto

    # ── Trade result ─────────────────────────────────────────────────────────
    traded: bool = False
    entry_price: float | None = None
    exit_price: float | None = None
    shares: int | None = None
    pnl_dollars: float | None = None
    pnl_pct: float | None = None
    stop_price: float | None = None
    exit_reason: str | None = None              # "stop", "target", "eod", "manual"
    hold_minutes: int | None = None

    # ── Minute bars ──────────────────────────────────────────────────────────
    minute_bars: list[dict[str, Any]] = field(default_factory=list)
    bars_fetched_at: str | None = None

    # ── Metadata ─────────────────────────────────────────────────────────────
    eval_count: int = 0                         # how many times this ticker was evaluated
    notes: list[str] = field(default_factory=list)


# =============================================================================
# Collector
# =============================================================================


class SessionDataCollector:
    """
    Central store for all session data. Instantiated once per trading day in main.py.

    Usage pattern:
        collector = SessionDataCollector()
        # On each candidate scan hit:
        collector.register_candidate(candidate)
        # After agent evaluation:
        collector.record_evaluation(ticker, verdict, mfcs, signals, ...)
        # After rejection:
        collector.record_rejection(ticker, reasons, agent)
        # After trade:
        collector.record_trade(ticker, entry, exit, pnl, ...)
        # EOD:
        await collector.fetch_and_store_bars(client, tickers)
        collector.save_session()
    """

    def __init__(
        self,
        session_date: date | None = None,
        storage_root: Path | str = _COLLECTION_ROOT,
    ) -> None:
        self._date = session_date or date.today()
        self._root = Path(storage_root) / self._date.isoformat()
        self._records: dict[str, TickerSessionData] = {}
        logger.info("D210: SessionDataCollector initialized -> %s", self._root)

    # ── Registration ──────────────────────────────────────────────────────────

    def register_candidate(self, candidate: Any) -> None:
        """Record a CandidateStock that passed the scanner filter."""
        ticker = candidate.ticker
        if ticker not in self._records:
            self._records[ticker] = TickerSessionData(
                ticker=ticker,
                session_date=self._date.isoformat(),
            )
        rec = self._records[ticker]
        rec.gap_pct = getattr(candidate, "gap_pct", None)
        rec.rvol = getattr(candidate, "rvol", None)
        rec.current_price = getattr(candidate, "current_price", None)
        rec.previous_close = getattr(candidate, "previous_close", None)
        rec.float_shares = getattr(candidate, "float_shares", None)
        rec.market_cap = getattr(candidate, "market_cap", None)
        rec.bid = getattr(candidate, "bid", None)
        rec.ask = getattr(candidate, "ask", None)
        rec.premarket_volume = getattr(candidate, "premarket_volume", None)
        # dollar volume: price × premarket_volume (rough proxy)
        if rec.current_price and rec.premarket_volume:
            rec.dolvol = rec.current_price * rec.premarket_volume
        logger.debug("D210: registered candidate %s (gap=%.1f%%, rvol=%.1fx)",
                     ticker,
                     (rec.gap_pct or 0) * 100,
                     rec.rvol or 0)

    # ── Evaluation recording ──────────────────────────────────────────────────

    def record_evaluation(
        self,
        ticker: str,
        verdict: str,
        mfcs: float | None = None,
        mfcs_components: dict[str, float] | None = None,
        agent_signals: list[Any] | None = None,
        qualifies_debate: bool = False,
        debate_outcome: str | None = None,
        debate_confidence: float | None = None,
        faller_risk_score: float | None = None,
        faller_classification: str | None = None,
    ) -> None:
        """Record full agent evaluation result for a ticker."""
        rec = self._get_or_create(ticker)
        rec.eval_count += 1
        rec.final_verdict = verdict
        if mfcs is not None:
            rec.mfcs = mfcs
        if mfcs_components:
            rec.mfcs_components = dict(mfcs_components)
        rec.qualifies_debate = qualifies_debate
        if debate_outcome is not None:
            rec.debate_outcome = debate_outcome
        if debate_confidence is not None:
            rec.debate_confidence = debate_confidence
        if faller_risk_score is not None:
            rec.faller_risk_score = faller_risk_score
        if faller_classification is not None:
            rec.faller_classification = faller_classification
        if agent_signals:
            rec.agent_signals = [_serialize_signal(s) for s in agent_signals]
        logger.debug("D210: recorded evaluation %s verdict=%s mfcs=%.3f",
                     ticker, verdict, mfcs or 0.0)

    # ── SEC data ──────────────────────────────────────────────────────────────

    def record_sec_data(
        self,
        ticker: str,
        has_recent_filing: bool | None = None,
        filing_type: str | None = None,
        dilution_risk: str | None = None,
        filing_date: str | None = None,
    ) -> None:
        rec = self._get_or_create(ticker)
        rec.sec_has_recent_filing = has_recent_filing
        rec.sec_filing_type = filing_type
        rec.sec_dilution_risk = dilution_risk
        rec.sec_filing_date = filing_date

    # ── Short interest ────────────────────────────────────────────────────────

    def record_short_interest(
        self,
        ticker: str,
        short_pct: float | None = None,
        days_to_cover: float | None = None,
        source: str | None = None,
    ) -> None:
        rec = self._get_or_create(ticker)
        rec.short_interest_pct = short_pct
        rec.days_to_cover = days_to_cover
        rec.short_interest_source = source

    # ── Sentiment ─────────────────────────────────────────────────────────────

    def record_sentiment(
        self,
        ticker: str,
        score: float | None = None,
        velocity: float | None = None,
        headline_count: int | None = None,
        source: str | None = None,
    ) -> None:
        rec = self._get_or_create(ticker)
        rec.sentiment_score = score
        rec.sentiment_velocity = velocity
        rec.headline_count = headline_count
        rec.sentiment_source = source

    # ── Rejection ─────────────────────────────────────────────────────────────

    def record_rejection(
        self,
        ticker: str,
        reasons: list[str] | None = None,
        agent: str | None = None,
    ) -> None:
        rec = self._get_or_create(ticker)
        if reasons:
            rec.rejection_reasons.extend(reasons)
        if agent:
            rec.rejection_agent = agent

    # ── Trade result ──────────────────────────────────────────────────────────

    def record_trade(
        self,
        ticker: str,
        entry_price: float,
        exit_price: float | None = None,
        shares: int | None = None,
        pnl_dollars: float | None = None,
        pnl_pct: float | None = None,
        stop_price: float | None = None,
        exit_reason: str | None = None,
        hold_minutes: int | None = None,
    ) -> None:
        rec = self._get_or_create(ticker)
        rec.traded = True
        rec.entry_price = entry_price
        rec.exit_price = exit_price
        rec.shares = shares
        rec.pnl_dollars = pnl_dollars
        rec.pnl_pct = pnl_pct
        rec.stop_price = stop_price
        rec.exit_reason = exit_reason
        rec.hold_minutes = hold_minutes

    # ── Bar fetching ──────────────────────────────────────────────────────────

    async def fetch_and_store_bars(
        self,
        client: Any,
        tickers: list[str] | None = None,
        session_date: date | None = None,
    ) -> None:
        """
        Fetch minute bars for all registered (or specified) tickers and store
        in each TickerSessionData. Call once EOD after market close.

        Args:
            client: AlpacaDataClient instance.
            tickers: Override list of tickers; defaults to all registered tickers.
            session_date: Date to fetch bars for; defaults to self._date.
        """
        fetch_date = session_date or self._date
        target_tickers = tickers or list(self._records.keys())
        if not target_tickers:
            logger.warning("D210: fetch_and_store_bars called with no tickers")
            return

        # Build session window: 09:30–16:00 ET in UTC
        from zoneinfo import ZoneInfo
        tz_et = ZoneInfo("America/New_York")
        day_start = datetime(fetch_date.year, fetch_date.month, fetch_date.day,
                             9, 30, tzinfo=tz_et).astimezone(timezone.utc)
        day_end = datetime(fetch_date.year, fetch_date.month, fetch_date.day,
                           16, 0, tzinfo=tz_et).astimezone(timezone.utc)

        logger.info("D210: fetching minute bars for %d tickers on %s",
                    len(target_tickers), fetch_date.isoformat())

        for ticker in target_tickers:
            try:
                bars = await client.get_bars(
                    symbol=ticker,
                    timeframe="1Min",
                    limit=500,
                    start=day_start.isoformat(),
                    end=day_end.isoformat(),
                )
                rec = self._get_or_create(ticker)
                rec.minute_bars = bars
                rec.bars_fetched_at = datetime.now(timezone.utc).isoformat()
                logger.debug("D210: %s — %d bars fetched", ticker, len(bars))
            except Exception as e:
                logger.warning("D210: bar fetch failed for %s: %s", ticker, e)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_session(self) -> None:
        """Write per-ticker JSON files + session_summary.json to storage_root/date/."""
        self._root.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.now(timezone.utc).isoformat()

        for ticker, rec in self._records.items():
            rec.recorded_at = now_iso
            out_path = self._root / f"{ticker}.json"
            try:
                out_path.write_text(
                    json.dumps(asdict(rec), indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.warning("D210: failed to write %s: %s", out_path, e)

        # Session summary
        summary = {
            "session_date": self._date.isoformat(),
            "recorded_at": now_iso,
            "total_candidates": len(self._records),
            "total_evaluated": sum(1 for r in self._records.values() if r.eval_count > 0),
            "total_traded": sum(1 for r in self._records.values() if r.traded),
            "tickers": sorted(self._records.keys()),
            "verdicts": {
                t: r.final_verdict for t, r in self._records.items()
                if r.final_verdict
            },
            "mfcs": {
                t: r.mfcs for t, r in self._records.items()
                if r.mfcs is not None
            },
            "pnl": {
                t: r.pnl_dollars for t, r in self._records.items()
                if r.pnl_dollars is not None
            },
        }
        summary_path = self._root / "session_summary.json"
        try:
            summary_path.write_text(
                json.dumps(summary, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("D210: failed to write session_summary.json: %s", e)

        logger.info(
            "D210: session saved -> %s (%d tickers, %d traded, %d with bars)",
            self._root,
            len(self._records),
            summary["total_traded"],
            sum(1 for r in self._records.values() if r.minute_bars),
        )

    @classmethod
    def load_session(cls, session_date: date, storage_root: Path | str = _COLLECTION_ROOT) -> "SessionDataCollector":
        """Load a previously saved session back into memory."""
        collector = cls(session_date=session_date, storage_root=storage_root)
        root = Path(storage_root) / session_date.isoformat()
        if not root.exists():
            logger.warning("D210: no saved session at %s", root)
            return collector
        for json_file in root.glob("*.json"):
            if json_file.name == "session_summary.json":
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                ticker = data.get("ticker", json_file.stem)
                collector._records[ticker] = TickerSessionData(**data)
            except Exception as e:
                logger.warning("D210: failed to load %s: %s", json_file, e)
        logger.info("D210: loaded session %s (%d tickers)",
                    session_date.isoformat(), len(collector._records))
        return collector

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_record(self, ticker: str) -> TickerSessionData | None:
        return self._records.get(ticker)

    def all_records(self) -> dict[str, TickerSessionData]:
        return dict(self._records)

    @property
    def tickers(self) -> list[str]:
        return sorted(self._records.keys())

    # ── Internals ─────────────────────────────────────────────────────────────

    def _get_or_create(self, ticker: str) -> TickerSessionData:
        if ticker not in self._records:
            self._records[ticker] = TickerSessionData(
                ticker=ticker,
                session_date=self._date.isoformat(),
            )
        return self._records[ticker]


# =============================================================================
# Helpers
# =============================================================================


def _serialize_signal(signal: Any) -> dict[str, Any]:
    """Convert an AgentSignal (or any object with __dict__) to a JSON-safe dict."""
    if isinstance(signal, dict):
        return signal
    if hasattr(signal, "__dataclass_fields__"):
        try:
            from dataclasses import asdict as _asdict
            return _asdict(signal)
        except Exception:
            pass
    if hasattr(signal, "__dict__"):
        return {k: str(v) for k, v in signal.__dict__.items()
                if not k.startswith("_")}
    return {"raw": str(signal)}
