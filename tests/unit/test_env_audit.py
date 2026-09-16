"""
D221 Phase F: bug-sweep + happy-path tests for src/utils/env_audit.

Per the Mon morning Track 2 directive, this module ships with the same
adversarial discipline that found 9 bugs in the deterministic agents
on Sunday night. Targeted tests for the 7 classes named in the user's
spec:

  1. Var set to empty string ("")
  2. Var set to whitespace ("   ")
  3. Var set to a value with leading/trailing whitespace
  4. Audit function called before dotenv loads (env truly empty)
  5. Audit function called when os.environ is missing a var entirely
     vs set-to-None (note: os.environ values are str|None at the dict
     level, but env vars in Python only ever surface as str or KeyError;
     test the equivalent of "spec lists var, env lacks it")
  6. Malformed length hint (negative, None, huge) -- N/A in our impl
     because length comes from len(str), but test that the int output
     is in plausible range
  7. Heartbeat format when env-audit dict is unexpectedly empty

If this sweep surfaces more than 3 bugs, the user's directive is to
ship Option C instead. Going in with defensive design.
"""

from __future__ import annotations

import logging

import pytest

from src.utils.env_audit import (
    AuditEntry, EnvVarSpec, _ALL_SPECS, _classify_value,
    collect_env_audit, format_for_heartbeat, log_env_audit,
)


# ── Pure helper: _classify_value (the 7-class adversarial sweep) ─────


class TestClassifyValue:
    """The exact 7 adversarial input classes from the user's spec."""

    def test_none_unset(self):
        """Class 4: env var truly missing"""
        assert _classify_value(None) == ("<UNSET>", 0)

    def test_empty_string(self):
        """Class 1: var set but empty"""
        assert _classify_value("") == ("<EMPTY>", 0)

    def test_whitespace_only(self):
        """Class 2: var set to whitespace"""
        assert _classify_value("   ") == ("<WHITESPACE>", 3)

    def test_whitespace_tab_newline(self):
        """Class 2 expanded: tabs and newlines are whitespace"""
        assert _classify_value("\t\n  \t") == ("<WHITESPACE>", 5)

    def test_value_with_leading_whitespace(self):
        """Class 3: SET status but length reveals padding"""
        status, length = _classify_value("  abc")
        assert status == "<SET>"
        assert length == 5  # raw length, not stripped

    def test_value_with_trailing_whitespace(self):
        status, length = _classify_value("abc  ")
        assert status == "<SET>"
        assert length == 5

    def test_normal_value(self):
        assert _classify_value("hello") == ("<SET>", 5)

    def test_long_value(self):
        """Class 6 (length sanity): long values report accurate length"""
        url = "https://discordapp.com/api/webhooks/" + "x" * 100
        status, length = _classify_value(url)
        assert status == "<SET>"
        assert length == len(url)
        assert length > 100  # plausible

    def test_single_char(self):
        """Class 6: minimum non-empty length"""
        assert _classify_value("1") == ("<SET>", 1)

    def test_value_with_newlines_inside(self):
        """Multi-line value -- length includes all chars"""
        status, length = _classify_value("line1\nline2")
        assert status == "<SET>"
        assert length == 11


# ── collect_env_audit: deterministic given env state ─────────────────


class TestCollectEnvAudit:
    """Behavioral tests around the registry walk."""

    def test_empty_env_all_unset(self, monkeypatch):
        """Class 4: dotenv hasn't loaded; nothing in env."""
        # Clear every spec'd var
        for spec in _ALL_SPECS:
            monkeypatch.delenv(spec.name, raising=False)

        audit = collect_env_audit()
        assert len(audit) == len(_ALL_SPECS)
        for entry in audit:
            assert entry.status == "<UNSET>"
            assert entry.length == 0

    def test_critical_set_features_unset(self, monkeypatch):
        """Realistic state: API keys present, feature toggles dormant."""
        for spec in _ALL_SPECS:
            monkeypatch.delenv(spec.name, raising=False)
        monkeypatch.setenv("ALPACA_API_KEY", "PK_TESTING_20_CHARS_X")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "secret_value_here")
        monkeypatch.setenv("FINNHUB_API_KEY", "finnhub_key_xxx")
        monkeypatch.setenv("TOGETHER_AI_API_KEY", "together_key_xxx")

        audit = collect_env_audit()
        by_name = {e.name: e for e in audit}

        # CRITICAL all SET
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                  "FINNHUB_API_KEY", "TOGETHER_AI_API_KEY"):
            assert by_name[k].status == "<SET>"
            assert by_name[k].length > 0

        # FEATURE_GATED all UNSET
        for k in ("MOMENTUM_PERSIST_LIVE_FEATURES", "DECISION_LEARNING_WEBHOOK_URL"):
            assert by_name[k].status == "<UNSET>"

    def test_preserves_category_order(self, monkeypatch):
        """CRITICAL specs come first in audit output, then FEATURE_GATED, then SAFETY_*"""
        audit = collect_env_audit()
        categories_seen = []
        for entry in audit:
            if not categories_seen or categories_seen[-1] != entry.category:
                categories_seen.append(entry.category)
        # Should not see CRITICAL after FEATURE_GATED, etc.
        order = ["CRITICAL", "FEATURE_GATED", "SAFETY_KILL_SWITCHES"]
        idx = -1
        for cat in categories_seen:
            new_idx = order.index(cat)
            assert new_idx >= idx, (
                f"category order violated: {categories_seen}"
            )
            idx = new_idx

    def test_never_echoes_value(self, monkeypatch):
        """The audit MUST NOT include the raw value anywhere."""
        sentinel = "SUPER_SECRET_DO_NOT_LEAK"
        monkeypatch.setenv("ALPACA_API_KEY", sentinel)
        audit = collect_env_audit()
        for entry in audit:
            for field in (entry.status, entry.description, entry.name):
                assert sentinel not in field, (
                    f"audit leaked the value into field: {field}"
                )
        # Also check the dict serialization (heartbeat path)
        ser = [e.to_dict() for e in audit]
        ser_str = repr(ser)
        assert sentinel not in ser_str, "to_dict leaked the value"


# ── log_env_audit: integration with logging ──────────────────────────


class TestLogEnvAudit:
    """Verify log lines fire at correct levels."""

    def test_critical_unset_logged_at_warning(self, monkeypatch, caplog):
        for spec in _ALL_SPECS:
            monkeypatch.delenv(spec.name, raising=False)
        with caplog.at_level(logging.INFO, logger="src.utils.env_audit"):
            log_env_audit()
        warning_lines = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_lines) >= 1  # at least the summary
        # CRITICAL var lines should each be WARNING
        critical_lines = [r for r in warning_lines if "CRITICAL" in r.message]
        assert len(critical_lines) >= 4  # 4 CRITICAL specs + summary

    def test_all_set_summary_at_info(self, monkeypatch, caplog):
        # Set every CRITICAL
        for spec in _ALL_SPECS:
            if spec.category == "CRITICAL":
                monkeypatch.setenv(spec.name, "x" * 20)
            else:
                monkeypatch.delenv(spec.name, raising=False)

        with caplog.at_level(logging.INFO, logger="src.utils.env_audit"):
            log_env_audit()

        warning_lines = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_lines) == 0, "no warnings expected when all CRITICAL set"
        assert any(
            "ENV_AUDIT summary" in r.message and "all CRITICAL set" in r.message
            for r in caplog.records
        )

    def test_log_returns_audit_list(self, monkeypatch):
        """Caller can use the returned list for heartbeat embedding."""
        for spec in _ALL_SPECS:
            monkeypatch.delenv(spec.name, raising=False)
        result = log_env_audit()
        assert isinstance(result, list)
        assert len(result) == len(_ALL_SPECS)
        assert all(isinstance(e, AuditEntry) for e in result)


# ── format_for_heartbeat: shape + safety ─────────────────────────────


class TestFormatForHeartbeat:

    def test_shape_under_normal_state(self, monkeypatch):
        for spec in _ALL_SPECS:
            monkeypatch.delenv(spec.name, raising=False)
        for spec in _ALL_SPECS:
            if spec.category == "CRITICAL":
                monkeypatch.setenv(spec.name, "x" * 20)

        d = format_for_heartbeat()
        assert "captured_at" in d
        assert d["n_total"] == len(_ALL_SPECS)
        assert d["n_critical_unset"] == 0
        assert "by_category" in d
        assert set(d["by_category"].keys()) == {
            "CRITICAL", "FEATURE_GATED", "SAFETY_KILL_SWITCHES",
        }

    def test_n_critical_unset_correct(self, monkeypatch):
        for spec in _ALL_SPECS:
            monkeypatch.delenv(spec.name, raising=False)
        # Set only 2 of the 4 CRITICAL
        monkeypatch.setenv("ALPACA_API_KEY", "x" * 20)
        monkeypatch.setenv("ALPACA_SECRET_KEY", "x" * 20)

        d = format_for_heartbeat()
        assert d["n_critical_unset"] == 2  # FINNHUB + TOGETHER unset

    def test_class_7_empty_audit_passed_in(self):
        """Caller passes an empty list explicitly -- format must not crash."""
        d = format_for_heartbeat([])
        assert d["n_total"] == 0
        assert d["n_critical_unset"] == 0
        assert d["by_category"] == {}

    def test_value_never_in_heartbeat(self, monkeypatch):
        sentinel = "SUPER_SECRET_DO_NOT_LEAK_TO_HEARTBEAT"
        monkeypatch.setenv("ALPACA_API_KEY", sentinel)
        d = format_for_heartbeat()
        assert sentinel not in repr(d), "heartbeat dict leaked the value"

    def test_serializable_to_json(self, monkeypatch):
        """Heartbeat is JSON-serialized; this must not crash."""
        import json
        for spec in _ALL_SPECS:
            monkeypatch.setenv(spec.name, "x" * 5)
        d = format_for_heartbeat()
        s = json.dumps(d)
        # Round-trip
        d2 = json.loads(s)
        assert d2["n_total"] == d["n_total"]


# ── Idempotency + ordering ───────────────────────────────────────────


class TestIdempotency:

    def test_collect_twice_same_result(self, monkeypatch):
        """Class 4-related: calling audit twice with same env yields identical
        AuditEntries (modulo captured_at in heartbeat path)."""
        monkeypatch.setenv("ALPACA_API_KEY", "x" * 20)
        a = collect_env_audit()
        b = collect_env_audit()
        # Compare by name->status mapping (ignore object identity)
        ka = {(e.name, e.status, e.length) for e in a}
        kb = {(e.name, e.status, e.length) for e in b}
        assert ka == kb

    def test_specs_registry_is_immutable(self):
        """_ALL_SPECS is a tuple (frozen registry)."""
        assert isinstance(_ALL_SPECS, tuple)
        # Each spec is also frozen
        for spec in _ALL_SPECS:
            with pytest.raises((AttributeError, Exception)):
                spec.name = "mutated"  # type: ignore[misc]
