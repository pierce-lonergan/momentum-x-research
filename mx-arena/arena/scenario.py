"""
Scenario library — synthetic market data generation for stress testing.

Generates minute-bar data for specific patterns:
- gap_and_go: Gap up with momentum continuation
- gap_and_fade: Gap up with immediate reversal
- flash_crash: Sudden drop mid-session
- trading_halt: LULD halt + resume
- short_squeeze: Accelerating move with volume surge
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional

from .fill_model import Bar


@dataclass
class ScenarioConfig:
    """Configuration for a synthetic scenario."""
    name: str
    base_pattern: str = "gap_and_go"
    entry_price: float = 5.0
    gap_pct: float = 0.20
    rvol: float = 5.0
    prev_close: float = 0.0  # Computed from entry_price / (1 + gap_pct)
    seed: int = 42

    # Pattern-specific params
    peak_minute: int = 20
    peak_return: float = 0.15
    fade_rate: float = 0.3

    # Injection params
    inject_at_minute: Optional[int] = None
    injection_type: Optional[str] = None  # flash_crash, halt, squeeze
    injection_params: dict = None

    def __post_init__(self):
        if self.prev_close == 0:
            self.prev_close = self.entry_price / (1 + self.gap_pct)
        if self.injection_params is None:
            self.injection_params = {}


class ScenarioGenerator:
    """Generates synthetic minute-bar data for stress testing."""

    def generate(
        self,
        config: ScenarioConfig,
        market_open: datetime | None = None,
    ) -> tuple[list[Bar], dict]:
        """
        Generate 390 bars (one trading day) from a scenario config.

        Returns:
            (bars, prev_daily_bar) — bars for the day + previous daily bar
            for snapshot construction.
        """
        if market_open is None:
            market_open = datetime(2026, 3, 25, 9, 30, tzinfo=timezone.utc)

        rng = Random(config.seed)

        if config.base_pattern == "gap_and_go":
            bars = self._gap_and_go(config, rng, market_open)
        elif config.base_pattern == "gap_and_fade":
            bars = self._gap_and_fade(config, rng, market_open)
        else:
            bars = self._gap_and_go(config, rng, market_open)

        # Apply injection if specified
        if config.inject_at_minute and config.injection_type:
            bars = self._apply_injection(
                bars, config.inject_at_minute,
                config.injection_type, config.injection_params, rng,
            )

        prev_daily = {
            "t": (market_open - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
            "o": round(config.prev_close * 0.98, 4),
            "h": round(config.prev_close * 1.02, 4),
            "l": round(config.prev_close * 0.97, 4),
            "c": round(config.prev_close, 4),
            "v": int(config.rvol * 500_000),
            "vw": round(config.prev_close, 4),
            "n": 10000,
        }

        return bars, prev_daily

    def _gap_and_go(
        self, config: ScenarioConfig, rng: Random, market_open: datetime,
    ) -> list[Bar]:
        """Gap up with momentum continuation, then fade."""
        bars = []
        price = config.entry_price

        for minute in range(390):
            ts = (market_open + timedelta(minutes=minute)).isoformat()

            if minute < 5:
                # Opening pullback (2-5%)
                drift = rng.gauss(-0.003, 0.005)
            elif minute < config.peak_minute:
                # Momentum advance
                drift = rng.gauss(config.peak_return / config.peak_minute, 0.004)
            elif minute < 60:
                # Gradual fade
                drift = rng.gauss(-config.fade_rate / 60, 0.003)
            else:
                # Consolidation
                drift = rng.gauss(0, 0.002)

            price *= (1 + drift)
            noise = abs(rng.gauss(0, 0.002))

            # U-shaped volume
            vol_base = config.rvol * 10000
            if minute < 15:
                vol = vol_base * (3.0 - minute * 0.13)
            elif minute > 375:
                vol = vol_base * (1.5 + (minute - 375) * 0.1)
            else:
                vol = vol_base * max(0.3, 1.0 - minute / 500)

            bars.append(Bar(
                timestamp=ts,
                open=round(price, 4),
                high=round(price * (1 + noise), 4),
                low=round(price * (1 - noise), 4),
                close=round(price, 4),
                volume=max(100, int(vol * rng.uniform(0.7, 1.3))),
                vwap=round(price * rng.uniform(0.998, 1.002), 4),
                trade_count=max(10, int(vol / 100)),
            ))

        return bars

    def _gap_and_fade(
        self, config: ScenarioConfig, rng: Random, market_open: datetime,
    ) -> list[Bar]:
        """Gap up with immediate reversal — tests stop-loss behavior."""
        bars = []
        price = config.entry_price

        for minute in range(390):
            ts = (market_open + timedelta(minutes=minute)).isoformat()

            if minute < 3:
                # Brief spike (2-3 bars)
                drift = rng.gauss(0.005, 0.003)
            elif minute < 30:
                # Sharp fade back through gap
                drift = rng.gauss(-0.008, 0.004)
            elif minute < 60:
                # Bounce attempt
                drift = rng.gauss(0.002, 0.003)
            else:
                # Continued weakness
                drift = rng.gauss(-0.001, 0.002)

            price *= (1 + drift)
            noise = abs(rng.gauss(0, 0.003))

            vol_base = config.rvol * 10000
            if minute < 15:
                vol = vol_base * (4.0 - minute * 0.2)
            else:
                vol = vol_base * max(0.3, 1.0 - minute / 500)

            bars.append(Bar(
                timestamp=ts,
                open=round(price, 4),
                high=round(price * (1 + noise), 4),
                low=round(price * (1 - noise), 4),
                close=round(price, 4),
                volume=max(100, int(vol * rng.uniform(0.7, 1.3))),
                vwap=round(price * rng.uniform(0.997, 1.003), 4),
                trade_count=max(10, int(vol / 100)),
            ))

        return bars

    def _apply_injection(
        self,
        bars: list[Bar],
        at_minute: int,
        injection_type: str,
        params: dict,
        rng: Random,
    ) -> list[Bar]:
        """Inject an event (crash, halt, squeeze) into existing bars."""
        if injection_type == "flash_crash":
            drop_pct = params.get("drop_pct", 0.12)
            recovery_bars = params.get("recovery_bars", 5)
            recovery_pct = params.get("recovery_pct", 0.07)

            if at_minute < len(bars):
                pre_price = bars[at_minute].close
                crash_price = pre_price * (1 - drop_pct)

                # Crash bar
                bars[at_minute] = Bar(
                    timestamp=bars[at_minute].timestamp,
                    open=pre_price,
                    high=pre_price,
                    low=crash_price,
                    close=crash_price,
                    volume=bars[at_minute].volume * 10,
                    vwap=round((pre_price + crash_price) / 2, 4),
                    trade_count=bars[at_minute].trade_count * 5,
                )

                # Recovery bars
                for i in range(1, recovery_bars + 1):
                    idx = at_minute + i
                    if idx < len(bars):
                        recovery_progress = i / recovery_bars
                        price = crash_price + (recovery_pct * pre_price * recovery_progress)
                        noise = abs(rng.gauss(0, 0.003))
                        bars[idx] = Bar(
                            timestamp=bars[idx].timestamp,
                            open=round(price * (1 - noise), 4),
                            high=round(price * (1 + noise), 4),
                            low=round(price * (1 - noise * 2), 4),
                            close=round(price, 4),
                            volume=bars[idx].volume * 3,
                            vwap=round(price, 4),
                            trade_count=bars[idx].trade_count * 2,
                        )

        elif injection_type == "trading_halt":
            duration_bars = params.get("duration_bars", 5)
            resume_gap_pct = params.get("resume_gap_pct", -0.05)

            if at_minute < len(bars):
                halt_price = bars[at_minute].close

                # Zero-volume halt bars
                for i in range(duration_bars):
                    idx = at_minute + i
                    if idx < len(bars):
                        bars[idx] = Bar(
                            timestamp=bars[idx].timestamp,
                            open=halt_price,
                            high=halt_price,
                            low=halt_price,
                            close=halt_price,
                            volume=0,
                            vwap=halt_price,
                            trade_count=0,
                        )

                # Resume with gap
                resume_idx = at_minute + duration_bars
                if resume_idx < len(bars):
                    resume_price = halt_price * (1 + resume_gap_pct)
                    bars[resume_idx] = Bar(
                        timestamp=bars[resume_idx].timestamp,
                        open=round(resume_price, 4),
                        high=round(resume_price * 1.02, 4),
                        low=round(resume_price * 0.98, 4),
                        close=round(resume_price, 4),
                        volume=bars[resume_idx].volume * 8,
                        vwap=round(resume_price, 4),
                        trade_count=bars[resume_idx].trade_count * 5,
                    )

        elif injection_type == "squeeze":
            acceleration = params.get("acceleration", 0.005)
            duration = params.get("duration_bars", 20)
            vol_mult = params.get("volume_multiplier", 5)

            for i in range(duration):
                idx = at_minute + i
                if idx < len(bars):
                    accel = acceleration * (1 + i / 10)
                    price = bars[idx].close * (1 + accel)
                    bars[idx] = Bar(
                        timestamp=bars[idx].timestamp,
                        open=round(bars[idx].close, 4),
                        high=round(price * 1.005, 4),
                        low=round(bars[idx].close * 0.998, 4),
                        close=round(price, 4),
                        volume=bars[idx].volume * vol_mult,
                        vwap=round(price * 0.999, 4),
                        trade_count=bars[idx].trade_count * 3,
                    )

        elif injection_type == "spread_shock":
            # Innovation 7: Multiply bar ranges to simulate wider spreads
            spread_mult = params.get("spread_multiplier", 3.0)
            duration = params.get("duration_bars", 30)
            for i in range(duration):
                idx = at_minute + i
                if idx < len(bars):
                    bar = bars[idx]
                    mid = (bar.high + bar.low) / 2
                    half_range = (bar.high - bar.low) / 2
                    new_half = half_range * spread_mult
                    bars[idx] = Bar(
                        timestamp=bar.timestamp, open=bar.open,
                        high=round(mid + new_half, 4),
                        low=round(mid - new_half, 4),
                        close=bar.close,
                        volume=max(1, int(bar.volume * 0.5)),
                        vwap=bar.vwap, trade_count=max(1, bar.trade_count // 2),
                    )

        elif injection_type == "volume_kill":
            # Innovation 7: Kill volume to simulate liquidity vacuum
            kill_pct = params.get("volume_kill_pct", 0.1)
            duration = params.get("duration_bars", 30)
            for i in range(duration):
                idx = at_minute + i
                if idx < len(bars):
                    bar = bars[idx]
                    bars[idx] = Bar(
                        timestamp=bar.timestamp, open=bar.open,
                        high=bar.high, low=bar.low, close=bar.close,
                        volume=max(1, int(bar.volume * kill_pct)),
                        vwap=bar.vwap, trade_count=max(1, int(bar.trade_count * kill_pct)),
                    )

        elif injection_type == "sector_correlation":
            # Innovation 10: Correlated move (sector selloff/rally)
            direction = params.get("direction", -1)
            mag = params.get("magnitude_per_bar", 0.003)
            duration = params.get("duration_bars", 30)
            for i in range(duration):
                idx = at_minute + i
                if idx < len(bars):
                    bar = bars[idx]
                    move = direction * mag * (1 + i * 0.1)
                    price = bar.close * (1 + move)
                    bars[idx] = Bar(
                        timestamp=bar.timestamp,
                        open=round(bar.close, 4),
                        high=round(max(bar.close, price) * 1.002, 4),
                        low=round(min(bar.close, price) * 0.998, 4),
                        close=round(price, 4),
                        volume=int(bar.volume * (1 + abs(move) * 50)),
                        vwap=round(price, 4), trade_count=bar.trade_count,
                    )

        return bars
