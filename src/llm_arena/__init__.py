"""LLM Performance Arena — labeled dataset and evaluation framework."""

from .models import (
    CatalystType,
    StockOutcome,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
)
from .dataset import DatasetManager
from .auto_labeler import AutoLabeler
from .harness import AgentConfig, AgentRunResult, AgentHarness
from .scoring import (
    ClassificationMetrics,
    OperationalMetrics,
    FinancialMetrics,
    AgentScorecard,
    MetricsCalculator,
    format_scorecard,
    format_comparison,
)
from .report import ReportGenerator

__all__ = [
    "CatalystType",
    "StockOutcome",
    "CorrectSignal",
    "LabelConfidence",
    "LabeledScenario",
    "DatasetManager",
    "AutoLabeler",
    "AgentConfig",
    "AgentRunResult",
    "AgentHarness",
    "ClassificationMetrics",
    "OperationalMetrics",
    "FinancialMetrics",
    "AgentScorecard",
    "MetricsCalculator",
    "format_scorecard",
    "format_comparison",
    "ReportGenerator",
]
