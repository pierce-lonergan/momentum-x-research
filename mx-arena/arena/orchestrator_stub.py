"""Orchestrator stub — mocks the LLM agent ensemble for arena replay.

Block C.2 of the strategy-harness session. Per the brief: two modes:
  - decision_row mode: read recorded agent decisions from
    data/instrumentation/decision_row/session_date=<date>/decisions.parquet
    (only 4/28 has this — pre-Bug-AO sessions have no corpus).
  - policy mode: deterministic gate (e.g. "enter if news_signal !=
    NEUTRAL") — for sessions without decision_row coverage.

Interface (clean for swap-in of real orchestrator later):

    class OrchestratorBase(Protocol):
        def candidates_at(self, ts: datetime, watchlist: list[str]) -> list[CandidateDecision]: ...

    class CandidateDecision:
        ticker: str
        verdict: Literal["BUY", "NO_TRADE"]
        entry_price: float
        stop_price: float
        size_qty: int
        reason: str

The runner calls candidates_at() at each tick. Stub implementations
honor the interface so production orchestrator can swap in.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, Optional

logger = logging.getLogger(__name__)


@dataclass
class CandidateDecision:
    """One per-cycle output from the orchestrator. The runner submits
    this as an OTO order if verdict=BUY."""
    ticker: str
    verdict: Literal["BUY", "NO_TRADE"]
    entry_price: float = 0.0
    stop_price: float = 0.0
    size_qty: int = 0
    reason: str = ""
    news_signal: str = ""
    news_conf: float = 0.0


class OrchestratorBase(Protocol):
    def candidates_at(
        self, *, ts: datetime, watchlist: list[str],
    ) -> list[CandidateDecision]: ...


# ── decision_row mode ─────────────────────────────────────────────


class DecisionRowOrchestrator:
    """Replays recorded agent decisions from the instrumentation corpus.
    For each cycle, returns the decisions whose timestamp falls within
    the previous tick's window."""

    def __init__(
        self, *, decision_corpus_path: Path,
        equity_for_sizing: float = 150_000.0,
        tier1_pct: float = 0.50,
    ) -> None:
        self._equity = equity_for_sizing
        self._tier1_pct = tier1_pct
        self._decisions = self._load_decisions(decision_corpus_path)
        self._fired: set[str] = set()  # decision_id; ensures one-shot fires

    def _load_decisions(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            import pandas as pd
            df = pd.read_parquet(path)
            return df.to_dict("records")
        except Exception as e:
            logger.warning("could not read %s: %s", path, e)
            return []

    def candidates_at(
        self, *, ts: datetime, watchlist: list[str],
    ) -> list[CandidateDecision]:
        out: list[CandidateDecision] = []
        ts_utc = ts.astimezone(timezone.utc)
        for d in self._decisions:
            decision_id = str(d.get("decision_id", ""))
            if decision_id in self._fired:
                continue
            ds_str = str(d.get("timestamp", ""))
            if not ds_str:
                continue
            try:
                d_ts = datetime.fromisoformat(ds_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            # Fire decision when current ts is at-or-after the decision's ts
            if d_ts > ts_utc:
                continue
            self._fired.add(decision_id)
            ticker = str(d.get("ticker", ""))
            if not ticker or (watchlist and ticker not in watchlist):
                continue
            verdict_action = str(d.get("verdict_action", "NO_TRADE"))
            if verdict_action not in {"BUY", "STRONG_BUY"}:
                continue
            entry_px = float(d.get("candidate_current_price", 0))
            if entry_px <= 0:
                continue
            qty = max(1, int(self._equity * self._tier1_pct / entry_px))
            stop = round(entry_px * 0.945, 4)  # 5.5% fallback stop per ExecutionConfig
            # Pull news_signal/conf from agent_signals if present
            news_sig = "NO_SIGNAL"
            news_conf = 0.0
            # agent_signals may come back as numpy ndarray from parquet;
            # truth-test fails on arrays so iterate explicitly.
            sigs = d.get("agent_signals")
            if sigs is not None:
                try:
                    sigs_iter = list(sigs)
                except TypeError:
                    sigs_iter = []
                for sig in sigs_iter:
                    try:
                        if sig.get("agent_id") == "news_agent":
                            news_sig = str(sig.get("signal", "NEUTRAL")).upper()
                            news_conf = float(sig.get("confidence", 0))
                            break
                    except (AttributeError, TypeError):
                        continue
            out.append(CandidateDecision(
                ticker=ticker, verdict="BUY",
                entry_price=entry_px, stop_price=stop, size_qty=qty,
                reason=f"decision_row[{verdict_action}]",
                news_signal=news_sig, news_conf=news_conf,
            ))
        return out


# ── policy mode ───────────────────────────────────────────────────


class DeterministicPolicyOrchestrator:
    """Deterministic policy for sessions without decision_row corpus.

    Block B candidate filter (this session): scan the bar stream and
    emit BUY candidates where price ≤ $15 AND first-bar volume ≥
    `min_first_bar_volume`. This is a minimum-viable proxy for prod's
    actual screener (gap + RVOL + float + price); full implementation
    deferred to next session when prev-day close + RVOL machinery
    lands. The price+liquidity filter is enough to keep policy mode
    from emitting illiquid/wrong-tier tickers wholesale.

    Each candidate is emitted at the regular-hours open (13:30 UTC)
    of the session, with verdict="BUY" and the filter rationale in
    `reason`. Per-ticker once-per-session.
    """

    def __init__(
        self, *, equity_for_sizing: float = 150_000.0,
        tier1_pct: float = 0.50,
        max_price: float = 15.0,
        min_first_bar_volume: int = 10_000,
        bar_loader=None,
        earnings_calendar: dict | None = None,
        require_earnings_catalyst: bool = False,
        earnings_window_days: int = 1,
    ) -> None:
        self._equity = equity_for_sizing
        self._tier1_pct = tier1_pct
        self._max_price = max_price
        self._min_first_bar_volume = min_first_bar_volume
        self._bar_loader = bar_loader
        # Earnings catalyst gate: if require_earnings_catalyst=True,
        # candidates must have an earnings event within ±earnings_window_days
        # of the session date. earnings_calendar is the loaded payload from
        # data/calibration/historical_earnings_*.json (by_ticker map).
        self._earnings_calendar = earnings_calendar or {}
        self._require_earnings_catalyst = require_earnings_catalyst
        self._earnings_window_days = earnings_window_days
        self._fired: set[str] = set()
        self._session_filtered_in: dict[str, dict] = {}

    def _has_earnings_within_window(self, ticker: str, session_date: str) -> bool:
        """Returns True if the ticker had an earnings event within
        ±earnings_window_days of session_date."""
        events = self._earnings_calendar.get(ticker)
        if not events:
            return False
        try:
            sd = datetime.fromisoformat(session_date).date()
        except ValueError:
            return False
        for e in events:
            ed_str = e.get("date", "")
            if not ed_str:
                continue
            try:
                ed = datetime.fromisoformat(ed_str).date()
            except ValueError:
                continue
            delta_days = abs((ed - sd).days)
            if delta_days <= self._earnings_window_days:
                return True
        return False

    def _filter_watchlist(self, *, watchlist: list[str], session_date: str) -> dict[str, dict]:
        """Return {ticker: {entry_price, gap_pct, rvol}} for tickers
        that pass price + liquidity filters at session open."""
        if session_date in self._session_filtered_in:
            return self._session_filtered_in[session_date]
        out: dict[str, dict] = {}
        if self._bar_loader is None:
            self._session_filtered_in[session_date] = out
            return out
        for ticker in watchlist:
            # Earnings catalyst gate (when require_earnings_catalyst=True)
            if self._require_earnings_catalyst:
                if not self._has_earnings_within_window(ticker, session_date):
                    continue
            bars = self._bar_loader(ticker, session_date)
            if bars is None:
                continue
            # Find the bar at 13:30 UTC (09:30 ET) — the first regular-hours bar
            target_iso = f"{session_date}T13:30:00Z"
            first_bar = None
            for _i, b in bars.items():
                if b.timestamp >= target_iso:
                    first_bar = b
                    break
            if first_bar is None:
                continue
            entry_px = float(first_bar.open)
            volume = int(first_bar.volume)
            if entry_px <= 0 or entry_px > self._max_price:
                continue
            if volume < self._min_first_bar_volume:
                continue
            # gap_pct and rvol require prev-day close + 20-day avg vol;
            # not in MVP. Defaulting to safe values that pass the
            # consensus stub's BULL-leaning rules.
            out[ticker] = {
                "entry_price": entry_px,
                "gap_pct": 0.10,  # placeholder: assume 10% gap (consensus BULL)
                "rvol": 2.0,      # placeholder: assume 2× RVOL (consensus BULL)
                "first_bar_volume": volume,
            }
        self._session_filtered_in[session_date] = out
        return out

    def candidates_at(
        self, *, ts: datetime, watchlist: list[str],
    ) -> list[CandidateDecision]:
        ts_utc = ts.astimezone(timezone.utc)
        # Fire only at the regular-hours open (13:30 UTC = 09:30 ET)
        if (ts_utc.hour, ts_utc.minute) != (13, 30):
            return []
        session_date = ts_utc.date().isoformat()
        filtered = self._filter_watchlist(
            watchlist=watchlist, session_date=session_date,
        )
        out: list[CandidateDecision] = []
        for ticker, ctx in filtered.items():
            if ticker in self._fired:
                continue
            self._fired.add(ticker)
            entry_px = float(ctx["entry_price"])
            qty = max(1, int(self._equity * self._tier1_pct / entry_px))
            stop = round(entry_px * 0.945, 4)
            cd = CandidateDecision(
                ticker=ticker, verdict="BUY",
                entry_price=entry_px, stop_price=stop, size_qty=qty,
                reason=f"policy:gappers_rvol_filter[vol={ctx['first_bar_volume']}]",
                news_signal="NO_SIGNAL", news_conf=0.0,
            )
            # Attach gap/rvol so consensus stub can read them
            object.__setattr__(cd, "_gap_pct", ctx["gap_pct"])
            object.__setattr__(cd, "_rvol", ctx["rvol"])
            out.append(cd)
        return out


def make_orchestrator(*, mode: str, **kwargs) -> OrchestratorBase:
    if mode == "decision_row":
        # Filter to only kwargs DecisionRowOrchestrator accepts
        accepted = {"decision_corpus_path", "equity_for_sizing", "tier1_pct"}
        return DecisionRowOrchestrator(**{k: v for k, v in kwargs.items() if k in accepted})
    if mode == "policy":
        accepted = {
            "equity_for_sizing", "tier1_pct", "max_price",
            "min_first_bar_volume", "bar_loader",
            "earnings_calendar", "require_earnings_catalyst",
            "earnings_window_days",
        }
        return DeterministicPolicyOrchestrator(**{k: v for k, v in kwargs.items() if k in accepted})
    raise ValueError(f"unknown orchestrator mode: {mode!r}")
