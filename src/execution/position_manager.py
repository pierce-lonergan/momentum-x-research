"""
MOMENTUM-X Position Manager

### ARCHITECTURAL CONTEXT
Node ID: execution.position_manager
Graph Link: docs/memory/graph_state.json → "execution.position_manager"

### RESEARCH BASIS
Stateful position lifecycle per ADR-003 §2.
Scaled exits (3-tranche) to maximize winner capture.
Circuit breaker at -5% daily P&L (ExecutionConfig.daily_loss_limit_pct).
Stop ratcheting: breakeven after T1, T1-target after T2.

### CRITICAL INVARIANTS
1. Circuit breaker halts ALL new entries when daily P&L < -5% (ADR-003 §2).
2. Time stop: close all intraday positions by 3:45 PM ET (ExecutionConfig.close_positions_by).
3. Stop ONLY moves UP (ratchets), never down.
4. Slippage tracked per ADR-003 §3 (H-005).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from src.monitoring.metrics import get_metrics
from datetime import datetime, timedelta, timezone

from config.settings import ExecutionConfig

logger = logging.getLogger(__name__)


@dataclass
class ManagedPosition:
    """
    A tracked position with lifecycle state.

    Node ID: execution.position_manager.ManagedPosition
    Ref: ADR-003 §2
    """

    ticker: str
    qty: int
    entry_price: float
    signal_price: float  # For slippage analysis (H-005, ADR-003 §3)
    stop_loss: float
    target_prices: list[float] = field(default_factory=list)
    order_id: str = ""
    fill_price: float | None = None  # Actual fill from Alpaca
    tranches_filled: int = 0  # 0, 1, 2, 3
    remaining_qty: int = 0
    realized_pnl: float = 0.0
    stop_order_id: str = ""  # D64: Actual stop order ID for StopResubmitter recovery
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # D78: Smart exit intelligence fields
    peak_price: float = 0.0  # Running high-water mark since entry
    trailing_stop_active: bool = False  # D63: True once +2% gain triggers trailing
    entry_spread: float = 0.0  # Bid-ask spread at entry for spread widening detection
    entry_volume: int = 0  # Volume at entry time for volume fade detection
    peak_volume: int = 0  # Highest volume bar seen since entry
    # D85: Fast-path entry tracking
    source: str = "FULL_EVAL"  # "FULL_EVAL" or "FAST_PATH"
    # D106: Manipulation lifecycle tracking
    manipulation_phase: str = "UNCERTAIN"  # ORGANIC_MOMENTUM, PROMOTIONAL_EARLY, etc.
    # D110: Catalyst and sector data for exit strategies + contagion network
    catalyst_type: str = "unknown"  # From news agent (fda_approval, earnings_beat, etc.)
    sector: str = ""  # From portfolio_risk.get_sector() at entry time
    gap_pct: float = 0.0  # Gap percentage at scanner detection time
    kelly_tier: int = 1  # D115: Kelly conviction tier (1-4) at entry
    # D118: Catalyst-profiled exit strategy parameters
    catalyst_half_life_minutes: int = 20  # From CatalystProfile or static table
    catalyst_gratitude_decay: float = 0.05  # From CatalystProfile or default
    # D150: Tier assignment for aggressive sizing enforcement
    position_tier: int = 3  # 1=aggressive (50%), 2=moderate (30%), 3=standard (15%)
    # D161: Trade direction — "long" (standard buy) or "short" (sell-short fader)
    direction: str = "long"
    # D214: Archetype exit pre-entry features (frozen at position creation)
    rvol: float = 1.0
    prior_gap_count: int = 0
    is_day2_runner: bool = False
    # D91 / Bug E (2026-04-22): when True, position is slated for an
    # imminent operator-driven close (e.g., overnight cleanup at market
    # open). The eval queue / candidate scorer must skip these so we
    # don't waste agent cycles re-scoring something we're about to flatten.
    # Set by `_is_overnight_position` detection at startup.
    close_pending: bool = False

    def __post_init__(self) -> None:
        if self.remaining_qty == 0:
            self.remaining_qty = self.qty

    @property
    def slippage_bps(self) -> float:
        """
        Slippage in basis points: (fill - signal) / signal × 10000.
        Ref: ADR-003 §3
        """
        if self.fill_price is None or self.signal_price <= 0:
            return 0.0
        return (self.fill_price - self.signal_price) / self.signal_price * 10_000


@dataclass
class ExitTranche:
    """A single tranche in the scaled exit plan."""

    tranche_number: int  # 1, 2, or 3
    qty: int
    target: float
    exit_type: str = "limit"  # "limit" or "trailing_stop"


class PositionManager:
    """
    Stateful position lifecycle manager.

    Node ID: execution.position_manager
    Graph Link: docs/memory/graph_state.json → "execution.position_manager"

    Manages:
    - Circuit breaker (daily P&L threshold)
    - Scaled exits (3-tranche)
    - Stop ratcheting (breakeven after T1, T1 after T2)
    - Time stops (close all by 3:45 PM ET)

    Ref: ADR-003 §2 (Stateful Position Lifecycle)
    Ref: MOMENTUM_LOGIC.md §6 (Position sizing)
    """

    def __init__(
        self,
        config: ExecutionConfig,
        starting_equity: float,
    ) -> None:
        self._config = config
        self._starting_equity = starting_equity
        self._daily_realized_pnl: float = 0.0
        self._positions: dict[str, ManagedPosition] = {}
        self._scored_cache: dict[str, Any] = {}  # ticker → ScoredCandidate
        # D216: Track recently closed tickers to prevent D94b race condition
        self._recently_closed: set[str] = set()

    @property
    def starting_equity(self) -> float:
        """Bug AN (Tier 3 #11, 2026-04-27): public accessor for the
        session's starting equity.

        The underscore-private `_starting_equity` was the source of a
        latent typo bug in `eod_recon.run_eod_invariants` — the
        function did `getattr(pm, "starting_equity", 0)` which always
        returned 0 (the default), making `internal_equity_estimate`
        always $0 and `delta = broker_equity - 0 = broker_equity`,
        triggering D230 RECON_WARN every 30s of every session for an
        unknown duration.

        Adding this public property:
          1. fixes the eod_recon caller (uses the public name now)
          2. preserves backward compat with tests that already set
             `pm.starting_equity = X` on MagicMock fixtures
          3. doesn't change the private convention for the rest of
             the class internals.
        """
        return self._starting_equity

    @property
    def is_circuit_breaker_active(self) -> bool:
        """
        Circuit breaker triggers when daily P&L < -daily_loss_limit_pct.
        Ref: ADR-003 §2 (Circuit Breaker)
        """
        # D121 BUG-R5: Guard against zero/negative starting equity which
        # would make threshold=0 and trigger breaker on any negative PnL.
        if self._starting_equity <= 0:
            return False
        threshold = -self._starting_equity * self._config.daily_loss_limit_pct
        return self._daily_realized_pnl < threshold

    def can_enter_new_position(self, tier: int = 3) -> bool:
        """
        Check if a new position can be opened.
        Blocked by: circuit breaker, max positions, tier-specific limits,
        total portfolio allocation.

        D150: Tier max_concurrent + total allocation guard.
        """
        if self.is_circuit_breaker_active:
            get_metrics().circuit_breaker_activations.inc()
            logger.warning("Circuit breaker ACTIVE — no new entries")
            return False
        if len(self._positions) >= self._config.max_positions:
            logger.warning("Max positions reached (%d)", self._config.max_positions)
            return False
        # D150: Enforce tier-specific concurrent position limits
        if getattr(self._config, "paper_aggressive_mode", False):
            tier_count = sum(
                1 for p in self._positions.values()
                if getattr(p, "position_tier", 3) == tier
            )
            tier_limits = {
                1: getattr(self._config, "tier1_max_concurrent", 2),
                2: getattr(self._config, "tier2_max_concurrent", 3),
                3: getattr(self._config, "tier3_max_concurrent", 5),
            }
            max_for_tier = tier_limits.get(tier, 5)
            if tier_count >= max_for_tier:
                logger.warning(
                    "D150: Tier %d at max concurrent (%d/%d)",
                    tier, tier_count, max_for_tier,
                )
                return False

            # D150: Total portfolio allocation guard — prevent over-allocation
            # Instead of blocking all trades, only block THIS tier if it won't fit.
            # Smaller tiers can still fit in remaining capital.
            tier_pcts = {
                1: getattr(self._config, "tier1_position_pct", 0.50),
                2: getattr(self._config, "tier2_position_pct", 0.30),
                3: getattr(self._config, "tier3_position_pct", 0.15),
            }
            current_alloc = sum(
                tier_pcts.get(getattr(p, "position_tier", 3), 0.15)
                for p in self._positions.values()
            )
            incoming_pct = tier_pcts.get(tier, 0.15)
            remaining = 1.0 - current_alloc
            if incoming_pct > remaining:
                logger.warning(
                    "D150: Tier %d (%.0f%%) won't fit — %.0f%% allocated, "
                    "%.0f%% remaining. Try smaller tier.",
                    tier, incoming_pct * 100,
                    current_alloc * 100, remaining * 100,
                )
                return False
        return True

    def record_realized_pnl(self, pnl: float) -> None:
        """
        Record a realized P&L event (from a closed position or tranche).
        Accumulates toward circuit breaker threshold.
        """
        self._daily_realized_pnl += pnl
        logger.info(
            "Daily P&L: $%.2f (%.2f%% of equity)",
            self._daily_realized_pnl,
            (self._daily_realized_pnl / self._starting_equity) * 100,
        )

    # ── D56: Broker Position Sync ────────────────────────────────────
    def has_position(self, ticker: str) -> bool:
        """Check if we already hold a position in this ticker."""
        return ticker in self._positions

    def get_position(self, ticker: str) -> "ManagedPosition | None":
        """D279 (2026-05-05): return tracked position by ticker, or None.

        Companion to `has_position()`. Required by `_close_overnight_position`
        in src/execution/bridge.py which previously crashed with
        ``AttributeError: 'PositionManager' object has no attribute
        'get_position'`` when D91 attempted to close MRAM/VLN/WNW at the
        Tuesday 2026-05-05 open. Today's positions only closed because the
        D165 tranche-exit ladder fired independently — D91, the canonical
        overnight-flat safety mechanism, was silently broken.
        """
        return self._positions.get(ticker)

    # ── Bug AM (Tier 3 #10, 2026-04-27): External-stop attach API ──
    def attach_external_stop(
        self,
        *,
        ticker: str,
        stop_order_id: str,
        stop_price: float,
        qty: int | None = None,
    ) -> bool:
        """Bug AM: register a broker stop order with the position tracker.

        Solves today's LIDR scenario where the operator submitted a
        manual GTC stop ($2.10 @ broker) while the tracker showed
        `stop=$2.29 COMPUTED DEFAULT, stop_order_id=""`. D230
        RECON_WARN fired every 30s with no remediation path. Now the
        operator (or automated startup discovery) can call:

            position_manager.attach_external_stop(
                ticker='LIDR',
                stop_order_id='859be64d-718e-4f6d-a09a-932d03a79916',
                stop_price=2.10,
            )

        And the tracker reflects reality. D230 stops firing.

        Args:
            ticker: Symbol to attach the stop to.
            stop_order_id: Broker order ID for the stop.
            stop_price: Stop trigger price.
            qty: Optional sanity check — if provided, must cover the
                 CURRENTLY-HELD qty (remaining_qty, falling back to
                 qty). Over-coverage is accepted (doc 286 — same
                 contract as _find_matching_protective_stop); only
                 under-coverage is refused. Pass None to skip.

        Returns:
            True if attached, False if validation failed (ticker
            unknown, stop_price invalid, qty under-covers).
        """
        if ticker not in self._positions:
            logger.warning(
                "D273 STOP_ATTACH FAILED %s: ticker not in tracker — "
                "no position to attach stop to (oid=%s, price=$%.2f)",
                ticker, stop_order_id[:8] if stop_order_id else "?", stop_price,
            )
            return False
        if not stop_order_id:
            logger.warning(
                "D273 STOP_ATTACH FAILED %s: empty stop_order_id",
                ticker,
            )
            return False
        if stop_price <= 0:
            logger.warning(
                "D273 STOP_ATTACH FAILED %s: invalid stop_price=$%.4f "
                "(must be > 0)", ticker, stop_price,
            )
            return False
        position = self._positions[ticker]
        # doc 286 (doc-285 gap #11): validate against the CURRENTLY-HELD
        # qty, not the original entry qty. On 7/6 the D165 T1 tranche sell
        # left RIVN at remaining_qty=1035 while position.qty stayed at the
        # stale entry 1552; the D313 watcher then submitted a REAL
        # emergency stop for the broker-true 1035 shares and this check
        # REFUSED the write-back ("caller passed qty=1035 but
        # position.qty=1552") — so stop_order_id stayed empty and recon
        # (D230), D98 and the resubmitter all disagreed about protection.
        # A stop covering MORE than held is over-protection and acceptable
        # (same contract as _find_matching_protective_stop's o_qty >= qty);
        # only UNDER-coverage is refused.
        held_qty = int(getattr(position, "remaining_qty", 0) or 0) or position.qty
        if qty is not None and qty < held_qty:
            logger.warning(
                "D273 STOP_ATTACH FAILED %s: qty under-covers — caller passed "
                "qty=%d but held qty=%d (stop must cover full position)",
                ticker, qty, held_qty,
            )
            return False
        # Mutate the dataclass position — this is the canonical state
        # update for "the tracker now knows about this broker stop"
        prev_stop = position.stop_loss
        prev_oid = position.stop_order_id
        position.stop_order_id = stop_order_id
        position.stop_loss = float(stop_price)
        logger.info(
            "D273 STOP_ATTACHED %s: stop_order_id=%s stop_price=$%.4f "
            "(was: oid=%s stop=$%.4f). D230 RECON_WARN should now stop "
            "firing for this position's stop drift.",
            ticker, stop_order_id[:8], stop_price,
            prev_oid[:8] if prev_oid else "<none>", prev_stop,
        )
        return True

    @staticmethod
    def _find_matching_protective_stop(
        ticker: str, qty: int, broker_orders: list[dict],
    ) -> dict | None:
        """Bug AM helper: find an active broker sell-stop order that
        covers this position's full qty.

        Returns the matching order dict, or None if no match. Used by
        sync_from_broker / sync_from_state_and_orders to auto-discover
        operator-submitted or carried-over stops on startup."""
        for o in broker_orders or []:
            if (o.get("symbol") or "").upper() != ticker.upper():
                continue
            if (o.get("side") or "").lower() != "sell":
                continue
            if (o.get("type") or "").lower() != "stop":
                continue
            if (o.get("status") or "").lower() not in ("new", "accepted", "held"):
                continue
            try:
                o_qty = int(float(o.get("qty", 0) or 0))
            except (TypeError, ValueError):  # noqa: silent-handler — broker qty parse: bad value means skip (no actionable recovery)
                continue
            # Stop must cover at least the position size — operator-
            # submitted stops typically match qty exactly, but a stop
            # for MORE shares is also acceptable (over-protection)
            if o_qty >= qty:
                return o
        return None

    def sync_from_broker(self, broker_positions: list[dict],
                         broker_orders: list[dict] | None = None) -> int:
        """
        D56: Hydrate PositionManager from Alpaca GET /v2/positions on startup.

        Prevents duplicate entries when the process is restarted mid-session.
        Each broker_position dict has Alpaca fields:
          symbol, qty, avg_entry_price, current_price, unrealized_pl, side, ...

        Returns the number of positions synced.
        """
        synced = 0
        for bp in broker_positions:
            symbol = bp.get("symbol", "")
            side = bp.get("side", "long")
            _is_short = (side == "short")   # D202: recover SHORTS too (was: skip)
            qty = abs(int(float(bp.get("qty", 0))))  # shorts report negative qty
            if qty == 0:
                continue
            if symbol in self._positions:
                logger.info(
                    "D56 sync: %s already tracked (qty=%d), skipping",
                    symbol, self._positions[symbol].qty,
                )
                continue

            entry_price = float(bp.get("avg_entry_price", 0))
            current_price = float(bp.get("current_price", entry_price))
            # Estimate stop from config (entry - stop_loss_pct).
            # Wed 2026-04-22 Bug C: this is a COMPUTED DEFAULT, NOT a
            # broker-confirmed stop. The position has NO actual broker
            # stop order yet -- D86 / exit-ladder downstream code is
            # expected to either preserve an existing GTC stop (Bug A)
            # or submit a fresh one. Until then, the displayed stop
            # value is a strategy-defined safety value, not a guarantee.
            stop_loss, targets = self._recovery_stop_targets(entry_price, _is_short)  # D202: direction-aware

            # Bug AM (2026-04-27): auto-discover an existing broker
            # protective stop for this symbol BEFORE creating the
            # tracker entry. Today's LIDR scenario had a manual operator
            # stop @ $2.10 at the broker but the tracker showed
            # COMPUTED DEFAULT $2.29 — D230 fired every 30s. With auto-
            # discovery the tracker reflects reality from the start.
            _attached_oid = ""
            _attached_stop = stop_loss
            _matched = self._find_matching_protective_stop(
                ticker=symbol, qty=qty,
                broker_orders=broker_orders or [],
            )
            if _matched:
                try:
                    _attached_stop = float(_matched.get("stop_price") or stop_loss)
                    _attached_oid = _matched.get("id") or ""
                except (TypeError, ValueError):
                    _attached_oid = ""
                    _attached_stop = stop_loss

            # Bug AP fix (2026-04-28): set opened_at to a sentinel that's
            # GUARANTEED before today's 04:00 ET. ManagedPosition's
            # default factory was `datetime.now(timezone.utc)`, so any
            # position discovered via D56 sync got "now" as its opened_at,
            # which made D91 think it was opened today (not overnight).
            # Today's LIDR (carried from 2026-04-25) was misclassified as
            # `overnight=0` and the Bug AJ Discord alert never fired.
            # Defensive default: positions discovered via broker sync
            # are BY DEFINITION from a prior session — set opened_at to
            # yesterday at 16:00 ET (yesterday's market close) so D91
            # correctly classifies them as overnight. The actual entry
            # time is unknown without `/v2/orders` history; this sentinel
            # is operationally correct (treat-as-overnight is the safe
            # default for unknown-acquisition positions).
            from zoneinfo import ZoneInfo as _bug_ap_zi
            _bug_ap_now_et = datetime.now(timezone.utc).astimezone(_bug_ap_zi("America/New_York"))
            _bug_ap_yesterday_close_et = (
                _bug_ap_now_et.replace(hour=16, minute=0, second=0, microsecond=0)
                - timedelta(days=1)
            )
            _bug_ap_opened_at = _bug_ap_yesterday_close_et.astimezone(timezone.utc)

            position = ManagedPosition(
                ticker=symbol,
                qty=qty,
                entry_price=entry_price,
                signal_price=entry_price,  # Unknown — use entry as proxy
                stop_loss=_attached_stop,
                target_prices=targets,
                fill_price=entry_price,
                remaining_qty=qty,
                # Bug AM: stop_order_id reflects auto-discovery if it
                # found a matching broker stop. Empty string preserves
                # the Wed 2026-04-22 Bug C honesty if no match.
                stop_order_id=_attached_oid,
                # Bug AP: explicit opened_at sentinel so D91 classifies
                # broker-synced positions as overnight (see comment above)
                opened_at=_bug_ap_opened_at,
                direction=("short" if _is_short else "long"),  # D202: short-recovery parity
            )
            self._positions[symbol] = position
            synced += 1
            if _attached_oid:
                logger.info(
                    "D56 sync (Bug AM auto-attach): %s qty=%d @ $%.2f "
                    "stop=$%.4f from broker oid=%s — D230 RECON_WARN "
                    "should NOT fire for this stop drift",
                    symbol, qty, entry_price, _attached_stop,
                    _attached_oid[:8],
                )
            else:
                # Wed 2026-04-22 Bug C: log honestly. The stop value is a
                # COMPUTED DEFAULT from settings, not a broker-confirmed stop.
                logger.info(
                    "D56 sync: %s qty=%d @ $%.2f (stop=$%.2f COMPUTED DEFAULT, "
                    "no broker order) — downstream code must verify or submit",
                    symbol, qty, entry_price, stop_loss,
                )

        logger.info(
            "D56: Synced %d positions from broker (%d total tracked)",
            synced, len(self._positions),
        )
        return synced

    def _recovery_stop_targets(self, entry_price: float, is_short: bool) -> tuple[float, list[float]]:
        """D202: direction-aware default stop + targets for a recovered position.
        Long: stop BELOW (entry*(1-stop_pct)), targets ABOVE. Short: stop ABOVE
        (entry*(1+stop_pct)), targets BELOW — so a recovered short is monitored with the
        correct (inverted) protective geometry."""
        sp = self._config.stop_loss_pct
        if is_short:
            stop = round(entry_price * (1.0 + sp), 4)
            targets = [round(entry_price * 0.95, 4), round(entry_price * 0.90, 4),
                       round(entry_price * 0.80, 4)]
        else:
            stop = round(entry_price * (1.0 - sp), 4)
            targets = [round(entry_price * 1.05, 4), round(entry_price * 1.10, 4),
                       round(entry_price * 1.20, 4)]
        return stop, targets

    # ── D64: Enhanced Recovery from Session State + Alpaca ──────────

    def sync_from_state_and_orders(
        self,
        session_state: Any,
        alpaca_positions: list[dict],
        alpaca_orders: list[dict],
    ) -> int:
        """
        D64: Enhanced recovery merging session state file + Alpaca API data.

        For each Alpaca position:
          - If session_state has metadata → use targets, tranches, signal_price, stop_order_id
          - If not → fall back to D56 estimation (entry ± config %)

        Also restores daily_realized_pnl for circuit breaker continuity.

        Args:
            session_state: SessionState from session_state.json
            alpaca_positions: Result of client.get_positions()
            alpaca_orders: Result of client.get_orders(status="open")

        Returns:
            Number of positions recovered.
        """
        # Build lookup of open orders by ID for quick matching
        order_by_id = {o.get("id", ""): o for o in alpaca_orders}

        synced = 0
        for bp in alpaca_positions:
            symbol = bp.get("symbol", "")
            side = bp.get("side", "long")
            _is_short = (side == "short")   # D202: recover SHORTS too (was: skip)
            qty = abs(int(float(bp.get("qty", 0))))  # Alpaca reports shorts with negative qty
            if qty == 0:
                continue
            if symbol in self._positions:
                logger.info(
                    "D64 sync: %s already tracked, skipping", symbol,
                )
                continue

            entry_price = float(bp.get("avg_entry_price", 0))

            # Try to enrich from session state
            pos_state = (
                session_state.positions.get(symbol)
                if session_state and hasattr(session_state, "positions")
                else None
            )

            if pos_state:
                # Enhanced recovery: use persisted metadata
                signal_price = pos_state.signal_price or entry_price
                _def_stop, _def_targets = self._recovery_stop_targets(entry_price, _is_short)
                stop_loss = pos_state.stop_loss or _def_stop
                targets = pos_state.target_prices or _def_targets
                tranches_filled = pos_state.tranches_filled
                realized_pnl = pos_state.realized_pnl
                stop_order_id = pos_state.stop_order_id
                opened_at_str = pos_state.opened_at

                logger.info(
                    "D64 sync (state-enhanced): %s qty=%d @ $%.2f | "
                    "tranches=%d, stop=$%.2f, stop_oid=%s",
                    symbol, qty, entry_price, tranches_filled,
                    stop_loss, stop_order_id[:12] if stop_order_id else "none",
                )
            else:
                # Fallback: D56 estimation (direction-aware, D202)
                signal_price = entry_price
                stop_loss, targets = self._recovery_stop_targets(entry_price, _is_short)
                tranches_filled = 0
                realized_pnl = 0.0
                stop_order_id = ""
                opened_at_str = ""

                logger.info(
                    "D64 sync (D56 fallback): %s qty=%d @ $%.2f",
                    symbol, qty, entry_price,
                )

            # Parse opened_at if available
            try:
                opened_at = (
                    datetime.fromisoformat(opened_at_str)
                    if opened_at_str
                    else datetime.now(timezone.utc)
                )
            except (ValueError, TypeError):
                opened_at = datetime.now(timezone.utc)

            position = ManagedPosition(
                ticker=symbol,
                qty=pos_state.qty if pos_state and pos_state.qty > 0 else qty,
                entry_price=entry_price,
                signal_price=signal_price,
                stop_loss=stop_loss,
                target_prices=targets,
                fill_price=entry_price,
                tranches_filled=tranches_filled,
                remaining_qty=qty,  # Alpaca's live qty is remaining
                realized_pnl=realized_pnl,
                stop_order_id=stop_order_id,
                opened_at=opened_at,
                # D150: Restore tier/catalyst fields from session state
                position_tier=getattr(pos_state, "position_tier", 3) if pos_state else 3,
                kelly_tier=getattr(pos_state, "kelly_tier", 1) if pos_state else 1,
                gap_pct=getattr(pos_state, "gap_pct", 0.0) if pos_state else 0.0,
                catalyst_type=getattr(pos_state, "catalyst_type", "unknown") if pos_state else "unknown",
                sector=getattr(pos_state, "sector", "") if pos_state else "",
                manipulation_phase=getattr(pos_state, "manipulation_phase", "UNCERTAIN") if pos_state else "UNCERTAIN",
                direction=("short" if _is_short else "long"),  # D202: short-recovery parity
            )
            self._positions[symbol] = position
            synced += 1
            if _is_short:
                logger.warning(
                    "D202 SHORT RECOVERED: %s qty=%d @ $%.2f stop=$%.2f (above entry) — "
                    "monitored as a short across the restart", symbol, qty, entry_price, stop_loss,
                )

        # Restore daily realized P&L for circuit breaker
        if session_state and hasattr(session_state, "daily_realized_pnl"):
            self._daily_realized_pnl = session_state.daily_realized_pnl
            logger.info(
                "D64: Restored daily P&L: $%.2f",
                self._daily_realized_pnl,
            )

        logger.info(
            "D64: Synced %d positions (%d total tracked), daily_pnl=$%.2f",
            synced, len(self._positions), self._daily_realized_pnl,
        )

        # D108: Validate recovered positions for internal consistency
        self._validate_recovered_positions()

        return synced

    def _validate_recovered_positions(self) -> None:
        """D108: Post-merge sanity checks on recovered positions.

        Catches silent corruption from buggy state writes. Clamps impossible
        values and logs warnings — never crashes.
        """
        for pos in self._positions.values():
            # remaining_qty must be non-negative
            if pos.remaining_qty < 0:
                logger.warning(
                    "D108 VALIDATION: %s remaining_qty=%d < 0 — clamping to 0",
                    pos.ticker, pos.remaining_qty,
                )
                pos.remaining_qty = 0
                get_metrics().state_validation_clamps.inc()

            # remaining_qty must not exceed original qty
            if pos.remaining_qty > pos.qty:
                logger.warning(
                    "D108 VALIDATION: %s remaining_qty=%d > qty=%d — clamping",
                    pos.ticker, pos.remaining_qty, pos.qty,
                )
                pos.remaining_qty = pos.qty
                get_metrics().state_validation_clamps.inc()

            # tranches_filled must be 0-3
            if pos.tranches_filled < 0 or pos.tranches_filled > 3:
                logger.warning(
                    "D108 VALIDATION: %s tranches_filled=%d out of range — clamping to [0,3]",
                    pos.ticker, pos.tranches_filled,
                )
                pos.tranches_filled = max(0, min(3, pos.tranches_filled))
                get_metrics().state_validation_clamps.inc()

            # stop_loss should be below entry_price (for longs)
            if pos.stop_loss > 0 and pos.entry_price > 0 and pos.stop_loss >= pos.entry_price:
                logger.warning(
                    "D108 VALIDATION: %s stop_loss=$%.2f >= entry_price=$%.2f "
                    "(may be trailing stop — not auto-correcting)",
                    pos.ticker, pos.stop_loss, pos.entry_price,
                )

            # target_prices should have exactly 3 entries
            if len(pos.target_prices) != 3 and len(pos.target_prices) > 0:
                logger.warning(
                    "D108 VALIDATION: %s has %d target_prices (expected 3)",
                    pos.ticker, len(pos.target_prices),
                )

    # ── D107 WS2: Orphaned Order Reconciliation ──────────────────

    def identify_orphaned_orders(
        self,
        alpaca_orders: list[dict],
    ) -> list[dict]:
        """
        D107: Identify orders for symbols not tracked by position manager.

        After a crash, pending orders may remain on the broker side for
        symbols that are no longer in our tracked positions. These "orphaned"
        orders risk untracked fills — the highest-risk failure mode.

        Args:
            alpaca_orders: Result of client.get_orders(status="open").
                           Each dict must have "symbol" and "status" keys.

        Returns:
            List of orphaned order dicts (caller handles cancellation).
        """
        tracked_symbols = set(self._positions.keys())
        actionable_statuses = {"new", "accepted", "pending_new", "partially_filled"}
        orphans: list[dict] = []

        for order in alpaca_orders:
            symbol = order.get("symbol", "")
            status = order.get("status", "").lower()
            if not symbol or status not in actionable_statuses:
                continue
            if symbol not in tracked_symbols:
                order_id = order.get("id", "unknown")
                side = order.get("side", "unknown")
                qty = order.get("qty", "?")
                logger.warning(
                    "D107 ORPHAN: Order %s for %s (%s %s) — not in tracked positions",
                    order_id, symbol, side, qty,
                )
                orphans.append(order)

        if orphans:
            logger.info("D107: Found %d orphaned orders across %d symbols",
                        len(orphans), len({o.get("symbol") for o in orphans}))
        else:
            logger.info("D107: No orphaned orders found")
        return orphans

    def add_position(self, position: ManagedPosition) -> ManagedPosition:
        """Track a new managed position — or MERGE into an existing same-ticker, same-direction
        one (D227 fix). RETURNS the canonical tracked object (the merged `existing` on a
        re-buy, else `position`) so callers reconcile/persist/ladder against the REAL tracker,
        not the discarded single-fill object (the doc-228 ripple fix).

        D227 BUG (the 6/2 LASE +143% ghost): a multi-buy on a runner (LASE bought 3x) called
        add_position 3x, and the old `self._positions[ticker] = position` REPLACED the tracker
        each time — DISCARDING the prior shares the broker still held. Tracker ended 10,680 sh
        SHORT of broker truth -> D218/D231 qty-drift all day (1,985 alerts) -> the EOD close
        403'd (stops reserved the "lost" shares) -> accidental overnight carry. The reported
        +143% "win" was a bug. Fix: when the ticker already exists and is the same direction,
        MERGE — sum qty (weighted-avg entry), keep the earliest opened_at + the protective
        stop, accumulate tranche/peak state — so the tracker matches the broker's aggregate.
        (A different-direction re-add is a flip; keep the replace there — it shouldn't happen,
        but replacing is safer than a nonsensical merge, and D161 shorts never re-add long.)
        """
        existing = self._positions.get(position.ticker)
        # doc 228 (#5/#6 from the sweep): merge only when the existing position still HOLDS
        # shares (remaining_qty>0), and weight the avg entry by the CURRENTLY-HELD qty
        # (remaining_qty), not the original qty — a position that already sold a tranche holds
        # fewer shares. A fully-exited-but-not-yet-removed position (remaining_qty==0) is
        # treated as REPLACE, not resurrected.
        held = int(getattr(existing, "remaining_qty", 0) or 0) if existing is not None else 0
        if (existing is not None
                and getattr(existing, "direction", "long") == getattr(position, "direction", "long")
                and held > 0 and position.qty > 0):
            add_qty = position.qty
            new_held = held + add_qty
            old_entry = existing.entry_price
            # weighted-average entry across CURRENTLY-HELD old shares + the new fill
            existing.entry_price = round(
                (existing.entry_price * held + position.entry_price * add_qty) / new_held, 6)
            existing.qty = new_held
            existing.remaining_qty = new_held
            # doc 229 (#4): the merge moved entry_price (weighted avg). target_prices and stop_loss
            # were buy#1's ABSOLUTE levels — for a runner re-bought HIGHER they can now sit at/below
            # the new blended entry, so the freshly-armed exit ladder (post_fill_handler ->
            # compute_exit_tranches reads pos_obj.target_prices) would place limit sells BELOW cost
            # basis. Rescale targets + stop by the entry ratio to preserve the %-offset risk/reward
            # geometry against the new cost basis (ratio>1 on a runner -> TIGHTER, never looser for a
            # long), and reset tranches_filled — the merged position is re-laddered fresh on this fill.
            if old_entry and old_entry > 0:
                _ratio = existing.entry_price / old_entry
                if existing.target_prices:
                    existing.target_prices = [round(t * _ratio, 6) for t in existing.target_prices]
                if existing.stop_loss:
                    existing.stop_loss = round(existing.stop_loss * _ratio, 6)
            existing.tranches_filled = 0
            # keep the EARLIEST opened_at (the position's true age, for D91 overnight logic)
            if getattr(position, "opened_at", None) and position.opened_at < existing.opened_at:
                pass  # keep existing (earlier)
            existing.peak_price = max(existing.peak_price or 0.0, position.peak_price or 0.0,
                                      existing.entry_price)
            # adopt the newer order's stop only if the existing one is unset
            if not existing.stop_order_id and position.stop_order_id:
                existing.stop_order_id = position.stop_order_id
            logger.warning(
                "D227 POSITION_MERGE %s: re-buy %d sh @ $%.4f added to held %d sh -> "
                "%d sh @ avg $%.4f (was REPLACE, which caused the 6/2 LASE qty-drift ghost)",
                position.ticker, add_qty, position.entry_price, held, new_held,
                existing.entry_price,
            )
            tracked = existing
        else:
            self._positions[position.ticker] = position
            tracked = position
        # D150: Sync metrics gauge on add (remove already syncs)
        try:
            get_metrics().open_positions.set(len(self._positions))
        except Exception:
            pass
        return tracked  # doc 228: callers reconcile/persist/ladder against THIS

    def remove_position(self, ticker: str) -> ManagedPosition | None:
        """Remove a fully closed position.

        BUG-003 fix: Always syncs the open_positions gauge so the dashboard
        stays accurate regardless of which code path triggers removal
        (D91 overnight, D76 EOD, D98 stop-out, D85 fast-path, etc.).

        D216: Track recently closed tickers to prevent race-condition
        re-entry (D94b appendix).
        """
        removed = self._positions.pop(ticker, None)
        if removed is not None:
            # D216: Track for race condition prevention (initialized in __init__)
            # Only blocks re-entry for STOP-related closes, not target hits
            self._recently_closed.add(ticker)
            try:
                get_metrics().open_positions.set(len(self._positions))
            except Exception:
                pass  # metrics not initialized yet during tests
        return removed

    @property
    def open_positions(self) -> list[ManagedPosition]:
        """List of all currently managed positions."""
        return list(self._positions.values())

    # ── Scaled Exits ─────────────────────────────────────────────────

    def compute_exit_tranches(
        self, position: ManagedPosition
    ) -> list[ExitTranche]:
        """
        Generate 3-tranche scaled exit plan.

        Tranche 1 (1/3): Limit at target_prices[0]
        Tranche 2 (1/3): Limit at target_prices[1]
        Tranche 3 (1/3): Limit at target_prices[2] or trailing stop

        Ref: ADR-003 §2 (Scaled Exits)
        """
        # D121 BUG: Use remaining_qty to avoid re-selling filled tranches after recovery
        total_qty = position.remaining_qty
        targets = position.target_prices
        tranches = []

        # D121 BUG-R3: Guard against 0-share tranche orders.
        # Can happen if position fully closed by tranches but not yet removed.
        if total_qty <= 0:
            return tranches

        # Sweep fix: for qty < 3, use fewer tranches to avoid zero-qty entries.
        # Submitting 0-share sell orders to the broker fails or is a no-op.
        if total_qty < 3:
            # Single tranche: sell all at first target, trailing stop
            target = targets[0] if targets else (
                position.entry_price * 1.10
            )
            tranches.append(ExitTranche(
                tranche_number=1,
                qty=total_qty,
                target=target,
                exit_type="trailing_stop",
            ))
        else:
            tranche_size = total_qty // 3
            remainder = total_qty - (tranche_size * 3)

            for i in range(3):
                # Sweep fix: add full remainder to last tranche, not just 1 share
                qty = tranche_size + (remainder if i == 2 else 0)
                target = targets[i] if i < len(targets) else (
                    position.entry_price * (1.0 + 0.10 * (i + 1))  # Default +10%, +20%, +30%
                )
                exit_type = "trailing_stop" if i == 2 else "limit"

                tranches.append(ExitTranche(
                    tranche_number=i + 1,
                    qty=qty,
                    target=target,
                    exit_type=exit_type,
                ))

        return tranches

    # ── Stop Ratcheting ──────────────────────────────────────────────

    def compute_stop_after_tranche(
        self,
        position: ManagedPosition,
        tranche_filled: int,
    ) -> float:
        """
        Compute new stop-loss level after a tranche fills.

        After T1: Move stop to breakeven (entry price).
        After T2: Move stop to T1 target price.
        After T3: Position fully closed, no stop needed.

        INVARIANT: Stop only moves UP, never down.

        Ref: ADR-003 §2 (Stop Management)
        """
        if tranche_filled <= 0:
            return position.stop_loss

        if tranche_filled == 1:
            # Move to breakeven
            new_stop = position.entry_price
        elif tranche_filled >= 2:
            # Move to T1 target
            new_stop = position.target_prices[0] if position.target_prices else position.entry_price
        else:
            new_stop = position.stop_loss

        # INVARIANT: stop only ratchets UP
        return max(new_stop, position.stop_loss)

    # ── Trailing Stop (D43) ─────────────────────────────────────

    def compute_trailing_stop(
        self,
        current_price: float,
        entry_price: float,
        current_stop: float,
        atr: float | None = None,
        peak_price: float | None = None,
    ) -> float:
        """
        Compute trailing stop using Chandelier Exit when possible.

        D89: Chandelier Exit = highest_high - multiplier × ATR.
        Uses 4.0× ATR multiplier for small-cap momentum stocks
        (3.0× triggers premature stops on volatile names).

        Falls back to 2× ATR from current price when peak unavailable,
        or 3% trail when no ATR data.

        INVARIANT: Trailing stop only ratchets UP, never down.

        D43: Activated when unrealized P&L > +2% from entry.
        """
        if atr and atr > 0 and peak_price and peak_price > 0:
            # D89: Chandelier Exit — trail from highest high
            trail_stop = peak_price - (4.0 * atr)
        elif atr and atr > 0:
            # ATR-based trailing: 2× ATR below current price (legacy)
            trail_stop = current_price - (2.0 * atr)
        else:
            # Percentage-based fallback: 3% below current price
            trail_stop = current_price * 0.97

        # D89: Safe-zone stop placement — avoid round-number clusters
        trail_stop = self.adjust_stop_for_safe_zone(trail_stop, atr)

        # INVARIANT: stop only ratchets UP
        return max(trail_stop, current_stop)

    @staticmethod
    def adjust_stop_for_safe_zone(raw_stop: float, atr: float | None = None) -> float:
        """
        D89: Nudge stop away from round numbers where stop hunters cluster.

        Market makers and algorithms run stops at round numbers ($5, $10,
        $50, etc.) and psychological levels ($X.50). This pushes the stop
        slightly below the nearest round number to avoid being hunted.

        Buffer = 0.15× ATR (or 0.3% of price if no ATR).
        """
        if raw_stop <= 0:
            return raw_stop

        buffer = (atr * 0.15) if atr and atr > 0 else raw_stop * 0.003

        # Check proximity to round numbers (largest to smallest)
        round_levels = [100.0, 50.0, 25.0, 10.0, 5.0, 1.0, 0.50]
        for level in round_levels:
            if raw_stop < level:
                continue  # Price below this round level entirely
            nearest = round(raw_stop / level) * level
            if abs(raw_stop - nearest) < buffer:
                # Nudge below the round number
                raw_stop = nearest - buffer
                break

        return round(raw_stop, 4)

    def should_activate_trailing_stop(
        self,
        position: ManagedPosition,
        current_price: float,
    ) -> bool:
        """
        Activate trailing stop when unrealized P&L exceeds threshold.
        D43: Switch from fixed ratcheting to trailing stop mode.

        Threshold scales with Kelly tier — higher conviction trades get wider
        activation to let momentum develop toward tranche targets before trailing.
        Replay analysis (Mar 19-20): 2% trailing exited ANNA at +2.3% of a +50% move.
        """
        if position.entry_price <= 0:
            return False

        # Base threshold from config (default 4%)
        base_pct = getattr(self._config, "trailing_stop_activation_pct", 0.04)

        # Scale wider for higher Kelly tiers — let conviction trades breathe
        kelly_tier = getattr(position, "kelly_tier", 1)
        if kelly_tier >= 3:
            activation_pct = base_pct * 1.5  # 6% for Tier 3+
        elif kelly_tier >= 2:
            activation_pct = base_pct * 1.25  # 5% for Tier 2
        else:
            activation_pct = base_pct

        unrealized_pct = (current_price - position.entry_price) / position.entry_price
        return unrealized_pct >= activation_pct

    # ── Adaptive Targets (D78) ────────────────────────────────────

    @staticmethod
    def compute_adaptive_targets(
        entry_price: float,
        atr: float | None = None,
        rvol: float = 1.0,
        hour_et: int = 10,
    ) -> list[float]:
        """
        Compute ATR-based, time-decaying exit targets.

        D78: Replaces static +3/6/10% with adaptive targets.
        - ATR-based: T1=1.5*ATR, T2=3*ATR, T3=5*ATR from entry
        - Time decay: tighten 30% after 13:00, 50% after 14:00
        - Volume boost: widen 20% if RVOL > 5
        - Fallback: +3/6/10% if no ATR
        """
        if atr and atr > 0 and entry_price > 0:
            t1 = entry_price + 1.5 * atr
            t2 = entry_price + 3.0 * atr
            t3 = entry_price + 6.0 * atr  # D94: widened from 5x to 6x ATR
        else:
            # D94: Widened fallback from 3/6/10% to 5/10/20%
            t1 = entry_price * 1.05
            t2 = entry_price * 1.10
            t3 = entry_price * 1.20

        # Volume boost: strong momentum = wider targets
        if rvol > 5.0:
            vol_mult = 1.20
        elif rvol > 3.0:
            vol_mult = 1.10
        else:
            vol_mult = 1.0

        t1 = entry_price + (t1 - entry_price) * vol_mult
        t2 = entry_price + (t2 - entry_price) * vol_mult
        t3 = entry_price + (t3 - entry_price) * vol_mult

        # Time decay: tighten targets as day progresses
        if hour_et >= 14:
            time_mult = 0.50  # Aggressive tightening after 2 PM
        elif hour_et >= 13:
            time_mult = 0.70  # Moderate tightening after 1 PM
        elif hour_et >= 12:
            time_mult = 0.85  # Slight tightening after noon
        else:
            time_mult = 1.0

        t1 = entry_price + (t1 - entry_price) * time_mult
        t2 = entry_price + (t2 - entry_price) * time_mult
        t3 = entry_price + (t3 - entry_price) * time_mult

        return [round(t1, 4), round(t2, 4), round(t3, 4)]

    # ── Daily Reset ──────────────────────────────────────────────────

    def reset_daily(self) -> None:
        """Reset daily P&L tracking at market open."""
        self._daily_realized_pnl = 0.0
        logger.info("Daily P&L reset to $0.00")

    # ── ScoredCandidate Cache (ADR-014: Pipeline Closure) ────────

    def cache_scored_candidate(self, ticker: str, scored: Any) -> None:
        """
        Cache a ScoredCandidate at entry time for Shapley attribution at close.

        Called by the orchestrator after MFCS scoring, before position entry.
        The cached data (component_scores, mfcs, debate_triggered) is used
        to construct EnrichedTradeResult when the position is closed.

        Args:
            ticker: Stock symbol.
            scored: ScoredCandidate from MFCS computation.

        Ref: ADR-014 (Pipeline Closure, D1)
        """
        self._scored_cache[ticker] = scored
        logger.debug("Cached ScoredCandidate for %s (MFCS=%.3f)", ticker, scored.mfcs)

    def get_cached_scored(self, ticker: str) -> Any | None:
        """Retrieve cached ScoredCandidate for a ticker, or None."""
        return self._scored_cache.get(ticker)

    def close_position_with_attribution(
        self,
        ticker: str,
        exit_price: float,
        exit_time: datetime,
        agent_signals_map: dict[str, str],
        variant_map: dict[str, str],
    ) -> Any | None:
        """
        Close a position and build EnrichedTradeResult for Shapley attribution.

        Requires a previously cached ScoredCandidate from entry time.
        If no cache exists, returns None (graceful degradation — old trades
        opened before the caching system was deployed won't have Shapley data).

        The returned EnrichedTradeResult can be passed directly to:
            PostTradeAnalyzer.analyze_with_shapley(enriched, attributor)

        Args:
            ticker: Stock symbol to close.
            exit_price: Fill price at exit.
            exit_time: When position was closed.
            agent_signals_map: agent_id → signal direction at entry.
            variant_map: agent_id → variant_id from arena selection.

        Returns:
            EnrichedTradeResult if cache exists, None otherwise.

        Ref: ADR-014 (Pipeline Closure)
        Ref: MOMENTUM_LOGIC.md §17 (Shapley Attribution)
        """
        scored = self._scored_cache.pop(ticker, None)

        # Sweep fix: ALWAYS remove position and record PnL, even without cached
        # ScoredCandidate. Previously, missing cache caused the position to leak
        # (never removed) and PnL was never recorded for the circuit breaker.
        position = self.remove_position(ticker)
        if position:
            # D121 BUG-C1: Use remaining_qty, not qty. Tranche fills already
            # recorded PnL for sold tranches via tranche_monitor.record_realized_pnl().
            # Using qty here double-counts those tranches.
            # D161: Invert for short positions — profit is entry - exit, not exit - entry.
            if getattr(position, "direction", "long") == "short":
                pnl = (position.entry_price - exit_price) * position.remaining_qty
            else:
                pnl = (exit_price - position.entry_price) * position.remaining_qty
            self.record_realized_pnl(pnl)

        if scored is None:
            logger.warning(
                "No cached ScoredCandidate for %s — Shapley attribution unavailable "
                "(position removed, PnL=$%.2f recorded)",
                ticker, pnl if position else 0.0,
            )
            return None

        # Build EnrichedTradeResult
        from src.analysis.shapley import EnrichedTradeResult

        enriched = EnrichedTradeResult(
            ticker=ticker,
            # Sweep fix: use actual fill price from position, not scanner price.
            # Scanner price (candidate.current_price) can differ significantly
            # from actual fill, especially on gap-up stocks.
            entry_price=position.entry_price if position else scored.candidate.current_price,
            exit_price=exit_price,
            entry_time=scored.candidate.scan_timestamp,
            exit_time=exit_time,
            agent_variants=variant_map,
            agent_signals=agent_signals_map,
            agent_component_scores=dict(scored.component_scores),
            mfcs_at_entry=scored.mfcs,
            risk_score=scored.risk_score,
            debate_triggered=scored.qualifies_for_debate,
            direction=getattr(position, "direction", "long") if position else "long",  # D161
        )

        logger.info(
            "Closed %s with Shapley attribution: PnL=%.2f, MFCS=%.3f",
            ticker, enriched.pnl, enriched.mfcs_at_entry,
        )

        return enriched
