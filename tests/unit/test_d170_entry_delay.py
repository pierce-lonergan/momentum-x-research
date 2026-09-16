"""
D170: Unit tests for the Entry Delay / Observation Window system.

Covers all 16 scenarios specified in the D170 design document.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.execution.entry_delay import (
    CandidateObservation,
    EntryDelayManager,
    ObservationConfig,
    ObservationState,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _t(offset_minutes: float = 0.0) -> datetime:
    """Return a UTC datetime offset_minutes from 'now'."""
    return datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)


def _make_manager(
    observation_minutes: float = 15.0,
    min_observation_minutes: float = 5.0,
    max_observation_minutes: float = 30.0,
    require_above_vwap: bool = True,
    require_higher_lows: bool = True,
    max_drawdown_from_open_pct: float = 0.10,
    min_volume_sustain_pct: float = 0.50,
    require_no_new_low: bool = True,
    early_entry_min_mfcs: float = 0.60,
    early_entry_min_agents_bullish: int = 4,
    enabled: bool = True,
) -> EntryDelayManager:
    cfg = ObservationConfig(
        enabled=enabled,
        observation_minutes=observation_minutes,
        min_observation_minutes=min_observation_minutes,
        max_observation_minutes=max_observation_minutes,
        require_above_vwap=require_above_vwap,
        require_higher_lows=require_higher_lows,
        max_drawdown_from_open_pct=max_drawdown_from_open_pct,
        min_volume_sustain_pct=min_volume_sustain_pct,
        require_no_new_low=require_no_new_low,
        early_entry_min_mfcs=early_entry_min_mfcs,
        early_entry_min_agents_bullish=early_entry_min_agents_bullish,
    )
    return EntryDelayManager(config=cfg)


def _register(
    mgr: EntryDelayManager,
    ticker: str = "AAPL",
    open_price: float = 10.0,
    mfcs: float = 0.45,
    agent_signals: dict | None = None,
    registered_minutes_ago: float = 0.0,
) -> CandidateObservation:
    """Register a candidate and return its observation record."""
    ts = _t(registered_minutes_ago)
    signals = agent_signals if agent_signals is not None else {}
    mgr.register_candidate(ticker, open_price, mfcs, signals, ts)
    return mgr.get_candidate(ticker)


# ── Test 1: Registration ──────────────────────────────────────────────────────

def test_candidate_registration():
    """Newly registered candidate starts in WATCHING state."""
    mgr = _make_manager()
    _register(mgr, "AAPL", open_price=10.0, mfcs=0.40)
    obs = mgr.get_candidate("AAPL")
    assert obs is not None
    assert obs.state == ObservationState.WATCHING
    assert obs.ticker == "AAPL"
    assert obs.open_price == 10.0
    assert obs.mfcs == 0.40


# ── Test 2: Approves healthy stock ───────────────────────────────────────────

def test_observation_approves_healthy_stock():
    """Stock above VWAP with rising prices should be APPROVED after window."""
    mgr = _make_manager(observation_minutes=15.0, min_observation_minutes=5.0)
    # Register 16 minutes ago so window has elapsed
    ts_registered = _t(16.0)
    mgr.register_candidate("HLTH", 10.0, 0.45, {}, ts_registered)

    # Feed rising price readings — above VWAP, higher lows, good volume
    for i, (price, vwap, vol) in enumerate([
        (10.50, 10.20, 100_000),  # reading 1: above VWAP
        (10.60, 10.25, 95_000),   # reading 2: higher low
        (10.70, 10.30, 90_000),   # reading 3: higher low
    ]):
        ts = ts_registered + timedelta(minutes=5 + i * 3)
        state = mgr.update_price("HLTH", price, vwap, vol, ts)

    assert state == ObservationState.APPROVED


# ── Test 3: Rejects fading stock ─────────────────────────────────────────────

def test_observation_rejects_fading_stock():
    """Stock below VWAP should be immediately REJECTED."""
    mgr = _make_manager(require_above_vwap=True, min_observation_minutes=1.0)
    ts = _t(8.0)
    mgr.register_candidate("FADE", 10.0, 0.40, {}, ts)

    # Price below VWAP after having two initial readings
    mgr.update_price("FADE", 10.10, 10.50, 80_000, ts + timedelta(minutes=1))
    state = mgr.update_price("FADE", 9.80, 10.50, 70_000, ts + timedelta(minutes=2))

    assert state == ObservationState.REJECTED
    obs = mgr.get_candidate("FADE")
    assert "below_vwap" in obs.rejection_reason


# ── Test 4: Rejects on excessive drawdown ────────────────────────────────────

def test_observation_rejects_drawdown():
    """Stock that drops >10% from open should be REJECTED."""
    mgr = _make_manager(
        max_drawdown_from_open_pct=0.10,
        require_above_vwap=False,
        require_higher_lows=False,
        require_no_new_low=False,
    )
    ts = _t(8.0)
    mgr.register_candidate("DROP", 10.0, 0.40, {}, ts)

    # First reading fine
    mgr.update_price("DROP", 9.50, 9.00, 100_000, ts + timedelta(minutes=1))
    # Second reading drops 15% from open → exceeds 10% max drawdown
    state = mgr.update_price("DROP", 8.40, 8.00, 100_000, ts + timedelta(minutes=2))

    assert state == ObservationState.REJECTED
    obs = mgr.get_candidate("DROP")
    assert "drawdown" in obs.rejection_reason


# ── Test 5: Expires after max window ─────────────────────────────────────────

def test_observation_expires():
    """Candidate that never meets criteria within max window → EXPIRED."""
    mgr = _make_manager(max_observation_minutes=30.0, require_above_vwap=False,
                         require_higher_lows=False, require_no_new_low=False,
                         min_volume_sustain_pct=0.0)
    # Register 31 minutes ago — beyond max window
    ts_registered = _t(31.0)
    mgr.register_candidate("SLOW", 10.0, 0.45, {}, ts_registered)

    # Feed a reading (required to trigger expiry check)
    ts_registered2 = _t(30.5)
    mgr.update_price("SLOW", 10.10, 9.50, 90_000, ts_registered2)
    state = mgr.update_price("SLOW", 10.15, 9.55, 85_000, _now())

    assert state == ObservationState.EXPIRED


# ── Test 6: Early entry on strong signal ─────────────────────────────────────

def test_early_entry_strong_signal():
    """MFCS > 0.60 with 4+ bullish agents should be approved after min_observation."""
    mgr = _make_manager(
        observation_minutes=15.0,
        min_observation_minutes=5.0,
        require_above_vwap=True,
        require_higher_lows=True,
        early_entry_min_mfcs=0.60,
        early_entry_min_agents_bullish=4,
    )
    strong_signals = {
        "technical_agent": "BULL",
        "news_agent": "BULL",
        "fundamental_agent": "BULL",
        "sentiment_agent": "BULL",
        "momentum_agent": "BULL",
    }
    # Register 6 minutes ago → past min_observation but before observation_minutes
    ts = _t(6.0)
    mgr.register_candidate("STRG", 10.0, 0.65, strong_signals, ts)

    # Feed two qualifying readings
    mgr.update_price("STRG", 10.30, 10.10, 100_000, ts + timedelta(minutes=3))
    state = mgr.update_price("STRG", 10.40, 10.15, 95_000, ts + timedelta(minutes=6))

    assert state == ObservationState.APPROVED


# ── Test 7: Weak signal must wait full window ─────────────────────────────────

def test_early_entry_weak_signal():
    """MFCS < 0.60 should not qualify for early entry; must wait full window."""
    mgr = _make_manager(
        observation_minutes=15.0,
        min_observation_minutes=5.0,
        early_entry_min_mfcs=0.60,
        early_entry_min_agents_bullish=4,
        require_above_vwap=True,
    )
    weak_signals = {"technical_agent": "BULL", "news_agent": "NEUTRAL"}
    # Register 7 minutes ago — past min_obs but before full window
    ts = _t(7.0)
    mgr.register_candidate("WEAK", 10.0, 0.45, weak_signals, ts)

    # Feed qualifying readings (above VWAP, higher lows)
    mgr.update_price("WEAK", 10.20, 10.00, 100_000, ts + timedelta(minutes=3))
    state = mgr.update_price("WEAK", 10.30, 10.05, 95_000, ts + timedelta(minutes=7))

    # Should still be WATCHING — criteria pass but MFCS too low for early entry
    assert state == ObservationState.WATCHING


# ── Test 8: VWAP check ────────────────────────────────────────────────────────

def test_above_vwap_check():
    """_check_above_vwap should pass when price >= VWAP and fail when below."""
    cfg = ObservationConfig()
    mgr = EntryDelayManager(cfg)

    obs_above = CandidateObservation(
        ticker="X", first_seen=_now(), open_price=10.0, mfcs=0.40, agent_signals={},
    )
    obs_above.record_price(_now(), 10.50, 10.30, 100_000)  # price > VWAP
    assert mgr._check_above_vwap(obs_above) is True

    obs_below = CandidateObservation(
        ticker="Y", first_seen=_now(), open_price=10.0, mfcs=0.40, agent_signals={},
    )
    obs_below.record_price(_now(), 9.80, 10.30, 100_000)  # price < VWAP
    assert mgr._check_above_vwap(obs_below) is False


# ── Test 9: Higher lows check ─────────────────────────────────────────────────

def test_higher_lows_check():
    """_check_higher_lows should detect when most recent price is a new all-time low."""
    cfg = ObservationConfig()
    mgr = EntryDelayManager(cfg)

    obs = CandidateObservation(
        ticker="Z", first_seen=_now(), open_price=10.0, mfcs=0.40, agent_signals={},
    )
    t = _now()
    # Sequence of higher lows
    obs.record_price(t, 10.10, 9.90, 100_000)
    obs.record_price(t + timedelta(seconds=30), 10.20, 9.95, 95_000)
    assert mgr._check_higher_lows(obs) is True

    # Now a lower low
    obs.record_price(t + timedelta(minutes=1), 9.90, 9.80, 90_000)  # new low!
    assert mgr._check_higher_lows(obs) is False


# ── Test 10: Volume sustain check ─────────────────────────────────────────────

def test_volume_sustain():
    """Volume must not drop below 50% of the opening bar."""
    cfg = ObservationConfig(min_volume_sustain_pct=0.50)
    mgr = EntryDelayManager(cfg)

    # Case 1: Volume sustained
    obs_ok = CandidateObservation(
        ticker="A", first_seen=_now(), open_price=10.0, mfcs=0.40, agent_signals={},
    )
    obs_ok.record_price(_now(), 10.10, 9.90, 100_000)  # baseline
    obs_ok.record_price(_now(), 10.20, 9.95, 60_000)   # 60% of baseline → ok
    assert mgr._check_volume_sustain(obs_ok) is True

    # Case 2: Volume dried up
    obs_dry = CandidateObservation(
        ticker="B", first_seen=_now(), open_price=10.0, mfcs=0.40, agent_signals={},
    )
    obs_dry.record_price(_now(), 10.10, 9.90, 100_000)  # baseline
    obs_dry.record_price(_now(), 10.20, 9.95, 40_000)   # 40% of baseline → fail
    assert mgr._check_volume_sustain(obs_dry) is False


# ── Test 11: Multiple candidates tracked independently ────────────────────────

def test_multiple_candidates_independent():
    """Two tickers in different states should not interfere with each other."""
    mgr = _make_manager(
        min_observation_minutes=5.0,
        observation_minutes=15.0,
        require_above_vwap=True,
        require_higher_lows=True,
    )
    ts_a = _t(16.0)
    ts_b = _t(16.0)

    mgr.register_candidate("AWIN", 10.0, 0.50, {}, ts_a)
    mgr.register_candidate("BLOS", 10.0, 0.50, {}, ts_b)

    # AWIN: healthy readings → APPROVED
    mgr.update_price("AWIN", 10.20, 9.90, 100_000, ts_a + timedelta(minutes=3))
    state_a = mgr.update_price("AWIN", 10.30, 9.95, 95_000, ts_a + timedelta(minutes=16))

    # BLOS: drops below VWAP → REJECTED
    mgr.update_price("BLOS", 10.20, 10.50, 100_000, ts_b + timedelta(minutes=3))
    state_b = mgr.update_price("BLOS", 9.50, 10.50, 80_000, ts_b + timedelta(minutes=6))

    assert state_a == ObservationState.APPROVED
    assert state_b == ObservationState.REJECTED


# ── Test 12: ARTL scenario — rejected (gap-and-fade) ─────────────────────────

def test_artl_scenario_rejected():
    """
    ARTL (Mar 30): open at $7.68 with VWAP at $8.22 (below by 6.5%).
    Should be immediately REJECTED by require_above_vwap.
    """
    mgr = _make_manager(require_above_vwap=True, min_observation_minutes=1.0)
    ts = _t(8.0)
    mgr.register_candidate("ARTL", open_price=7.68, mfcs=0.35, agent_signals={}, timestamp=ts)

    # First reading: price still below VWAP (matches real journal data)
    mgr.update_price("ARTL", 7.68, 8.2182, 50_000, ts + timedelta(minutes=1))
    state = mgr.update_price("ARTL", 7.60, 8.25, 40_000, ts + timedelta(minutes=2))

    assert state == ObservationState.REJECTED
    obs = mgr.get_candidate("ARTL")
    assert "below_vwap" in obs.rejection_reason


# ── Test 13: ELAB scenario — approved (real catalyst) ────────────────────────

def test_elab_scenario_approved():
    """
    ELAB (Mar 30): Entered at 10:14 EDT. Scenario: held above VWAP,
    real licensing catalyst. Observation window should approve it.
    """
    mgr = _make_manager(
        observation_minutes=15.0,
        min_observation_minutes=5.0,
        require_above_vwap=True,
    )
    # Simulate ELAB being identified 16 minutes ago at $3.775, VWAP $3.056
    ts = _t(16.0)
    mgr.register_candidate("ELAB", open_price=3.775, mfcs=0.50, agent_signals={}, timestamp=ts)

    # Price holds above VWAP throughout observation
    mgr.update_price("ELAB", 3.800, 3.056, 200_000, ts + timedelta(minutes=3))
    mgr.update_price("ELAB", 3.820, 3.100, 180_000, ts + timedelta(minutes=7))
    state = mgr.update_price("ELAB", 3.850, 3.120, 170_000, ts + timedelta(minutes=16))

    assert state == ObservationState.APPROVED


# ── Test 14: get_approved_candidates ─────────────────────────────────────────

def test_get_approved_candidates():
    """Only APPROVED candidates should be returned."""
    mgr = _make_manager(
        min_observation_minutes=5.0,
        observation_minutes=15.0,
        require_above_vwap=True,
    )
    # GOOD: above VWAP, registered long ago
    ts = _t(16.0)
    mgr.register_candidate("GOOD", 10.0, 0.50, {}, ts)
    mgr.update_price("GOOD", 10.20, 9.80, 100_000, ts + timedelta(minutes=3))
    mgr.update_price("GOOD", 10.30, 9.85, 95_000, ts + timedelta(minutes=16))

    # BAD: still watching (registered recently)
    ts2 = _t(2.0)
    mgr.register_candidate("NEW", 10.0, 0.45, {}, ts2)
    mgr.update_price("NEW", 10.10, 9.90, 100_000, ts2 + timedelta(minutes=1))
    mgr.update_price("NEW", 10.15, 9.92, 95_000, ts2 + timedelta(minutes=2))

    approved = mgr.get_approved_candidates()
    tickers = [o.ticker for o in approved]
    assert "GOOD" in tickers
    assert "NEW" not in tickers


# ── Test 15: remove candidate ────────────────────────────────────────────────

def test_remove_candidate():
    """remove() should cleanly delete the candidate."""
    mgr = _make_manager()
    _register(mgr, "RMVD")
    assert mgr.get_candidate("RMVD") is not None
    mgr.remove("RMVD")
    assert mgr.get_candidate("RMVD") is None


# ── Test 16: Disabled config immediately approves ────────────────────────────

def test_disabled_config():
    """When enabled=False, all candidates are immediately APPROVED at registration."""
    mgr = _make_manager(enabled=False)
    ts = _now()
    mgr.register_candidate("FAST", 10.0, 0.35, {}, ts)
    obs = mgr.get_candidate("FAST")
    assert obs.state == ObservationState.APPROVED
    assert obs.approval_time == ts


# ── Bonus: bullish_agent_count property ──────────────────────────────────────

def test_bullish_agent_count():
    """bullish_agent_count should count BULL/STRONG_BUY/BUY signals."""
    obs = CandidateObservation(
        ticker="T",
        first_seen=_now(),
        open_price=10.0,
        mfcs=0.50,
        agent_signals={
            "a": "BULL",
            "b": "STRONG_BUY",
            "c": "BUY",
            "d": "NEUTRAL",
            "e": "BEAR",
        },
    )
    assert obs.bullish_agent_count == 3


# ── Bonus: double-register is idempotent ─────────────────────────────────────

def test_double_register_idempotent():
    """Registering the same ticker twice should not create a new entry."""
    mgr = _make_manager()
    ts1 = _t(5.0)
    ts2 = _now()
    mgr.register_candidate("DUP", 10.0, 0.40, {}, ts1)
    mgr.register_candidate("DUP", 11.0, 0.50, {}, ts2)  # should be ignored
    obs = mgr.get_candidate("DUP")
    assert obs.open_price == 10.0  # first registration wins
    assert obs.first_seen == ts1


# ── Bonus: summary() returns structured snapshot ─────────────────────────────

def test_summary():
    """summary() should return a dict with state info for all candidates."""
    mgr = _make_manager()
    _register(mgr, "X1")
    _register(mgr, "X2")
    s = mgr.summary()
    assert "X1" in s
    assert "X2" in s
    assert s["X1"]["state"] == "watching"
