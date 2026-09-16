"""
Evaluation Ordering Study + Pipeline Inversion Simulation.

The #1 P&L leak: sequential evaluation means later candidates enter at
worse prices. This module quantifies the cost and tests whether
parallel entry (pipeline inversion) would improve portfolio P&L.

Innovation 1 from the final assessment.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Ordering Heuristics ──────────────────────────────────────────────

ORDERING_HEURISTICS = {
    "momentum_desc": lambda b: -(abs(b.get("gap_pct", 0)) * b.get("rvol", 1)),
    "gap_desc": lambda b: -abs(b.get("gap_pct", 0)),
    "rvol_desc": lambda b: -b.get("rvol", 0),
    "spread_asc": lambda b: b.get("entry_price", 0),  # Lower price = tighter spread
    "confidence_desc": lambda b: -b.get("confidence", 0),
    "random": lambda b: hash(b.get("ticker", "")) % 1000,  # Deterministic "random"
}


def sort_candidates(
    candidates: list[dict],
    heuristic: str = "momentum_desc",
) -> list[dict]:
    """Sort candidates by the given ordering heuristic."""
    key_fn = ORDERING_HEURISTICS.get(heuristic, ORDERING_HEURISTICS["momentum_desc"])
    return sorted(candidates, key=key_fn)


def run_ordering_comparison(
    candidates: list[dict],
    simulate_fn,
    instance,
    base_params: dict,
    delay_bars: int = 1,
) -> dict[str, dict]:
    """
    Run the same candidates through all ordering heuristics.

    Returns {heuristic_name: {pnl, trades, capture_ratio, details}}.
    """
    results = {}

    for name in ORDERING_HEURISTICS:
        sorted_cands = sort_candidates(candidates, name)
        params = {**base_params, "evaluation_delay_bars": delay_bars}
        trades = simulate_fn(instance, sorted_cands, params)

        total_pnl = sum(t.get("pnl", 0) for t in trades)
        n_trades = len(trades)
        avg_capture = (
            sum(t.get("capture_ratio", 0) for t in trades) / max(n_trades, 1)
        )

        results[name] = {
            "pnl": round(total_pnl, 4),
            "trades": n_trades,
            "avg_capture_ratio": round(avg_capture, 4),
            "tickers": [t["ticker"] for t in trades],
            "per_trade": [{
                "ticker": t["ticker"],
                "pnl": t["pnl"],
                "mfe_pct": t.get("mfe_pct", 0),
                "capture_ratio": t.get("capture_ratio", 0),
            } for t in trades],
        }

    return results


def run_pipeline_inversion(
    candidates: list[dict],
    simulate_fn,
    instance,
    base_params: dict,
) -> dict:
    """
    Simulate pipeline inversion: enter ALL candidates at bar 0 (no delay),
    then cancel after N bars if LLM evaluation would have rejected them.

    Compares:
    - Sequential (current): delay_bars=1, max_positions=3
    - Parallel (inverted): delay_bars=0, max_positions=all, cancel after 5 bars
    """
    # Sequential baseline
    seq_params = {**base_params, "evaluation_delay_bars": 1, "max_positions": 3}
    seq_trades = simulate_fn(instance, candidates, seq_params)

    # Parallel (all enter at bar 0)
    par_params = {**base_params, "evaluation_delay_bars": 0, "max_positions": len(candidates)}
    par_trades = simulate_fn(instance, candidates, par_params)

    seq_pnl = sum(t.get("pnl", 0) for t in seq_trades)
    par_pnl = sum(t.get("pnl", 0) for t in par_trades)

    return {
        "sequential": {
            "pnl": round(seq_pnl, 4),
            "trades": len(seq_trades),
            "tickers": [t["ticker"] for t in seq_trades],
        },
        "parallel": {
            "pnl": round(par_pnl, 4),
            "trades": len(par_trades),
            "tickers": [t["ticker"] for t in par_trades],
        },
        "delta_pnl": round(par_pnl - seq_pnl, 4),
        "delta_pct": round((par_pnl - seq_pnl) / max(abs(seq_pnl), 0.01) * 100, 1),
    }


def format_ordering_results(results: dict[str, dict]) -> str:
    """Format ordering comparison as readable table."""
    lines = ["EVALUATION ORDERING STUDY", "=" * 70]
    lines.append(f"{'Ordering':<20s} {'P&L':>9s} {'Trades':>7s} {'Capture':>8s} Tickers")
    lines.append("-" * 70)

    for name in sorted(results, key=lambda n: results[n]["pnl"], reverse=True):
        r = results[name]
        tickers = ", ".join(r["tickers"][:5])
        lines.append(
            f"{name:<20s} ${r['pnl']:>+8.2f} {r['trades']:>7d} "
            f"{r['avg_capture_ratio']:>7.1%} {tickers}"
        )

    best = max(results, key=lambda n: results[n]["pnl"])
    worst = min(results, key=lambda n: results[n]["pnl"])
    delta = results[best]["pnl"] - results[worst]["pnl"]
    lines.append(f"\nBest: {best} (${results[best]['pnl']:+.2f})")
    lines.append(f"Worst: {worst} (${results[worst]['pnl']:+.2f})")
    lines.append(f"Ordering delta: ${delta:+.2f} ({delta/max(abs(results[best]['pnl']),0.01)*100:.0f}% of best)")

    return "\n".join(lines)
