"""
Tue 2026-04-21 Fix 3: Task Scheduler auto-restart-on-failure policy
contract tests.

Background. Today's session died at 10:18 ET when the watchdog
SIGKILL'd Python for stale heartbeat (Fix 2 territory). Task Scheduler
DID NOT auto-restart -- the system stayed dead from 10:18 to 16:00
(5h 42m). Inspection of the live task XML found `<RestartOnFailure/>`
EMPTY on first read, then populated as `<Count>3</Count>` after some
indirect Get-ScheduledTask interaction. Either way: the policy did
not enforce a restart, and the gap is not fully closed without
elevation.

Two scripts ship as part of Fix 3:

  scripts/configure_paper_task_restart_policy.ps1
    Applies RestartCount=6, RestartInterval=PT5M to the live task.
    REQUIRES admin elevation (`Set-ScheduledTask`). Idempotent.

  scripts/verify_paper_task_settings.ps1
    Reads the live task XML via `Export-ScheduledTask` and asserts the
    policy is populated as expected. Returns 0 on PASS, 1 on FAIL.
    Suitable for CI / scheduled health probe. Does NOT require admin.

Python tests below are CONTRACT tests on the scripts -- they don't
talk to Task Scheduler (which is OS state, not Python state). They
assert the scripts exist with the right shape so a future refactor
can't accidentally drop or weaken them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURE = REPO_ROOT / "scripts" / "configure_paper_task_restart_policy.ps1"
VERIFY    = REPO_ROOT / "scripts" / "verify_paper_task_settings.ps1"


class TestScriptsExist:

    def test_configure_script_exists(self):
        assert CONFIGURE.exists(), (
            f"Missing apply script: {CONFIGURE}. "
            "Restart policy cannot be set programmatically without it."
        )

    def test_verify_script_exists(self):
        assert VERIFY.exists(), (
            f"Missing verify script: {VERIFY}. "
            "Without it operators have no way to confirm policy drift."
        )


class TestConfigureScriptContract:
    """The configure script must apply Count=6 and Interval=PT5M
    by default per the user spec. Parameter overrides allowed for
    operator flexibility."""

    def test_default_count_is_6(self):
        text = CONFIGURE.read_text(encoding="utf-8")
        assert re.search(r"\$RestartCount\s*=\s*6\b", text), (
            "configure_paper_task_restart_policy.ps1 must default "
            "$RestartCount = 6 (today's spec). Today's session ran "
            "with Count=3 default-reported but the policy didn't even "
            "fire -- 6 retries × 5min covers a 30-min window vs "
            "today's 5h 42m dark window."
        )

    def test_default_interval_is_5min(self):
        text = CONFIGURE.read_text(encoding="utf-8")
        assert re.search(
            r"\$RestartIntervalMinutes\s*=\s*5\b", text,
        ), "configure script must default to 5-minute restart interval"

    def test_uses_set_scheduled_task(self):
        """The script must actually call Set-ScheduledTask, not just
        log the intent. Catches a future PR that 'simplifies' the
        script into a no-op."""
        text = CONFIGURE.read_text(encoding="utf-8")
        assert "Set-ScheduledTask" in text, (
            "configure script must invoke Set-ScheduledTask"
        )

    def test_post_apply_verification_present(self):
        """After applying, the script must verify the change
        actually persisted (the original bug class)."""
        text = CONFIGURE.read_text(encoding="utf-8")
        assert "Export-ScheduledTask" in text, (
            "configure script must verify via Export-ScheduledTask "
            "after applying — Set-ScheduledTask reporting success "
            "doesn't guarantee XML persistence (today's bug)"
        )
        assert "PASS" in text and "FAIL" in text, (
            "verification block must report PASS or FAIL clearly"
        )


class TestVerifyScriptContract:
    """The verify script must FAIL loudly if the live policy doesn't
    match the spec. Tolerance for the as-current Count=3 must be
    explicit, not silent."""

    def test_default_expected_count_is_6(self):
        text = VERIFY.read_text(encoding="utf-8")
        assert re.search(
            r"\$ExpectedRestartCount\s*=\s*6\b", text,
        ), (
            "verify script must default to $ExpectedRestartCount=6 -- "
            "matching the spec. Operators can override for downgrade "
            "investigation, but the default is the spec."
        )

    def test_xml_is_source_of_truth(self):
        """Get-ScheduledTask reports defaults that may not match
        actual XML (today's bug). Verify must use Export-ScheduledTask."""
        text = VERIFY.read_text(encoding="utf-8")
        assert "Export-ScheduledTask" in text, (
            "verify must read XML via Export-ScheduledTask, NOT "
            "Get-ScheduledTask (which today reported phantom defaults)"
        )

    def test_checks_for_empty_restart_block(self):
        """Specifically catches today's bug: <RestartOnFailure/>
        empty self-closing element."""
        text = VERIFY.read_text(encoding="utf-8")
        assert re.search(
            r"<RestartOnFailure\\?\s*/>", text,
        ), (
            "verify script must specifically detect the empty "
            "<RestartOnFailure/> case that today's investigation found"
        )

    def test_checks_count_and_interval(self):
        text = VERIFY.read_text(encoding="utf-8")
        assert "<Count>" in text and "<Interval>" in text, (
            "verify must check both Count and Interval values"
        )

    def test_returns_proper_exit_codes(self):
        text = VERIFY.read_text(encoding="utf-8")
        assert "exit 0" in text and "exit 1" in text, (
            "verify must return 0 on PASS and 1 on FAIL for CI use"
        )


class TestScriptsAreLinked:
    """If verify FAILs, it must point operators at the configure
    remediation. Closes the loop."""

    def test_verify_links_to_configure_on_fail(self):
        text = VERIFY.read_text(encoding="utf-8")
        assert "configure_paper_task_restart_policy" in text, (
            "verify script must reference the configure script in "
            "its FAIL message — operators shouldn't have to guess "
            "the remediation"
        )
