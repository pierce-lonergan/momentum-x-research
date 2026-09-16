"""
Synthetic Candidate Generator — Create candidates from bar data alone.

Gap 4: The arena only has journal data for 17 of 75 dates (49 trades).
Walk-forward needs 200+ trades. This module generates CandidateContext
objects from Parquet bar data without requiring journal entries.

For each ticker-date: compute gap from previous close to open, compute
RVOL from volume surge, construct candidate with bar-derived metrics.
This multiplies the trade universe from 49 to 500+.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .decision_replay import CandidateContext
from .fill_model import Bar

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def generate_candidates_from_bars(
    date: str,
    historical_dir: str | Path,
    daily_dir: str | Path | None = None,
    min_gap_pct: float = 0.08,
    min_volume: int = 100_000,
) -> list[CandidateContext]:
    """
    Generate synthetic CandidateContext objects from bar data.

    For each ticker with Parquet data on this date:
    1. Load the daily bar to get previous close
    2. Load minute bars to get opening price and volume
    3. Compute gap_pct = (open - prev_close) / prev_close
    4. Compute RVOL from first-hour volume vs avg daily volume
    5. If gap > min_gap_pct, create a CandidateContext

    Returns candidates sorted by gap_pct * rvol (highest momentum first).
    """
    hist_dir = Path(historical_dir)
    daily = Path(daily_dir) if daily_dir else hist_dir.parent / "daily"
    candidates = []

    # Find all tickers with data for this date
    for ticker_dir in sorted(hist_dir.iterdir()):
        if not ticker_dir.is_dir():
            continue
        symbol = ticker_dir.name
        if symbol.startswith("_") or symbol.startswith("."):
            continue

        parquet_file = ticker_dir / f"{date}.parquet"
        if not parquet_file.exists():
            continue

        # Load minute bars
        if not HAS_PANDAS:
            continue
        try:
            df = pd.read_parquet(parquet_file)
        except Exception:
            continue

        if len(df) < 10:
            continue

        # Get opening price and volume
        ts_col = "timestamp" if "timestamp" in df.columns else "t"
        if ts_col in df.columns:
            df = df.sort_values(ts_col)

        first_bar_open = float(df.iloc[0].get("o", df.iloc[0].get("open", 0)))
        total_volume = int(df["v"].sum() if "v" in df.columns else df.get("volume", pd.Series([0])).sum())

        if first_bar_open <= 0 or total_volume < min_volume:
            continue

        # Get previous close from daily bars
        prev_close = _get_prev_close(daily, symbol, date)
        if prev_close <= 0:
            # Try to infer from minute bars (first bar's open of previous day)
            continue

        # Compute gap
        gap_pct = (first_bar_open - prev_close) / prev_close

        if abs(gap_pct) < min_gap_pct:
            continue

        # Compute approximate RVOL (first hour volume / avg daily volume * (bars/390))
        n_bars = len(df)
        first_hour_bars = min(60, n_bars)
        first_hour_vol = int(df.iloc[:first_hour_bars]["v"].sum() if "v" in df.columns else 0)
        avg_daily_vol = max(total_volume, 1)
        rvol = (first_hour_vol / (avg_daily_vol * (first_hour_bars / max(n_bars, 1)))) if avg_daily_vol > 0 else 1.0

        # Estimate entry price (first bar close + small slippage)
        entry_price = first_bar_open
        stop_loss = entry_price * 0.96  # Default 4% stop

        candidates.append(CandidateContext(
            ticker=symbol,
            current_price=entry_price,
            previous_close=prev_close,
            gap_pct=gap_pct,
            rvol=rvol,
            premarket_volume=first_hour_vol,
            has_news_catalyst=False,
            entry_price=entry_price,
            stop_loss=stop_loss,
            journal_signals=[],  # No journal data
            journal_mfcs=0.0,
            journal_action="SYNTHETIC",
            journal_component_scores={},
            journal_risk_score=0.3,
        ))

    # Sort by momentum score (highest first)
    candidates.sort(key=lambda c: abs(c.gap_pct) * c.rvol, reverse=True)
    logger.info(
        "Generated %d synthetic candidates for %s (from %d tickers with data)",
        len(candidates), date, sum(1 for _ in hist_dir.iterdir() if _.is_dir()),
    )
    return candidates


def _get_prev_close(daily_dir: Path, symbol: str, date: str) -> float:
    """Get previous trading day's close price from daily bars."""
    if not HAS_PANDAS:
        return 0.0

    parquet = daily_dir / f"{symbol}.parquet"
    if parquet.exists():
        try:
            df = pd.read_parquet(parquet)
            ts_col = "timestamp" if "timestamp" in df.columns else "t"
            if ts_col in df.columns:
                df[ts_col] = df[ts_col].astype(str)
                df["_date"] = df[ts_col].str[:10]
                # Find the bar BEFORE this date
                prior = df[df["_date"] < date].sort_values(ts_col)
                if len(prior) > 0:
                    return float(prior.iloc[-1].get("c", prior.iloc[-1].get("close", 0)))
        except Exception:
            pass
    return 0.0


def generate_all_dates(
    historical_dir: str | Path,
    daily_dir: str | Path | None = None,
    min_gap_pct: float = 0.08,
    min_volume: int = 100_000,
) -> dict[str, list[CandidateContext]]:
    """
    Generate synthetic candidates for ALL dates in the historical data.

    Returns {date: [candidates]} for every date found.
    """
    hist_dir = Path(historical_dir)
    all_dates = set()

    # Discover all dates from Parquet files
    for ticker_dir in hist_dir.iterdir():
        if not ticker_dir.is_dir():
            continue
        for f in ticker_dir.glob("*.parquet"):
            date = f.stem  # YYYY-MM-DD
            if len(date) == 10:
                all_dates.add(date)

    results = {}
    for date in sorted(all_dates):
        candidates = generate_candidates_from_bars(
            date, historical_dir, daily_dir, min_gap_pct, min_volume,
        )
        if candidates:
            results[date] = candidates

    total = sum(len(v) for v in results.values())
    logger.info(
        "Generated %d total synthetic candidates across %d dates",
        total, len(results),
    )
    return results
