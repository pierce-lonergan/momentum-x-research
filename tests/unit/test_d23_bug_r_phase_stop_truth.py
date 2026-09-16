"""
Thu 2026-04-23 EOD Bug R regression tests.

Background. XNDU trade today logged THREE different stop values in
three adjacent log lines:

    Submitting OTO order — qty=395, entry=32.93, stop=32.44      ← _stop_price (Phase-0 tightened)
    OTO stop leg — oid=...c @ $25.72                              ← verdict.stop_loss (UN-tightened)
    Position opened — qty=395, entry=$30.74, stop=$25.72          ← verdict.stop_loss

The broker actually has the stop at $32.44 (tightened, 1.5% below
entry). The internal tracker thinks it's at $25.72 (un-tightened, 22%
below entry). Risk math is wrong by ~14×.

Two distinct bugs that compound:
  R1 — alpaca_executor.py:355 logs verdict.stop_loss instead of _stop_price
  R2 — bridge.py builds ManagedPosition with verdict.stop_loss instead of
       the actual broker stop

Fix:
  1. OrderResult gains an actual_stop_price field set to _stop_price
  2. alpaca_executor logs and stores actual_stop_price
  3. bridge.execute_verdict reads order_result.actual_stop_price
     when constructing ManagedPosition
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ── R1: log line uses _stop_price not verdict.stop_loss ────────────


class TestBugR_LogLineSourceStopPrice:

    def test_oto_stop_leg_log_uses_phase_tightened_stop(self):
        """Source-grep guard: the OTO stop leg log line must reference
        _stop_price (the Phase-0/1 tightened value actually submitted),
        NOT verdict.stop_loss (the un-tightened original)."""
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        text = (repo / "src" / "execution" / "alpaca_executor.py").read_text(
            encoding="utf-8",
        )
        # Find the "OTO stop leg" marker, then take the surrounding
        # ~400 chars (covers the full logger.info call + args).
        idx = text.find("OTO stop leg")
        assert idx != -1, "OTO stop leg log not found"
        log_block = text[max(0, idx - 50):idx + 400]
        # Must reference _stop_price, NOT verdict.stop_loss
        assert "_stop_price" in log_block, (
            f"Bug R regression: OTO stop leg log must use _stop_price "
            f"(the actual submitted stop), not verdict.stop_loss. "
            f"Got: {log_block}"
        )
        assert "verdict.stop_loss" not in log_block, (
            f"Bug R regression: OTO stop leg log still references "
            f"verdict.stop_loss. This was the today's bug. Got: {log_block}"
        )


# ── R2: OrderResult and ManagedPosition carry actual broker stop ───


class TestBugR_OrderResultCarriesActualStop:

    def test_order_result_has_actual_stop_price_field(self):
        """OrderResult must expose actual_stop_price so the bridge can
        construct ManagedPosition with the broker-truth stop."""
        from src.execution.alpaca_executor import OrderResult

        result = OrderResult(
            order_id="test-1",
            status="filled",
            ticker="X",
            qty=100,
            side="buy",
            order_type="oto",
            signal_price=10.0,
            submitted_price=10.0,
            stop_loss=8.0,
            take_profit=0.0,
            stop_order_id="stop-1",
            fill_price=10.0,
            actual_stop_price=9.85,  # Phase-0 tightened to 1.5% below entry
        )
        assert result.actual_stop_price == 9.85

    def test_order_result_actual_stop_price_defaults_to_none(self):
        """Backward compat: existing call sites that don't set the field
        get None (signals 'no override, use verdict.stop_loss')."""
        from src.execution.alpaca_executor import OrderResult

        result = OrderResult(
            order_id="test-1",
            status="filled",
            ticker="X",
            qty=100,
            side="buy",
            order_type="oto",
            signal_price=10.0,
            submitted_price=10.0,
            stop_loss=8.0,
            take_profit=0.0,
            stop_order_id="stop-1",
            fill_price=10.0,
        )
        assert result.actual_stop_price is None

    def test_bridge_managed_position_uses_actual_stop_when_present(self):
        """When OrderResult.actual_stop_price is set, ManagedPosition
        must carry that value (broker truth) — not verdict.stop_loss.

        The patch may compute the broker-stop value either inline in
        the constructor kwargs, OR in a pre-computed local that the
        constructor references. Test accepts both shapes by verifying:
        (a) actual_stop_price is referenced in execute_verdict, AND
        (b) the ManagedPosition construction does NOT use the bare
            `stop_loss=verdict.stop_loss` pattern."""
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        text = (repo / "src" / "execution" / "bridge.py").read_text(
            encoding="utf-8",
        )
        # (a) actual_stop_price must be referenced somewhere in bridge.py
        assert "actual_stop_price" in text, (
            "Bug R regression: bridge.py must reference "
            "order_result.actual_stop_price to read the broker-truth "
            "stop value. The reference is missing."
        )
        # (b) The ManagedPosition construction block must NOT use
        #     `stop_loss=verdict.stop_loss` directly.
        m = re.search(
            r"position = ManagedPosition\((.*?)\n        \)",
            text, re.DOTALL,
        )
        assert m is not None, "ManagedPosition construction site not found"
        block = m.group(1)
        assert "stop_loss=verdict.stop_loss" not in block, (
            "Bug R regression: bridge.execute_verdict still constructs "
            "ManagedPosition with stop_loss=verdict.stop_loss directly. "
            "Must use the broker-truth stop (order_result.actual_stop_price "
            "when present, falling back to verdict.stop_loss). "
            f"Block excerpt: {block[:500]}..."
        )
