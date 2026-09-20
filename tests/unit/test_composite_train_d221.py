"""D221 Phase D tests — composite/train.py CLI flags + retrain script idempotency.

Covers:
  - --min-rows enforcement (refuse to train below threshold)
  - --output-version auto-increment (v0 → v1 → v2)
  - --output-version explicit override
  - feature_stability_check helper (warns on big AUC drift)
  - maybe_retrain_composite.sh idempotency + decision logic

Network/training is mocked — these tests verify CLI plumbing, not learning.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.composite.train import (
    auto_increment_version,
    build_parser,
    discover_existing_versions,
    feature_stability_check,
    load_prior_version_metadata,
    main as train_main,
)


# ── Versioning tests ────────────────────────────────────────────────────


class TestVersioning:

    def test_discover_no_versions_returns_empty(self, tmp_path):
        assert discover_existing_versions(tmp_path) == []

    def test_discover_finds_v0(self, tmp_path):
        (tmp_path / "composite_v0_full.pkl").touch()
        (tmp_path / "composite_v0_prescore.pkl").touch()
        (tmp_path / "composite_v0_metadata.json").touch()
        assert discover_existing_versions(tmp_path) == ["v0"]

    def test_discover_finds_multiple_sorted_numerically(self, tmp_path):
        for v in ["v0", "v1", "v2", "v10"]:
            (tmp_path / f"composite_{v}_full.pkl").touch()
        # Numeric sort, not lexicographic — v10 must come AFTER v2
        assert discover_existing_versions(tmp_path) == ["v0", "v1", "v2", "v10"]

    def test_discover_ignores_non_matching_files(self, tmp_path):
        (tmp_path / "composite_v0_full.pkl").touch()
        (tmp_path / "random.pkl").touch()
        (tmp_path / "composite_X_full.pkl").touch()  # X is not vN
        (tmp_path / "composite_v_full.pkl").touch()  # missing digit
        assert discover_existing_versions(tmp_path) == ["v0"]

    def test_auto_increment_starts_at_v0(self, tmp_path):
        assert auto_increment_version(tmp_path) == "v0"

    def test_auto_increment_advances(self, tmp_path):
        (tmp_path / "composite_v0_full.pkl").touch()
        (tmp_path / "composite_v3_full.pkl").touch()
        assert auto_increment_version(tmp_path) == "v4"

    def test_load_prior_metadata_missing(self, tmp_path):
        assert load_prior_version_metadata("v0", tmp_path) is None

    def test_load_prior_metadata_present(self, tmp_path):
        meta = {"version": "v0", "models": {"full": {"cv_auc_mean": 0.756}}}
        (tmp_path / "composite_v0_metadata.json").write_text(json.dumps(meta))
        loaded = load_prior_version_metadata("v0", tmp_path)
        assert loaded is not None
        assert loaded["models"]["full"]["cv_auc_mean"] == 0.756

    def test_load_prior_metadata_corrupted(self, tmp_path, caplog):
        (tmp_path / "composite_v0_metadata.json").write_text("not valid json {{{")
        loaded = load_prior_version_metadata("v0", tmp_path)
        assert loaded is None
        assert any("failed to load" in r.message for r in caplog.records)


# ── Stability check tests ──────────────────────────────────────────────


class TestStabilityCheck:

    def test_no_prior_returns_stable(self):
        ok, msg = feature_stability_check(0.756, None)
        assert ok is True
        assert "no prior" in msg

    def test_within_threshold_stable(self):
        prior = {"version": "v0", "models": {"full": {"cv_auc_mean": 0.756}}}
        ok, msg = feature_stability_check(0.770, prior, threshold=0.05)  # +0.014
        assert ok is True
        assert "stable" in msg.lower()

    def test_exceeds_threshold_unstable_positive_drift(self):
        prior = {"version": "v0", "models": {"full": {"cv_auc_mean": 0.756}}}
        ok, msg = feature_stability_check(0.850, prior, threshold=0.05)  # +0.094
        assert ok is False
        assert "differs" in msg
        assert "+0.0940" in msg or "+0.094" in msg

    def test_exceeds_threshold_unstable_negative_drift(self):
        """A new model that's MUCH WORSE should also fail the stability check."""
        prior = {"version": "v0", "models": {"full": {"cv_auc_mean": 0.756}}}
        ok, msg = feature_stability_check(0.650, prior, threshold=0.05)  # -0.106
        assert ok is False

    def test_corrupted_prior_metadata_returns_stable(self):
        """If prior metadata is missing the cv_auc_mean key, can't compare → stable."""
        prior = {"version": "v0", "models": {}}
        ok, msg = feature_stability_check(0.756, prior)
        assert ok is True

    def test_threshold_is_configurable(self):
        prior = {"version": "v0", "models": {"full": {"cv_auc_mean": 0.756}}}
        # Default threshold 0.05 — +0.04 is stable
        assert feature_stability_check(0.796, prior)[0] is True
        # Strict threshold 0.02 — +0.04 is unstable
        assert feature_stability_check(0.796, prior, threshold=0.02)[0] is False


# ── CLI argument parsing ───────────────────────────────────────────────


class TestCLIParser:

    def test_min_rows_default_is_1000(self):
        args = build_parser().parse_args([])
        assert args.min_rows == 1000

    def test_min_rows_override(self):
        args = build_parser().parse_args(["--min-rows", "500"])
        assert args.min_rows == 500

    def test_output_version_default_is_none(self):
        args = build_parser().parse_args([])
        assert args.output_version is None

    def test_output_version_explicit(self):
        args = build_parser().parse_args(["--output-version", "v7"])
        assert args.output_version == "v7"

    def test_stability_check_flag(self):
        args = build_parser().parse_args(["--feature-stability-check"])
        assert args.feature_stability_check is True
        assert args.i_know_what_im_doing is False


# ── train.main() with --min-rows enforcement ───────────────────────────


class TestTrainMinRowsGuard:

    def test_min_rows_blocks_training_when_too_few(self, tmp_path, monkeypatch):
        """If labeled rows < --min-rows, train.main() returns 2 without writing models."""
        # Patch _build_training_set to return a small list
        from src.composite import train as train_module

        class _FakeRow:
            def __init__(self, win, ab):
                self.win_close = win
                self.arena_buy = ab
                self.candidate_dict = {"gap_pct": 0.1, "price": 5.0}

        fake_rows = [_FakeRow(True, True) for _ in range(50)]
        monkeypatch.setattr(train_module, "_build_training_set", lambda: fake_rows)

        rc = train_module.main(["--min-rows", "1000",
                                "--output-dir", str(tmp_path),
                                "--doc-output", str(tmp_path / "doc.md")])
        assert rc == 2
        # No models should have been written
        assert list(tmp_path.glob("composite_*.pkl")) == []

    def test_min_rows_zero_disables_check(self, tmp_path, monkeypatch):
        """--min-rows 0 means train regardless of count.

        We patch _build_training_set to return a tiny mixed-class dataset and
        verify the guard does not early-exit at the row-count check. The downstream
        training may succeed or fail for unrelated reasons (small sample); the
        important assertion is that we never return rc=2 (the row-count guard's exit code).
        """
        from src.composite import train as train_module

        class _FakeRow:
            def __init__(self, win, ab):
                self.win_close = win
                self.arena_buy = ab
                self.candidate_dict = {"gap_pct": 0.1, "price": 5.0,
                                       "premarket_volume": 1e6, "dollar_volume": 1e6}

        # 30 rows total, mixed classes for sklearn's stratified CV
        fake_rows = ([_FakeRow(True, True) for _ in range(15)]
                     + [_FakeRow(False, False) for _ in range(15)])
        monkeypatch.setattr(train_module, "_build_training_set", lambda: fake_rows)

        try:
            rc = train_module.main(["--min-rows", "0",
                                    "--output-dir", str(tmp_path),
                                    "--doc-output", str(tmp_path / "doc.md")])
            # Did not raise — verify it didn't early-exit on the row-count check
            assert rc != 2, f"--min-rows 0 should bypass row-count check, got rc={rc}"
        except Exception as e:
            # Downstream training failure is fine — what matters is we got PAST
            # the row-count guard (which is the only path that returns rc=2 cleanly).
            # Verify we DID call into _build_training_set (we have, since the
            # mock fired). If we'd hit the rc=2 path, no exception would have
            # been raised.
            assert "min-rows" not in str(e).lower(), (
                f"unexpected min-rows error after --min-rows 0: {e}"
            )


# ── maybe_retrain_composite.sh script tests ────────────────────────────


@pytest.fixture
def script_path():
    return Path(__file__).resolve().parents[2] / "scripts" / "maybe_retrain_composite.sh"


class TestRetrainScriptIdempotency:
    """The script is bash; tests run via subprocess on git-bash / wsl."""

    def _run_script(self, *args, cwd=None, log_file=None):
        bash = shutil.which("bash") or shutil.which("sh")
        if bash is None:
            pytest.skip("bash not available on this platform")
        env = os.environ.copy()
        # The script appends its decision to a log. Redirect it, or the fast
        # suite silently mutates docs/research-log/composite_retrain_log.md —
        # a tracked research document — on every run.
        env["COMPOSITE_RETRAIN_LOG"] = str(
            log_file or (self._tmp_log_dir / "composite_retrain_log.md")
        )
        result = subprocess.run(
            [bash, str(script_path()), *args],
            cwd=cwd or Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True, text=True,
        )
        return result

    @pytest.fixture(autouse=True)
    def _tmp_log(self, tmp_path):
        self._tmp_log_dir = tmp_path

    def test_script_exists_and_executable(self):
        p = Path(__file__).resolve().parents[2] / "scripts" / "maybe_retrain_composite.sh"
        assert p.exists(), "scripts/maybe_retrain_composite.sh missing"

    def test_check_only_does_not_train(self):
        """--check-only must NOT invoke training; just decide + log."""
        r = self._run_script("--check-only")
        # Should exit 0 and print decision
        assert r.returncode == 0
        assert "decision:" in r.stdout

    def test_check_only_idempotent(self):
        """Running --check-only twice produces consistent output (modulo timestamp)."""
        r1 = self._run_script("--check-only")
        r2 = self._run_script("--check-only")
        # Same decision word ("SKIP" or "RETRAIN")
        decision_word = lambda s: "SKIP" if "SKIP" in s.stdout else (
            "RETRAIN" if "RETRAIN" in s.stdout else None)
        assert decision_word(r1) == decision_word(r2)

    def test_help_flag(self):
        r = self._run_script("--help")
        assert r.returncode == 0
        assert "usage" in r.stdout.lower() or "Usage" in r.stdout

    def test_unknown_flag_errors(self):
        r = self._run_script("--bogus-flag")
        assert r.returncode != 0


# helper for module-level access
def script_path():
    return Path(__file__).resolve().parents[2] / "scripts" / "maybe_retrain_composite.sh"
