"""
MOMENTUM-X Agent Calibration Analysis

### ARCHITECTURAL CONTEXT
Node ID: analysis.agent_calibration
Graph Link: docs/memory/graph_state.json → "analysis.agent_calibration"

### RESEARCH BASIS
Phase 3.4 measures whether agent confidence scores correlate with
actual outcomes. A well-calibrated agent should have:
  - 70% confidence BUY signals winning ~70% of the time
  - Higher confidence → higher win rate (monotonically increasing)
  - Positive rank correlation (Spearman) between confidence and return

If calibration is poor, the MFCS weights are amplifying noise.

Ref: ADR-025 (Phase 3: Agent Calibration)
Ref: MOMENTUM_LOGIC.md §14 (Agent Signal Processing)

### CRITICAL INVARIANTS
1. Uses only post-hoc data: actual outcome vs. predicted signal.
2. Confidence bins must have minimum sample size for statistical validity.
3. Per-agent calibration allows targeted weight adjustment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CalibrationBin:
    """Calibration statistics for a confidence bin."""

    bin_low: float
    bin_high: float
    n_samples: int = 0
    n_correct: int = 0
    avg_confidence: float = 0.0
    avg_return: float = 0.0
    win_rate: float = 0.0


@dataclass
class AgentCalibrationReport:
    """Calibration report for a single agent."""

    agent_id: str
    n_signals: int = 0
    n_buy: int = 0
    n_neutral: int = 0
    buy_accuracy: float = 0.0
    avg_confidence: float = 0.0
    confidence_return_correlation: float = 0.0
    calibration_bins: list[CalibrationBin] = field(default_factory=list)
    calibration_error: float = 0.0  # Mean absolute calibration error

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "n_signals": self.n_signals,
            "n_buy": self.n_buy,
            "n_neutral": self.n_neutral,
            "buy_accuracy": round(self.buy_accuracy, 3),
            "avg_confidence": round(self.avg_confidence, 3),
            "confidence_return_correlation": round(self.confidence_return_correlation, 3),
            "calibration_error": round(self.calibration_error, 3),
            "bins": [
                {
                    "range": f"{b.bin_low:.1f}-{b.bin_high:.1f}",
                    "n": b.n_samples,
                    "win_rate": round(b.win_rate, 3),
                    "avg_confidence": round(b.avg_confidence, 3),
                    "avg_return": round(b.avg_return * 100, 2),
                }
                for b in self.calibration_bins
            ],
        }


@dataclass
class CalibrationSuite:
    """Full calibration report across all agents."""

    agent_reports: list[AgentCalibrationReport] = field(default_factory=list)
    overall_accuracy: float = 0.0
    overall_calibration_error: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_accuracy": round(self.overall_accuracy, 3),
            "overall_calibration_error": round(self.overall_calibration_error, 3),
            "agents": [r.to_dict() for r in self.agent_reports],
        }


def compute_agent_calibration(
    results: list[dict[str, Any]],
    n_bins: int = 5,
) -> CalibrationSuite:
    """
    Compute per-agent calibration from scenario recording results.

    Args:
        results: List of ScenarioResult dicts, each with:
            - scenario.outcome: "WIN" or "LOSS"
            - scenario.intraday_return: float
            - agent_signals: {agent_id: {signal, confidence, reasoning}}
            - verdict_action: "BUY" or "NO_TRADE"
        n_bins: Number of confidence bins for calibration curve.

    Returns:
        CalibrationSuite with per-agent calibration reports.
    """
    # Collect per-agent data
    agent_data: dict[str, list[tuple[str, float, str, float]]] = {}
    # Each tuple: (signal, confidence, outcome, return)

    for result in results:
        outcome = result.get("scenario", {}).get("outcome", "LOSS")
        intraday_return = result.get("scenario", {}).get("intraday_return", 0.0)
        agent_signals = result.get("agent_signals", {})

        for agent_id, sig_data in agent_signals.items():
            signal = sig_data.get("signal", "NEUTRAL")
            confidence = sig_data.get("confidence", 0.0)

            if agent_id not in agent_data:
                agent_data[agent_id] = []
            agent_data[agent_id].append((signal, confidence, outcome, intraday_return))

    # Compute per-agent reports
    suite = CalibrationSuite()
    total_correct = 0
    total_signals = 0
    total_cal_error = 0.0

    for agent_id, data in sorted(agent_data.items()):
        report = _compute_single_agent(agent_id, data, n_bins)
        suite.agent_reports.append(report)

        # Accumulate for overall stats
        n_buy = report.n_buy
        correct = int(report.buy_accuracy * n_buy)
        total_correct += correct
        total_signals += n_buy
        total_cal_error += report.calibration_error

    if total_signals > 0:
        suite.overall_accuracy = total_correct / total_signals
    if suite.agent_reports:
        suite.overall_calibration_error = total_cal_error / len(suite.agent_reports)

    return suite


def _compute_single_agent(
    agent_id: str,
    data: list[tuple[str, float, str, float]],
    n_bins: int,
) -> AgentCalibrationReport:
    """Compute calibration for a single agent."""
    report = AgentCalibrationReport(agent_id=agent_id, n_signals=len(data))

    buy_data = [(conf, outcome, ret) for sig, conf, outcome, ret in data if sig == "BUY"]
    neutral_data = [(conf, outcome, ret) for sig, conf, outcome, ret in data if sig != "BUY"]

    report.n_buy = len(buy_data)
    report.n_neutral = len(neutral_data)

    if not buy_data:
        return report

    # Buy accuracy: BUY + WIN = correct
    buy_correct = sum(1 for _, outcome, _ in buy_data if outcome == "WIN")
    report.buy_accuracy = buy_correct / len(buy_data)

    # Average confidence
    confidences = [conf for conf, _, _ in buy_data]
    report.avg_confidence = float(np.mean(confidences))

    # Confidence-return correlation (Spearman rank)
    returns = [ret for _, _, ret in buy_data]
    if len(buy_data) >= 5:
        report.confidence_return_correlation = _spearman_correlation(confidences, returns)

    # Calibration bins
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    cal_errors = []

    for i in range(n_bins):
        low, high = float(bin_edges[i]), float(bin_edges[i + 1])
        bin_data = [
            (conf, outcome, ret) for conf, outcome, ret in buy_data
            if low <= conf < high or (i == n_bins - 1 and conf == high)
        ]

        cb = CalibrationBin(bin_low=low, bin_high=high, n_samples=len(bin_data))

        if bin_data:
            cb.n_correct = sum(1 for _, outcome, _ in bin_data if outcome == "WIN")
            cb.win_rate = cb.n_correct / len(bin_data)
            cb.avg_confidence = float(np.mean([c for c, _, _ in bin_data]))
            cb.avg_return = float(np.mean([r for _, _, r in bin_data]))

            # Calibration error: |win_rate - avg_confidence|
            cal_errors.append(abs(cb.win_rate - cb.avg_confidence))

        report.calibration_bins.append(cb)

    if cal_errors:
        report.calibration_error = float(np.mean(cal_errors))

    return report


def _spearman_correlation(x: list[float], y: list[float]) -> float:
    """Compute Spearman rank correlation without scipy dependency."""
    n = len(x)
    if n < 3:
        return 0.0

    # Compute ranks
    x_ranks = _rank_array(x)
    y_ranks = _rank_array(y)

    # Pearson on ranks
    x_arr = np.array(x_ranks)
    y_arr = np.array(y_ranks)

    x_mean = np.mean(x_arr)
    y_mean = np.mean(y_arr)

    numerator = float(np.sum((x_arr - x_mean) * (y_arr - y_mean)))
    denom = float(np.sqrt(np.sum((x_arr - x_mean) ** 2) * np.sum((y_arr - y_mean) ** 2)))

    if denom == 0:
        return 0.0
    return numerator / denom


def _rank_array(values: list[float]) -> list[float]:
    """Compute ranks with average tie-breaking."""
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)

    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j

    return ranks
