"""Tests for src/analysis/corpus_reconciliation.py.

Per PROMPT_08 §6.3, 5 cases that exercise the Bug AU reconciliation
contract: long match, long with qty drift, short flip, multi-leg buy,
missing broker truth. Plus 3 bonus cases.
"""
from __future__ import annotations

import math
import pandas as pd
import pytest

from src.analysis.corpus_reconciliation import (
    reconcile_one_row,
    load_broker_fills_for,
)


def _broker_df(rows):
    return pd.DataFrame(rows)


# §6.3 Case 1: long trade with matching qty -> flag NULL
def test_long_match_no_flag():
    fills = _broker_df([{"side": "buy", "qty": 1000, "price": 5.00}])
    r = reconcile_one_row(
        ticker="X", session_date="2026-04-22",
        journal_qty=1000, journal_entry_avg_px=5.00,
        broker_fills=fills,
    )
    assert r.side == "long"
    assert r.broker_qty == 1000
    assert r.journal_qty == 1000
    assert r.qty_drift_pct == 0.0
    assert r.n_legs == 1
    assert r.data_integrity_flag is None
    assert r.canonical_qty == 1000
    assert r.canonical_entry_avg_px == 5.00


# §6.3 Case 2: long with 10% qty drift -> qty_mismatch_AU
def test_long_with_qty_drift_flagged():
    fills = _broker_df([{"side": "buy", "qty": 900, "price": 5.00}])
    r = reconcile_one_row(
        ticker="X", session_date="2026-04-22",
        journal_qty=1000, journal_entry_avg_px=5.00,
        broker_fills=fills,
    )
    assert r.side == "long"
    assert r.broker_qty == 900
    assert r.qty_drift_pct == pytest.approx(-10.0)
    assert r.data_integrity_flag == "qty_mismatch_AU"
    assert r.canonical_qty == 900


# §6.3 Case 3: short trade -> side_mismatch_AU
def test_short_trade_flagged_side_mismatch():
    """OGN-class case: journal says BUY 999 @ $11.25; broker says
    sell_short 666 @ $13.18. Side wins; broker is canonical."""
    fills = _broker_df([
        {"side": "sell_short", "qty": 333, "price": 13.17},
        {"side": "sell_short", "qty": 333, "price": 13.18},
    ])
    r = reconcile_one_row(
        ticker="OGN", session_date="2026-04-27",
        journal_qty=999, journal_entry_avg_px=11.25,
        broker_fills=fills,
    )
    assert r.side == "short"
    assert r.broker_qty == 666
    assert r.n_legs == 2
    assert r.data_integrity_flag == "side_mismatch_AU"
    assert r.canonical_qty == 666
    assert r.canonical_entry_avg_px == pytest.approx(13.175)


# §6.3 Case 4: multi-leg buy -> n_legs=3
def test_multi_leg_buy_aggregated():
    fills = _broker_df([
        {"side": "buy", "qty": 200, "price": 9.50},
        {"side": "buy", "qty": 300, "price": 9.55},
        {"side": "buy", "qty": 500, "price": 9.60},
    ])
    r = reconcile_one_row(
        ticker="X", session_date="2026-04-22",
        journal_qty=1000, journal_entry_avg_px=9.55,
        broker_fills=fills,
    )
    assert r.side == "long"
    assert r.broker_qty == 1000
    assert r.n_legs == 3
    assert r.qty_drift_pct == 0.0
    assert r.data_integrity_flag is None
    assert r.canonical_entry_avg_px == pytest.approx(9.565)


# §6.3 Case 5: missing broker truth -> flag=missing_broker_truth
def test_missing_broker_truth():
    fills = _broker_df([])
    r = reconcile_one_row(
        ticker="X", session_date="2026-04-22",
        journal_qty=1000, journal_entry_avg_px=5.00,
        broker_fills=fills,
    )
    assert r.side == "long_legacy"
    assert r.broker_qty == 0
    assert math.isnan(r.qty_drift_pct)
    assert r.n_legs == 0
    assert r.data_integrity_flag == "missing_broker_truth"
    assert r.canonical_qty == 1000
    assert r.canonical_entry_avg_px == 5.00


# Bonus: small qty drift (< 5% threshold) does NOT flag
def test_small_qty_drift_below_threshold_no_flag():
    fills = _broker_df([{"side": "buy", "qty": 1020, "price": 5.00}])
    r = reconcile_one_row(
        ticker="X", session_date="2026-04-22",
        journal_qty=1000, journal_entry_avg_px=5.00,
        broker_fills=fills,
    )
    assert r.qty_drift_pct == pytest.approx(2.0)
    assert r.data_integrity_flag is None


# Bonus: side_ambiguous when both buys + shorts present
def test_side_ambiguous_when_both_present():
    fills = _broker_df([
        {"side": "buy", "qty": 500, "price": 5.00},
        {"side": "sell_short", "qty": 200, "price": 5.10},
    ])
    r = reconcile_one_row(
        ticker="X", session_date="2026-04-22",
        journal_qty=500, journal_entry_avg_px=5.00,
        broker_fills=fills,
    )
    assert r.side == "long"
    assert r.broker_qty == 500
    assert r.data_integrity_flag == "side_ambiguous_AU"


# Bonus: load_broker_fills_for filters correctly
def test_load_broker_fills_for_filters_correctly():
    df = pd.DataFrame([
        {"symbol": "AAA", "transaction_time": "2026-04-22T15:00:00Z",
         "side": "buy", "qty": 100, "price": 1.00},
        {"symbol": "BBB", "transaction_time": "2026-04-22T15:00:00Z",
         "side": "buy", "qty": 200, "price": 2.00},
        {"symbol": "AAA", "transaction_time": "2026-04-23T15:00:00Z",
         "side": "buy", "qty": 300, "price": 3.00},
    ])
    out = load_broker_fills_for(df, "AAA", "2026-04-22")
    assert len(out) == 1
    assert int(out.iloc[0]["qty"]) == 100


def test_load_broker_fills_for_empty_input():
    df = pd.DataFrame()
    out = load_broker_fills_for(df, "X", "2026-04-22")
    assert out.empty


# End-to-end with real OGN-shaped broker tape
def test_ogn_real_broker_tape_shape():
    """Replicate the canonical OGN tape from Bug AU finding."""
    fills = _broker_df([
        {"side": "sell_short", "qty": 117, "price": 13.20},
        {"side": "sell_short", "qty": 333, "price": 13.17},
        {"side": "sell_short", "qty": 40,  "price": 13.16},
        {"side": "sell_short", "qty": 176, "price": 13.16},
        {"side": "buy", "qty": 666, "price": 13.20},
    ])
    r = reconcile_one_row(
        ticker="OGN", session_date="2026-04-27",
        journal_qty=999, journal_entry_avg_px=11.25,
        broker_fills=fills,
    )
    # Both sides present -> side_ambiguous_AU + side defaults to long
    assert r.data_integrity_flag is not None, (
        "OGN-class trade must flag SOMETHING"
    )
    assert r.side == "long"
    assert r.broker_qty == 666
    assert r.data_integrity_flag == "side_ambiguous_AU"
