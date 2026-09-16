"""Report Generator for the LLM Performance Arena (Component 5).

Produces human-readable reports from experiment results: scorecards,
comparisons, significance results, and actionable recommendations.
Outputs to terminal (string) and optionally to a markdown file.

Usage::

    from src.llm_arena.report import ReportGenerator

    gen = ReportGenerator()
    print(gen.generate_scorecard_report(scorecard))
    print(gen.generate_experiment_report(result))
    gen.save_markdown(report, "data/llm_arena/reports/my_report.md")
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from .experiment import ExperimentResult, SignificanceResult
from .scoring import AgentScorecard, ClassificationMetrics


# ---------------------------------------------------------------------------
# Formatting constants
# ---------------------------------------------------------------------------

_WIDTH = 72
_SEP = "=" * _WIDTH
_THIN = "-" * _WIDTH


class ReportGenerator:
    """Generates human-readable reports from LLM Arena results."""

    # ------------------------------------------------------------------
    # Public: single scorecard
    # ------------------------------------------------------------------

    def generate_scorecard_report(self, scorecard: AgentScorecard) -> str:
        """Generate a formatted report for a single agent scorecard.

        Sections:
        - Header (agent type, model, scenario count)
        - Classification Performance (accuracy, confusion matrix, precision/recall/F1)
        - Confidence Calibration (calibration error)
        - Operational Performance (latency, timeouts, parse success)
        - Financial Impact (signal value, avoided losses, ROI)
        - Breakdown by Catalyst Type
        - Breakdown by Outcome
        """
        c = scorecard.classification
        o = scorecard.operational
        f = scorecard.financial
        model = scorecard.config.get("model_id", "unknown")

        lines = [
            _SEP,
            f"  AGENT SCORECARD: {scorecard.agent_type}",
            f"  Model: {model}  |  Scenarios: {scorecard.scenario_count}",
            _SEP,
            "",
            "CLASSIFICATION PERFORMANCE",
            _THIN,
            f"  Direction Accuracy : {c.direction_accuracy:.1%}",
            f"  Catalyst Accuracy  : {c.catalyst_accuracy:.1%}",
            "",
            self.generate_confusion_matrix_display(c),
            "",
            f"  Precision : {c.precision:.3f}   Recall : {c.recall:.3f}   F1 : {c.f1_score:.3f}",
            "",
            f"  Accuracy on RUNNERS : {c.accuracy_on_runners:.1%}",
            f"  Accuracy on FADERS  : {c.accuracy_on_faders:.1%}",
            "",
            "CONFIDENCE CALIBRATION",
            _THIN,
            self.generate_calibration_chart(scorecard),
            "",
            "OPERATIONAL PERFORMANCE",
            _THIN,
            f"  Parse Success Rate : {o.parse_success_rate:.1%}",
            f"  Timeout Rate       : {o.timeout_rate:.1%}",
            f"  Median Latency     : {o.median_latency_ms:,.0f} ms",
            f"  P95 Latency        : {o.p95_latency_ms:,.0f} ms",
            f"  P99 Latency        : {o.p99_latency_ms:,.0f} ms",
            f"  Avg Tokens/Call    : {o.avg_tokens_per_call:,.0f}",
            "",
            "FINANCIAL IMPACT",
            _THIN,
            f"  Captured Gains  : ${f.captured_gains:>10,.0f}",
            f"  Avoided Losses  : ${f.avoided_losses:>10,.0f}",
            f"  Signal Value    : ${f.signal_value:>10,.0f}",
            f"  Cost per Signal : ${f.cost_per_signal:>10.4f}",
            f"  ROI             :  {f.roi:>10.1f}x",
        ]

        if scorecard.accuracy_by_catalyst:
            lines += ["", "BREAKDOWN BY CATALYST TYPE", _THIN]
            for cat, acc in sorted(scorecard.accuracy_by_catalyst.items()):
                bar = self._bar(acc, width=20)
                lines.append(f"  {cat:<25} {bar}  {acc:.1%}")

        if scorecard.accuracy_by_outcome:
            lines += ["", "BREAKDOWN BY OUTCOME", _THIN]
            for outcome, acc in sorted(scorecard.accuracy_by_outcome.items()):
                bar = self._bar(acc, width=20)
                lines.append(f"  {outcome:<25} {bar}  {acc:.1%}")

        lines.append(_SEP)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: full experiment report
    # ------------------------------------------------------------------

    def generate_experiment_report(self, result: ExperimentResult) -> str:
        """Generate a full experiment report.

        Sections:
        - Executive Summary (winner, key finding)
        - Experiment Setup (configs, scenario count, stats settings)
        - Head-to-Head Comparison Table (all metrics side by side)
        - Statistical Significance (p-values, CIs, effect sizes)
        - Detailed Scorecards (one per config)
        - Recommendation (what to change and expected impact)
        """
        cfg = result.config
        completed = (
            result.completed_at.strftime("%Y-%m-%d %H:%M UTC")
            if result.completed_at
            else "unknown"
        )

        lines = [
            _SEP,
            f"  LLM ARENA EXPERIMENT: {cfg.name}",
            f"  {cfg.description}",
            _SEP,
            f"  Date: {completed}  |  Scenarios: {result.scenario_count}  |  Mode: {cfg.mode}",
            f"  Duration: {result.run_duration_seconds:.1f}s  |  Bootstrap: {cfg.bootstrap_iterations}  |  Confidence: {cfg.confidence_level:.0%}",
            "",
        ]

        # Executive summary
        lines += self._executive_summary(result)
        lines.append("")

        # Experiment setup
        lines += [
            "EXPERIMENT SETUP",
            _THIN,
            f"  Baseline : {cfg.baseline.agent_type} / {cfg.baseline.model_id}",
        ]
        for i, v in enumerate(cfg.variants):
            lines.append(f"  Variant {i+1} : {v.agent_type} / {v.model_id}")
        if cfg.scenario_filters:
            filters_str = "  ".join(
                f"{k}={v}" for k, v in cfg.scenario_filters.items()
            )
            lines.append(f"  Filters  : {filters_str}")
        lines.append("")

        # Comparison tables (one per variant)
        for i, (variant_sc, comparison) in enumerate(
            zip(result.variant_scorecards, result.comparisons)
        ):
            variant_name = (
                cfg.variants[i].agent_type
                if i < len(cfg.variants)
                else f"variant_{i}"
            )
            sig = result.significance[i] if i < len(result.significance) else None
            lines += [
                f"HEAD-TO-HEAD: baseline vs {variant_name}",
                _THIN,
                self.generate_comparison_table(
                    result.baseline_scorecard, variant_sc, sig
                ),
                "",
            ]

        # Statistical significance
        lines += ["STATISTICAL SIGNIFICANCE", _THIN]
        for i, sig in enumerate(result.significance):
            variant_name = (
                cfg.variants[i].agent_type
                if i < len(cfg.variants)
                else f"variant_{i}"
            )
            lines += [
                f"  baseline vs {variant_name}  ({sig.metric})",
                f"    Baseline : {sig.baseline_mean:.3%}",
                f"    Variant  : {sig.variant_mean:.3%}",
                f"    Delta    : {sig.delta:+.3%}  (relative: {sig.delta_pct:+.1%})",
                f"    p-value  : {sig.p_value:.4f}  {self.format_significance(sig.p_value)}",
                f"    Sig?     : {'YES' if sig.significant else 'NO'} at {cfg.confidence_level:.0%}",
                f"    95% CI   : [{sig.confidence_interval[0]:+.3%}, {sig.confidence_interval[1]:+.3%}]",
                f"    Cohen's d: {sig.effect_size:.3f}  ({self._effect_size_label(sig.effect_size)})",
                "",
            ]

        # Detailed scorecards
        lines += ["DETAILED SCORECARDS", _SEP]
        lines.append(self.generate_scorecard_report(result.baseline_scorecard))
        for i, variant_sc in enumerate(result.variant_scorecards):
            lines.append(self.generate_scorecard_report(variant_sc))

        # Recommendations
        lines += ["", "RECOMMENDATION", _SEP]
        lines += self._format_recommendation(result)
        lines.append(_SEP)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: comparison table
    # ------------------------------------------------------------------

    def generate_comparison_table(
        self,
        baseline: AgentScorecard,
        variant: AgentScorecard,
        significance: Optional[SignificanceResult] = None,
    ) -> str:
        """Generate a side-by-side comparison table.

        Metric                  | Baseline | Variant  | Delta   | Sig?
        Direction Accuracy      |   30.6%  |  55.2%   | +24.6%  | ***
        """
        bc = baseline.classification
        vc = variant.classification
        bo = baseline.operational
        vo = variant.operational
        bf = baseline.financial
        vf = variant.financial

        sig_stars = (
            self.format_significance(significance.p_value)
            if significance and significance.metric == "direction_accuracy"
            else "n/a"
        )

        # helper: format a row
        def _row(
            name: str,
            b_val: str,
            v_val: str,
            delta_val: float,
            fmt: str = ".1%",
            positive_good: bool = True,
            sig: str = "   ",
        ) -> str:
            delta_str = self.format_pct(delta_val) if "%" in fmt else f"{delta_val:+,.1f}"
            good = (delta_val > 0) == positive_good
            hint = "+" if good and delta_val != 0 else ("-" if not good and delta_val != 0 else " ")
            return (
                f"  {name:<28} | {b_val:>8} | {v_val:>8} | {delta_str:>8} | {sig}"
            )

        header = (
            f"  {'Metric':<28} | {'Baseline':>8} | {'Variant':>8} | {'Delta':>8} | Sig?"
        )
        divider = "  " + "-" * 28 + "-+-" + "-" * 8 + "-+-" + "-" * 8 + "-+-" + "-" * 8 + "-+-" + "----"

        rows = [
            header,
            divider,
            _row(
                "Direction Accuracy",
                f"{bc.direction_accuracy:.1%}",
                f"{vc.direction_accuracy:.1%}",
                vc.direction_accuracy - bc.direction_accuracy,
                sig=sig_stars,
            ),
            _row(
                "Catalyst Accuracy",
                f"{bc.catalyst_accuracy:.1%}",
                f"{vc.catalyst_accuracy:.1%}",
                vc.catalyst_accuracy - bc.catalyst_accuracy,
            ),
            _row(
                "F1 Score",
                f"{bc.f1_score:.3f}",
                f"{vc.f1_score:.3f}",
                vc.f1_score - bc.f1_score,
                fmt=".3f",
            ),
            _row(
                "Precision",
                f"{bc.precision:.3f}",
                f"{vc.precision:.3f}",
                vc.precision - bc.precision,
                fmt=".3f",
            ),
            _row(
                "Recall",
                f"{bc.recall:.3f}",
                f"{vc.recall:.3f}",
                vc.recall - bc.recall,
                fmt=".3f",
            ),
            _row(
                "False Positive Rate",
                f"{bc.false_positives}/{bc.true_positives + bc.false_positives}" if (bc.true_positives + bc.false_positives) > 0 else "0/0",
                f"{vc.false_positives}/{vc.true_positives + vc.false_positives}" if (vc.true_positives + vc.false_positives) > 0 else "0/0",
                (vc.false_positives / max(vc.true_positives + vc.false_positives, 1))
                - (bc.false_positives / max(bc.true_positives + bc.false_positives, 1)),
                positive_good=False,
            ),
            _row(
                "Calibration Error",
                f"{bc.calibration_error:.3f}",
                f"{vc.calibration_error:.3f}",
                vc.calibration_error - bc.calibration_error,
                fmt=".3f",
                positive_good=False,
            ),
            divider,
            _row(
                "Parse Success Rate",
                f"{bo.parse_success_rate:.1%}",
                f"{vo.parse_success_rate:.1%}",
                vo.parse_success_rate - bo.parse_success_rate,
            ),
            _row(
                "Timeout Rate",
                f"{bo.timeout_rate:.1%}",
                f"{vo.timeout_rate:.1%}",
                vo.timeout_rate - bo.timeout_rate,
                positive_good=False,
            ),
            _row(
                "Median Latency (ms)",
                f"{bo.median_latency_ms:,.0f}",
                f"{vo.median_latency_ms:,.0f}",
                vo.median_latency_ms - bo.median_latency_ms,
                fmt=".0f",
                positive_good=False,
            ),
            divider,
            _row(
                "Signal Value ($)",
                f"${bf.signal_value:,.0f}",
                f"${vf.signal_value:,.0f}",
                vf.signal_value - bf.signal_value,
                fmt=",.0f",
            ),
            _row(
                "ROI",
                f"{bf.roi:.1f}x",
                f"{vf.roi:.1f}x",
                vf.roi - bf.roi,
                fmt=".1f",
            ),
        ]
        return "\n".join(rows)

    # ------------------------------------------------------------------
    # Public: confusion matrix
    # ------------------------------------------------------------------

    def generate_confusion_matrix_display(
        self, metrics: ClassificationMetrics
    ) -> str:
        """Generate a visual confusion matrix.

                              Predicted BULL   Predicted BEAR/NEUTRAL
          Actual RUNNER       TP=XX            FN=XX
          Actual FADER        FP=XX            TN=XX
        """
        tp = metrics.true_positives
        fn = metrics.false_negatives
        fp = metrics.false_positives
        tn = metrics.true_negatives

        lines = [
            "  Confusion Matrix:",
            f"  {'':30} {'Pred BULL':>12}   {'Pred BEAR/NEUTRAL':>18}",
            f"  {'Actual RUNNER (opportunity)':30} {'TP=' + str(tp):>12}   {'FN=' + str(fn):>18}",
            f"  {'Actual FADER  (avoid)':30} {'FP=' + str(fp):>12}   {'TN=' + str(tn):>18}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: calibration chart
    # ------------------------------------------------------------------

    def generate_calibration_chart(self, scorecard: AgentScorecard) -> str:
        """Generate calibration summary display.

        Shows calibration error and overconfidence rate.
        Full bin breakdown requires raw prediction data (not stored in scorecard).
        """
        c = scorecard.classification
        cal_err = c.calibration_error
        overconf = c.overconfidence_rate

        # Interpret calibration quality
        if cal_err < 0.05:
            quality = "well-calibrated"
        elif cal_err < 0.10:
            quality = "slightly miscalibrated"
        elif cal_err < 0.20:
            quality = "moderately miscalibrated"
        else:
            quality = "poorly calibrated"

        overconf_label = "overconfident" if overconf > 0.5 else "underconfident"

        lines = [
            f"  Calibration Error    : {cal_err:.3f}  ({quality})",
            f"  Overconfidence Rate  : {overconf:.1%}  ({overconf_label})",
            f"  Interpretation: Agent confidence vs actual accuracy gap = {cal_err:.3f}",
            f"    0.00-0.05 = well-calibrated  |  0.05-0.10 = slight issue",
            f"    0.10-0.20 = moderate issue   |  >0.20     = poorly calibrated",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: recommendations
    # ------------------------------------------------------------------

    def generate_recommendations(self, result: ExperimentResult) -> str:
        """Generate actionable recommendations ranked by impact.

        1. [HIGH IMPACT] Switch news agent model from X to Y
           Expected: +24.6% direction accuracy, -14% false positive rate
           Confidence: p=0.000, Cohen's d=0.51
        """
        lines = ["RECOMMENDATIONS", _SEP]
        lines += self._format_recommendation(result)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: dataset quality report
    # ------------------------------------------------------------------

    def generate_dataset_quality_report(self, dataset_stats: dict) -> str:
        """Generate a report on dataset quality.

        - Total scenarios, by source
        - Label distribution (catalyst types, outcomes)
        - Label confidence distribution
        - Gaps (catalyst types with few examples)
        - Recommendations for improving dataset
        """
        total = dataset_stats.get("total", 0)
        traded = dataset_stats.get("traded", 0)
        trade_wins = dataset_stats.get("trade_wins", 0)
        win_rate = dataset_stats.get("trade_win_rate", 0.0)

        lines = [
            _SEP,
            "  LLM ARENA — DATASET QUALITY REPORT",
            f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            _SEP,
            "",
            "OVERVIEW",
            _THIN,
            f"  Total Scenarios   : {total}",
            f"  Traded Scenarios  : {traded}  ({traded/total*100:.1f}% of total)" if total > 0 else "  Traded Scenarios  : 0",
            f"  Trade Win Rate    : {win_rate:.1%}  ({trade_wins} wins / {traded} trades)" if traded > 0 else "  Trade Win Rate    : N/A",
            "",
        ]

        # Outcome distribution
        by_outcome = dataset_stats.get("by_outcome", {})
        if by_outcome:
            lines += ["OUTCOME DISTRIBUTION", _THIN]
            for k, v in sorted(by_outcome.items(), key=lambda x: -x[1]):
                pct = v / total * 100 if total > 0 else 0
                bar = self._bar(pct / 100, width=25)
                lines.append(f"  {k:<20} {bar}  {v:>4}  ({pct:.1f}%)")
            lines.append("")

        # Catalyst type distribution
        by_catalyst = dataset_stats.get("by_catalyst_type", {})
        if by_catalyst:
            lines += ["CATALYST TYPE DISTRIBUTION", _THIN]
            for k, v in sorted(by_catalyst.items(), key=lambda x: -x[1]):
                pct = v / total * 100 if total > 0 else 0
                bar = self._bar(pct / 100, width=25)
                lines.append(f"  {k:<20} {bar}  {v:>4}  ({pct:.1f}%)")
            lines.append("")

        # Label confidence distribution
        by_conf = dataset_stats.get("by_label_confidence", {})
        if by_conf:
            lines += ["LABEL CONFIDENCE DISTRIBUTION", _THIN]
            for k, v in sorted(by_conf.items(), key=lambda x: -x[1]):
                pct = v / total * 100 if total > 0 else 0
                bar = self._bar(pct / 100, width=25)
                lines.append(f"  {k:<20} {bar}  {v:>4}  ({pct:.1f}%)")
            lines.append("")

        # Gap analysis
        gaps = []
        if by_catalyst:
            for k, v in by_catalyst.items():
                if v < 10:
                    gaps.append(f"    - {k}: only {v} scenario(s) — insufficient for reliable evaluation")
        if by_outcome:
            for k, v in by_outcome.items():
                if v < 10:
                    gaps.append(f"    - outcome={k}: only {v} scenario(s) — evaluation unreliable")

        # Confidence quality check
        unlabeled = by_conf.get("unlabeled", 0)
        low_conf = by_conf.get("auto_low", 0)
        low_quality = unlabeled + low_conf
        if low_quality > 0:
            gaps.append(
                f"    - {low_quality} scenarios are unlabeled/low-confidence "
                f"({low_quality/total*100:.1f}%) — consider manual review"
            )

        if gaps:
            lines += ["GAPS & DATA QUALITY ISSUES", _THIN]
            lines += gaps
            lines.append("")

        # Recommendations
        recs = []
        if total < 50:
            recs.append("  1. Dataset is small (<50). Add more scenarios before trusting experiment results.")
        if unlabeled > total * 0.2:
            recs.append(f"  2. {unlabeled/total*100:.0f}% of scenarios are unlabeled. Run manual labeling to improve quality.")
        if by_catalyst:
            sparse = [k for k, v in by_catalyst.items() if v < 5]
            if sparse:
                recs.append(f"  3. Sparse catalyst types ({', '.join(sparse)}) — prioritize collecting these scenarios.")
        if not recs:
            recs.append("  Dataset quality looks good. No major gaps detected.")

        lines += ["RECOMMENDATIONS", _THIN]
        lines += recs
        lines.append(_SEP)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: save markdown
    # ------------------------------------------------------------------

    def save_markdown(self, report: str, filepath: str) -> None:
        """Save report as markdown file (wraps plain text in a code block)."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write("```\n")
            fh.write(report)
            fh.write("\n```\n")

    # ------------------------------------------------------------------
    # Public: formatting helpers
    # ------------------------------------------------------------------

    def format_pct(self, value: float) -> str:
        """Format as percentage with sign: +24.6% or -14.0%."""
        sign = "+" if value >= 0 else ""
        return f"{sign}{value:.1%}"

    def format_significance(self, p_value: float) -> str:
        """Format significance stars.

        *** p<0.001  ** p<0.01  * p<0.05  ns otherwise.
        """
        if p_value < 0.001:
            return "***"
        if p_value < 0.01:
            return "** "
        if p_value < 0.05:
            return "*  "
        return "ns "

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _executive_summary(self, result: ExperimentResult) -> list[str]:
        """Return lines for the executive summary section."""
        cfg = result.config
        winner = result.winner

        if winner == "baseline":
            verdict = f"WINNER: BASELINE (no variant achieved significance)"
            detail_lines = [
                f"  No variant outperformed the baseline '{cfg.baseline.agent_type}' at",
                f"  {cfg.confidence_level:.0%} confidence. The baseline is recommended.",
            ]
        else:
            # Find the winning variant's significance result
            winning_sig = next(
                (
                    s
                    for s in result.significance
                    if s.variant_name == winner and s.significant
                ),
                result.significance[0] if result.significance else None,
            )
            stars = self.format_significance(winning_sig.p_value) if winning_sig else "   "
            delta_pct = winning_sig.delta if winning_sig else 0.0
            p_val = winning_sig.p_value if winning_sig else 1.0
            d = winning_sig.effect_size if winning_sig else 0.0

            verdict = (
                f"WINNER: {winner.upper()}  "
                f"({self.format_pct(delta_pct)} direction accuracy, "
                f"p={p_val:.3f} {stars.strip()})"
            )
            effect_label = self._effect_size_label(d)
            detail_lines = [
                f"  Variant '{winner}' outperforms baseline '{cfg.baseline.agent_type}'",
                f"  by {self.format_pct(delta_pct)} on direction accuracy. "
                f"Effect size: {d:.2f} ({effect_label}).",
            ]

        lines = [
            "EXECUTIVE SUMMARY",
            _SEP,
            f"  {verdict}",
            "",
        ]
        lines += detail_lines
        lines.append(_SEP)
        return lines

    def _format_recommendation(self, result: ExperimentResult) -> list[str]:
        """Format the recommendation section."""
        cfg = result.config
        lines: list[str] = []

        for line in result.recommendation.split("\n"):
            lines.append(f"  {line}")

        # Add impact section for each winning variant
        for i, sig in enumerate(result.significance):
            if sig.significant and sig.delta > 0:
                variant_cfg = cfg.variants[i] if i < len(cfg.variants) else None
                impact = "HIGH" if abs(sig.effect_size) >= 0.5 else "MEDIUM" if abs(sig.effect_size) >= 0.2 else "LOW"
                variant_name = variant_cfg.agent_type if variant_cfg else f"variant_{i}"
                baseline_name = cfg.baseline.agent_type

                lines += [
                    "",
                    f"  [{impact} IMPACT] Deploy '{variant_name}' in place of '{baseline_name}'",
                    f"    Expected: {self.format_pct(sig.delta)} direction accuracy",
                    f"    Confidence: p={sig.p_value:.3f} {self.format_significance(sig.p_value).strip()}, "
                    f"Cohen's d={sig.effect_size:.2f}",
                    f"    95% CI: [{sig.confidence_interval[0]:+.3%}, {sig.confidence_interval[1]:+.3%}]",
                ]

        return lines

    @staticmethod
    def _effect_size_label(d: float) -> str:
        """Classify Cohen's d effect size."""
        abs_d = abs(d)
        if abs_d >= 0.8:
            return "large"
        if abs_d >= 0.5:
            return "medium"
        if abs_d >= 0.2:
            return "small"
        return "negligible"

    @staticmethod
    def _bar(fraction: float, width: int = 20) -> str:
        """Render an ASCII progress bar."""
        filled = max(0, min(width, int(fraction * width)))
        return "[" + "#" * filled + "." * (width - filled) + "]"
