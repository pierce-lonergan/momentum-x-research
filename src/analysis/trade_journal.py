"""
MOMENTUM-X Trade Journal — Comprehensive Data Capture for Backtesting

### ARCHITECTURAL CONTEXT
Node ID: analysis.trade_journal
Graph Link: docs/memory/graph_state.json → "analysis.trade_journal"

### PURPOSE
Captures the COMPLETE data context for every candidate evaluation during
paper/live trading sessions. This data enables:
  1. Full agent signal replay (reasoning, confidence, key_data, sources)
  2. MFCS component score attribution per evaluation
  3. Input data provenance (what news/indicators each agent received)
  4. Debate transcript preservation (bull/bear/judge reasoning)
  5. Risk veto reasoning and data quality assessment
  6. Position lifecycle tracking (entry → tranches → exit → P&L)
  7. Shapley attribution at close

Every field logged here was previously ephemeral (logged to stdout, then lost).
The TradeJournal persists it to disk for future CPCV backtesting and strategy tuning.

### CRITICAL INVARIANTS
1. Journal entries are append-only during a session.
2. Journal is flushed to disk at every evaluation (no data loss on crash).
3. All timestamps are UTC ISO-8601.
4. Journal file per session: data/journals/journal_{DATE}_{TIMESTAMP}.jsonl
5. JSONL format (one JSON object per line) for streaming writes.

Ref: ADR-019 (Full Observability)
Ref: MOMENTUM_LOGIC.md §17 (Post-trade Analysis)
"""

from __future__ import annotations

import src.utils.fast_json as json  # D87: orjson drop-in (~3-10x faster)
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_DIR = Path("data/journals")


@dataclass
class AgentSignalRecord:
    """Serialized snapshot of a single agent's signal."""

    agent_id: str
    signal: str  # STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR
    confidence: float
    reasoning: str
    key_data: dict[str, Any] = field(default_factory=dict)
    sources_used: list[str] = field(default_factory=list)
    model_id: str = ""
    prompt_variant_id: str = ""
    latency_ms: float = 0.0

    # Risk-specific fields
    risk_verdict: str | None = None  # APPROVE, CAUTION, VETO
    risk_score: float | None = None
    veto_reason: str | None = None

    # News-specific fields
    catalyst_type: str | None = None
    catalyst_specificity: str | None = None
    sentiment_score: float | None = None

    # Technical-specific fields
    pattern_identified: str | None = None
    breakout_confirmed: bool | None = None


@dataclass
class DebateRecord:
    """Serialized snapshot of a debate engine result."""

    verdict: str  # STRONG_BUY, BUY, HOLD, NO_TRADE
    confidence: float
    bull_strength: float
    bear_strength: float
    debate_divergence: float
    position_size: str  # FULL, HALF, QUARTER, NONE
    arguments: dict[str, Any] = field(default_factory=dict)
    entry_price: float | None = None
    stop_loss: float | None = None
    target_prices: list[float] = field(default_factory=list)


@dataclass
class DataQualityRecord:
    """Per-agent data quality assessment at evaluation time."""

    agent_id: str
    status: str  # COMPLETE, PARTIAL, EMPTY
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class InputDataRecord:
    """What data each agent actually received."""

    news_items_count: int = 0
    news_headlines: list[str] = field(default_factory=list)
    news_sources: list[str] = field(default_factory=list)
    technical_indicators: dict[str, float] = field(default_factory=dict)
    price_bars_count: int = 0
    sec_filings_count: int = 0
    sec_filing_types: list[str] = field(default_factory=list)
    options_data_available: bool = False
    gex_data: dict[str, Any] = field(default_factory=dict)
    # D204: Bid/ask spread data for spread-impact analysis.
    # D201-E10 found zero spread data in journals. Wire it here.
    bid: float | None = None
    ask: float | None = None
    spread_pct: float | None = None


@dataclass
class JournalEntry:
    """
    Complete record of a single candidate evaluation.

    This is the atomic unit of the trade journal — one entry per
    orchestrator.evaluate_candidate() call.
    """

    # ── Identity ──
    trade_id: str = ""
    ticker: str = ""
    timestamp: str = ""  # UTC ISO-8601
    session_date: str = ""
    phase: str = ""  # PRE_MARKET, MARKET_OPEN, INTRADAY, VWAP_BREAKOUT, RESCAN

    # ── Candidate Data ──
    current_price: float = 0.0
    previous_close: float = 0.0
    gap_pct: float = 0.0
    gap_classification: str = ""
    rvol: float = 0.0
    premarket_volume: int = 0
    float_shares: int | None = None
    market_cap: float | None = None

    # ── Input Data Provenance ──
    input_data: InputDataRecord = field(default_factory=InputDataRecord)

    # ── Agent Signals (complete, with reasoning) ──
    agent_signals: list[AgentSignalRecord] = field(default_factory=list)

    # ── Data Quality Assessment ──
    data_quality: list[DataQualityRecord] = field(default_factory=list)

    # ── MFCS Scoring ──
    mfcs: float = 0.0
    component_scores: dict[str, float] = field(default_factory=dict)
    risk_score: float = 0.0
    qualifies_for_debate: bool = False
    mfcs_buy_threshold: float = 0.0
    debate_threshold: float = 0.0
    risk_aversion_lambda: float = 0.0

    # ── Debate Result ──
    debate: DebateRecord | None = None

    # ── Final Verdict ──
    action: str = ""  # BUY, HOLD, NO_TRADE
    confidence: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target_prices: list[float] = field(default_factory=list)
    position_size_pct: float = 0.0
    reasoning_summary: str = ""
    rejection_reason: str = ""  # If NO_TRADE, why

    # ── Pipeline Metrics ──
    pipeline_latency_ms: float = 0.0
    arena_variant_map: dict[str, str] = field(default_factory=dict)

    # ── Execution (filled post-order) ──
    order_id: str = ""
    fill_price: float | None = None
    fill_qty: int | None = None
    slippage_bps: float | None = None
    # D66: Execution diagnostics
    order_submitted_at: str = ""        # UTC ISO-8601 timestamp of order submission
    order_status: str = ""              # Alpaca status: new, accepted, filled, rejected, etc.
    rejection_code: str = ""            # Broker rejection reason code
    time_to_fill_s: float | None = None # Seconds from submission to fill confirmation
    stop_order_id: str = ""             # Correlated stop order ID (D57 separate stop)
    partial_fill_qty: int | None = None # If partially filled, actual shares received

    # ── Position Outcome (filled at close) ──
    exit_price: float | None = None
    exit_time: str | None = None
    realized_pnl: float | None = None
    hold_duration_minutes: float | None = None
    tranches_filled: int | None = None
    trailing_stop_activated: bool = False
    # D78: Smart exit intelligence
    exit_reason: str = ""  # TRANCHE_FILL, TRAILING_STOP, SMART_EXIT, EOD_CLOSE, STOP_LOSS
    exit_signal_scores: dict[str, float] = field(default_factory=dict)

    # D101 §3.6: All-in transaction cost tracking
    entry_spread_bps: float | None = None  # Bid-ask spread at entry in basis points
    exit_spread_bps: float | None = None  # Bid-ask spread at exit in basis points
    entry_slippage_bps: float | None = None  # Signal→fill slippage at entry
    exit_slippage_bps: float | None = None  # Signal→fill slippage at exit
    total_round_trip_cost_bps: float | None = None  # Sum of all costs

    # D160: Faller Risk Gate — persisted for arena analysis and weight recalibration
    faller_score: float | None = None          # 0.0 (runner) to 1.0 (faller)
    faller_reject: bool | None = None          # True if faller gate blocked the trade
    faller_position_multiplier: float | None = None  # 1.0 = full, 0.75/0.50 = reduced

    # D161: Short Selling — direction and short candidate flag
    direction: str = "long"                    # "long" or "short"
    faller_short_candidate: bool = False       # True if faller gate routed to short path

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = {}
        for k, v in asdict(self).items():
            if v is None:
                continue
            d[k] = v
        return d


class TradeJournal:
    """
    Append-only trade journal for comprehensive data capture.

    Writes JSONL (one JSON object per line) for efficient streaming
    reads and crash-safe incremental writes.

    Usage:
        journal = TradeJournal(session_date="2026-02-10")

        # During evaluation:
        entry = journal.create_entry(trade_id, candidate, ...)
        journal.record_agent_signals(entry, signals)
        journal.record_mfcs(entry, scored)
        journal.record_debate(entry, debate_result)
        journal.record_verdict(entry, verdict)
        journal.flush(entry)  # Writes to disk

        # After order fill:
        journal.record_fill(trade_id, fill_price, qty)

        # At position close:
        journal.record_close(trade_id, exit_price, pnl, ...)
    """

    def __init__(
        self,
        session_date: str | None = None,
        journal_dir: Path = DEFAULT_JOURNAL_DIR,
    ) -> None:
        self._journal_dir = journal_dir
        self._journal_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        self._session_date = session_date or now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%H%M%S")
        self._filename = f"journal_{self._session_date}_{timestamp}.jsonl"
        self._path = self._journal_dir / self._filename

        # In-memory index: trade_id → line number (for updates)
        self._entries: dict[str, JournalEntry] = {}
        self._line_count = 0

        logger.info("TradeJournal initialized: %s", self._path)

    @property
    def path(self) -> Path:
        """Path to the journal file."""
        return self._path

    @property
    def entry_count(self) -> int:
        """Number of entries recorded this session."""
        return len(self._entries)

    # ── Entry Creation ────────────────────────────────────────────────

    def create_entry(
        self,
        trade_id: str,
        candidate: Any,
        phase: str = "MARKET_OPEN",
    ) -> JournalEntry:
        """Create a new journal entry for a candidate evaluation."""
        entry = JournalEntry(
            trade_id=trade_id,
            ticker=candidate.ticker,
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_date=self._session_date,
            phase=phase,
            current_price=candidate.current_price,
            previous_close=candidate.previous_close,
            gap_pct=candidate.gap_pct,
            gap_classification=getattr(candidate, "gap_classification", ""),
            rvol=candidate.rvol,
            premarket_volume=getattr(candidate, "premarket_volume", 0),
            float_shares=getattr(candidate, "float_shares", None),
            market_cap=getattr(candidate, "market_cap", None),
        )
        self._entries[trade_id] = entry
        return entry

    # ── Input Data Recording ──────────────────────────────────────────

    def record_input_data(
        self,
        entry: JournalEntry,
        news_items: list | None = None,
        market_data: dict | None = None,
        sec_filings: dict | None = None,
        options_data: dict | None = None,
    ) -> None:
        """Record what data was available for agent dispatch."""
        news_items = news_items or []
        market_data = market_data or {}
        sec_filings = sec_filings or {}

        filings_list = sec_filings.get("filings", []) if isinstance(sec_filings, dict) else []

        entry.input_data = InputDataRecord(
            news_items_count=len(news_items),
            news_headlines=[
                getattr(item, "headline", str(item))[:120]
                for item in news_items[:10]
            ],
            news_sources=list({
                getattr(item, "provider", "unknown")
                for item in news_items
            }),
            technical_indicators={
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in market_data.get("indicators", {}).items()
            },
            price_bars_count=len(market_data.get("price_data", {}).get("1min", [])),
            sec_filings_count=len(filings_list),
            sec_filing_types=[
                f.get("form", "") if isinstance(f, dict) else str(f)
                for f in filings_list[:5]
            ],
            options_data_available=bool(options_data),
            gex_data={
                "gex_net": getattr(entry, "_gex_net", None),
            } if options_data else {},
            # D204: Bid/ask spread from market data
            bid=market_data.get("bid"),
            ask=market_data.get("ask"),
            spread_pct=(
                (market_data["ask"] - market_data["bid"]) / ((market_data["ask"] + market_data["bid"]) / 2)
                if market_data.get("bid") and market_data.get("ask") and market_data["bid"] > 0 and market_data["ask"] > 0
                else None
            ),
        )

    # ── Agent Signal Recording ────────────────────────────────────────

    def record_agent_signals(
        self,
        entry: JournalEntry,
        signals: list,
        variant_map: dict[str, str] | None = None,
        data_report: dict | None = None,
    ) -> None:
        """Record complete agent signals with reasoning."""
        variant_map = variant_map or {}
        data_report = data_report or {}

        for sig in signals:
            record = AgentSignalRecord(
                agent_id=getattr(sig, "agent_id", "unknown"),
                signal=getattr(sig, "signal", "NEUTRAL"),
                confidence=getattr(sig, "confidence", 0.0),
                reasoning=getattr(sig, "reasoning", "")[:1500],  # D108: Cap raised 500→1500
                key_data=_safe_dict(getattr(sig, "key_data", {})),
                sources_used=list(getattr(sig, "sources_used", []) or []),
                model_id=getattr(sig, "model_id", ""),
                prompt_variant_id=variant_map.get(
                    getattr(sig, "agent_id", ""), ""
                ),
                latency_ms=getattr(sig, "latency_ms", 0.0),
            )

            # Risk-specific fields
            if hasattr(sig, "risk_verdict"):
                record.risk_verdict = sig.risk_verdict
                record.risk_score = getattr(sig, "risk_score", None)
                record.veto_reason = getattr(sig, "veto_reason", None)

            # News-specific fields
            if hasattr(sig, "catalyst_type"):
                record.catalyst_type = getattr(sig, "catalyst_type", None)
                record.catalyst_specificity = getattr(sig, "catalyst_specificity", None)
                record.sentiment_score = getattr(sig, "sentiment_score", None)

            # Technical-specific fields
            if hasattr(sig, "pattern_identified"):
                record.pattern_identified = getattr(sig, "pattern_identified", None)
                record.breakout_confirmed = getattr(sig, "breakout_confirmed", None)

            entry.agent_signals.append(record)

        # Data quality
        for agent_id, report in data_report.items():
            entry.data_quality.append(DataQualityRecord(
                agent_id=agent_id,
                status=report.get("status", "UNKNOWN"),
                missing_fields=report.get("missing_fields", []),
            ))

        entry.arena_variant_map = dict(variant_map)

    # ── MFCS Recording ────────────────────────────────────────────────

    def record_mfcs(
        self,
        entry: JournalEntry,
        scored: Any,
        settings: Any = None,
    ) -> None:
        """Record MFCS scoring result."""
        entry.mfcs = scored.mfcs
        entry.component_scores = dict(scored.component_scores)
        entry.risk_score = scored.risk_score
        entry.qualifies_for_debate = scored.qualifies_for_debate

        if settings:
            entry.mfcs_buy_threshold = getattr(
                settings.scoring, "mfcs_buy_threshold", 0.0
            )
            entry.debate_threshold = getattr(
                settings.debate, "mfcs_debate_threshold", 0.0
            )
            entry.risk_aversion_lambda = getattr(
                settings.scoring, "risk_aversion_lambda", 0.0
            )

    # ── Debate Recording ──────────────────────────────────────────────

    def record_debate(
        self,
        entry: JournalEntry,
        debate_result: Any | None,
    ) -> None:
        """Record debate engine result."""
        if debate_result is None:
            entry.debate = None
            return

        entry.debate = DebateRecord(
            verdict=debate_result.verdict,
            confidence=debate_result.confidence,
            bull_strength=debate_result.bull_strength,
            bear_strength=debate_result.bear_strength,
            debate_divergence=debate_result.debate_divergence,
            position_size=debate_result.position_size,
            arguments=_safe_dict(getattr(debate_result, "arguments", {})),
            entry_price=getattr(debate_result, "entry_price", None),
            stop_loss=getattr(debate_result, "stop_loss", None),
            target_prices=list(getattr(debate_result, "target_prices", []) or []),
        )

    # ── Verdict Recording ─────────────────────────────────────────────

    def record_verdict(
        self,
        entry: JournalEntry,
        verdict: Any,
    ) -> None:
        """Record final trade verdict."""
        entry.action = verdict.action
        entry.confidence = verdict.confidence
        entry.entry_price = verdict.entry_price
        entry.stop_loss = verdict.stop_loss
        entry.target_prices = list(verdict.target_prices)
        entry.position_size_pct = verdict.position_size_pct
        entry.reasoning_summary = getattr(verdict, "reasoning_summary", "")

        if verdict.action == "NO_TRADE":
            entry.rejection_reason = getattr(verdict, "reasoning_summary", "")

    # ── Execution Recording ───────────────────────────────────────────

    def record_fill(
        self,
        trade_id: str,
        order_id: str,
        fill_price: float,
        fill_qty: int,
        slippage_bps: float = 0.0,
        order_submitted_at: str = "",
        order_status: str = "filled",
        stop_order_id: str = "",
        time_to_fill_s: float | None = None,
        partial_fill_qty: int | None = None,
        entry_spread_bps: float | None = None,
    ) -> None:
        """Record order fill details (called after execution).

        D66: Enhanced with execution diagnostics — order timestamps,
        status, stop correlation, and partial fill tracking.
        D101 §3.6: entry_spread_bps for transaction cost tracking.
        """
        entry = self._entries.get(trade_id)
        if entry is None:
            logger.warning("TradeJournal: no entry for trade_id=%s", trade_id)
            return

        entry.order_id = order_id
        entry.fill_price = fill_price
        entry.fill_qty = fill_qty
        entry.slippage_bps = slippage_bps
        # D101 §3.6: Entry spread cost tracking
        entry.entry_slippage_bps = slippage_bps
        if entry_spread_bps is not None:
            entry.entry_spread_bps = entry_spread_bps
        # D66: Execution diagnostics
        if order_submitted_at:
            entry.order_submitted_at = order_submitted_at
        if order_status:
            entry.order_status = order_status
        if stop_order_id:
            entry.stop_order_id = stop_order_id
        if time_to_fill_s is not None:
            entry.time_to_fill_s = time_to_fill_s
        if partial_fill_qty is not None:
            entry.partial_fill_qty = partial_fill_qty

        # Re-flush to update the entry on disk
        self._append_to_file(entry)

    def record_rejection(
        self,
        trade_id: str,
        order_id: str,
        rejection_code: str,
        order_status: str = "rejected",
        order_submitted_at: str = "",
    ) -> None:
        """Record order rejection (called when Alpaca rejects an order).

        D66: Tracks why orders fail at the broker level.
        """
        entry = self._entries.get(trade_id)
        if entry is None:
            logger.warning("TradeJournal: no entry for rejection trade_id=%s", trade_id)
            return

        entry.order_id = order_id
        entry.order_status = order_status
        entry.rejection_code = rejection_code
        if order_submitted_at:
            entry.order_submitted_at = order_submitted_at

        self._append_to_file(entry)

    # ── Position Close Recording ──────────────────────────────────────

    def record_close(
        self,
        trade_id: str,
        exit_price: float,
        realized_pnl: float,
        exit_time: datetime | None = None,
        hold_duration_minutes: float = 0.0,
        tranches_filled: int = 0,
        trailing_stop_activated: bool = False,
        exit_reason: str = "",
        exit_signal_scores: dict[str, float] | None = None,
        exit_spread_bps: float | None = None,
        exit_slippage_bps: float | None = None,
    ) -> None:
        """Record position close (called at exit).

        D101 §3.6: exit_spread_bps and exit_slippage_bps for total cost tracking.

        doc 269 (OCC 2026-06-08): NEVER stamp a close onto a row that already
        carries a realized close. Callers resolve trade_id via "first
        same-ticker BUY" loops, so an intraday re-buy's close used to land on
        the FIRST BUY row, clobbering the prior round-trip's realized_pnl
        (journal/broker +$936 delta on OCC). Now:
          - if the addressed row is already CLOSED → redirect to the
            still-OPEN same-ticker BUY (no exit_price/exit_time/realized_pnl);
          - if none is open → APPEND a new row; never overwrite a CLOSED row;
          - the unknown-trade_id fallback only targets still-OPEN BUY rows
            (it previously grabbed the first BUY of ANY ticker, closed or not).
        """

        def _is_closed(e: JournalEntry) -> bool:
            return (
                e.realized_pnl is not None
                or e.exit_price is not None
                or bool(e.exit_time)
            )

        entry = self._entries.get(trade_id)
        if entry is None:
            # Fallback: target a still-OPEN BUY only (doc 269 — the old
            # unconditional "first BUY" pick overwrote closed rows).
            for eid, e in self._entries.items():
                if e.action == "BUY" and not _is_closed(e):
                    entry = e
                    break
            if entry is None:
                logger.warning("TradeJournal: no entry for close trade_id=%s", trade_id)
                return
        elif _is_closed(entry):
            # doc 269: addressed row already closed — redirect to the
            # still-OPEN same-ticker BUY (the re-buy this close belongs to).
            redirect = None
            for eid, e in self._entries.items():
                if (
                    eid != trade_id
                    and e.ticker == entry.ticker
                    and e.action == "BUY"
                    and not _is_closed(e)
                ):
                    redirect = e
                    break
            if redirect is not None:
                logger.warning(
                    "TradeJournal doc269: trade_id=%s (%s) already closed — "
                    "redirecting close to open BUY trade_id=%s",
                    trade_id, entry.ticker, redirect.trade_id,
                )
                entry = redirect
            else:
                # No open BUY for this ticker — append a NEW row rather than
                # overwrite the closed one (preserves the prior round-trip).
                base_id = f"{trade_id}_dupclose"
                new_id = base_id
                _suffix = 2
                while new_id in self._entries:
                    new_id = f"{base_id}{_suffix}"
                    _suffix += 1
                logger.warning(
                    "TradeJournal doc269: trade_id=%s (%s) already closed and "
                    "no open BUY to redirect to — appending new row %s "
                    "(never overwriting a closed row)",
                    trade_id, entry.ticker, new_id,
                )
                dup = JournalEntry(
                    trade_id=new_id,
                    ticker=entry.ticker,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    session_date=self._session_date,
                    phase=entry.phase,
                    action="BUY",
                    entry_price=entry.entry_price,
                    direction=entry.direction,
                )
                self._entries[new_id] = dup
                entry = dup

        entry.exit_price = exit_price
        entry.realized_pnl = realized_pnl
        entry.exit_time = (exit_time or datetime.now(timezone.utc)).isoformat()
        entry.hold_duration_minutes = hold_duration_minutes
        entry.tranches_filled = tranches_filled
        entry.trailing_stop_activated = trailing_stop_activated
        # D78: Smart exit tracking
        if exit_reason:
            entry.exit_reason = exit_reason
        if exit_signal_scores:
            entry.exit_signal_scores = exit_signal_scores
        # D101 §3.6: Exit cost tracking
        if exit_spread_bps is not None:
            entry.exit_spread_bps = exit_spread_bps
        if exit_slippage_bps is not None:
            entry.exit_slippage_bps = exit_slippage_bps

        # D101 §3.6: Compute total round-trip cost
        _total = 0.0
        if entry.entry_spread_bps is not None:
            _total += entry.entry_spread_bps
        if entry.exit_spread_bps is not None:
            _total += entry.exit_spread_bps
        if entry.entry_slippage_bps is not None:
            _total += abs(entry.entry_slippage_bps)
        if entry.exit_slippage_bps is not None:
            _total += abs(entry.exit_slippage_bps)
        if _total > 0:
            entry.total_round_trip_cost_bps = round(_total, 1)

        # Re-flush to update
        self._append_to_file(entry)

    # ── Flush to Disk ─────────────────────────────────────────────────

    def flush(self, entry: JournalEntry) -> None:
        """Write entry to JSONL file (append mode)."""
        self._append_to_file(entry)

    def _append_to_file(self, entry: JournalEntry) -> None:
        """Append a single entry as one JSON line."""
        try:
            line = json.dumps(entry.to_dict(), default=str)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._line_count += 1
        except Exception as e:
            logger.error("TradeJournal write failed: %s", e)

    # ── Session Summary ───────────────────────────────────────────────

    def session_summary(self) -> dict[str, Any]:
        """Generate summary statistics for the session."""
        entries = list(self._entries.values())
        buy_entries = [e for e in entries if e.action == "BUY"]
        no_trade_entries = [e for e in entries if e.action == "NO_TRADE"]

        closed_entries = [e for e in buy_entries if e.exit_price is not None]
        total_pnl = sum(e.realized_pnl or 0 for e in closed_entries)

        return {
            "session_date": self._session_date,
            "journal_file": str(self._path),
            "total_evaluations": len(entries),
            "buy_count": len(buy_entries),
            "no_trade_count": len(no_trade_entries),
            "closed_count": len(closed_entries),
            "total_pnl": round(total_pnl, 2),
            "avg_mfcs": round(
                sum(e.mfcs for e in entries) / max(1, len(entries)), 4
            ),
            "avg_pipeline_latency_ms": round(
                sum(e.pipeline_latency_ms for e in entries) / max(1, len(entries)), 1
            ),
            "unique_tickers": len({e.ticker for e in entries}),
            "agent_data_quality": _aggregate_data_quality(entries),
        }

    # ── Load from Disk ────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path | str) -> list[JournalEntry]:
        """Load journal entries from a JSONL file."""
        path = Path(path)
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Reconstruct nested dataclasses
                    if "input_data" in data and isinstance(data["input_data"], dict):
                        data["input_data"] = InputDataRecord(**data["input_data"])
                    if "debate" in data and isinstance(data["debate"], dict):
                        data["debate"] = DebateRecord(**data["debate"])

                    agent_signals = []
                    for sig_data in data.get("agent_signals", []):
                        if isinstance(sig_data, dict):
                            agent_signals.append(AgentSignalRecord(**sig_data))
                    data["agent_signals"] = agent_signals

                    dq = []
                    for dq_data in data.get("data_quality", []):
                        if isinstance(dq_data, dict):
                            dq.append(DataQualityRecord(**dq_data))
                    data["data_quality"] = dq

                    entries.append(JournalEntry(**data))
                except Exception as e:
                    logger.warning("Failed to parse journal line: %s", e)
        return entries

    @classmethod
    def load_all_sessions(cls, journal_dir: Path = DEFAULT_JOURNAL_DIR) -> list[JournalEntry]:
        """Load all journal entries from all session files."""
        all_entries = []
        if not journal_dir.exists():
            return all_entries

        for f in sorted(journal_dir.glob("journal_*.jsonl")):
            try:
                entries = cls.load(f)
                all_entries.extend(entries)
            except Exception as e:
                logger.warning("Failed to load journal %s: %s", f, e)
        return all_entries


# ── Helpers ───────────────────────────────────────────────────────────


def _safe_dict(obj: Any) -> dict:
    """Convert to dict safely, handling non-serializable types."""
    if isinstance(obj, dict):
        return {
            str(k): _safe_value(v)
            for k, v in obj.items()
        }
    return {}


def _safe_value(v: Any) -> Any:
    """Make a value JSON-serializable."""
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    if isinstance(v, (list, tuple)):
        return [_safe_value(i) for i in v]
    if isinstance(v, dict):
        return _safe_dict(v)
    return str(v)


def _aggregate_data_quality(entries: list[JournalEntry]) -> dict[str, dict[str, int]]:
    """Aggregate data quality across all entries."""
    quality: dict[str, dict[str, int]] = {}
    for entry in entries:
        for dq in entry.data_quality:
            if dq.agent_id not in quality:
                quality[dq.agent_id] = {"COMPLETE": 0, "PARTIAL": 0, "EMPTY": 0}
            status = dq.status if dq.status in ("COMPLETE", "PARTIAL", "EMPTY") else "EMPTY"
            quality[dq.agent_id][status] += 1
    return quality


# ────────────────────────────────────────────────────────────────────────
# Bug #13 / D222 — Broker-fill-derived journal counts + EOD reconciliation
# ────────────────────────────────────────────────────────────────────────
# Bug root cause (Wed 2026-04-22 EOD): TradeJournal.session_summary()
# derived `closed_count` and `total_pnl` from `entry.exit_price is not
# None`, which only flips when the exit path explicitly calls
# `record_close()`. The BAR-1 / D146 path does not — so AGPU's BAR-1
# exit silently dropped from the journal counter. Three closures and
# +$328.80 realized rendered as `Closed: 0 | Total P&L: $0.00`.
#
# Fix per directive: reverse the data flow. The Closed count and P&L
# total derive from the broker fill ledger as single source of truth.
# Per-exit-path counters become advisory only — the broker is truth.
# At EOD we reconcile (journal.realized_pnl ↔ broker.realized_pnl);
# divergence > $1 emits a labeled D222 PNL_RECON warning so silent
# disagreement can never recur.

PNL_RECON_TOLERANCE_USD = 1.00


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Coerce broker fill numeric fields (often strings) to float."""
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def summarize_from_broker_fills(fills: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Derive `closed_count`, `open_count`, `total_pnl`, and per-ticker
    realized P&L from the broker fill ledger — the single source of
    truth that does not depend on each exit path remembering to call
    `record_close()`.

    Accepts a list of fill dicts with keys:
        symbol  : str
        side    : "buy" | "sell" (long-only assumed; shorts via D161
                  produce sell-then-buy, also handled)
        qty     : str | int | float
        price   : str | int | float

    Returns:
        {
          "closed_count": int,         # tickers net flat
          "open_count":   int,         # tickers with unbalanced shares
          "total_pnl":    float,       # sum realized across fills
          "per_ticker":   {sym: realized_pnl, ...},
        }

    Notes:
      * "Closed" means net qty == 0 across all fills for that ticker
        on the session (buy_qty - sell_qty == 0 for longs).
      * Realized P&L uses average-cost accounting on the closed shares
        only — partial closes count their realized portion in
        total_pnl but the ticker stays in `open_count` until flat.
    """
    by_ticker: dict[str, dict[str, list[tuple[float, float]]]] = {}
    # by_ticker[sym] = {"buy": [(qty, price), ...], "sell": [...]}
    #
    # Bug N fix (2026-04-23): accept both schemas:
    #   (a) {symbol, side, qty, price}        — Alpaca account activities
    #   (b) {symbol, side, filled_qty,        — Alpaca get_orders response
    #        filled_avg_price, status}
    # Schema (b) is what we actually have in production because
    # AlpacaDataClient does not expose get_account_activities; the
    # production fix routes through get_orders(status='closed').
    for f in fills or []:
        sym = (f.get("symbol") or "").upper()
        side = (f.get("side") or "").lower()
        # Schema (b): only include filled (terminal-success) orders.
        # If a `status` key is present, it must be filled/done_for_day
        # to count toward realized P&L. (Cancelled/rejected don't.)
        _status = (f.get("status") or "").lower()
        if _status and _status not in ("filled", "done_for_day"):
            continue
        qty = _safe_float(f.get("qty")) or _safe_float(f.get("filled_qty"))
        price = _safe_float(f.get("price")) or _safe_float(f.get("filled_avg_price"))
        if not sym or qty <= 0 or price <= 0 or side not in ("buy", "sell"):
            continue
        bucket = by_ticker.setdefault(sym, {"buy": [], "sell": []})
        bucket[side].append((qty, price))

    per_ticker: dict[str, float] = {}
    closed_count = 0
    open_count = 0

    for sym, sides in by_ticker.items():
        buy_qty = sum(q for q, _ in sides["buy"])
        sell_qty = sum(q for q, _ in sides["sell"])
        avg_buy = (
            sum(q * p for q, p in sides["buy"]) / buy_qty if buy_qty > 0 else 0.0
        )
        avg_sell = (
            sum(q * p for q, p in sides["sell"]) / sell_qty if sell_qty > 0 else 0.0
        )

        # Realized P&L on the matched (closed) qty for this ticker.
        # For longs: matched_qty = min(buy_qty, sell_qty);
        #            pnl = matched_qty * (avg_sell - avg_buy)
        # For shorts: same formula, negated when sells precede buys
        # (still works because side-direction is captured in price diff).
        matched = min(buy_qty, sell_qty)
        if matched > 0 and avg_buy > 0 and avg_sell > 0:
            realized = matched * (avg_sell - avg_buy)
        else:
            realized = 0.0
        per_ticker[sym] = round(realized, 4)

        if buy_qty > 0 and sell_qty > 0 and abs(buy_qty - sell_qty) < 1e-6:
            closed_count += 1
        elif buy_qty > 0 or sell_qty > 0:
            open_count += 1

    total_pnl = round(sum(per_ticker.values()), 2)
    return {
        "closed_count": closed_count,
        "open_count": open_count,
        "total_pnl": total_pnl,
        "per_ticker": per_ticker,
    }


def emit_pnl_reconciliation(
    *,
    journal_pnl: float,
    broker_pnl: float,
    per_ticker_journal: dict[str, float],
    per_ticker_broker: dict[str, float],
    tolerance_usd: float = PNL_RECON_TOLERANCE_USD,
) -> bool:
    """
    Compare journal-derived realized P&L against broker-derived realized
    P&L. On divergence > tolerance, emit a D222 PNL_RECON warning that
    spells out the delta and which tickers contributed.

    Returns True if a divergence warning was emitted, False if within
    tolerance. Always non-fatal.
    """
    delta = round(broker_pnl - journal_pnl, 2)
    if abs(delta) <= tolerance_usd:
        logger.info(
            "D222 PNL_RECON OK: journal=$%.2f broker=$%.2f delta=$%.2f (within $%.2f)",
            journal_pnl, broker_pnl, delta, tolerance_usd,
        )
        return False

    # Per-ticker breakdown — explicitly call out missing or mismatched
    # tickers so operators see WHICH exit path forgot to record.
    all_syms = sorted(set(per_ticker_journal) | set(per_ticker_broker))
    breakdown_parts: list[str] = []
    for sym in all_syms:
        j = per_ticker_journal.get(sym)
        b = per_ticker_broker.get(sym)
        if j is None and b is not None:
            breakdown_parts.append(f"{sym}: journal=MISSING broker=${b:+.2f}")
        elif b is None and j is not None:
            breakdown_parts.append(f"{sym}: journal=${j:+.2f} broker=MISSING")
        elif j is not None and b is not None:
            tdelta = round(b - j, 2)
            if abs(tdelta) > tolerance_usd:
                breakdown_parts.append(
                    f"{sym}: journal=${j:+.2f} broker=${b:+.2f} delta=${tdelta:+.2f}"
                )

    breakdown = " | ".join(breakdown_parts) if breakdown_parts else "no per-ticker delta"
    logger.warning(
        "D222 PNL_RECON DIVERGENCE: journal_total=$%.2f broker_total=$%.2f "
        "delta=$%.2f (tolerance=$%.2f) — breakdown: %s. "
        "Likely cause: an exit path closed positions without calling "
        "TradeJournal.record_close() (BAR-1 / D146 was the original Bug #13).",
        journal_pnl, broker_pnl, delta, tolerance_usd, breakdown,
    )
    return True


async def run_pnl_reconciliation(
    *,
    client: Any,
    journal_pnl: float,
    per_ticker_journal: dict[str, float],
    tolerance_usd: float = PNL_RECON_TOLERANCE_USD,
) -> bool:
    """
    EOD entrypoint: pull today's broker fills, derive realized P&L,
    and call emit_pnl_reconciliation(). Non-fatal on broker-API failure.

    Bug N fix (2026-04-23): yesterday's first-cut called
    `client.get_account_activities()` which AlpacaDataClient does NOT
    expose — produced a silent AttributeError, fell through to journal-
    only counts, reproduced the original Bug #13 symptom verbatim.
    Fix: derive fills from `client.get_orders(status='closed')` which
    is already used by Bug D's poll loop. Each filled order has
    `filled_avg_price`, `filled_qty`, `side`, `symbol`, `status` —
    exactly what `summarize_from_broker_fills` needs after Bug N's
    schema-flexibility patch above.

    Returns True if a divergence warning was emitted.
    """
    fills: list[dict[str, Any]] = []
    try:
        # Prefer get_account_activities if available (richer schema);
        # fall back to get_orders for the production AlpacaDataClient.
        # Track A item 2 (2026-04-23): D236 marker labels which path
        # ran so future Bug-N-class regressions are visible without
        # reading code (the audit caught this branch as a dark zone).
        if hasattr(client, "get_account_activities"):
            logger.info("D236 PNL_RECON_PATH: using get_account_activities (richer schema)")
            fills = await client.get_account_activities()
        else:
            logger.info(
                "D236 PNL_RECON_PATH: using get_orders fallback "
                "(AlpacaDataClient does not expose get_account_activities)"
            )
            # get_orders(status='closed') returns terminal orders for
            # the day. We use 'all' + filter on status='filled' downstream
            # for safety against API parameter drift.
            # D311 (2026-05-24): MUST pass `after=` (today's UTC midnight),
            # otherwise this pulls the 500 most-recent orders (potentially
            # weeks of history) and compares to today-only journal,
            # producing false-positive $40k+ deltas with 60+ tickers
            # marked journal=MISSING. Same architectural bug D238 had,
            # patched in doc 158 -- this sibling site (D222) was missed.
            from datetime import datetime, timezone, timedelta
            _now_utc = datetime.now(timezone.utc)
            _today_utc_midnight = _now_utc - timedelta(
                hours=_now_utc.hour, minutes=_now_utc.minute,
                seconds=_now_utc.second, microseconds=_now_utc.microsecond,
            )
            _after_iso = _today_utc_midnight.isoformat().replace("+00:00", "Z")
            fills = await client.get_orders(
                status="all", limit=500, after=_after_iso,
            )
    except Exception as e:
        logger.warning(
            "D222 PNL_RECON DEGRADED: broker fill fetch raised "
            "(%s) — recon did not run. journal_pnl=$%.2f",
            e, journal_pnl,
        )
        return False

    summary = summarize_from_broker_fills(fills or [])
    return emit_pnl_reconciliation(
        journal_pnl=journal_pnl,
        broker_pnl=summary["total_pnl"],
        per_ticker_journal=per_ticker_journal,
        per_ticker_broker=summary["per_ticker"],
        tolerance_usd=tolerance_usd,
    )
