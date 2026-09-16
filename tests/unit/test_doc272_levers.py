"""doc 272 LEVER tests — FLAG-B default-OFF, no-op-when-OFF pins.

LEVER 1 (MOMENTUM_EMPTY_NOT_BEAR): EMPTY != BEAR fail-open classifier for
the D200-E4 catalyst gate and D204 news gates (src/execution/empty_not_bear.py,
wired at 4 block sites in main.py).

LEVER 2 (FAST_PATH_DIP_OVERRIDE_PCT): DIP_TABLE flatten via _dip_factor()
(src/execution/fast_path.py).

The critical pins:
  - With BOTH env vars unset, behavior is byte-identical to legacy:
    * absence_fail_open() returns "" for EVERY input -> gate decision
      identical to the pre-doc-272 block sites (simulated both ways below).
    * _dip_factor() returns exactly DIP_TABLE for every classification.
  - Neither lever ever raises, no matter how hostile the input.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.models import (
    AgentSignal,
    NewsSignal,
    RiskSignal,
    TradeVerdict,
)
from src.execution.empty_not_bear import (
    ABSENCE,
    BEARISH,
    ENV_FLAG,
    NO_SIGNALS,
    WEAK_DATA,
    absence_fail_open,
    classify_block,
    enabled,
)
from src.execution.fast_path import (
    DIP_OVERRIDE_ENV,
    DIP_TABLE,
    DipEntryCalculator,
    _dip_factor,
)


# ── Signal fixtures (object- and dict-shaped, as seen at the gate sites) ──

_NOW = datetime.now(timezone.utc)


def _sig(agent_id: str, signal: str, confidence: float = 0.5) -> AgentSignal:
    return AgentSignal(
        agent_id=agent_id,
        ticker="TEST",
        timestamp=_NOW,
        signal=signal,
        confidence=confidence,
        reasoning="synthetic",
    )


def _news(signal: str, confidence: float = 0.5, catalyst_type: str = "NONE") -> NewsSignal:
    return NewsSignal(
        agent_id="news_agent",
        ticker="TEST",
        timestamp=_NOW,
        signal=signal,
        confidence=confidence,
        reasoning="synthetic",
        catalyst_type=catalyst_type,
    )


def _risk(risk_verdict: str = "VETO") -> RiskSignal:
    return RiskSignal(
        agent_id="risk_agent",
        ticker="TEST",
        timestamp=_NOW,
        signal="NEUTRAL",
        confidence=0.5,
        reasoning="synthetic",
        risk_verdict=risk_verdict,
        veto_reason="synthetic veto" if risk_verdict == "VETO" else None,
    )


def _verdict(qty_multiplier: float = 1.0) -> TradeVerdict:
    return TradeVerdict(
        ticker="TEST",
        action="BUY",
        confidence=0.6,
        mfcs=0.40,
        entry_price=5.0,
        stop_loss=4.7,
        target_prices=[5.25, 5.5, 6.0],
        position_size_pct=0.10,
        qty_multiplier=qty_multiplier,
    )


# Synthetic-signal grid covering every shape the 4 gate sites can see.
# (label, signals, expected classification when flag ON, at D204 sites)
GRID = [
    ("empty_list", [], NO_SIGNALS),
    ("not_a_list", None, NO_SIGNALS),
    ("not_a_list_dict", {"agent_id": "news_agent"}, NO_SIGNALS),
    ("news_missing_others_present",
     [_sig("technical_agent", "BULL", 0.7), _sig("fundamental_agent", "NEUTRAL", 0.4)],
     ABSENCE),
    ("news_neutral_no_catalyst", [_news("NEUTRAL", 0.0, "NONE")], ABSENCE),
    ("news_neutral_no_catalyst_highconf",
     [_news("NEUTRAL", 0.9, "NONE"), _sig("technical_agent", "BULL", 0.8)],
     ABSENCE),
    ("news_neutral_dict_shape",
     [{"agent_id": "news_agent", "signal": "NEUTRAL", "confidence": 0.0,
       "catalyst_type": "NONE"}],
     ABSENCE),
    ("news_neutral_real_catalyst", [_news("NEUTRAL", 0.6, "FDA_APPROVAL")], WEAK_DATA),
    ("news_weak_bull", [_news("BULL", 0.2, "EARNINGS_BEAT")], WEAK_DATA),
    ("news_bear", [_news("BEAR", 0.8, "NONE")], BEARISH),
    ("news_strong_bear", [_news("STRONG_BEAR", 0.9, "NONE")], BEARISH),
    ("news_bear_dict_shape",
     [{"agent_id": "news_agent", "signal": "BEAR", "confidence": 0.7}],
     BEARISH),
    ("any_agent_strong_bear",
     [_sig("technical_agent", "STRONG_BEAR", 0.9), _news("NEUTRAL", 0.0, "NONE")],
     BEARISH),
    ("risk_veto_object", [_risk("VETO"), _news("NEUTRAL", 0.0, "NONE")], BEARISH),
    ("risk_veto_dict",
     [{"agent_id": "risk_agent", "signal": "NEUTRAL", "risk_verdict": "VETO"},
      {"agent_id": "news_agent", "signal": "NEUTRAL", "confidence": 0.0}],
     BEARISH),
    ("risk_caution_news_neutral",
     [_risk("CAUTION"), _news("NEUTRAL", 0.0, "NONE")],
     ABSENCE),
    ("garbage_entries", [42, None, "hello", object()], NO_SIGNALS),  # unparseable -> block
]


def _gate_decision_legacy(signals, *, catalyst_none_trigger: bool) -> str:
    """What every pre-doc-272 block site does once triggered: BLOCK."""
    return "BLOCK"


def _gate_decision_new(signals, *, catalyst_none_trigger: bool) -> str:
    """The doc-272 block site: fail-open ONLY when absence_fail_open is
    truthy (which requires the env flag), else the legacy BLOCK."""
    if absence_fail_open(signals, catalyst_none_trigger=catalyst_none_trigger):
        return "DOWNGRADE"
    return "BLOCK"


# ── LEVER 1: no-op pin (flag unset => byte-identical outcomes) ──────────

class TestLever1NoOpWhenOff:
    def test_flag_unset_gate_outcomes_identical_both_ways(self, monkeypatch):
        """THE no-op pin: with MOMENTUM_EMPTY_NOT_BEAR unset, run the gate
        logic both ways (legacy vs doc-272) on every synthetic signal set
        and assert identical outcomes at both the E4 and D204 sites."""
        monkeypatch.delenv(ENV_FLAG, raising=False)
        for label, signals, _expected in GRID:
            for trigger in (True, False):  # E4 site / D204 sites
                legacy = _gate_decision_legacy(signals, catalyst_none_trigger=trigger)
                new = _gate_decision_new(signals, catalyst_none_trigger=trigger)
                assert new == legacy == "BLOCK", (
                    f"flag-unset behavior diverged for {label} "
                    f"(catalyst_none_trigger={trigger}): legacy={legacy} new={new}"
                )

    def test_flag_unset_absence_fail_open_always_empty(self, monkeypatch):
        monkeypatch.delenv(ENV_FLAG, raising=False)
        for label, signals, _expected in GRID:
            assert absence_fail_open(signals) == "", label
            assert absence_fail_open(signals, catalyst_none_trigger=True) == "", label

    @pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "garbage"])
    def test_flag_falsy_values_disabled(self, monkeypatch, raw):
        monkeypatch.setenv(ENV_FLAG, raw)
        assert enabled() is False
        assert absence_fail_open([_news("NEUTRAL", 0.0, "NONE")]) == ""

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "On"])
    def test_flag_truthy_values_enabled(self, monkeypatch, raw):
        monkeypatch.setenv(ENV_FLAG, raw)
        assert enabled() is True


# ── LEVER 1: classification when flag ON ────────────────────────────────

class TestLever1Classification:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(ENV_FLAG, "1")

    @pytest.mark.parametrize("label,signals,expected", GRID)
    def test_grid_classification_d204_sites(self, label, signals, expected):
        cls, detail = classify_block(signals, catalyst_none_trigger=False)
        assert cls == expected, f"{label}: {cls} ({detail})"
        # fail-open iff ABSENCE
        fo = absence_fail_open(signals, catalyst_none_trigger=False)
        assert bool(fo) == (expected == ABSENCE), f"{label}: fail_open={fo!r}"

    def test_e4_site_bull_news_with_none_catalyst_is_absence(self):
        # E4 trigger = catalyst_type NONE; a non-bearish news read there is
        # "catalyst NONE with no bearish signal" = ABSENCE.
        signals = [_news("BULL", 0.8, "NONE")]
        assert classify_block(signals, catalyst_none_trigger=True)[0] == ABSENCE
        assert absence_fail_open(signals, catalyst_none_trigger=True)
        # ...but at the D204 sites the same read is WEAK_DATA (conservative).
        assert classify_block(signals, catalyst_none_trigger=False)[0] == WEAK_DATA
        assert absence_fail_open(signals, catalyst_none_trigger=False) == ""

    def test_bearish_always_blocks_even_at_e4_site(self):
        for signals in (
            [_news("BEAR", 0.8, "NONE")],
            [_news("STRONG_BEAR", 0.9, "NONE")],
            [_risk("VETO"), _sig("technical_agent", "BULL", 0.9)],
            [_sig("institutional_agent", "STRONG_BEAR", 0.6)],
        ):
            assert absence_fail_open(signals, catalyst_none_trigger=True) == ""
            assert absence_fail_open(signals, catalyst_none_trigger=False) == ""

    def test_timeout_vs_explicit_neutral_both_absence_but_empty_eval_blocks(self):
        # news agent timed out, rest of eval ran -> ABSENCE
        assert classify_block([_sig("technical_agent", "BULL", 0.7)])[0] == ABSENCE
        # whole eval void (no signals at all) -> NOT absence -> block
        assert classify_block([])[0] == NO_SIGNALS
        assert absence_fail_open([]) == ""

    def test_never_raises_on_hostile_input(self):
        class Hostile:
            @property
            def agent_id(self):
                raise RuntimeError("boom")

        for signals in (None, 0, "x", [Hostile()], [{"agent_id": None, "signal": None}],
                        [[]], [{}], object()):
            # must not raise, must return str
            out = absence_fail_open(signals)
            assert isinstance(out, str)

    def test_downgrade_mechanism_doc178_field_parity(self):
        """The fail-open branch reuses the doc-178 mechanism:
        verdict.model_copy(update={'qty_multiplier': min(current, mult)}).
        Verify it works on the real TradeVerdict model and only clamps down."""
        v = _verdict(qty_multiplier=1.0)
        v2 = v.model_copy(update={"qty_multiplier": min(v.qty_multiplier, 0.5)})
        assert v2.qty_multiplier == 0.5
        assert v2.ticker == v.ticker and v2.action == v.action
        # already-downgraded verdict is never bumped back up
        v3 = _verdict(qty_multiplier=0.25)
        v4 = v3.model_copy(update={"qty_multiplier": min(v3.qty_multiplier, 0.5)})
        assert v4.qty_multiplier == 0.25


# ── LEVER 2: DIP_TABLE flatten ──────────────────────────────────────────

ALL_CLASSES = ("MINOR", "SIGNIFICANT", "MAJOR", "EXPLOSIVE")


class TestLever2DipOverride:
    def test_unset_factors_equal_table(self, monkeypatch):
        """No-op pin: env unset -> _dip_factor == DIP_TABLE exactly."""
        monkeypatch.delenv(DIP_OVERRIDE_ENV, raising=False)
        for cls in ALL_CLASSES:
            assert _dip_factor(cls) == DIP_TABLE[cls]
            assert DipEntryCalculator.get_dip_factor(cls) == DIP_TABLE[cls]
        # unknown classification -> legacy 0.02 default
        assert _dip_factor("WEIRD") == 0.02

    def test_override_zero_flattens_all_classes(self, monkeypatch):
        monkeypatch.setenv(DIP_OVERRIDE_ENV, "0.0")
        for cls in ALL_CLASSES:
            assert _dip_factor(cls) == 0.0
            assert DipEntryCalculator.get_dip_factor(cls) == 0.0
        assert _dip_factor("WEIRD") == 0.0

    def test_override_mid_value(self, monkeypatch):
        monkeypatch.setenv(DIP_OVERRIDE_ENV, "0.07")
        for cls in ALL_CLASSES:
            assert _dip_factor(cls) == 0.07

    @pytest.mark.parametrize(
        "garbage",
        ["banana", "", " ", "nan", "inf", "-inf", "-0.05", "1.5", "1.0", "0x10", "None"],
    )
    def test_override_garbage_falls_back_to_table_never_raises(self, monkeypatch, garbage):
        monkeypatch.setenv(DIP_OVERRIDE_ENV, garbage)
        for cls in ALL_CLASSES:
            assert _dip_factor(cls) == DIP_TABLE[cls]

    def test_compute_entry_unset_vs_zero(self, monkeypatch):
        from src.core.models import CandidateStock

        cand = CandidateStock(
            ticker="TEST",
            current_price=10.0,
            previous_close=6.0,
            gap_pct=0.66,
            gap_classification="MAJOR",
            rvol=5.0,
            premarket_volume=500_000,
            scan_timestamp=_NOW,
            scan_phase="PRE_MARKET",
        )
        monkeypatch.delenv(DIP_OVERRIDE_ENV, raising=False)
        entry_legacy, _, _ = DipEntryCalculator.compute_entry(cand)
        assert entry_legacy == round(10.0 * (1 - DIP_TABLE["MAJOR"]), 2)

        monkeypatch.setenv(DIP_OVERRIDE_ENV, "0.0")
        entry_flat, stop, targets = DipEntryCalculator.compute_entry(cand)
        assert entry_flat == 10.0  # decision-price entry, no dip
        assert stop < entry_flat
        assert targets == [round(10.0 * 1.05, 2), round(10.0 * 1.10, 2), round(10.0 * 1.20, 2)]
