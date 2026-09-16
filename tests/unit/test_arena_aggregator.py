"""Unit tests for src/production_arena/aggregator.py.

Uses synthetic ProductionVerdict fixtures only — does NOT depend on a
real pipeline_runner_fn (the sweep test stubs one). The goal of this
suite is to lock the math/contract of:

  - aggregate_by_gate            (gate-bucket counts, BUY/ERROR sentinels)
  - aggregate_by_day             (per-day rollup with ticker-set serialization)
  - simulate_session_pnl         (equity curve, max_concurrent throttle, sharpe)
  - counterfactual_sweep         (Cartesian product, stub runner_fn)
  - _max_drawdown_pct            (closed-form DD on a known curve)
"""

from __future__ import annotations

import math

import pytest

from src.production_arena.aggregator import (
    _annualized_sharpe,
    _max_drawdown_pct,
    aggregate_by_day,
    aggregate_by_gate,
    counterfactual_sweep,
    simulate_session_pnl,
)
from src.production_arena.types import (
    ArenaConfig,
    LabeledOutcome,
    ProductionVerdict,
    Scenario,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _buy(
    ticker: str,
    date: str,
    pnl_pct: float,
    mfe_pct: float | None = None,
    mfcs: float = 0.5,
) -> ProductionVerdict:
    return ProductionVerdict(
        ticker=ticker,
        session_date=date,
        decision="BUY",
        mfcs=mfcs,
        would_be_pnl_pct=pnl_pct,
        mfe_pct=mfe_pct if mfe_pct is not None else pnl_pct,
    )


def _no_trade(ticker: str, date: str, gate: str | None) -> ProductionVerdict:
    return ProductionVerdict(
        ticker=ticker,
        session_date=date,
        decision="NO_TRADE",
        gate_rejected=gate,
        mfcs=0.1,
    )


def _error(ticker: str, date: str, msg: str = "boom") -> ProductionVerdict:
    return ProductionVerdict(
        ticker=ticker,
        session_date=date,
        decision="ERROR",
        error_msg=msg,
    )


def _scenario(ticker: str, date: str) -> Scenario:
    return Scenario(
        ticker=ticker,
        session_date=date,
        premarket_features={"gap_pct": 0.10, "price": 5.0, "dolvol": 5_000_000},
        minute_bars=tuple(),
        labeled_outcome=LabeledOutcome(),
    )


# ── 1. aggregate_by_gate ───────────────────────────────────────────────


def test_aggregate_by_gate_counts_correctly():
    verdicts = [
        _buy("AAA", "2026-04-01", 0.05),
        _buy("BBB", "2026-04-01", -0.03),
        _no_trade("CCC", "2026-04-01", "adaptive_router.py:149"),
        _no_trade("DDD", "2026-04-01", "adaptive_router.py:149"),
        _error("EEE", "2026-04-01"),
    ]
    counts = aggregate_by_gate(verdicts)

    assert counts["__buy__"] == 2
    assert counts["__error__"] == 1
    assert counts["adaptive_router.py:149"] == 2
    # Total reconciles with the input length.
    assert sum(counts.values()) == len(verdicts)


def test_aggregate_by_gate_handles_unknown_gate_sentinel():
    """NO_TRADE without a gate_rejected goes under '__unknown_gate__'."""
    verdicts = [_no_trade("AAA", "2026-04-01", None)]
    counts = aggregate_by_gate(verdicts)
    assert counts["__unknown_gate__"] == 1


# ── 2. aggregate_by_day ────────────────────────────────────────────────


def test_aggregate_by_day_rollup():
    verdicts = [
        # Day 1: 1 BUY, 1 NO_TRADE
        _buy("AAA", "2026-04-01", 0.04, mfe_pct=0.06),
        _no_trade("BBB", "2026-04-01", "adaptive_router.py:149"),
        # Day 2: 1 BUY, 1 ERROR, 1 NO_TRADE
        _buy("CCC", "2026-04-02", -0.02, mfe_pct=0.03),
        _error("DDD", "2026-04-02"),
        _no_trade("EEE", "2026-04-02", "orchestrator.py:1320"),
    ]
    out = aggregate_by_day(verdicts)

    assert set(out.keys()) == {"2026-04-01", "2026-04-02"}

    d1 = out["2026-04-01"]
    assert d1["total"] == 2
    assert d1["buy"] == 1
    assert d1["error"] == 0
    assert d1["pnl_pct_sum"] == pytest.approx(0.04)
    assert d1["mfe_pct_sum"] == pytest.approx(0.06)
    assert d1["reject_by_gate"] == {"adaptive_router.py:149": 1}
    # Ticker set must be serialized as a sorted list.
    assert d1["tickers_evaluated"] == ["AAA", "BBB"]
    assert isinstance(d1["tickers_evaluated"], list)

    d2 = out["2026-04-02"]
    assert d2["total"] == 3
    assert d2["buy"] == 1
    assert d2["error"] == 1
    assert d2["pnl_pct_sum"] == pytest.approx(-0.02)
    assert d2["mfe_pct_sum"] == pytest.approx(0.03)
    assert d2["reject_by_gate"] == {"orchestrator.py:1320": 1}
    assert d2["tickers_evaluated"] == ["CCC", "DDD", "EEE"]


# ── 3-5. simulate_session_pnl ──────────────────────────────────────────


def test_simulate_pnl_three_trades():
    """Three known BUYs at $142K equity, 2% sizing → known ending equity.

    position_size = $142,000 * 0.02 = $2,840 per trade.
      Trade 1: +5%  →  +$142.00
      Trade 2: -2%  →   -$56.80
      Trade 3: +3%  →   +$85.20
    Net P&L = $170.40, ending equity = $142,170.40.
    """
    verdicts = [
        _buy("AAA", "2026-04-01", 0.05),
        _buy("BBB", "2026-04-02", -0.02),
        _buy("CCC", "2026-04-03", 0.03),
    ]
    result = simulate_session_pnl(
        verdicts,
        starting_equity=142_000.0,
        kelly_tier_pct=0.02,
        max_concurrent=3,
    )

    assert result["starting_equity"] == 142_000.0
    assert result["ending_equity"] == pytest.approx(142_170.40, abs=1e-6)
    assert result["n_trades"] == 3
    assert result["win_rate"] == pytest.approx(2 / 3)
    assert result["total_pnl_pct"] == pytest.approx(170.40 / 142_000.0)
    # Three daily observations is below the 5-day Sharpe floor.
    assert result["sharpe_annualized"] is None
    assert len(result["daily_pnl"]) == 3
    assert result["daily_pnl"][0]["date"] == "2026-04-01"


def test_simulate_pnl_max_concurrent_throttles():
    """Five BUYs on the same day with max_concurrent=3 should open exactly 3."""
    verdicts = [
        _buy("AAA", "2026-04-01", 0.10),
        _buy("BBB", "2026-04-01", 0.10),
        _buy("CCC", "2026-04-01", 0.10),
        _buy("DDD", "2026-04-01", 0.10),  # throttled
        _buy("EEE", "2026-04-01", 0.10),  # throttled
    ]
    result = simulate_session_pnl(
        verdicts,
        starting_equity=142_000.0,
        kelly_tier_pct=0.02,
        max_concurrent=3,
    )

    assert result["n_trades"] == 3
    # Only 3 trades opened at $2,840 each at +10% = $284 each → +$852 total.
    assert result["ending_equity"] == pytest.approx(142_000.0 + 3 * 2_840.0 * 0.10)


def test_simulate_pnl_handles_zero_trades():
    """Empty input → ending == starting, sharpe is None, no trades."""
    result = simulate_session_pnl(
        [],
        starting_equity=142_000.0,
        kelly_tier_pct=0.02,
        max_concurrent=3,
    )
    assert result["ending_equity"] == 142_000.0
    assert result["n_trades"] == 0
    assert result["win_rate"] == 0.0
    assert result["sharpe_annualized"] is None
    assert result["max_drawdown_pct"] == 0.0
    assert result["daily_pnl"] == []


# ── 6. Sharpe sanity check ─────────────────────────────────────────────


def test_sharpe_calculation_basic():
    """Daily returns [0.01, -0.005, 0.02, -0.01, 0.015] → known Sharpe.

    mean = 0.006
    stdev (sample) = statistics.stdev of the list
    sharpe_annualized = (mean/stdev) * sqrt(252)
    """
    daily = [0.01, -0.005, 0.02, -0.01, 0.015]
    sharpe = _annualized_sharpe(daily)
    assert sharpe is not None

    import statistics as _stats

    expected = (_stats.fmean(daily) / _stats.stdev(daily)) * math.sqrt(252)
    assert sharpe == pytest.approx(expected, rel=1e-9)


def test_sharpe_returns_none_below_5_observations():
    """<5 daily returns → None (insufficient sample)."""
    assert _annualized_sharpe([0.01, 0.02, -0.01, 0.0]) is None


# ── 7. Max drawdown closed-form ────────────────────────────────────────


def test_max_drawdown_calculation():
    """Equity curve [100, 105, 95, 100, 90] → 14.3% DD (15/105)."""
    curve = [100.0, 105.0, 95.0, 100.0, 90.0]
    dd = _max_drawdown_pct(curve)
    # 15 / 105 = 0.142857...
    assert dd == pytest.approx(15.0 / 105.0, abs=1e-9)
    assert dd == pytest.approx(0.142857, abs=1e-4)


def test_max_drawdown_monotone_up_is_zero():
    """Strictly increasing curve has zero drawdown."""
    assert _max_drawdown_pct([100.0, 110.0, 120.0, 130.0]) == 0.0


# ── 8. counterfactual_sweep with stub runner ───────────────────────────


def _stub_pipeline_runner(scenario: Scenario, config: ArenaConfig) -> ProductionVerdict:
    """Toy runner: BUY iff mfcs_buy_threshold is at most 0.30, with a
    fixed +5% return. Otherwise NO_TRADE rejected at a synthetic gate.

    This is just enough policy to exercise the Cartesian product without
    depending on the real orchestrator.
    """
    threshold = config.mfcs_buy_threshold
    if threshold is not None and threshold <= 0.30:
        return ProductionVerdict(
            ticker=scenario.ticker,
            session_date=scenario.session_date,
            decision="BUY",
            mfcs=0.50,
            would_be_pnl_pct=0.05,
            mfe_pct=0.10,
        )
    return ProductionVerdict(
        ticker=scenario.ticker,
        session_date=scenario.session_date,
        decision="NO_TRADE",
        gate_rejected="stub:threshold_too_high",
        mfcs=0.20,
    )


def test_counterfactual_sweep_cartesian_product():
    """2x2 grid → 4 SweepResults; only the low-threshold combos produce trades."""
    scenarios = [
        _scenario("AAA", "2026-04-01"),
        _scenario("BBB", "2026-04-02"),
    ]
    grid = {
        "mfcs_buy_threshold": [0.20, 0.50],
        "instant_reject_max_float": [200_000_000, 2_000_000_000],
    }
    results = counterfactual_sweep(
        scenarios=scenarios,
        pipeline_runner_fn=_stub_pipeline_runner,
        param_grid=grid,
        parallel_workers=1,
    )

    assert len(results) == 4

    by_threshold: dict[float, list] = {0.20: [], 0.50: []}
    for r in results:
        by_threshold[r.param_combo["mfcs_buy_threshold"]].append(r)

    # Low threshold: every scenario buys, +5% per trade, 100% win rate.
    for r in by_threshold[0.20]:
        assert r.n_scenarios == 2
        assert r.n_trades == 2
        assert r.n_winners == 2
        assert r.n_losers == 0
        assert r.win_rate == pytest.approx(1.0)
        assert r.avg_return_pct == pytest.approx(0.05)
        # MFE capture: 0.05 / 0.10 = 0.5 averaged across both trades.
        assert r.avg_mfe_captured_pct == pytest.approx(0.5)

    # High threshold: zero trades.
    for r in by_threshold[0.50]:
        assert r.n_scenarios == 2
        assert r.n_trades == 0
        assert r.win_rate == 0.0
        assert r.avg_return_pct == 0.0
        assert r.sharpe_annualized is None
