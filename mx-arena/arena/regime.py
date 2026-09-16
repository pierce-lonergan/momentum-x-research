"""
Regime-Conditional Optimization + Parameter Drift Detection.

Instead of finding one parameter set for all days, find different sets
for different market regimes. The bot then checks regime at startup and
loads the appropriate parameters.

Sprint 5 innovations:
1. Regime labeling from SPY return + VIX proxy (VIXY)
2. Per-regime parameter optimization
3. Automated drift detection: alert when recent performance degrades

A true 10/10 system adapts to the market environment.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .stats import bootstrap_profit_factor

logger = logging.getLogger(__name__)


# ── Regime Classification ────────────────────────────────────────────

@dataclass
class DayRegime:
    """Market regime for one trading day."""
    date: str
    spy_return_pct: float       # SPY daily return
    vixy_return_pct: float      # VIXY daily return (VIX proxy)
    regime: str                 # LOW_VOL_TREND, ELEVATED_VOL, CRISIS


def classify_regime(spy_return: float, vixy_return: float) -> str:
    """
    Classify market regime from SPY and VIXY daily returns.

    LOW_VOL_TREND:  SPY > -0.5%, VIXY < +5%  (normal trending market)
    ELEVATED_VOL:   SPY -0.5% to -1.5% OR VIXY +5% to +15%
    CRISIS:         SPY < -1.5% OR VIXY > +15%
    """
    if spy_return < -1.5 or vixy_return > 15:
        return "CRISIS"
    elif spy_return < -0.5 or vixy_return > 5:
        return "ELEVATED_VOL"
    else:
        return "LOW_VOL_TREND"


def label_dates_with_regime(
    dates: list[str],
    daily_bars_dir: str | Path,
) -> list[DayRegime]:
    """
    Label each date with its market regime using SPY and VIXY daily bars.
    """
    daily_dir = Path(daily_bars_dir)
    regimes = []

    spy_bars = _load_daily_bars(daily_dir, "SPY")
    vixy_bars = _load_daily_bars(daily_dir, "VIXY")

    spy_by_date = {_extract_date(b.get("t", b.get("timestamp", ""))): b for b in spy_bars}
    vixy_by_date = {_extract_date(b.get("t", b.get("timestamp", ""))): b for b in vixy_bars}

    for date in dates:
        spy_bar = spy_by_date.get(date)
        vixy_bar = vixy_by_date.get(date)

        if spy_bar and spy_bar.get("o", 0) > 0:
            _spy_c = spy_bar.get("c", 0)
            _spy_o = spy_bar.get("o", 0)
            spy_return = ((_spy_c - _spy_o) / _spy_o) * 100 if _spy_o > 0 else 0.0
        else:
            spy_return = 0.0

        if vixy_bar and vixy_bar.get("o", 0) > 0:
            _vixy_c = vixy_bar.get("c", 0)
            _vixy_o = vixy_bar.get("o", 0)
            vixy_return = ((_vixy_c - _vixy_o) / _vixy_o) * 100 if _vixy_o > 0 else 0.0
        else:
            vixy_return = 0.0

        regime = classify_regime(spy_return, vixy_return)
        regimes.append(DayRegime(
            date=date,
            spy_return_pct=round(spy_return, 2),
            vixy_return_pct=round(vixy_return, 2),
            regime=regime,
        ))

    # Log regime distribution
    counts = {}
    for r in regimes:
        counts[r.regime] = counts.get(r.regime, 0) + 1
    logger.info("Regime distribution: %s", counts)

    return regimes


def _load_daily_bars(daily_dir: Path, symbol: str) -> list[dict]:
    """Load daily bars for a symbol from Parquet or JSON."""
    try:
        import pandas as pd
        parquet = daily_dir / f"{symbol}.parquet"
        if parquet.exists():
            df = pd.read_parquet(parquet)
            return df.to_dict("records")
    except ImportError:
        pass

    json_path = daily_dir / f"{symbol}_daily.json"
    if json_path.exists():
        with open(json_path) as f:
            return json.load(f)

    return []


def _extract_date(ts) -> str:
    """Extract YYYY-MM-DD from timestamp string or object."""
    if not ts:
        return ""
    s = str(ts)
    return s[:10]


# ── Parameter Drift Detection ────────────────────────────────────────

@dataclass
class DriftAlert:
    """Alert when parameter performance degrades."""
    metric: str
    training_value: float
    recent_value: float
    z_score: float
    is_alert: bool
    message: str


def detect_parameter_drift(
    training_trades: list[dict],
    recent_trades: list[dict],
    alert_threshold_z: float = 2.0,
) -> list[DriftAlert]:
    """
    Compare recent simulation performance to training baseline.

    Uses bootstrap to estimate training distribution, then checks
    if recent performance falls outside the 95% CI.

    Run this after each new trading day: replay with current production
    params, compare to the training set performance.

    Returns alerts for any metric where z-score exceeds threshold.
    """
    alerts = []

    if len(training_trades) < 5 or len(recent_trades) < 3:
        return alerts

    # PF drift
    train_pf, train_lo, train_hi = bootstrap_profit_factor(training_trades)
    recent_pf, _, _ = bootstrap_profit_factor(recent_trades)

    train_std = (train_hi - train_lo) / 4  # Approximate std from 95% CI
    if train_std > 0:
        z = (train_pf - recent_pf) / train_std
    else:
        z = 0

    alerts.append(DriftAlert(
        metric="profit_factor",
        training_value=train_pf,
        recent_value=recent_pf,
        z_score=round(z, 2),
        is_alert=z > alert_threshold_z,
        message=(
            f"PF drift: train={train_pf:.2f}, recent={recent_pf:.2f}, z={z:.1f}"
            + (" — RE-OPTIMIZE RECOMMENDED" if z > alert_threshold_z else "")
        ),
    ))

    # Win rate drift
    train_wr = sum(1 for t in training_trades if t.get("pnl", 0) > 0) / max(len(training_trades), 1)
    recent_wr = sum(1 for t in recent_trades if t.get("pnl", 0) > 0) / max(len(recent_trades), 1)

    # Binomial z-test approximation
    p = train_wr
    n = len(recent_trades)
    if p > 0 and p < 1 and n > 0:
        se = (p * (1 - p) / n) ** 0.5
        z_wr = (train_wr - recent_wr) / max(se, 0.001)
    else:
        z_wr = 0

    alerts.append(DriftAlert(
        metric="win_rate",
        training_value=round(train_wr, 3),
        recent_value=round(recent_wr, 3),
        z_score=round(z_wr, 2),
        is_alert=z_wr > alert_threshold_z,
        message=(
            f"WR drift: train={train_wr:.1%}, recent={recent_wr:.1%}, z={z_wr:.1f}"
            + (" — RE-OPTIMIZE RECOMMENDED" if z_wr > alert_threshold_z else "")
        ),
    ))

    # Mean P&L drift
    train_mean = sum(t.get("pnl", 0) for t in training_trades) / max(len(training_trades), 1)
    recent_mean = sum(t.get("pnl", 0) for t in recent_trades) / max(len(recent_trades), 1)

    alerts.append(DriftAlert(
        metric="mean_pnl",
        training_value=round(train_mean, 4),
        recent_value=round(recent_mean, 4),
        z_score=0.0,  # Simplified — would need bootstrap std
        is_alert=recent_mean < train_mean * 0.5,
        message=f"Mean P&L: train=${train_mean:+.4f}, recent=${recent_mean:+.4f}",
    ))

    return alerts


def format_drift_report(alerts: list[DriftAlert]) -> str:
    """Format drift alerts as readable text."""
    lines = ["PARAMETER DRIFT DETECTION", "=" * 50]
    has_alert = False
    for a in alerts:
        prefix = "ALERT" if a.is_alert else "  OK "
        if a.is_alert:
            has_alert = True
        lines.append(f"  [{prefix}] {a.message}")

    if has_alert:
        lines.append("")
        lines.append("  ACTION: Re-run walk-forward optimization with recent data")
    else:
        lines.append("")
        lines.append("  STATUS: Parameters performing within expected range")

    return "\n".join(lines)
