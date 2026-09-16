"""
MOMENTUM-X Tests: Technical Agent (Deterministic)

Node ID: tests.unit.test_technical_agent
Graph Link: tested_by -> agent.technical

Tests verify the PROMPT_SIGNATURES invariants on the DeterministicTechnicalAgent
that has been live in production since D101. The LLM-based TechnicalAgent was
removed in D221 Phase F (2026-04-19) along with its parse_response tests.

The invariants under test (from deterministic_technical.py module docstring):
  1. Pattern without RVOL > 2.0 confirmation caps at NEUTRAL.
  2. STRONG_BULL requires breakout_confirmed + RVOL > 3.0 + VWAP above.
  3. Daily-timeframe patterns without intraday confirmation cap at BULL.
  4. RVOL > 5.0 + RSI(9) > 75 = EXHAUSTION RISK -- cap at BULL max.
  5. Never fails -- guaranteed TechnicalSignal output on every call.

Other coverage of DeterministicTechnicalAgent lives in:
  - test_d104_atr_resilience.py (ATR computation under data gaps)
  - test_d106_immediate_fixes.py (D106-specific fixes)
  - test_d126_gap_momentum.py (D126 gap-momentum override behavior)
"""

from __future__ import annotations

import pytest

from src.agents.deterministic_technical import (
    DeterministicTechnicalAgent,
    _TA_BACKEND,
    _TA_BACKEND_NAME,
    _compute_indicators,
)
from src.core.models import TechnicalSignal


def _bars_uptrending(n: int = 30, start: float = 5.00, step: float = 0.05) -> list[dict]:
    """Generate n synthetic uptrending OHLCV bars."""
    bars = []
    price = start
    for i in range(n):
        bars.append({
            "timestamp": f"2026-04-17T13:{30 + i:02d}:00Z",
            "o": price, "h": price + step, "l": price - step / 2,
            "c": price + step, "v": 100_000 + i * 1_000,
        })
        price += step
    return bars


def _bars_flat(n: int = 30, level: float = 5.00) -> list[dict]:
    """Generate n synthetic flat OHLCV bars (no breakout possible)."""
    return [
        {
            "timestamp": f"2026-04-17T13:{30 + i:02d}:00Z",
            "o": level, "h": level + 0.01, "l": level - 0.01,
            "c": level, "v": 50_000,
        }
        for i in range(n)
    ]


@pytest.fixture
def agent() -> DeterministicTechnicalAgent:
    return DeterministicTechnicalAgent()


class TestDeterministicTechnicalSignalInvariants:
    """The deterministic agent must enforce the same PROMPT_SIGNATURES
    invariants the LLM agent did. Behavior tests on real analyze() calls."""

    @pytest.mark.asyncio
    async def test_low_rvol_does_not_emit_strong_bull(self, agent):
        """INV 1: Pattern without RVOL >= 2.0 confirmation must NOT emit
        STRONG_BULL even on an otherwise-strong setup."""
        result = await agent.analyze(
            ticker="LOWVOL",
            current_price=5.50,
            rvol=1.5,  # below 2.0 threshold
            gap_pct=0.10,
            vwap=5.20,
            price_data={"5min": _bars_uptrending()},
            indicators={},
        )
        assert isinstance(result, TechnicalSignal)
        assert result.signal != "STRONG_BULL", (
            f"low-RVOL setup must not produce STRONG_BULL; got {result.signal}"
        )

    @pytest.mark.asyncio
    async def test_strong_bull_requires_high_rvol(self, agent):
        """INV 2: STRONG_BULL requires breakout_confirmed + RVOL > 3.0 + VWAP
        above. With RVOL=2.5 (above 2.0 floor but below 3.0 threshold),
        STRONG_BULL must not fire."""
        result = await agent.analyze(
            ticker="MIDVOL",
            current_price=5.50,
            rvol=2.5,  # above 2.0 floor, below 3.0 STRONG_BULL threshold
            gap_pct=0.10,
            vwap=5.20,  # price above vwap
            price_data={"5min": _bars_uptrending()},
            indicators={},
        )
        assert result.signal != "STRONG_BULL"

    @pytest.mark.asyncio
    async def test_strong_bull_requires_vwap_above(self, agent):
        """INV 2: Even with high RVOL + breakout, STRONG_BULL needs VWAP
        above. Below VWAP must cap signal."""
        result = await agent.analyze(
            ticker="BELOWVWAP",
            current_price=5.00,  # BELOW vwap
            rvol=4.0,  # high
            gap_pct=0.10,
            vwap=5.50,  # price below vwap
            price_data={"5min": _bars_uptrending()},
            indicators={},
        )
        assert result.signal != "STRONG_BULL"
        assert result.vwap_above is False

    @pytest.mark.asyncio
    async def test_never_fails_on_empty_inputs(self, agent):
        """INV 5: Never fails -- guaranteed TechnicalSignal even with
        completely empty inputs."""
        result = await agent.analyze(ticker="EMPTY")
        assert isinstance(result, TechnicalSignal)
        assert result.ticker == "EMPTY"
        # Signal must be a valid value, not None
        assert result.signal in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR")

    @pytest.mark.asyncio
    async def test_never_fails_on_malformed_bars(self, agent):
        """INV 5: Even with malformed/incomplete bar dicts, returns a
        valid TechnicalSignal (does not raise)."""
        bad_bars = [
            {"o": "not a number", "h": None, "l": -1, "c": float("nan"), "v": "x"},
            {},  # empty bar
            {"price": 5.0},  # wrong keys
        ]
        result = await agent.analyze(
            ticker="MALFORMED",
            current_price=5.0,
            rvol=2.0,
            vwap=5.0,
            price_data={"5min": bad_bars},
        )
        assert isinstance(result, TechnicalSignal)
        assert result.ticker == "MALFORMED"


class TestDeterministicTechnicalBugSweep:
    """D221 Phase F Sunday adversarial sweep. Mirrors the risk-agent sweep
    that found 5 silent-failure surfaces; applies the same lens to the
    technical agent.
    """

    @pytest.mark.asyncio
    async def test_price_data_is_none_does_not_crash(self, agent):
        """kwargs.get('price_data', {}) returns None when the caller passes
        price_data=None explicitly -- the default only applies when the key
        is missing. Downstream `if tf in price_data` crashes on None."""
        result = await agent.analyze(
            ticker="NONEPD",
            current_price=5.0,
            rvol=2.5,
            vwap=5.0,
            price_data=None,  # crashes pre-fix at `tf in price_data`
        )
        assert isinstance(result, TechnicalSignal)

    @pytest.mark.asyncio
    async def test_indicators_is_none_does_not_crash(self, agent):
        """Same None-bypass bug for indicators kwarg."""
        result = await agent.analyze(
            ticker="NONEIND",
            current_price=5.0,
            rvol=2.5,
            vwap=5.0,
            price_data={},
            indicators=None,
        )
        assert isinstance(result, TechnicalSignal)

    @pytest.mark.asyncio
    async def test_indicators_as_list_does_not_crash(self, agent):
        """Wrong-type indicators (list where dict expected) -- .items() crashes."""
        result = await agent.analyze(
            ticker="LISTIND",
            current_price=5.0,
            rvol=2.5,
            vwap=5.0,
            price_data={},
            indicators=[{"bad": "shape"}],
        )
        assert isinstance(result, TechnicalSignal)

    @pytest.mark.asyncio
    async def test_price_data_as_string_does_not_crash(self, agent):
        """Wrong-type price_data (string) -- `in` check is character-level, then
        price_data[tf] crashes."""
        result = await agent.analyze(
            ticker="STRPD",
            current_price=5.0,
            rvol=2.5,
            vwap=5.0,
            price_data="not a dict",
            indicators={},
        )
        assert isinstance(result, TechnicalSignal)

    @pytest.mark.asyncio
    async def test_current_price_as_string_does_not_crash(self, agent):
        """kwargs.get('current_price', 0.0) goes through _safe_float so already
        handled -- but verify explicitly to lock in the invariant."""
        result = await agent.analyze(
            ticker="STRCP",
            current_price="not a number",
            rvol=2.5,
            vwap=5.0,
        )
        assert isinstance(result, TechnicalSignal)


class TestDeterministicTechnicalSignalShape:
    """The TechnicalSignal output must be structurally complete regardless
    of input quality -- downstream consumers depend on the schema."""

    @pytest.mark.asyncio
    async def test_signal_has_all_required_fields(self, agent):
        """All TechnicalSignal fields populated to non-None where the
        contract requires it."""
        result = await agent.analyze(
            ticker="SHAPE",
            current_price=5.0,
            rvol=2.5,
            vwap=4.95,
            price_data={"5min": _bars_uptrending()},
        )
        assert result.agent_id == "technical_agent"
        assert result.ticker == "SHAPE"
        assert result.timestamp is not None
        assert result.signal in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR")
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.flags, list)
        # Optional fields can be None but should not raise on attribute access
        _ = result.pattern_identified
        _ = result.breakout_confirmed
        _ = result.breakout_rvol
        _ = result.vwap_above
        _ = result.projected_target
        _ = result.stop_loss_level

    @pytest.mark.asyncio
    async def test_agent_id_matches_journal_compatibility(self, agent):
        """The deterministic agent reports agent_id='technical_agent' for
        journal schema compatibility -- 2,397+ records on disk reference
        this string and we must not break them."""
        assert agent.agent_id == "technical_agent"


# ────────────────────────────────────────────────────────────────────────
# Mon 2026-04-20 Bug #1: pandas_ta dormancy
#
# Root cause: pandas_ta 0.3.14b0 imports `from numpy import NaN`. NumPy 2.0
# removed that alias (renamed to `nan`). The original
# `_compute_indicators` swallowed the ImportError silently and returned
# {} — so RSI, MACD, BB, EMA, ATR were all dormant for an unknown number
# of trading sessions. Every existing test in this file STILL PASSED
# during the dormancy because they assert on the output `signal` and
# defaults push the agent toward NEUTRAL when indicators are missing.
#
# Hardened bug-sweep rule (e), added Mon 2026-04-20:
#   "Every fail-safe catch block ships with a test that asserts the
#    output is non-empty under normal conditions."
#
# This test class is the floor for that rule. If we ever silently lose
# the indicator backend again (numpy 3.0, library deprecation, conda
# environment drift), these four tests fail loudly at CI time.
# ────────────────────────────────────────────────────────────────────────


class TestIndicatorsActuallyPopulated:
    """Adversarial population check — proves the indicator backend
    actually produces data, not just an empty dict the silent
    fail-safe returns. Catches the Mon 2026-04-20 dormancy class."""

    def test_module_load_resolves_ta_backend(self):
        """The module-level backend probe MUST resolve to a real library.
        If this fails, the indicator pipeline is dead — install
        pandas-ta-classic per requirements.txt."""
        assert _TA_BACKEND is not None, (
            f"No TA backend loaded (_TA_BACKEND_NAME={_TA_BACKEND_NAME!r}). "
            "Install pandas-ta-classic — see requirements.txt for context."
        )
        assert _TA_BACKEND_NAME in ("pandas_ta_classic", "pandas_ta"), (
            f"Unknown backend: {_TA_BACKEND_NAME!r}"
        )

    def test_compute_indicators_returns_nonempty_for_known_good_bars(self):
        """30 well-formed uptrending bars MUST produce a populated
        indicator dict. The dormancy bug caused this to return {}.

        This is rule (e) of the hardened bug-sweep template applied
        directly to _compute_indicators — the catch block in that
        function returns {} on ImportError, so we need a positive-case
        test that the non-error path actually emits data."""
        bars = _bars_uptrending(n=30)
        # Convert from "o/h/l/c/v" agent-format to the same shape
        # _compute_indicators consumes (it normalizes column names).
        indicators = _compute_indicators(bars)

        assert indicators, (
            f"_compute_indicators returned empty dict for 30 known-good "
            f"uptrending bars — the silent ImportError fail-safe likely "
            f"fired. Backend: {_TA_BACKEND_NAME!r}."
        )

        # All five canonical indicator families must appear. Each maps
        # to a different pandas_ta_classic call site, so this also
        # catches partial-API-break scenarios (e.g. one indicator
        # function disappeared in a future fork).
        required = {
            "rsi_9": "RSI(9) — small-cap tuned momentum",
            "macd_line": "MACD(5,13,4) — fast crossover",
            "macd_signal": "MACD signal line",
            "macd_histogram": "MACD histogram",
            "bb_upper": "Bollinger upper band",
            "bb_lower": "Bollinger lower band",
            "ema_9": "EMA(9) short trend",
            "ema_21": "EMA(21) medium trend",
            "atr_14": "ATR(14) for stop calculation",
        }
        missing = [k for k in required if k not in indicators]
        assert not missing, (
            f"Indicator dict missing keys {missing}. "
            f"Backend: {_TA_BACKEND_NAME!r}. "
            f"Got keys: {sorted(indicators.keys())}"
        )

    def test_compute_indicators_values_are_finite(self):
        """Every returned indicator must be a finite float (not NaN,
        not inf). The dormancy bug returned {} which trivially passes
        any value-shape check; a partial-population bug (one indicator
        crashes mid-compute and leaves NaN) would slip past the
        keys-present test above."""
        import math
        bars = _bars_uptrending(n=30)
        indicators = _compute_indicators(bars)
        for key, val in indicators.items():
            assert isinstance(val, (int, float)), (
                f"{key}={val!r} is not numeric (type={type(val).__name__})"
            )
            assert math.isfinite(val), (
                f"{key}={val} is not finite (NaN or inf). "
                f"Backend: {_TA_BACKEND_NAME!r}"
            )

    @pytest.mark.asyncio
    async def test_strong_setup_does_not_default_to_neutral(self, agent):
        """End-to-end: a setup that argues unambiguously for BULL must
        produce a non-NEUTRAL signal. The dormancy bug zeroed every
        indicator factor, collapsing the factor-balance score to 0
        and forcing every result to NEUTRAL with confidence=0.35.

        Inputs chosen to satisfy both normal and gap-momentum branches:
          - 30 uptrending bars → RSI rises, MACD crosses, EMA(9)>EMA(21)
          - rvol=4.0           → above all thresholds
          - vwap_above=True    → adds bullish factor
          - gap_pct=0.0        → forces normal-mode path (no D126 override)

        With indicators populated, net_bull >= 2 → BULL or STRONG_BULL.
        With indicators dormant, net_bull == 0 → NEUTRAL."""
        result = await agent.analyze(
            ticker="STRONGSETUP",
            current_price=6.50,           # well above the 5.00 start
            rvol=4.0,                     # above STRONG_BULL threshold
            gap_pct=0.0,                  # force normal mode (skip D126)
            vwap=5.50,                    # price above VWAP
            price_data={"5min": _bars_uptrending(n=30)},
            indicators={},
        )
        assert isinstance(result, TechnicalSignal)
        assert result.signal in ("BULL", "STRONG_BULL"), (
            f"Strong-setup analyze() returned {result.signal!r} "
            f"(conf={result.confidence:.2f}). This is the symptom of "
            f"_compute_indicators returning {{}} — every factor zeroes "
            f"and net_bull collapses to 0 (NEUTRAL). Backend: "
            f"{_TA_BACKEND_NAME!r}. Reasoning: {result.reasoning}"
        )
