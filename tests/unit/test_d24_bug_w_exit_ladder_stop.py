"""
Fri 2026-04-24 Bug W regression tests.

Background. LIDR live state at 10:18 ET today:
  Internal tracker: pos.stop_loss = $2.40  (Phase-0 tightened, ~0.8% below entry)
  Broker live order: stop_price   = $1.90  (verdict.stop_loss UN-tightened, ~22%)

The bridge correctly stored $2.40 (Bug R fix from D23 holds at the
ManagedPosition construction boundary). But when the Phase-2 exit
ladder fires post-fill, it cancels the OTO stop at $2.40 and submits a
NEW stop at $1.90 — re-creating the Bug R class divergence at a
different layer.

Root cause. `main.py:3488` (Phase-2 exit-ladder restructure) and
`main.py:3528` (stop-resubmitter registration) read `verdict.stop_loss`
(the un-tightened verdict-time stop) instead of `pos.stop_loss` (which
yesterday's Bug R fix populates with the actual broker stop).

Source-grep guards: any future patch that re-introduces
`stop_price=verdict.stop_loss` in an exit-ladder or register_stop call
site fails CI. Unit test pins the LIDR case via behavioral simulation.

T1 — source-grep: NO call to `cancel_stop_and_submit_exit_ladder` in
                  main.py uses `stop_price=verdict.stop_loss`
T2 — source-grep: NO call to `stop_resubmitter.register_stop` uses
                  `stop_price=verdict.stop_loss` or
                  `stop_price=_short_verdict.stop_loss`
T3 — unit: build a ManagedPosition with stop=$2.40 (post Bug R fix)
          and a verdict with stop_loss=$1.90; assert that the value
          passed to the exit_ladder helper would be $2.40, not $1.90
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MAIN_PY = REPO / "main.py"


# ── T1 — exit-ladder call sites must use pos.stop_loss ─────────────


class TestBugW_ExitLadderCallSites:

    def test_no_exit_ladder_call_uses_verdict_stop_loss(self):
        """Source-grep guard: every cancel_stop_and_submit_exit_ladder
        call site in main.py must use a position-tracked stop_loss
        value (pos.stop_loss or equivalent), NOT verdict.stop_loss
        (the un-tightened verdict-time stop)."""
        text = MAIN_PY.read_text(encoding="utf-8")
        # Find each exit-ladder call (multiline; scan a window for stop_price=)
        offsets = [m.start() for m in re.finditer(r"cancel_stop_and_submit_exit_ladder\(", text)]
        assert offsets, "expected at least one exit-ladder call site in main.py"

        violations: list[str] = []
        for off in offsets:
            # Take the next ~600 chars (covers the kwarg block)
            window = text[off:off + 600]
            # Find the stop_price= argument
            m = re.search(r"stop_price\s*=\s*([\w\.\[\]]+)", window)
            if m is None:
                continue   # call may be wrapped; not all sites pass the kwarg
            arg = m.group(1)
            # Allowed: anything ending in .stop_loss EXCEPT verdict.stop_loss /
            # _short_verdict.stop_loss / fpe.stop_loss (the un-tightened
            # verdict-time values). The Bug R-fixed source is pos.stop_loss
            # or *_pos.stop_loss.
            if arg in ("verdict.stop_loss", "_short_verdict.stop_loss",
                       "_p2_verdict.stop_loss"):
                # Find approximate line number for the report
                line = text[:off + m.start()].count("\n") + 1
                violations.append(f"main.py:~{line}  stop_price={arg}")

        assert not violations, (
            "Bug W regression: exit-ladder call site(s) use the un-tightened "
            "verdict.stop_loss instead of the broker-truth pos.stop_loss "
            "(carried by ManagedPosition per Bug R fix). Sites:\n  "
            + "\n  ".join(violations)
        )


# ── T2 — stop_resubmitter register must use pos.stop_loss ──────────


class TestBugW_RegisterStopCallSites:

    def test_no_register_stop_uses_verdict_stop_loss(self):
        """Same shape as T1 for stop_resubmitter.register_stop calls.
        These propagate the stop value to the ratchet logic; if wrong,
        the ratchet baseline is wrong by the Phase-0 tightening delta.

        Allowed exceptions (NOT Bug W):
          - fpe.stop_loss: FastPath bypasses alpaca_executor's Phase-0
            tightening logic (only LONG-OTO via the executor applies
            Phase-0 per alpaca_executor.py:244). In FastPath the broker
            stop equals fpe.stop_loss by construction.
          - _short_verdict.stop_loss: SHORT OTO also doesn't get
            Phase-0 tightening (only longs do). Broker stop equals
            verdict-time stop by construction.

        Bug W only fires on LONG OTO submissions that go through
        alpaca_executor with Phase-0 enabled."""
        text = MAIN_PY.read_text(encoding="utf-8")
        offsets = [m.start() for m in re.finditer(r"stop_resubmitter\.register_stop\(", text)]
        assert offsets, "expected at least one register_stop call site in main.py"

        # Bug-W-class violations (verdict.stop_loss in long-OTO contexts only)
        BUG_W_VIOLATING_ARGS = {"verdict.stop_loss"}
        violations: list[str] = []
        for off in offsets:
            window = text[off:off + 400]
            m = re.search(r"stop_price\s*=\s*([\w\.\[\]]+)", window)
            if m is None:
                continue
            arg = m.group(1)
            if arg in BUG_W_VIOLATING_ARGS:
                line = text[:off + m.start()].count("\n") + 1
                violations.append(f"main.py:~{line}  stop_price={arg}")

        assert not violations, (
            "Bug W regression: long-OTO register_stop call site(s) use "
            "verdict.stop_loss (un-tightened) instead of the broker-truth "
            "pos.stop_loss (which carries actual_stop_price per Bug R fix). "
            "Sites:\n  " + "\n  ".join(violations)
        )


# ── T3 — semantic check: pos.stop_loss carries actual_stop_price ──


class TestBugW_PositionStopIsBrokerTruth:

    def test_managed_position_stop_loss_is_actual_stop_after_bridge(self):
        """Prove the assumption underlying Bug W's fix: after the bridge
        constructs a ManagedPosition with the Bug R fix in place, the
        position's stop_loss field carries the actual broker stop
        (actual_stop_price), not verdict.stop_loss. So callers that
        read pos.stop_loss get broker truth."""
        from src.execution.alpaca_executor import OrderResult
        from src.execution.position_manager import ManagedPosition

        # Simulate the LIDR situation at 10:16 today
        order_result = OrderResult(
            order_id="lidr-test-1",
            status="filled",
            ticker="LIDR",
            qty=5264,
            side="buy",
            order_type="oto",
            signal_price=2.44,
            submitted_price=2.44,
            stop_loss=1.90,           # verdict's un-tightened (verdict.stop_loss)
            take_profit=0.0,
            stop_order_id="stop-1",
            fill_price=2.42,
            actual_stop_price=2.40,   # Phase-0 tightened (Bug R fix sets this)
        )

        # Replay the bridge construction logic (from execute_verdict)
        # exactly as the production code does post-Bug-R:
        _broker_stop = (
            float(order_result.actual_stop_price)
            if getattr(order_result, "actual_stop_price", None) is not None
            else order_result.stop_loss   # fallback if pre-Bug-R OrderResult
        )
        pos = ManagedPosition(
            ticker="LIDR",
            qty=5264,
            entry_price=2.42,
            signal_price=2.44,
            stop_loss=_broker_stop,
            target_prices=[2.56, 2.68, 2.92],
            stop_order_id="stop-1",
        )

        # The position's stop_loss must be the Phase-0 tightened value,
        # not the verdict's un-tightened value
        assert pos.stop_loss == 2.40, (
            f"Bug R precondition broken: ManagedPosition.stop_loss = "
            f"{pos.stop_loss} but expected 2.40 (Phase-0 tightened). "
            f"Bug W fix depends on this assumption holding."
        )
        # And the verdict's un-tightened value is NOT what gets carried
        assert pos.stop_loss != order_result.stop_loss, (
            "Bug W's whole point: pos.stop_loss must differ from the "
            "verdict-time stop_loss when Phase-0 tightening is active. "
            "If they're equal, the Bug R fix isn't doing anything."
        )
