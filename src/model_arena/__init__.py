"""Cross-model evaluation harness for D220 Phase 7.

Wraps litellm to run identical labeled tasks against many models in parallel,
producing a cost-per-correct ranking. Sister to src/llm_arena/ (which compares
PROMPT variants on a single model); this module compares MODEL variants on a
single prompt.

Hard rules:
  - This package is INFRASTRUCTURE ONLY. No production agents are swapped.
  - The model catalog (catalog.yaml) is the source of truth for which models
    can be tested. Adding a model = appending an entry, no code change.
  - API keys via env vars (ANTHROPIC_API_KEY, TOGETHER_API_KEY or
    TOGETHER_AI_API_KEY). Missing keys are logged and the model is skipped.
  - Model IDs marked `verified: false` may fail to resolve at API call time —
    failures are logged and the model is excluded from the run, NEVER raised.
"""

# Auto-load .env if present so CLI invocations pick up project keys.
try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    _env_file = _Path(__file__).resolve().parents[2] / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:  # noqa: silent-handler
    pass  # python-dotenv optional; CLI users can `export` manually

from src.model_arena.models import ModelEntry, load_catalog, available_models
from src.model_arena.tasks import Task, CatalystClassificationTask, load_task
from src.model_arena.runner import ModelResponse, run_task
from src.model_arena.metrics import compute_metrics, rank_by_cost_per_correct

__all__ = [
    "ModelEntry",
    "load_catalog",
    "available_models",
    "Task",
    "CatalystClassificationTask",
    "load_task",
    "ModelResponse",
    "run_task",
    "compute_metrics",
    "rank_by_cost_per_correct",
]
