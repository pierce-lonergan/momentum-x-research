"""
MOMENTUM-X Session State Persistence

### ARCHITECTURAL CONTEXT
Node ID: execution.session_state
Graph Link: docs/memory/graph_state.json → "execution.session_state"

### PURPOSE
Provides lightweight persistence for execution state that the Alpaca API
doesn't track. On restart, Alpaca gives us positions and open orders, but
NOT: tranches_filled, daily_realized_pnl, original target_prices,
signal_price, or the mapping of order_ids to tranche numbers.

This module writes a session_state.json file after every state change.
On startup, the recovery sequence merges this file with live Alpaca data.

### CRITICAL INVARIANTS
1. Atomic writes: write to .tmp then os.replace() — no corrupt state on crash.
2. Date-scoped: stale state (wrong date) is rejected on load.
3. Alpaca is authoritative for position existence — state file only enriches.
4. Graceful degradation: missing/corrupt file → D56 basic recovery.

Ref: D64 (Restart Robustness)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """Persisted metadata for a single managed position."""

    ticker: str
    qty: int = 0
    entry_price: float = 0.0
    signal_price: float = 0.0
    stop_loss: float = 0.0
    target_prices: list[float] = field(default_factory=list)
    tranches_filled: int = 0
    remaining_qty: int = 0
    realized_pnl: float = 0.0
    entry_order_id: str = ""
    stop_order_id: str = ""
    tranche_order_ids: list[str] = field(default_factory=list)
    opened_at: str = ""
    # D78: Smart exit intelligence
    trailing_stop_active: bool = False
    peak_price: float = 0.0
    # D150: Tier and catalyst fields for crash recovery
    position_tier: int = 3
    kelly_tier: int = 1
    gap_pct: float = 0.0
    catalyst_type: str = "unknown"
    sector: str = ""
    manipulation_phase: str = "UNCERTAIN"
    # D202: trade direction (long|short) — required for SHORT crash-recovery parity.
    # Without this a recovered short comes back as a long (inverted stop/exit logic).
    direction: str = "long"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PositionState:
        """Deserialize from dict with safe type coercion.

        D121 BUG-S7: Guard against malformed values in corrupt state files.
        Each conversion uses a try-except so one bad field doesn't crash recovery.
        """
        def _safe_int(val: Any, default: int = 0) -> int:
            try:
                return int(val) if val is not None else default
            except (ValueError, TypeError):
                return default

        def _safe_float(val: Any, default: float = 0.0) -> float:
            try:
                return float(val) if val is not None else default
            except (ValueError, TypeError):
                return default

        return cls(
            ticker=data.get("ticker", ""),
            qty=_safe_int(data.get("qty", 0)),
            entry_price=_safe_float(data.get("entry_price", 0)),
            signal_price=_safe_float(data.get("signal_price", 0)),
            stop_loss=_safe_float(data.get("stop_loss", 0)),
            target_prices=[_safe_float(t) for t in data.get("target_prices", [])],
            tranches_filled=_safe_int(data.get("tranches_filled", 0)),
            remaining_qty=_safe_int(data.get("remaining_qty", 0)),
            realized_pnl=_safe_float(data.get("realized_pnl", 0)),
            entry_order_id=str(data.get("entry_order_id", "")),
            stop_order_id=str(data.get("stop_order_id", "")),
            tranche_order_ids=[str(t) for t in data.get("tranche_order_ids", [])],
            opened_at=str(data.get("opened_at", "")),
            trailing_stop_active=bool(data.get("trailing_stop_active", False)),
            peak_price=_safe_float(data.get("peak_price", 0)),
            # D150: Tier and catalyst fields
            position_tier=_safe_int(data.get("position_tier", 3)),
            kelly_tier=_safe_int(data.get("kelly_tier", 1)),
            gap_pct=_safe_float(data.get("gap_pct", 0)),
            catalyst_type=str(data.get("catalyst_type", "unknown")),
            sector=str(data.get("sector", "")),
            manipulation_phase=str(data.get("manipulation_phase", "UNCERTAIN")),
            direction=str(data.get("direction", "long")),  # D202
        )


@dataclass
class SessionState:
    """Full session state for persistence."""

    version: int = 1
    session_date: str = ""
    last_update: str = ""
    daily_realized_pnl: float = 0.0
    positions: dict[str, PositionState] = field(default_factory=dict)
    # D95: Session-level flags that must survive restarts
    stopped_out_tickers: list[str] = field(default_factory=list)
    eod_close_completed: bool = False
    phase0_completed: bool = False

    def is_stale(self) -> bool:
        """Check if state is from a previous trading day."""
        if not self.session_date:
            return True
        try:
            state_date = date.fromisoformat(self.session_date)
            return state_date < date.today()
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_date": self.session_date,
            "last_update": self.last_update,
            "daily_realized_pnl": self.daily_realized_pnl,
            "positions": {
                ticker: pos.to_dict()
                for ticker, pos in self.positions.items()
            },
            "stopped_out_tickers": self.stopped_out_tickers,
            "eod_close_completed": self.eod_close_completed,
            "phase0_completed": self.phase0_completed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        positions = {}
        for ticker, pos_data in data.get("positions", {}).items():
            positions[ticker] = PositionState.from_dict(pos_data)
        return cls(
            version=data.get("version", 1),
            session_date=data.get("session_date", ""),
            last_update=data.get("last_update", ""),
            daily_realized_pnl=float(data.get("daily_realized_pnl", 0)),
            positions=positions,
            stopped_out_tickers=list(data.get("stopped_out_tickers", [])),
            eod_close_completed=bool(data.get("eod_close_completed", False)),
            phase0_completed=bool(data.get("phase0_completed", False)),
        )


class SessionStateManager:
    """
    Manages session state persistence to disk.

    Usage:
        mgr = SessionStateManager()

        # On startup:
        state = mgr.load()  # None if missing/stale/corrupt

        # After every state change:
        mgr.update_position(ticker="AAPL", tranches_filled=1, stop_loss=155.0)
        mgr.save()

        # After P&L change:
        mgr.update_daily_pnl(-350.50)
        mgr.save()
    """

    def __init__(self, state_dir: Path | str = Path("data")) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._state_dir / "session_state.json"
        self._backup_path = Path(str(self._file_path) + ".bak")
        self._state = SessionState(
            session_date=date.today().isoformat(),
            last_update=datetime.now(timezone.utc).isoformat(),
        )
        # D108: Track last saved state for diff logging
        self._last_saved_dict: dict = {}
        # D150: Lock to prevent concurrent save() calls from corrupting state
        import threading
        self._save_lock = threading.Lock()

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def file_path(self) -> Path:
        return self._file_path

    def load(self) -> SessionState | None:
        """
        Load session state from disk.

        Returns None if:
        - File doesn't exist AND backup .bak doesn't exist (or is stale)
        - File is corrupt AND backup .bak is also corrupt
        - State is stale (wrong date)

        Wed 2026-04-22 Bug B defense-in-depth: when the primary file is
        MISSING (not just corrupt), also try the .bak before giving up.
        Today's launcher had a field-name bug that deleted the primary
        file every morning; the .bak escape valve gives a recovery path
        even when the launcher misbehaves.
        """
        if not self._file_path.exists():
            # Try backup (Bug B fix). Previously this branch went straight
            # to "No session state file found — fresh start", which lost
            # ALL state when the primary file was deleted by mistake.
            if self._backup_path.exists():
                logger.warning(
                    "D64: Primary session_state.json missing -- "
                    "attempting recovery from %s",
                    self._backup_path.name,
                )
                state = self._load_backup()
                if state is not None and not state.is_stale():
                    logger.info(
                        "D64 RECOVERED: %d positions restored from backup "
                        "(primary file was missing -- check launcher D90 "
                        "logic + tests/unit/test_session_state_recovery.py)",
                        len(state.positions),
                    )
                    self._state = state
                    return state
                # Either backup was missing, corrupt, or stale -- fall through
                logger.warning(
                    "D64: Backup recovery failed (state=%s, stale=%s) -- "
                    "falling through to fresh start",
                    "loaded" if state else "None",
                    state.is_stale() if state else "n/a",
                )
            logger.info("D64: No session state file found — fresh start")
            return None

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            state = SessionState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(
                "D64: Corrupt session state file — ignoring: %s", e
            )
            # D108: Try backup file before giving up
            state = self._load_backup()
            if state is None:
                return None

        if state.is_stale():
            logger.info(
                "D64: Session state is stale (date=%s, today=%s) — fresh start",
                state.session_date, date.today().isoformat(),
            )
            return None

        n_positions = len(state.positions)
        logger.info(
            "D64: Loaded session state — %d positions, daily_pnl=$%.2f, updated=%s",
            n_positions, state.daily_realized_pnl, state.last_update,
        )
        self._state = state
        return state

    def save(self) -> None:
        """
        Atomic write: write to .tmp then os.replace() to final path.
        Prevents corruption if process crashes mid-write.

        D108: Creates .bak backup before write + logs state diffs.
        D150: Thread lock prevents concurrent writes from corrupting state.
        """
        with self._save_lock:
            self._save_inner()

    def _save_inner(self) -> None:
        """Inner save logic — called under lock."""
        self._state.last_update = datetime.now(timezone.utc).isoformat()
        new_dict = self._state.to_dict()

        # D108: Log state diffs before writing
        self._log_state_diff(new_dict)

        # D108: Backup current file before overwriting
        try:
            if self._file_path.exists():
                shutil.copy2(str(self._file_path), str(self._backup_path))
        except Exception as e:
            logger.debug("D108: Backup copy failed (non-fatal): %s", e)

        tmp_path = self._file_path.with_suffix(".tmp")

        try:
            tmp_path.write_text(
                json.dumps(new_dict, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp_path), str(self._file_path))
            self._last_saved_dict = new_dict
        except Exception as e:
            logger.warning("D64: Failed to save session state: %s", e)
            # Clean up tmp file if it exists
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _load_backup(self) -> SessionState | None:
        """D108: Try loading from .bak file when primary is corrupt."""
        if not self._backup_path.exists():
            return None
        try:
            raw = self._backup_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            state = SessionState.from_dict(data)
            logger.info("D108: Recovered from backup state file (date=%s)", state.session_date)
            return state
        except Exception as e:
            logger.warning("D108: Backup state file also corrupt: %s", e)
            return None

    def _log_state_diff(self, new_dict: dict) -> None:
        """D108: Log field-level changes between saves for debugging."""
        if not self._last_saved_dict:
            return
        old = self._last_saved_dict
        for key in set(list(old.keys()) + list(new_dict.keys())):
            if key == "last_update":
                continue
            old_val = old.get(key)
            new_val = new_dict.get(key)
            if key == "positions":
                self._log_position_diffs(old_val or {}, new_val or {})
            elif old_val != new_val:
                logger.debug("D108 state change: %s: %s -> %s", key, old_val, new_val)

    def _log_position_diffs(self, old_positions: dict, new_positions: dict) -> None:
        """D108: Log per-position field changes."""
        all_tickers = set(list(old_positions.keys()) + list(new_positions.keys()))
        for ticker in all_tickers:
            if ticker not in old_positions:
                logger.debug("D108 state change: position added: %s", ticker)
            elif ticker not in new_positions:
                logger.debug("D108 state change: position removed: %s", ticker)
            else:
                old_pos = old_positions[ticker]
                new_pos = new_positions[ticker]
                for field_key in set(list(old_pos.keys()) + list(new_pos.keys())):
                    if old_pos.get(field_key) != new_pos.get(field_key):
                        logger.debug(
                            "D108 state change: %s.%s: %s -> %s",
                            ticker, field_key,
                            old_pos.get(field_key), new_pos.get(field_key),
                        )

    def update_position(self, ticker: str, **kwargs: Any) -> None:
        """
        Update or create a position's state. Merges partial updates.

        Args:
            ticker: Stock symbol.
            **kwargs: Fields to update (e.g. tranches_filled=1, stop_loss=155.0).
        """
        if ticker in self._state.positions:
            pos = self._state.positions[ticker]
            for key, value in kwargs.items():
                if hasattr(pos, key):
                    setattr(pos, key, value)
        else:
            self._state.positions[ticker] = PositionState(ticker=ticker, **kwargs)

    def remove_position(self, ticker: str) -> None:
        """Remove a fully closed position from state."""
        self._state.positions.pop(ticker, None)

    def update_daily_pnl(self, pnl: float) -> None:
        """Update the daily realized P&L (for circuit breaker recovery)."""
        self._state.daily_realized_pnl = pnl

    def set_tranche_order_ids(self, ticker: str, order_ids: list[str]) -> None:
        """Set the tranche order IDs for a position."""
        if ticker in self._state.positions:
            self._state.positions[ticker].tranche_order_ids = order_ids
        else:
            self._state.positions[ticker] = PositionState(
                ticker=ticker, tranche_order_ids=order_ids,
            )

    # ── D95: Session-level flag persistence ──

    def add_stopped_out_ticker(self, ticker: str) -> None:
        """Record a ticker that was stopped out (prevents re-entry on restart)."""
        if ticker not in self._state.stopped_out_tickers:
            self._state.stopped_out_tickers.append(ticker)

    def get_stopped_out_tickers(self) -> set[str]:
        """Get all tickers stopped out this session."""
        return set(self._state.stopped_out_tickers)

    def set_eod_close_completed(self, completed: bool = True) -> None:
        """Mark EOD close as completed (prevents triple-fire on restart)."""
        self._state.eod_close_completed = completed

    def set_phase0_completed(self, completed: bool = True) -> None:
        """Mark Phase 0 research as completed (prevents re-run on restart)."""
        self._state.phase0_completed = completed

    def reset(self) -> None:
        """Reset state for a new trading day."""
        self._state = SessionState(
            session_date=date.today().isoformat(),
            last_update=datetime.now(timezone.utc).isoformat(),
        )
