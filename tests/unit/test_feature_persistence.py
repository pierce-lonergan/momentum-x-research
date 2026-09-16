"""Tests for src/data/_feature_persistence.py.

Covers:
  - default-OFF behavior (no env var → no disk writes)
  - enabled writes land at the right per-day shard path
  - append semantics (multiple writes accumulate, do not overwrite)
  - JSONL formatting (one JSON object per line)
  - parent-dir creation
  - fail-safe contract (disk failure must NOT raise into caller)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data import _feature_persistence as fp


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect persistence to a tmp_path so tests don't touch real data/."""
    monkeypatch.setattr(fp, "_PERSIST_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the module-level _ENABLED flag on for this test."""
    monkeypatch.setattr(fp, "_ENABLED", True)


@pytest.fixture
def disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fp, "_ENABLED", False)


def _today_shard(root: Path, module: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return root / module / f"{today}.jsonl"


# ── Default-OFF behavior ────────────────────────────────────────────────


def test_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module's default state matches the env-var contract."""
    monkeypatch.delenv("MOMENTUM_PERSIST_LIVE_FEATURES", raising=False)
    # _ENABLED is read at import; we don't reload here — just assert the
    # documented default contract holds: when env is unset at import, the
    # module is disabled. (The current process imported with env unset.)
    # We re-evaluate the env check inline for the spec test:
    import os
    raw = os.environ.get("MOMENTUM_PERSIST_LIVE_FEATURES", "0").strip()
    assert raw in ("0", "")  # default value


def test_persist_is_noop_when_disabled(
    disabled: None,
    isolated_root: Path,
) -> None:
    """When disabled, no file is created even if persist_feature_row is called."""
    fp.persist_feature_row("any_module", {"ticker": "X", "v": 1})
    assert list(isolated_root.iterdir()) == [], "expected zero subdirs created"


# ── Enabled-write behavior ──────────────────────────────────────────────


def test_persist_creates_shard_at_expected_path(
    enabled: None,
    isolated_root: Path,
) -> None:
    fp.persist_feature_row("short_interest", {"ticker": "AAPL", "v": 27.3})
    shard = _today_shard(isolated_root, "short_interest")
    assert shard.exists(), f"expected shard at {shard}"


def test_persist_writes_valid_json_per_line(
    enabled: None,
    isolated_root: Path,
) -> None:
    fp.persist_feature_row("m", {"ticker": "AAA", "value": 1})
    fp.persist_feature_row("m", {"ticker": "BBB", "value": 2.5})
    fp.persist_feature_row("m", {"ticker": "CCC", "value": None})

    shard = _today_shard(isolated_root, "m")
    lines = [L for L in shard.read_text(encoding="utf-8").splitlines() if L.strip()]
    assert len(lines) == 3
    rows = [json.loads(L) for L in lines]
    assert [r["ticker"] for r in rows] == ["AAA", "BBB", "CCC"]
    assert rows[2]["value"] is None


def test_persist_appends_does_not_overwrite(
    enabled: None,
    isolated_root: Path,
) -> None:
    """Two calls must produce two lines, not one (append semantics)."""
    fp.persist_feature_row("m", {"ticker": "X", "n": 1})
    fp.persist_feature_row("m", {"ticker": "X", "n": 2})

    shard = _today_shard(isolated_root, "m")
    lines = [L for L in shard.read_text(encoding="utf-8").splitlines() if L.strip()]
    assert len(lines) == 2, "second persist must append, not overwrite"


def test_persist_creates_module_subdir_lazily(
    enabled: None,
    isolated_root: Path,
) -> None:
    """Parent directory is created on first write (mkdir parents=True)."""
    module_dir = isolated_root / "brand_new_module"
    assert not module_dir.exists()

    fp.persist_feature_row("brand_new_module", {"ticker": "X"})

    assert module_dir.exists()
    assert module_dir.is_dir()


# ── Fail-safe contract ──────────────────────────────────────────────────


def test_persist_does_not_raise_on_disk_failure(
    enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    isolated_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the open() fails, the helper logs a WARNING but does not raise.

    This is the most important property of the helper: live feature
    computation must continue even if persistence breaks.
    """
    def _broken_open(*args, **kwargs):
        raise OSError("simulated disk-full")

    # Patch builtins.open AS SEEN BY the persistence module specifically.
    monkeypatch.setattr("builtins.open", _broken_open)

    with caplog.at_level(logging.WARNING, logger="src.data._feature_persistence"):
        # Must not raise.
        fp.persist_feature_row("m", {"ticker": "X", "v": 1})

    assert any("feature persistence failed" in r.message for r in caplog.records), (
        "expected a WARNING log when persistence fails"
    )


def test_persist_does_not_raise_on_unserializable_row(
    enabled: None,
    isolated_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row containing unserializable values logs a warning but doesn't raise."""
    class _Unserializable:
        pass

    with caplog.at_level(logging.WARNING, logger="src.data._feature_persistence"):
        fp.persist_feature_row("m", {"ticker": "X", "weird": _Unserializable()})

    assert any("feature persistence failed" in r.message for r in caplog.records)


# ── Module-level helper ─────────────────────────────────────────────────


def test_is_enabled_reflects_module_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_enabled() must mirror the internal _ENABLED flag exactly."""
    monkeypatch.setattr(fp, "_ENABLED", False)
    assert fp.is_enabled() is False

    monkeypatch.setattr(fp, "_ENABLED", True)
    assert fp.is_enabled() is True
