"""
MOMENTUM-X Gap-and-Go Backtester (D100, D109 analytical extensions)

Simulates the full scan → entry → stop/tranche → exit lifecycle against
historical data from Alpaca. Unlike simulate_trades.py (which replays
specific BUY verdicts), this backtester DISCOVERS gap-up candidates from
historical data and simulates what would have happened with ATR-based stops
and tranche profit-taking.

The goal: answer "what is our EXPECTED win rate and profit factor with the
current scanner thresholds and stop/target parameters?"

D109 Analytical Extensions:
  --null-time           Random entry timing null (does entry TIMING matter?)
  --null-filter         Buy-everything null (does gap RANKING matter?)
  --anti-signal         Anti-signal test (top-ranked vs bottom-ranked candidates)
  (MFE/MAE tracking is always active — shows stop tightness diagnostics)

Usage:
  python scripts/backtest.py                                # Last 90 days
  python scripts/backtest.py --days 30                      # Last 30 days
  python scripts/backtest.py --days 60 --stop-mult 2.5      # Custom ATR mult
  python scripts/backtest.py --days 90 --csv results.csv    # Export CSV
  python scripts/backtest.py --days 90 --sweep              # Parameter sweep
  python scripts/backtest.py --days 30 --null-time           # Entry timing null
  python scripts/backtest.py --days 30 --null-filter         # Filtering null
  python scripts/backtest.py --days 30 --anti-signal         # Signal quality

Output: Win rate, avg winner, avg loser, profit factor, max drawdown, Sharpe.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# ── Project root ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

_env_path = _PROJECT_ROOT / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=False)

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from src.execution.exit_intelligence import compute_atr


# ── Data Types ──────────────────────────────────────────────────────

@dataclass
class GapCandidate:
    """A stock that gapped up on a given date."""
    ticker: str
    date: str           # YYYY-MM-DD
    prev_close: float
    open_price: float
    gap_pct: float
    daily_volume: int
    atr_14d: float | None


@dataclass
class SimulatedTrade:
    """Result of simulating a single trade entry."""
    ticker: str
    date: str
    entry_price: float
    entry_time: str      # HH:MM
    exit_price: float
    exit_time: str
    exit_reason: str     # "stop", "t1", "t2", "t3", "eod"
    pnl_pct: float
    pnl_dollars: float
    stop_price: float
    stop_distance_pct: float
    t1_target: float
    t2_target: float
    t3_target: float
    gap_pct: float
    atr: float | None
    hold_minutes: int
    # D109: MFE/MAE tracking (Exit Autopsy Phase 1)
    mfe_pct: float = 0.0      # Maximum Favorable Excursion (highest unrealized gain %)
    mae_pct: float = 0.0      # Maximum Adverse Excursion (deepest unrealized loss %)
    mfe_bar: int = 0           # Which bar after entry hit MFE (0-indexed)
    stopped_before_mfe: bool = False  # True if stop was hit before peak was reached


# ── Alpaca Data Client ──────────────────────────────────────────────

class BacktestDataClient:
    """Lightweight Alpaca data client for backtesting."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("ALPACA_API_KEY", "")
        self._secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self._base = "https://data.alpaca.markets"
        self._client = httpx.AsyncClient(
            headers={
                "APCA-API-KEY-ID": self._api_key,
                "APCA-API-SECRET-KEY": self._secret,
            },
            timeout=30.0,
        )
        self._cache_dir = _PROJECT_ROOT / "data" / "backtest_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def get_daily_bars(
        self, symbol: str, start: str, end: str, limit: int = 1000,
    ) -> list[dict]:
        """Fetch daily OHLCV bars."""
        url = f"{self._base}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": "1Day", "start": start, "end": end,
            "limit": str(limit), "feed": "sip",
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            return resp.json().get("bars", [])
        except Exception:
            return []

    async def get_minute_bars(
        self, symbol: str, date: str, limit: int = 500,
    ) -> list[dict]:
        """Fetch 1-minute bars for a single trading day (cached)."""
        cache_file = self._cache_dir / f"{symbol}_{date}_1min.json"
        if cache_file.exists():
            import json
            try:
                with open(cache_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError):
                cache_file.unlink(missing_ok=True)  # Remove corrupted cache

        start = f"{date}T09:30:00-04:00"
        end = f"{date}T16:00:00-04:00"
        url = f"{self._base}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": "1Min", "start": start, "end": end,
            "limit": str(limit), "feed": "sip",
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            bars = resp.json().get("bars", [])
            if bars:
                import json
                with open(cache_file, "w") as f:
                    json.dump(bars, f)
            return bars
        except Exception:
            return []

    async def get_most_active_gappers(
        self,
        date: str,
        min_gap_pct: float = 0.05,
        min_price: float = 0.50,
        max_price: float = 20.0,
    ) -> list[dict]:
        """
        Find gap-up stocks on a given date by fetching daily bars
        and comparing open vs previous close.
        """
        url = f"{self._base}/v2/stocks/bars"
        # Fetch bars for the date and previous day
        start_dt = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=5)
        params = {
            "timeframe": "1Day",
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": f"{date}T23:59:59Z",
            "limit": "10000",
            "feed": "sip",
        }
        # Use screener snapshots if available, otherwise use a universe
        return []

    async def close(self) -> None:
        await self._client.aclose()


# ── Scanner Simulation ──────────────────────────────────────────────

# Small-cap momentum universe — most active tickers from our scanner logs
SCAN_UNIVERSE = [
    # This is a seed list. In practice, use the Alpaca "most active" endpoint.
    # For backtesting, we scan snapshots day by day.
]


async def find_gap_candidates(
    client: BacktestDataClient,
    date: str,
    tickers: list[str],
    min_gap_pct: float = 0.05,
    min_price: float = 0.50,
    max_price: float = 20.0,
) -> list[GapCandidate]:
    """
    Scan a list of tickers for gap-ups on a given date.
    Returns candidates matching our scanner thresholds.
    """
    candidates = []

    for ticker in tickers:
        try:
            # Fetch enough daily bars for ATR calculation + gap detection
            start_dt = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30)
            bars = await client.get_daily_bars(
                ticker,
                start=start_dt.strftime("%Y-%m-%d"),
                end=f"{date}T23:59:59Z",
            )

            if len(bars) < 2:
                continue

            # Find the bar for our target date
            target_bar = None
            prev_bar = None
            for i, bar in enumerate(bars):
                bar_date = bar.get("t", "")[:10]
                if bar_date == date:
                    target_bar = bar
                    if i > 0:
                        prev_bar = bars[i - 1]
                    break

            if not target_bar or not prev_bar:
                continue

            prev_close = float(prev_bar.get("c", 0))
            open_price = float(target_bar.get("o", 0))
            volume = int(target_bar.get("v", 0))

            if prev_close <= 0 or open_price <= 0:
                continue

            gap_pct = (open_price - prev_close) / prev_close

            # Apply scanner filters
            if gap_pct < min_gap_pct:
                continue
            if open_price < min_price or open_price > max_price:
                continue
            if volume < 100_000:  # Minimum liquidity
                continue

            # Compute 14-day ATR from daily bars preceding the target date
            preceding_bars = [
                b for b in bars
                if b.get("t", "")[:10] < date
            ]
            atr = compute_atr(preceding_bars, period=14)

            candidates.append(GapCandidate(
                ticker=ticker,
                date=date,
                prev_close=prev_close,
                open_price=open_price,
                gap_pct=gap_pct,
                daily_volume=volume,
                atr_14d=atr,
            ))

        except Exception:
            continue

    # Sort by gap size
    candidates.sort(key=lambda c: c.gap_pct, reverse=True)
    return candidates


# ── Trade Simulation ────────────────────────────────────────────────

async def simulate_trade(
    client: BacktestDataClient,
    candidate: GapCandidate,
    entry_delay_bars: int = 2,
    stop_atr_mult: float = 2.0,
    stop_floor_pct: float = 0.04,
    stop_fallback_pct: float = 0.055,
    t1_pct: float = 0.05,
    t2_pct: float = 0.10,
    t3_pct: float = 0.20,
    position_size: float = 10_000.0,
    use_gap_fallback: bool = True,
) -> SimulatedTrade | None:
    """
    Simulate a single gap-and-go trade using minute bars.

    Entry: at close of bar N (entry_delay_bars after open).
    Stop: ATR-based or D104 gap-aware fallback.
    Targets: T1/T2/T3 as % above entry.
    Exit: stop hit, tranche targets hit, or EOD close.
    """
    bars = await client.get_minute_bars(candidate.ticker, candidate.date)
    if not bars or len(bars) < entry_delay_bars + 1:
        return None

    # Entry at the close of the Nth bar after open
    entry_bar = bars[entry_delay_bars]
    entry_price = float(entry_bar.get("c", 0))
    entry_time = entry_bar.get("t", "")

    if entry_price <= 0:
        return None

    # Compute stop price
    if candidate.atr_14d and candidate.atr_14d > 0:
        stop_distance = max(
            stop_atr_mult * candidate.atr_14d,
            entry_price * stop_floor_pct,
        )
    elif use_gap_fallback:
        # D104: Gap-aware fallback — wider stop for high-gap stocks
        gap_stop_pct = max(stop_fallback_pct, abs(candidate.gap_pct) * 0.5)
        gap_stop_pct = min(gap_stop_pct, 0.15)  # Cap at 15%
        stop_distance = entry_price * gap_stop_pct
    else:
        stop_distance = entry_price * stop_fallback_pct

    stop_price = entry_price - stop_distance

    # Compute targets
    t1 = entry_price * (1 + t1_pct)
    t2 = entry_price * (1 + t2_pct)
    t3 = entry_price * (1 + t3_pct)

    # Position sizing
    qty = int(position_size / entry_price)
    if qty <= 0:
        return None
    tranche_qty = qty // 3

    # Simulate bar-by-bar
    remaining_qty = qty
    realized_pnl = 0.0
    t1_hit = t2_hit = t3_hit = False
    exit_reason = "eod"
    exit_price = entry_price
    exit_time = entry_time
    peak_price = entry_price

    # D109: MFE/MAE tracking
    max_favorable = 0.0       # Best unrealized gain (as price)
    max_adverse = 0.0         # Worst unrealized loss (as price)
    mfe_bar_idx = 0           # Bar index where MFE occurred
    bar_counter = 0

    for bar in bars[entry_delay_bars + 1:]:
        bar_low = float(bar.get("l", 0))
        bar_high = float(bar.get("h", 0))
        bar_close = float(bar.get("c", 0))
        bar_time = bar.get("t", "")

        if bar_low <= 0 or bar_high <= 0:
            continue

        bar_counter += 1

        # Track peak for trailing stop
        peak_price = max(peak_price, bar_high)

        # D109: Track MFE/MAE
        unrealized_high = bar_high - entry_price
        unrealized_low = bar_low - entry_price
        if unrealized_high > max_favorable:
            max_favorable = unrealized_high
            mfe_bar_idx = bar_counter
        if unrealized_low < -max_adverse:
            max_adverse = -unrealized_low  # Store as positive value

        # Check stop-loss (assume worst case: stop hit first)
        if bar_low <= stop_price:
            exit_price = stop_price  # Assume fill at stop
            exit_time = bar_time
            realized_pnl += (exit_price - entry_price) * remaining_qty
            remaining_qty = 0
            exit_reason = "stop"
            break

        # Check T1
        if not t1_hit and bar_high >= t1 and tranche_qty > 0:
            t1_hit = True
            sell_qty = min(tranche_qty, remaining_qty)
            realized_pnl += (t1 - entry_price) * sell_qty
            remaining_qty -= sell_qty
            # Ratchet stop to breakeven after T1
            stop_price = max(stop_price, entry_price)
            if remaining_qty <= 0:
                exit_price = t1
                exit_time = bar_time
                exit_reason = "t1"
                break

        # Check T2
        if not t2_hit and bar_high >= t2 and tranche_qty > 0:
            t2_hit = True
            sell_qty = min(tranche_qty, remaining_qty)
            realized_pnl += (t2 - entry_price) * sell_qty
            remaining_qty -= sell_qty
            # Ratchet stop to T1 after T2
            stop_price = max(stop_price, t1)
            if remaining_qty <= 0:
                exit_price = t2
                exit_time = bar_time
                exit_reason = "t2"
                break

        # Check T3
        if not t3_hit and bar_high >= t3 and tranche_qty > 0:
            t3_hit = True
            sell_qty = min(tranche_qty, remaining_qty)
            realized_pnl += (t3 - entry_price) * sell_qty
            remaining_qty -= sell_qty
            if remaining_qty <= 0:
                exit_price = t3
                exit_time = bar_time
                exit_reason = "t3"
                break

    # EOD close: sell remaining at last bar close
    if remaining_qty > 0:
        last_bar = bars[-1]
        exit_price = float(last_bar.get("c", 0))
        exit_time = last_bar.get("t", "")
        realized_pnl += (exit_price - entry_price) * remaining_qty

    pnl_pct = realized_pnl / (entry_price * qty) * 100 if qty > 0 else 0

    # D109: Compute MFE/MAE as percentages
    mfe_pct = (max_favorable / entry_price * 100) if entry_price > 0 else 0.0
    mae_pct = (max_adverse / entry_price * 100) if entry_price > 0 else 0.0
    # Trade showed profit (MFE > 0) but still got stopped out
    stopped_before_mfe = (exit_reason == "stop" and mfe_pct > 0.5)

    # Parse times for hold duration
    try:
        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        exit_dt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
        hold_minutes = int((exit_dt - entry_dt).total_seconds() / 60)
    except Exception:
        hold_minutes = 0

    return SimulatedTrade(
        ticker=candidate.ticker,
        date=candidate.date,
        entry_price=entry_price,
        entry_time=entry_time[11:16] if len(entry_time) > 16 else entry_time,
        exit_price=exit_price,
        exit_time=exit_time[11:16] if len(exit_time) > 16 else exit_time,
        exit_reason=exit_reason,
        pnl_pct=pnl_pct,
        pnl_dollars=realized_pnl,
        stop_price=stop_price,
        stop_distance_pct=(entry_price - stop_price) / entry_price * 100 if entry_price > 0 else 0,
        t1_target=t1,
        t2_target=t2,
        t3_target=t3,
        gap_pct=candidate.gap_pct * 100,
        atr=candidate.atr_14d,
        hold_minutes=hold_minutes,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        mfe_bar=mfe_bar_idx,
        stopped_before_mfe=stopped_before_mfe,
    )


# ── Backtest Runner ─────────────────────────────────────────────────

async def run_backtest(
    days: int = 90,
    stop_atr_mult: float = 2.0,
    stop_floor_pct: float = 0.04,
    t1_pct: float = 0.05,
    t2_pct: float = 0.10,
    t3_pct: float = 0.20,
    min_gap_pct: float = 0.05,
    entry_delay_bars: int = 2,
    max_trades_per_day: int = 4,
    csv_path: str | None = None,
    verbose: bool = True,
    ticker: str | None = None,
    use_gap_fallback: bool = True,
) -> dict:
    """Run the full backtest across N days of historical data."""
    client = BacktestDataClient()

    # Generate trading days (weekdays only)
    end_date = datetime.now(timezone.utc)
    trading_days: list[str] = []
    current = end_date - timedelta(days=days)
    while current < end_date:
        if current.weekday() < 5:  # Mon-Fri
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    if verbose:
        print(f"\n{'='*70}")
        print(f"  MOMENTUM-X BACKTEST  |  {days} calendar days  |  {len(trading_days)} trading days")
        print(f"  Stop: {stop_atr_mult}x ATR (floor {stop_floor_pct*100:.0f}%)  |  "
              f"Targets: +{t1_pct*100:.0f}%/+{t2_pct*100:.0f}%/+{t3_pct*100:.0f}%  |  "
              f"Min gap: {min_gap_pct*100:.0f}%")
        print(f"{'='*70}\n")

    # For backtesting, we need a universe of tickers to scan.
    # Use the Alpaca "most active" or a pre-defined small-cap list.
    # For now, fetch snapshots to find gappers (more realistic).

    # Strategy: For each day, use the Alpaca snapshots endpoint to find
    # all stocks that gapped up 5%+. This requires fetching snapshot data.
    # Since we can't get historical snapshots, we use daily bars.

    # D104: Single-ticker mode for focused backtest
    if ticker:
        universe = [ticker.upper()]
        if verbose:
            print(f"  Single-ticker mode: {ticker.upper()}")
    else:
        # Build universe from most active small-cap tickers
        try:
            from src.scanners.universe import get_scan_universe
            universe = get_scan_universe()
        except Exception:
            universe = []

        if not universe:
            if verbose:
                print("WARNING: No scan universe found. Using static small-cap list.")
                print("For best results, create src/scanners/universe.py with get_scan_universe().\n")
            # Static fallback: recent tickers from our logs + D104 tickers
            universe = [
                "NVTS", "ACXP", "BRLS", "RITR",  # D103/D104 trade candidates
                "PRSO", "RCAT", "LRHC", "RLMD", "MTEK", "NITO", "DRUG", "HOLO",
                "SVRN", "MCW", "ASNS", "CANF", "ALUR", "VIR", "EDSA", "RIME",
                "LRMR", "IOVA", "SYNX", "CERO", "SMFL", "SONN", "DGLY", "BFRG",
                "APLD", "CORT", "GFAI", "ELEV", "MARA", "RIOT", "BITF", "WULF",
            ]

    all_trades: list[SimulatedTrade] = []
    total_candidates = 0
    days_with_trades = 0

    for i, day in enumerate(trading_days):
        if verbose and i % 10 == 0:
            print(f"  Scanning day {i+1}/{len(trading_days)}: {day}...", end="\r")

        candidates = await find_gap_candidates(
            client, day, universe,
            min_gap_pct=min_gap_pct,
            min_price=0.50,
            max_price=20.0,
        )
        total_candidates += len(candidates)

        if not candidates:
            continue

        # Take top N candidates per day
        day_candidates = candidates[:max_trades_per_day]
        day_trades = []

        for cand in day_candidates:
            trade = await simulate_trade(
                client, cand,
                entry_delay_bars=entry_delay_bars,
                stop_atr_mult=stop_atr_mult,
                stop_floor_pct=stop_floor_pct,
                t1_pct=t1_pct,
                t2_pct=t2_pct,
                t3_pct=t3_pct,
                use_gap_fallback=use_gap_fallback,
            )
            if trade:
                day_trades.append(trade)
                all_trades.append(trade)

        if day_trades:
            days_with_trades += 1

        # Rate limit: avoid hammering Alpaca API
        await asyncio.sleep(0.1)

    await client.close()

    # ── Compute Statistics ──────────────────────────────────────────
    stats = compute_stats(all_trades, total_candidates, len(trading_days), days_with_trades)

    if verbose:
        print_stats(stats, all_trades)

    # Export CSV if requested
    if csv_path and all_trades:
        export_csv(all_trades, csv_path)
        if verbose:
            print(f"\nTrades exported to: {csv_path}")

    # D109 Phase 3: store trades on stats dict for exit autopsy access
    stats["_trades"] = all_trades

    return stats


def compute_stats(
    trades: list[SimulatedTrade],
    total_candidates: int,
    total_days: int,
    days_with_trades: int,
) -> dict:
    """Compute backtest statistics from trade results."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0, "profit_factor": 0.0, "total_pnl": 0.0,
            "max_drawdown_pct": 0.0, "candidates_found": total_candidates,
            "trading_days": total_days, "days_with_trades": days_with_trades,
        }

    winners = [t for t in trades if t.pnl_dollars > 0]
    losers = [t for t in trades if t.pnl_dollars <= 0]
    win_rate = len(winners) / len(trades) * 100 if trades else 0

    avg_win_pct = sum(t.pnl_pct for t in winners) / len(winners) if winners else 0
    avg_loss_pct = sum(t.pnl_pct for t in losers) / len(losers) if losers else 0

    gross_profit = sum(t.pnl_dollars for t in winners)
    gross_loss = abs(sum(t.pnl_dollars for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_pnl = sum(t.pnl_dollars for t in trades)

    # Max drawdown (sequential)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.pnl_dollars
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    # Average hold time
    avg_hold = sum(t.hold_minutes for t in trades) / len(trades) if trades else 0

    # Average stop distance
    avg_stop_dist = sum(t.stop_distance_pct for t in trades) / len(trades) if trades else 0

    # D109: MFE/MAE statistics
    avg_mfe = sum(t.mfe_pct for t in trades) / len(trades) if trades else 0
    avg_mae = sum(t.mae_pct for t in trades) / len(trades) if trades else 0
    mfe_mae_ratio = avg_mfe / avg_mae if avg_mae > 0 else float("inf")
    stopped_before_mfe_count = sum(1 for t in trades if t.stopped_before_mfe)
    stopped_before_mfe_pct = (
        stopped_before_mfe_count / len(trades) * 100 if trades else 0
    )

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "profit_factor": profit_factor,
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "max_drawdown": max_dd,
        "avg_hold_minutes": avg_hold,
        "avg_stop_distance_pct": avg_stop_dist,
        "exit_reasons": exit_reasons,
        "candidates_found": total_candidates,
        "trading_days": total_days,
        "days_with_trades": days_with_trades,
        # D109: MFE/MAE
        "avg_mfe_pct": avg_mfe,
        "avg_mae_pct": avg_mae,
        "mfe_mae_ratio": mfe_mae_ratio,
        "stopped_before_mfe_pct": stopped_before_mfe_pct,
        "stopped_before_mfe_count": stopped_before_mfe_count,
    }


def print_stats(stats: dict, trades: list[SimulatedTrade]) -> None:
    """Print formatted backtest results."""
    print(f"\n{'='*70}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*70}")
    print(f"  Trading days scanned:  {stats['trading_days']}")
    print(f"  Days with trades:      {stats['days_with_trades']}")
    print(f"  Candidates found:      {stats['candidates_found']}")
    print(f"  Trades taken:          {stats['total_trades']}")
    print(f"{'─'*70}")

    if stats["total_trades"] == 0:
        print("  No trades to analyze.")
        return

    print(f"  Win rate:              {stats['win_rate']:.1f}%  "
          f"({stats['winners']}W / {stats['losers']}L)")
    print(f"  Avg winner:            +{stats['avg_win_pct']:.2f}%")
    print(f"  Avg loser:             {stats['avg_loss_pct']:.2f}%")
    print(f"  Profit factor:         {stats['profit_factor']:.2f}")
    print(f"{'─'*70}")
    print(f"  Total P&L:             ${stats['total_pnl']:,.2f}")
    print(f"  Gross profit:          ${stats['gross_profit']:,.2f}")
    print(f"  Gross loss:            -${stats['gross_loss']:,.2f}")
    print(f"  Max drawdown:          -${stats['max_drawdown']:,.2f}")
    print(f"{'─'*70}")
    print(f"  Avg hold time:         {stats['avg_hold_minutes']:.0f} min")
    print(f"  Avg stop distance:     {stats['avg_stop_distance_pct']:.1f}%")
    print(f"{'─'*70}")

    # Exit reason breakdown
    print(f"  Exit reasons:")
    for reason, count in sorted(stats.get("exit_reasons", {}).items()):
        pct = count / stats["total_trades"] * 100
        print(f"    {reason:8s}: {count:4d}  ({pct:.1f}%)")

    # D109: MFE/MAE summary (Exit Autopsy)
    print(f"{'─'*70}")
    print(f"  EXIT AUTOPSY (MFE/MAE):")
    print(f"    Avg MFE:               +{stats.get('avg_mfe_pct', 0):.2f}%  (best unrealized gain)")
    print(f"    Avg MAE:               -{stats.get('avg_mae_pct', 0):.2f}%  (worst unrealized loss)")
    print(f"    MFE/MAE ratio:         {stats.get('mfe_mae_ratio', 0):.2f}")
    print(f"    Stopped w/ prior gain: {stats.get('stopped_before_mfe_count', 0)} "
          f"({stats.get('stopped_before_mfe_pct', 0):.0f}%)")
    if stats.get("stopped_before_mfe_pct", 0) > 50:
        print(f"    ⚠  >50% of stop-outs had prior unrealized profit → stops may be too tight")

    # Top 5 winners and losers
    sorted_trades = sorted(trades, key=lambda t: t.pnl_pct, reverse=True)
    print(f"{'─'*70}")
    print(f"  Top 5 winners:")
    for t in sorted_trades[:5]:
        print(f"    {t.ticker:6s} {t.date} +{t.pnl_pct:.1f}%  "
              f"${t.pnl_dollars:,.0f}  gap={t.gap_pct:.0f}%  exit={t.exit_reason}")
    print(f"  Bottom 5 losers:")
    for t in sorted_trades[-5:]:
        print(f"    {t.ticker:6s} {t.date} {t.pnl_pct:.1f}%  "
              f"${t.pnl_dollars:,.0f}  gap={t.gap_pct:.0f}%  exit={t.exit_reason}")

    print(f"{'='*70}\n")


def export_csv(trades: list[SimulatedTrade], path: str) -> None:
    """Export trades to CSV for analysis."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ticker", "date", "entry_price", "entry_time", "exit_price",
            "exit_time", "exit_reason", "pnl_pct", "pnl_dollars",
            "stop_price", "stop_dist_pct", "t1", "t2", "t3",
            "gap_pct", "atr", "hold_min",
            "mfe_pct", "mae_pct", "mfe_bar", "stopped_before_mfe",
        ])
        for t in trades:
            writer.writerow([
                t.ticker, t.date, f"{t.entry_price:.4f}", t.entry_time,
                f"{t.exit_price:.4f}", t.exit_time, t.exit_reason,
                f"{t.pnl_pct:.2f}", f"{t.pnl_dollars:.2f}",
                f"{t.stop_price:.4f}", f"{t.stop_distance_pct:.1f}",
                f"{t.t1_target:.4f}", f"{t.t2_target:.4f}", f"{t.t3_target:.4f}",
                f"{t.gap_pct:.1f}", f"{t.atr:.4f}" if t.atr else "",
                t.hold_minutes,
                f"{t.mfe_pct:.2f}", f"{t.mae_pct:.2f}", t.mfe_bar,
                "1" if t.stopped_before_mfe else "0",
            ])


# ── Parameter Sweep ─────────────────────────────────────────────────

async def run_sweep(days: int = 60) -> None:
    """Grid search across key parameters."""
    print(f"\n{'='*70}")
    print(f"  PARAMETER SWEEP  |  {days} days")
    print(f"{'='*70}\n")

    configs = [
        {"stop_atr_mult": 1.5, "t1_pct": 0.03, "t2_pct": 0.06, "t3_pct": 0.10},
        {"stop_atr_mult": 2.0, "t1_pct": 0.05, "t2_pct": 0.10, "t3_pct": 0.20},
        {"stop_atr_mult": 2.5, "t1_pct": 0.05, "t2_pct": 0.10, "t3_pct": 0.20},
        {"stop_atr_mult": 2.0, "t1_pct": 0.03, "t2_pct": 0.08, "t3_pct": 0.15},
        {"stop_atr_mult": 3.0, "t1_pct": 0.07, "t2_pct": 0.15, "t3_pct": 0.25},
    ]

    print(f"  {'Config':40s} {'Trades':>7s} {'Win%':>6s} {'PF':>6s} {'P&L':>10s} {'AvgW':>7s} {'AvgL':>7s}")
    print(f"  {'─'*82}")

    for cfg in configs:
        stats = await run_backtest(
            days=days,
            verbose=False,
            **cfg,
        )
        label = (f"stop={cfg['stop_atr_mult']}x "
                 f"T1={cfg['t1_pct']*100:.0f}% "
                 f"T2={cfg['t2_pct']*100:.0f}% "
                 f"T3={cfg['t3_pct']*100:.0f}%")
        print(f"  {label:40s} {stats['total_trades']:7d} "
              f"{stats['win_rate']:5.1f}% "
              f"{stats['profit_factor']:5.2f} "
              f"${stats['total_pnl']:9,.0f} "
              f"+{stats['avg_win_pct']:5.1f}% "
              f"{stats['avg_loss_pct']:6.1f}%")

    print(f"\n{'='*70}\n")


# ── D109: Null Benchmarks & Anti-Signal Analysis ───────────────────


def _compare_groups(
    label_a: str,
    trades_a: list[SimulatedTrade],
    label_b: str,
    trades_b: list[SimulatedTrade],
) -> None:
    """Print a comparison table between two groups of trades."""
    def _group_stats(trades: list[SimulatedTrade]) -> dict:
        if not trades:
            return {"n": 0, "win_pct": 0, "pf": 0, "avg_pnl": 0}
        winners = [t for t in trades if t.pnl_dollars > 0]
        losers = [t for t in trades if t.pnl_dollars <= 0]
        win_pct = len(winners) / len(trades) * 100
        gross_profit = sum(t.pnl_dollars for t in winners)
        gross_loss = abs(sum(t.pnl_dollars for t in losers))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_pnl = sum(t.pnl_dollars for t in trades) / len(trades)
        return {"n": len(trades), "win_pct": win_pct, "pf": pf, "avg_pnl": avg_pnl}

    a = _group_stats(trades_a)
    b = _group_stats(trades_b)

    # t-test on per-trade returns if both groups have sufficient data
    p_value_str = "N/A"
    if len(trades_a) >= 20 and len(trades_b) >= 20:
        try:
            from scipy.stats import ttest_ind
            returns_a = [t.pnl_pct for t in trades_a]
            returns_b = [t.pnl_pct for t in trades_b]
            _, p_val = ttest_ind(returns_a, returns_b, equal_var=False)
            p_value_str = f"{p_val:.4f}" if not math.isnan(p_val) else "N/A"
        except ImportError:
            # Manual Welch's t-test
            n1, n2 = len(trades_a), len(trades_b)
            returns_a = [t.pnl_pct for t in trades_a]
            returns_b = [t.pnl_pct for t in trades_b]
            m1 = sum(returns_a) / n1
            m2 = sum(returns_b) / n2
            v1 = sum((x - m1) ** 2 for x in returns_a) / (n1 - 1) if n1 > 1 else 0
            v2 = sum((x - m2) ** 2 for x in returns_b) / (n2 - 1) if n2 > 1 else 0
            se = math.sqrt(v1 / n1 + v2 / n2) if (v1 / n1 + v2 / n2) > 0 else 1
            t_stat = (m1 - m2) / se
            p_value_str = f"t={t_stat:.2f}"

    print(f"\n{'─'*70}")
    print(f"  {'Group':20s} {'N':>5s} {'Win%':>7s} {'PF':>7s} {'Avg P&L':>10s}")
    print(f"  {'─'*49}")
    print(f"  {label_a:20s} {a['n']:5d} {a['win_pct']:6.1f}% {a['pf']:6.2f} ${a['avg_pnl']:9,.0f}")
    print(f"  {label_b:20s} {b['n']:5d} {b['win_pct']:6.1f}% {b['pf']:6.2f} ${b['avg_pnl']:9,.0f}")
    print(f"  {'─'*49}")
    print(f"  p-value: {p_value_str}")


async def run_null_time_benchmark(
    days: int = 90,
    null_runs: int = 100,
    **kwargs,
) -> None:
    """
    D109: Random entry timing null hypothesis.

    Tests: "Does entry TIMING matter?"
    Randomizes entry bar within the first 30 minutes (bars 0-29) for each
    candidate, runs the same stop/target logic. Compares real entry timing
    vs random entry timing performance.
    """
    print(f"\n{'='*70}")
    print(f"  NULL HYPOTHESIS: RANDOM ENTRY TIMING  |  {null_runs} iterations")
    print(f"{'='*70}")

    # First, run the normal backtest
    print("\n  Running baseline backtest...")
    real_stats = await run_backtest(days=days, verbose=False, **kwargs)

    # Now run N iterations with randomized entry bars
    client = BacktestDataClient()
    end_date = datetime.now(timezone.utc)
    trading_days: list[str] = []
    current = end_date - timedelta(days=days)
    while current < end_date:
        if current.weekday() < 5:
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    entry_delay = kwargs.get("entry_delay_bars", 2)
    stop_atr_mult = kwargs.get("stop_atr_mult", 2.0)
    stop_floor_pct = kwargs.get("stop_floor_pct", 0.04)
    t1_pct = kwargs.get("t1_pct", 0.05)
    t2_pct = kwargs.get("t2_pct", 0.10)
    t3_pct = kwargs.get("t3_pct", 0.20)
    min_gap_pct = kwargs.get("min_gap_pct", 0.05)
    max_per_day = kwargs.get("max_trades_per_day", 4)
    use_gap_fallback = kwargs.get("use_gap_fallback", True)
    ticker = kwargs.get("ticker")

    if ticker:
        universe = [ticker.upper()]
    else:
        try:
            from src.scanners.universe import get_scan_universe
            universe = get_scan_universe()
        except Exception:
            universe = [
                "NVTS", "ACXP", "BRLS", "RITR", "PRSO", "RCAT", "LRHC",
                "RLMD", "MTEK", "NITO", "DRUG", "HOLO", "SVRN", "MCW",
                "ASNS", "CANF", "ALUR", "VIR", "EDSA", "RIME",
            ]

    # Collect candidates for each day (reuse data)
    day_candidates_map: dict[str, list[GapCandidate]] = {}
    for day in trading_days:
        candidates = await find_gap_candidates(
            client, day, universe, min_gap_pct=min_gap_pct,
        )
        if candidates:
            day_candidates_map[day] = candidates[:max_per_day]

    # Run random iterations
    random_all_returns: list[float] = []
    for run_i in range(null_runs):
        if run_i % 10 == 0:
            print(f"  Null iteration {run_i+1}/{null_runs}...", end="\r")

        for day, cands in day_candidates_map.items():
            for cand in cands:
                # Randomize entry bar within first 30 minutes
                random_delay = random.randint(0, 29)
                trade = await simulate_trade(
                    client, cand,
                    entry_delay_bars=random_delay,
                    stop_atr_mult=stop_atr_mult,
                    stop_floor_pct=stop_floor_pct,
                    t1_pct=t1_pct, t2_pct=t2_pct, t3_pct=t3_pct,
                    use_gap_fallback=use_gap_fallback,
                )
                if trade:
                    random_all_returns.append(trade.pnl_pct)

    await client.close()

    # Build comparison
    real_trades_n = real_stats.get("total_trades", 0)
    real_win_pct = real_stats.get("win_rate", 0)
    real_pf = real_stats.get("profit_factor", 0)
    real_avg_pnl = real_stats.get("total_pnl", 0) / real_trades_n if real_trades_n > 0 else 0

    random_n = len(random_all_returns)
    random_wins = sum(1 for r in random_all_returns if r > 0)
    random_win_pct = random_wins / random_n * 100 if random_n > 0 else 0
    random_avg_pnl_pct = sum(random_all_returns) / random_n if random_n > 0 else 0

    print(f"\n\n  RESULTS: Entry Timing Null")
    print(f"  {'─'*50}")
    print(f"  {'Group':20s} {'N':>8s} {'Win%':>7s} {'Avg Return':>12s}")
    print(f"  {'─'*50}")
    print(f"  {'Real (bar '+str(entry_delay)+')':20s} {real_trades_n:8d} {real_win_pct:6.1f}% "
          f"{real_stats.get('avg_win_pct', 0) - abs(real_stats.get('avg_loss_pct', 0)):+11.2f}%")
    print(f"  {'Random (0-29)':20s} {random_n:8d} {random_win_pct:6.1f}% {random_avg_pnl_pct:+11.2f}%")
    print(f"  {'─'*50}")

    if real_win_pct > random_win_pct + 5:
        print(f"  ✓ Entry timing adds value (+{real_win_pct - random_win_pct:.1f}% win rate)")
    elif abs(real_win_pct - random_win_pct) <= 5:
        print(f"  ⚠ Entry timing may not matter (Δ{real_win_pct - random_win_pct:+.1f}% win rate)")
    else:
        print(f"  ✗ Random timing outperforms! Entry timing may hurt ({real_win_pct - random_win_pct:+.1f}%)")

    print(f"{'='*70}\n")


async def run_null_filter_benchmark(
    days: int = 90,
    **kwargs,
) -> None:
    """
    D109: Buy-everything null hypothesis.

    Tests: "Does gap RANKING/FILTERING add value beyond the scanner?"
    Enters ALL scanner candidates (not just top N per day) at the same
    entry time with the same stop/target logic. If unfiltered performance
    matches filtered, ranking adds no value.
    """
    print(f"\n{'='*70}")
    print(f"  NULL HYPOTHESIS: BUY EVERYTHING (no ranking)")
    print(f"{'='*70}")

    max_per_day = kwargs.get("max_trades_per_day", 4)

    # Run filtered (normal) backtest
    print("\n  Running filtered backtest (top %d per day)..." % max_per_day)
    filtered_stats = await run_backtest(days=days, verbose=False, **kwargs)

    # Run unfiltered backtest (take ALL candidates)
    print("  Running unfiltered backtest (ALL candidates)...")
    unfiltered_kwargs = dict(kwargs)
    unfiltered_kwargs["max_trades_per_day"] = 999  # Effectively unlimited
    unfiltered_stats = await run_backtest(days=days, verbose=False, **unfiltered_kwargs)

    # Compare
    print(f"\n  RESULTS: Filtering Null")
    print(f"  {'─'*60}")
    print(f"  {'Group':25s} {'N':>5s} {'Win%':>7s} {'PF':>7s} {'Avg P&L':>10s}")
    print(f"  {'─'*60}")

    def _fmt(label: str, s: dict) -> None:
        n = s.get("total_trades", 0)
        avg = s.get("total_pnl", 0) / n if n > 0 else 0
        print(f"  {label:25s} {n:5d} {s.get('win_rate', 0):6.1f}% "
              f"{s.get('profit_factor', 0):6.2f} ${avg:9,.0f}")

    _fmt(f"Filtered (top {max_per_day}/day)", filtered_stats)
    _fmt("Unfiltered (ALL)", unfiltered_stats)

    print(f"  {'─'*60}")

    fw = filtered_stats.get("win_rate", 0)
    uw = unfiltered_stats.get("win_rate", 0)
    if fw > uw + 3:
        print(f"  ✓ Ranking adds value (+{fw - uw:.1f}% win rate vs unfiltered)")
    elif abs(fw - uw) <= 3:
        print(f"  ⚠ Ranking may not matter (Δ{fw - uw:+.1f}% win rate)")
    else:
        print(f"  ✗ Unfiltered outperforms! Ranking hurts ({fw - uw:+.1f}%)")

    print(f"{'='*70}\n")


async def run_anti_signal_analysis(
    days: int = 90,
    **kwargs,
) -> None:
    """
    D109: Anti-signal test (signal quality analysis).

    Tests: "Does gap SIZE ranking have discriminative power?"
    Partitions all scanner candidates into:
      - Group A: Top-ranked (largest gaps) — what we actually trade
      - Group B: Bottom-ranked (smallest gaps above threshold) — what we reject
    Both groups run long-only with identical stop/target logic.
    If rejected candidates perform as well, ranking has no signal.
    """
    print(f"\n{'='*70}")
    print(f"  ANTI-SIGNAL TEST: TOP-RANKED vs BOTTOM-RANKED")
    print(f"{'='*70}")

    max_per_day = kwargs.get("max_trades_per_day", 4)

    client = BacktestDataClient()
    end_date = datetime.now(timezone.utc)
    trading_days: list[str] = []
    current = end_date - timedelta(days=days)
    while current < end_date:
        if current.weekday() < 5:
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    min_gap_pct = kwargs.get("min_gap_pct", 0.05)
    entry_delay = kwargs.get("entry_delay_bars", 2)
    stop_atr_mult = kwargs.get("stop_atr_mult", 2.0)
    stop_floor_pct = kwargs.get("stop_floor_pct", 0.04)
    t1_pct = kwargs.get("t1_pct", 0.05)
    t2_pct = kwargs.get("t2_pct", 0.10)
    t3_pct = kwargs.get("t3_pct", 0.20)
    use_gap_fallback = kwargs.get("use_gap_fallback", True)
    ticker = kwargs.get("ticker")

    if ticker:
        universe = [ticker.upper()]
    else:
        try:
            from src.scanners.universe import get_scan_universe
            universe = get_scan_universe()
        except Exception:
            universe = [
                "NVTS", "ACXP", "BRLS", "RITR", "PRSO", "RCAT", "LRHC",
                "RLMD", "MTEK", "NITO", "DRUG", "HOLO", "SVRN", "MCW",
                "ASNS", "CANF", "ALUR", "VIR", "EDSA", "RIME",
            ]

    top_trades: list[SimulatedTrade] = []
    bottom_trades: list[SimulatedTrade] = []

    for i, day in enumerate(trading_days):
        if i % 10 == 0:
            print(f"  Scanning day {i+1}/{len(trading_days)}...", end="\r")

        candidates = await find_gap_candidates(
            client, day, universe, min_gap_pct=min_gap_pct,
        )

        if len(candidates) < 2:
            continue

        # Partition: top N vs remainder
        top_group = candidates[:max_per_day]
        bottom_group = candidates[max_per_day:]

        # Simulate both groups
        sim_kwargs = dict(
            entry_delay_bars=entry_delay,
            stop_atr_mult=stop_atr_mult,
            stop_floor_pct=stop_floor_pct,
            t1_pct=t1_pct, t2_pct=t2_pct, t3_pct=t3_pct,
            use_gap_fallback=use_gap_fallback,
        )

        for cand in top_group:
            trade = await simulate_trade(client, cand, **sim_kwargs)
            if trade:
                top_trades.append(trade)

        for cand in bottom_group:
            trade = await simulate_trade(client, cand, **sim_kwargs)
            if trade:
                bottom_trades.append(trade)

        await asyncio.sleep(0.1)

    await client.close()

    print(f"\n")
    _compare_groups(
        f"Top {max_per_day} (accepted)", top_trades,
        "Remainder (rejected)", bottom_trades,
    )

    # Interpretation
    if top_trades and bottom_trades:
        top_wr = sum(1 for t in top_trades if t.pnl_dollars > 0) / len(top_trades) * 100
        bot_wr = sum(1 for t in bottom_trades if t.pnl_dollars > 0) / len(bottom_trades) * 100
        if top_wr > bot_wr + 5:
            print(f"\n  ✓ Gap ranking has discriminative power (+{top_wr - bot_wr:.1f}% vs rejected)")
        elif abs(top_wr - bot_wr) <= 5:
            print(f"\n  ⚠ Gap ranking may lack discriminative power (Δ{top_wr - bot_wr:+.1f}%)")
        else:
            print(f"\n  ✗ Rejected candidates outperform! Signal may be inverted ({top_wr - bot_wr:+.1f}%)")

    print(f"{'='*70}\n")


# ── D109 Phase 3: Full Exit Autopsy Engine ────────────────────────


def run_exit_autopsy(trades: list[SimulatedTrade]) -> dict:
    """
    D109 Phase 3: Full Exit Autopsy — comprehensive exit quality analysis.

    Goes beyond basic MFE/MAE to answer:
      1. "Are stops too tight?" — % of trades stopped out before reaching MFE
      2. "What is the optimal stop distance?" — finds stop distance that maximizes PF
      3. "Where is money left on table?" — gap between MFE and actual exit
      4. "When do stops fire?" — time-of-day and hold-duration breakdown
      5. "Exit reason effectiveness" — P&L by exit_reason category

    Args:
        trades: List of SimulatedTrade results with MFE/MAE populated.

    Returns:
        Dict of autopsy metrics.
    """
    if not trades:
        return {}

    autopsy: dict = {}

    # ── 1. Stop Tightness Analysis ─────────────────────────────────
    stopped = [t for t in trades if t.exit_reason == "stop"]
    non_stopped = [t for t in trades if t.exit_reason != "stop"]
    stopped_with_prior_gain = [t for t in stopped if t.mfe_pct > 0]
    stopped_high_mfe = [t for t in stopped if t.mfe_pct > 2.0]

    autopsy["stop_tightness"] = {
        "total_trades": len(trades),
        "stop_outs": len(stopped),
        "stop_out_pct": len(stopped) / len(trades) * 100 if trades else 0,
        "stopped_with_prior_gain": len(stopped_with_prior_gain),
        "stopped_with_prior_gain_pct": (
            len(stopped_with_prior_gain) / len(stopped) * 100 if stopped else 0
        ),
        "stopped_high_mfe": len(stopped_high_mfe),
        "avg_mfe_before_stop": (
            sum(t.mfe_pct for t in stopped) / len(stopped) if stopped else 0
        ),
        "avg_mae_at_stop": (
            sum(t.mae_pct for t in stopped) / len(stopped) if stopped else 0
        ),
        "avg_stop_distance_pct": (
            sum(t.stop_distance_pct for t in stopped) / len(stopped) if stopped else 0
        ),
    }

    # ── 2. Money Left on Table ─────────────────────────────────────
    mfe_vs_exit = []
    for t in trades:
        if t.mfe_pct > 0:
            # How much of the MFE did we actually capture?
            capture_ratio = t.pnl_pct / t.mfe_pct if t.mfe_pct > 0 else 0
            mfe_vs_exit.append({
                "ticker": t.ticker,
                "mfe_pct": t.mfe_pct,
                "actual_pnl_pct": t.pnl_pct,
                "left_on_table_pct": t.mfe_pct - t.pnl_pct,
                "capture_ratio": capture_ratio,
                "exit_reason": t.exit_reason,
            })

    if mfe_vs_exit:
        avg_capture = sum(m["capture_ratio"] for m in mfe_vs_exit) / len(mfe_vs_exit)
        avg_left = sum(m["left_on_table_pct"] for m in mfe_vs_exit) / len(mfe_vs_exit)
    else:
        avg_capture = 0
        avg_left = 0

    autopsy["money_left_on_table"] = {
        "avg_capture_ratio": avg_capture,
        "avg_left_on_table_pct": avg_left,
        "trades_with_positive_mfe": len(mfe_vs_exit),
    }

    # ── 3. Exit Reason Effectiveness ──────────────────────────────
    exit_breakdown: dict[str, list[SimulatedTrade]] = {}
    for t in trades:
        exit_breakdown.setdefault(t.exit_reason, []).append(t)

    exit_effectiveness: dict[str, dict] = {}
    for reason, group in sorted(exit_breakdown.items()):
        wins = [t for t in group if t.pnl_dollars > 0]
        gross_profit = sum(t.pnl_dollars for t in wins)
        gross_loss = abs(sum(t.pnl_dollars for t in group if t.pnl_dollars <= 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        exit_effectiveness[reason] = {
            "count": len(group),
            "pct_of_trades": len(group) / len(trades) * 100,
            "win_rate": len(wins) / len(group) * 100 if group else 0,
            "profit_factor": pf,
            "avg_pnl_pct": sum(t.pnl_pct for t in group) / len(group),
            "total_pnl": sum(t.pnl_dollars for t in group),
            "avg_hold_min": sum(t.hold_minutes for t in group) / len(group),
            "avg_mfe_pct": sum(t.mfe_pct for t in group) / len(group),
            "avg_mae_pct": sum(t.mae_pct for t in group) / len(group),
        }

    autopsy["exit_effectiveness"] = exit_effectiveness

    # ── 4. Hold Duration Analysis ──────────────────────────────────
    time_buckets = {
        "0-5 min": (0, 5),
        "5-15 min": (5, 15),
        "15-30 min": (15, 30),
        "30-60 min": (30, 60),
        "60+ min": (60, 999),
    }
    hold_analysis: dict[str, dict] = {}
    for bucket_name, (lo, hi) in time_buckets.items():
        bucket_trades = [t for t in trades if lo <= t.hold_minutes < hi]
        if bucket_trades:
            wins = [t for t in bucket_trades if t.pnl_dollars > 0]
            hold_analysis[bucket_name] = {
                "count": len(bucket_trades),
                "win_rate": len(wins) / len(bucket_trades) * 100,
                "avg_pnl_pct": sum(t.pnl_pct for t in bucket_trades) / len(bucket_trades),
                "avg_mfe_pct": sum(t.mfe_pct for t in bucket_trades) / len(bucket_trades),
            }
        else:
            hold_analysis[bucket_name] = {"count": 0, "win_rate": 0, "avg_pnl_pct": 0, "avg_mfe_pct": 0}

    autopsy["hold_duration_analysis"] = hold_analysis

    # ── 5. MFE Bar Analysis (when does the peak happen?) ──────────
    mfe_bars = [t.mfe_bar for t in trades if t.mfe_pct > 0]
    if mfe_bars:
        avg_mfe_bar = sum(mfe_bars) / len(mfe_bars)
        mfe_within_5 = sum(1 for b in mfe_bars if b <= 5)
        mfe_within_15 = sum(1 for b in mfe_bars if b <= 15)
        mfe_within_30 = sum(1 for b in mfe_bars if b <= 30)
    else:
        avg_mfe_bar = 0
        mfe_within_5 = mfe_within_15 = mfe_within_30 = 0

    autopsy["mfe_timing"] = {
        "avg_mfe_bar": avg_mfe_bar,
        "mfe_within_5_bars": mfe_within_5,
        "mfe_within_15_bars": mfe_within_15,
        "mfe_within_30_bars": mfe_within_30,
        "total_with_positive_mfe": len(mfe_bars),
    }

    # ── 6. Optimal Stop Finder ─────────────────────────────────────
    # Try different stop distances and find which maximizes profit factor
    best_pf = 0
    best_stop = 0
    stop_sweep_results = []
    for stop_pct in [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0]:
        # Simulate: would each trade have been stopped with this wider/tighter stop?
        sim_wins = 0
        sim_gross_profit = 0.0
        sim_gross_loss = 0.0
        sim_count = 0
        for t in trades:
            sim_count += 1
            if t.mae_pct >= stop_pct:
                # Would have been stopped at this distance
                sim_gross_loss += stop_pct  # Approximate loss
            else:
                # Would have survived to original exit
                if t.pnl_pct > 0:
                    sim_wins += 1
                    sim_gross_profit += t.pnl_pct
                else:
                    sim_gross_loss += abs(t.pnl_pct)

        sim_pf = sim_gross_profit / sim_gross_loss if sim_gross_loss > 0 else float("inf")
        sim_wr = sim_wins / sim_count * 100 if sim_count > 0 else 0
        stop_sweep_results.append({
            "stop_pct": stop_pct,
            "simulated_pf": sim_pf,
            "simulated_wr": sim_wr,
            "would_stop_count": sum(1 for t in trades if t.mae_pct >= stop_pct),
        })
        if sim_pf > best_pf and sim_pf != float("inf"):
            best_pf = sim_pf
            best_stop = stop_pct

    # If no finite PF was found (all stops either stop everything or nothing),
    # pick the widest stop as "best" since wider = fewer stop-outs
    if best_stop == 0 and stop_sweep_results:
        best_stop = stop_sweep_results[-1]["stop_pct"]
        best_pf = stop_sweep_results[-1]["simulated_pf"]

    autopsy["optimal_stop"] = {
        "best_stop_pct": best_stop,
        "best_profit_factor": best_pf,
        "sweep_results": stop_sweep_results,
    }

    return autopsy


def print_exit_autopsy(trades: list[SimulatedTrade]) -> None:
    """Print formatted Exit Autopsy report."""
    autopsy = run_exit_autopsy(trades)
    if not autopsy:
        print("  No trades to analyze.")
        return

    print(f"\n{'='*70}")
    print(f"  EXIT AUTOPSY — Full Trade Exit Analysis (D109 Phase 3)")
    print(f"{'='*70}")

    # 1. Stop Tightness
    st = autopsy["stop_tightness"]
    print(f"\n  ── STOP TIGHTNESS ──")
    print(f"    Total trades:          {st['total_trades']}")
    print(f"    Stop-outs:             {st['stop_outs']} ({st['stop_out_pct']:.0f}%)")
    print(f"    Stopped w/ prior gain: {st['stopped_with_prior_gain']} "
          f"({st['stopped_with_prior_gain_pct']:.0f}% of stop-outs)")
    print(f"    Stopped w/ MFE > 2%:   {st['stopped_high_mfe']}")
    print(f"    Avg MFE before stop:   {st['avg_mfe_before_stop']:.2f}%")
    print(f"    Avg MAE at stop:       {st['avg_mae_at_stop']:.2f}%")
    print(f"    Avg stop distance:     {st['avg_stop_distance_pct']:.1f}%")
    if st['stopped_with_prior_gain_pct'] > 60:
        print(f"    ⚠  HIGH: {st['stopped_with_prior_gain_pct']:.0f}% of stop-outs had prior "
              f"unrealized gain → STOPS ARE TOO TIGHT")
    elif st['stopped_with_prior_gain_pct'] > 40:
        print(f"    ⚠  MODERATE: {st['stopped_with_prior_gain_pct']:.0f}% of stop-outs had "
              f"prior unrealized gain → consider widening stops")

    # 2. Money Left on Table
    mlt = autopsy["money_left_on_table"]
    print(f"\n  ── MONEY LEFT ON TABLE ──")
    print(f"    Avg capture ratio:     {mlt['avg_capture_ratio']:.1%}")
    print(f"    Avg left on table:     {mlt['avg_left_on_table_pct']:.2f}%")
    print(f"    Trades w/ positive MFE:{mlt['trades_with_positive_mfe']}")
    if mlt['avg_capture_ratio'] < 0.3:
        print(f"    ⚠  Capturing only {mlt['avg_capture_ratio']:.0%} of available "
              f"favorable excursion — exits too early or stops too tight")

    # 3. Exit Effectiveness
    ee = autopsy["exit_effectiveness"]
    print(f"\n  ── EXIT REASON EFFECTIVENESS ──")
    print(f"    {'Reason':10s} {'Count':>6s} {'%Trades':>8s} {'Win%':>6s} "
          f"{'PF':>6s} {'AvgPnL':>8s} {'TotalP&L':>10s} {'AvgMFE':>7s}")
    print(f"    {'─'*72}")
    for reason, data in ee.items():
        pf_str = f"{data['profit_factor']:.2f}" if data['profit_factor'] != float("inf") else "∞"
        print(f"    {reason:10s} {data['count']:6d} {data['pct_of_trades']:7.0f}% "
              f"{data['win_rate']:5.0f}% {pf_str:>6s} "
              f"{data['avg_pnl_pct']:+7.2f}% ${data['total_pnl']:9,.0f} "
              f"{data['avg_mfe_pct']:6.2f}%")

    # 4. Hold Duration
    hd = autopsy["hold_duration_analysis"]
    print(f"\n  ── HOLD DURATION BREAKDOWN ──")
    print(f"    {'Duration':12s} {'Count':>6s} {'Win%':>6s} {'AvgPnL':>8s} {'AvgMFE':>7s}")
    print(f"    {'─'*42}")
    for bucket, data in hd.items():
        if data["count"] > 0:
            print(f"    {bucket:12s} {data['count']:6d} {data['win_rate']:5.0f}% "
                  f"{data['avg_pnl_pct']:+7.2f}% {data['avg_mfe_pct']:6.2f}%")

    # 5. MFE Timing
    mt = autopsy["mfe_timing"]
    print(f"\n  ── MFE TIMING (when does peak occur?) ──")
    print(f"    Avg MFE bar:           {mt['avg_mfe_bar']:.1f} (bar after entry)")
    total_mfe = mt["total_with_positive_mfe"]
    if total_mfe > 0:
        print(f"    MFE within 5 bars:     {mt['mfe_within_5_bars']} "
              f"({mt['mfe_within_5_bars']/total_mfe*100:.0f}%)")
        print(f"    MFE within 15 bars:    {mt['mfe_within_15_bars']} "
              f"({mt['mfe_within_15_bars']/total_mfe*100:.0f}%)")
        print(f"    MFE within 30 bars:    {mt['mfe_within_30_bars']} "
              f"({mt['mfe_within_30_bars']/total_mfe*100:.0f}%)")

    # 6. Optimal Stop
    os_data = autopsy["optimal_stop"]
    print(f"\n  ── OPTIMAL STOP FINDER ──")
    print(f"    {'Stop%':>6s} {'PF':>6s} {'Win%':>6s} {'#Stopped':>9s}")
    print(f"    {'─'*30}")
    for r in os_data["sweep_results"]:
        pf_str = f"{r['simulated_pf']:.2f}" if r['simulated_pf'] != float("inf") else "∞"
        marker = " ←" if r["stop_pct"] == os_data["best_stop_pct"] else ""
        print(f"    {r['stop_pct']:5.0f}% {pf_str:>6s} {r['simulated_wr']:5.0f}% "
              f"{r['would_stop_count']:9d}{marker}")
    print(f"\n    Best stop distance: {os_data['best_stop_pct']:.0f}% "
          f"(PF={os_data['best_profit_factor']:.2f})")

    print(f"\n{'='*70}\n")


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MOMENTUM-X Gap-and-Go Backtester")
    parser.add_argument("--days", type=int, default=90, help="Lookback period in calendar days")
    parser.add_argument("--stop-mult", type=float, default=2.0, help="ATR multiplier for stop")
    parser.add_argument("--stop-floor", type=float, default=0.04, help="Minimum stop as %% of entry")
    parser.add_argument("--t1", type=float, default=0.05, help="T1 target as %% above entry")
    parser.add_argument("--t2", type=float, default=0.10, help="T2 target as %% above entry")
    parser.add_argument("--t3", type=float, default=0.20, help="T3 target as %% above entry")
    parser.add_argument("--min-gap", type=float, default=0.05, help="Minimum gap %% (0.05 = 5%%)")
    parser.add_argument("--entry-delay", type=int, default=2, help="Bars after open before entry")
    parser.add_argument("--max-per-day", type=int, default=4, help="Max trades per day")
    parser.add_argument("--csv", type=str, default=None, help="Export trades to CSV file")
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep")
    parser.add_argument("--ticker", type=str, default=None, help="Single ticker to backtest (e.g., NVTS)")
    parser.add_argument("--no-gap-fallback", action="store_true",
                        help="Disable D104 gap-based fallback (use old fixed 5.5%%)")
    # D109: Analytical extensions
    parser.add_argument("--null-time", action="store_true",
                        help="D109: Run random entry timing null benchmark")
    parser.add_argument("--null-runs", type=int, default=100,
                        help="Number of random iterations for --null-time (default: 100)")
    parser.add_argument("--null-filter", action="store_true",
                        help="D109: Run buy-everything null benchmark (no ranking)")
    parser.add_argument("--anti-signal", action="store_true",
                        help="D109: Anti-signal test (top-ranked vs bottom-ranked)")
    parser.add_argument("--exit-autopsy", action="store_true",
                        help="D109: Full Exit Autopsy (stop tightness, optimal stop, exit effectiveness)")
    args = parser.parse_args()

    common_kwargs = dict(
        stop_atr_mult=args.stop_mult,
        stop_floor_pct=args.stop_floor,
        t1_pct=args.t1,
        t2_pct=args.t2,
        t3_pct=args.t3,
        min_gap_pct=args.min_gap,
        entry_delay_bars=args.entry_delay,
        max_trades_per_day=args.max_per_day,
        ticker=args.ticker,
        use_gap_fallback=not args.no_gap_fallback,
    )

    if args.sweep:
        asyncio.run(run_sweep(days=args.days))
    elif args.null_time:
        asyncio.run(run_null_time_benchmark(
            days=args.days, null_runs=args.null_runs, **common_kwargs,
        ))
    elif args.null_filter:
        asyncio.run(run_null_filter_benchmark(days=args.days, **common_kwargs))
    elif args.anti_signal:
        asyncio.run(run_anti_signal_analysis(days=args.days, **common_kwargs))
    elif args.exit_autopsy:
        stats = asyncio.run(run_backtest(
            days=args.days,
            csv_path=args.csv,
            **common_kwargs,
        ))
        trades = stats.get("_trades", [])
        if trades:
            print_exit_autopsy(trades)
        else:
            print("  No trades to analyze for Exit Autopsy.")
    else:
        asyncio.run(run_backtest(
            days=args.days,
            csv_path=args.csv,
            **common_kwargs,
        ))


if __name__ == "__main__":
    main()
