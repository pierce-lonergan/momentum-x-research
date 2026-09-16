"""
Parameter sweep runner — run many simulations in parallel.

Modes:
- SINGLE: One date, one parameter set (basic replay)
- PARAMETER_SWEEP: One date, grid of parameter values
- ROBUSTNESS: One parameter set, multiple dates
- FULL_MATRIX: Dates x params (comprehensive)

Each simulation is independent — embarrassingly parallel.
Uses multiprocessing for clean isolation per run.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .decision_replay import load_candidates_from_journals, replay_decisions
from .stats import bootstrap_profit_factor, rank_by_ci_lower
from typing import Any

from .clock import ClockMode
from .harness import ArenaConfig, ArenaInstance, RunResult

logger = logging.getLogger(__name__)


@dataclass
class SweepConfig:
    """Configuration for a parameter sweep."""
    dates: list[str]
    symbols_per_date: dict[str, list[str]]  # {date: [tickers]}
    prev_daily_per_date: dict[str, dict]     # {date: {ticker: prev_daily}}
    param_grid: dict[str, list[Any]]         # {param_name: [values]}
    data_dir: str = "mx-arena/data/historical"
    json_bars_dir: str = ""
    daily_dir: str = "mx-arena/data/daily"
    initial_cash: float = 100_000.0
    base_seed: int = 42
    fill_model: str = "alpaca"
    max_workers: int = 4
    # D132: Decision replay mode — re-evaluate candidates with sweep params
    decision_replay: bool = False
    journals_dir: str = ""


def _run_single_sim(config_dict: dict) -> dict:
    """
    Run a single simulation in a subprocess.

    Takes a serializable dict (not ArenaConfig) for multiprocessing pickling.
    Returns a serializable result dict.
    """
    config = ArenaConfig(
        date=config_dict["date"],
        symbols=config_dict["symbols"],
        data_dir=config_dict["data_dir"],
        daily_dir=config_dict.get("daily_dir"),
        initial_cash=config_dict.get("initial_cash", 100_000),
        seed=config_dict.get("seed", 42),
        clock_mode=ClockMode.REPLAY,
        fill_model=config_dict.get("fill_model", "alpaca"),
        param_overrides=config_dict.get("param_overrides", {}),
        prev_daily_bars=config_dict.get("prev_daily_bars", {}),
        id=config_dict.get("id", ""),
    )

    instance = ArenaInstance(config)

    # Set JSON bars dir if provided
    json_bars_dir = config_dict.get("json_bars_dir", "")
    if json_bars_dir:
        instance.data_engine.json_bars_dir = Path(json_bars_dir)

    instance.load_data()

    # Run synchronously (we're in a subprocess)
    result = asyncio.run(instance.run_standalone())

    # D132: Decision replay mode — re-evaluate candidates with sweep params
    decision_replay = config_dict.get("decision_replay", False)
    if decision_replay:
        candidates_data = config_dict.get("candidates_data", [])
        # Reconstruct CandidateContext objects
        from .decision_replay import CandidateContext
        candidates = [CandidateContext(**c) for c in candidates_data]
        replay_buys = replay_decisions(candidates, config_dict.get("param_overrides", {}))
    else:
        journal_buys = config_dict.get("journal_buys", [])
        replay_buys = journal_buys

    # Simulate trades against real bars
    sim_trades = _simulate_journal_trades(
        instance, replay_buys, config_dict.get("param_overrides", {}),
    )

    # Compute bootstrap CI
    pf, ci_lo, ci_hi = bootstrap_profit_factor(sim_trades)

    return {
        "config_id": config.id,
        "date": config.date,
        "params": config.param_overrides,
        "decision_replay": decision_replay,
        "arena_pnl": result.pnl,
        "arena_trades": len(result.trades),
        "arena_orders": result.orders_total,
        "sim_trades": sim_trades,
        "sim_pnl": sum(t.get("pnl", 0) for t in sim_trades),
        "sim_win_rate": (
            sum(1 for t in sim_trades if t.get("pnl", 0) > 0)
            / max(len(sim_trades), 1)
        ),
        "sim_trade_count": len(sim_trades),
        "pf_point": pf,
        "pf_ci_lower": ci_lo,
        "pf_ci_upper": ci_hi,
        # Track how many NEW buys (not in original journal)
        "new_buys": sum(1 for t in sim_trades if t.get("is_new_buy", False)),
    }


def _parse_bar_timestamp(ts: str) -> "datetime":
    """Parse bar timestamp string to datetime."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        if isinstance(ts, str) and ts:
            return _dt.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    return _dt.now(_tz.utc)


def _assign_tier(float_shares, gap_pct, rvol, tier_config=None):
    """D150: Assign position tier based on float/gap/RVOL.

    Replicates production logic from alpaca_executor.py.
    Returns (tier_number, position_pct).
    """
    tc = tier_config or {}
    t1_float = tc.get("tier1_float_max", 5_000_000)
    t1_gap = tc.get("tier1_gap_min", 0.20)
    t1_rvol = tc.get("tier1_rvol_min", 5.0)
    t2_float = tc.get("tier2_float_max", 20_000_000)
    t2_gap = tc.get("tier2_gap_min", 0.10)
    t2_rvol = tc.get("tier2_rvol_min", 3.0)

    if float_shares is not None and float_shares < t1_float and gap_pct >= t1_gap and rvol >= t1_rvol:
        return 1, tc.get("tier1_position_pct", 0.50)
    elif float_shares is not None and float_shares < t2_float and gap_pct >= t2_gap and rvol >= t2_rvol:
        return 2, tc.get("tier2_position_pct", 0.30)
    else:
        return 3, tc.get("tier3_position_pct", 0.15)


def _simulate_journal_trades(
    instance: ArenaInstance,
    journal_buys: list[dict],
    param_overrides: dict,
) -> list[dict]:
    """
    Simulate journal BUY signals against real bar data.

    Sprint 2: Multi-tranche exits (T1/T2/T3) + stop ratcheting.
    Sprint 3: Exit intelligence (4 strategies) + portfolio constraints.

    Portfolio constraints:
    - max_positions (default 3): Can't enter more than N positions
    - evaluation_delay_bars (default 0): Bars offset per candidate (models
      30s LLM eval delay — candidate #2 starts checking at bar 1, #3 at bar 2)
    - Candidates sorted by gap_pct * rvol (highest momentum first)
    """
    from .spread_model import SpreadModel
    from datetime import datetime as _dt, timezone as _tz
    _spread = SpreadModel()

    trades = []
    stop_pct_override = param_overrides.get("stop_loss_pct")
    max_positions = int(param_overrides.get("max_positions", 3))
    eval_delay_bars = int(param_overrides.get("evaluation_delay_bars", 0))

    # Sort candidates by momentum score (highest first — best gets evaluated first)
    journal_buys = sorted(
        journal_buys,
        key=lambda b: abs(b.get("gap_pct", 0)) * b.get("rvol", 1),
        reverse=True,
    )

    positions_open = 0

    for candidate_idx, buy in enumerate(journal_buys):
        # Portfolio constraint: max concurrent positions
        if positions_open >= max_positions:
            break

        ticker = buy.get("ticker", "")
        entry_price = buy.get("entry_price", 0)
        stop_loss = buy.get("stop_loss", 0)
        target_prices = buy.get("target_prices", [])

        # Apply stop override
        if stop_pct_override and entry_price > 0:
            stop_loss = entry_price * (1 - stop_pct_override)

        bars = instance.data_engine._minute_bars.get(ticker, {})
        if not bars or entry_price <= 0:
            continue

        # Sequential evaluation delay: candidate N starts checking at bar N*delay
        entry_start_bar = candidate_idx * eval_delay_bars

        # ── Entry detection ──────────────────────────────────────
        filled = False
        fill_price = 0.0
        fill_idx = 0

        for idx in sorted(bars.keys()):
            # Sequential delay: skip bars before this candidate's eval start
            if idx < entry_start_bar:
                continue
            bar = bars[idx]
            _ts_dt = _parse_bar_timestamp(bar.timestamp)
            _, ask = _spread.get_bid_ask(bar.close, bar.volume, _ts_dt)
            if ask <= entry_price:
                filled = True
                fill_price = ask
                fill_idx = idx
                break
            if idx > 60:
                break

        if not filled:
            continue

        positions_open += 1

        # ── Tranche setup ────────────────────────────────────────
        # Production: 3 equal tranches at T1, T2, T3
        # Stop ratchets: after T1 -> breakeven, after T2 -> T1
        # D150: Tier-based position sizing (scales qty by float/gap/RVOL tier)
        tier_config = param_overrides.get("tier_config")
        if tier_config:
            _float = buy.get("float_shares")
            _gap = abs(buy.get("gap_pct", 0))
            _rvol = buy.get("rvol", 0)
            _tier, _tier_pct = _assign_tier(_float, _gap, _rvol, tier_config)
            _base_pct = tier_config.get("tier3_position_pct", 0.15)
            total_qty = max(1, int(100 * _tier_pct / _base_pct))
            buy["_tier"] = _tier
            buy["_tier_pct"] = _tier_pct
        else:
            total_qty = 100  # Normalized to 100 shares for P&L per-share math
        # D150: Tranche qty must scale with total_qty (not hardcoded to 100)
        _t1_qty = total_qty // 3
        _t2_qty = total_qty // 3
        _t3_qty = total_qty - _t1_qty - _t2_qty  # Remainder to last tranche
        if len(target_prices) >= 3:
            tranches = [
                {"target": target_prices[0], "qty": _t1_qty, "filled": False, "fill_price": 0},
                {"target": target_prices[1], "qty": _t2_qty, "filled": False, "fill_price": 0},
                {"target": target_prices[2], "qty": _t3_qty, "filled": False, "fill_price": 0},
            ]
        elif len(target_prices) == 2:
            _h1 = total_qty // 2
            tranches = [
                {"target": target_prices[0], "qty": _h1, "filled": False, "fill_price": 0},
                {"target": target_prices[1], "qty": total_qty - _h1, "filled": False, "fill_price": 0},
            ]
        elif len(target_prices) == 1:
            tranches = [
                {"target": target_prices[0], "qty": total_qty, "filled": False, "fill_price": 0},
            ]
        else:
            tranches = []  # No targets — hold until stop/EOD

        # D142 Innovation A: Time-triggered partial exits
        # Instead of waiting for price targets, exit fractions at fixed time intervals.
        # MFE peaks at bar 1 — lock in profit early before fade.
        time_exit_bars = param_overrides.get("time_exit_bars", [])  # e.g. [5, 15]
        time_exit_pcts = param_overrides.get("time_exit_pcts", [])   # e.g. [0.40, 0.30]
        # Remaining qty held with trailing stop (or until EOD)
        time_exits_applied: set[int] = set()

        # Phase 3 innovation: time-phased stops
        phase_stop_t1 = int(param_overrides.get("phase_stop_t1", 0))  # 0 = disabled
        phase_stop_t2 = int(param_overrides.get("phase_stop_t2", 0))
        phase1_pct = float(param_overrides.get("phase1_stop_pct", 0))
        phase3_trail_pct = float(param_overrides.get("phase3_trail_pct", 0))

        if phase_stop_t1 > 0 and phase1_pct > 0:
            # Start with ultra-tight Phase 1 stop
            current_stop = fill_price * (1 - phase1_pct)
        else:
            current_stop = stop_loss

        remaining_qty = total_qty
        max_price = fill_price
        min_price = fill_price
        mfe_bar = 0
        mae_bar = 0
        tranche_pnl = 0.0
        tranche_exits = []
        exit_reason = "eod"
        final_exit_price = fill_price
        bars_since_entry: list[Bar] = []
        from .fill_model import Bar as _Bar
        entry_volume = bars.get(fill_idx, _Bar("", 0, 0, 0, 0, 50000)).volume
        exit_intel_triggered = False

        # ── Bar-by-bar simulation ────────────────────────────────
        for idx in sorted(bars.keys()):
            if idx <= fill_idx:
                continue
            bar = bars[idx]
            _ts_dt = _parse_bar_timestamp(bar.timestamp)
            bars_since_entry.append(bar)
            minutes_held = len(bars_since_entry)
            minutes_since_open = idx  # Approximate: 1 bar = 1 minute

            if bar.high > max_price:
                max_price = bar.high
                mfe_bar = len(bars_since_entry)
            if bar.low < min_price:
                min_price = bar.low
                mae_bar = len(bars_since_entry)

            # ── Phase 3 innovation: time-phased stop transitions ──
            if phase_stop_t1 > 0:
                if minutes_held == phase_stop_t1:
                    # Transition to Phase 2: widen to original ATR-based stop
                    new_stop = stop_loss
                    if new_stop > current_stop:
                        current_stop = new_stop  # Only widen if higher
                elif phase_stop_t2 > 0 and minutes_held == phase_stop_t2 and phase3_trail_pct > 0:
                    # Transition to Phase 3: trailing stop
                    trail_stop = max_price * (1 - phase3_trail_pct)
                    if trail_stop > current_stop:
                        current_stop = trail_stop
                elif phase_stop_t2 > 0 and minutes_held > phase_stop_t2 and phase3_trail_pct > 0:
                    # Phase 3 ongoing: update trailing stop each bar
                    trail_stop = max_price * (1 - phase3_trail_pct)
                    if trail_stop > current_stop:
                        current_stop = trail_stop

            # ── Sprint 3: Exit intelligence (4 parallel strategies) ──
            # Check every 5 bars (production checks every 60s cycle)
            if (minutes_held >= 5 and minutes_held % 5 == 0
                    and remaining_qty > 0 and not exit_intel_triggered):
                from .exit_intelligence import evaluate_exit
                action, conf, _ = evaluate_exit(
                    bars_since_entry=bars_since_entry,
                    entry_price=fill_price,
                    current_price=bar.close,
                    stop_loss=current_stop,
                    entry_volume=entry_volume,
                    minutes_held=minutes_held,
                    minutes_since_open=minutes_since_open,
                )
                if action == "EXIT" and conf >= 0.5:
                    # Smart exit: close all remaining at bid
                    bid, _ = _spread.get_bid_ask(bar.close, bar.volume, _ts_dt)
                    exit_pnl = remaining_qty * (bid - fill_price) / total_qty
                    tranche_pnl += exit_pnl
                    tranche_exits.append({
                        "type": "exit_intel",
                        "qty": remaining_qty,
                        "price": round(bid, 4),
                        "pnl": round(exit_pnl, 4),
                    })
                    remaining_qty = 0
                    exit_reason = "exit_intelligence"
                    final_exit_price = bid
                    exit_intel_triggered = True
                    break
                elif action == "TIGHTEN":
                    # Ratchet stop up (ATR-grounded: ~1.5% trail)
                    new_stop = bar.close * 0.985
                    if new_stop > current_stop:
                        current_stop = new_stop

            # D142 Innovation A: Time-triggered partial exits
            # If position is profitable at bar N, sell a fraction immediately
            if time_exit_bars and remaining_qty > 0:
                for i, exit_bar in enumerate(time_exit_bars):
                    if (minutes_held == exit_bar
                            and i not in time_exits_applied
                            and i < len(time_exit_pcts)
                            and bar.close > fill_price):  # Only if profitable
                        exit_pct = time_exit_pcts[i]
                        exit_qty = int(total_qty * exit_pct)
                        exit_qty = min(exit_qty, remaining_qty)
                        if exit_qty > 0:
                            bid, _ = _spread.get_bid_ask(bar.close, bar.volume, _ts_dt)
                            te_pnl = exit_qty * (bid - fill_price) / total_qty
                            tranche_pnl += te_pnl
                            remaining_qty -= exit_qty
                            tranche_exits.append({
                                "type": f"time_T{i+1}@{exit_bar}m",
                                "qty": exit_qty,
                                "price": round(bid, 4),
                                "pnl": round(te_pnl, 4),
                            })
                            # Ratchet stop to breakeven after first time exit
                            if fill_price > current_stop:
                                current_stop = fill_price
                        time_exits_applied.add(i)

            # Check stop first (stops have priority over limit sells)
            if current_stop > 0 and bar.low <= current_stop and remaining_qty > 0:
                if bar.open <= current_stop:
                    stop_fill = bar.open
                else:
                    bid, _ = _spread.get_bid_ask(bar.close, bar.volume, _ts_dt)
                    stop_fill = min(bid, current_stop)

                # Stop closes ALL remaining qty
                stop_pnl = remaining_qty * (stop_fill - fill_price) / total_qty
                tranche_pnl += stop_pnl
                tranche_exits.append({
                    "type": "stop",
                    "qty": remaining_qty,
                    "price": round(stop_fill, 4),
                    "pnl": round(stop_pnl, 4),
                })
                remaining_qty = 0
                exit_reason = "stop"
                final_exit_price = stop_fill
                break

            # Check tranche targets (sell 1/3 at each level)
            for tr in tranches:
                if tr["filled"] or remaining_qty <= 0:
                    continue
                if bar.high >= tr["target"]:
                    # Tranche fills at target (limit sell)
                    bid, _ = _spread.get_bid_ask(bar.close, bar.volume, _ts_dt)
                    tr_fill = max(bid, tr["target"])  # At least target price
                    tr["filled"] = True
                    tr["fill_price"] = tr_fill
                    tr_qty = tr["qty"]
                    tr_pnl = tr_qty * (tr_fill - fill_price) / total_qty
                    tranche_pnl += tr_pnl
                    remaining_qty -= tr_qty
                    tranche_exits.append({
                        "type": f"T{tranches.index(tr)+1}",
                        "qty": tr_qty,
                        "price": round(tr_fill, 4),
                        "pnl": round(tr_pnl, 4),
                    })

                    # ── Stop ratcheting ──────────────────────
                    filled_count = sum(1 for t in tranches if t["filled"])
                    if filled_count == 1:
                        # After T1: ratchet stop to breakeven
                        new_stop = fill_price
                        if new_stop > current_stop:
                            current_stop = new_stop
                    elif filled_count == 2:
                        # After T2: ratchet stop to T1 price
                        new_stop = tranches[0]["target"]
                        if new_stop > current_stop:
                            current_stop = new_stop

            if remaining_qty <= 0:
                exit_reason = "all_tranches"
                break

            final_exit_price = bar.close

        # EOD: close remaining at last bar close
        if remaining_qty > 0:
            eod_pnl = remaining_qty * (final_exit_price - fill_price) / total_qty
            tranche_pnl += eod_pnl
            tranche_exits.append({
                "type": "eod",
                "qty": remaining_qty,
                "price": round(final_exit_price, 4),
                "pnl": round(eod_pnl, 4),
            })

        # MFE/MAE/Capture ratio (Phase 1 innovation)
        mfe = max_price - fill_price  # Max favorable excursion ($)
        mae = fill_price - min_price  # Max adverse excursion ($)
        mfe_pct = mfe / fill_price if fill_price > 0 else 0
        mae_pct = mae / fill_price if fill_price > 0 else 0
        capture_ratio = tranche_pnl / mfe if mfe > 0 else 0  # How much of MFE captured

        trades.append({
            "ticker": ticker,
            "fill_price": round(fill_price, 4),
            "stop_loss": round(stop_loss, 4),
            "current_stop": round(current_stop, 4),
            "target_prices": [round(t, 2) for t in target_prices],
            "pnl": round(tranche_pnl, 4),
            "pnl_pct": round(tranche_pnl / fill_price, 4) if fill_price > 0 else 0,
            "mfe": round(mfe, 4),
            "mfe_pct": round(mfe_pct, 4),
            "mfe_bar": mfe_bar,
            "mae": round(mae, 4),
            "mae_pct": round(mae_pct, 4),
            "mae_bar": mae_bar,
            "capture_ratio": round(capture_ratio, 4),
            "exit_reason": exit_reason,
            "tranches": tranche_exits,
            "is_new_buy": buy.get("is_new_buy", False),
            # D148: Enrichment data for ML training
            "float_shares": buy.get("float_shares"),
            "market_cap": buy.get("market_cap"),
            "industry": buy.get("industry"),
            # D150: Tier sizing data
            "tier": buy.get("_tier", 0),
            "tier_pct": buy.get("_tier_pct", 0),
            "total_qty": total_qty,
        })

    return trades


class SweepRunner:
    """Run parameter sweeps across dates and parameter combinations."""

    def __init__(self, sweep_config: SweepConfig):
        self.config = sweep_config

    def generate_configs(self) -> list[dict]:
        """Generate all (date x param) configurations."""
        configs = []

        # Generate param combos
        if self.config.param_grid:
            param_names = list(self.config.param_grid.keys())
            param_values = list(self.config.param_grid.values())
            combos = list(itertools.product(*param_values))
        else:
            param_names = []
            combos = [()]

        for date in self.config.dates:
            symbols = self.config.symbols_per_date.get(date, [])
            prev_daily = self.config.prev_daily_per_date.get(date, {})

            # D132: Decision replay loads ALL candidates, not just BUY signals
            if self.config.decision_replay and self.config.journals_dir:
                candidates = load_candidates_from_journals(date, self.config.journals_dir)
                candidates_data = [
                    {
                        "ticker": c.ticker,
                        "current_price": c.current_price,
                        "previous_close": c.previous_close,
                        "gap_pct": c.gap_pct,
                        "rvol": c.rvol,
                        "premarket_volume": c.premarket_volume,
                        "has_news_catalyst": c.has_news_catalyst,
                        "entry_price": c.entry_price,
                        "stop_loss": c.stop_loss,
                        "journal_signals": c.journal_signals,
                        "journal_mfcs": c.journal_mfcs,
                        "journal_action": c.journal_action,
                        "journal_component_scores": c.journal_component_scores,
                        "journal_risk_score": c.journal_risk_score,
                    }
                    for c in candidates
                ]
            else:
                candidates_data = []

            journal_buys = self._load_journal_buys(date)

            for combo in combos:
                overrides = dict(zip(param_names, combo))
                config_id = f"{date}_{'_'.join(f'{k}={v}' for k,v in overrides.items())}"

                configs.append({
                    "id": config_id,
                    "date": date,
                    "symbols": symbols,
                    "data_dir": self.config.data_dir,
                    "json_bars_dir": self.config.json_bars_dir,
                    "daily_dir": self.config.daily_dir,
                    "initial_cash": self.config.initial_cash,
                    "seed": self.config.base_seed,
                    "fill_model": self.config.fill_model,
                    "param_overrides": overrides,
                    "prev_daily_bars": prev_daily,
                    "journal_buys": journal_buys,
                    "decision_replay": self.config.decision_replay,
                    "candidates_data": candidates_data,
                })

        return configs

    def _load_journal_buys(self, date: str) -> list[dict]:
        """Load BUY signals from journal for a date."""
        import glob

        buys = []
        seen = set()
        project_root = Path(self.config.data_dir).resolve().parent.parent.parent
        journals = glob.glob(str(project_root / "data" / "journals" / f"journal_{date}_*.jsonl"))

        for f in journals:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("action") in ("BUY", "STRONG_BUY"):
                            ticker = entry.get("ticker", "")
                            if ticker not in seen:
                                seen.add(ticker)
                                buys.append({
                                    "ticker": ticker,
                                    "entry_price": entry.get("entry_price", 0),
                                    "stop_loss": entry.get("stop_loss", 0),
                                    "confidence": entry.get("confidence", 0),
                                    "gap_pct": entry.get("gap_pct", 0),
                                })
                    except json.JSONDecodeError:
                        continue

        return buys

    def run(self) -> list[dict]:
        """Run all configurations, return results."""
        configs = self.generate_configs()
        logger.info(
            "Sweep: %d configs (%d dates x %d param combos), %d workers",
            len(configs), len(self.config.dates),
            len(configs) // max(len(self.config.dates), 1),
            self.config.max_workers,
        )

        t0 = time.perf_counter()
        results = []

        if self.config.max_workers <= 1 or len(configs) <= 1:
            # Sequential (for debugging)
            for cfg in configs:
                results.append(_run_single_sim(cfg))
        else:
            # Parallel
            with ProcessPoolExecutor(max_workers=self.config.max_workers) as pool:
                futures = {pool.submit(_run_single_sim, cfg): cfg for cfg in configs}
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        cfg = futures[future]
                        logger.error("Sim %s failed: %s", cfg.get("id"), e)
                        results.append({
                            "config_id": cfg.get("id"),
                            "date": cfg.get("date"),
                            "params": cfg.get("param_overrides"),
                            "error": str(e),
                        })

        elapsed = time.perf_counter() - t0
        logger.info(
            "Sweep complete: %d results in %.1fs (%.1f sims/sec)",
            len(results), elapsed, len(results) / max(elapsed, 0.001),
        )

        return results

    @staticmethod
    def results_to_table(results: list[dict]) -> str:
        """Format results as a text table with bootstrap CI."""
        if not results:
            return "No results."

        lines = []
        lines.append(
            f"{'Config ID':<45} {'P&L':>8} {'#':>3} {'Win%':>5} "
            f"{'PF':>5} {'CI Low':>7} {'CI Hi':>7} {'New':>3} {'Trades'}"
        )
        lines.append("-" * 120)

        # Sort by CI lower bound (statistically robust ranking)
        for r in sorted(results, key=lambda x: x.get("pf_ci_lower", 0), reverse=True):
            config_id = r.get("config_id", "?")[:45]
            pnl = r.get("sim_pnl", 0)
            n_trades = r.get("sim_trade_count", len(r.get("sim_trades", [])))
            win_rate = r.get("sim_win_rate", 0)
            pf = r.get("pf_point", 0)
            ci_lo = r.get("pf_ci_lower", 0)
            ci_hi = r.get("pf_ci_upper", 0)
            new_buys = r.get("new_buys", 0)

            trade_details = []
            for t in r.get("sim_trades", []):
                status = "W" if t.get("pnl", 0) > 0 else "L"
                new_tag = "*" if t.get("is_new_buy") else ""
                trade_details.append(f"{t['ticker']}:{status}{new_tag}")

            lines.append(
                f"{config_id:<45} ${pnl:>+7.2f} {n_trades:>3} {win_rate:>4.0%} "
                f"{pf:>5.2f} [{ci_lo:>5.2f}, {ci_hi:>5.2f}] {new_buys:>3} "
                f"{', '.join(trade_details)}"
            )

        return "\n".join(lines)
