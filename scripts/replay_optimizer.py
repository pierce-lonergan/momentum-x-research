"""
MOMENTUM-X Replay Optimizer

Replays historical sessions with different parameter configurations to prove
what PnL would have been and find optimal settings. Uses REAL 1-minute bars
and the production ExitSignalEngine.

Unlike simulate_trades.py (which only sweeps exit/tighten thresholds), this
tool sweeps the FULL parameter space:
  - Kelly tier thresholds (MFCS cutoffs, risk %, max position %)
  - Position sizing (risk_per_trade, MFCS scaling)
  - Stop-loss parameters (ATR multiplier, cap %, floor %)
  - Tranche targets (+5/10/20% vs +3/6/10% vs +8/15/30%)
  - Exit intelligence thresholds
  - Runner parameters

Usage:
  # Single day, show what would've happened with current settings
  python scripts/replay_optimizer.py 2026-03-20

  # Single day with specific ticker
  python scripts/replay_optimizer.py 2026-03-20 --ticker ANNA

  # Full parameter sweep
  python scripts/replay_optimizer.py 2026-03-20 --sweep

  # Multi-day aggregate sweep
  python scripts/replay_optimizer.py --sweep-all

  # Compare old vs new settings
  python scripts/replay_optimizer.py 2026-03-19 2026-03-20 --compare
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# ── Project root ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

_env_path = _PROJECT_ROOT / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=False)
# Also try main repo .env (worktree may not have credentials)
_main_repo_env = Path.home() / "Documents" / "GitHub" / "momentum-x" / ".env"
if _main_repo_env.exists():
    load_dotenv(_main_repo_env, override=False)

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore

from src.execution.exit_intelligence import ExitSignalEngine

# ═══════════════════════════════════════════════════════════════════
# Configuration Profiles
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SimConfig:
    """Full set of tunable parameters for a simulation run."""
    name: str = "default"

    # Account
    equity: float = 138_000.0

    # Position sizing
    risk_per_trade_pct: float = 0.01      # 1% risk
    max_position_pct: float = 0.15        # 15% max
    mfcs_scaling_denom: float = 0.5       # position_pct = 0.05 + 0.10 * (mfcs / denom)

    # Kelly tier overrides (if MFCS exceeds threshold, scale up)
    kelly_enabled: bool = True
    tier2_min_mfcs: float = 0.25
    tier2_risk_pct: float = 0.02
    tier2_max_position_pct: float = 0.25
    tier3_min_mfcs: float = 0.35
    tier3_risk_pct: float = 0.04
    tier3_max_position_pct: float = 0.35

    # Kelly tier guardrails
    kelly_min_price: float = 1.00   # No tier upgrade for sub-$1 stocks
    kelly_min_gap_pct: float = 0.10  # Need 10%+ gap for tier upgrade

    # Stop-loss
    stop_loss_pct: float = 0.055          # Fixed fallback
    atr_multiplier: float = 2.0           # ATR-based stop
    atr_floor_pct: float = 0.04           # Min stop distance
    atr_cap_pct: float = 0.20             # Max stop distance (new!)

    # Tranche targets
    tranche_targets: tuple[float, ...] = (0.05, 0.10, 0.20)

    # Exit intelligence
    exit_threshold: float = 0.6
    tighten_threshold: float = 0.3

    # Trailing stop
    trailing_activation_pct: float = 0.04    # 4% — wider to let momentum trades develop
    trailing_trail_distance_pct: float | None = None  # D120: separate trail width (None = use activation_pct)
    chandelier_multiplier: float = 4.0

    # Tranche ratchet
    tranche_ratchet_ratio: float = 0.25  # D120: how much stop moves after tranche fill

    # Exit signal weight overrides
    exit_signal_weight_overrides: dict[str, float] | None = None  # D120: partial dict merged over defaults

    # Runner mode
    runner_pct: float = 0.0
    runner_trail_pct: float = 0.15

    # EOD
    eod_close_hour_et: int = 15
    eod_close_minute_et: int = 55


# Pre-built profiles for comparison
PROFILES = {
    "old_defaults": SimConfig(
        name="old_defaults",
        risk_per_trade_pct=0.01,
        max_position_pct=0.15,
        kelly_enabled=False,
        stop_loss_pct=0.055,
        atr_cap_pct=1.0,   # No cap (old behavior)
        trailing_activation_pct=0.02,  # Old tight trailing
        tranche_targets=(0.05, 0.10, 0.20),
        exit_threshold=0.6,
        tighten_threshold=0.3,
    ),
    "current": SimConfig(
        name="current",
        risk_per_trade_pct=0.01,
        max_position_pct=0.15,
        kelly_enabled=True,
        tier2_min_mfcs=0.25,
        tier2_risk_pct=0.02,
        tier2_max_position_pct=0.25,
        atr_cap_pct=0.20,
        tranche_targets=(0.05, 0.10, 0.20),
        exit_threshold=0.6,
        tighten_threshold=0.3,
    ),
    "aggressive": SimConfig(
        name="aggressive",
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tier3_min_mfcs=0.30,
        tier3_risk_pct=0.05,
        tier3_max_position_pct=0.40,
        atr_cap_pct=0.15,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.7,
        tighten_threshold=0.35,
        runner_pct=0.25,
    ),
    "conservative": SimConfig(
        name="conservative",
        risk_per_trade_pct=0.005,
        max_position_pct=0.10,
        kelly_enabled=True,
        tier2_min_mfcs=0.30,
        tier2_risk_pct=0.01,
        tier2_max_position_pct=0.15,
        atr_cap_pct=0.12,
        tranche_targets=(0.03, 0.06, 0.10),
        exit_threshold=0.5,
        tighten_threshold=0.25,
    ),
    "wide_targets": SimConfig(
        name="wide_targets",
        kelly_enabled=True,
        tranche_targets=(0.10, 0.20, 0.40),
        exit_threshold=0.7,
        tighten_threshold=0.4,
        runner_pct=0.25,
    ),
    "optimal_anna": SimConfig(
        name="optimal_anna",
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.25,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tier3_min_mfcs=0.30,
        tier3_risk_pct=0.05,
        tier3_max_position_pct=0.40,
        atr_cap_pct=0.15,
        trailing_activation_pct=0.06,  # Wider trailing for momentum
        tranche_targets=(0.05, 0.10, 0.20),
        exit_threshold=0.7,
        tighten_threshold=0.35,
        runner_pct=0.25,
        runner_trail_pct=0.10,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Bar:
    timestamp: str
    dt: datetime
    o: float
    h: float
    l: float
    c: float
    v: int
    vw: float


@dataclass
class TradeSetup:
    """A BUY verdict from the journal with all metadata for replay."""
    ticker: str
    entry_price: float
    entry_time: datetime
    mfcs: float
    risk_score: float
    gap_pct: float
    rvol: float
    stop_loss: float
    target_prices: list[float]
    position_size_pct: float
    catalyst_type: str
    agent_signals: dict[str, str]  # agent_id -> signal
    directional_count: int
    phase: str


def _find_journal(date_str: str) -> Path | None:
    journals_dir = _PROJECT_ROOT / "data" / "journals"
    for f in sorted(journals_dir.glob(f"journal_{date_str}*.jsonl")):
        return f
    return None


def _load_trade_setups(journal_path: Path) -> list[TradeSetup]:
    """Extract BUY verdicts with full metadata from journal."""
    setups: list[TradeSetup] = []
    seen_tickers: set[str] = set()

    with open(journal_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if entry.get("action") != "BUY":
                continue

            ticker = entry.get("ticker", "")
            # Only take the LAST BUY for each ticker (the one that was actually filled)
            if entry.get("order_id"):
                # Has an order_id = was actually executed
                # Remove any previous entry for this ticker
                setups = [s for s in setups if s.ticker != ticker]
            elif ticker in seen_tickers:
                continue

            seen_tickers.add(ticker)

            # Parse entry time
            ts_str = entry.get("timestamp", "")
            try:
                entry_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            # Extract agent signals
            signals = {}
            directional = 0
            for sig in entry.get("agent_signals", []):
                agent_id = sig.get("agent_id", "")
                signal = sig.get("signal", "NEUTRAL")
                signals[agent_id] = signal
                if signal in ("BULL", "STRONG_BULL", "BEAR", "STRONG_BEAR"):
                    directional += 1

            fill_price = entry.get("fill_price") or entry.get("current_price", 0)
            if not fill_price or fill_price <= 0:
                fill_price = entry.get("current_price", 0)

            setups.append(TradeSetup(
                ticker=ticker,
                entry_price=float(fill_price),
                entry_time=entry_time,
                mfcs=float(entry.get("mfcs", 0)),
                risk_score=float(entry.get("risk_score", 0)),
                gap_pct=float(entry.get("gap_pct", 0)),
                rvol=float(entry.get("rvol", 0)),
                stop_loss=float(entry.get("stop_loss", 0)),
                target_prices=entry.get("target_prices", []),
                position_size_pct=float(entry.get("position_size_pct", 0)),
                catalyst_type=entry.get("agent_signals", [{}])[0].get("catalyst_type", "NONE") if entry.get("agent_signals") else "NONE",
                agent_signals=signals,
                directional_count=directional,
                phase=entry.get("phase", "UNKNOWN"),
            ))

    # Deduplicate: keep only filled entries, or if none filled, keep first
    filled = [s for s in setups if True]  # All are valid at this point
    return filled


# ═══════════════════════════════════════════════════════════════════
# Bar Fetching (reuses cache from simulate_trades.py)
# ═══════════════════════════════════════════════════════════════════

BARS_CACHE_DIR = _PROJECT_ROOT / "data" / "bars"


async def fetch_bars(ticker: str, date_str: str) -> list[Bar]:
    """Fetch 1-min bars from Alpaca, with disk caching."""
    BARS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = BARS_CACHE_DIR / f"bars_{ticker}_{date_str}.json"

    if cache_file.exists():
        with open(cache_file, "r") as f:
            raw_bars = json.load(f)
        return _parse_bars(raw_bars)

    # Fetch from Alpaca (support both APCA_* and ALPACA_* env var names)
    api_key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
    base_url = os.environ.get("APCA_DATA_URL") or os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")

    if not api_key or not api_secret:
        print(f"  [{ticker}] No Alpaca credentials — cannot fetch bars")
        return []

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    # Fetch full day (4AM - 8PM ET)
    start = f"{date_str}T08:00:00Z"
    end = f"{date_str}T21:00:00Z"

    all_bars: list[dict] = []
    next_page = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(10):  # Max 10 pages
            params: dict[str, Any] = {
                "timeframe": "1Min",
                "start": start,
                "end": end,
                "limit": 10000,
                "feed": "sip",
                "adjustment": "split",
            }
            if next_page:
                params["page_token"] = next_page

            resp = await client.get(
                f"{base_url}/v2/stocks/{ticker}/bars",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                print(f"  [{ticker}] Alpaca API error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            bars = data.get("bars", [])
            all_bars.extend(bars)
            next_page = data.get("next_page_token")
            if not next_page:
                break

    if all_bars:
        with open(cache_file, "w") as f:
            json.dump(all_bars, f)
        print(f"  [{ticker}] Fetched {len(all_bars)} bars (cached)")

    return _parse_bars(all_bars)


def _parse_bars(raw: list[dict]) -> list[Bar]:
    bars = []
    for b in raw:
        try:
            ts = b.get("t", "")
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            bars.append(Bar(
                timestamp=ts, dt=dt,
                o=float(b.get("o", 0)), h=float(b.get("h", 0)),
                l=float(b.get("l", 0)), c=float(b.get("c", 0)),
                v=int(b.get("v", 0)), vw=float(b.get("vw", 0)),
            ))
        except (ValueError, TypeError):
            continue
    return sorted(bars, key=lambda x: x.dt)


# ═══════════════════════════════════════════════════════════════════
# Position Sizing with Kelly Tiers
# ═══════════════════════════════════════════════════════════════════

def compute_position_size(setup: TradeSetup, config: SimConfig) -> tuple[int, float, str]:
    """
    Compute position size using MFCS scaling + Kelly tier overrides.

    Returns: (qty, position_dollar_value, kelly_tier_name)
    """
    mfcs = setup.mfcs
    entry = setup.entry_price

    # Determine Kelly tier
    risk_pct = config.risk_per_trade_pct
    max_pos_pct = config.max_position_pct
    tier_name = "T1"

    if config.kelly_enabled:
        # Guardrails: only upgrade tier for stocks that merit it
        price_ok = entry >= config.kelly_min_price
        gap_ok = setup.gap_pct >= config.kelly_min_gap_pct

        if price_ok and gap_ok:
            if mfcs >= config.tier3_min_mfcs:
                risk_pct = config.tier3_risk_pct
                max_pos_pct = config.tier3_max_position_pct
                tier_name = "T3"
            elif mfcs >= config.tier2_min_mfcs:
                risk_pct = config.tier2_risk_pct
                max_pos_pct = config.tier2_max_position_pct
                tier_name = "T2"

    # MFCS-scaled position percentage
    # D121 BUG-S10: Guard against mfcs_scaling_denom=0 (ZeroDivisionError).
    _denom = config.mfcs_scaling_denom if config.mfcs_scaling_denom > 0 else 0.5
    mfcs_pct = 0.05 + 0.10 * min(1.0, mfcs / _denom)
    position_pct = min(mfcs_pct, max_pos_pct)

    # Compute stop distance
    stop_distance = entry * config.stop_loss_pct
    # Cap stop distance
    stop_distance = min(stop_distance, entry * config.atr_cap_pct)
    stop_distance = max(stop_distance, entry * config.atr_floor_pct)

    # Use actual stop from journal if available (ATR-based), capped
    if setup.stop_loss > 0:
        actual_stop_dist = entry - setup.stop_loss
        if actual_stop_dist > 0:
            actual_stop_dist = min(actual_stop_dist, entry * config.atr_cap_pct)
            stop_distance = actual_stop_dist

    # Risk-based qty: risk_pct of equity / stop distance per share
    if stop_distance > 0:
        qty_risk = int(config.equity * risk_pct / stop_distance)
    else:
        qty_risk = 0

    # Cap-based qty: max position as pct of equity
    qty_cap = int(config.equity * position_pct / entry) if entry > 0 else 0

    # Take the smaller (binding constraint)
    qty = min(qty_risk, qty_cap)
    qty = max(qty, 0)

    dollar_value = qty * entry
    return qty, dollar_value, tier_name


# ═══════════════════════════════════════════════════════════════════
# Trade Simulation Engine
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SimResult:
    ticker: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    exit_reason: str
    qty: int
    pnl: float
    pnl_pct: float
    mfe_pct: float  # Max favorable excursion
    mae_pct: float  # Max adverse excursion
    mfcs: float
    kelly_tier: str
    hold_minutes: float
    tranches_filled: int
    position_value: float
    gap_pct: float
    catalyst_type: str


def simulate_trade(
    setup: TradeSetup,
    bars: list[Bar],
    config: SimConfig,
) -> SimResult | None:
    """Simulate a single trade through bar-by-bar replay."""

    qty, position_value, tier_name = compute_position_size(setup, config)
    if qty <= 0:
        return None

    entry = setup.entry_price
    entry_time = setup.entry_time

    # Compute stop and targets from config (override journal values)
    stop_distance = entry * config.stop_loss_pct
    stop_distance = min(stop_distance, entry * config.atr_cap_pct)
    stop_distance = max(stop_distance, entry * config.atr_floor_pct)

    # Use actual ATR-based stop if available, but cap it and take the tighter of
    # fixed vs ATR (prevents penny stocks from having absurdly wide stops)
    if setup.stop_loss > 0:
        actual_dist = entry - setup.stop_loss
        if actual_dist > 0:
            actual_dist = min(actual_dist, entry * config.atr_cap_pct)
            # Use the TIGHTER (smaller) of fixed and ATR-capped stop
            stop_distance = min(stop_distance, actual_dist)

    stop_loss = entry - stop_distance
    targets = [round(entry * (1 + t), 4) for t in config.tranche_targets]

    # State
    peak_price = entry
    remaining_qty = qty
    tranches_filled = 0
    # D121 BUG-E1: When qty=1-2, qty//3=0 → all tranche exits skipped.
    # Use max(1, ...) so even single-share positions can take profit.
    tranche_size = max(1, qty // 3)
    trailing_active = False
    trailing_level = 0.0
    runner_mode = False
    # D121 BUG-H4: Pre-compute runner_qty so T3 sell can reserve shares.
    # Previously runner_qty was 0 until after T3 sold everything, leaving
    # 0 shares for the runner.
    runner_qty = max(1, int(qty * config.runner_pct)) if config.runner_pct > 0 else 0
    realized_pnl = 0.0
    mfe = 0.0  # Max favorable excursion %
    mae = 0.0  # Max adverse excursion %

    # Exit intelligence
    exit_engine = ExitSignalEngine(weights=config.exit_signal_weight_overrides)
    bar_history: list[dict] = []
    cum_vol_price = 0.0
    cum_vol = 0

    # Filter bars to after entry
    trade_bars = [b for b in bars if b.dt >= entry_time]
    if not trade_bars:
        return None

    exit_price = entry
    exit_time = entry_time
    exit_reason = "open"

    for bar_idx, bar in enumerate(trade_bars):
        # EOD close check
        bar_et = bar.dt.astimezone(timezone(timedelta(hours=-4)))
        if (bar_et.hour > config.eod_close_hour_et or
            (bar_et.hour == config.eod_close_hour_et and
             bar_et.minute >= config.eod_close_minute_et)):
            exit_price = bar.c
            exit_time = bar.dt
            exit_reason = "eod_close"
            break

        # Update tracking
        cum_vol_price += bar.vw * bar.v if bar.vw > 0 and bar.v > 0 else 0
        cum_vol += bar.v

        price_pct = (bar.h - entry) / entry if entry > 0 else 0
        dip_pct = (bar.l - entry) / entry if entry > 0 else 0
        mfe = max(mfe, price_pct)
        mae = min(mae, dip_pct)

        if bar.h > peak_price:
            peak_price = bar.h

        # 1. Stop-loss check (using bar low)
        if bar.l <= stop_loss and not runner_mode:
            exit_price = stop_loss  # Stop is a limit
            exit_time = bar.dt
            exit_reason = "stop_loss"
            break

        # 2. Tranche exits (check BEFORE trailing stop — price hitting targets
        #    takes priority over trailing stop within the same bar)
        if tranches_filled < 3 and remaining_qty > 0:
            for ti in range(tranches_filled, min(3, len(targets))):
                if bar.h >= targets[ti]:
                    # D121 BUG-H4: At T3, reserve runner_qty shares instead of selling all.
                    # Previously runner_qty was 0 here, so T3 sold everything.
                    if ti < 2:
                        sell_qty = tranche_size
                    elif runner_qty > 0 and not runner_mode:
                        sell_qty = remaining_qty - runner_qty
                    else:
                        sell_qty = remaining_qty
                    sell_qty = min(max(sell_qty, 0), remaining_qty)
                    if sell_qty > 0:
                        t_pnl = (targets[ti] - entry) * sell_qty
                        realized_pnl += t_pnl
                        remaining_qty -= sell_qty
                        tranches_filled = ti + 1

                        # After filling a tranche, raise stop to breakeven or higher
                        if ti >= 1:
                            new_stop = max(stop_loss, entry + (targets[ti] - entry) * config.tranche_ratchet_ratio)
                            stop_loss = new_stop

                        # After T3: activate runner or close
                        if ti == 2:
                            if config.runner_pct > 0 and remaining_qty > 0:
                                runner_mode = True
                                runner_qty = remaining_qty
                            elif remaining_qty <= 0:
                                exit_price = targets[ti]
                                exit_time = bar.dt
                                exit_reason = f"t{ti+1}_fill"
                                break

            if remaining_qty <= 0:
                exit_price = targets[min(tranches_filled - 1, len(targets) - 1)]
                exit_time = bar.dt
                exit_reason = f"t{tranches_filled}_fill"
                break

        # 3. Trailing stop (only after tranches have had a chance to fill)
        #    Don't activate trailing until price has exceeded first target or
        #    significant gain threshold — prevents premature exits on volatile stocks
        if not runner_mode:
            gain_pct = (bar.h - entry) / entry if entry > 0 else 0
            # Only activate trailing after meaningful gain (not just 2%)
            trail_activation = config.trailing_activation_pct
            if tranches_filled == 0:
                # Before any tranche fills, use a wider activation threshold
                # to let the trade develop toward targets
                trail_activation = max(trail_activation, config.tranche_targets[0] * 0.8 if config.tranche_targets else 0.04)

            if gain_pct >= trail_activation:
                trailing_active = True

            if trailing_active:
                # Trail from peak, but use wider trail distance based on
                # how many tranches have filled (more filled = tighter trail)
                # D120: decouple trail width from activation threshold
                base_trail = config.trailing_trail_distance_pct if config.trailing_trail_distance_pct is not None else config.trailing_activation_pct
                trail_distance = base_trail
                if tranches_filled >= 2:
                    trail_distance = base_trail * 0.75  # Tighter after T2
                new_trail = peak_price * (1 - trail_distance)
                if new_trail > trailing_level:
                    trailing_level = new_trail
                if bar.l <= trailing_level and trailing_level > stop_loss:
                    exit_price = trailing_level
                    exit_time = bar.dt
                    exit_reason = "trailing_stop"
                    break
        else:
            # Runner trailing stop (wider)
            runner_trail = peak_price * (1 - config.runner_trail_pct)
            if bar.l <= runner_trail:
                exit_price = runner_trail
                exit_time = bar.dt
                exit_reason = "runner_stop"
                break

        # 4. Exit intelligence (every bar after first few)
        if bar_idx >= 2 and not runner_mode:
            bar_history.append({
                "o": bar.o, "h": bar.h, "l": bar.l, "c": bar.c,
                "v": bar.v, "vw": bar.vw,
            })
            if len(bar_history) > 30:
                bar_history = bar_history[-30:]

            try:
                vwap = cum_vol_price / cum_vol if cum_vol > 0 else bar.c
                # D121 BUG-E5: Fixed API mismatch — compute_exit_signals requires
                # ticker as first arg, uses 'bars' not 'bar_history', and doesn't
                # have peak_price/time_held_minutes/session_high params. Previously
                # every call threw TypeError, caught by bare except → exit intelligence
                # was silently a no-op in all backtests.
                # D121 BUG-A6: Use actual bar time, not hold duration + hardcoded 9.
                # Previous code suppressed time_decay for afternoon entries.
                _hour_et = bar_et.hour
                _minute_et = bar_et.minute
                _exit_signal = exit_engine.compute_exit_signals(
                    ticker=setup.ticker,
                    current_price=bar.c,
                    entry_price=entry,
                    bid=bar.c * 0.999,
                    ask=bar.c * 1.001,
                    current_volume=bar.v,
                    peak_volume=max(b.v for b in trade_bars[:bar_idx+1]),
                    vwap=vwap,
                    hour_et=min(_hour_et, 16),
                    minute_et=_minute_et,
                    bars=bar_history,
                )
                composite = _exit_signal.composite_exit_urgency

                if composite >= config.exit_threshold:
                    exit_price = bar.c
                    exit_time = bar.dt
                    exit_reason = f"exit_signal({composite:.2f})"
                    break
                elif composite >= config.tighten_threshold:
                    # Tighten stop to breakeven or higher
                    new_stop = max(entry, stop_loss + (peak_price - entry) * 0.5)
                    if new_stop > stop_loss:
                        stop_loss = new_stop
            except Exception:
                pass

    # If we never broke out, EOD close at last bar
    if exit_reason == "open" and trade_bars:
        exit_price = trade_bars[-1].c
        exit_time = trade_bars[-1].dt
        exit_reason = "eod_close"

    # Final PnL
    unrealized_pnl = (exit_price - entry) * remaining_qty
    total_pnl = realized_pnl + unrealized_pnl

    hold_minutes = (exit_time - entry_time).total_seconds() / 60

    return SimResult(
        ticker=setup.ticker,
        entry_price=entry,
        exit_price=exit_price,
        entry_time=entry_time.isoformat(),
        exit_time=exit_time.isoformat(),
        exit_reason=exit_reason,
        qty=qty,
        pnl=round(total_pnl, 2),
        pnl_pct=round((total_pnl / (qty * entry)) * 100 if qty * entry > 0 else 0, 2),
        mfe_pct=round(mfe * 100, 2),
        mae_pct=round(mae * 100, 2),
        mfcs=setup.mfcs,
        kelly_tier=tier_name,
        hold_minutes=round(hold_minutes, 1),
        tranches_filled=tranches_filled,
        position_value=round(qty * entry, 2),
        gap_pct=round(setup.gap_pct * 100, 1),
        catalyst_type=setup.catalyst_type or "NONE",
    )


# ═══════════════════════════════════════════════════════════════════
# Portfolio Simulation
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PortfolioResult:
    config_name: str
    dates: list[str]
    total_pnl: float
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    avg_pnl: float
    best_trade: float
    worst_trade: float
    total_position_value: float
    roi_pct: float
    trades: list[SimResult]


async def simulate_day(
    date_str: str,
    config: SimConfig,
    ticker_filter: str | None = None,
    quiet: bool = False,
) -> list[SimResult]:
    """Simulate all trades for a single day with given config."""

    journal_path = _find_journal(date_str)
    if not journal_path:
        if not quiet:
            print(f"  No journal for {date_str}")
        return []

    setups = _load_trade_setups(journal_path)
    if ticker_filter:
        setups = [s for s in setups if s.ticker == ticker_filter.upper()]

    if not setups:
        if not quiet:
            print(f"  No BUY verdicts for {date_str}")
        return []

    if not quiet:
        print(f"\n  {date_str}: {len(setups)} trades — {', '.join(s.ticker for s in setups)}")

    # Fetch bars for all tickers
    results: list[SimResult] = []
    for setup in setups:
        bars = await fetch_bars(setup.ticker, date_str)
        if not bars:
            if not quiet:
                print(f"    [{setup.ticker}] No bars — skipping")
            continue

        # Filter to market hours (9:30 ET = 13:30 UTC)
        # D121 BUG-H7: Was `hour >= 13` which admits 13:00-13:29 (pre-market).
        market_bars = [b for b in bars
                       if b.dt.hour > 13 or (b.dt.hour == 13 and b.dt.minute >= 30)]

        result = simulate_trade(setup, market_bars, config)
        if result:
            results.append(result)
            if not quiet:
                pnl_color = "+" if result.pnl >= 0 else ""
                print(
                    f"    [{result.ticker}] {result.kelly_tier} "
                    f"qty={result.qty} @ ${result.entry_price:.2f} → "
                    f"${result.exit_price:.2f} ({result.exit_reason}) "
                    f"PnL={pnl_color}${result.pnl:,.2f} ({pnl_color}{result.pnl_pct:.1f}%) "
                    f"MFE={result.mfe_pct:.1f}% MAE={result.mae_pct:.1f}% "
                    f"hold={result.hold_minutes:.0f}min T{result.tranches_filled}/3"
                )

    return results


def aggregate_results(
    config: SimConfig,
    dates: list[str],
    all_results: list[SimResult],
) -> PortfolioResult:
    """Aggregate simulation results across days."""
    winners = [r for r in all_results if r.pnl > 0]
    losers = [r for r in all_results if r.pnl <= 0]
    total_pnl = sum(r.pnl for r in all_results)
    total_position = sum(r.position_value for r in all_results)

    return PortfolioResult(
        config_name=config.name,
        dates=dates,
        total_pnl=round(total_pnl, 2),
        total_trades=len(all_results),
        winners=len(winners),
        losers=len(losers),
        win_rate=round(len(winners) / max(1, len(all_results)) * 100, 1),
        avg_pnl=round(total_pnl / max(1, len(all_results)), 2),
        best_trade=max((r.pnl for r in all_results), default=0),
        worst_trade=min((r.pnl for r in all_results), default=0),
        total_position_value=round(total_position, 2),
        roi_pct=round(total_pnl / max(1, total_position) * 100, 2),
        trades=all_results,
    )


# ═══════════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════════

def print_portfolio_result(result: PortfolioResult) -> None:
    """Pretty-print a portfolio simulation result."""
    print(f"\n{'=' * 72}")
    print(f"  REPLAY RESULTS: {result.config_name}")
    print(f"  Dates: {', '.join(result.dates)}")
    print(f"{'=' * 72}")

    if not result.trades:
        print("  No trades simulated.")
        return

    print(f"\n  Total P&L:        ${result.total_pnl:>+10,.2f}")
    print(f"  Trades:           {result.total_trades} ({result.winners}W / {result.losers}L)")
    print(f"  Win Rate:         {result.win_rate:.1f}%")
    print(f"  Avg P&L/trade:    ${result.avg_pnl:>+10,.2f}")
    print(f"  Best Trade:       ${result.best_trade:>+10,.2f}")
    print(f"  Worst Trade:      ${result.worst_trade:>+10,.2f}")
    print(f"  Capital Deployed: ${result.total_position_value:>10,.2f}")
    print(f"  ROI:              {result.roi_pct:>+9.2f}%")

    print(f"\n  {'Ticker':<8} {'Tier':<4} {'Qty':>5} {'Entry':>8} {'Exit':>8} "
          f"{'P&L':>10} {'P&L%':>7} {'MFE%':>6} {'MAE%':>7} {'Hold':>6} "
          f"{'T':>2} {'Exit Reason':<20}")
    print(f"  {'-'*8} {'-'*4} {'-'*5} {'-'*8} {'-'*8} {'-'*10} {'-'*7} "
          f"{'-'*6} {'-'*7} {'-'*6} {'-'*2} {'-'*20}")

    for t in sorted(result.trades, key=lambda x: x.pnl, reverse=True):
        print(
            f"  {t.ticker:<8} {t.kelly_tier:<4} {t.qty:>5} "
            f"${t.entry_price:>7.2f} ${t.exit_price:>7.2f} "
            f"${t.pnl:>+9,.2f} {t.pnl_pct:>+6.1f}% "
            f"{t.mfe_pct:>5.1f}% {t.mae_pct:>+6.1f}% "
            f"{t.hold_minutes:>5.0f}m "
            f"T{t.tranches_filled} {t.exit_reason:<20}"
        )


def print_comparison(results: list[PortfolioResult]) -> None:
    """Compare multiple configuration results side-by-side."""
    print(f"\n{'=' * 80}")
    print(f"  CONFIGURATION COMPARISON")
    print(f"{'=' * 80}")

    header = f"  {'Metric':<25}"
    for r in results:
        header += f" | {r.config_name:>14}"
    print(header)
    print(f"  {'-' * 25}" + (" | " + "-" * 14) * len(results))

    metrics = [
        ("Total P&L", lambda r: f"${r.total_pnl:>+10,.2f}"),
        ("Trades", lambda r: f"{r.total_trades:>14}"),
        ("Win Rate", lambda r: f"{r.win_rate:>13.1f}%"),
        ("Avg P&L/trade", lambda r: f"${r.avg_pnl:>+10,.2f}"),
        ("Best Trade", lambda r: f"${r.best_trade:>+10,.2f}"),
        ("Worst Trade", lambda r: f"${r.worst_trade:>+10,.2f}"),
        ("Capital Deployed", lambda r: f"${r.total_position_value:>10,.2f}"),
        ("ROI", lambda r: f"{r.roi_pct:>+13.2f}%"),
    ]

    for name, fn in metrics:
        row = f"  {name:<25}"
        for r in results:
            row += f" | {fn(r):>14}"
        print(row)

    # Per-trade detail
    print(f"\n  Per-Trade Breakdown:")
    all_tickers = sorted(set(t.ticker for r in results for t in r.trades))
    for ticker in all_tickers:
        row = f"    {ticker:<8}"
        for r in results:
            trade = next((t for t in r.trades if t.ticker == ticker), None)
            if trade:
                row += f" | {trade.kelly_tier} ${trade.pnl:>+8,.2f} ({trade.qty:>4} sh)"
            else:
                row += f" |     {'N/A':>14}"
        print(row)


# ═══════════════════════════════════════════════════════════════════
# Sweep Engine
# ═══════════════════════════════════════════════════════════════════

async def run_sweep(
    dates: list[str],
    ticker_filter: str | None = None,
) -> None:
    """Sweep key parameters and find optimal configuration."""
    print(f"\n{'=' * 80}")
    print(f"  PARAMETER SWEEP — {', '.join(dates)}")
    print(f"{'=' * 80}")

    # Define sweep dimensions
    risk_pcts = [0.01, 0.015, 0.02, 0.03]
    atr_caps = [0.10, 0.15, 0.20, 0.25]
    target_profiles = {
        "tight":    (0.03, 0.06, 0.10),
        "standard": (0.05, 0.10, 0.20),
        "wide":     (0.08, 0.15, 0.30),
        "ultra":    (0.10, 0.20, 0.40),
    }
    exit_thresholds = [0.5, 0.6, 0.7, 0.8]
    runner_pcts = [0.0, 0.25]

    total_combos = len(risk_pcts) * len(atr_caps) * len(target_profiles) * len(exit_thresholds) * len(runner_pcts)
    print(f"  {total_combos} combinations to test\n")

    best_pnl = float("-inf")
    best_config: SimConfig | None = None
    all_sweep_results: list[tuple[str, float, int, float]] = []
    combo_idx = 0

    for risk, cap, (tgt_name, tgt_vals), exit_t, runner in product(
        risk_pcts, atr_caps, target_profiles.items(), exit_thresholds, runner_pcts
    ):
        combo_idx += 1
        config = SimConfig(
            name=f"r{risk:.0%}_c{cap:.0%}_{tgt_name}_e{exit_t}_run{runner:.0%}",
            risk_per_trade_pct=risk,
            atr_cap_pct=cap,
            tranche_targets=tgt_vals,
            exit_threshold=exit_t,
            tighten_threshold=exit_t * 0.5,
            runner_pct=runner,
            kelly_enabled=True,
        )

        all_results: list[SimResult] = []
        for date_str in dates:
            day_results = await simulate_day(date_str, config, ticker_filter, quiet=True)
            all_results.extend(day_results)

        total_pnl = sum(r.pnl for r in all_results)
        win_rate = sum(1 for r in all_results if r.pnl > 0) / max(1, len(all_results)) * 100

        all_sweep_results.append((config.name, total_pnl, len(all_results), win_rate))

        if total_pnl > best_pnl:
            best_pnl = total_pnl
            best_config = config

        if combo_idx % 50 == 0 or combo_idx == total_combos:
            print(f"  [{combo_idx}/{total_combos}] Best so far: ${best_pnl:+,.2f} ({best_config.name if best_config else 'N/A'})")

    # Sort and show top 10
    all_sweep_results.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  TOP 10 CONFIGURATIONS:")
    print(f"  {'Rank':<6} {'Config':<45} {'P&L':>10} {'Trades':>7} {'WinRate':>8}")
    print(f"  {'-'*6} {'-'*45} {'-'*10} {'-'*7} {'-'*8}")
    for i, (name, pnl, trades, wr) in enumerate(all_sweep_results[:10]):
        print(f"  {i+1:<6} {name:<45} ${pnl:>+9,.2f} {trades:>7} {wr:>7.1f}%")

    # Run detailed report with best config
    if best_config:
        print(f"\n  Running detailed simulation with best config: {best_config.name}")
        all_results = []
        for date_str in dates:
            day_results = await simulate_day(date_str, best_config, ticker_filter)
            all_results.extend(day_results)
        portfolio = aggregate_results(best_config, dates, all_results)
        print_portfolio_result(portfolio)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

async def async_main(args: argparse.Namespace) -> None:
    dates = args.dates

    if args.sweep:
        await run_sweep(dates, args.ticker)
        return

    if args.compare:
        # Compare all profiles
        profile_results: list[PortfolioResult] = []
        for profile_name, config in PROFILES.items():
            all_results: list[SimResult] = []
            for date_str in dates:
                day_results = await simulate_day(date_str, config, args.ticker, quiet=True)
                all_results.extend(day_results)
            portfolio = aggregate_results(config, dates, all_results)
            profile_results.append(portfolio)
            print_portfolio_result(portfolio)

        print_comparison(profile_results)
        return

    # Default: run with current config
    config = PROFILES.get(args.profile, PROFILES["current"])
    all_results: list[SimResult] = []
    for date_str in dates:
        day_results = await simulate_day(date_str, config, args.ticker)
        all_results.extend(day_results)

    portfolio = aggregate_results(config, dates, all_results)
    print_portfolio_result(portfolio)

    # Save results
    out_dir = _PROJECT_ROOT / "data" / "simulations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"replay_{'-'.join(dates)}_{config.name}_{int(time.time())}.json"
    with open(out_file, "w") as f:
        json.dump({
            "config": asdict(config),
            "portfolio": {
                "total_pnl": portfolio.total_pnl,
                "total_trades": portfolio.total_trades,
                "win_rate": portfolio.win_rate,
                "roi_pct": portfolio.roi_pct,
            },
            "trades": [asdict(t) for t in all_results],
        }, f, indent=2, default=str)
    print(f"\n  Results saved to: {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Momentum-X Replay Optimizer — prove P&L under different configs"
    )
    parser.add_argument(
        "dates", nargs="*", default=[],
        help="Trading dates to replay (YYYY-MM-DD). Multiple dates for aggregate."
    )
    parser.add_argument("--ticker", "-t", help="Filter to single ticker")
    parser.add_argument("--profile", "-p", default="current",
                        choices=list(PROFILES.keys()),
                        help="Pre-built config profile")
    parser.add_argument("--compare", "-c", action="store_true",
                        help="Compare all profiles side-by-side")
    parser.add_argument("--sweep", "-s", action="store_true",
                        help="Full parameter sweep to find optimal config")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available trading days")

    args = parser.parse_args()

    if args.list:
        journals_dir = _PROJECT_ROOT / "data" / "journals"
        for f in sorted(journals_dir.glob("journal_*.jsonl")):
            date = f.stem.split("_")[1]
            size = f.stat().st_size
            print(f"  {date}  ({size:,} bytes)")
        return

    if not args.dates and not args.list:
        # Default to last 2 days
        args.dates = ["2026-03-19", "2026-03-20"]
        print(f"  No dates specified — defaulting to {', '.join(args.dates)}")

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
