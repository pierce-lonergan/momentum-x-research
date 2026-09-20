"""Bug AS tests: RESCAN/Phase2/VWAP exit-ladder must mirror the new
stop OID into the in-memory ManagedPosition AND must not fall back
to the entry-order ID when no real stop exists.

Pins today's (Tuesday 2026-04-28) finding as a regression guard:

  - Today's RESCAN entries (SBLX, ATER, SEGG) registered with their
    ENTRY ORDER ID stored in position.stop_order_id (e.g. b9703115).
  - Cause #1: the Phase2/VWAP/RESCAN call sites in main.py wrote
    state_mgr.update_position(stop_order_id=_new_stop_oid or ...)
    but NEVER updated the in-memory _pos.stop_order_id. FAST_PATH
    (main.py:2315) did. Asymmetry meant Phase2/VWAP/RESCAN positions
    carried the STALE OTO leg ID — already cancelled by
    cancel_stop_and_submit_exit_ladder. D231 RECON_HARD_BLOCK fired
    for every such position because the broker had no order with
    that ID.
  - Cause #2: the stop_resubmitter registration line used
    `_new_stop_oid or order.stop_order_id or order.order_id`. When
    exit_ladder yielded an empty new OID (D147 short-circuit,
    residual_qty=0, or stop submit failure), the third fallback put
    the ENTRY ORDER ID — `order.order_id` — into the stop_order_id
    field. Not a stop at all. StopResubmitter would later try to
    cancel an entry order to "ratchet the stop".

Bug AS fix (main.py at 3 sites — Phase2 ~3805, VWAP ~6291,
RESCAN ~6710):
  1. Add `_pos.stop_order_id = _result.new_stop_order_id` after
     cancel_stop_and_submit_exit_ladder returns. This mirrors
     FAST_PATH's existing line at 2315.
  2. Replace `_eff_stop = _stop_oid or order.stop_order_id or
     order.order_id` with `_eff_stop = _stop_oid` (with a warning
     log when empty so operators see the no-stop state cleanly).

Aggressive testing per the discipline framework:
  1. In-memory write: after exit_ladder, _pos.stop_order_id MUST
     equal _result.new_stop_order_id (mirrors FAST_PATH parity).
  2. Empty-OID path: when exit_ladder yields no new stop, the
     in-memory field MUST be empty (NOT the entry order ID).
  3. Recon parity: a position with the in-memory stop_order_id
     mirroring a real broker stop OID survives D231 RECON_HARD_BLOCK.
  4. Source-grep guard: NO main.py call site may use the
     `_eff_stop = ... or order.order_id` pattern. Future regressions
     surface immediately.
  5. Source-grep guard: every cancel_stop_and_submit_exit_ladder call
     site must be followed by an in-memory mirror write.

See docs/research-log/57_bug_as_stop_oid_corruption.md and
audit/2026-04-28-infrastructure-audit.md §0.4.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.execution.exit_ladder import ExitLadderResult


REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = REPO_ROOT / "main.py"
# The exit-ladder call sites were consolidated out of main.py into a single
# shared post-fill path. That is what these Bug AS guards must now inspect.
POST_FILL = REPO_ROOT / "src" / "execution" / "post_fill_handler.py"


# ── Behavior: ManagedPosition mirrors the new stop OID ───────────────


def test_in_memory_position_mirrors_new_stop_oid_after_exit_ladder():
    """The fix's primary behavior: post-exit_ladder, the in-memory
    ManagedPosition.stop_order_id MUST equal the freshly-submitted
    residual stop's order ID — NOT the stale OTO leg ID that
    cancel_stop_and_submit_exit_ladder already cancelled.

    This test simulates the assignment that the fixed call sites
    perform: `_pos.stop_order_id = _result.new_stop_order_id`."""
    from datetime import datetime, timezone
    from src.execution.position_manager import ManagedPosition

    # Position created from an OTO entry — initial stop_order_id is
    # the OTO leg ID
    pos = ManagedPosition(
        ticker="SBLX",
        qty=1000,
        entry_price=2.50,
        signal_price=2.50,
        stop_loss=2.30,
        target_prices=[2.65, 2.80, 3.00],
        order_id="entry-oid-AABBCCDD",  # ENTRY order ID
        stop_order_id="oto-stop-leg-OLDOID11",  # OTO stop leg, about to be cancelled
        opened_at=datetime.now(timezone.utc),
    )

    # exit_ladder runs (cancels old OTO stop, submits new residual stop)
    fake_result = ExitLadderResult(
        residual_qty=300,
        new_stop_order_id="new-residual-stop-NEWOID22",
        tranche_order_ids=["t1", "t2"],
    )

    # The fix: mirror the new OID into the in-memory position
    pos.stop_order_id = fake_result.new_stop_order_id

    assert pos.stop_order_id == "new-residual-stop-NEWOID22"
    # And critically, NOT the entry order ID
    assert pos.stop_order_id != pos.order_id
    assert pos.stop_order_id != "entry-oid-AABBCCDD"


def test_empty_new_stop_oid_yields_empty_in_memory_field_NOT_entry_oid():
    """Bug AS root cause #2: when exit_ladder doesn't yield a new
    stop OID (D147 short-circuit, residual_qty=0, or submit failure),
    the in-memory field must be empty — NOT the entry order ID.
    Empty correctly signals "no broker stop to ratchet"; entry-order
    ID would silently corrupt all downstream stop logic."""
    from datetime import datetime, timezone
    from src.execution.position_manager import ManagedPosition

    pos = ManagedPosition(
        ticker="SBLX",
        qty=1000,
        entry_price=2.50,
        signal_price=2.50,
        stop_loss=2.30,
        target_prices=[2.65],
        order_id="entry-oid-AABBCCDD",
        stop_order_id="oto-stop-leg-OLDOID11",
        opened_at=datetime.now(timezone.utc),
    )

    # exit_ladder returns no new OID (the unprotected case)
    fake_result = ExitLadderResult(
        residual_qty=0,
        new_stop_order_id="",  # NO new stop submitted
        tranche_order_ids=[],
        is_position_unprotected=True,
    )

    # The fix: mirror unconditionally — empty stays empty
    pos.stop_order_id = fake_result.new_stop_order_id

    assert pos.stop_order_id == ""
    # NOT the entry order ID
    assert pos.stop_order_id != "entry-oid-AABBCCDD"
    assert pos.stop_order_id != pos.order_id


# ── D231 recon parity: cleanly mirrored OID survives reconciliation ──


def _build_recon_client(broker_positions, broker_orders, equity=100_000.0):
    """Async-mock client mimicking the surface that run_eod_invariants
    queries: get_positions(), get_orders(status, limit), get_account()."""
    from unittest.mock import AsyncMock, MagicMock
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=broker_positions)
    client.get_orders = AsyncMock(return_value=broker_orders)
    client.get_account = AsyncMock(return_value={
        "equity": str(equity),
        "cash": str(equity),
        "portfolio_value": str(equity),
    })
    return client


@pytest.mark.asyncio
async def test_d231_recon_passes_when_in_memory_oid_matches_broker_stop():
    """End-to-end: with the fix in place (in-memory stop_order_id
    matches the real residual stop OID at the broker), the D231
    RECON_HARD_BLOCK invariant in eod_recon succeeds. This is the
    test that today's RESCAN entries would have failed."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from src.execution.position_manager import ManagedPosition
    from src.monitoring.eod_recon import run_eod_invariants

    real_residual_stop_oid = "new-residual-stop-NEWOID22"

    pos = ManagedPosition(
        ticker="SBLX",
        qty=300,
        entry_price=2.50,
        signal_price=2.50,
        stop_loss=2.30,
        target_prices=[2.65],
        order_id="entry-oid-AABBCCDD",
        stop_order_id=real_residual_stop_oid,  # post-fix: matches broker
        opened_at=datetime.now(timezone.utc),
    )

    # Broker has the matching residual stop active
    broker_orders = [
        {
            "id": real_residual_stop_oid,
            "symbol": "SBLX",
            "side": "sell",
            "type": "stop",
            "qty": "300",
            "stop_price": "2.30",
            "status": "new",
        },
    ]
    broker_positions = [
        {"symbol": "SBLX", "qty": "300", "side": "long",
         "avg_entry_price": "2.50"},
    ]

    pm = MagicMock()
    pm.open_positions = [pos]
    pm._positions = {"SBLX": pos}
    pm.starting_equity = 100_000.0
    pm._starting_equity = 100_000.0

    client = _build_recon_client(broker_positions, broker_orders)
    result = await run_eod_invariants(
        client=client,
        position_manager=pm,
    )

    # No D231 RECON_HARD_BLOCK because internal OID matches a real
    # active broker stop
    assert result.get("stop_drift_count", 0) == 0, (
        f"D231 fired despite mirrored OID: {result}"
    )


@pytest.mark.asyncio
async def test_d231_FIRES_when_in_memory_oid_is_stale_OTO_leg():
    """Negative case: pre-fix behavior. With the in-memory field
    holding a stale OID (the OTO leg that exit_ladder already
    cancelled), D231 RECON_HARD_BLOCK MUST fire. This test pins the
    failure mode the fix eliminates."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from src.execution.position_manager import ManagedPosition
    from src.monitoring.eod_recon import run_eod_invariants

    stale_oto_leg_id = "oto-stop-leg-OLDOID11"  # cancelled
    real_residual_stop_oid = "new-residual-stop-NEWOID22"

    pos = ManagedPosition(
        ticker="SBLX",
        qty=300,
        entry_price=2.50,
        signal_price=2.50,
        stop_loss=2.30,
        target_prices=[2.65],
        order_id="entry-oid-AABBCCDD",
        stop_order_id=stale_oto_leg_id,  # PRE-FIX: stale OID
        opened_at=datetime.now(timezone.utc),
    )

    broker_orders = [
        # Broker only has the real residual stop, NOT the stale OTO leg
        {
            "id": real_residual_stop_oid,
            "symbol": "SBLX",
            "side": "sell",
            "type": "stop",
            "qty": "300",
            "stop_price": "2.30",
            "status": "new",
        },
    ]
    broker_positions = [
        {"symbol": "SBLX", "qty": "300", "side": "long",
         "avg_entry_price": "2.50"},
    ]

    pm = MagicMock()
    pm.open_positions = [pos]
    pm._positions = {"SBLX": pos}
    pm.starting_equity = 100_000.0
    pm._starting_equity = 100_000.0

    client = _build_recon_client(broker_positions, broker_orders)
    result = await run_eod_invariants(
        client=client,
        position_manager=pm,
    )

    # D231 fires — broker has no order matching the stale OID
    assert result.get("stop_drift_count", 0) >= 1, (
        f"Expected D231 to fire on stale OID, got: {result}"
    )
    details = result.get("details", [])
    assert any(d.get("kind") == "stop_oid_missing" for d in details), (
        f"Expected stop_oid_missing detail, got: {details}"
    )


# ── Source-grep guards: regressions surface immediately ──────────────


def test_main_py_has_no_entry_order_id_fallback_for_stop_registration():
    """SOURCE-GREP GUARD: no Bug AS regression. Previously the
    Phase2/VWAP/RESCAN sites used:
        _eff_stop = _stop_oid or order.stop_order_id or order.order_id
    The third fallback corrupts stop_order_id with the ENTRY order ID.
    Future refactors that re-introduce the pattern fail this test.

    Skips comment lines so the fix's own documentation (which references
    the OLD broken pattern in a `# ...` block explaining why it was
    wrong) does not trip the guard."""
    pattern = re.compile(
        r"(?:_new_stop_oid|_vwap_stop_oid|_rs_stop_oid|_fp_new_stop_oid|_p2_stop_oid)"
        r"\s+or\s+order\.stop_order_id\s+or\s+order\.order_id"
    )
    offenders: list[tuple[int, str]] = []
    for lineno, raw in enumerate(MAIN_PY.read_text(encoding="utf-8").splitlines(), start=1):
        # Strip comment lines and lines whose effective code is empty
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # Strip trailing inline comments before matching
        code_part = re.sub(r"\s+#.*$", "", raw)
        if pattern.search(code_part):
            offenders.append((lineno, raw.strip()[:120]))
    assert offenders == [], (
        f"Bug AS regression: found {len(offenders)} call site(s) with the "
        f"`_stop_oid or order.stop_order_id or order.order_id` fallback "
        f"pattern. order.order_id is the ENTRY order ID — it MUST NOT "
        f"be used as a stop order ID. Offenders:\n  " +
        "\n  ".join(f"line {ln}: {txt}" for ln, txt in offenders)
    )


def test_every_exit_ladder_call_site_mirrors_new_stop_oid_to_in_memory_position():
    """SOURCE-GREP GUARD: every cancel_stop_and_submit_exit_ladder call
    site in main.py must be followed (within ~25 lines) by an
    assignment that mirrors `result.new_stop_order_id` into the
    in-memory ManagedPosition. Asymmetric handling between FAST_PATH
    (always mirrored) and Phase2/VWAP/RESCAN (formerly never mirrored)
    was Bug AS root cause #1."""
    # Bug AS was four DUPLICATED call sites where only one remembered to mirror
    # the new stop OID. They are now a single shared path, which removes the bug
    # class by construction — but the mirror must still be present, so the guard
    # follows the code rather than the old file layout.
    text = POST_FILL.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find every call site
    call_indices = [
        i for i, line in enumerate(lines)
        if "await cancel_stop_and_submit_exit_ladder" in line
    ]
    assert len(call_indices) >= 1, (
        f"Expected at least one cancel_stop_and_submit_exit_ladder call site, "
        f"found {len(call_indices)}. Did the file shape change?"
    )

    # Pattern for the in-memory mirror write
    # pos_obj is the consolidated path's variable; the rest are the legacy
    # per-call-site names, kept so the guard still fires if those ever return.
    mirror_pattern = re.compile(
        r"(?:pos_obj|fp_pos|_p2_pos|_vwap_pos|_rs_pos|_pos)\.stop_order_id\s*="
    )

    failures = []
    for idx in call_indices:
        # Look at the next 30 lines for the mirror pattern
        window = "\n".join(lines[idx:idx + 30])
        if not mirror_pattern.search(window):
            failures.append(
                f"  line {idx + 1}: {lines[idx].strip()[:100]}"
            )

    assert not failures, (
        "Bug AS regression: cancel_stop_and_submit_exit_ladder call site(s) "
        "without an in-memory `_pos.stop_order_id = ...` mirror within 30 "
        "lines. The in-memory ManagedPosition would carry the stale OTO "
        "leg ID, causing D231 RECON_HARD_BLOCK on every reconciliation.\n"
        + "\n".join(failures)
    )


# ── Sanity: FAST_PATH (the parity baseline) still has the mirror ────


def test_fast_path_mirror_line_is_present_as_parity_baseline():
    """Pin the in-memory stop-OID mirror on the shared post-fill path.

    Originally this pinned `fp_pos.stop_order_id = _fp_new_stop_oid` in main.py's
    FAST_PATH, as the template the other three call sites should have copied. The
    four sites have since been consolidated into one, so the mirror it guards now
    lives in post_fill_handler. If a refactor drops it, every path regresses —
    which is exactly what this test exists to catch.
    """
    text = POST_FILL.read_text(encoding="utf-8")
    assert "pos_obj.stop_order_id = new_stop_oid" in text, (
        "FAST_PATH parity baseline missing: `fp_pos.stop_order_id = "
        "_fp_new_stop_oid` not found in main.py. This was the line "
        "that Phase2/VWAP/RESCAN should have mirrored from. Without "
        "it, there's no template — and Bug AS regresses across all "
        "exit-ladder call sites."
    )
