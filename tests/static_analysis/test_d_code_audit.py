"""D-code registry audit gate.

Per `26_d_code_registry.md` discipline: every reserved D-code must have
at least one reference outside the registry itself (production source,
test, or other doc). Codes that exist only in the registry are ORPHANS
— either reserved-but-never-wired (silent debt) or vestigial (forgotten
during a refactor).

This test runs `scripts/audit_d_codes.py` programmatically and fails if
any orphan code is detected. Future contributors who reserve a code in
the registry but never wire it surface immediately.

Allowlist (KNOWN_NON_CODES in the script):
  - convention boundary refs (D000, D199, D200, D299)
  - date references (D23, D24, D25 — referring to "the 24th day of trading")

The "Next available code: DXXX" sentinel is auto-detected and excluded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Import the audit functions from the script
from audit_d_codes import audit_d_codes  # noqa: E402


class TestDCodeAudit:

    def test_no_orphan_codes_in_registry(self) -> None:
        """Every D-code in 26_d_code_registry.md must have at least one
        reference outside the registry itself."""
        results = audit_d_codes()
        orphans = [r.code for r in results if r.is_orphan]
        if orphans:
            details = "\n".join(
                f"  {r.code} (cat={r.category}, prod={r.in_production}, "
                f"tests={r.in_tests}, docs={r.in_docs_outside_registry})"
                for r in results if r.is_orphan
            )
            pytest.fail(
                f"D-code registry audit found {len(orphans)} ORPHAN code(s):\n"
                f"{details}\n\n"
                f"Each must be either:\n"
                f"  (a) WIRED — add a production-code reference (the code "
                f"actually emitted by the system), OR\n"
                f"  (b) REMOVED — delete from 26_d_code_registry.md (was "
                f"reserved but never used), OR\n"
                f"  (c) ALLOWLISTED — add to KNOWN_NON_CODES in "
                f"scripts/audit_d_codes.py (only if it's a legitimate "
                f"non-code reference)."
            )

    def test_audit_returns_at_least_one_live_code(self) -> None:
        """Sanity: the audit must find SOME live codes; otherwise the
        registry parsing or production grep is broken."""
        results = audit_d_codes()
        live_codes = [r for r in results if r.category == "LIVE"]
        assert len(live_codes) > 10, (
            f"Audit found only {len(live_codes)} LIVE codes — registry "
            f"parser may be broken (expected 30+ for current state)"
        )

    def test_d224_kelly_halved_is_live(self) -> None:
        """Spot-check: D224 KELLY_HALVED was the most recent critical
        wire-in (commit 9b80944). Must surface as LIVE in the audit."""
        results = audit_d_codes()
        d224 = next((r for r in results if r.code == "D224"), None)
        assert d224 is not None, "D224 not found in registry"
        assert d224.category == "LIVE", (
            f"D224 KELLY_HALVED expected LIVE, got {d224.category} "
            f"(prod={d224.in_production}, tests={d224.in_tests})"
        )
