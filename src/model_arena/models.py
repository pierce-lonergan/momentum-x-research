"""Model catalog loader for cross-model arena.

Reads catalog.yaml; produces ModelEntry instances. No I/O outside catalog load.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parent / "catalog.yaml"


@dataclass(frozen=True)
class ModelEntry:
    """One model in the cross-model catalog."""
    key: str                                   # short id used in CLI flags
    provider: str                              # 'anthropic' / 'together' / 'openai'
    api_model_id: str                          # litellm-format model string
    input_price_per_mtok: float                # USD per 1M input tokens
    output_price_per_mtok: float               # USD per 1M output tokens
    context_window: int
    notes: str = ""
    enabled: bool = True
    verified: bool = False                     # confirmed against provider API

    def cost_for(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            (prompt_tokens / 1_000_000) * self.input_price_per_mtok
            + (completion_tokens / 1_000_000) * self.output_price_per_mtok
        )

    def has_api_key(self) -> bool:
        # Provider may have multiple env-var aliases (the project's .env uses
        # TOGETHER_AI_API_KEY rather than TOGETHER_API_KEY, and we want to
        # accept both).
        candidates = {
            "anthropic": ("ANTHROPIC_API_KEY",),
            "together": ("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY", "TOGETHERAI_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }.get(self.provider, ())
        return any(bool(os.environ.get(v)) for v in candidates)

    def get_api_key(self) -> str | None:
        """Return the actual key string for this provider, or None if missing."""
        candidates = {
            "anthropic": ("ANTHROPIC_API_KEY",),
            "together": ("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY", "TOGETHERAI_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }.get(self.provider, ())
        for v in candidates:
            val = os.environ.get(v)
            if val:
                return val
        return None


def load_catalog(path: Path | str | None = None) -> list[ModelEntry]:
    """Load the catalog from YAML. Returns ALL entries, including disabled.

    Use `available_models()` to filter to enabled + has-key models.
    """
    p = Path(path) if path else _CATALOG_PATH
    if not p.exists():
        raise FileNotFoundError(f"catalog YAML not found at {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    raw = data.get("models", []) or []
    out: list[ModelEntry] = []
    for r in raw:
        try:
            out.append(ModelEntry(
                key=r["key"],
                provider=r["provider"],
                api_model_id=r["api_model_id"],
                input_price_per_mtok=float(r["input_price_per_mtok"]),
                output_price_per_mtok=float(r["output_price_per_mtok"]),
                context_window=int(r.get("context_window", 32000)),
                notes=r.get("notes", ""),
                enabled=bool(r.get("enabled", True)),
                verified=bool(r.get("verified", False)),
            ))
        except (KeyError, ValueError) as e:
            logger.warning("catalog: skipping malformed entry %r: %s", r.get("key"), e)
    return out


def available_models(
    keys: list[str] | None = None,
    require_verified: bool = False,
    require_key: bool = True,
) -> list[ModelEntry]:
    """Return models that are enabled, have an API key, and (optionally) verified.

    Args:
        keys: optional whitelist of model keys to filter to.
        require_verified: drop models marked verified=false.
        require_key: drop models without a corresponding env var set.
    """
    out: list[ModelEntry] = []
    catalog = load_catalog()
    keyset = set(keys) if keys else None
    for m in catalog:
        if not m.enabled:
            continue
        if keyset and m.key not in keyset:
            continue
        if require_verified and not m.verified:
            continue
        if require_key and not m.has_api_key():
            logger.info("model_arena: skipping %s (provider=%s, no API key)", m.key, m.provider)
            continue
        out.append(m)
    return out
