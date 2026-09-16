"""
D221 Bug #3 (Mon 2026-04-20): tests for src.utils.crash_report.

Adversarial sweep + rule-(e) positive-case test, per the hardened
bug-sweep template added Mon 2026-04-20.

The 5 adversarial classes for crash-report writing:
  1. Heartbeat file MISSING (most common case at session start)
  2. Heartbeat file EXISTS but malformed JSON
  3. Heartbeat file EXISTS but unexpected shape (list, not dict)
  4. Crash report dir DOESN'T exist (first session ever)
  5. Exception with non-string str() (custom __str__ raises)

Plus rule (e): under normal conditions, the report file MUST be
written, MUST contain valid JSON, MUST have all expected keys, and
the traceback MUST be non-empty. The "fail-safe returns None" branch
in write_crash_report is exactly the kind of defensive catch that
hides production failures otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils import crash_report
from src.utils.crash_report import (
    EXIT_CODE_CANCELLED,
    EXIT_CODE_UNHANDLED,
    _coerce_jsonable,
    _is_cancelled_error,
    _read_last_heartbeat,
    _safe_repr,
    write_crash_report,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_crash_dir(tmp_path: Path) -> Path:
    """Per-test crash_dir under pytest tmp_path. Avoids touching the
    real data/crash_reports/ during unit tests."""
    d = tmp_path / "crash_reports"
    return d


def _raise_and_capture(exc: BaseException) -> BaseException:
    """Raise + catch to attach a real traceback (write_crash_report
    formats it via traceback.format_exception)."""
    try:
        raise exc
    except BaseException as e:
        return e


# ── Class 1: Heartbeat MISSING ────────────────────────────────────────


class TestHeartbeatMissing:

    def test_no_heartbeat_returns_none(self, monkeypatch, tmp_path):
        """When the heartbeat path doesn't exist, _read_last_heartbeat
        returns None silently. No log spam, no crash."""
        nonexistent = tmp_path / "no-such-heartbeat.json"
        monkeypatch.setattr(
            crash_report, "_heartbeat_path", lambda: nonexistent,
        )
        assert _read_last_heartbeat() is None

    def test_write_report_with_missing_heartbeat(
        self, monkeypatch, tmp_path, tmp_crash_dir,
    ):
        """When heartbeat is missing, the crash report is still
        written successfully with last_heartbeat=None."""
        monkeypatch.setattr(
            crash_report, "_heartbeat_path",
            lambda: tmp_path / "no-such-heartbeat.json",
        )
        exc = _raise_and_capture(ValueError("synthetic"))
        out = write_crash_report(exc, crash_dir=tmp_crash_dir)
        assert out is not None
        payload = json.loads(out.read_text())
        assert payload["last_heartbeat"] is None
        assert payload["exception_type"] == "ValueError"


# ── Class 2: Heartbeat MALFORMED JSON ────────────────────────────────


class TestHeartbeatMalformed:

    def test_malformed_heartbeat_returns_none(self, monkeypatch, tmp_path):
        """A heartbeat file with garbage JSON returns None and logs
        a warning — does not propagate the JSONDecodeError."""
        bad = tmp_path / "heartbeat.json"
        bad.write_text("{not valid: json")
        monkeypatch.setattr(crash_report, "_heartbeat_path", lambda: bad)
        assert _read_last_heartbeat() is None

    def test_empty_heartbeat_returns_none(self, monkeypatch, tmp_path):
        """An empty heartbeat file (zero bytes) returns None, doesn't
        raise json.JSONDecodeError."""
        empty = tmp_path / "heartbeat.json"
        empty.write_text("")
        monkeypatch.setattr(crash_report, "_heartbeat_path", lambda: empty)
        assert _read_last_heartbeat() is None

    def test_whitespace_only_heartbeat_returns_none(self, monkeypatch, tmp_path):
        """A whitespace-only heartbeat file returns None."""
        ws = tmp_path / "heartbeat.json"
        ws.write_text("   \n\t  ")
        monkeypatch.setattr(crash_report, "_heartbeat_path", lambda: ws)
        assert _read_last_heartbeat() is None


# ── Class 3: Heartbeat UNEXPECTED SHAPE ──────────────────────────────


class TestHeartbeatUnexpectedShape:

    def test_heartbeat_is_list_returns_unexpected_marker(
        self, monkeypatch, tmp_path,
    ):
        """If someone wrote a JSON list as the heartbeat, surface the
        shape mismatch but don't crash."""
        listy = tmp_path / "heartbeat.json"
        listy.write_text(json.dumps(["a", "b", "c"]))
        monkeypatch.setattr(crash_report, "_heartbeat_path", lambda: listy)
        result = _read_last_heartbeat()
        assert isinstance(result, dict)
        assert result.get("unexpected_shape") == "list"

    def test_heartbeat_is_string_returns_unexpected_marker(
        self, monkeypatch, tmp_path,
    ):
        """JSON string at root → also unexpected shape."""
        stringy = tmp_path / "heartbeat.json"
        stringy.write_text(json.dumps("just a string"))
        monkeypatch.setattr(crash_report, "_heartbeat_path", lambda: stringy)
        result = _read_last_heartbeat()
        assert isinstance(result, dict)
        assert result.get("unexpected_shape") == "str"


# ── Class 4: Crash report DIR DOESN'T EXIST ──────────────────────────


class TestCrashDirCreation:

    def test_dir_created_lazily(self, monkeypatch, tmp_path, tmp_crash_dir):
        """The crash dir is created on first write. Tests the "first
        session ever" case."""
        monkeypatch.setattr(
            crash_report, "_heartbeat_path",
            lambda: tmp_path / "no-such-heartbeat.json",
        )
        assert not tmp_crash_dir.exists()  # precondition

        exc = _raise_and_capture(RuntimeError("first session"))
        out = write_crash_report(exc, crash_dir=tmp_crash_dir)
        assert out is not None
        assert tmp_crash_dir.exists()
        assert tmp_crash_dir.is_dir()
        assert out.parent == tmp_crash_dir

    def test_nested_dir_created(self, monkeypatch, tmp_path):
        """Even multi-level missing dirs (data/crash_reports/) are
        created via mkdir(parents=True)."""
        monkeypatch.setattr(
            crash_report, "_heartbeat_path",
            lambda: tmp_path / "no-hb.json",
        )
        deep = tmp_path / "level1" / "level2" / "crash_reports"
        exc = _raise_and_capture(KeyError("k"))
        out = write_crash_report(exc, crash_dir=deep)
        assert out is not None and deep.exists()


# ── Class 5: Exception with NON-STRING str() ─────────────────────────


class _ExplodingException(Exception):
    """An exception whose __str__ raises. Pathological but real:
    seen in custom Exception classes that try to format() with a
    missing field. Don't let this prevent crash-report writing."""

    def __str__(self) -> str:
        raise RuntimeError("my __str__ method explodes")


class TestExceptionWithBadStr:

    def test_bad_str_does_not_crash_report_writer(
        self, monkeypatch, tmp_path, tmp_crash_dir,
    ):
        """An exception whose __str__ raises still gets a crash report
        written. The exception_message field surfaces the failure."""
        monkeypatch.setattr(
            crash_report, "_heartbeat_path",
            lambda: tmp_path / "no-hb.json",
        )
        exc = _raise_and_capture(_ExplodingException())
        out = write_crash_report(exc, crash_dir=tmp_crash_dir)
        assert out is not None
        payload = json.loads(out.read_text())
        assert payload["exception_type"] == "_ExplodingException"
        # _safe_repr falls through to repr() which always works for
        # standard exceptions; we just need to confirm SOMETHING was
        # captured and we didn't crash.
        assert isinstance(payload["exception_message"], str)
        assert len(payload["exception_message"]) > 0


# ── Rule (e) positive-case test ──────────────────────────────────────


class TestPositiveCasePopulated:
    """Rule (e) of the hardened bug-sweep template:
        'Every fail-safe catch block ships with a test that asserts
         the output is non-empty under normal conditions.'

    write_crash_report has multiple fail-safe paths that return None
    or a sparse dict. Without this test class, those paths could
    silently dominate production and we'd never know."""

    def test_full_payload_under_normal_conditions(
        self, monkeypatch, tmp_path, tmp_crash_dir,
    ):
        """A real heartbeat + a real exception → a complete crash
        report with every expected field populated and the traceback
        actually containing stack frame lines."""
        # Set up a realistic heartbeat
        hb_path = tmp_path / "heartbeat.json"
        hb_path.write_text(json.dumps({
            "timestamp": "2026-04-20T13:40:00+00:00",
            "phase": "EVALUATION",
            "last_function": "evaluate_candidate",
            "pulse_count": 1234,
            "positions": 3,
            "trades_today": 5,
            "pid": 9999,
        }))
        monkeypatch.setattr(crash_report, "_heartbeat_path", lambda: hb_path)

        # Raise + catch a realistic exception with a real traceback
        try:
            def _level_3():
                raise ValueError("simulated mid-trade crash")
            def _level_2():
                _level_3()
            def _level_1():
                _level_2()
            _level_1()
        except ValueError as e:
            exc = e

        out = write_crash_report(
            exc,
            context={"command": "paper", "mode": "paper"},
            crash_dir=tmp_crash_dir,
        )

        # File MUST exist
        assert out is not None, "write_crash_report returned None for happy path"
        assert out.exists(), f"crash report file not on disk: {out}"

        # File MUST be valid JSON
        payload = json.loads(out.read_text())

        # Every expected key MUST be present and non-empty
        required_keys = {
            "schema_version", "captured_at", "pid", "exit_code_planned",
            "exception_type", "exception_message",
            "traceback", "traceback_lines",
            "last_heartbeat", "argv", "python_version", "cwd",
            "context",
        }
        missing = required_keys - set(payload.keys())
        assert not missing, f"crash report missing keys: {missing}"

        # Specific field checks
        assert payload["exception_type"] == "ValueError"
        assert "simulated mid-trade crash" in payload["exception_message"]
        assert payload["traceback_lines"] >= 3, (
            f"traceback should include the 3-level call stack; got "
            f"{payload['traceback_lines']} lines"
        )
        assert "_level_3" in payload["traceback"], (
            "traceback must include the deepest frame name"
        )
        assert payload["last_heartbeat"]["last_function"] == "evaluate_candidate"
        assert payload["context"]["command"] == "paper"
        assert payload["exit_code_planned"] == EXIT_CODE_UNHANDLED


# ── Cancelled-error distinction ──────────────────────────────────────


class TestCancelledErrorDistinction:
    """asyncio.CancelledError must produce a different planned exit
    code than ValueError/RuntimeError, so the watchdog can distinguish
    'I died' from 'I was killed by the loop'."""

    def test_cancelled_error_marked_91(
        self, monkeypatch, tmp_path, tmp_crash_dir,
    ):
        import asyncio
        monkeypatch.setattr(
            crash_report, "_heartbeat_path",
            lambda: tmp_path / "no-hb.json",
        )
        exc = _raise_and_capture(asyncio.CancelledError())
        out = write_crash_report(exc, crash_dir=tmp_crash_dir)
        assert out is not None
        payload = json.loads(out.read_text())
        assert payload["exit_code_planned"] == EXIT_CODE_CANCELLED
        assert payload["exception_type"] == "CancelledError"

    def test_regular_exception_marked_90(
        self, monkeypatch, tmp_path, tmp_crash_dir,
    ):
        monkeypatch.setattr(
            crash_report, "_heartbeat_path",
            lambda: tmp_path / "no-hb.json",
        )
        exc = _raise_and_capture(RuntimeError("just an error"))
        out = write_crash_report(exc, crash_dir=tmp_crash_dir)
        assert out is not None
        payload = json.loads(out.read_text())
        assert payload["exit_code_planned"] == EXIT_CODE_UNHANDLED

    def test_is_cancelled_helper(self):
        import asyncio
        assert _is_cancelled_error(asyncio.CancelledError()) is True
        assert _is_cancelled_error(ValueError("x")) is False
        assert _is_cancelled_error(RuntimeError()) is False


# ── Helpers ──────────────────────────────────────────────────────────


class TestSafeRepr:

    def test_normal_string(self):
        assert _safe_repr("hello") == "hello"

    def test_int(self):
        assert _safe_repr(42) == "42"

    def test_object_with_normal_str(self):
        assert _safe_repr([1, 2]) == "[1, 2]"

    def test_object_whose_str_raises(self):
        class Bad:
            def __str__(self): raise RuntimeError("nope")
        # Must fall back to repr, never raise
        result = _safe_repr(Bad())
        assert isinstance(result, str)
        assert len(result) > 0


class TestCoerceJsonable:

    def test_dict_passthrough(self):
        assert _coerce_jsonable({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    def test_path_coerced_to_str(self):
        from pathlib import Path
        result = _coerce_jsonable({"p": Path("/tmp/x")})
        assert isinstance(result["p"], str)

    def test_nested(self):
        from pathlib import Path
        result = _coerce_jsonable({"a": [Path("x"), 1, {"b": Path("y")}]})
        assert isinstance(result["a"][0], str)
        assert result["a"][1] == 1
        assert isinstance(result["a"][2]["b"], str)

    def test_dict_keys_coerced_to_str(self):
        # JSON requires string keys; numeric keys coerce to str
        result = _coerce_jsonable({1: "one", 2: "two"})
        assert "1" in result and "2" in result

    def test_none_passthrough(self):
        assert _coerce_jsonable(None) is None
