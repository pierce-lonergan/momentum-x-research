"""Shadow-mode telemetry — write-only side channel.

Two shadow tracks:
  1. composite_shadow — per-candidate composite score logged at evaluation time
  2. inverted_shadow — separate post-9:36 batch on cascade-rejected candidates

Hard rule (enforced by tests/static_analysis/test_shadow_isolation.py):
  Production code MUST NOT read shadow fields. Shadow data is pure telemetry
  for post-session analysis. Any read path through shadow_v0 / shadow_inverted
  fields is a static-analysis failure.

Two-layer kill switch:
  SHADOW_SCORING_ENABLED — composite shadow (default True)
  SHADOW_INVERTED_ENABLED — inverted shadow (default True)

Either can be flipped to "false" via environment variable to disable
independently in seconds without code changes.
"""

from src.shadow.logger import ShadowLogger, get_shadow_logger
from src.shadow.composite_shadow import (
    maybe_score_composite,
    is_composite_shadow_enabled,
)
from src.shadow.inverted_shadow import (
    InvertedShadowEntry,
    INVERTED_ENTRY_BASIS,
    is_inverted_shadow_enabled,
    log_inverted_shadow_safe,
)

__all__ = [
    "ShadowLogger",
    "get_shadow_logger",
    "maybe_score_composite",
    "is_composite_shadow_enabled",
    "InvertedShadowEntry",
    "INVERTED_ENTRY_BASIS",
    "is_inverted_shadow_enabled",
    "log_inverted_shadow_safe",
]
