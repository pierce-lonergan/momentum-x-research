"""Bug AL tests: CandidateStock float_shares plausibility validator.

Pins today's SCNI scenario as a regression guard:
- SCNI reported float=11.3B, market_cap=$2M, price=$0.40
- Implied float = $2M / $0.40 = 5M shares
- Reported / implied = 2263× — clearly bad data
- D112 router rejected on float > 2B max BEFORE any agent could
  evaluate. SCNI was the highest-RVOL setup of the day (3667.6x).

Aggressive testing per Tier 3 mandate:
  1. Direct unit tests pinning the SCNI case + boundary cases
  2. Property test (Hypothesis) verifying invariants hold for
     ALL float/market_cap/price combinations
  3. Frozen-mutation regression test (the validator MUST work
     with frozen=True Pydantic; this asserts the discipline)
  4. Integration test against the D112 router showing the SCNI
     trade now flows through to MFCS scoring

See docs/research-log/51_bug_al_float_plausibility.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from src.core.models import CandidateStock


def _build_candidate(*, ticker="X", price=10.0, prev=9.0, gap=0.11,
                     rvol=5.0, vol=100_000, float_shares=None,
                     market_cap=None) -> CandidateStock:
    """Test factory — defaults to a generic mid-cap setup."""
    return CandidateStock(
        ticker=ticker,
        current_price=price,
        previous_close=prev,
        gap_pct=gap,
        gap_classification="MAJOR" if gap >= 0.10 else "SIGNIFICANT",
        rvol=rvol,
        premarket_volume=vol,
        float_shares=float_shares,
        market_cap=market_cap,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )


# ── Direct unit tests ──────────────────────────────────────────────


def test_scni_today_is_corrected_to_none(caplog):
    """The canonical regression: SCNI's 11.3B reported float vs 5M
    implied (2263× ratio) → float_shares=None. Cost us today's
    highest-RVOL setup; pinned here so any future drift surfaces."""
    with caplog.at_level(logging.WARNING):
        c = _build_candidate(
            ticker="SCNI", price=0.40, prev=0.265, gap=0.51, rvol=3667.6,
            float_shares=11_313_568_000, market_cap=2_000_000.0,
        )
    assert c.float_shares is None, "SCNI's 11.3B float should be dropped to None"
    assert c.market_cap == 2_000_000.0, "market_cap unchanged"
    assert c.current_price == 0.40, "price unchanged"
    # Operator-visibility check: D272 warning must be in the log
    assert any("D272 FLOAT_IMPLAUSIBLE" in r.message for r in caplog.records), (
        "D272 warning must fire so operator can see + investigate"
    )


def test_aapl_normal_float_unchanged():
    """AAPL: 15.5B float × $200 = $3.1T ≈ market cap.
    Ratio ~1.0 — must not be touched."""
    c = _build_candidate(
        ticker="AAPL", price=200.0, prev=190.0,
        float_shares=15_500_000_000, market_cap=3_100_000_000_000,
    )
    assert c.float_shares == 15_500_000_000


def test_etf_high_ratio_borderline_unchanged():
    """ETFs can have 2-10× ratio because of share-issuance/redemption
    dynamics. SPY: 900M float × $400 = $360B AUM. Market cap reporting
    varies but stays well under 100× ratio."""
    c = _build_candidate(
        ticker="SPY", price=400.0, prev=395.0,
        float_shares=900_000_000, market_cap=180_000_000_000,  # 2× ratio
    )
    assert c.float_shares == 900_000_000  # 2x ratio, unchanged


@pytest.mark.parametrize("fs,mc,px,expected_kept", [
    # (float_shares, market_cap, price, expected: should be kept?)
    (1_000_000, 10_000_000, 10.0, True),  # ratio 1.0 — perfect
    (5_000_000, 10_000_000, 10.0, True),  # ratio 5x — fine
    (50_000_000, 10_000_000, 10.0, True),  # ratio 50x — borderline OK
    (99_000_000, 10_000_000, 10.0, True),  # ratio 99x — just under threshold
    (101_000_000, 10_000_000, 10.0, False),  # ratio 101x — just over
    (11_300_000_000, 2_000_000, 0.40, False),  # SCNI-class
    (1_000_000_000, 1_000_000, 1.0, False),  # 1000× ratio
])
def test_threshold_boundary(fs, mc, px, expected_kept):
    """Pin the 100× threshold at the boundary."""
    c = _build_candidate(price=px, prev=px*0.95, float_shares=fs, market_cap=mc)
    if expected_kept:
        assert c.float_shares == fs, f"ratio {fs/(mc/px):.1f}x should be kept"
    else:
        assert c.float_shares is None, f"ratio {fs/(mc/px):.1f}x should be dropped"


# ── No-op / edge-case tests (must NOT mutate when data missing) ──


@pytest.mark.parametrize("fs,mc,px,description", [
    (None, 1_000_000, 10.0, "missing float — no validator action"),
    (1_000_000, None, 10.0, "missing market_cap — no validator action"),
    (1_000_000, 1_000_000, None, "missing price — no validator action"),
    (None, None, None, "all missing — no validator action"),
    (0, 1_000_000, 10.0, "zero float — skip (avoid div-by-zero)"),
    (1_000_000, 0, 10.0, "zero market_cap — skip"),
    (1_000_000, 1_000_000, 0, "zero price — skip"),
    (-100, 1_000_000, 10.0, "negative float — skip (handled upstream)"),
])
def test_missing_or_invalid_inputs_no_action(fs, mc, px, description):
    """Validator must be a no-op when inputs are missing/zero/negative.
    Skipping price=None is awkward because price is required — use the
    factory with a tiny non-zero price OR skip the test."""
    if px is None or px <= 0:
        pytest.skip(f"{description} — price is required by Pydantic")
    c = _build_candidate(price=px, prev=max(px*0.95, 0.01),
                         float_shares=fs, market_cap=mc)
    assert c.float_shares == fs, f"validator should not touch: {description}"


# ── Property test: validator NEVER modifies legitimate data ──


@hyp_settings(max_examples=500, deadline=None)
@given(
    fs=st.integers(min_value=1, max_value=10_000_000_000),
    mc=st.floats(min_value=1_000_000, max_value=10_000_000_000_000,
                 allow_nan=False, allow_infinity=False),
    px=st.floats(min_value=0.50, max_value=500.0,
                 allow_nan=False, allow_infinity=False),
)
def test_property_only_modifies_when_ratio_exceeds_threshold(fs, mc, px):
    """For ALL combinations: float_shares is preserved IFF
    fs / (mc / px) <= 100. The validator is a pure function of those
    three inputs."""
    c = _build_candidate(price=px, prev=px*0.95,
                         float_shares=fs, market_cap=mc)
    implied = mc / px
    ratio = fs / implied if implied > 0 else 0
    if ratio > 100:
        assert c.float_shares is None, (
            f"ratio={ratio:.1f}x > 100x should drop float, but kept {fs}"
        )
    else:
        assert c.float_shares == fs, (
            f"ratio={ratio:.1f}x <= 100x should keep float, but dropped"
        )


# ── Frozen-mutation regression test ──


def test_frozen_model_still_immutable():
    """Bug AL adds a model_validator. Verify the model is STILL frozen
    after construction — i.e., no one can silently add a setter that
    would mutate float_shares post-validation. This regression-guards
    the entire frozen=True discipline."""
    c = _build_candidate(
        ticker="X", float_shares=1_000_000, market_cap=10_000_000,
    )
    with pytest.raises((AttributeError, TypeError, Exception)) as exc_info:
        c.float_shares = 5_000_000  # type: ignore  # frozen — must raise
    # Pydantic raises ValidationError or AttributeError or TypeError
    # depending on version; any "frozen" or "immutable" message is fine.
    assert any(
        kw in str(exc_info.value).lower()
        for kw in ("frozen", "immutable", "instance is frozen", "validation")
    ), f"Expected frozen-related error, got: {exc_info.value}"


# ── D112 router integration test ──


def test_d112_router_no_longer_rejects_scni_after_validator():
    """The end-to-end win: SCNI today was rejected by D112 with
    `float 11,313,568,000 > 2,000,000,000 max`. With Bug AL, the
    validator drops float to None, and D112 router skips its
    float_max check (per adaptive_router.py:156, the check requires
    `candidate.float_shares is not None`).

    This test exercises the integration WITHOUT spinning up the full
    orchestrator — it directly invokes the AdaptiveRouter."""
    from src.core.adaptive_router import AdaptiveComputeRouter, EvalTier
    from config.settings import RouterConfig
    cfg = RouterConfig(enabled=True)
    router = AdaptiveComputeRouter(config=cfg)

    # Pre-Bug-AL: SCNI was rejected as INSTANT_REJECT here
    scni = _build_candidate(
        ticker="SCNI", price=0.40, prev=0.265, gap=0.51, rvol=3667.6,
        vol=10_000_000,
        float_shares=11_313_568_000, market_cap=2_000_000.0,
    )
    # Bug AL dropped float to None, so router's float_max check skips
    decision = router.classify(candidate=scni, sec_filings=None, news_items=None)
    assert decision.tier != EvalTier.INSTANT_REJECT or "float" not in decision.reason.lower(), (
        f"SCNI must not be float-rejected; got tier={decision.tier} reason={decision.reason}"
    )


def test_d112_router_still_rejects_legitimately_huge_float():
    """The safety contract: a stock with REAL huge float (e.g., a
    legitimate large-cap with a market_cap matching the float) is still
    correctly rejected. The validator is targeted at DATA ERRORS, not
    legit huge-float exclusion."""
    from src.core.adaptive_router import AdaptiveComputeRouter, EvalTier
    from config.settings import RouterConfig
    cfg = RouterConfig(enabled=True)
    router = AdaptiveComputeRouter(config=cfg)

    # Mega-cap with PLAUSIBLE float (matches market cap)
    huge_legit = _build_candidate(
        ticker="MEGA", price=10.0, prev=9.0, gap=0.11, rvol=3.0, vol=500_000,
        float_shares=5_000_000_000,   # 5B shares
        market_cap=50_000_000_000,    # $50B mcap; implied = 5B → ratio 1.0
    )
    assert huge_legit.float_shares == 5_000_000_000, "legit huge float should be preserved"
    decision = router.classify(candidate=huge_legit, sec_filings=None, news_items=None)
    assert decision.tier == EvalTier.INSTANT_REJECT, (
        f"5B legit float > 2B max should still INSTANT_REJECT; "
        f"got tier={decision.tier} reason={decision.reason}"
    )
    assert "float" in decision.reason.lower()
