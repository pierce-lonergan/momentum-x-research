"""
Failure Mode Clustering + Conditional Defense.

Extracts price path features from losing trades, clusters them into
archetypes (gap-and-fade, slow bleed, phantom gap, pump-dump), and
builds a real-time classifier that adapts defense per trade shape.

Innovation 5 from the final assessment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TradeFeatures:
    """Features extracted from a trade's price path for clustering."""
    ticker: str
    pnl: float
    # Shape features
    initial_5bar_return: float    # Return in first 5 bars (direction signal)
    time_to_minimum: int          # Bars until MAE (how fast it drops)
    max_drawdown_pct: float       # MAE as % of entry
    recovery_ratio: float         # How much it recovered from MAE
    volume_fade_rate: float       # Avg volume decay over hold period
    # Classification
    archetype: str = ""           # Assigned after clustering


def extract_features(trade: dict, bars_data: dict | None = None) -> TradeFeatures:
    """Extract shape features from a simulated trade result."""
    fill = trade.get("fill_price", 0)
    pnl = trade.get("pnl", 0)
    mae_pct = trade.get("mae_pct", 0)
    mfe_pct = trade.get("mfe_pct", 0)
    mae_bar = trade.get("mae_bar", 0)
    mfe_bar = trade.get("mfe_bar", 0)

    # Recovery: how much of the drop was recovered
    if mae_pct > 0:
        # If we hit MAE then recovered some, recovery = (final - mae) / mae
        recovery = (mfe_pct + mae_pct) / mae_pct if mae_pct > 0.001 else 0
    else:
        recovery = 1.0  # Never dropped below entry

    return TradeFeatures(
        ticker=trade.get("ticker", ""),
        pnl=pnl,
        initial_5bar_return=0.0,  # Would need bar data to compute
        time_to_minimum=mae_bar,
        max_drawdown_pct=mae_pct,
        recovery_ratio=min(recovery, 5.0),
        volume_fade_rate=0.0,  # Would need bar data
    )


def classify_archetype(features: TradeFeatures) -> str:
    """
    Classify a losing trade into failure archetypes.

    Simple threshold-based classification (no ML needed):
    - GAP_AND_FADE: Immediate decline, MAE in first 10 bars, low recovery
    - SLOW_BLEED: Gradual decline, MAE after 30 bars, no recovery
    - PHANTOM_GAP: Small drawdown but stops out on noise (MAE near stop)
    - PUMP_DUMP: Initial rise (MFE > 3%) then crash below entry
    """
    if features.pnl >= 0:
        return "WINNER"  # Not a failure

    # GAP_AND_FADE: drops fast, stays down
    if features.time_to_minimum <= 10 and features.max_drawdown_pct > 0.03:
        return "GAP_AND_FADE"

    # SLOW_BLEED: gradual decline
    if features.time_to_minimum > 30 and features.recovery_ratio < 0.3:
        return "SLOW_BLEED"

    # PHANTOM_GAP: small drawdown, stopped on noise
    if features.max_drawdown_pct < 0.02:
        return "PHANTOM_GAP"

    # Default: general loss
    return "GENERAL_LOSS"


def get_archetype_defense(archetype: str) -> dict:
    """
    Get recommended defensive parameters per failure archetype.

    These are starting points — sweep to find optimal per archetype.
    """
    defenses = {
        "GAP_AND_FADE": {
            "stop_loss_pct": 0.02,       # Ultra-tight — cut immediately
            "phase_stop_t1": 3,          # Phase 1 only 3 bars
            "phase1_stop_pct": 0.015,    # 1.5% Phase 1 stop
            "exit_intel_confidence": 0.3, # Lower bar for exit signals
        },
        "SLOW_BLEED": {
            "stop_loss_pct": 0.04,       # Normal stop
            "phase_stop_t1": 5,
            "phase_stop_t2": 15,         # Quick transition to trailing
            "phase3_trail_pct": 0.015,   # Tight trail
        },
        "PHANTOM_GAP": {
            "stop_loss_pct": 0.03,       # Slightly wider than gap noise
            "phase_stop_t1": 7,          # Give more room early
            "phase1_stop_pct": 0.025,
        },
        "GENERAL_LOSS": {
            "stop_loss_pct": 0.04,       # Default
        },
        "WINNER": {},  # No defense needed
    }
    return defenses.get(archetype, defenses["GENERAL_LOSS"])


def classify_early(
    bars_since_entry: list,
    fill_price: float,
    n_bars: int = 5,
) -> str:
    """
    Real-time archetype classification from first N bars.

    Called during trade simulation to apply conditional defense.
    """
    if len(bars_since_entry) < n_bars or fill_price <= 0:
        return "UNKNOWN"

    # Compute features from first N bars
    first_n = bars_since_entry[:n_bars]
    min_low = min(b.low for b in first_n)
    max_high = max(b.high for b in first_n)
    last_close = first_n[-1].close

    initial_return = (last_close - fill_price) / fill_price
    initial_drawdown = (fill_price - min_low) / fill_price

    # Simple threshold classifier
    if initial_return < -0.02 and initial_drawdown > 0.03:
        return "GAP_AND_FADE"
    elif abs(initial_return) < 0.005 and initial_drawdown < 0.01:
        return "SLOW_BLEED"  # Flat start often precedes slow decline
    elif initial_drawdown < 0.015 and initial_return > -0.01:
        return "PHANTOM_GAP"
    elif initial_return > 0.02:
        return "WINNER"  # Strong start

    return "UNKNOWN"


def analyze_failure_distribution(trades: list[dict]) -> dict:
    """
    Analyze failure mode distribution across a set of trades.

    Returns counts and P&L by archetype.
    """
    archetypes: dict[str, list] = {}

    for t in trades:
        features = extract_features(t)
        arch = classify_archetype(features)

        if arch not in archetypes:
            archetypes[arch] = []
        archetypes[arch].append({
            "ticker": t.get("ticker", ""),
            "pnl": t.get("pnl", 0),
            "mae_pct": t.get("mae_pct", 0),
            "mfe_pct": t.get("mfe_pct", 0),
        })

    summary = {}
    for arch, trades_list in archetypes.items():
        total_pnl = sum(t["pnl"] for t in trades_list)
        avg_mae = sum(t["mae_pct"] for t in trades_list) / max(len(trades_list), 1)
        summary[arch] = {
            "count": len(trades_list),
            "total_pnl": round(total_pnl, 4),
            "avg_mae_pct": round(avg_mae, 4),
            "tickers": [t["ticker"] for t in trades_list],
            "defense": get_archetype_defense(arch),
        }

    return summary


def format_failure_analysis(summary: dict) -> str:
    """Format failure mode analysis as readable text."""
    lines = ["FAILURE MODE ANALYSIS", "=" * 60]

    for arch in sorted(summary, key=lambda a: summary[a]["total_pnl"]):
        info = summary[arch]
        lines.append(
            f"\n  {arch:20s}: {info['count']} trades, "
            f"P&L=${info['total_pnl']:+.2f}, avg MAE={info['avg_mae_pct']:.1%}"
        )
        lines.append(f"    Tickers: {', '.join(info['tickers'][:5])}")
        if info["defense"]:
            defense_str = ", ".join(f"{k}={v}" for k, v in info["defense"].items())
            lines.append(f"    Defense: {defense_str}")

    return "\n".join(lines)
