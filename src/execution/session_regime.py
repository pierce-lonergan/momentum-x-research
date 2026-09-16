"""D198: Live Session Regime Detector.

Monitors real-time session performance and adapts execution parameters.
Prevents cascade losses by reducing exposure after consecutive stops.
Pauses entries when intraday VIX spikes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SessionRegime(str, Enum):
    NORMAL = "normal"        # Standard parameters
    CAUTIOUS = "cautious"    # After 1 stop-out, reduce size 50%
    DEFENSIVE = "defensive"  # After 2 consecutive stops, reduce size 75%
    HALTED = "halted"        # After 3 consecutive stops OR drawdown > 3%, pause all entries
    MOMENTUM = "momentum"    # After 2 consecutive wins, increase size (Kelly kicks in)


@dataclass
class SessionPerformance:
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    starting_equity: float = 0.0
    current_equity: float = 0.0
    session_start: Optional[datetime] = None

    # VIX tracking
    vix_at_open: float = 0.0
    vix_current: float = 0.0
    vix_spike_detected: bool = False  # VIX jumped >3 points intraday


class SessionRegimeDetector:
    """Real-time intra-session adaptation system.

    Monitors session performance and adjusts position sizing to prevent
    cascade losses and capitalise on momentum streaks.

    Integration (in main.py):
        - initialize_session()  — once, after Alpaca equity fetch
        - should_allow_new_entry()  — before every execute_verdict
        - get_size_multiplier()  — multiply into verdict.position_size_pct
        - record_trade_result()  — after every close_with_attribution
        - update_equity()  — periodically (e.g., per Phase 3 cycle)
        - update_vix()  — periodically when VIX data is available
    """

    def __init__(self, config=None) -> None:
        self._perf = SessionPerformance()
        self._regime = SessionRegime.NORMAL
        self._config = config

        # Thresholds
        self._cautious_after_losses: int = 1
        self._defensive_after_losses: int = 2
        self._halt_after_losses: int = 3
        self._halt_drawdown_pct: float = 0.03   # 3% session drawdown → halt
        self._momentum_after_wins: int = 2
        self._vix_spike_threshold: float = 3.0  # Points intraday

        self._size_multipliers: dict[SessionRegime, float] = {
            SessionRegime.NORMAL: 1.0,
            SessionRegime.CAUTIOUS: 0.50,
            SessionRegime.DEFENSIVE: 0.25,
            SessionRegime.HALTED: 0.0,   # No new entries
            SessionRegime.MOMENTUM: 1.5,  # Kelly boost
        }

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def initialize_session(self, starting_equity: float, vix_at_open: float = 0.0) -> None:
        """Call at session start, after Alpaca equity is fetched."""
        self._perf = SessionPerformance(
            starting_equity=starting_equity,
            current_equity=starting_equity,
            vix_at_open=vix_at_open,
            vix_current=vix_at_open,
            session_start=datetime.now(timezone.utc),
        )
        self._regime = SessionRegime.NORMAL
        logger.info(
            "D198: Session initialized — equity=$%.2f vix_open=%.1f regime=%s",
            starting_equity, vix_at_open, self._regime.value,
        )

    # ── State updates ────────────────────────────────────────────────────────

    def record_trade_result(self, pnl: float, is_win: bool) -> None:
        """Call after each trade closes (stop-out, trailing exit, profit take, EOD).

        Updates consecutive counters, total PnL, and recomputes the regime.
        """
        p = self._perf
        p.trades_taken += 1
        p.total_pnl += pnl

        if is_win:
            p.wins += 1
            p.consecutive_wins += 1
            p.consecutive_losses = 0   # reset loss streak
        else:
            p.losses += 1
            p.consecutive_losses += 1
            p.consecutive_wins = 0     # reset win streak

        old_regime = self._regime
        self._regime = self._compute_regime()

        if self._regime != old_regime:
            logger.warning(
                "D198 REGIME CHANGE: %s -> %s "
                "(%d consecutive %s, drawdown=%.2f%%, vix_spike=%s)",
                old_regime.value, self._regime.value,
                p.consecutive_losses if not is_win else p.consecutive_wins,
                "loss" if not is_win else "win",
                p.max_drawdown_pct * 100,
                p.vix_spike_detected,
            )

    def update_vix(self, current_vix: float) -> None:
        """Call periodically with latest VIX. Detects intraday spikes."""
        p = self._perf
        p.vix_current = current_vix

        if p.vix_at_open > 0 and not p.vix_spike_detected:
            spike = current_vix - p.vix_at_open
            if spike >= self._vix_spike_threshold:
                p.vix_spike_detected = True
                old_regime = self._regime
                self._regime = self._compute_regime()
                logger.warning(
                    "D198 VIX SPIKE: %.1f → %.1f (+%.1f pts) — regime=%s",
                    p.vix_at_open, current_vix, spike, self._regime.value,
                )
                if self._regime != old_regime:
                    logger.warning(
                        "D198 REGIME CHANGE: %s -> %s (VIX spike +%.1f)",
                        old_regime.value, self._regime.value, spike,
                    )

    def update_equity(self, current_equity: float) -> None:
        """Call periodically to track intra-session drawdown."""
        p = self._perf
        p.current_equity = current_equity

        if p.starting_equity > 0:
            drawdown = (p.starting_equity - current_equity) / p.starting_equity
            if drawdown > p.max_drawdown_pct:
                p.max_drawdown_pct = drawdown

        old_regime = self._regime
        self._regime = self._compute_regime()

        if self._regime != old_regime:
            logger.warning(
                "D198 REGIME CHANGE: %s -> %s (drawdown=%.2f%%)",
                old_regime.value, self._regime.value, p.max_drawdown_pct * 100,
            )

    # ── Queries ──────────────────────────────────────────────────────────────

    def get_regime(self) -> SessionRegime:
        """Return current session regime."""
        return self._regime

    def get_size_multiplier(self) -> float:
        """Position size multiplier for current regime.

        Multiply this against all other sizing logic (D150, D160, VIX).
        Returns 0.0 when halted to prevent any new entries.
        """
        return self._size_multipliers[self._regime]

    def should_allow_new_entry(self) -> tuple[bool, str]:
        """Check if new entries are permitted under the current regime.

        Returns:
            (True, "")  when entry is allowed.
            (False, reason)  when entry should be blocked.
        """
        p = self._perf
        if self._regime == SessionRegime.HALTED:
            reasons = []
            if p.consecutive_losses >= self._halt_after_losses:
                reasons.append(f"{p.consecutive_losses} consecutive losses")
            if p.max_drawdown_pct >= self._halt_drawdown_pct:
                reasons.append(f"session drawdown {p.max_drawdown_pct*100:.1f}%")
            if p.vix_spike_detected:
                reasons.append(f"VIX spike {p.vix_current:.1f} (+{p.vix_current-p.vix_at_open:.1f})")
            reason = "; ".join(reasons) if reasons else "regime=HALTED"
            return False, reason

        return True, ""

    def get_status_report(self) -> str:
        """One-line status string for dashboard / structured logging."""
        p = self._perf
        return (
            f"D198 regime={self._regime.value} "
            f"trades={p.trades_taken} W={p.wins} L={p.losses} "
            f"consec_L={p.consecutive_losses} consec_W={p.consecutive_wins} "
            f"pnl=${p.total_pnl:+.2f} dd={p.max_drawdown_pct*100:.2f}% "
            f"size_mult={self.get_size_multiplier():.2f}x "
            f"vix={p.vix_current:.1f}{'⚠' if p.vix_spike_detected else ''}"
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _compute_regime(self) -> SessionRegime:
        """Pure function: derive regime from current performance snapshot."""
        p = self._perf

        # HALTED takes precedence — drawdown OR consecutive losses OR VIX spike
        if (
            p.consecutive_losses >= self._halt_after_losses
            or p.max_drawdown_pct >= self._halt_drawdown_pct
            or p.vix_spike_detected
        ):
            return SessionRegime.HALTED

        if p.consecutive_losses >= self._defensive_after_losses:
            return SessionRegime.DEFENSIVE

        if p.consecutive_losses >= self._cautious_after_losses:
            return SessionRegime.CAUTIOUS

        if p.consecutive_wins >= self._momentum_after_wins:
            return SessionRegime.MOMENTUM

        return SessionRegime.NORMAL
