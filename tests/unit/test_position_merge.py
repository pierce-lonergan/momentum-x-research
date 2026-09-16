"""doc 227: add_position MUST merge a same-ticker re-buy, not replace it.

The 6/2 LASE +143% ghost: LASE was bought 3x (multi-buy on a runner). The old
`self._positions[ticker] = position` REPLACED the tracker each time, discarding prior shares
the broker still held -> the tracker ended 10,680 sh short of broker truth -> D218/D231
qty-drift all day -> the EOD close 403'd -> accidental overnight carry. These pin the merge.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config.settings import ExecutionConfig
from src.execution.position_manager import PositionManager, ManagedPosition


def _pm():
    return PositionManager(config=ExecutionConfig(), starting_equity=100_000.0)


def _pos(ticker, qty, entry, **kw):
    return ManagedPosition(
        ticker=ticker, qty=qty, entry_price=entry, signal_price=entry,
        stop_loss=round(entry * 0.95, 2), remaining_qty=qty,
        opened_at=kw.pop("opened_at", datetime.now(timezone.utc)), **kw)


def test_rebuy_merges_qty_and_weighted_avg_entry():
    """The LASE case: 3 buys must sum to the broker's aggregate, not replace down to the last."""
    pm = _pm()
    pm.add_position(_pos("LASE", 16020, 1.34))
    pm.add_position(_pos("LASE", 16092, 1.32))   # re-buy — old code DROPPED the first 16020
    p = pm._positions["LASE"]
    assert p.qty == 16020 + 16092 == 32112          # summed, not replaced
    # weighted-avg entry
    assert abs(p.entry_price - (1.34 * 16020 + 1.32 * 16092) / 32112) < 1e-4
    assert p.remaining_qty == 32112


def test_three_buys_match_broker_aggregate():
    """3-buy runner -> tracker qty == broker aggregate (no drift -> no D218/D231 ghost).
    The 6/2 LASE bug: old REPLACE left the tracker at the LAST buy's qty (e.g. 6092) while the
    broker held the SUM (26,772) -> 10,680 drift. The merge must sum to the broker aggregate."""
    pm = _pm()
    fills = [(10680, 1.29), (10000, 1.34), (6092, 1.32)]  # sum = 26,772 (the broker aggregate)
    for q, px in fills:
        pm.add_position(_pos("LASE", q, px))
    assert pm._positions["LASE"].qty == sum(q for q, _ in fills) == 26772
    # the OLD bug would have left it at 6092 (the last buy) -> a 20,680-share ghost
    assert pm._positions["LASE"].qty != 6092


def test_only_one_position_after_rebuy():
    """A re-buy must NOT create a second tracker entry for the same ticker."""
    pm = _pm()
    pm.add_position(_pos("LASE", 100, 1.30))
    pm.add_position(_pos("LASE", 200, 1.40))
    assert len([t for t in pm._positions if t == "LASE"]) == 1
    assert len(pm.open_positions) == 1


def test_keeps_earliest_opened_at():
    """opened_at stays the EARLIEST (the position's true age for D91 overnight logic)."""
    pm = _pm()
    early = datetime(2026, 6, 2, 13, 30, tzinfo=timezone.utc)
    late = datetime(2026, 6, 2, 14, 30, tzinfo=timezone.utc)
    pm.add_position(_pos("LASE", 100, 1.30, opened_at=early))
    pm.add_position(_pos("LASE", 100, 1.35, opened_at=late))
    assert pm._positions["LASE"].opened_at == early


def test_distinct_tickers_do_not_merge():
    pm = _pm()
    pm.add_position(_pos("AAA", 100, 5.0))
    pm.add_position(_pos("BBB", 100, 6.0))
    assert pm._positions["AAA"].qty == 100 and pm._positions["BBB"].qty == 100
    assert len(pm.open_positions) == 2


def test_add_position_returns_canonical_object_doc228():
    """doc 228: add_position must RETURN the canonical tracked object so callers reconcile/
    persist/ladder against the merged tracker, not the discarded single-fill object."""
    pm = _pm()
    first = _pos("LASE", 100, 1.30)
    r1 = pm.add_position(first)
    assert r1 is first and r1 is pm._positions["LASE"]   # new position -> itself, and it's tracked
    rebuy = _pos("LASE", 50, 1.40)
    r2 = pm.add_position(rebuy)
    assert r2 is pm._positions["LASE"]                    # re-buy -> the MERGED (existing) object
    assert r2 is not rebuy                                # NOT the discarded single-fill object
    assert r2.qty == 150                                  # and it carries the merged qty


def test_merge_weights_by_held_not_original_after_tranche():
    """doc 228 (#5): if a tranche already sold, weight the avg by remaining_qty (held), not the
    original qty, and set qty to the held total."""
    pm = _pm()
    p = _pos("LASE", 100, 1.30)
    pm.add_position(p)
    p.remaining_qty = 67   # simulate a tranche sold 33 (qty stays 100, remaining drops)
    pm.add_position(_pos("LASE", 50, 1.60))
    m = pm._positions["LASE"]
    assert m.qty == 67 + 50 == 117                        # held + new, not 150
    assert abs(m.entry_price - (1.30 * 67 + 1.60 * 50) / 117) < 1e-4


def test_merge_rescales_targets_and_stop_and_resets_tranches_doc229():
    """doc 229 (#4): a re-buy HIGHER moves the blended entry up; buy#1's ABSOLUTE targets/stop
    must rescale to the new entry so the re-armed exit ladder never sells BELOW the new cost
    basis, and tranches_filled resets for the fresh re-ladder."""
    pm = _pm()
    p = _pos("LASE", 100, 1.30)
    p.target_prices = [1.365, 1.43, 1.56]   # +5/+10/+20% off 1.30
    p.stop_loss = 1.23                       # -5.4% off 1.30
    p.tranches_filled = 1
    pm.add_position(p)
    pm.add_position(_pos("LASE", 100, 1.70))  # re-buy higher -> blended entry 1.50
    m = pm._positions["LASE"]
    assert abs(m.entry_price - 1.50) < 1e-6
    # every target strictly ABOVE the new blended entry (no sell-below-cost on the merged qty)
    assert all(t > m.entry_price for t in m.target_prices), m.target_prices
    # %-offset geometry preserved: first target still ~+5% off the new entry
    assert abs(m.target_prices[0] / m.entry_price - 1.365 / 1.30) < 1e-3
    # stop rescaled proportionally (same % below the higher entry -> tighter in absolute $)
    assert abs(m.stop_loss / m.entry_price - 1.23 / 1.30) < 1e-3
    assert m.tranches_filled == 0


def test_fully_exited_not_resurrected():
    """doc 228 (#6): a position with remaining_qty==0 (fully exited, not yet removed) is
    REPLACED by a re-buy, not resurrected into phantom shares."""
    pm = _pm()
    p = _pos("LASE", 100, 1.30)
    pm.add_position(p)
    p.remaining_qty = 0    # fully exited
    pm.add_position(_pos("LASE", 50, 1.60))
    assert pm._positions["LASE"].qty == 50   # the new buy only, not 150
