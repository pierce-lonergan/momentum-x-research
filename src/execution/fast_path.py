"""
MOMENTUM-X D85: Fast-Path Entry System

### ARCHITECTURAL CONTEXT
Node ID: execution.fast_path
Graph Link: docs/memory/graph_state.json → "execution.fast_path"

### PURPOSE
Enables immediate position entry at market open (9:30:01 ET) based on
pre-market scoring, without waiting for the full 6-agent LLM evaluation
pipeline (~100s per candidate).

### DESIGN
1. FastPathScorer: Runs at 9:20 ET using 3 agents with real pre-market data:
   - News Agent (30% MFCS weight) — pre-fetched 24h news from Phase 0
   - Fundamental Agent (15% weight) — float_shares + SEC filings from Phase 0
   - Deep Search Agent (5% weight) — SEC filings cross-reference from Phase 0
   Total: 50% MFCS signal weight with real data (vs 30% with News-only).
   D26 weight redistribution in compute_mfcs() handles 3 missing agents.
2. DipEntryCalculator: Gap-size-dependent dip factors for limit entry prices.
   Empirical: opening dip captures 2-15% below premarket high.
3. FastPathExecutor: Fires OTO limit orders at 9:30:01 with THIRD sizing (8%).
4. FastPathReconciler: After full LLM eval completes (~2 min), confirms or
   cancels fast-path entries based on TradeVerdict agreement.

### RISK MITIGATION
- THIRD sizing (8% equity per entry) — justified by 50% signal coverage
- Max 3 concurrent fast-path entries
- OTO stop loss active immediately (4% max loss = 0.32% equity risk per entry)
- Full LLM eval is final arbiter — cancel/close on disagree
- Limit orders expire unfilled if dip doesn't reach target

### CRITICAL INVARIANTS
1. Fast-path entries NEVER exceed configured position_size_pct.
2. Portfolio risk checks (sector, heat) apply before every entry.
3. Circuit breaker blocks fast-path entries.
4. Reconciliation MUST run after full eval — no orphaned fast-path positions.

Ref: Day 5 analysis — 174.8 min average entry delay, 1.7% capture rate
Ref: MOMENTUM_LOGIC.md §5 (MFCS with D26 redistribution)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from config.settings import Settings
from src.core.models import (
    AgentSignal,
    CandidateStock,
    GapClassification,
    NewsSignal,
    ScoredCandidate,
    TradeVerdict,
)
from src.core.scoring import compute_mfcs

logger = logging.getLogger(__name__)

# ── Fast-Path Entry Status ────────────────────────────────────────────

FastPathStatus = Literal[
    "QUEUED",       # Scored and ready, pre-9:30
    "SUBMITTED",    # OTO order sent to Alpaca
    "FILLED",       # Buy leg filled
    "CONFIRMED",    # Full LLM eval agrees — keep/upgrade
    "CANCELLED",    # Full LLM eval disagrees — cancel/close
    "EXPIRED",      # Order expired unfilled (dip didn't reach limit)
    "ERROR",        # Submission or reconciliation error
    # Doc 84 (PROMPT_07, 2026-04-30): added "READY" status for the
    # AT-1 fix migration path. prepare_queue() sets READY after
    # pre-checks pass; main.py then routes through bridge.execute_verdict
    # which submits + waits for broker confirmation. Replaces the old
    # FAST_PATH limit-as-fill recording (Bug AT-1).
    "READY",        # Pre-checks passed; ready for bridge.execute_verdict
]


@dataclass
class FastPathEntry:
    """
    A single fast-path entry candidate with lifecycle tracking.

    Tracks the full lifecycle: QUEUED → SUBMITTED → FILLED → CONFIRMED/CANCELLED.
    """

    ticker: str
    candidate: CandidateStock
    partial_mfcs: float
    news_signal: NewsSignal | AgentSignal | None
    entry_price: float
    stop_loss: float
    target_prices: list[float] = field(default_factory=list)
    qty: int = 0
    dip_factor: float = 0.0
    order_id: str = ""
    stop_order_id: str = ""
    status: FastPathStatus = "QUEUED"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str = ""
    # D106: All agent signals from scoring — stored for MFCS cache bypass.
    # At market open, orchestrator reuses cached LLM signals + re-runs
    # deterministic agents with real data instead of full LLM re-dispatch.
    agent_signals: list[AgentSignal] = field(default_factory=list)


# ── Dip Entry Calculator ─────────────────────────────────────────────

# Empirical dip table from Day 5 analysis:
# Opening dip magnitude correlates with gap size.
# Larger gaps → deeper opening dip → more aggressive limit offset.
DIP_TABLE: dict[GapClassification, float] = {
    "MINOR": 0.02,       # <20% gap → 2% dip below premarket price
    "SIGNIFICANT": 0.05,  # 20-50% gap → 5% dip
    "MAJOR": 0.10,       # 50-100% gap → 10% dip
    "EXPLOSIVE": 0.15,   # >100% gap → 15% dip
}

# doc 272 LEVER-2 (FLAG-B, default unset): env FAST_PATH_DIP_OVERRIDE_PCT.
# Doc-265 replay evidence: decision-price limits (dip~0) fill 94% vs 26%
# for the deep DIP_TABLE dips — the table's deep dips adversely select.
# When the env var parses as a finite float in [0.0, 1.0), ALL gap classes
# use that single dip factor. Unset / empty / garbage / out-of-range →
# exact legacy DIP_TABLE behavior (never raises).
DIP_OVERRIDE_ENV = "FAST_PATH_DIP_OVERRIDE_PCT"


def _dip_factor(classification: GapClassification) -> float:
    """Single source of dip factors (doc 272 LEVER-2).

    Reads FAST_PATH_DIP_OVERRIDE_PCT at call time. Any value that is not a
    finite float in [0.0, 1.0) — including unset, "", and garbage — falls
    back to the legacy DIP_TABLE lookup (same 0.02 default). Never raises.
    """
    raw = os.environ.get(DIP_OVERRIDE_ENV)
    if raw is not None:
        try:
            val = float(raw)
            # Reject NaN/inf, negatives (entry above decision price), and
            # >=1.0 (entry price would hit zero or below).
            if math.isfinite(val) and 0.0 <= val < 1.0:
                return val
        except (TypeError, ValueError):
            pass  # garbage → legacy table
    return DIP_TABLE.get(classification, 0.02)


class DipEntryCalculator:
    """
    Computes opening-dip entry prices based on gap classification.

    Day 5 finding: Every optimal entry was in the first 20 minutes,
    during the opening dip that follows large pre-market gaps.
    The dip depth correlates with gap magnitude.
    """

    @staticmethod
    def compute_entry(
        candidate: CandidateStock,
        stop_pct: float = 0.04,
    ) -> tuple[float, float, list[float]]:
        """
        Compute dip entry price, stop loss, and target prices.

        Args:
            candidate: Stock with current_price (premarket high) and gap_classification.
            stop_pct: Stop loss distance from entry (default 4%).

        Returns:
            (entry_price, stop_loss, target_prices)
        """
        premarket_price = candidate.current_price
        # doc 272 LEVER-2: routed through _dip_factor() — identical to the
        # legacy DIP_TABLE lookup unless FAST_PATH_DIP_OVERRIDE_PCT is set.
        dip_factor = _dip_factor(candidate.gap_classification)

        # Entry at dip below premarket price
        entry_price = premarket_price * (1.0 - dip_factor)

        # Stop loss below entry
        stop_loss = entry_price * (1.0 - stop_pct)

        # D121 BUG: Targets updated to match main pipeline (+5/10/20%)
        # Was +3/6/10% — caused premature tranche exits on fast-path entries
        targets = [
            entry_price * 1.05,
            entry_price * 1.10,
            entry_price * 1.20,
        ]

        # Round per SEC Rule 612
        if entry_price >= 1.0:
            entry_price = round(entry_price, 2)
            stop_loss = round(stop_loss, 2)
            targets = [round(t, 2) for t in targets]
        else:
            entry_price = round(entry_price, 4)
            stop_loss = round(stop_loss, 4)
            targets = [round(t, 4) for t in targets]

        return entry_price, stop_loss, targets

    @staticmethod
    def get_dip_factor(gap_classification: GapClassification) -> float:
        """Get the dip factor for a gap classification."""
        # doc 272 LEVER-2: routed through _dip_factor() — identical to the
        # legacy DIP_TABLE lookup unless FAST_PATH_DIP_OVERRIDE_PCT is set.
        return _dip_factor(gap_classification)


# ── Fast-Path Scorer ─────────────────────────────────────────────────

class FastPathScorer:
    """
    Pre-market scorer: up to 5 agents (3 LLM + 2 deterministic).

    D106: Extended from 3-agent (50% MFCS weight) to 5-agent (100% weight)
    by adding DeterministicTechnicalAgent and DeterministicRiskAgent.
    These run in <1ms with graceful degradation when pre-market data is empty.

    Runs at 9:20 ET with real pre-market data:
    - News Agent (30%) — pre-fetched 24h news from Phase 0
    - Fundamental Agent (15%) — float_shares + SEC filings from Phase 0
    - Deep Search Agent (5%) — SEC cross-reference from Phase 0
    - Technical Agent (20%) — graceful degrade: empty price_data → NEUTRAL/0.0
    - Risk Agent (30%) — market_data from Phase 0 cache, bid=0/ask=0 pre-market

    Uses compute_mfcs() with D26 weight redistribution for any missing agents.
    Full 5-agent score ("partial_mfcs" — name kept for backward compat) enables
    smart cache bypass at 9:30: skip LLM re-dispatch, re-run only deterministic
    agents with real market data.
    """

    def __init__(
        self,
        settings: Settings,
        news_agent: Any,
        fundamental_agent: Any | None = None,
        deep_search_agent: Any | None = None,
        technical_agent: Any | None = None,
        risk_agent: Any | None = None,
        threshold: float = 0.35,
        max_entries: int = 3,
    ) -> None:
        self._settings = settings
        self._news_agent = news_agent
        self._fundamental_agent = fundamental_agent
        self._deep_search_agent = deep_search_agent
        self._technical_agent = technical_agent
        self._risk_agent = risk_agent
        self._threshold = threshold
        self._max_entries = max_entries

    async def score_candidates(
        self,
        candidates: list[CandidateStock],
        news_by_ticker: dict[str, list] | None = None,
        sec_filings_by_ticker: dict[str, list] | None = None,
        market_data_by_ticker: dict[str, dict] | None = None,
    ) -> list[FastPathEntry]:
        """
        Score candidates using up to 5 agents with real pre-market data.

        D106: Extended from 3 to 5 agents. Runs News + Fundamental + Deep Search
        (LLM) + Technical + Risk (deterministic, <1ms) in parallel for all
        candidates, then computes MFCS. D26 redistribution handles any missing agents.
        Filters by threshold and caps at max_entries.

        Args:
            candidates: Pre-market watchlist candidates.
            news_by_ticker: Pre-fetched news per ticker (optional).
            sec_filings_by_ticker: Pre-fetched SEC filings per ticker (optional).
                Format: {ticker: [{"form": "S-3", "description": "...", "date": "..."}]}
            market_data_by_ticker: D106: Pre-market data per ticker for deterministic
                agents (optional). Format: {ticker: {"current_price": ..., "rvol": ..., ...}}

        Returns:
            Sorted list of FastPathEntry (highest MFCS first), capped at max_entries.
        """
        if not candidates:
            return []

        news_by_ticker = news_by_ticker or {}
        sec_filings_by_ticker = sec_filings_by_ticker or {}
        market_data_by_ticker = market_data_by_ticker or {}

        # ── Build per-candidate task lists: up to 5 agents each ──
        # All agent calls across all candidates run in one big asyncio.gather
        # for maximum parallelism (5 agents × 10 candidates = 50 calls).
        all_tasks: list[asyncio.Task | Any] = []
        task_map: list[tuple[int, str]] = []  # (candidate_idx, agent_id)

        for i, c in enumerate(candidates):
            # News Agent (always runs)
            news_items = news_by_ticker.get(c.ticker, [])
            all_tasks.append(
                self._news_agent.analyze(
                    ticker=c.ticker,
                    company_name=getattr(c, "company_name", c.ticker),
                    news_items=news_items,
                    market_cap=c.market_cap,
                    sector="Unknown",
                )
            )
            task_map.append((i, "news_agent"))

            # Fundamental Agent (if available)
            if self._fundamental_agent is not None:
                filings = sec_filings_by_ticker.get(c.ticker, [])
                all_tasks.append(
                    self._fundamental_agent.analyze(
                        ticker=c.ticker,
                        float_shares=c.float_shares,
                        shares_outstanding=None,
                        short_interest=None,
                        insider_ownership_pct=None,
                        institutional_ownership_pct=None,
                        recent_filings=filings,
                    )
                )
                task_map.append((i, "fundamental_agent"))

            # Deep Search Agent (if available)
            if self._deep_search_agent is not None:
                filings = sec_filings_by_ticker.get(c.ticker, [])
                all_tasks.append(
                    self._deep_search_agent.analyze(
                        ticker=c.ticker,
                        sec_filings=filings,
                        social_data={},
                        historical_moves=[],
                        sector_peers=[],
                    )
                )
                task_map.append((i, "deep_search_agent"))

            # D106: Technical Agent — deterministic, <1ms
            # Graceful degrade pre-market: empty price_data → NEUTRAL/0.0
            if self._technical_agent is not None:
                all_tasks.append(
                    self._technical_agent.analyze(
                        ticker=c.ticker,
                        current_price=c.current_price,
                        rvol=c.rvol,
                        gap_pct=c.gap_pct,  # D126: gap momentum mode
                        vwap=0.0,  # VWAP unavailable pre-market
                        price_data={},  # No intraday bars pre-market
                    )
                )
                task_map.append((i, "technical_agent"))

            # D106: Risk Agent — deterministic, <1ms
            # Pre-market: bid=0/ask=0 (spread unavailable), rest from candidate
            if self._risk_agent is not None:
                _mkt = market_data_by_ticker.get(c.ticker, {})
                all_tasks.append(
                    self._risk_agent.analyze(
                        ticker=c.ticker,
                        candidate_signals=[],  # No prior signals available
                        market_data={
                            "current_price": c.current_price,
                            "bid": _mkt.get("bid", 0),
                            "ask": _mkt.get("ask", 0),
                            "rvol": c.rvol,
                            "float_shares": c.float_shares,
                            "gap_pct": c.gap_pct,
                            "has_news": c.has_news_catalyst,
                            "halt_count": 0,
                            "avg_daily_volume": c.avg_daily_volume or 0,
                        },
                        sec_filings=sec_filings_by_ticker.get(c.ticker, {}),
                    )
                )
                task_map.append((i, "risk_agent"))

        # ── Execute all agent calls in parallel ──
        all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # ── Collect signals per candidate ──
        signals_by_idx: dict[int, list[AgentSignal]] = {
            i: [] for i in range(len(candidates))
        }
        news_signal_by_idx: dict[int, AgentSignal | None] = {
            i: None for i in range(len(candidates))
        }

        agent_count = 0
        for (cand_idx, agent_id), result in zip(task_map, all_results):
            ticker = candidates[cand_idx].ticker
            if isinstance(result, Exception):
                logger.warning(
                    "D85 fast-path: %s failed for %s: %s",
                    agent_id, ticker, result,
                )
                continue
            if not isinstance(result, AgentSignal):
                logger.warning(
                    "D85 fast-path: unexpected %s result for %s: %s",
                    agent_id, ticker, type(result),
                )
                continue

            signals_by_idx[cand_idx].append(result)
            agent_count += 1
            if agent_id == "news_agent":
                news_signal_by_idx[cand_idx] = result

        active_agents = sum(1 for a in [
            self._news_agent, self._fundamental_agent, self._deep_search_agent,
            self._technical_agent, self._risk_agent,
        ] if a is not None)
        logger.info(
            "D85 fast-path: %d agent calls completed (%d agents × %d candidates)",
            agent_count, active_agents, len(candidates),
        )

        # ── Score each candidate ──
        entries: list[FastPathEntry] = []

        for i, candidate in enumerate(candidates):
            signals = signals_by_idx[i]
            if not signals:
                logger.debug("D85: %s — no signals, skipping", candidate.ticker)
                continue

            # Compute partial MFCS — D26 redistribution handles missing agents
            scored = compute_mfcs(
                candidate=candidate,
                signals=signals,
                weights={
                    "catalyst_news": self._settings.scoring.catalyst_news,
                    "technical": self._settings.scoring.technical,
                    "volume_rvol": self._settings.scoring.volume_rvol,
                    "float_structure": self._settings.scoring.float_structure,
                    "institutional": self._settings.scoring.institutional,
                    "deep_search": self._settings.scoring.deep_search,
                },
                risk_aversion_lambda=self._settings.scoring.risk_aversion_lambda,
                debate_threshold=1.0,  # Never trigger debate in fast-path
            )

            partial_mfcs = scored.mfcs
            news_sig = news_signal_by_idx.get(i)

            agent_summary = "/".join(
                f"{s.agent_id.replace('_agent', '')}={s.signal}"
                for s in signals
            )
            logger.info(
                "D85 fast-path score: %s | partial_MFCS=%.3f | agents=%d [%s] | RVOL=%.1fx | gap=%.1f%%",
                candidate.ticker,
                partial_mfcs,
                len(signals),
                agent_summary,
                candidate.rvol,
                candidate.gap_pct * 100,
            )

            if partial_mfcs < self._threshold:
                logger.debug(
                    "D85: %s below threshold (%.3f < %.3f), skipping",
                    candidate.ticker, partial_mfcs, self._threshold,
                )
                continue

            # Compute dip entry price
            entry_price, stop_loss, targets = DipEntryCalculator.compute_entry(
                candidate, stop_pct=self._settings.execution.stop_loss_pct,
            )
            dip_factor = DipEntryCalculator.get_dip_factor(candidate.gap_classification)

            entries.append(FastPathEntry(
                ticker=candidate.ticker,
                candidate=candidate,
                partial_mfcs=partial_mfcs,
                news_signal=news_sig,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_prices=targets,
                dip_factor=dip_factor,
                agent_signals=list(signals),  # D106: store for MFCS cache bypass
            ))

        # Sort by partial MFCS descending, cap at max_entries
        entries.sort(key=lambda e: e.partial_mfcs, reverse=True)
        entries = entries[: self._max_entries]

        logger.info(
            "D85 fast-path: %d/%d candidates qualify (threshold=%.2f, max=%d, agents=%d)",
            len(entries), len(candidates), self._threshold, self._max_entries,
            active_agents,
        )
        for e in entries:
            logger.info(
                "  → %s: MFCS=%.3f, entry=$%.2f (dip=%.0f%%), stop=$%.2f",
                e.ticker, e.partial_mfcs, e.entry_price,
                e.dip_factor * 100, e.stop_loss,
            )

        return entries


# ── Fast-Path Executor ───────────────────────────────────────────────

class FastPathExecutor:
    """
    Fires OTO limit orders for fast-path entries at market open.

    Each entry gets: buy limit at dip price + stop sell at stop_loss.
    Position sizing is THIRD (8% of equity) — justified by 50% MFCS coverage.
    """

    def __init__(
        self,
        settings: Settings,
        alpaca_client: Any,
        position_manager: Any,
        portfolio_risk: Any,
    ) -> None:
        self._settings = settings
        self._client = alpaca_client
        self._position_manager = position_manager
        self._portfolio_risk = portfolio_risk

    async def prepare_queue(
        self,
        entries: list[FastPathEntry],
    ) -> list[FastPathEntry]:
        """
        Doc 84 (PROMPT_07, 2026-04-30): AT-1 fix entry point. Pre-flights
        FAST_PATH entries WITHOUT submitting any OTO order.

        Performs the same pre-checks as `execute_queue` (circuit breaker,
        existing position, portfolio risk, qty sizing) but DOES NOT call
        `client.submit_oto_order`. After pre-checks pass, sets
        entry.status = "READY"; the caller (main.py) then routes through
        `bridge.execute_verdict(verdict, scored)` which submits the order
        AND waits for broker confirmation via `_poll_for_terminal_fill`.

        This closes Bug AT-1: the helper sees `order.fill_price` as the
        REALIZED broker fill, never the limit price.

        execute_queue (legacy below) still works for backward compat with
        existing tests; new code paths should use prepare_queue.

        Args:
            entries: Scored FastPathEntry list from FastPathScorer.

        Returns:
            Same list with updated status/qty/error fields.
            Entries that pass all pre-checks have status="READY".
        """
        if not entries:
            return entries

        try:
            account = await self._client.get_account()
            equity = float(account.get("equity", 0))
        except Exception as e:
            logger.error("D85 prepare: account fetch failed: %s", e)
            for entry in entries:
                entry.status = "ERROR"
                entry.error = f"Account fetch failed: {e}"
            return entries

        if equity <= 0:
            logger.error("D85 prepare: zero equity")
            for entry in entries:
                entry.status = "ERROR"
                entry.error = "Zero equity"
            return entries

        position_size_pct = self._settings.fast_path.position_size_pct

        for entry in entries:
            if not self._position_manager.can_enter_new_position():
                logger.warning(
                    "D85 prepare: %s blocked — circuit breaker or max positions",
                    entry.ticker,
                )
                entry.status = "CANCELLED"
                entry.error = "Circuit breaker or max positions"
                continue

            if self._position_manager.has_position(entry.ticker):
                logger.info(
                    "D85 prepare: %s already held, skipping",
                    entry.ticker,
                )
                entry.status = "CANCELLED"
                entry.error = "Already held"
                continue

            stop_pct = self._settings.execution.stop_loss_pct * 100
            risk_check = self._portfolio_risk.check_entry(
                ticker=entry.ticker,
                stop_loss_pct=stop_pct,
                positions=self._position_manager.open_positions,
            )
            if not risk_check.allowed:
                logger.warning(
                    "D85 prepare: %s blocked by portfolio risk: %s",
                    entry.ticker, risk_check.reason,
                )
                entry.status = "CANCELLED"
                entry.error = f"Portfolio risk: {risk_check.reason}"
                continue

            dollar_amount = equity * position_size_pct
            qty = math.floor(dollar_amount / entry.entry_price)
            if qty <= 0:
                logger.warning(
                    "D85 prepare: %s qty=0 (equity=%.0f, pct=%.3f, price=%.2f)",
                    entry.ticker, equity, position_size_pct, entry.entry_price,
                )
                entry.status = "CANCELLED"
                entry.error = "Computed qty=0"
                continue

            entry.qty = qty
            entry.status = "READY"
            logger.info(
                "D85 READY: %s | qty=%d | entry=$%.2f | stop=$%.2f "
                "(awaiting bridge.execute_verdict for broker submit + confirmation)",
                entry.ticker, qty, entry.entry_price, entry.stop_loss,
            )

        return entries

    async def execute_queue(
        self,
        entries: list[FastPathEntry],
    ) -> list[FastPathEntry]:
        """
        Submit OTO orders for all fast-path entries.

        DEPRECATED for production use as of doc 84 (2026-04-30): this
        method records a fill_price = limit price (Bug AT-1 — see doc 80
        §1). Production code now uses `prepare_queue` + the unified
        post-fill helper. This method is preserved only for the existing
        unit tests that exercise the OLD contract.

        Pre-checks: circuit breaker, existing position, portfolio risk.
        Uses QUARTER sizing (5% of equity).

        Args:
            entries: Scored FastPathEntry list from FastPathScorer.

        Returns:
            Same list with updated status/order_id fields.
        """
        if not entries:
            return entries

        # Get account equity for sizing
        try:
            account = await self._client.get_account()
            equity = float(account.get("equity", 0))
        except Exception as e:
            logger.error("D85: Failed to get account for sizing: %s", e)
            for entry in entries:
                entry.status = "ERROR"
                entry.error = f"Account fetch failed: {e}"
            return entries

        if equity <= 0:
            logger.error("D85: Zero equity, cannot size fast-path entries")
            for entry in entries:
                entry.status = "ERROR"
                entry.error = "Zero equity"
            return entries

        position_size_pct = self._settings.fast_path.position_size_pct

        for entry in entries:
            # Pre-check: circuit breaker
            if not self._position_manager.can_enter_new_position():
                logger.warning(
                    "D85: %s blocked — circuit breaker or max positions",
                    entry.ticker,
                )
                entry.status = "CANCELLED"
                entry.error = "Circuit breaker or max positions"
                continue

            # Pre-check: already have position
            if self._position_manager.has_position(entry.ticker):
                logger.info(
                    "D85: %s already held, skipping fast-path entry",
                    entry.ticker,
                )
                entry.status = "CANCELLED"
                entry.error = "Already held"
                continue

            # Pre-check: portfolio risk (sector concentration, heat)
            stop_pct = self._settings.execution.stop_loss_pct * 100
            risk_check = self._portfolio_risk.check_entry(
                ticker=entry.ticker,
                stop_loss_pct=stop_pct,
                positions=self._position_manager.open_positions,
            )
            if not risk_check.allowed:
                logger.warning(
                    "D85: %s blocked by portfolio risk: %s",
                    entry.ticker, risk_check.reason,
                )
                entry.status = "CANCELLED"
                entry.error = f"Portfolio risk: {risk_check.reason}"
                continue

            # Compute qty at QUARTER sizing
            dollar_amount = equity * position_size_pct
            qty = math.floor(dollar_amount / entry.entry_price)
            if qty <= 0:
                logger.warning(
                    "D85: %s qty=0 (equity=%.0f, pct=%.3f, price=%.2f)",
                    entry.ticker, equity, position_size_pct, entry.entry_price,
                )
                entry.status = "CANCELLED"
                entry.error = "Computed qty=0"
                continue

            entry.qty = qty

            # Submit OTO order
            try:
                response = await self._client.submit_oto_order(
                    symbol=entry.ticker,
                    qty=qty,
                    limit_price=entry.entry_price,
                    stop_loss=entry.stop_loss,
                    time_in_force="day",
                )
                entry.order_id = response.get("id", "")
                entry.status = "SUBMITTED"

                # Extract stop leg ID
                legs = response.get("legs") or []
                for leg in legs:
                    if leg.get("side") == "sell" and leg.get("type") == "stop":
                        entry.stop_order_id = leg.get("id", "")
                        break

                logger.info(
                    "D85 SUBMITTED: %s | qty=%d | entry=$%.2f | stop=$%.2f | oid=%s",
                    entry.ticker, qty, entry.entry_price, entry.stop_loss,
                    entry.order_id[:12],
                )

            except Exception as e:
                logger.error(
                    "D85: OTO order failed for %s: %s", entry.ticker, e,
                )
                entry.status = "ERROR"
                entry.error = str(e)

        return entries


# ── Fast-Path Reconciler ─────────────────────────────────────────────

class FastPathReconciler:
    """
    Reconciles fast-path entries with full LLM evaluation verdicts.

    After the full pipeline evaluates candidates (~2 min), this reconciler:
    - CONFIRMS entries where the verdict agrees (BUY/STRONG_BUY)
    - CANCELS entries where the verdict disagrees (NO_TRADE/HOLD)

    Confirmed entries can optionally be upgraded (more shares added).
    Cancelled entries are closed if filled, or order cancelled if pending.
    """

    def __init__(
        self,
        settings: Settings,
        alpaca_client: Any,
        position_manager: Any,
    ) -> None:
        self._settings = settings
        self._client = alpaca_client
        self._position_manager = position_manager

    async def reconcile(
        self,
        fast_path_entries: list[FastPathEntry],
        verdicts: list[TradeVerdict],
    ) -> list[FastPathEntry]:
        """
        Match verdicts to fast-path entries and confirm or cancel.

        Args:
            fast_path_entries: List of submitted/filled fast-path entries.
            verdicts: TradeVerdicts from full LLM evaluation.

        Returns:
            Updated entries with CONFIRMED or CANCELLED status.
        """
        if not fast_path_entries:
            return fast_path_entries

        # Build verdict lookup by ticker
        verdict_map: dict[str, TradeVerdict] = {v.ticker: v for v in verdicts}

        for entry in fast_path_entries:
            # Only reconcile active entries
            if entry.status not in ("SUBMITTED", "FILLED"):
                continue

            verdict = verdict_map.get(entry.ticker)

            if verdict is None:
                # Ticker wasn't evaluated (e.g., not in candidate list)
                # Keep fast-path entry as-is — will expire EOD
                logger.info(
                    "D85 reconcile: %s — no verdict found, keeping as-is",
                    entry.ticker,
                )
                continue

            if verdict.action in ("BUY", "STRONG_BUY"):
                # Full eval agrees — confirm
                entry.status = "CONFIRMED"
                logger.info(
                    "D85 CONFIRMED: %s | verdict=%s conf=%.2f mfcs=%.3f",
                    entry.ticker, verdict.action, verdict.confidence, verdict.mfcs,
                )

                # Optionally upgrade: handled by main loop (add more shares)
                if self._settings.fast_path.upgrade_on_confirm:
                    logger.info(
                        "D85: %s eligible for position upgrade (full eval confirms)",
                        entry.ticker,
                    )

            elif verdict.action == "NO_TRADE" and any(
                s.signal in ("BEAR", "STRONG_BEAR")
                for s in getattr(verdict, "agent_signals", [])
            ):
                # D139: Pipeline inversion — only cancel on explicit BEAR signal.
                # Arena finding: LLM agents add zero marginal value (+$0.07 both
                # with and without). Keeping fast-path entries by default captures
                # 10x more P&L (+$1.04 parallel vs +$0.09 sequential).
                if self._settings.fast_path.cancel_on_disagree:
                    logger.info(
                        "D139: %s CANCEL — explicit BEAR from full eval",
                        entry.ticker,
                    )
                    await self._cancel_entry(entry, verdict)
            else:
                # D139: NO_TRADE without BEAR signal — KEEP the entry.
                # The fast-path deterministic signals were sufficient for entry.
                # LLM returning NEUTRAL/HOLD doesn't override the momentum signal.
                logger.info(
                    "D139: %s KEEP — full eval returned %s but no BEAR signal, "
                    "keeping fast-path entry (pipeline inversion)",
                    entry.ticker, verdict.action,
                )

        return fast_path_entries

    async def _cancel_entry(
        self,
        entry: FastPathEntry,
        verdict: TradeVerdict,
    ) -> None:
        """Cancel or close a fast-path entry that the full eval rejects."""
        entry.status = "CANCELLED"

        logger.info(
            "D85 CANCEL: %s | full_eval=%s (mfcs=%.3f) disagrees with fast-path (mfcs=%.3f)",
            entry.ticker, verdict.action, verdict.mfcs, entry.partial_mfcs,
        )

        # If order is still pending (not filled), cancel it
        if entry.order_id:
            try:
                await self._client.cancel_order(entry.order_id)
                logger.info(
                    "D85: Cancelled pending order %s for %s",
                    entry.order_id[:12], entry.ticker,
                )
                # D98: Remove ghost position from internal tracker.
                # Previously returned without removing, leaving phantom
                # positions tracked all day (D12: GXAI ghost).
                if self._position_manager.has_position(entry.ticker):
                    self._position_manager.remove_position(entry.ticker)
                    logger.info(
                        "D98: Removed cancelled fast-path position %s from tracker",
                        entry.ticker,
                    )
                return
            except Exception as e:
                logger.warning(
                    "D85: Failed to cancel order %s: %s — checking if filled",
                    entry.order_id[:12], e,
                )

        # If position exists (order filled), close it at market
        if self._position_manager.has_position(entry.ticker):
            # 2026-05-28 (doc 175): route through cancel-blocking-stops-then-close.
            # Bug Z / Bug AI machinery cancels OTO child stops that would
            # otherwise hold inventory and 403 the close. Fast-path is
            # particularly vulnerable because OTO bracket is submitted
            # together with entry, so a fast-path-cancel-after-fill
            # always has the child stop blocking inventory.
            try:
                from src.execution.bridge import attempt_close_with_status_check
                _pos = self._position_manager.get(entry.ticker)
                _qty = abs(int(getattr(_pos, "qty", 0) or 0))
                close_result = await attempt_close_with_status_check(
                    client=self._client,
                    ticker=entry.ticker,
                    qty=_qty,
                    max_retries=3,
                    cancel_blocking_stops_first=True,
                )
                if close_result.get("succeeded"):
                    self._position_manager.remove_position(entry.ticker)
                    logger.info(
                        "D85: Market-closed filled fast-path position %s "
                        "(attempt %d/3, cancel-stops path)",
                        entry.ticker, close_result.get("attempts", 1),
                    )
                else:
                    logger.error(
                        "D85: Failed to close fast-path position %s after "
                        "%d retries with cancel-stops; last_error=%s — "
                        "removing phantom from tracker",
                        entry.ticker, close_result.get("attempts", 0),
                        close_result.get("last_error"),
                    )
                    self._position_manager.remove_position(entry.ticker)
            except Exception as e:
                logger.error(
                    "D85: Failed to close fast-path position %s: %s — removing phantom from tracker",
                    entry.ticker, e,
                )
                # Sweep fix: Remove phantom position even when close fails.
                # Previously, a failed close_position() left the ticker in
                # position_manager, creating a ghost that blocked new entries.
                self._position_manager.remove_position(entry.ticker)


# ── Utility: Get fast-path tickers ───────────────────────────────────

def get_fast_path_tickers(entries: list[FastPathEntry]) -> set[str]:
    """Get set of tickers with active fast-path entries (not cancelled/expired)."""
    return {
        e.ticker for e in entries
        if e.status in ("QUEUED", "SUBMITTED", "READY", "FILLED", "CONFIRMED")
    }


# ── Doc 84: FastPathEntry → TradeVerdict / ScoredCandidate adapters ──
#
# AT-1 fix: bridge.execute_verdict expects a TradeVerdict + ScoredCandidate.
# FAST_PATH lacks a real LLM-derived verdict (the whole point of fast path
# is to fire BEFORE the ~100s LLM pipeline produces one). These adapters
# build a synthetic verdict carrying the partial-MFCS signal that FAST_PATH
# produced at 9:20 ET pre-market scoring. The bridge's normal guards
# (D101 spread, D125 widening) then apply uniformly with the other 3
# entry paths. See doc 84 §3.


def fast_path_entry_to_verdict(fpe: "FastPathEntry"):
    """Build a synthetic TradeVerdict from a FAST_PATH entry.

    Used by main.py's FAST_PATH call site to route through
    bridge.execute_verdict per the unified post-fill bookkeeping
    contract (doc 82, doc 83).

    Field mapping (per doc 84 §3 audit):
      ticker / entry_price / stop_loss / target_prices: direct from fpe
      action: "BUY" (FAST_PATH is long-only by construction)
      confidence: clip(fpe.partial_mfcs into [0, 1]) — partial MFCS is
                  the best signal-confidence proxy at 9:30:01
      mfcs: fpe.partial_mfcs
      kelly_tier: 1 (FAST_PATH always uses tier-1 sizing per design)
      gap_pct / rvol / float_shares: from fpe.candidate
      direction: "long"
    """
    from src.core.models import TradeVerdict
    cand = fpe.candidate
    # confidence must be in [0, 1]; partial_mfcs can be in [-1, 1]
    confidence = max(0.0, min(1.0, abs(fpe.partial_mfcs)))
    return TradeVerdict(
        ticker=fpe.ticker,
        action="BUY",
        confidence=confidence,
        mfcs=fpe.partial_mfcs,
        entry_price=fpe.entry_price,
        stop_loss=fpe.stop_loss,
        target_prices=list(fpe.target_prices),
        position_size_pct=0.0,  # bridge sizes from kelly_tier; this is unused
        kelly_tier=1,
        risk_per_trade_pct=0.01,
        time_horizon="INTRADAY",
        reasoning_summary=f"FAST_PATH dip entry at {fpe.dip_factor:.0%} below premarket",
        catalyst_profile=None,
        float_shares=getattr(cand, "float_shares", None),
        gap_pct=getattr(cand, "gap_pct", 0.0),
        rvol=getattr(cand, "rvol", 0.0),
        direction="long",
    )


def fast_path_entry_to_scored(fpe: "FastPathEntry"):
    """Build a synthetic ScoredCandidate from a FAST_PATH entry.

    Used alongside fast_path_entry_to_verdict for bridge.execute_verdict's
    `scored=` parameter. The scored object's `candidate` field carries
    the same CandidateStock that the unified post-fill helper extracts
    metadata from (gap, rvol, float, market_cap, etc.).
    """
    from src.core.models import ScoredCandidate
    return ScoredCandidate(
        candidate=fpe.candidate,
        mfcs=fpe.partial_mfcs,
        agent_signals=list(fpe.agent_signals),
        component_scores={},  # FAST_PATH doesn't break MFCS into components
        risk_score=0.0,
        qualifies_for_debate=False,
    )
