"""doc 229 regression: the two CRITICAL phantom-P&L close guards must not be silently removed.

The two guards live inline in main.py's EOD loops and book P&L (close_with_attribution) ONLY when the
broker close is CONFIRMED:
  - CRITICAL #1 shutdown close (main.py ~7800): market-open gate + attempt_close_with_status_check +
    book only on confirmed close (else carry intact, stops untouched -- no naked overnight).
  - CRITICAL #2 Phase-4 close (main.py ~7150): `if not broker_closed: continue` before booking.

Both gate on the bridge primitive `attempt_close_with_status_check(...).succeeded`. This file locks:
  (A) BEHAVIORAL: the primitive correctly reports succeeded=False on a failed broker close (a 403) and
      True on a clean close -- the load-bearing dependency of both guards (via the doc-218 Adversary).
  (B) TRIPWIRE: the inline guards still exist in main.py (these inline monolith paths cannot be unit-
      called; the tripwire fails loudly if a refactor strips the guard -- the exact doc-229 regression).
"""
from __future__ import annotations

import asyncio
import pathlib

from src.execution.bridge import attempt_close_with_status_check
from src.ops.adversary import AdversaryBroker

_MAIN = pathlib.Path(__file__).resolve().parents[2] / "main.py"
MAIN_SRC = _MAIN.read_text(encoding="utf-8", errors="ignore")


# ---------- (A) behavioral: the primitive the guards depend on ----------
def test_close_primitive_reports_failure_on_403():
    """A failed broker close (403) MUST return succeeded=False -> main.py guards then skip booking."""
    broker = AdversaryBroker(close_hard_fail=True)
    broker.seed_position("X", 1000, entry=3.46, current=3.00)
    r = asyncio.run(attempt_close_with_status_check(
        client=broker, ticker="X", qty=1000, max_retries=2, cancel_blocking_stops_first=True))
    assert r.get("succeeded") is False, r


def test_close_primitive_reports_success_on_clean_close():
    broker = AdversaryBroker()
    broker.seed_position("X", 1000, entry=3.46, current=3.00)
    r = asyncio.run(attempt_close_with_status_check(
        client=broker, ticker="X", qty=1000, max_retries=2))
    assert r.get("succeeded") is True, r


# ---------- (B) tripwires: the inline guards must remain in main.py ----------
def test_phase4_phantom_guard_present():
    """doc-229 CRITICAL #2: Phase-4 must NOT book P&L unless broker_closed (mirror of the D76 guard)."""
    assert "if not broker_closed:" in MAIN_SRC, \
        "Phase-4 phantom guard removed: booking is no longer gated on a confirmed broker close (doc 229 #2)"


def test_shutdown_phantom_guard_present():
    """doc-229 CRITICAL #1: shutdown close must gate on market-open + a confirmed close."""
    assert "_shutdown_market_open" in MAIN_SRC, \
        "Shutdown market-open gate removed -- cancelling stops while closed would leave a naked carry (doc 229 #1)"
    assert "if not _sc_ok:" in MAIN_SRC, \
        "Shutdown confirmed-close gate removed: P&L may be booked without a broker close (doc 229 #1)"
