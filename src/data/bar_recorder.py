"""D196: Minute-bar recording and replay for arena realism.

Records actual 1-minute OHLCV bars for every evaluated stock during live sessions.
The meta simulation replays the actual price path minute-by-minute through
D163/D164/D165/D170 instead of using summary statistics.

Key insight: simulate_trailing_stop(), simulate_early_profit(), simulate_tranches(),
and simulate_observation_window() replay the ACTUAL bar-by-bar price path through
our exit logic, giving far more accurate P&L than summary statistics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Market session constants ───────────────────────────────────────────────────
_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 30


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class MinuteBar:
    """One 1-minute OHLCV bar."""

    timestamp: str          # ISO-8601 UTC string for JSON serialisation
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float = 0.0

    @classmethod
    def from_alpaca(cls, raw: dict) -> "MinuteBar":
        """Construct from Alpaca bar dict {t, o, h, l, c, v, vw}."""
        return cls(
            timestamp=raw["t"],
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=int(raw.get("v", 0)),
            vwap=float(raw.get("vw", 0.0)),
        )


@dataclass
class BarSeries:
    """Complete 1-minute bar series for one ticker on one date."""

    ticker: str
    date: str               # YYYY-MM-DD
    bars: list[MinuteBar]

    # ── Derived analytics ─────────────────────────────────────────────────────

    def max_gain_from_open(self) -> float:
        """Actual max gain from first bar's open, as fraction (0.05 = 5%)."""
        if not self.bars:
            return 0.0
        entry = self.bars[0].open
        if entry <= 0:
            return 0.0
        return max((b.high - entry) / entry for b in self.bars)

    def max_drawdown_from_open(self) -> float:
        """Actual max drawdown from first bar's open, as fraction (positive = down)."""
        if not self.bars:
            return 0.0
        entry = self.bars[0].open
        if entry <= 0:
            return 0.0
        return max((entry - b.low) / entry for b in self.bars)

    def price_at_minute(self, minutes_after_open: int) -> float:
        """Get close price at specific minute offset from market open.

        Returns the last available close if the index is beyond the series.
        """
        if not self.bars:
            return 0.0
        idx = max(0, min(minutes_after_open, len(self.bars) - 1))
        return self.bars[idx].close

    # ── Exit logic replayers ──────────────────────────────────────────────────

    def simulate_trailing_stop(
        self,
        entry_price: float,
        activation_pct: float = 0.02,
        trail_pct_of_gain: float = 0.50,
        max_trail_pct: float = 0.35,
        min_trail_pct: float = 0.02,
    ) -> tuple[float, int]:
        """Replay D163 trailing stop logic bar-by-bar.

        Args:
            entry_price: Trade entry price.
            activation_pct: Trail activates when gain >= this (0.02 = 2%).
            trail_pct_of_gain: Trail level = peak - trail_pct_of_gain * (peak - entry).
            max_trail_pct: Trail never wider than this below current price.
            min_trail_pct: Trail never tighter than this below current price.

        Returns:
            (exit_price, exit_minute) — exit_minute=-1 if stop never fires.
        """
        if not self.bars or entry_price <= 0:
            return entry_price, -1

        peak_price = entry_price
        trail_level: Optional[float] = None
        activated = False

        for minute, bar in enumerate(self.bars):
            # Update peak on each bar's high
            if bar.high > peak_price:
                peak_price = bar.high

            # Check activation
            gain_pct = (peak_price - entry_price) / entry_price
            if gain_pct >= activation_pct:
                activated = True

            if activated:
                # Compute ideal trail: peak - trail_pct_of_gain * (peak - entry)
                ideal_trail = peak_price - trail_pct_of_gain * (peak_price - entry_price)
                # Constrain to min/max band around current close
                current = bar.close
                floor = current * (1.0 - max_trail_pct)
                ceiling = current * (1.0 - min_trail_pct)
                computed = max(floor, min(ceiling, ideal_trail))

                # Ratchet: trail only moves up
                if trail_level is None:
                    trail_level = computed
                else:
                    trail_level = max(trail_level, computed)

                # Check stop: does bar's low breach the trail?
                if bar.low <= trail_level:
                    # Fill at trail level (assume it fills cleanly within the bar)
                    exit_price = max(bar.low, trail_level)
                    return exit_price, minute

        # Trail never fired — return last close
        return self.bars[-1].close if self.bars else entry_price, -1

    def simulate_early_profit(
        self,
        entry_price: float,
        delay_minutes: int = 2,
        exit_pct: float = 0.50,
        min_profit: float = 0.005,
    ) -> tuple[float, int, int]:
        """Replay D164 early profit-take logic bar-by-bar.

        Sells `exit_pct` of position at `min_profit` threshold, if reachable
        within the first `delay_minutes` bars.

        Args:
            entry_price: Trade entry price.
            delay_minutes: Window to check for early profit (bars).
            exit_pct: Fraction of position sold early (0.50 = 50%).
            min_profit: Minimum gain to trigger early exit (0.005 = 0.5%).

        Returns:
            (exit_price, qty_sold_pct_int, exit_minute)
            qty_sold_pct_int = 50 if triggered, 0 otherwise.
            exit_minute = -1 if not triggered.
        """
        if not self.bars or entry_price <= 0:
            return entry_price, 0, -1

        target = entry_price * (1.0 + min_profit)
        window = self.bars[:delay_minutes] if delay_minutes < len(self.bars) else self.bars

        for minute, bar in enumerate(window):
            if bar.high >= target:
                sold_pct = int(exit_pct * 100)
                return target, sold_pct, minute

        return entry_price, 0, -1

    def simulate_tranches(
        self,
        entry_price: float,
        targets: list[float] | None = None,
    ) -> list[tuple[float, int]]:
        """Replay D165 tranche exits bar-by-bar.

        Args:
            entry_price: Trade entry price.
            targets: Gain thresholds as fractions (default: [0.03, 0.06, 0.10]).

        Returns:
            List of (fill_price, hit_minute) for each target reached.
            Targets not reached are omitted.
        """
        if targets is None:
            targets = [0.03, 0.06, 0.10]

        if not self.bars or entry_price <= 0:
            return []

        hits: list[tuple[float, int]] = []
        pending = list(targets)

        for minute, bar in enumerate(self.bars):
            if not pending:
                break
            target_pct = pending[0]
            target_price = entry_price * (1.0 + target_pct)
            if bar.high >= target_price:
                hits.append((target_price, minute))
                pending.pop(0)

        return hits

    def simulate_observation_window(
        self,
        minutes: int = 15,
    ) -> tuple[bool, str]:
        """Replay D170 open candle observation window bar-by-bar.

        Evaluates whether the first `minutes` bars justify entry.
        Approval criteria:
          - First bar closes green (close > open).
          - Price holds above open for majority of window.
          - No extreme reversal bar in window (single bar down >5% from prior close).

        Returns:
            (approved, reason)
        """
        if not self.bars:
            return False, "no_bars"

        window = self.bars[:minutes] if minutes < len(self.bars) else self.bars
        first_bar = window[0]

        # Check 1: first bar is green
        if first_bar.close <= first_bar.open:
            return False, "first_bar_red"

        open_price = first_bar.open
        above_open_count = sum(1 for b in window if b.close >= open_price)
        above_ratio = above_open_count / len(window)

        # Check 2: majority of window above open
        if above_ratio < 0.50:
            return False, f"price_below_open_{above_ratio:.0%}"

        # Check 3: no extreme reversal bar (>5% single-bar drop)
        prev_close = first_bar.close
        for bar in window[1:]:
            if prev_close > 0 and (prev_close - bar.close) / prev_close > 0.05:
                return False, "extreme_reversal_bar"
            prev_close = bar.close

        return True, "approved"

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "date": self.date,
            "bars": [asdict(b) for b in self.bars],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BarSeries":
        bars = [MinuteBar(**b) for b in data["bars"]]
        return cls(ticker=data["ticker"], date=data["date"], bars=bars)


# =============================================================================
# BarRecorder
# =============================================================================


class BarRecorder:
    """Records minute bars during live sessions for arena replay.

    Saves bars to ``storage_dir/YYYY-MM-DD/TICKER.json`` so the meta
    simulation can reload the exact price path for any historical session.
    """

    def __init__(self, storage_dir: str) -> None:
        self._storage_dir = Path(storage_dir)

    async def record_bars(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        alpaca_client=None,
    ) -> Optional[BarSeries]:
        """Fetch and store minute bars from Alpaca.

        Args:
            ticker: Stock symbol.
            start: Window start (UTC).
            end: Window end (UTC).
            alpaca_client: AlpacaClient instance; if None, skips fetch.

        Returns:
            BarSeries if successful, None on error.
        """
        if alpaca_client is None:
            logger.warning("record_bars: no alpaca_client provided for %s", ticker)
            return None

        try:
            raw_bars = await alpaca_client.get_bars(
                symbol=ticker,
                timeframe="1Min",
                start=start.isoformat(),
                end=end.isoformat(),
                limit=390,
            )
        except Exception as exc:
            logger.error("record_bars failed for %s: %s", ticker, exc)
            return None

        if not raw_bars:
            logger.debug("record_bars: no bars returned for %s", ticker)
            return None

        bars = [MinuteBar.from_alpaca(b) for b in raw_bars]
        session_date = start.astimezone(timezone.utc).strftime("%Y-%m-%d")
        series = BarSeries(ticker=ticker, date=session_date, bars=bars)
        self.save(series)
        return series

    def save(self, series: BarSeries) -> None:
        """Save BarSeries to data/bar_recordings/YYYY-MM-DD/TICKER.json."""
        day_dir = self._storage_dir / series.date
        day_dir.mkdir(parents=True, exist_ok=True)
        out_path = day_dir / f"{series.ticker}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(series.to_dict(), f, indent=2)
        logger.debug("Saved bar series %s/%s → %s", series.ticker, series.date, out_path)

    def load(self, ticker: str, session_date: date) -> Optional[BarSeries]:
        """Load recorded bars for a ticker on a given date.

        Args:
            ticker: Stock symbol.
            session_date: Trading date.

        Returns:
            BarSeries or None if not recorded.
        """
        date_str = session_date.strftime("%Y-%m-%d")
        path = self._storage_dir / date_str / f"{ticker}.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return BarSeries.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load bar series %s/%s: %s", ticker, date_str, exc)
            return None

    def list_available(self) -> dict[date, list[str]]:
        """List all recorded date/ticker combinations.

        Returns:
            Dict mapping date → sorted list of tickers available that day.
        """
        result: dict[date, list[str]] = {}
        if not self._storage_dir.exists():
            return result

        for day_dir in sorted(self._storage_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            try:
                d = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            tickers = sorted(
                p.stem for p in day_dir.glob("*.json")
            )
            if tickers:
                result[d] = tickers

        return result
