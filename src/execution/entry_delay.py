"""
D170: Entry Delay with Observation Window

### MOTIVATION
Analysis of all live trades (Mar–Apr 2026) shows a stark time-of-day effect:

  9:28–9:35 bucket: 10 trades, 0% win rate, avg P&L -$1,263 (total -$12,634)
  10:00–10:15 bucket: 3 trades, 33% win rate, avg P&L -$64

The open-bucket losses share a pattern: gap-and-fade stocks where the
pre-market spike was driven by retail FOMO, not sustained institutional demand.
These stocks peaked at or before the open and faded immediately.

Key finding from journal data:
  ARTL at open: price=$7.68 vs VWAP=$8.22 (BELOW by 6.5%) → lost $1,399
  BFRG at open: price=$1.18 vs VWAP=$1.24 (BELOW by 4.8%) → lost $1,421

Both were already below VWAP at 9:30. The observation window would have
rejected them immediately based on the require_above_vwap criterion.

### HOW IT WORKS
When Phase 2 identifies a BUY candidate, instead of submitting immediately:
  1. Register the candidate with EntryDelayManager (WATCHING state)
  2. Poll price/VWAP every update cycle during the observation window
  3. Check all criteria: VWAP position, higher lows, drawdown, volume sustain
  4. When ALL criteria pass → APPROVED (caller submits the order)
  5. If any criterion fails → REJECTED (with reason logged)
  6. If window expires without approval → EXPIRED

### EARLY ENTRY OVERRIDE
Very strong signals (MFCS > 0.60 AND 4+ bullish agents) can enter after
min_observation_minutes (default 5 min) rather than the full window.
This avoids missing genuine breakout continuation moves.

### INTEGRATION
See main.py Phase 2 loop — candidates are registered after MFCS scoring
and faller gate, then checked on every monitoring cycle. Orders are
submitted with the CURRENT price (not the stale 9:30 snapshot price).

Ref: D160 (Faller Risk Gate — runs before observation registration)
Ref: D169 (News agent catalyst detection)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("momentum_x.entry_delay")


@dataclass
class ObservationConfig:
    """Configuration for the observation window system."""

    enabled: bool = True

    # How long to observe before allowing entry
    observation_minutes: float = 15.0
    min_observation_minutes: float = 5.0   # Minimum even for strong signals
    max_observation_minutes: float = 30.0  # Don't wait longer than this

    # Criteria — stock must pass ALL enabled checks
    require_above_vwap: bool = True
    require_higher_lows: bool = True
    min_volume_sustain_pct: float = 0.50    # Observation volume >= 50% of opening bar
    max_drawdown_from_open_pct: float = 0.10  # Max allowed drop from open price
    require_no_new_low: bool = True          # No new low in last N readings

    # Early entry override thresholds
    early_entry_min_mfcs: float = 0.60
    early_entry_min_agents_bullish: int = 4

    # How many recent price readings to check for "new low" criterion
    no_new_low_lookback: int = 3


class ObservationState(Enum):
    WATCHING = "watching"   # In observation period, still accumulating data
    APPROVED = "approved"   # Passed all criteria, ready for entry
    REJECTED = "rejected"   # Failed criteria during observation
    EXPIRED = "expired"     # Observation window closed without approval
    ENTERED = "entered"     # Position taken (terminal state, for logging)


@dataclass
class CandidateObservation:
    """Tracks a single candidate stock during its observation window."""

    ticker: str
    first_seen: datetime
    open_price: float       # Price when first identified at Phase 2
    mfcs: float             # Multi-Factor Conviction Score from orchestrator
    agent_signals: dict     # {agent_id: signal} from orchestrator scored result

    # Price/volume readings accumulated during observation
    prices: list[tuple[datetime, float]] = field(default_factory=list)
    vwap_readings: list[tuple[datetime, float]] = field(default_factory=list)
    volume_readings: list[tuple[datetime, float]] = field(default_factory=list)

    # State machine
    state: ObservationState = ObservationState.WATCHING
    approval_time: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    # Running stats (updated on each price tick)
    min_price: float = field(init=False)
    max_price: float = 0.0
    latest_price: float = 0.0
    latest_vwap: float = 0.0
    opening_bar_volume: float = 0.0  # Volume of first reading (baseline)

    # D219: Opening Range Breakout tracking
    # Tracks the 5-minute opening range (high/low of first ~5 readings)
    # ORB break above the range high with volume is a fast-track entry signal
    orb_high: float = 0.0
    orb_low: float = float("inf")
    orb_locked: bool = False  # True after 5 minutes of data
    orb_avg_volume: float = 0.0  # Average volume during ORB formation

    def __post_init__(self) -> None:
        self.min_price = self.open_price
        self.max_price = self.open_price
        self.latest_price = self.open_price
        self.orb_high = self.open_price
        self.orb_low = self.open_price

    def record_price(self, timestamp: datetime, price: float, vwap: float, volume: float) -> None:
        """Append a new price reading and update running stats."""
        self.prices.append((timestamp, price))
        self.vwap_readings.append((timestamp, vwap))
        self.volume_readings.append((timestamp, volume))
        self.latest_price = price
        self.latest_vwap = vwap
        if price < self.min_price:
            self.min_price = price
        if price > self.max_price:
            self.max_price = price
        # Capture first reading as baseline for volume sustain check
        if self.opening_bar_volume == 0.0 and volume > 0:
            self.opening_bar_volume = volume

        # D219: Build the 5-minute Opening Range (first 5 readings ≈ 5 minutes)
        if not self.orb_locked:
            if price > self.orb_high:
                self.orb_high = price
            if price < self.orb_low:
                self.orb_low = price
            if len(self.prices) >= 5:
                self.orb_locked = True
                # Compute average volume during ORB formation
                orb_volumes = [v for _, v in zip(self.prices[:5], [vol for _, vol in zip(self.prices[:5], self.volume_readings[:5])])]
                self.orb_avg_volume = sum(v for _, v in self.volume_readings[:5]) / 5 if len(self.volume_readings) >= 5 else volume

    @property
    def elapsed_minutes(self) -> float:
        """Minutes since candidate was first registered."""
        now = datetime.now(timezone.utc)
        return (now - self.first_seen).total_seconds() / 60.0

    @property
    def bullish_agent_count(self) -> int:
        """Number of agents with a BULL or STRONG_BUY signal."""
        count = 0
        for sig in self.agent_signals.values():
            if isinstance(sig, str) and sig.upper() in ("BULL", "STRONG_BUY", "BUY"):
                count += 1
        return count


class EntryDelayManager:
    """
    Manages observation windows for Phase 2 BUY candidates.

    Usage:
        # On Phase 2 BUY verdict:
        manager.register_candidate(ticker, open_price, mfcs, agent_signals, now)

        # On every monitoring cycle:
        for ticker, state in manager.get_state_updates(prices_dict, now):
            if state == ObservationState.APPROVED:
                submit_order(ticker)

        # After entry:
        manager.remove(ticker)
    """

    def __init__(self, config: ObservationConfig) -> None:
        self._config = config
        self._candidates: dict[str, CandidateObservation] = {}

    @property
    def config(self) -> ObservationConfig:
        return self._config

    @property
    def watching_tickers(self) -> list[str]:
        return [t for t, c in self._candidates.items() if c.state == ObservationState.WATCHING]

    def register_candidate(
        self,
        ticker: str,
        open_price: float,
        mfcs: float,
        agent_signals: dict,
        timestamp: datetime,
    ) -> None:
        """
        Called when Phase 2 produces a BUY verdict for a candidate.

        If config.enabled is False, immediately approves the candidate
        so the caller can submit the order without any delay.
        """
        if ticker in self._candidates:
            logger.debug("D170: %s already registered (state=%s), skipping re-register",
                         ticker, self._candidates[ticker].state.value)
            return

        obs = CandidateObservation(
            ticker=ticker,
            first_seen=timestamp,
            open_price=open_price,
            mfcs=mfcs,
            agent_signals=agent_signals,
        )

        if not self._config.enabled:
            obs.state = ObservationState.APPROVED
            obs.approval_time = timestamp
            logger.info("D170: %s immediately APPROVED (observation disabled)", ticker)
        else:
            logger.info(
                "D170: %s registered for observation | open=$%.2f MFCS=%.3f bullish_agents=%d | "
                "window=%.0f–%.0fmin",
                ticker, open_price, mfcs, obs.bullish_agent_count,
                self._config.min_observation_minutes,
                self._config.observation_minutes,
            )

        self._candidates[ticker] = obs

    def update_price(
        self,
        ticker: str,
        price: float,
        vwap: float,
        volume: float,
        timestamp: datetime,
    ) -> ObservationState:
        """
        Feed a new price tick for a candidate under observation.

        Returns the current ObservationState after evaluation. Callers
        should check for APPROVED to trigger order submission, and
        REJECTED / EXPIRED to clean up.
        """
        obs = self._candidates.get(ticker)
        if obs is None:
            logger.warning("D170: update_price called for unknown ticker %s", ticker)
            return ObservationState.EXPIRED

        if obs.state != ObservationState.WATCHING:
            return obs.state

        obs.record_price(timestamp, price, vwap, volume)

        elapsed = obs.elapsed_minutes

        # ── Expiry check ────────────────────────────────────────────────
        if elapsed >= self._config.max_observation_minutes:
            obs.state = ObservationState.EXPIRED
            logger.warning(
                "D170: %s EXPIRED after %.1f min without meeting all criteria",
                ticker, elapsed,
            )
            return obs.state

        # Need at least 2 readings for meaningful higher-lows check
        if len(obs.prices) < 2:
            return obs.state

        # ── Criteria evaluation ─────────────────────────────────────────
        passed, reason = self._check_criteria(obs, timestamp)
        if not passed:
            obs.state = ObservationState.REJECTED
            obs.rejection_reason = reason
            logger.info(
                "D170: %s REJECTED at +%.1f min | reason=%s | price=$%.2f vwap=$%.2f",
                ticker, elapsed, reason, price, vwap,
            )
            # doc 182: grade this rejection post-close — did the deferred name fade
            # (D170 correct) or recover/run (D170 too strict)? Write-only shadow;
            # never raises into the entry-delay hot path.
            try:
                from src.shadow.rejection_outcome_shadow import (
                    log_rejection_for_grading as _d182_grade,
                )
                _d182_grade(
                    ticker=ticker, gate="D170_ENTRY_DELAY",
                    decision_price=price, reason=reason,
                )
            except Exception:
                pass
            return obs.state

        # ── Minimum time gate ──────────────────────────────────────────
        if elapsed < self._config.min_observation_minutes:
            return obs.state  # Still watching; criteria passed but too early

        # ── Early entry override ────────────────────────────────────────
        if elapsed < self._config.observation_minutes:
            # D219: ORB fast-track — if price breaks above 5-min opening range
            # high with volume confirmation, approve immediately (after min time)
            if self._check_orb_breakout(obs):
                obs.state = ObservationState.APPROVED
                obs.approval_time = timestamp
                logger.info(
                    "D219: %s ORB BREAKOUT APPROVED at +%.1f min | "
                    "price=$%.2f > ORB high=$%.2f | volume=%.0f > ORB avg=%.0f | "
                    "vwap=$%.2f",
                    ticker, elapsed, price, obs.orb_high,
                    volume, obs.orb_avg_volume, vwap,
                )
                return obs.state

            # Original D170 early entry (MFCS + agent consensus)
            if not self._check_early_entry(obs, timestamp):
                return obs.state  # Not yet eligible for early entry

        # ── Approved ───────────────────────────────────────────────────
        obs.state = ObservationState.APPROVED
        obs.approval_time = timestamp
        logger.info(
            "D170: %s APPROVED at +%.1f min | price=$%.2f vwap=$%.2f "
            "min=$%.2f max=$%.2f drawdown=%.1f%%",
            ticker, elapsed, price, vwap,
            obs.min_price, obs.max_price,
            (obs.open_price - obs.min_price) / obs.open_price * 100,
        )
        return obs.state

    def _check_criteria(self, obs: CandidateObservation, now: datetime) -> tuple[bool, str]:
        """
        Evaluate all observation criteria. Returns (True, "") if all pass,
        or (False, rejection_reason) on first failure.

        Order: cheapest checks first for short-circuit efficiency.
        """
        if self._config.max_drawdown_from_open_pct > 0:
            if not self._check_max_drawdown(obs):
                drawdown = (obs.open_price - obs.min_price) / obs.open_price * 100
                return False, f"drawdown_{drawdown:.1f}pct_exceeds_{self._config.max_drawdown_from_open_pct*100:.0f}pct_limit"

        if self._config.require_above_vwap:
            if not self._check_above_vwap(obs):
                return False, f"below_vwap_price={obs.latest_price:.2f}_vwap={obs.latest_vwap:.2f}"

        if self._config.require_higher_lows:
            if not self._check_higher_lows(obs):
                return False, "lower_low_detected"

        if self._config.require_no_new_low:
            if not self._check_no_new_low_recent(obs):
                return False, f"new_low_in_last_{self._config.no_new_low_lookback}_readings"

        if self._config.min_volume_sustain_pct > 0:
            if not self._check_volume_sustain(obs):
                return False, f"volume_dry_up_below_{self._config.min_volume_sustain_pct*100:.0f}pct"

        return True, ""

    def _check_orb_breakout(self, obs: CandidateObservation) -> bool:
        """
        D219: Opening Range Breakout fast-track confirmation.

        After the 5-minute opening range is established (orb_locked=True),
        check if:
        1. Current price > ORB high (breakout above the range)
        2. Current volume > ORB average volume (volume confirmation)
        3. Minimum observation time has elapsed (min_observation_minutes)

        This is based on Zarattini et al. (2024, Swiss Finance Institute)
        which achieved Sharpe 2.81 using ORB on "stocks in play."
        The ORB acts as institutional flow confirmation — if the stock
        absorbs the opening selloff and breaks higher with volume,
        demand vastly exceeds supply.
        """
        if not obs.orb_locked:
            return False  # ORB not yet established

        if obs.elapsed_minutes < self._config.min_observation_minutes:
            return False  # Too early even for fast-track

        # Price must break ABOVE the 5-minute high
        if obs.latest_price <= obs.orb_high:
            return False

        # Volume on breakout must exceed average ORB volume
        if obs.orb_avg_volume > 0:
            latest_vol = obs.volume_readings[-1][1] if obs.volume_readings else 0
            if latest_vol < obs.orb_avg_volume:
                return False

        return True

    def _check_early_entry(self, obs: CandidateObservation, now: datetime) -> bool:
        """
        Strong-signal fast-path: skip the remainder of the observation window
        if MFCS is high AND multiple agents are independently bullish.
        """
        mfcs_ok = obs.mfcs >= self._config.early_entry_min_mfcs
        agents_ok = obs.bullish_agent_count >= self._config.early_entry_min_agents_bullish
        if mfcs_ok and agents_ok:
            logger.info(
                "D170: %s EARLY ENTRY approved at +%.1f min | "
                "MFCS=%.3f (>= %.2f) agents_bullish=%d (>= %d)",
                obs.ticker, obs.elapsed_minutes,
                obs.mfcs, self._config.early_entry_min_mfcs,
                obs.bullish_agent_count, self._config.early_entry_min_agents_bullish,
            )
            return True
        return False

    def _check_above_vwap(self, obs: CandidateObservation) -> bool:
        """Price must be at or above VWAP (institutional demand holding the line)."""
        if obs.latest_vwap <= 0:
            return True  # No VWAP data — can't reject on it
        return obs.latest_price >= obs.latest_vwap

    def _check_higher_lows(self, obs: CandidateObservation) -> bool:
        """
        Each successive price reading must not make a new intraday low.

        Looks at all price readings taken so far and checks that the most
        recent reading is not a new overall low. This catches stocks that
        are steadily fading even if each individual step is small.
        """
        if len(obs.prices) < 2:
            return True
        # The min_price running stat is updated on every record_price call.
        # A new low would mean the LATEST price equals the running minimum
        # AND that minimum is lower than the previous minimum.
        # Simple check: current price is the running minimum (new low).
        prices_only = [p for _, p in obs.prices]
        return prices_only[-1] > min(prices_only[:-1])

    def _check_no_new_low_recent(self, obs: CandidateObservation) -> bool:
        """
        No new low in the last N readings (momentum decelerating check).
        Distinct from higher_lows: higher_lows checks the full history,
        this checks only the recent lookback window.
        """
        n = self._config.no_new_low_lookback
        if len(obs.prices) < n + 1:
            return True  # Not enough data yet
        recent = [p for _, p in obs.prices[-(n + 1):]]
        baseline = recent[0]
        return all(p >= baseline for p in recent[1:])

    def _check_volume_sustain(self, obs: CandidateObservation) -> bool:
        """
        Volume during observation must not collapse below min_volume_sustain_pct
        of the first (opening bar) reading. Dry-up signals distribution complete.
        """
        if obs.opening_bar_volume <= 0 or len(obs.volume_readings) < 2:
            return True
        recent_vol = obs.volume_readings[-1][1]
        threshold = obs.opening_bar_volume * self._config.min_volume_sustain_pct
        return recent_vol >= threshold

    def _check_max_drawdown(self, obs: CandidateObservation) -> bool:
        """Price hasn't dropped more than max_drawdown_from_open_pct from open."""
        if obs.open_price <= 0:
            return True
        drawdown_pct = (obs.open_price - obs.min_price) / obs.open_price
        return drawdown_pct <= self._config.max_drawdown_from_open_pct

    def get_approved_candidates(self) -> list[CandidateObservation]:
        """Return all candidates currently in APPROVED state."""
        return [obs for obs in self._candidates.values()
                if obs.state == ObservationState.APPROVED]

    def get_candidate(self, ticker: str) -> Optional[CandidateObservation]:
        """Return the observation record for a ticker, or None."""
        return self._candidates.get(ticker)

    def remove(self, ticker: str) -> None:
        """Remove a candidate (called after order submission or skip decision)."""
        if ticker in self._candidates:
            obs = self._candidates.pop(ticker)
            logger.debug("D170: %s removed from observation (final state=%s)",
                         ticker, obs.state.value)

    def summary(self) -> dict:
        """Return a snapshot of all candidate states for logging."""
        return {
            ticker: {
                "state": obs.state.value,
                "elapsed_min": round(obs.elapsed_minutes, 1),
                "price": obs.latest_price,
                "vwap": obs.latest_vwap,
                "readings": len(obs.prices),
                "rejection_reason": obs.rejection_reason,
            }
            for ticker, obs in self._candidates.items()
        }
