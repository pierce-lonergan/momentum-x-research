#!/usr/bin/env python3
"""
Phase 3 Replay Simulator — D122 Pre-Monday Validation

Replays historical minute-bar data through the complete D122 exit decision
pipeline to predict Monday behavior. This is NOT a backtest of entries — it
simulates the Phase 3 management loop for positions that were actually taken.

Usage:
    # Replay all historical trades through D122 exit logic
    python scripts/phase3_replay_simulator.py --days 90

    # Replay specific trades from the stop-out days (11-14)
    python scripts/phase3_replay_simulator.py --tickers PRSO RLMD RCAT NVTS

    # Replay with sensitivity analysis on confidence threshold
    python scripts/phase3_replay_simulator.py --days 90 --sweep-confidence 0.5 0.6 0.7 0.8 0.9

    # Replay with counterfactual stop widths
    python scripts/phase3_replay_simulator.py --days 90 --sweep-stops 1.5 2.0 2.5 3.0

Output:
    CSV: data/replay/phase3_replay_{timestamp}.csv
    Summary: printed to stdout with per-strategy attribution
"""

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Portable simulation models (no dependency on src/ — this script is standalone)
# ---------------------------------------------------------------------------

@dataclass
class SimBar:
    """Single minute bar for simulation."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float = 0.0


@dataclass
class SimPosition:
    """Simulated position state for Phase 3 replay."""
    ticker: str
    entry_price: float
    entry_time: datetime
    qty: int
    stop_loss: float
    atr: float
    gap_pct: float = 0.0
    catalyst_type: str = "unknown"
    peak_price: float = 0.0
    bars_since_entry: int = 0
    # Tracking
    mfe: float = 0.0  # max favorable excursion (%)
    mae: float = 0.0  # max adverse excursion (%)
    mfe_bar: int = 0   # bar at which MFE occurred
    # State for parallel strategies
    velocity_window: list = field(default_factory=list)
    pullback_state: str = "ADVANCING"
    pullback_peak: float = 0.0
    pullback_trough: float = 0.0
    advance_size: float = 0.0


@dataclass
class ExitSignal:
    """Result from a single exit strategy evaluation."""
    strategy: str
    action: str  # HOLD, TIGHTEN, EXIT
    confidence: float
    reason: str


@dataclass
class ReplayResult:
    """Complete result of replaying one position."""
    ticker: str
    entry_price: float
    entry_time: str
    exit_price: float
    exit_time: str
    exit_bar: int
    exit_reason: str
    pnl_pct: float
    mfe_pct: float
    mae_pct: float
    mfe_bar: int
    hold_minutes: int
    legacy_exit_bar: Optional[int] = None
    legacy_exit_reason: str = ""
    signals_fired: list = field(default_factory=list)
    d122_would_have_changed: bool = False


# ---------------------------------------------------------------------------
# Strategy simulators (mirror D122 logic without importing src/)
# ---------------------------------------------------------------------------

class VelocityEngineSimulator:
    """Simulates VelocityEngine from exit_strategies.py."""

    PHASE_THRESHOLDS = {
        "IGNITION": (0, 5, -0.002),    # 0-5 min: tolerate -0.2%/min
        "THRUST": (5, 15, -0.001),      # 5-15 min: tolerate -0.1%/min
        "CRUISE": (15, 30, -0.0005),    # 15-30 min: tolerate -0.05%/min
        "DECAY": (30, 9999, -0.0003),   # 30+ min: tolerate -0.03%/min
    }

    def evaluate(self, pos: SimPosition, bar: SimBar) -> ExitSignal:
        minutes = pos.bars_since_entry
        phase = "DECAY"
        threshold = -0.0003
        for name, (lo, hi, thr) in self.PHASE_THRESHOLDS.items():
            if lo <= minutes < hi:
                phase = name
                threshold = thr
                break

        # 3-bar rolling velocity
        if len(pos.velocity_window) >= 3:
            velocity = (pos.velocity_window[-1] - pos.velocity_window[-3]) / (
                pos.velocity_window[-3] if pos.velocity_window[-3] != 0 else 1
            ) / 3.0
        else:
            velocity = 0.0

        if velocity < threshold and velocity < 0:
            conf = min(1.0, abs(velocity / threshold))
            return ExitSignal("VelocityEngine", "EXIT", conf,
                              f"{phase}: velocity={velocity:.5f} < {threshold}")
        elif velocity < threshold * 0.5:
            conf = min(1.0, abs(velocity / (threshold * 0.5)))
            return ExitSignal("VelocityEngine", "TIGHTEN", conf * 0.5,
                              f"{phase}: slowing, velocity={velocity:.5f}")
        return ExitSignal("VelocityEngine", "HOLD", 0.0, f"{phase}: ok")


class PullbackClassifierSimulator:
    """Simulates PullbackClassifier state machine."""

    def evaluate(self, pos: SimPosition, bar: SimBar) -> ExitSignal:
        current_return = (bar.close - pos.entry_price) / pos.entry_price

        # Update peak tracking
        if bar.close > pos.pullback_peak:
            pos.pullback_peak = bar.close
            pos.advance_size = (pos.pullback_peak - pos.entry_price) / pos.entry_price

        if pos.advance_size <= 0:
            return ExitSignal("PullbackClassifier", "HOLD", 0.0, "no advance yet")

        # Retracement from peak
        retracement = (pos.pullback_peak - bar.close) / (
            pos.pullback_peak - pos.entry_price
        ) if pos.pullback_peak > pos.entry_price else 0.0

        if retracement > 0.50:
            return ExitSignal("PullbackClassifier", "EXIT", 0.8,
                              f"EXHAUSTED: {retracement:.0%} retracement of {pos.advance_size:.1%} advance")
        elif retracement > 0.30:
            return ExitSignal("PullbackClassifier", "TIGHTEN", 0.5,
                              f"PULLBACK: {retracement:.0%} retracement")
        return ExitSignal("PullbackClassifier", "HOLD", 0.0,
                          f"ADVANCING: {retracement:.0%} retracement")


class VolumeExhaustionSimulator:
    """Simulates VolumeExhaustion strategy."""

    def __init__(self):
        self._entry_volume = {}

    def evaluate(self, pos: SimPosition, bar: SimBar, entry_bar_vol: float) -> ExitSignal:
        if entry_bar_vol <= 0:
            return ExitSignal("VolumeExhaustion", "HOLD", 0.0, "no entry volume")

        ratio = bar.volume / entry_bar_vol if entry_bar_vol > 0 else 1.0

        if ratio < 0.15:
            return ExitSignal("VolumeExhaustion", "EXIT", 0.85,
                              f"ratio={ratio:.2f} < 0.15")
        elif ratio < 0.30:
            return ExitSignal("VolumeExhaustion", "TIGHTEN", 0.5,
                              f"ratio={ratio:.2f} < 0.30")
        return ExitSignal("VolumeExhaustion", "HOLD", 0.0, f"ratio={ratio:.2f}")


class GratitudeExitSimulator:
    """Simulates GratitudeExit time-decaying R-multiple."""

    def evaluate(self, pos: SimPosition, bar: SimBar) -> ExitSignal:
        risk = pos.entry_price - pos.stop_loss
        if risk <= 0:
            return ExitSignal("GratitudeExit", "HOLD", 0.0, "no risk defined")

        r_multiple = (bar.close - pos.entry_price) / risk
        threshold = max(0.75, 3.0 - 0.05 * pos.bars_since_entry)

        if r_multiple >= threshold * 1.5:
            return ExitSignal("GratitudeExit", "EXIT", 0.9,
                              f"R={r_multiple:.2f} >= {threshold*1.5:.2f} (1.5x threshold)")
        elif r_multiple >= threshold:
            return ExitSignal("GratitudeExit", "TIGHTEN", 0.6,
                              f"R={r_multiple:.2f} >= {threshold:.2f}")
        return ExitSignal("GratitudeExit", "HOLD", 0.0,
                          f"R={r_multiple:.2f} < {threshold:.2f}")


class CatalystHalfLifeSimulator:
    """Simulates CatalystHalfLife with known catalyst types."""

    HALF_LIVES = {
        "FDA_APPROVAL": 180, "EARNINGS_BEAT": 120, "MERGER_ACQUISITION": 150,
        "BREAKOUT": 25, "SOCIAL_MEDIA": 12, "PUMP_DUMP": 8,
        "CORPORATE_UPDATE": 30, "SECTOR_CATALYST": 20, "unknown": 20,
    }

    def evaluate(self, pos: SimPosition, bar: SimBar) -> ExitSignal:
        half_life = self.HALF_LIVES.get(pos.catalyst_type, 20)
        minutes = pos.bars_since_entry

        # Velocity check
        if len(pos.velocity_window) >= 3:
            velocity = (pos.velocity_window[-1] - pos.velocity_window[0]) / len(pos.velocity_window)
        else:
            velocity = 0.0

        if minutes > half_life * 1.5 and velocity <= 0:
            return ExitSignal("CatalystHalfLife", "EXIT", 0.75,
                              f"past 1.5x half-life ({half_life}m), velocity={velocity:.5f}")
        elif minutes > half_life * 0.8:
            return ExitSignal("CatalystHalfLife", "TIGHTEN", 0.4,
                              f"approaching half-life ({half_life}m)")
        return ExitSignal("CatalystHalfLife", "HOLD", 0.0,
                          f"{minutes}/{half_life}m elapsed")


class AlphaDecayOracleSimulator:
    """Simulates AlphaDecayOracle with configurable null curve."""

    def __init__(self, null_curve=None):
        self.null_curve = null_curve or [
            (5, 3.0), (10, 2.0), (15, 1.5), (20, 1.0),
            (30, 0.5), (45, 0.2), (60, 0.0),
        ]

    def _interpolate_null(self, minutes: float) -> float:
        if minutes <= self.null_curve[0][0]:
            return self.null_curve[0][1]
        if minutes >= self.null_curve[-1][0]:
            return self.null_curve[-1][1]
        for i in range(len(self.null_curve) - 1):
            t0, v0 = self.null_curve[i]
            t1, v1 = self.null_curve[i + 1]
            if t0 <= minutes <= t1:
                frac = (minutes - t0) / (t1 - t0)
                return v0 + frac * (v1 - v0)
        return 0.0

    MIN_EVALUATION_MINUTES = 5  # D122: Don't evaluate before first null curve point

    def evaluate(self, pos: SimPosition, bar: SimBar) -> ExitSignal:
        minutes = pos.bars_since_entry

        # D122: Minimum evaluation time. At minute 0, observed return ≈ 0%
        # but the null curve says gap-up stocks should be at +3.0% by minute 5.
        # So alpha = 0% - 3.0% = -3.0%, triggering EXIT on every position.
        if minutes < self.MIN_EVALUATION_MINUTES:
            return ExitSignal("AlphaDecayOracle", "HOLD", 0.0,
                              f"below minimum evaluation time ({minutes}/{self.MIN_EVALUATION_MINUTES})")

        observed_return = (bar.close - pos.entry_price) / pos.entry_price * 100
        null_return = self._interpolate_null(minutes)
        alpha = observed_return - null_return

        if alpha <= 0:
            return ExitSignal("AlphaDecayOracle", "EXIT", 0.7,
                              f"alpha={alpha:.2f}% (observed={observed_return:.2f}%, null={null_return:.2f}%)")
        elif alpha < 0.5:
            return ExitSignal("AlphaDecayOracle", "TIGHTEN", 0.4,
                              f"thin alpha={alpha:.2f}%")
        return ExitSignal("AlphaDecayOracle", "HOLD", 0.0,
                          f"alpha={alpha:.2f}%")


# ---------------------------------------------------------------------------
# Composite exit evaluator (mirrors D122 upgrade-only semantics)
# ---------------------------------------------------------------------------

class D122ExitEvaluator:
    """
    Full D122 exit evaluator with upgrade-only semantics.

    Matches the logic in exit_intelligence.py post-D122:
    - Legacy 13-signal composite produces base action
    - Parallel strategies can UPGRADE (HOLD→TIGHTEN, HOLD→EXIT, TIGHTEN→EXIT)
    - Never downgrade (EXIT→HOLD, EXIT→TIGHTEN)
    - Confidence gate: min_confidence (default 0.7)
    - Strategy gate: min_strategies_for_exit (default 1)
    """

    def __init__(self, min_confidence: float = 0.7,
                 min_strategies_for_exit: int = 1,
                 null_curve=None):
        self.min_confidence = min_confidence
        self.min_strategies = min_strategies_for_exit
        self.strategies = [
            VelocityEngineSimulator(),
            PullbackClassifierSimulator(),
            VolumeExhaustionSimulator(),
            GratitudeExitSimulator(),
            CatalystHalfLifeSimulator(),
            AlphaDecayOracleSimulator(null_curve),
        ]

    def evaluate(self, pos: SimPosition, bar: SimBar,
                 entry_bar_vol: float = 0.0) -> tuple[str, list[ExitSignal]]:
        """
        Returns (action, signals) where action is HOLD/TIGHTEN/EXIT
        and signals is the list of all strategy evaluations.
        """
        signals = []
        for strat in self.strategies:
            if isinstance(strat, VolumeExhaustionSimulator):
                sig = strat.evaluate(pos, bar, entry_bar_vol)
            else:
                sig = strat.evaluate(pos, bar)
            signals.append(sig)

        # Legacy action: simple stop-loss check (simulates the 13-signal composite
        # which in practice almost never fires — treat as HOLD unless stopped)
        legacy_action = "HOLD"
        if bar.low <= pos.stop_loss:
            legacy_action = "EXIT"

        # D122 parallel upgrade
        exits = [s for s in signals
                 if s.action == "EXIT" and s.confidence >= self.min_confidence]
        tightens = [s for s in signals
                    if s.action == "TIGHTEN" and s.confidence >= self.min_confidence]

        action = legacy_action

        if len(exits) >= self.min_strategies and action != "EXIT":
            action = "EXIT"
        elif tightens and action == "HOLD":
            action = "TIGHTEN"

        return action, signals


# ---------------------------------------------------------------------------
# Minute-bar loader (reads cached backtest data)
# ---------------------------------------------------------------------------

def load_minute_bars(ticker: str, date: str,
                     cache_dir: str = "data/backtest_cache") -> list[SimBar]:
    """
    Load minute bars from backtest cache.
    Supports both JSON and CSV formats.
    """
    bars = []

    # Try JSON format first (Alpaca cache)
    json_path = Path(cache_dir) / f"{ticker}_{date}.json"
    if json_path.exists():
        with open(json_path) as f:
            raw = json.load(f)
        for b in raw:
            bars.append(SimBar(
                timestamp=datetime.fromisoformat(b.get("t", b.get("timestamp", ""))),
                open=float(b.get("o", b.get("open", 0))),
                high=float(b.get("h", b.get("high", 0))),
                close=float(b.get("c", b.get("close", 0))),
                low=float(b.get("l", b.get("low", 0))),
                volume=float(b.get("v", b.get("volume", 0))),
                vwap=float(b.get("vw", b.get("vwap", 0))),
            ))
        return bars

    # Try CSV format
    csv_path = Path(cache_dir) / f"{ticker}_{date}.csv"
    if csv_path.exists():
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                bars.append(SimBar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    vwap=float(row.get("vwap", 0)),
                ))
        return bars

    # Try glob for any matching file
    cache = Path(cache_dir)
    for p in sorted(cache.glob(f"{ticker}*")):
        if date.replace("-", "") in p.stem or date in p.stem:
            # Try to parse it
            try:
                with open(p) as f:
                    if p.suffix == ".json":
                        raw = json.load(f)
                        for b in (raw if isinstance(raw, list) else raw.get("bars", [])):
                            bars.append(SimBar(
                                timestamp=datetime.fromisoformat(str(b.get("t", ""))),
                                open=float(b.get("o", 0)), high=float(b.get("h", 0)),
                                low=float(b.get("l", 0)), close=float(b.get("c", 0)),
                                volume=float(b.get("v", 0)), vwap=float(b.get("vw", 0)),
                            ))
            except Exception:
                pass
    return bars


def load_trades_from_journal(journal_dir: str = "data/trade_journal") -> list[dict]:
    """Load historical trades from JSONL trade journal."""
    trades = []
    jdir = Path(journal_dir)
    if not jdir.exists():
        return trades

    for f in sorted(jdir.glob("*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("action") == "BUY" or entry.get("entry_price"):
                        trades.append(entry)
                except json.JSONDecodeError:
                    continue
    return trades


# ---------------------------------------------------------------------------
# Core replay engine
# ---------------------------------------------------------------------------

def replay_position(
    pos: SimPosition,
    bars: list[SimBar],
    evaluator: D122ExitEvaluator,
    legacy_stop: float,
    d122_stop: float,
) -> tuple[ReplayResult, ReplayResult]:
    """
    Replay a single position through both legacy and D122 exit logic.

    Returns (legacy_result, d122_result) for direct comparison.
    """
    entry_bar_vol = bars[0].volume if bars else 0.0

    # --- Legacy replay (stop-loss only, no parallel strategies) ---
    legacy_pos = SimPosition(
        ticker=pos.ticker, entry_price=pos.entry_price,
        entry_time=pos.entry_time, qty=pos.qty,
        stop_loss=legacy_stop, atr=pos.atr,
        gap_pct=pos.gap_pct, catalyst_type=pos.catalyst_type,
    )
    legacy_exit_bar = len(bars) - 1
    legacy_exit_reason = "EOD_CLOSE"
    legacy_exit_price = bars[-1].close if bars else pos.entry_price

    for i, bar in enumerate(bars):
        pct = (bar.high - pos.entry_price) / pos.entry_price
        if pct > legacy_pos.mfe:
            legacy_pos.mfe = pct
            legacy_pos.mfe_bar = i
        pct_low = (pos.entry_price - bar.low) / pos.entry_price
        if pct_low > legacy_pos.mae:
            legacy_pos.mae = pct_low

        if bar.low <= legacy_pos.stop_loss:
            legacy_exit_bar = i
            legacy_exit_price = legacy_pos.stop_loss
            legacy_exit_reason = "STOP_LOSS"
            break

    legacy_pnl = (legacy_exit_price - pos.entry_price) / pos.entry_price

    # --- D122 replay (parallel strategies active) ---
    d122_pos = SimPosition(
        ticker=pos.ticker, entry_price=pos.entry_price,
        entry_time=pos.entry_time, qty=pos.qty,
        stop_loss=d122_stop, atr=pos.atr,
        gap_pct=pos.gap_pct, catalyst_type=pos.catalyst_type,
    )
    d122_pos.pullback_peak = pos.entry_price

    d122_exit_bar = len(bars) - 1
    d122_exit_reason = "EOD_CLOSE"
    d122_exit_price = bars[-1].close if bars else pos.entry_price
    all_signals = []

    for i, bar in enumerate(bars):
        d122_pos.bars_since_entry = i
        d122_pos.velocity_window.append(bar.close)
        if len(d122_pos.velocity_window) > 10:
            d122_pos.velocity_window = d122_pos.velocity_window[-10:]

        # Track MFE/MAE
        pct = (bar.high - pos.entry_price) / pos.entry_price
        if pct > d122_pos.mfe:
            d122_pos.mfe = pct
            d122_pos.mfe_bar = i
        pct_low = (pos.entry_price - bar.low) / pos.entry_price
        if pct_low > d122_pos.mae:
            d122_pos.mae = pct_low

        if bar.close > d122_pos.peak_price:
            d122_pos.peak_price = bar.close

        # Check stop loss first
        if bar.low <= d122_pos.stop_loss:
            d122_exit_bar = i
            d122_exit_price = d122_pos.stop_loss
            d122_exit_reason = "STOP_LOSS"
            break

        # D122 parallel evaluation
        action, signals = evaluator.evaluate(d122_pos, bar, entry_bar_vol)
        for s in signals:
            if s.action != "HOLD":
                all_signals.append({
                    "bar": i, "strategy": s.strategy,
                    "action": s.action, "confidence": round(s.confidence, 3),
                    "reason": s.reason,
                })

        if action == "EXIT":
            d122_exit_bar = i
            d122_exit_price = bar.close
            firing = [s for s in signals if s.action == "EXIT"
                      and s.confidence >= evaluator.min_confidence]
            d122_exit_reason = f"D122_PARALLEL: {', '.join(s.strategy for s in firing)}"
            break
        elif action == "TIGHTEN":
            # Ratchet stop up
            firing = [s for s in signals if s.action == "TIGHTEN"
                      and s.confidence >= evaluator.min_confidence]
            if firing:
                best = max(firing, key=lambda s: s.confidence)
                trail = d122_pos.atr * (2.0 - best.confidence)
                new_stop = bar.close - trail
                if new_stop > d122_pos.stop_loss:
                    d122_pos.stop_loss = new_stop

    d122_pnl = (d122_exit_price - pos.entry_price) / pos.entry_price

    legacy_result = ReplayResult(
        ticker=pos.ticker, entry_price=pos.entry_price,
        entry_time=str(pos.entry_time),
        exit_price=round(legacy_exit_price, 4),
        exit_time=str(bars[legacy_exit_bar].timestamp) if bars else "",
        exit_bar=legacy_exit_bar,
        exit_reason=legacy_exit_reason,
        pnl_pct=round(legacy_pnl * 100, 2),
        mfe_pct=round(legacy_pos.mfe * 100, 2),
        mae_pct=round(legacy_pos.mae * 100, 2),
        mfe_bar=legacy_pos.mfe_bar,
        hold_minutes=legacy_exit_bar,
    )

    d122_result = ReplayResult(
        ticker=pos.ticker, entry_price=pos.entry_price,
        entry_time=str(pos.entry_time),
        exit_price=round(d122_exit_price, 4),
        exit_time=str(bars[d122_exit_bar].timestamp) if bars else "",
        exit_bar=d122_exit_bar,
        exit_reason=d122_exit_reason,
        pnl_pct=round(d122_pnl * 100, 2),
        mfe_pct=round(d122_pos.mfe * 100, 2),
        mae_pct=round(d122_pos.mae * 100, 2),
        mfe_bar=d122_pos.mfe_bar,
        hold_minutes=d122_exit_bar,
        legacy_exit_bar=legacy_exit_bar,
        legacy_exit_reason=legacy_exit_reason,
        signals_fired=all_signals,
        d122_would_have_changed=(d122_exit_bar != legacy_exit_bar),
    )

    return legacy_result, d122_result


# ---------------------------------------------------------------------------
# Sensitivity sweep
# ---------------------------------------------------------------------------

def sweep_confidence_thresholds(
    positions: list[tuple[SimPosition, list[SimBar]]],
    thresholds: list[float],
) -> dict:
    """
    Sweep confidence thresholds and report aggregate P&L for each.
    """
    results = {}
    for thresh in thresholds:
        evaluator = D122ExitEvaluator(min_confidence=thresh, min_strategies_for_exit=1)
        total_pnl = 0.0
        exits_fired = 0
        win_count = 0
        n = 0

        for pos, bars in positions:
            if not bars:
                continue
            legacy_stop = pos.entry_price * (1 - max(pos.atr * 2.0 / pos.entry_price, 0.04))
            gap_floor = pos.entry_price * abs(pos.gap_pct) * 0.5 if abs(pos.gap_pct) > 0.08 else 0
            d122_stop_dist = max(pos.atr * 2.0, pos.entry_price * 0.04, gap_floor)
            d122_stop = pos.entry_price - min(d122_stop_dist, pos.entry_price * 0.20)

            _, d122 = replay_position(pos, bars, evaluator, legacy_stop, d122_stop)
            total_pnl += d122.pnl_pct
            if "D122_PARALLEL" in d122.exit_reason:
                exits_fired += 1
            if d122.pnl_pct > 0:
                win_count += 1
            n += 1

        results[thresh] = {
            "threshold": thresh,
            "avg_pnl": round(total_pnl / max(n, 1), 2),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_count / max(n, 1) * 100, 1),
            "parallel_exits": exits_fired,
            "trades": n,
        }
    return results


def sweep_stop_multipliers(
    positions: list[tuple[SimPosition, list[SimBar]]],
    multipliers: list[float],
) -> dict:
    """
    Sweep ATR stop multipliers and report aggregate P&L for each.
    """
    results = {}
    evaluator = D122ExitEvaluator(min_confidence=0.7, min_strategies_for_exit=1)

    for mult in multipliers:
        total_pnl = 0.0
        stopped = 0
        stopped_with_prior_gain = 0
        win_count = 0
        n = 0

        for pos, bars in positions:
            if not bars:
                continue
            stop_dist = max(pos.atr * mult, pos.entry_price * 0.04)
            stop = pos.entry_price - min(stop_dist, pos.entry_price * 0.20)

            _, d122 = replay_position(pos, bars, evaluator, stop, stop)
            total_pnl += d122.pnl_pct
            if "STOP_LOSS" in d122.exit_reason:
                stopped += 1
                if d122.mfe_pct > 0:
                    stopped_with_prior_gain += 1
            if d122.pnl_pct > 0:
                win_count += 1
            n += 1

        results[mult] = {
            "multiplier": mult,
            "avg_pnl": round(total_pnl / max(n, 1), 2),
            "win_rate": round(win_count / max(n, 1) * 100, 1),
            "stop_rate": round(stopped / max(n, 1) * 100, 1),
            "stopped_with_gain": round(stopped_with_prior_gain / max(n, 1) * 100, 1),
            "trades": n,
        }
    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_comparison_report(
    legacy_results: list[ReplayResult],
    d122_results: list[ReplayResult],
):
    """Print a formatted comparison of legacy vs D122 exits."""
    n = len(legacy_results)
    if n == 0:
        print("No results to report.")
        return

    print("\n" + "=" * 80)
    print("PHASE 3 REPLAY: LEGACY vs D122 COMPARISON")
    print("=" * 80)

    # Aggregate stats
    legacy_pnl = sum(r.pnl_pct for r in legacy_results)
    d122_pnl = sum(r.pnl_pct for r in d122_results)
    legacy_wins = sum(1 for r in legacy_results if r.pnl_pct > 0)
    d122_wins = sum(1 for r in d122_results if r.pnl_pct > 0)
    legacy_stops = sum(1 for r in legacy_results if "STOP" in r.exit_reason)
    d122_stops = sum(1 for r in d122_results if "STOP" in r.exit_reason)
    d122_parallel = sum(1 for r in d122_results if "D122_PARALLEL" in r.exit_reason)
    changed = sum(1 for r in d122_results if r.d122_would_have_changed)

    print(f"\n{'Metric':<30} {'Legacy':>12} {'D122':>12} {'Delta':>12}")
    print("-" * 66)
    print(f"{'Trades':<30} {n:>12} {n:>12} {'':>12}")
    print(f"{'Total P&L (%)':<30} {legacy_pnl:>11.1f}% {d122_pnl:>11.1f}% {d122_pnl-legacy_pnl:>+11.1f}%")
    print(f"{'Avg P&L (%)':<30} {legacy_pnl/n:>11.2f}% {d122_pnl/n:>11.2f}% {(d122_pnl-legacy_pnl)/n:>+11.2f}%")
    print(f"{'Win Rate':<30} {legacy_wins/n*100:>11.1f}% {d122_wins/n*100:>11.1f}% {(d122_wins-legacy_wins)/n*100:>+11.1f}%")
    print(f"{'Stop-Outs':<30} {legacy_stops:>12} {d122_stops:>12} {d122_stops-legacy_stops:>+12}")
    print(f"{'Parallel Exits':<30} {'N/A':>12} {d122_parallel:>12} {'':>12}")
    print(f"{'EOD Closes':<30} {n-legacy_stops:>12} {n-d122_stops-d122_parallel:>12} {'':>12}")
    print(f"{'D122 Changed Outcome':<30} {'':>12} {changed:>12} {'':>12}")

    # Per-strategy attribution
    strategy_exits = {}
    for r in d122_results:
        for s in r.signals_fired:
            if s["action"] == "EXIT" and s["confidence"] >= 0.7:
                name = s["strategy"]
                strategy_exits[name] = strategy_exits.get(name, 0) + 1

    if strategy_exits:
        print(f"\n{'Strategy Exit Fires (conf >= 0.7)':}")
        print("-" * 40)
        for name, count in sorted(strategy_exits.items(), key=lambda x: -x[1]):
            print(f"  {name:<30} {count:>6}")

    # Per-trade detail for changed outcomes
    changed_trades = [(l, d) for l, d in zip(legacy_results, d122_results)
                      if d.d122_would_have_changed]
    if changed_trades:
        print(f"\n{'TRADES WHERE D122 CHANGED OUTCOME':}")
        print("-" * 80)
        print(f"{'Ticker':<8} {'Legacy Exit':<15} {'D122 Exit':<15} "
              f"{'Legacy P&L':>10} {'D122 P&L':>10} {'Delta':>10} {'D122 Reason'}")
        print("-" * 80)
        for leg, d12 in changed_trades:
            delta = d12.pnl_pct - leg.pnl_pct
            marker = "  [+]" if delta > 0 else " [-]" if delta < 0 else ""
            print(f"{d12.ticker:<8} "
                  f"bar {leg.exit_bar:<4} {leg.exit_reason:<10} "
                  f"bar {d12.exit_bar:<4} {d12.exit_reason[:10]:<10} "
                  f"{leg.pnl_pct:>+9.1f}% {d12.pnl_pct:>+9.1f}% "
                  f"{delta:>+9.1f}%{marker}")

    print("\n" + "=" * 80)


# ---------------------------------------------------------------------------
# Synthetic position generator for when real data isn't available
# ---------------------------------------------------------------------------

def generate_synthetic_positions(n: int = 50, seed: int = 42) -> list[tuple[SimPosition, list[SimBar]]]:
    """
    Generate synthetic positions with realistic price paths for testing
    when backtest cache data isn't available.

    Produces 4 archetypes:
    1. Gap-and-go (strong momentum, peaks early, fades)
    2. Gap-and-fade (immediate reversal)
    3. Consolidation (flat after gap, breakout or breakdown later)
    4. Grind higher (slow steady advance)
    """
    import random
    random.seed(seed)
    positions = []

    archetypes = [
        ("gap_and_go", 0.35),
        ("gap_and_fade", 0.30),
        ("consolidation", 0.20),
        ("grind_higher", 0.15),
    ]

    for i in range(n):
        # Pick archetype
        r = random.random()
        cum = 0
        archetype = "consolidation"
        for name, prob in archetypes:
            cum += prob
            if r <= cum:
                archetype = name
                break

        entry = round(random.uniform(3, 25), 2)
        gap_pct = round(random.uniform(0.05, 0.25), 3)
        atr = round(entry * random.uniform(0.02, 0.06), 4)
        entry_time = datetime(2026, 3, 15, 9, 30) + timedelta(minutes=random.randint(0, 30))

        # Generate 390 minute bars (full trading day)
        bars = []
        price = entry
        peak = entry
        entry_vol = random.uniform(50000, 500000)

        for m in range(390):
            t = entry_time + timedelta(minutes=m)

            if archetype == "gap_and_go":
                # Strong early, peaks at 15-45 min, slow fade
                if m < random.randint(15, 45):
                    drift = random.gauss(0.002, 0.005)
                else:
                    drift = random.gauss(-0.0008, 0.004)
            elif archetype == "gap_and_fade":
                # Immediate reversal
                drift = random.gauss(-0.0015, 0.005)
            elif archetype == "consolidation":
                # Flat 30 min, then random direction
                if m < 30:
                    drift = random.gauss(0, 0.003)
                else:
                    drift = random.gauss(0.0005, 0.004)
            else:  # grind_higher
                drift = random.gauss(0.0008, 0.003)

            price = price * (1 + drift)
            high = price * (1 + abs(random.gauss(0, 0.002)))
            low = price * (1 - abs(random.gauss(0, 0.002)))
            vol = entry_vol * max(0.05, (1 - m / 500) + random.gauss(0, 0.2))

            bars.append(SimBar(
                timestamp=t, open=price, high=high,
                low=low, close=price, volume=max(100, vol),
            ))

            if price > peak:
                peak = price

        pos = SimPosition(
            ticker=f"SYN{i:03d}",
            entry_price=entry,
            entry_time=entry_time,
            qty=100,
            stop_loss=entry - max(atr * 2.0, entry * 0.04),
            atr=atr,
            gap_pct=gap_pct,
            catalyst_type=random.choice(["unknown", "CORPORATE_UPDATE", "BREAKOUT", "FDA_APPROVAL"]),
        )
        positions.append((pos, bars))

    return positions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Phase 3 Replay Simulator")
    parser.add_argument("--days", type=int, default=90, help="Days of history")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to replay")
    parser.add_argument("--sweep-confidence", nargs="+", type=float,
                        help="Sweep confidence thresholds")
    parser.add_argument("--sweep-stops", nargs="+", type=float,
                        help="Sweep ATR stop multipliers")
    parser.add_argument("--synthetic", type=int, default=0,
                        help="Generate N synthetic positions if no real data")
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--min-strategies", type=int, default=1)
    parser.add_argument("--output", default="data/replay")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Load positions — try real data first, fall back to synthetic
    positions = []

    # Try loading from trade journal
    trades = load_trades_from_journal()
    if trades and not args.synthetic:
        print(f"Loaded {len(trades)} trades from journal")
        for t in trades:
            ticker = t.get("ticker", "")
            if args.tickers and ticker not in args.tickers:
                continue
            entry_price = float(t.get("entry_price", t.get("signal_price", 0)))
            if entry_price <= 0:
                continue

            entry_date = t.get("date", t.get("timestamp", ""))[:10]
            bars = load_minute_bars(ticker, entry_date)
            if not bars:
                continue

            atr = float(t.get("atr", entry_price * 0.03))
            gap_pct = float(t.get("gap_pct", 0.08))

            pos = SimPosition(
                ticker=ticker, entry_price=entry_price,
                entry_time=bars[0].timestamp if bars else datetime.now(),
                qty=int(t.get("qty", 100)),
                stop_loss=float(t.get("stop_loss", entry_price * 0.94)),
                atr=atr, gap_pct=gap_pct,
                catalyst_type=t.get("catalyst_type", "unknown"),
            )
            positions.append((pos, bars))

    if not positions:
        n = args.synthetic or 50
        print(f"No real data found. Generating {n} synthetic positions...")
        positions = generate_synthetic_positions(n)

    print(f"Replaying {len(positions)} positions through D122 exit logic")

    # Main replay
    evaluator = D122ExitEvaluator(
        min_confidence=args.min_confidence,
        min_strategies_for_exit=args.min_strategies,
    )

    legacy_results = []
    d122_results = []

    for pos, bars in positions:
        if not bars:
            continue

        legacy_stop = pos.entry_price - max(pos.atr * 2.0, pos.entry_price * 0.04)
        gap_floor = pos.entry_price * abs(pos.gap_pct) * 0.5 if abs(pos.gap_pct) > 0.08 else 0
        d122_stop_dist = max(pos.atr * 2.0, pos.entry_price * 0.04, gap_floor)
        d122_stop = pos.entry_price - min(d122_stop_dist, pos.entry_price * 0.20)

        leg, d12 = replay_position(pos, bars, evaluator, legacy_stop, d122_stop)
        legacy_results.append(leg)
        d122_results.append(d12)

    # Print comparison
    print_comparison_report(legacy_results, d122_results)

    # Sweeps
    if args.sweep_confidence:
        print("\n\nCONFIDENCE THRESHOLD SWEEP")
        print("=" * 70)
        results = sweep_confidence_thresholds(positions, args.sweep_confidence)
        print(f"{'Threshold':>10} {'Avg P&L':>10} {'Win Rate':>10} "
              f"{'Parallel':>10} {'Trades':>8}")
        print("-" * 48)
        for thresh, r in sorted(results.items()):
            print(f"{thresh:>10.2f} {r['avg_pnl']:>9.2f}% {r['win_rate']:>9.1f}% "
                  f"{r['parallel_exits']:>10} {r['trades']:>8}")

    if args.sweep_stops:
        print("\n\nATR STOP MULTIPLIER SWEEP")
        print("=" * 70)
        results = sweep_stop_multipliers(positions, args.sweep_stops)
        print(f"{'Mult':>8} {'Avg P&L':>10} {'Win Rate':>10} "
              f"{'Stop Rate':>10} {'Stopped+Gain':>13}")
        print("-" * 51)
        for mult, r in sorted(results.items()):
            print(f"{mult:>8.1f}x {r['avg_pnl']:>9.2f}% {r['win_rate']:>9.1f}% "
                  f"{r['stop_rate']:>9.1f}% {r['stopped_with_gain']:>12.1f}%")

    # Save CSV
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(args.output) / f"phase3_replay_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "system", "ticker", "entry_price", "exit_price",
            "exit_bar", "exit_reason", "pnl_pct", "mfe_pct",
            "mae_pct", "mfe_bar", "hold_minutes",
        ])
        writer.writeheader()
        for r in legacy_results:
            writer.writerow({
                "system": "legacy", "ticker": r.ticker,
                "entry_price": r.entry_price, "exit_price": r.exit_price,
                "exit_bar": r.exit_bar, "exit_reason": r.exit_reason,
                "pnl_pct": r.pnl_pct, "mfe_pct": r.mfe_pct,
                "mae_pct": r.mae_pct, "mfe_bar": r.mfe_bar,
                "hold_minutes": r.hold_minutes,
            })
        for r in d122_results:
            writer.writerow({
                "system": "d122", "ticker": r.ticker,
                "entry_price": r.entry_price, "exit_price": r.exit_price,
                "exit_bar": r.exit_bar, "exit_reason": r.exit_reason,
                "pnl_pct": r.pnl_pct, "mfe_pct": r.mfe_pct,
                "mae_pct": r.mae_pct, "mfe_bar": r.mfe_bar,
                "hold_minutes": r.hold_minutes,
            })

    print(f"\nResults saved to {csv_path}")


if __name__ == "__main__":
    main()
