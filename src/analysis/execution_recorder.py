"""
D215: Unified Execution Recorder

ALL 7 execution paths must call record_execution() after every order fill.
No path can submit an order without recording to:
  1. Trade journal (full feature vector)
  2. Session data collector (D210)
  3. Phantom journal (D211, if available)

This is the single point of truth for "what did the system actually trade."
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionRecorder:
    """Unified recorder for all execution paths.

    Wire this into main.py at startup. All 7 entry paths call
    record_execution() after a fill. Missing features are logged
    as warnings, not silently defaulted.

    Also tracks per-path P&L for the daily dashboard.
    """

    def __init__(
        self,
        trade_journal: Any = None,
        session_collector: Any = None,
        phantom_journal: Any = None,
    ) -> None:
        self._journal = trade_journal
        self._collector = session_collector
        self._phantom = phantom_journal
        self._execution_count = 0
        self._path_counts: dict[str, int] = {}
        # Per-path P&L tracking
        self._path_entries: dict[str, list[dict]] = {}  # path -> [{ticker, fill_price, qty, ...}]
        self._path_pnl: dict[str, float] = {}  # path -> cumulative P&L $

    def record_execution(
        self,
        *,  # All keyword-only to prevent positional arg mistakes
        ticker: str,
        side: str,
        fill_price: float,
        signal_price: float,
        qty: int,
        order_id: str,
        execution_path: str,
        # Features at entry
        gap_pct: float,
        rvol: float,
        mfcs: float | None = None,
        # Optional enrichment
        float_shares: int | None = None,
        market_cap: float | None = None,
        catalyst_type: str = "unknown",
        faller_score: float | None = None,
        prior_gap_count: int | None = None,
        is_day2_runner: bool = False,
        sector: str = "",
        # Trade parameters
        stop_loss: float = 0.0,
        target_prices: list[float] | None = None,
        kelly_tier: int = 1,
        vix_level: float | None = None,
        direction: str = "long",
        # Raw objects for journal detail
        verdict: Any = None,
        scored: Any = None,
        candidate: Any = None,
    ) -> None:
        """Record a trade execution across all tracking systems.

        Called by every execution path after fill confirmation.
        """
        self._execution_count += 1
        self._path_counts[execution_path] = self._path_counts.get(execution_path, 0) + 1

        # Compute slippage
        slippage_bps = 0.0
        if fill_price > 0 and signal_price > 0:
            slippage_bps = (fill_price - signal_price) / signal_price * 10000

        timestamp = datetime.now(timezone.utc).isoformat()

        logger.info(
            "D215 EXECUTION RECORDED: %s %s %s qty=%d fill=$%.2f signal=$%.2f "
            "slip=%+.1fbps path=%s gap=%.1f%% rvol=%.1fx mfcs=%s",
            side.upper(), ticker, direction, qty, fill_price, signal_price,
            slippage_bps, execution_path, gap_pct * 100, rvol,
            f"{mfcs:.3f}" if mfcs is not None else "N/A",
        )

        # Track for per-path P&L dashboard
        if execution_path not in self._path_entries:
            self._path_entries[execution_path] = []
        self._path_entries[execution_path].append({
            "ticker": ticker, "side": side, "fill_price": fill_price,
            "signal_price": signal_price, "qty": qty, "gap_pct": gap_pct,
            "rvol": rvol, "mfcs": mfcs, "timestamp": timestamp,
        })

        # 1. Trade Journal
        if self._journal is not None:
            try:
                from src.utils.trade_logger import generate_trade_id
                trade_id = generate_trade_id(ticker)

                # Create or find existing entry
                # Bug AV fix (PROMPT_09, 2026-04-30): direction-aware journal
                # lookup. Pre-fix: any entry with action in BUY/STRONG_BUY/SHORT
                # matched regardless of caller's direction. So a SHORT call
                # would mutate a long BUY entry on the same ticker the same day,
                # silently corrupting both records (the OGN failure mode pattern
                # documented in docs/audits/2026-04-30_bug_au_root_cause.md §1).
                # New behavior: long calls match BUY/STRONG_BUY only; short
                # calls match SHORT only. Keeps two-path same-ticker collisions
                # from cross-mutating each other's records.
                if direction == "short":
                    _target_actions = ("SHORT",)
                else:
                    _target_actions = ("BUY", "STRONG_BUY")
                j_entry = None
                for tid, ent in self._journal._entries.items():
                    if ent.ticker == ticker and ent.action in _target_actions:
                        j_entry = ent
                        trade_id = tid
                        break

                if j_entry is None:
                    # Create new entry for paths that didn't go through Phase 2
                    if candidate is not None:
                        j_entry = self._journal.create_entry(
                            trade_id, candidate, phase=execution_path,
                        )
                    else:
                        # Minimal entry without candidate object
                        from src.analysis.trade_journal import JournalEntry
                        j_entry = JournalEntry(
                            trade_id=trade_id,
                            ticker=ticker,
                            action=side.upper(),
                            phase=execution_path,
                            timestamp=timestamp,
                        )
                        j_entry.gap_pct = gap_pct
                        j_entry.rvol = rvol
                        j_entry.mfcs = mfcs
                        j_entry.current_price = signal_price
                        self._journal._entries[trade_id] = j_entry

                # Record fill
                self._journal.record_fill(
                    trade_id, order_id,
                    fill_price, qty,
                    slippage_bps=slippage_bps,
                    order_status="filled",
                )
                self._journal.flush(j_entry)
            except Exception as e:
                logger.warning("D215: Journal recording failed for %s: %s", ticker, e)

        # 2. Session Collector
        if self._collector is not None:
            try:
                self._collector.record_trade(
                    ticker=ticker,
                    entry_price=fill_price,
                    shares=qty,
                )
            except Exception as e:
                logger.debug("D215: Session collector failed for %s: %s", ticker, e)

        # 3. Phantom Journal
        if self._phantom is not None:
            try:
                self._phantom.update_gate(ticker, f"EXECUTED_VIA_{execution_path}")
            except Exception:
                pass

    def record_close(
        self,
        ticker: str,
        exit_price: float,
        exit_qty: int,
        pnl_dollars: float,
        exit_reason: str = "unknown",
    ) -> None:
        """Record a position close. Updates per-path P&L.

        Called when any position is closed (stop, target, EOD, etc.)
        Matches the OLDEST unclosed entry for this ticker (FIFO) to handle
        re-entry scenarios correctly.
        """
        # D217 FIX: Find the OLDEST unclosed entry by timestamp across ALL paths.
        # Previously iterated by path insertion order, which could match the
        # wrong entry if a ticker had entries in multiple paths.
        _oldest_entry = None
        _oldest_path = None
        _oldest_ts = None
        for path, entries in self._path_entries.items():
            for entry in entries:
                if entry["ticker"] == ticker and not entry.get("closed"):
                    _entry_ts = entry.get("timestamp", "")
                    if _oldest_ts is None or _entry_ts < _oldest_ts:
                        _oldest_entry = entry
                        _oldest_path = path
                        _oldest_ts = _entry_ts

        if _oldest_entry is not None:
            _oldest_entry["closed"] = True
            _oldest_entry["exit_price"] = exit_price
            _oldest_entry["pnl_dollars"] = pnl_dollars
            _oldest_entry["exit_reason"] = exit_reason
            self._path_pnl[_oldest_path] = self._path_pnl.get(_oldest_path, 0.0) + pnl_dollars

            logger.info(
                "D215 PATH P&L: %s closed via %s | P&L=$%+.2f | "
                "Path %s cumulative: $%+.2f",
                ticker, exit_reason, pnl_dollars,
                _oldest_path, self._path_pnl[_oldest_path],
            )
            return

    def get_stats(self) -> dict:
        """Return execution recording statistics with per-path P&L."""
        return {
            "total_executions": self._execution_count,
            "path_counts": dict(self._path_counts),
            "path_pnl": dict(self._path_pnl),
            "path_entries": {
                path: [
                    {
                        "ticker": e["ticker"],
                        "fill_price": e["fill_price"],
                        "pnl_dollars": e.get("pnl_dollars", 0),
                        "closed": e.get("closed", False),
                        "exit_reason": e.get("exit_reason", "open"),
                    }
                    for e in entries
                ]
                for path, entries in self._path_entries.items()
            },
        }

    def print_dashboard(self) -> None:
        """Print per-path P&L dashboard to log."""
        if not self._path_entries:
            return

        lines = [
            "",
            "=" * 60,
            "  D215 PER-PATH P&L DASHBOARD",
            "=" * 60,
        ]

        total_pnl = 0.0
        total_trades = 0
        for path in sorted(self._path_entries.keys()):
            entries = self._path_entries[path]
            n = len(entries)
            closed = [e for e in entries if e.get("closed")]
            pnl = self._path_pnl.get(path, 0.0)
            wins = sum(1 for e in closed if e.get("pnl_dollars", 0) > 0)
            losses = len(closed) - wins
            wr = wins / len(closed) * 100 if closed else 0

            total_pnl += pnl
            total_trades += n

            # Red flag: rolling 10-trade PF check
            recent_pnls = [e.get("pnl_dollars", 0) for e in closed[-10:]]
            if len(recent_pnls) >= 5:
                r_wins = sum(p for p in recent_pnls if p > 0)
                r_losses = abs(sum(p for p in recent_pnls if p <= 0))
                r_pf = r_wins / r_losses if r_losses > 0 else 10
                flag = " RED FLAG: PF<0.8!" if r_pf < 0.8 else ""
            else:
                flag = ""

            lines.append(
                f"  {path:<22s} {n:>3d} trades ({wins}W/{losses}L) "
                f"WR={wr:>4.0f}% P&L=${pnl:>+9.2f}{flag}"
            )

        lines.append(f"  {'─' * 55}")
        lines.append(f"  {'TOTAL':<22s} {total_trades:>3d} trades P&L=${total_pnl:>+9.2f}")
        lines.append("=" * 60)

        for line in lines:
            logger.info(line)
