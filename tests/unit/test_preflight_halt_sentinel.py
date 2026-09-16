"""Tests for the D277 HALT switch sentinel in preflight_check.py.

Added 2026-05-12 after the bot ran 2 days with HALT switch silently on,
blocking 8+ valid entries that the operator only discovered post-EOD.
The sentinel surfaces this state in preflight (fail-loud) so operators
see "BOT WILL REFUSE TRADES" before market open instead of after.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def test_halt_sentinel_passes_when_unset(monkeypatch):
    """No HALT env var + no config flag -> PASS."""
    monkeypatch.delenv("MOMENTUM_HALT_NEW_ENTRIES", raising=False)
    from preflight_check import check_halt_switch
    passed, msg = check_halt_switch()
    assert passed is True, f"Expected PASS but got FAIL: {msg}"
    assert "OFF" in msg or "off" in msg.lower()


def test_halt_sentinel_fails_when_env_set_to_one(monkeypatch):
    """MOMENTUM_HALT_NEW_ENTRIES=1 -> FAIL with clear remediation."""
    monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", "1")
    from preflight_check import check_halt_switch
    passed, msg = check_halt_switch()
    assert passed is False, "Expected FAIL when MOMENTUM_HALT_NEW_ENTRIES=1"
    assert "HALT SWITCH IS ON" in msg
    assert "MOMENTUM_HALT_NEW_ENTRIES" in msg  # remediation includes the var name
    assert "lift" in msg.lower() or "unset" in msg.lower() or "SetEnvironmentVariable" in msg


def test_halt_sentinel_fails_on_truthy_variants(monkeypatch):
    """Any of 1/true/yes/on (case-insensitive) -> FAIL."""
    from preflight_check import check_halt_switch
    for value in ["1", "true", "TRUE", "yes", "YES", "on", "ON"]:
        monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", value)
        passed, msg = check_halt_switch()
        assert passed is False, (
            f"Expected FAIL for value '{value}' but got PASS: {msg}"
        )


def test_halt_sentinel_passes_on_falsy_values(monkeypatch):
    """Empty string / 0 / false / no / off -> PASS (treated as not set)."""
    from preflight_check import check_halt_switch
    for value in ["", "0", "false", "FALSE", "no", "NO", "off", "OFF"]:
        monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", value)
        passed, msg = check_halt_switch()
        assert passed is True, (
            f"Expected PASS for value '{value}' but got FAIL: {msg}"
        )


def test_halt_sentinel_in_main_check_list():
    """Confirm 'Halt Switch' appears in the main() check list -- guards
    against future refactors silently dropping the sentinel."""
    import preflight_check
    src = (REPO / "scripts" / "preflight_check.py").read_text()
    # Both the function and the registration in checks=[] must exist
    assert "def check_halt_switch" in src
    assert '"Halt Switch"' in src or "'Halt Switch'" in src
