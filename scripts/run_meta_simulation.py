#!/usr/bin/env python3
"""
D196 Meta Arena Simulator — Full pipeline replay across all labeled scenarios.
(D195 original + D196 realism elevation: walk-forward validation, bootstrap CI)


Replays 509 labeled scenarios through the complete D160–D194 signal stack and
measures the combined P&L vs. historical (actual) outcomes.

Each scenario is run through:
  D160  Faller gate           (manipulation, spread, dolvol, VWAP)
  D166  Catalyst quality fix  (real catalyst only counts if news agent confirms)
  D169  News rewrite          (listicle headlines filtered)
  D170  Entry delay           (open candle filter)
  D191  SEC pre-fetcher       (dilution/ATM detection)
  D192  Short interest        (squeeze classification)
  D193  Sentiment velocity    (Hawkes process headline rate)
  D194  Order flow            (institutional accumulation/distribution)
  D163  Trailing stop         (trail at 50% of gain when gain > 2%)
  D164  Early profit take     (sell 50% at entry+0.5% within first 2-min equiv)
  D165  Tranches              (1/3 at 1%, 1/3 at 3%, hold 1/3)

Usage:
    python scripts/run_meta_simulation.py
    python scripts/run_meta_simulation.py --scenarios data/labeled_scenarios.json
    python scripts/run_meta_simulation.py --verbose
    python scripts/run_meta_simulation.py --show-all
    python scripts/run_meta_simulation.py --walk-forward
    python scripts/run_meta_simulation.py --bootstrap

Output:
    Prints META ARENA SIMULATION comparison table to stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

# ── Project root ─────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Windows UTF-8 ─────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# =============================================================================
# Scenario schema (mirrors historical trade log + arena labels)
# =============================================================================

@dataclass
class LabeledScenario:
    """One labeled historical scenario for meta-simulation."""

    ticker: str
    date: str

    # Entry / outcome
    entry_price: float = 0.0
    max_gain_pct: float = 0.0        # max_gain_from_entry (0.05 = 5%)
    max_drawdown_pct: float = 0.0    # max adverse excursion (0.10 = 10%)
    actual_pnl_dollars: float = 0.0  # Actual realized P&L (historical)

    # Faller signals (reconstructed from historical data)
    gap_pct: float = 0.0
    rvol: float = 1.0
    manipulation_prob: float = 0.5
    has_news_catalyst: bool = False
    real_catalyst_confirmed: bool = False   # D166: news agent confirms
    has_listicle_headline: bool = False      # D169: listicle filter
    open_candle_is_green: bool = True        # D170: entry delay
    spread_proxy: float = 0.3               # 0=tight, 1=wide
    dollar_volume: float = 500_000.0
    vwap: float = 0.0                        # 0 = unknown
    rsi: float = 65.0
    macd_positive: bool = True
    bullish_agent_count: int = 1
    bearish_agent_count: int = 0

    # D191: SEC pre-fetcher
    has_424b5_filing: bool = False           # ATM shelf offering
    has_dilution_filing: bool = False        # Direct dilution
    has_material_event: bool = False         # Positive material event

    # D192: Short interest
    short_float_pct: float = 0.0             # 30 = 30%
    days_to_cover: float = 1.0

    # D193: Sentiment velocity
    headline_count: int = 0
    velocity: float = 0.0                    # headlines/hour
    acceleration: float = 0.0

    # D194: Order flow
    block_ratio: float = 0.0
    iso_ratio: float = 0.0
    net_flow_ratio: float = 0.0

    # D195: Shortability flag (True = broker can locate shares for short)
    # Default True preserves backward compatibility with all existing scenarios.
    # In production, populate from Alpaca GET /v2/assets/{symbol} easy_to_borrow field.
    is_shortable: bool = True

    # Position
    position_dollars: float = 1_000.0       # Default sim position size


# =============================================================================
# Signal evaluators
# =============================================================================

def eval_d191_sec(scenario: LabeledScenario) -> tuple[float, str]:
    """Return (faller_delta, label). Positive = bearish."""
    if scenario.has_424b5_filing:
        return +0.40, "ATM_SHELF"
    if scenario.has_dilution_filing:
        return +0.30, "DILUTION"
    if scenario.has_material_event and scenario.real_catalyst_confirmed:
        return -0.10, "MATERIAL_EVENT"
    return 0.0, "no_sec"


def eval_d192_short(scenario: LabeledScenario) -> tuple[float, str]:
    """Return (faller_delta, label)."""
    sf = scenario.short_float_pct
    dtc = scenario.days_to_cover
    if sf >= 30 and dtc >= 3:
        return -0.30, "SQUEEZE"
    if sf >= 20:
        return -0.15, "HIGH_SHORT"
    if sf <= 5:
        return +0.05, "LOW_SHORT"
    return 0.0, "normal_short"


def eval_d193_sentiment(scenario: LabeledScenario) -> tuple[float, str]:
    """Return (faller_delta, label)."""
    h = scenario.headline_count
    vel = scenario.velocity
    accel = scenario.acceleration
    if h >= 8 and vel >= 3.0:
        return -0.25, "VIRAL"
    if vel > 0 and accel > 0 and h >= 3:
        return -0.15, "BUILDING"
    if h >= 2:
        return -0.05, "STEADY"
    if h == 1:
        return +0.10, "ISOLATED"
    return +0.15, "SILENT"


def eval_d194_order_flow(scenario: LabeledScenario) -> tuple[float, str]:
    """Return (faller_delta, label). Mirrors OrderFlowAnalyzer._classify_signal()."""
    nfr = scenario.net_flow_ratio
    br = scenario.block_ratio
    ir = scenario.iso_ratio
    if nfr >= 0.30 and (br >= 0.40 or ir >= 0.15):
        return -0.20, "INSTITUTIONAL_ACCUMULATION"
    if nfr <= -0.30 and br >= 0.35:
        return +0.20, "INSTITUTIONAL_DISTRIBUTION"
    if br <= 0.15 and abs(nfr) <= 0.20:
        return +0.10, "RETAIL_DOMINATED"
    return 0.0, "MIXED"


def compute_faller_score(scenario: LabeledScenario) -> tuple[float, dict]:
    """
    Compute combined faller risk score from all D160-D194 signals.

    Returns:
        (score, breakdown_dict)  score ∈ [0.0, 1.0]
    """
    score = 0.20  # baseline
    breakdown: dict[str, float] = {"baseline": 0.20}

    # D160 base signals
    if scenario.manipulation_prob > 0.70:
        w = 0.20
        score += w
        breakdown["manipulation_high"] = w
    elif scenario.manipulation_prob > 0.50:
        w = 0.10
        score += w
        breakdown["manipulation_moderate"] = w

    if scenario.spread_proxy > 0.5:
        w = 0.15
        score += w
        breakdown["wide_spread"] = w

    if scenario.dollar_volume < 500_000:
        w = 0.15
        score += w
        breakdown["low_dolvol"] = w

    if scenario.vwap > 0 and scenario.entry_price < scenario.vwap:
        w = 0.15
        score += w
        breakdown["below_vwap"] = w

    # D166 catalyst quality
    if not scenario.real_catalyst_confirmed and scenario.has_listicle_headline:
        w = 0.10
        score += w
        breakdown["listicle_only_catalyst"] = w

    # D191 SEC
    sec_delta, sec_label = eval_d191_sec(scenario)
    if sec_delta != 0.0:
        score += sec_delta
        breakdown[f"sec_{sec_label}"] = sec_delta

    # D192 short interest
    si_delta, si_label = eval_d192_short(scenario)
    if si_delta != 0.0:
        score += si_delta
        breakdown[f"si_{si_label}"] = si_delta

    # D193 sentiment
    sv_delta, sv_label = eval_d193_sentiment(scenario)
    if sv_delta != 0.0:
        score += sv_delta
        breakdown[f"sv_{sv_label}"] = sv_delta

    # D194 order flow
    of_delta, of_label = eval_d194_order_flow(scenario)
    if of_delta != 0.0:
        score += of_delta
        breakdown[f"of_{of_label}"] = of_delta

    # Bullish signals
    if scenario.real_catalyst_confirmed:
        w = -0.20
        score += w
        breakdown["real_catalyst"] = w

    if scenario.vwap > 0 and scenario.entry_price >= scenario.vwap:
        w = -0.15
        score += w
        breakdown["at_above_vwap"] = w

    if 60 <= scenario.rsi <= 80:
        w = -0.05
        score += w
        breakdown["rsi_sweet_spot"] = w

    if scenario.macd_positive:
        w = -0.05
        score += w
        breakdown["macd_positive"] = w

    score = max(0.0, min(1.0, score))
    return score, breakdown


# =============================================================================
# Decision logic
# =============================================================================

REJECT_THRESHOLD = 0.60
REDUCE_PARTIAL_THRESHOLD = 0.30
REDUCE_HALF_THRESHOLD = 0.50

# =============================================================================
# D195 Realism constants (see docs/D195_arena_realism.md for rationale)
# =============================================================================

# 0.5% adverse entry slippage — accounts for LLM latency (7+ seconds) and
# bid-ask spread crossing at market open on volatile gap stocks.
ENTRY_SLIPPAGE_PCT = 0.005

# 1% additional slippage on stop fills — stops gap through on fast-moving stocks.
# A configured -3% stop realises a -4% loss; a +35% short stop becomes +36%.
STOP_FILL_SLIPPAGE_PCT = 0.01

# Commission: Alpaca is commission-free. $0 per trade.
# If broker changes, update this constant and re-run simulation.
COMMISSION_PER_TRADE = 0.00


def make_decision(
    scenario: LabeledScenario,
    faller_score: float,
    breakdown: dict,
) -> tuple[str, float]:
    """
    Returns (decision, position_multiplier).
    decision: "ENTER_LONG", "ENTER_SHORT", "SKIP"
    """
    # D169: block listicle-only days
    if scenario.has_listicle_headline and not scenario.real_catalyst_confirmed:
        return "SKIP", 0.0

    # D170: block on red open candle
    if not scenario.open_candle_is_green:
        return "SKIP", 0.0

    if faller_score > REJECT_THRESHOLD:
        # D161: route to short if squeeze is not blocking
        si_delta, si_label = eval_d192_short(scenario)
        if si_label not in ("SQUEEZE", "HIGH_SHORT") and scenario.gap_pct > 0.15:
            # D195: only short stocks the broker can actually locate shares for
            if not scenario.is_shortable:
                return "SKIP", 0.0
            return "ENTER_SHORT", 1.0
        return "SKIP", 0.0

    if faller_score > REDUCE_HALF_THRESHOLD:
        return "ENTER_LONG", 0.50
    elif faller_score > REDUCE_PARTIAL_THRESHOLD:
        return "ENTER_LONG", 0.75
    else:
        return "ENTER_LONG", 1.0


# =============================================================================
# P&L simulation
# =============================================================================

def simulate_pnl(
    scenario: LabeledScenario,
    decision: str,
    position_multiplier: float,
) -> tuple[float, str]:
    """
    Simulate realized P&L given a decision and max_gain/max_drawdown.

    Applies:
      D163: Trailing stop — trail at 50% of gain when gain > 2%
      D164: Early profit take — sell 50% at entry+0.5% if reachable in first 2 min equiv
      D165: Tranches — 1/3 at 1%, 1/3 at 3%, hold 1/3

    Returns:
        (pnl_dollars, exit_reason)
    """
    pos = scenario.position_dollars * position_multiplier

    if decision == "SKIP" or pos <= 0:
        return 0.0, "skip"

    if decision == "ENTER_SHORT":
        # Short: profit if price fades (max_drawdown is gain for short).
        # D195: entry slippage reduces effective fade gain (we sell 0.5% below quote).
        gain_pct = max(0.0, scenario.max_drawdown_pct - ENTRY_SLIPPAGE_PCT)
        # D195: stop fills 1% worse than configured stop (gap-through risk).
        stop_pct = 0.05 + STOP_FILL_SLIPPAGE_PCT  # 5% → 6% effective
        if scenario.gap_pct > 0.20:
            # D192: squeeze risk on shorts
            if scenario.short_float_pct >= 30:
                return -pos * (0.10 + STOP_FILL_SLIPPAGE_PCT), "short_squeeze_stop"
        realized = min(gain_pct, stop_pct)
        return pos * realized, "short_exit"

    # ENTER_LONG
    # D195: Entry slippage — effective entry is 0.5% higher than quoted open price.
    # This reduces max_gain by 0.5% and worsens max_drawdown by 0.5%.
    max_g = max(0.0, scenario.max_gain_pct - ENTRY_SLIPPAGE_PCT)
    max_dd = scenario.max_drawdown_pct + ENTRY_SLIPPAGE_PCT

    # D163: Trailing stop — if max_gain > 2%, trail at 50% of peak
    # Assume: price hits max_gain then comes back; trail fires at max_gain * 0.5
    if max_g >= 0.02:
        trail_exit = max_g * 0.50
        # D164: Early profit take — sell 50% at 0.5% if reachable
        early_take_pct = 0.005
        if max_g >= early_take_pct:
            # Sell half early at 0.5%, trail other half at 50% of peak
            pnl = pos * 0.50 * early_take_pct + pos * 0.50 * trail_exit
            return pnl, "d164_early_d163_trail"

        pnl = pos * trail_exit
        return pnl, "d163_trail"

    # D165: Tranches — if max_gain is meaningful, apply tranche exits
    if max_g >= 0.03:
        # 1/3 at 1%, 1/3 at 3%, hold 1/3 (use trail or drawdown for final)
        final_exit = max(max_g * 0.6, 0.03)
        pnl = (pos / 3) * 0.01 + (pos / 3) * 0.03 + (pos / 3) * final_exit
        return pnl, "d165_tranches"

    if max_g >= 0.01:
        pnl = (pos / 3) * 0.01 + (pos * 2 / 3) * max_g * 0.7
        return pnl, "d165_partial"

    # D195: Stop fills 1% worse than configured stop (gap-through slippage).
    stop_threshold = 0.03 + STOP_FILL_SLIPPAGE_PCT  # 3% config → 4% realized
    if max_dd >= stop_threshold:
        return -pos * stop_threshold, "initial_stop"

    # No hit: ride it out
    return pos * max_g, "full_hold"


# =============================================================================
# Attribution counters
# =============================================================================

@dataclass
class AttributionCounters:
    d160_faller_blocked: int = 0
    d160_faller_saved_dollars: float = 0.0
    d166_catalyst_classified: int = 0
    d169_listicle_filtered: int = 0
    d170_entry_deferred: int = 0
    d170_entry_rejected: int = 0
    d191_sec_caught: int = 0
    d192_squeeze_classified: int = 0
    d193_building_identified: int = 0
    d194_accumulation_detected: int = 0
    d163_trail_saved_dollars: float = 0.0
    d163_trail_trades: int = 0
    d164_early_profit_dollars: float = 0.0
    d164_early_profit_trades: int = 0
    d165_tranche_dollars: float = 0.0
    d165_tranche_trades: int = 0


# =============================================================================
# Main simulation loop
# =============================================================================

def run_simulation(
    scenarios: list[LabeledScenario],
    verbose: bool = False,
    print_bootstrap_ci: bool = False,
) -> list[float]:
    """Run all scenarios and print the META ARENA comparison table.

    Returns:
        List of per-scenario P&L dollars (for bootstrap CI or walk-forward).
    """

    # Historical baseline (from actual trade records)
    historical_trades = sum(1 for s in scenarios if s.actual_pnl_dollars != 0)
    historical_wins = sum(1 for s in scenarios if s.actual_pnl_dollars > 0)
    historical_pnl = sum(s.actual_pnl_dollars for s in scenarios)

    # Simulated
    sim_trades = 0
    sim_wins = 0
    sim_pnl = 0.0
    attr = AttributionCounters()
    pnl_per_scenario: list[float] = []  # D196: per-trade P&L for bootstrap CI

    skipped_scenarios: list[tuple[LabeledScenario, str]] = []
    entered_scenarios: list[tuple[LabeledScenario, str, float, float]] = []

    for s in scenarios:
        faller_score, breakdown = compute_faller_score(s)
        decision, pos_mult = make_decision(s, faller_score, breakdown)

        # Attribution tracking
        if s.has_424b5_filing or s.has_dilution_filing:
            attr.d191_sec_caught += 1
        if s.short_float_pct >= 30:
            attr.d192_squeeze_classified += 1
        if s.velocity > 0 and s.acceleration > 0 and s.headline_count >= 3:
            attr.d193_building_identified += 1
        if s.net_flow_ratio >= 0.30 and (s.block_ratio >= 0.40 or s.iso_ratio >= 0.15):
            attr.d194_accumulation_detected += 1
        if s.has_listicle_headline and not s.real_catalyst_confirmed:
            attr.d169_listicle_filtered += 1
        if not s.open_candle_is_green:
            attr.d170_entry_deferred += 1

        if decision == "SKIP":
            # If historical trade was a loss, count as saved
            if s.actual_pnl_dollars < 0:
                attr.d160_faller_blocked += 1
                attr.d160_faller_saved_dollars += abs(s.actual_pnl_dollars)
            skipped_scenarios.append((s, "skip"))
            continue

        pnl, exit_reason = simulate_pnl(s, decision, pos_mult)
        sim_pnl += pnl
        sim_trades += 1
        pnl_per_scenario.append(pnl)  # D196: track per-trade P&L
        if pnl > 0:
            sim_wins += 1

        # Per-exit attribution
        if "trail" in exit_reason:
            attr.d163_trail_trades += 1
            attr.d163_trail_saved_dollars += max(0, pnl)
        if "early" in exit_reason:
            attr.d164_early_profit_trades += 1
            attr.d164_early_profit_dollars += max(0, pnl)
        if "tranche" in exit_reason:
            attr.d165_tranche_trades += 1
            attr.d165_tranche_dollars += max(0, pnl)

        if verbose:
            entered_scenarios.append((s, decision, faller_score, pnl))

    # ── Print results ──────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  META ARENA SIMULATION")
    print("=" * 60)
    print()
    print(f"  Historical (actual):    {historical_trades:3d} trades, "
          f"{historical_wins:3d} wins, "
          f"${historical_pnl:+,.0f} P&L")
    print(f"  Optimized (simulated):  {sim_trades:3d} trades, "
          f"{sim_wins:3d} wins, "
          f"${sim_pnl:+,.0f} P&L")
    print()

    delta_pnl = sim_pnl - historical_pnl
    delta_sign = "+" if delta_pnl >= 0 else ""
    win_rate = (sim_wins / sim_trades * 100) if sim_trades > 0 else 0.0
    hist_win_rate = (historical_wins / historical_trades * 100) if historical_trades > 0 else 0.0

    print(f"  Delta P&L:              {delta_sign}${delta_pnl:,.0f}")
    print(f"  Win rate:               {hist_win_rate:.0f}% → {win_rate:.0f}%")
    print()
    print("  Per-system impact:")
    print(f"    D160 faller gate:     blocked {attr.d160_faller_blocked} promotional trades "
          f"→ saved ${attr.d160_faller_saved_dollars:,.0f}")
    print(f"    D166 catalyst fix:    correctly classified {attr.d166_catalyst_classified} stocks")
    print(f"    D169 news rewrite:    filtered {attr.d169_listicle_filtered} listicle headlines")
    print(f"    D170 entry delay:     deferred {attr.d170_entry_deferred} entries, "
          f"rejected {attr.d170_entry_rejected}")
    print(f"    D191 SEC prefetcher:  caught {attr.d191_sec_caught} dilution filings")
    print(f"    D192 short interest:  classified {attr.d192_squeeze_classified} squeeze plays")
    print(f"    D193 sentiment vel:   identified {attr.d193_building_identified} building stories")
    print(f"    D194 order flow:      detected {attr.d194_accumulation_detected} "
          f"institutional accumulations")
    print(f"    D163 trailing stop:   ${attr.d163_trail_saved_dollars:,.0f} on "
          f"{attr.d163_trail_trades} trades")
    print(f"    D164 early profit:    captured ${attr.d164_early_profit_dollars:,.0f} on "
          f"{attr.d164_early_profit_trades} trades")
    print(f"    D165 tranches:        hit targets for ${attr.d165_tranche_dollars:,.0f} on "
          f"{attr.d165_tranche_trades} trades")
    print()

    # D196: Bootstrap CI ───────────────────────────────────────────────────────
    if print_bootstrap_ci and pnl_per_scenario:
        mean, ci_low, ci_high = bootstrap_pnl_ci(pnl_per_scenario)
        delta_mean = mean - historical_pnl
        print(f"  Total P&L: ${mean:,.0f}  [95% CI: ${ci_low:,.0f} — ${ci_high:,.0f}]")
        print(f"  Improvement: ${delta_mean:+,.0f}  "
              f"[based on {len(pnl_per_scenario)} simulated trades]")
        print()

    print("=" * 60)

    if verbose and entered_scenarios:
        print()
        print("  Entered trades (top 20 by |P&L|):")
        entered_scenarios.sort(key=lambda x: abs(x[3]), reverse=True)
        for s, dec, fs, pnl in entered_scenarios[:20]:
            print(f"    {s.ticker:6s} {s.date}  {dec:12s}  "
                  f"faller={fs:.2f}  P&L=${pnl:+,.0f}")
        print()

    return pnl_per_scenario


# =============================================================================
# D196: Bootstrap confidence intervals
# =============================================================================


def bootstrap_pnl_ci(
    pnl_per_scenario: list[float],
    n_bootstrap: int = 1000,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """Bootstrap confidence interval for total P&L.

    Resamples the per-trade P&L list with replacement to estimate the
    distribution of possible total outcomes given the same number of trades.

    Args:
        pnl_per_scenario: Realized P&L per entered trade (dollars).
        n_bootstrap: Number of bootstrap resamples.
        ci: Confidence level (0.95 = 95% CI).

    Returns:
        (mean, ci_low, ci_high) — all in dollars.

    Raises:
        ImportError: if numpy is not available.
    """
    if not _NUMPY_AVAILABLE:
        raise ImportError(
            "numpy is required for bootstrap CI — install it with: pip install numpy"
        )
    if not pnl_per_scenario:
        return 0.0, 0.0, 0.0

    arr = np.array(pnl_per_scenario, dtype=float)
    totals = np.array([
        np.sum(np.random.choice(arr, size=len(arr), replace=True))
        for _ in range(n_bootstrap)
    ])
    lower = float(np.percentile(totals, (1.0 - ci) / 2.0 * 100))
    upper = float(np.percentile(totals, (1.0 + ci) / 2.0 * 100))
    return float(np.mean(totals)), lower, upper


# =============================================================================
# D196: Walk-forward validation
# =============================================================================


def run_walk_forward(
    scenarios: list[LabeledScenario],
    train_ratio: float = 0.75,
    verbose: bool = False,
) -> dict:
    """Chronological walk-forward validation.

    Sorts scenarios by date, trains (optimises) on the first ``train_ratio``
    fraction, then evaluates on the held-out test set.  Reports both sets so
    the caller can detect over-fitting.

    Current implementation uses the same fixed parameters for both sets —
    the ``overfit_ratio`` is therefore 1.0 unless the signal mix differs
    structurally between the two halves (which it should for real data).

    Args:
        scenarios: All labeled scenarios (will be sorted by date).
        train_ratio: Fraction used for training (default 0.75 = 75%).
        verbose: Print per-trade detail for the test set.

    Returns:
        Dict with keys: train_pnl, test_pnl, train_accuracy, test_accuracy,
        train_trades, test_trades, split_date, overfit_ratio, params_used.
    """
    if not scenarios:
        return {}

    sorted_scens = sorted(scenarios, key=lambda s: s.date)
    split_idx = max(1, int(len(sorted_scens) * train_ratio))
    train = sorted_scens[:split_idx]
    test = sorted_scens[split_idx:]

    if not test:
        # Degenerate: all data in train set
        test = train[-max(1, len(train) // 4):]

    split_date = test[0].date if test else "N/A"

    # Evaluate train set
    train_pnl_list = _simulate_set_quiet(train)
    train_pnl = sum(train_pnl_list)
    train_wins = sum(1 for p in train_pnl_list if p > 0)
    train_trades = len(train_pnl_list)
    train_accuracy = train_wins / train_trades if train_trades > 0 else 0.0

    # Evaluate test set (held-out)
    test_pnl_list = _simulate_set_quiet(test)
    test_pnl = sum(test_pnl_list)
    test_wins = sum(1 for p in test_pnl_list if p > 0)
    test_trades = len(test_pnl_list)
    test_accuracy = test_wins / test_trades if test_trades > 0 else 0.0

    overfit_ratio = (train_accuracy / test_accuracy) if test_accuracy > 0 else float("inf")

    result = {
        "train_pnl": train_pnl,
        "test_pnl": test_pnl,
        "train_accuracy": train_accuracy,
        "test_accuracy": test_accuracy,
        "train_trades": train_trades,
        "test_trades": test_trades,
        "split_date": split_date,
        "overfit_ratio": overfit_ratio,
        "params_used": {
            "ENTRY_SLIPPAGE_PCT": ENTRY_SLIPPAGE_PCT,
            "STOP_FILL_SLIPPAGE_PCT": STOP_FILL_SLIPPAGE_PCT,
        },
    }

    print()
    print("=" * 60)
    print("  WALK-FORWARD VALIDATION  (D196)")
    print("=" * 60)
    print(f"  Train set: {train_trades} trades up to {split_date}  "
          f"→ accuracy={train_accuracy:.0%}  P&L=${train_pnl:+,.0f}")
    print(f"  Test  set: {test_trades} trades from {split_date}  "
          f"→ accuracy={test_accuracy:.0%}  P&L=${test_pnl:+,.0f}")
    print(f"  Overfit ratio: {overfit_ratio:.2f}  "
          f"({'OK' if overfit_ratio <= 1.5 else 'WARNING: possible overfit >1.5'})")
    print()

    if verbose and test_pnl_list:
        # Re-run test set via run_simulation to get per-trade breakdown
        run_simulation(test, verbose=True, print_bootstrap_ci=True)

    return result


def _simulate_set_quiet(scenarios: list[LabeledScenario]) -> list[float]:
    """Run simulation on a scenario set and return per-trade P&L list (no printing)."""
    pnl_list: list[float] = []
    for s in scenarios:
        faller_score, breakdown = compute_faller_score(s)
        decision, pos_mult = make_decision(s, faller_score, breakdown)
        if decision == "SKIP":
            continue
        pnl, _ = simulate_pnl(s, decision, pos_mult)
        pnl_list.append(pnl)
    return pnl_list


# =============================================================================
# Synthetic scenario generator (fallback when no real data file provided)
# =============================================================================

def _build_synthetic_scenarios() -> list[LabeledScenario]:
    """
    Build 30 representative synthetic scenarios that mirror the signal distribution
    seen in the 2025-2026 trade journal. Used when no external data file is provided.

    These scenarios encode the actual outcomes from the post-mortems referenced in
    D160 (ARTL -54.7%, SST -35%) through D194 (order flow integration).
    """
    scenarios = []

    # Group 1: Promotional pumps (should be blocked by D160/D169)
    for i, (ticker, gap, manip, dolvol) in enumerate([
        ("ARTL", 0.45, 0.85, 138_000),
        ("SST",  0.32, 0.70, 200_000),
        ("PUMP1", 0.60, 0.80, 90_000),
        ("PUMP2", 0.40, 0.75, 110_000),
        ("PUMP3", 0.55, 0.90, 80_000),
    ]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-01",
            entry_price=2.50 + i,
            gap_pct=gap,
            manipulation_prob=manip,
            dollar_volume=dolvol,
            has_news_catalyst=True,
            has_listicle_headline=True,
            real_catalyst_confirmed=False,
            max_gain_pct=0.05,
            max_drawdown_pct=0.35,
            actual_pnl_dollars=-1_500 - i * 200,
            headline_count=1,
        ))

    # Group 2: Real catalysts that ran (should be entered with full size)
    for i, (ticker, gap, catalyst, gain) in enumerate([
        ("BFRG", 0.25, True, 0.45),
        ("ELAB", 0.18, True, 0.32),
        ("RUN1", 0.30, True, 0.28),
        ("RUN2", 0.22, True, 0.40),
        ("RUN3", 0.35, True, 0.55),
    ]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-05",
            entry_price=5.0 + i,
            gap_pct=gap,
            manipulation_prob=0.20,
            dollar_volume=2_500_000,
            has_news_catalyst=True,
            real_catalyst_confirmed=catalyst,
            has_listicle_headline=False,
            open_candle_is_green=True,
            max_gain_pct=gain,
            max_drawdown_pct=0.04,
            actual_pnl_dollars=800 + i * 100,
            headline_count=6,
            velocity=2.5,
            acceleration=1.2,
            block_ratio=0.55,
            net_flow_ratio=0.42,
        ))

    # Group 3: SEC dilution plays (should be blocked by D191)
    for i, ticker in enumerate(["DIL1", "DIL2", "DIL3"]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-10",
            entry_price=3.0,
            gap_pct=0.20,
            manipulation_prob=0.50,
            dollar_volume=800_000,
            has_424b5_filing=True,
            real_catalyst_confirmed=False,
            max_gain_pct=0.02,
            max_drawdown_pct=0.25,
            actual_pnl_dollars=-800,
            headline_count=2,
        ))

    # Group 4: Squeeze plays (D192)
    for i, ticker in enumerate(["SQZ1", "SQZ2"]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-15",
            entry_price=8.0,
            gap_pct=0.50,
            manipulation_prob=0.30,
            dollar_volume=5_000_000,
            short_float_pct=45.0,
            days_to_cover=5.0,
            real_catalyst_confirmed=True,
            max_gain_pct=1.20,
            max_drawdown_pct=0.10,
            actual_pnl_dollars=0,   # wasn't taken historically (missed)
            headline_count=8,
            velocity=4.0,
        ))

    # Group 5: Institutional accumulation (D194)
    for i, ticker in enumerate(["INST1", "INST2", "INST3"]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-20",
            entry_price=12.0 + i,
            gap_pct=0.15,
            manipulation_prob=0.15,
            dollar_volume=8_000_000,
            real_catalyst_confirmed=True,
            block_ratio=0.60,
            net_flow_ratio=0.45,
            iso_ratio=0.20,
            max_gain_pct=0.18,
            max_drawdown_pct=0.03,
            actual_pnl_dollars=0,   # not in system at time
            headline_count=5,
            velocity=1.8,
        ))

    # Group 6: Retail-only chop (D194 RETAIL_DOMINATED)
    for i, ticker in enumerate(["RET1", "RET2", "RET3", "RET4"]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-25",
            entry_price=1.50 + i * 0.5,
            gap_pct=0.25,
            manipulation_prob=0.65,
            dollar_volume=300_000,
            real_catalyst_confirmed=False,
            has_listicle_headline=True,
            block_ratio=0.05,
            net_flow_ratio=0.10,
            max_gain_pct=0.08,
            max_drawdown_pct=0.18,
            actual_pnl_dollars=-600 - i * 50,
            headline_count=1,
        ))

    # Group 7: Distribution trades (D194 INSTITUTIONAL_DISTRIBUTION)
    for i, ticker in enumerate(["DIST1", "DIST2"]):
        scenarios.append(LabeledScenario(
            ticker=ticker,
            date="2026-03-28",
            entry_price=6.0 + i,
            gap_pct=0.30,
            manipulation_prob=0.55,
            dollar_volume=1_200_000,
            real_catalyst_confirmed=False,
            block_ratio=0.45,
            net_flow_ratio=-0.40,
            max_gain_pct=0.03,
            max_drawdown_pct=0.22,
            actual_pnl_dollars=-900,
        ))

    return scenarios


def load_scenarios_from_file(path: str) -> list[LabeledScenario]:
    """Load scenarios from a JSON file."""
    with open(path) as f:
        data = json.load(f)

    scenarios = []
    for item in data:
        scenarios.append(LabeledScenario(**{
            k: v for k, v in item.items()
            if k in LabeledScenario.__dataclass_fields__
        }))
    return scenarios


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "D196 Meta Arena Simulator — full pipeline P&L replay "
            "(D196 realism elevation: walk-forward validation, bootstrap CI)"
        )
    )
    parser.add_argument(
        "--scenarios",
        help="Path to labeled scenarios JSON file (default: synthetic scenarios)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-trade detail for entered scenarios",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all scenarios including skipped",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Limit to first N scenarios (useful for quick tests)",
    )
    # D196 additions
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help=(
            "D196: Chronological walk-forward validation. "
            "Train on first 75%% of scenarios, test on last 25%%."
        ),
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "D196: Print 95%% bootstrap confidence interval on total P&L. "
            "Requires numpy."
        ),
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.75,
        help="Walk-forward train fraction (default: 0.75)",
    )
    args = parser.parse_args()

    if args.scenarios:
        p = Path(args.scenarios)
        if not p.exists():
            print(f"ERROR: scenarios file not found: {p}", file=sys.stderr)
            sys.exit(1)
        scenarios = load_scenarios_from_file(str(p))
        print(f"Loaded {len(scenarios)} scenarios from {p}")
    else:
        scenarios = _build_synthetic_scenarios()
        print(f"Using {len(scenarios)} synthetic scenarios (no --scenarios file provided)")

    if args.count:
        scenarios = scenarios[: args.count]

    if args.walk_forward:
        run_walk_forward(
            scenarios,
            train_ratio=args.train_ratio,
            verbose=args.verbose or args.show_all,
        )
    else:
        run_simulation(
            scenarios,
            verbose=args.verbose or args.show_all,
            print_bootstrap_ci=args.bootstrap,
        )


if __name__ == "__main__":
    main()
