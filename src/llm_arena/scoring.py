"""Scoring & Metrics Engine for the LLM Performance Arena.

Computes classification, operational, financial, and calibration metrics
for a set of AgentRunResults evaluated against LabeledScenarios.

Usage::

    from src.llm_arena.scoring import MetricsCalculator

    calc = MetricsCalculator()
    scorecard = calc.score_results(results, scenarios)
    print(scorecard.classification.direction_accuracy)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from typing import Optional

from .harness import AgentRunResult, AgentConfig
from .models import LabeledScenario, StockOutcome, CorrectSignal


# ---------------------------------------------------------------------------
# Token pricing (per million tokens) -- used for ROI estimation
# ---------------------------------------------------------------------------

_PRICING = {
    # model_id substring -> (input $/M, output $/M)
    "opus": (3.0, 15.0),
    "sonnet": (1.0, 5.0),
    "haiku": (0.80, 4.0),
    "default": (1.0, 5.0),
}


def _cost_per_call(model_id: str, tokens_input: int, tokens_output: int) -> float:
    """Estimate USD cost for one LLM call based on token counts."""
    model_lower = (model_id or "").lower()
    price_input, price_output = _PRICING["default"]
    for key, prices in _PRICING.items():
        if key != "default" and key in model_lower:
            price_input, price_output = prices
            break
    return (tokens_input / 1_000_000) * price_input + (tokens_output / 1_000_000) * price_output


# ---------------------------------------------------------------------------
# Direction matching helpers
# ---------------------------------------------------------------------------

_BULL_DIRECTIONS = {"BULL", "STRONG_BULL"}
_BEAR_DIRECTIONS = {"BEAR", "STRONG_BEAR"}
_NEUTRAL_DIRECTIONS = {"NEUTRAL"}

_BULL_SIGNALS = {CorrectSignal.BULL, CorrectSignal.STRONG_BULL}
_BEAR_SIGNALS = {CorrectSignal.BEAR, CorrectSignal.STRONG_BEAR}
_NEUTRAL_SIGNALS = {CorrectSignal.NEUTRAL}


def _direction_correct(result: AgentRunResult, scenario: LabeledScenario) -> bool:
    """Return True if the agent's direction matches the scenario's correct_signal."""
    direction = (result.signal_direction or "").upper()
    correct = scenario.correct_signal

    if direction in _BULL_DIRECTIONS and correct in _BULL_SIGNALS:
        return True
    if direction in _BEAR_DIRECTIONS and correct in _BEAR_SIGNALS:
        return True
    if direction in _NEUTRAL_DIRECTIONS and correct in _NEUTRAL_SIGNALS:
        return True
    return False


def _is_bull_call(result: AgentRunResult) -> bool:
    return (result.signal_direction or "").upper() in _BULL_DIRECTIONS


def _is_bear_or_neutral_call(result: AgentRunResult) -> bool:
    return (result.signal_direction or "").upper() in (_BEAR_DIRECTIONS | _NEUTRAL_DIRECTIONS)


def _is_runner(scenario: LabeledScenario) -> bool:
    return scenario.outcome == StockOutcome.RUNNER


def _is_fader(scenario: LabeledScenario) -> bool:
    return scenario.outcome == StockOutcome.FADER


# ---------------------------------------------------------------------------
# Metric dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ClassificationMetrics:
    """Binary and multi-class classification metrics in trading context."""

    direction_accuracy: float       # % of correct direction calls
    catalyst_accuracy: float        # % of correct catalyst classifications
    true_positives: int             # BULL on RUNNER (correct buy signal)
    true_negatives: int             # BEAR/NEUTRAL on FADER (correct avoid)
    false_positives: int            # BULL on FADER (expensive mistake)
    false_negatives: int            # BEAR/NEUTRAL on RUNNER (missed opportunity)
    precision: float                # TP / (TP + FP)
    recall: float                   # TP / (TP + FN)
    f1_score: float                 # Harmonic mean of precision and recall
    calibration_error: float        # Mean |confidence - actual_accuracy| across bins
    overconfidence_rate: float      # % of predictions where confidence > bin accuracy
    accuracy_on_runners: float      # Direction accuracy when outcome is RUNNER
    accuracy_on_faders: float       # Direction accuracy when outcome is FADER

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ClassificationMetrics":
        return cls(**d)


@dataclass
class OperationalMetrics:
    """Latency, reliability, and token-cost operational metrics."""

    timeout_rate: float             # Fraction of calls that timed out
    parse_success_rate: float       # Fraction of calls that parsed cleanly
    median_latency_ms: float        # Median latency in milliseconds
    p95_latency_ms: float           # 95th percentile latency
    p99_latency_ms: float           # 99th percentile latency
    total_tokens_input: int         # Sum of input tokens across all calls
    total_tokens_output: int        # Sum of output tokens across all calls
    avg_tokens_per_call: float      # Average (input + output) tokens per call

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OperationalMetrics":
        return cls(**d)


@dataclass
class FinancialMetrics:
    """Dollar-value signal quality metrics."""

    signal_value: float             # Net estimated value of the agent's calls
    cost_per_signal: float          # Average USD cost per LLM call
    roi: float                      # signal_value / (cost_per_signal * scenario_count)
    avoided_losses: float           # Gains from correctly identified faders
    captured_gains: float           # Gains from correctly identified runners

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FinancialMetrics":
        return cls(**d)


@dataclass
class AgentScorecard:
    """Full performance scorecard for one agent on one experiment."""

    agent_type: str
    config: dict
    scenario_count: int
    classification: ClassificationMetrics
    operational: OperationalMetrics
    financial: FinancialMetrics
    accuracy_by_catalyst: dict[str, float]
    accuracy_by_outcome: dict[str, float]

    # ---------------------------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "config": self.config,
            "scenario_count": self.scenario_count,
            "classification": self.classification.to_dict(),
            "operational": self.operational.to_dict(),
            "financial": self.financial.to_dict(),
            "accuracy_by_catalyst": self.accuracy_by_catalyst,
            "accuracy_by_outcome": self.accuracy_by_outcome,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentScorecard":
        return cls(
            agent_type=d["agent_type"],
            config=d.get("config", {}),
            scenario_count=d["scenario_count"],
            classification=ClassificationMetrics.from_dict(d["classification"]),
            operational=OperationalMetrics.from_dict(d["operational"]),
            financial=FinancialMetrics.from_dict(d["financial"]),
            accuracy_by_catalyst=d.get("accuracy_by_catalyst", {}),
            accuracy_by_outcome=d.get("accuracy_by_outcome", {}),
        )


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class MetricsCalculator:
    """Computes all metrics from a list of AgentRunResults + LabeledScenarios."""

    def score_results(
        self,
        results: list[AgentRunResult],
        scenarios: list[LabeledScenario],
    ) -> AgentScorecard:
        """Compute and return a full AgentScorecard.

        Args:
            results:   List of AgentRunResult objects (one per scenario).
            scenarios: Corresponding LabeledScenario objects.

        Returns:
            AgentScorecard with all metrics populated.
        """
        # Build a lookup map: scenario_id -> LabeledScenario
        scenario_map: dict[str, LabeledScenario] = {s.scenario_id: s for s in scenarios}

        # Filter results to only those with matching scenarios
        paired: list[tuple[AgentRunResult, LabeledScenario]] = []
        for r in results:
            s = scenario_map.get(r.scenario_id)
            if s is not None:
                paired.append((r, s))

        # Determine agent identity from first result (or empty)
        agent_type = results[0].agent_config.agent_type if results else "unknown"
        config = results[0].agent_config.to_dict() if results else {}

        classification = self._compute_classification(paired)
        operational = self._compute_operational(results)
        financial = self._compute_financial(paired)
        by_catalyst = self._compute_accuracy_by_catalyst(paired)
        by_outcome = self._compute_accuracy_by_outcome(paired)

        return AgentScorecard(
            agent_type=agent_type,
            config=config,
            scenario_count=len(paired),
            classification=classification,
            operational=operational,
            financial=financial,
            accuracy_by_catalyst=by_catalyst,
            accuracy_by_outcome=by_outcome,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _compute_classification(
        self,
        paired: list[tuple[AgentRunResult, LabeledScenario]],
    ) -> ClassificationMetrics:
        if not paired:
            return ClassificationMetrics(
                direction_accuracy=0.0,
                catalyst_accuracy=0.0,
                true_positives=0,
                true_negatives=0,
                false_positives=0,
                false_negatives=0,
                precision=0.0,
                recall=0.0,
                f1_score=0.0,
                calibration_error=0.0,
                overconfidence_rate=0.0,
                accuracy_on_runners=0.0,
                accuracy_on_faders=0.0,
            )

        n = len(paired)
        correct_dir = sum(1 for r, s in paired if _direction_correct(r, s))
        direction_accuracy = correct_dir / n

        # Catalyst accuracy (only count where both sides have a value)
        cat_pairs = [
            (r, s) for r, s in paired
            if r.catalyst_type is not None and s.catalyst_type is not None
        ]
        if cat_pairs:
            cat_correct = sum(
                1 for r, s in cat_pairs
                if (r.catalyst_type or "").lower() == s.catalyst_type.value.lower()
            )
            catalyst_accuracy = cat_correct / len(cat_pairs)
        else:
            catalyst_accuracy = 0.0

        # Confusion matrix (RUNNER/FADER scope)
        tp = sum(1 for r, s in paired if _is_bull_call(r) and _is_runner(s))
        fp = sum(1 for r, s in paired if _is_bull_call(r) and _is_fader(s))
        tn = sum(1 for r, s in paired if _is_bear_or_neutral_call(r) and _is_fader(s))
        fn = sum(1 for r, s in paired if _is_bear_or_neutral_call(r) and _is_runner(s))

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        # Calibration
        cal_error, overconf_rate = self._compute_calibration(paired)

        # Accuracy on runners / faders
        runner_pairs = [(r, s) for r, s in paired if _is_runner(s)]
        fader_pairs = [(r, s) for r, s in paired if _is_fader(s)]

        acc_runners = (
            sum(1 for r, s in runner_pairs if _direction_correct(r, s)) / len(runner_pairs)
            if runner_pairs else 0.0
        )
        acc_faders = (
            sum(1 for r, s in fader_pairs if _direction_correct(r, s)) / len(fader_pairs)
            if fader_pairs else 0.0
        )

        return ClassificationMetrics(
            direction_accuracy=direction_accuracy,
            catalyst_accuracy=catalyst_accuracy,
            true_positives=tp,
            true_negatives=tn,
            false_positives=fp,
            false_negatives=fn,
            precision=precision,
            recall=recall,
            f1_score=f1,
            calibration_error=cal_error,
            overconfidence_rate=overconf_rate,
            accuracy_on_runners=acc_runners,
            accuracy_on_faders=acc_faders,
        )

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def _compute_calibration(
        self,
        paired: list[tuple[AgentRunResult, LabeledScenario]],
    ) -> tuple[float, float]:
        """Return (calibration_error, overconfidence_rate).

        Calibration error: mean |confidence - actual_accuracy| across non-empty bins.
        Overconfidence rate: % of predictions whose confidence > bin accuracy.
        """
        # Only use results that have a confidence value
        conf_pairs = [
            (r.signal_confidence, _direction_correct(r, s))
            for r, s in paired
            if r.signal_confidence is not None
        ]

        if not conf_pairs:
            return 0.0, 0.0

        # 5 calibration bins
        bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.001)]
        bin_errors = []
        overconfident_count = 0
        total_with_conf = len(conf_pairs)

        for lo, hi in bins:
            bucket = [(conf, correct) for conf, correct in conf_pairs if lo <= conf < hi]
            if not bucket:
                continue
            avg_conf = sum(c for c, _ in bucket) / len(bucket)
            actual_acc = sum(1 for _, correct in bucket if correct) / len(bucket)
            bin_errors.append(abs(avg_conf - actual_acc))
            # Count predictions in this bin that are overconfident
            overconfident_count += sum(1 for conf, _ in bucket if conf > actual_acc)

        calibration_error = sum(bin_errors) / len(bin_errors) if bin_errors else 0.0
        overconfidence_rate = overconfident_count / total_with_conf if total_with_conf > 0 else 0.0

        return calibration_error, overconfidence_rate

    # ------------------------------------------------------------------
    # Operational
    # ------------------------------------------------------------------

    def _compute_operational(
        self,
        results: list[AgentRunResult],
    ) -> OperationalMetrics:
        if not results:
            return OperationalMetrics(
                timeout_rate=0.0,
                parse_success_rate=0.0,
                median_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                total_tokens_input=0,
                total_tokens_output=0,
                avg_tokens_per_call=0.0,
            )

        n = len(results)
        timeout_rate = sum(1 for r in results if r.timed_out) / n
        parse_rate = sum(1 for r in results if r.parse_success) / n

        latencies = sorted(r.latency_ms for r in results)
        median_lat = statistics.median(latencies) if latencies else 0.0

        def _percentile(data: list[float], pct: float) -> float:
            if not data:
                return 0.0
            idx = int(len(data) * pct / 100)
            idx = min(idx, len(data) - 1)
            return data[idx]

        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        total_in = sum(r.tokens_input for r in results)
        total_out = sum(r.tokens_output for r in results)
        avg_tokens = (total_in + total_out) / n

        return OperationalMetrics(
            timeout_rate=timeout_rate,
            parse_success_rate=parse_rate,
            median_latency_ms=median_lat,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            total_tokens_input=total_in,
            total_tokens_output=total_out,
            avg_tokens_per_call=avg_tokens,
        )

    # ------------------------------------------------------------------
    # Financial
    # ------------------------------------------------------------------

    def _compute_financial(
        self,
        paired: list[tuple[AgentRunResult, LabeledScenario]],
    ) -> FinancialMetrics:
        """Estimate dollar-value signal quality.

        Convention (all values in dollars):
        - TP (BULL on RUNNER): captured_gains += trade_pnl (or max_gain proxy)
        - TN (BEAR/NEUTRAL on FADER): avoided_losses += abs(drawdown proxy)
        - FP (BULL on FADER): signal_value -= abs(trade_pnl or drawdown) * 1.5 (weighted)
        - FN (BEAR/NEUTRAL on RUNNER): signal_value -= trade_pnl (missed gain)
        """
        captured_gains = 0.0
        avoided_losses = 0.0
        fp_penalty = 0.0
        fn_penalty = 0.0

        model_id = paired[0][0].agent_config.model_id if paired else "unknown"

        for r, s in paired:
            is_bull = _is_bull_call(r)
            is_runner = _is_runner(s)
            is_fader = _is_fader(s)

            # Estimate gain/loss proxies from scenario
            gain_proxy = s.trade_pnl if s.trade_pnl is not None else (
                s.max_gain_pct * (s.open_price or 1.0) * 100 if s.max_gain_pct is not None else 0.0
            )
            loss_proxy = abs(s.trade_pnl) if s.trade_pnl is not None else (
                abs(s.max_drawdown_pct or 0.0) * (s.open_price or 1.0) * 100
            )

            if is_bull and is_runner:
                # True positive: correct buy call
                captured_gains += max(gain_proxy, 0.0)
            elif is_bull and is_fader:
                # False positive: called bull on a fader -- costly
                fp_penalty += loss_proxy * 1.5
            elif not is_bull and is_fader:
                # True negative: avoided a fader
                avoided_losses += loss_proxy
            elif not is_bull and is_runner:
                # False negative: missed a runner
                fn_penalty += max(gain_proxy, 0.0)

        signal_value = captured_gains + avoided_losses - fp_penalty - fn_penalty

        # Cost estimation
        total_cost = sum(
            _cost_per_call(model_id, r.tokens_input, r.tokens_output)
            for r, _ in paired
        )
        n = len(paired)
        cost_per_signal = total_cost / n if n > 0 else 0.0

        roi = signal_value / max(cost_per_signal * n, 0.01)

        return FinancialMetrics(
            signal_value=round(signal_value, 2),
            cost_per_signal=round(cost_per_signal, 6),
            roi=round(roi, 2),
            avoided_losses=round(avoided_losses, 2),
            captured_gains=round(captured_gains, 2),
        )

    # ------------------------------------------------------------------
    # Accuracy by catalyst / outcome
    # ------------------------------------------------------------------

    def _compute_accuracy_by_catalyst(
        self,
        paired: list[tuple[AgentRunResult, LabeledScenario]],
    ) -> dict[str, float]:
        """Return {catalyst_type_value: direction_accuracy} for each catalyst type."""
        groups: dict[str, list[bool]] = {}
        for r, s in paired:
            key = s.catalyst_type.value
            groups.setdefault(key, []).append(_direction_correct(r, s))

        return {k: sum(v) / len(v) for k, v in groups.items() if v}

    def _compute_accuracy_by_outcome(
        self,
        paired: list[tuple[AgentRunResult, LabeledScenario]],
    ) -> dict[str, float]:
        """Return {outcome_value: direction_accuracy} for each stock outcome type."""
        groups: dict[str, list[bool]] = {}
        for r, s in paired:
            key = s.outcome.value
            groups.setdefault(key, []).append(_direction_correct(r, s))

        return {k: sum(v) / len(v) for k, v in groups.items() if v}

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def compare_scorecards(
        self,
        baseline: AgentScorecard,
        variant: AgentScorecard,
    ) -> dict:
        """Compute deltas between a baseline and variant scorecard.

        Returns a dict with keys:
          - direction_accuracy_delta
          - f1_delta
          - precision_delta
          - recall_delta
          - signal_value_delta
          - roi_delta
          - parse_success_delta
          - timeout_delta
          - calibration_error_delta
          - overconfidence_rate_delta
          - summary (human-readable string)
        """
        bc = baseline.classification
        vc = variant.classification
        bo = baseline.operational
        vo = variant.operational
        bf = baseline.financial
        vf = variant.financial

        dir_delta = vc.direction_accuracy - bc.direction_accuracy
        f1_delta = vc.f1_score - bc.f1_score
        prec_delta = vc.precision - bc.precision
        rec_delta = vc.recall - bc.recall
        sv_delta = vf.signal_value - bf.signal_value
        roi_delta = vf.roi - bf.roi
        parse_delta = vo.parse_success_rate - bo.parse_success_rate
        timeout_delta = vo.timeout_rate - bo.timeout_rate
        cal_delta = vc.calibration_error - bc.calibration_error
        overconf_delta = vc.overconfidence_rate - bc.overconfidence_rate

        sign = lambda x: "+" if x >= 0 else ""
        summary = (
            f"Direction: {sign(dir_delta)}{dir_delta:+.1%}  "
            f"F1: {sign(f1_delta)}{f1_delta:+.3f}  "
            f"Signal Value: {sign(sv_delta)}${sv_delta:,.0f}  "
            f"ROI: {sign(roi_delta)}{roi_delta:+.1f}x"
        )

        return {
            "direction_accuracy_delta": dir_delta,
            "f1_delta": f1_delta,
            "precision_delta": prec_delta,
            "recall_delta": rec_delta,
            "signal_value_delta": sv_delta,
            "roi_delta": roi_delta,
            "parse_success_delta": parse_delta,
            "timeout_delta": timeout_delta,
            "calibration_error_delta": cal_delta,
            "overconfidence_rate_delta": overconf_delta,
            "summary": summary,
        }


# ---------------------------------------------------------------------------
# Formatting helpers (used by CLI)
# ---------------------------------------------------------------------------


def format_scorecard(scorecard: AgentScorecard, experiment_name: str = "", detail: bool = False) -> str:
    """Return a formatted multi-line string representation of a scorecard."""
    c = scorecard.classification
    o = scorecard.operational
    f = scorecard.financial

    label = experiment_name or scorecard.agent_type
    width = 54
    sep = "=" * width

    lines = [
        sep,
        f"  AGENT SCORECARD: {scorecard.agent_type} ({label})",
        sep,
        "",
        "CLASSIFICATION METRICS",
        f"  Direction Accuracy:  {c.direction_accuracy:.1%}",
        f"  Catalyst Accuracy:   {c.catalyst_accuracy:.1%}",
        "",
        "  Confusion Matrix:",
        f"    TP (Bull on Runner): {c.true_positives:>4}  |  FP (Bull on Fader):  {c.false_positives:>4}",
        f"    FN (Miss on Runner): {c.false_negatives:>4}  |  TN (Bear on Fader):  {c.true_negatives:>4}",
        "",
        f"  Precision: {c.precision:.3f}  Recall: {c.recall:.3f}  F1: {c.f1_score:.3f}",
        "",
        "CALIBRATION",
        f"  Calibration Error:   {c.calibration_error:.3f}",
        f"  Overconfidence Rate: {c.overconfidence_rate:.1%}",
        "",
        "OPERATIONAL METRICS",
        f"  Parse Success Rate:  {o.parse_success_rate:.1%}",
        f"  Timeout Rate:        {o.timeout_rate:.1%}",
        f"  Median Latency:      {o.median_latency_ms:,.0f}ms",
        f"  P95 Latency:         {o.p95_latency_ms:,.0f}ms",
        "",
        "FINANCIAL METRICS",
        f"  Captured Gains:     ${f.captured_gains:>10,.0f}",
        f"  Avoided Losses:     ${f.avoided_losses:>10,.0f}",
        f"  Signal Value:       ${f.signal_value:>10,.0f}",
        f"  Cost per Signal:    ${f.cost_per_signal:>10.4f}",
        f"  ROI:                 {f.roi:>10.1f}x",
    ]

    if detail:
        lines += [
            "",
            "ACCURACY BY CATALYST TYPE",
        ]
        for catalyst, acc in sorted(scorecard.accuracy_by_catalyst.items()):
            lines.append(f"  {catalyst:<22} {acc:.1%}")

        lines += [
            "",
            "ACCURACY BY OUTCOME",
        ]
        for outcome, acc in sorted(scorecard.accuracy_by_outcome.items()):
            lines.append(f"  {outcome:<22} {acc:.1%}")

    lines.append(sep)
    return "\n".join(lines)


def format_comparison(comparison: dict, baseline_name: str, variant_name: str) -> str:
    """Return a formatted comparison between two scorecards."""
    width = 54
    sep = "=" * width

    def _delta_str(val: float, fmt: str = ".1%", positive_good: bool = True) -> str:
        arrow = "^" if val > 0 else ("v" if val < 0 else "--")
        sign = "+" if val >= 0 else ""
        color_hint = "" if val == 0 else (
            " (better)" if (val > 0) == positive_good else " (worse)"
        )
        return f"{arrow} {sign}{val:{fmt}}{color_hint}"

    lines = [
        sep,
        f"  COMPARISON: {baseline_name}  vs  {variant_name}",
        sep,
        "",
        "METRIC                       BASELINE -> VARIANT",
        f"  Direction Accuracy   {_delta_str(comparison['direction_accuracy_delta'])}",
        f"  F1 Score             {_delta_str(comparison['f1_delta'], '.3f')}",
        f"  Precision            {_delta_str(comparison['precision_delta'], '.3f')}",
        f"  Recall               {_delta_str(comparison['recall_delta'], '.3f')}",
        f"  Signal Value         {_delta_str(comparison['signal_value_delta'], ',.0f')}",
        f"  ROI                  {_delta_str(comparison['roi_delta'], '.1f')}",
        f"  Parse Success        {_delta_str(comparison['parse_success_delta'])}",
        f"  Timeout Rate         {_delta_str(comparison['timeout_delta'], '.1%', positive_good=False)}",
        f"  Calibration Error    {_delta_str(comparison['calibration_error_delta'], '.3f', positive_good=False)}",
        f"  Overconfidence Rate  {_delta_str(comparison['overconfidence_rate_delta'], '.1%', positive_good=False)}",
        "",
        f"  Summary: {comparison['summary']}",
        sep,
    ]
    return "\n".join(lines)
