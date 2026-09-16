"""doc 188: tests for the opening-range continuation detector (the validated edge)."""
from __future__ import annotations

from src.analysis import opening_range as orng


def _bars(prefix="2026-05-29T13:3"):
    # 9:30-9:34 ET = 13:30-13:34 UTC (EDT). Bullish opening range: 10.0 -> 10.8, high 11.
    return [
        {"t": f"{prefix}0:00Z", "o": 10.0, "h": 10.5, "l": 9.9, "c": 10.4, "v": 50000},
        {"t": f"{prefix}1:00Z", "o": 10.4, "h": 11.0, "l": 10.3, "c": 10.8, "v": 40000},
        {"t": f"{prefix}5:00Z", "o": 10.8, "h": 11.2, "l": 10.7, "c": 11.0, "v": 30000},  # 9:35 — excluded
    ]


def test_extract_opening_bar_window():
    ob = orng.extract_opening_bar(_bars())
    assert ob is not None
    assert ob["o"] == 10.0          # first 9:30 open
    assert ob["h"] == 11.0          # max high in 9:30-9:34 (excludes the 9:35 bar's 11.2)
    assert ob["l"] == 9.9
    assert ob["c"] == 10.8          # last in-window close
    assert ob["v"] == 90000         # 50k+40k (9:35 bar excluded)


def test_confirmed_continuation():
    ob = {"o": 10.0, "h": 11.0, "l": 9.9, "c": 10.8, "v": 90000}  # bullish
    s = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.9,
                            rvol_proxy=8.0, rvol_threshold=1.0)
    assert s.range_sign == 1
    assert s.broke_high is True       # 11.5 > 11.0
    assert s.above_vwap is True       # 11.5 >= 10.9
    assert s.confirmed is True
    assert s.score > 0.6


def test_not_confirmed_below_vwap():
    ob = {"o": 10.0, "h": 11.0, "l": 9.9, "c": 10.8, "v": 90000}
    s = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=12.0,  # below VWAP
                            rvol_proxy=8.0)
    assert s.above_vwap is False
    assert s.confirmed is False


def test_not_confirmed_bearish_open():
    ob = {"o": 10.8, "h": 11.0, "l": 9.9, "c": 10.0, "v": 90000}  # bearish (c<o)
    s = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.0, rvol_proxy=8.0)
    assert s.range_sign == -1
    assert s.confirmed is False       # don't fade the open / don't long a bearish open


def test_not_confirmed_low_rvol():
    ob = {"o": 10.0, "h": 11.0, "l": 9.9, "c": 10.8, "v": 90000}
    s = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.9,
                            rvol_proxy=0.3, rvol_threshold=1.0)  # RVOL too low
    assert s.confirmed is False


def test_score_monotonic_in_rvol():
    ob = {"o": 10.0, "h": 11.0, "l": 9.9, "c": 10.8, "v": 90000}
    lo = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.9, rvol_proxy=1.0)
    hi = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.9, rvol_proxy=10.0)
    assert hi.score > lo.score        # higher RVOL -> higher continuation confidence


def test_precise_baseline_overrides_proxy():
    ob = {"o": 10.0, "h": 11.0, "l": 9.9, "c": 10.8, "v": 90000}
    s = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.9,
                            baseline_5min_volume=30000, rvol_proxy=999)
    assert abs(s.opening_rvol - 3.0) < 1e-6   # 90000/30000 = 3.0, not the proxy


def test_tracker_capture_and_signal():
    t = orng.OpeningRangeTracker()
    assert t.capture("AAA", _bars()) is True
    assert t.has("AAA")
    s = t.signal("AAA", current_price=11.5, vwap=10.9, rvol_proxy=8.0)
    assert s is not None and s.confirmed is True
    t.reset()
    assert not t.has("AAA")


def test_features_shape():
    ob = {"o": 10.0, "h": 11.0, "l": 9.9, "c": 10.8, "v": 90000}
    s = orng.compute_signal(opening_bar=ob, current_price=11.5, vwap=10.9, rvol_proxy=8.0)
    f = s.as_features()
    assert set(f.keys()) == {"opening_rvol_log", "opening_range_sign", "broke_or_high"}
    assert f["opening_range_sign"] == 1.0 and f["broke_or_high"] == 1.0
