"""
MOMENTUM-X Experiment Registry

### ARCHITECTURAL CONTEXT
Node ID: experiments.registry
Graph Link: docs/memory/graph_state.json → "experiments.registry"

### DESIGN DECISIONS
- Loads experiment definitions from YAML (data/experiments/experiments.yaml)
- apply_overrides() creates deep copies of Settings — never mutates the active config
- Dotted-path override keys map to nested Pydantic sub-configs
- Singleton-style usage: one registry per Orchestrator instance

Ref: D102 (Experimentation Framework Phase 1)
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

from config.settings import Settings
from src.experiments.models import ExperimentConfig, ExperimentVariant

logger = logging.getLogger(__name__)

# Sub-config names in Settings that support dotted-path overrides.
# Maps the prefix of "scoring.mfcs_buy_threshold" to the Settings attribute name.
_SETTINGS_SECTIONS = {
    "scoring",
    "execution",
    "debate",
    "thresholds",
    "models",
    "exit_intelligence",
    "fast_path",
    "alpaca",
    "experiments",
}


class ExperimentRegistry:
    """
    Loads and manages experiment definitions from YAML.

    Usage:
        registry = ExperimentRegistry()
        registry.load(Path("data/experiments/experiments.yaml"))
        experiments = registry.get_active_experiments()
        for exp in experiments:
            for variant in exp.variants:
                variant_settings = registry.apply_overrides(settings, variant.overrides)
                # Re-score with variant_settings...

    Ref: D102 (Experimentation Framework Phase 1)
    """

    def __init__(self) -> None:
        self._experiments: list[ExperimentConfig] = []
        self._loaded = False

    @property
    def loaded(self) -> bool:
        """Whether experiments have been loaded from YAML."""
        return self._loaded

    @property
    def experiment_count(self) -> int:
        """Total number of experiments (active + disabled)."""
        return len(self._experiments)

    @property
    def variant_count(self) -> int:
        """Total number of variants across all active experiments."""
        return sum(
            len(exp.variants)
            for exp in self._experiments
            if exp.enabled
        )

    def load(self, yaml_path: Path | str) -> None:
        """
        Load experiment definitions from a YAML file.

        Args:
            yaml_path: Path to the experiments YAML file.

        Raises:
            FileNotFoundError: If the YAML file doesn't exist.
            ValueError: If the YAML structure is invalid.
        """
        yaml_path = Path(yaml_path)

        if not yaml_path.exists():
            raise FileNotFoundError(f"Experiment config not found: {yaml_path}")

        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not raw or not isinstance(raw, dict):
            raise ValueError(f"Invalid experiment YAML: expected dict, got {type(raw)}")

        experiments_raw = raw.get("experiments", [])
        if not isinstance(experiments_raw, list):
            raise ValueError(
                f"Invalid 'experiments' key: expected list, got {type(experiments_raw)}"
            )

        experiments = []
        for exp_raw in experiments_raw:
            if not isinstance(exp_raw, dict):
                logger.warning("D102: Skipping non-dict experiment entry: %s", exp_raw)
                continue

            # Parse variants
            variants_raw = exp_raw.get("variants", [])
            variants = []
            for var_raw in variants_raw:
                if not isinstance(var_raw, dict):
                    continue
                variants.append(
                    ExperimentVariant(
                        variant_id=str(var_raw.get("variant_id", "")),
                        overrides=var_raw.get("overrides", {}),
                    )
                )

            experiment = ExperimentConfig(
                experiment_id=str(exp_raw.get("experiment_id", "")),
                experiment_type=str(exp_raw.get("experiment_type", "parameter")),
                description=str(exp_raw.get("description", "")),
                enabled=bool(exp_raw.get("enabled", True)),
                variants=variants,
            )
            experiments.append(experiment)

        self._experiments = experiments
        self._loaded = True
        active = sum(1 for e in experiments if e.enabled)
        total_variants = sum(len(e.variants) for e in experiments if e.enabled)
        logger.info(
            "D102: Loaded %d experiments (%d active, %d total variants) from %s",
            len(experiments),
            active,
            total_variants,
            yaml_path,
        )

    def get_active_experiments(self) -> list[ExperimentConfig]:
        """Return only enabled experiments."""
        return [exp for exp in self._experiments if exp.enabled]

    def get_all_experiments(self) -> list[ExperimentConfig]:
        """Return all experiments including disabled ones."""
        return list(self._experiments)

    @staticmethod
    def apply_overrides(
        settings: Settings,
        overrides: dict[str, Any],
    ) -> Settings:
        """
        Create a modified COPY of Settings with overrides applied.

        Overrides use dotted paths mapping to nested Pydantic sub-configs:
            {"scoring.mfcs_buy_threshold": 0.30}
            → settings.scoring.mfcs_buy_threshold = 0.30

        CRITICAL: Never mutates the original Settings object. Returns a deep copy.

        Args:
            settings: The active Settings to base the copy on.
            overrides: Dict of dotted-path → value overrides.

        Returns:
            New Settings object with overrides applied.

        Raises:
            ValueError: If an override path doesn't match a known section.
        """
        if not overrides:
            return settings

        # Group overrides by section (first part of dotted path)
        section_updates: dict[str, dict[str, Any]] = {}
        root_updates: dict[str, Any] = {}

        for dotted_key, value in overrides.items():
            parts = dotted_key.split(".", 1)

            if len(parts) == 2 and parts[0] in _SETTINGS_SECTIONS:
                section_name, field_name = parts
                if section_name not in section_updates:
                    section_updates[section_name] = {}
                section_updates[section_name][field_name] = value
            elif len(parts) == 1:
                # Root-level setting (e.g., "mode", "log_level")
                root_updates[dotted_key] = value
            else:
                logger.warning(
                    "D102: Unknown override section '%s' in key '%s' — skipping",
                    parts[0] if parts else "?",
                    dotted_key,
                )

        # Build updated sub-configs
        updated_sections: dict[str, Any] = {}
        for section_name, field_updates in section_updates.items():
            current_sub = getattr(settings, section_name)
            # Use model_copy for Pydantic v2 deep copy with updates
            updated_sub = current_sub.model_copy(update=field_updates)
            updated_sections[section_name] = updated_sub

        # Combine section + root updates
        all_updates = {**updated_sections, **root_updates}

        # Create new Settings with all updates
        return settings.model_copy(update=all_updates)

    @staticmethod
    def validate_overrides(
        settings: Settings,
        overrides: dict[str, Any],
    ) -> list[str]:
        """
        Validate override keys against the Settings schema.

        Returns a list of error messages (empty = all valid).
        """
        errors = []
        for dotted_key, value in overrides.items():
            parts = dotted_key.split(".", 1)

            if len(parts) == 2:
                section_name, field_name = parts
                if section_name not in _SETTINGS_SECTIONS:
                    errors.append(f"Unknown section '{section_name}' in '{dotted_key}'")
                    continue

                sub_config = getattr(settings, section_name, None)
                if sub_config is None:
                    errors.append(f"Section '{section_name}' not found in Settings")
                    continue

                if not hasattr(sub_config, field_name):
                    errors.append(
                        f"Field '{field_name}' not found in {section_name} config"
                    )
            elif len(parts) == 1:
                if not hasattr(settings, dotted_key):
                    errors.append(f"Root field '{dotted_key}' not found in Settings")
            else:
                errors.append(f"Invalid override key format: '{dotted_key}'")

        return errors
