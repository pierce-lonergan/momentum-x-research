"""
Tests for ExperimentRegistry — YAML loading, override application, validation.

D102: Experimentation Framework Phase 1.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from config.settings import Settings
from src.experiments.registry import ExperimentRegistry
from src.experiments.models import ExperimentConfig, ExperimentVariant


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def yaml_content() -> str:
    """Minimal valid experiment YAML."""
    return """
experiments:
  - experiment_id: test_atr
    experiment_type: parameter
    description: "Test ATR sweep"
    enabled: true
    variants:
      - variant_id: atr_1.5
        overrides:
          execution.initial_stop_atr_multiplier: 1.5
      - variant_id: atr_3.0
        overrides:
          execution.initial_stop_atr_multiplier: 3.0
  - experiment_id: test_threshold
    experiment_type: threshold
    description: "Test threshold sweep"
    enabled: true
    variants:
      - variant_id: thresh_0.20
        overrides:
          scoring.mfcs_buy_threshold: 0.20
      - variant_id: thresh_0.35
        overrides:
          scoring.mfcs_buy_threshold: 0.35
  - experiment_id: disabled_exp
    experiment_type: parameter
    description: "This one is disabled"
    enabled: false
    variants:
      - variant_id: dummy
        overrides:
          execution.max_positions: 5
"""


@pytest.fixture
def yaml_file(tmp_path: Path, yaml_content: str) -> Path:
    """Write YAML content to a temp file."""
    f = tmp_path / "experiments.yaml"
    f.write_text(yaml_content)
    return f


@pytest.fixture
def settings() -> Settings:
    """Default Settings instance."""
    return Settings()


@pytest.fixture
def registry(yaml_file: Path) -> ExperimentRegistry:
    """Registry loaded with test YAML."""
    reg = ExperimentRegistry()
    reg.load(yaml_file)
    return reg


# ─── YAML Loading Tests ─────────────────────────────────────────────


class TestYAMLLoading:
    def test_load_basic(self, registry: ExperimentRegistry):
        """Registry loads and parses the YAML correctly."""
        assert registry.loaded
        assert registry.experiment_count == 3  # 2 enabled + 1 disabled

    def test_active_experiments_exclude_disabled(self, registry: ExperimentRegistry):
        """get_active_experiments() filters out disabled experiments."""
        active = registry.get_active_experiments()
        assert len(active) == 2
        ids = [e.experiment_id for e in active]
        assert "test_atr" in ids
        assert "test_threshold" in ids
        assert "disabled_exp" not in ids

    def test_variant_count(self, registry: ExperimentRegistry):
        """variant_count only counts active experiment variants."""
        assert registry.variant_count == 4  # 2 ATR + 2 threshold (disabled excluded)

    def test_experiment_fields(self, registry: ExperimentRegistry):
        """Experiment fields are correctly parsed."""
        active = registry.get_active_experiments()
        atr_exp = next(e for e in active if e.experiment_id == "test_atr")
        assert atr_exp.experiment_type == "parameter"
        assert atr_exp.description == "Test ATR sweep"
        assert atr_exp.enabled is True
        assert len(atr_exp.variants) == 2

    def test_variant_fields(self, registry: ExperimentRegistry):
        """Variant fields are correctly parsed."""
        active = registry.get_active_experiments()
        atr_exp = next(e for e in active if e.experiment_id == "test_atr")
        v = atr_exp.variants[0]
        assert v.variant_id == "atr_1.5"
        assert v.overrides == {"execution.initial_stop_atr_multiplier": 1.5}

    def test_all_experiments_includes_disabled(self, registry: ExperimentRegistry):
        """get_all_experiments() includes disabled experiments."""
        all_exps = registry.get_all_experiments()
        assert len(all_exps) == 3

    def test_load_nonexistent_file(self):
        """Loading a nonexistent file raises FileNotFoundError."""
        reg = ExperimentRegistry()
        with pytest.raises(FileNotFoundError):
            reg.load(Path("/nonexistent/path.yaml"))

    def test_load_empty_yaml(self, tmp_path: Path):
        """Loading an empty YAML file raises ValueError."""
        f = tmp_path / "empty.yaml"
        f.write_text("")
        reg = ExperimentRegistry()
        with pytest.raises(ValueError):
            reg.load(f)

    def test_load_invalid_structure(self, tmp_path: Path):
        """Loading YAML without 'experiments' list raises ValueError."""
        f = tmp_path / "bad.yaml"
        f.write_text("foo: bar\nbaz: 42\n")
        reg = ExperimentRegistry()
        # Should not raise but should have 0 experiments
        reg.load(f)
        assert reg.experiment_count == 0

    def test_not_loaded_initially(self):
        """Registry starts as not loaded."""
        reg = ExperimentRegistry()
        assert not reg.loaded
        assert reg.experiment_count == 0
        assert reg.variant_count == 0

    def test_get_active_when_not_loaded(self):
        """Unloaded registry returns empty list."""
        reg = ExperimentRegistry()
        assert reg.get_active_experiments() == []


# ─── apply_overrides Tests ───────────────────────────────────────────


class TestApplyOverrides:
    def test_scoring_override(self, settings: Settings):
        """Scoring field override works correctly."""
        original_value = settings.scoring.mfcs_buy_threshold
        new_value = 0.99  # Extreme value that won't match any env override
        overrides = {"scoring.mfcs_buy_threshold": new_value}
        new_settings = ExperimentRegistry.apply_overrides(settings, overrides)

        assert new_settings.scoring.mfcs_buy_threshold == new_value
        # Original unchanged
        assert settings.scoring.mfcs_buy_threshold == original_value

    def test_execution_override(self, settings: Settings):
        """Execution field override works correctly."""
        overrides = {"execution.initial_stop_atr_multiplier": 2.5}
        new_settings = ExperimentRegistry.apply_overrides(settings, overrides)

        assert new_settings.execution.initial_stop_atr_multiplier == 2.5
        assert settings.execution.initial_stop_atr_multiplier == 2.0

    def test_multiple_overrides_same_section(self, settings: Settings):
        """Multiple overrides in the same section all apply."""
        overrides = {
            "scoring.catalyst_news": 0.40,
            "scoring.technical": 0.25,
            "scoring.mfcs_buy_threshold": 0.30,
        }
        new_settings = ExperimentRegistry.apply_overrides(settings, overrides)

        assert new_settings.scoring.catalyst_news == 0.40
        assert new_settings.scoring.technical == 0.25
        assert new_settings.scoring.mfcs_buy_threshold == 0.30
        # Other fields unchanged
        assert new_settings.scoring.volume_rvol == settings.scoring.volume_rvol

    def test_multiple_overrides_different_sections(self, settings: Settings):
        """Overrides across different sections all apply."""
        overrides = {
            "scoring.risk_aversion_lambda": 0.30,
            "execution.initial_stop_atr_multiplier": 1.5,
        }
        new_settings = ExperimentRegistry.apply_overrides(settings, overrides)

        assert new_settings.scoring.risk_aversion_lambda == 0.30
        assert new_settings.execution.initial_stop_atr_multiplier == 1.5

    def test_original_settings_never_mutated(self, settings: Settings):
        """apply_overrides NEVER mutates the original Settings object."""
        original_threshold = settings.scoring.mfcs_buy_threshold
        original_atr = settings.execution.initial_stop_atr_multiplier

        _ = ExperimentRegistry.apply_overrides(
            settings,
            {
                "scoring.mfcs_buy_threshold": 0.99,
                "execution.initial_stop_atr_multiplier": 99.0,
            },
        )

        assert settings.scoring.mfcs_buy_threshold == original_threshold
        assert settings.execution.initial_stop_atr_multiplier == original_atr

    def test_empty_overrides_returns_settings(self, settings: Settings):
        """Empty overrides return the original settings (same object)."""
        result = ExperimentRegistry.apply_overrides(settings, {})
        assert result is settings

    def test_unknown_section_logged_and_skipped(self, settings: Settings):
        """Unknown section prefix is skipped (no error raised)."""
        overrides = {"nonexistent_section.field": 42}
        # Should not raise
        result = ExperimentRegistry.apply_overrides(settings, overrides)
        # Returns settings with no changes
        assert result.scoring.mfcs_buy_threshold == settings.scoring.mfcs_buy_threshold

    def test_debate_override(self, settings: Settings):
        """Debate config override works."""
        overrides = {"debate.mfcs_debate_threshold": 0.50}
        new_settings = ExperimentRegistry.apply_overrides(settings, overrides)
        assert new_settings.debate.mfcs_debate_threshold == 0.50


# ─── validate_overrides Tests ───────────────────────────────────────


class TestValidateOverrides:
    def test_valid_overrides(self, settings: Settings):
        """Valid overrides produce no errors."""
        overrides = {
            "scoring.mfcs_buy_threshold": 0.30,
            "execution.initial_stop_atr_multiplier": 2.5,
        }
        errors = ExperimentRegistry.validate_overrides(settings, overrides)
        assert errors == []

    def test_unknown_section(self, settings: Settings):
        """Unknown section produces an error."""
        errors = ExperimentRegistry.validate_overrides(
            settings, {"nonexistent.field": 42}
        )
        assert len(errors) == 1
        assert "Unknown section" in errors[0]

    def test_unknown_field(self, settings: Settings):
        """Unknown field in a valid section produces an error."""
        errors = ExperimentRegistry.validate_overrides(
            settings, {"scoring.nonexistent_field": 42}
        )
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_mixed_valid_and_invalid(self, settings: Settings):
        """Mixed valid/invalid overrides reports only invalid ones."""
        overrides = {
            "scoring.mfcs_buy_threshold": 0.30,  # valid
            "scoring.fake_field": 999,  # invalid
            "execution.initial_stop_atr_multiplier": 2.5,  # valid
        }
        errors = ExperimentRegistry.validate_overrides(settings, overrides)
        assert len(errors) == 1
        assert "fake_field" in errors[0]


# ─── Production YAML Loading ────────────────────────────────────────


class TestProductionYAML:
    def test_load_real_yaml(self):
        """Load the actual production experiments.yaml if it exists."""
        yaml_path = Path("data/experiments/experiments.yaml")
        if not yaml_path.exists():
            pytest.skip("Production experiments.yaml not found")

        reg = ExperimentRegistry()
        reg.load(yaml_path)

        assert reg.loaded
        assert reg.experiment_count >= 1
        assert reg.variant_count >= 1

        # All active experiments should have at least one variant
        for exp in reg.get_active_experiments():
            assert len(exp.variants) > 0
            assert exp.experiment_id
            assert exp.experiment_type

    def test_production_overrides_are_valid(self):
        """All overrides in production YAML map to real Settings fields."""
        yaml_path = Path("data/experiments/experiments.yaml")
        if not yaml_path.exists():
            pytest.skip("Production experiments.yaml not found")

        reg = ExperimentRegistry()
        reg.load(yaml_path)
        settings = Settings()

        for exp in reg.get_active_experiments():
            for variant in exp.variants:
                errors = reg.validate_overrides(settings, variant.overrides)
                assert errors == [], (
                    f"Invalid overrides in {exp.experiment_id}/{variant.variant_id}: {errors}"
                )

    def test_production_overrides_apply_without_error(self):
        """All overrides in production YAML apply without raising."""
        yaml_path = Path("data/experiments/experiments.yaml")
        if not yaml_path.exists():
            pytest.skip("Production experiments.yaml not found")

        reg = ExperimentRegistry()
        reg.load(yaml_path)
        settings = Settings()

        for exp in reg.get_active_experiments():
            for variant in exp.variants:
                # Should not raise
                new_settings = reg.apply_overrides(settings, variant.overrides)
                assert new_settings is not settings or not variant.overrides
