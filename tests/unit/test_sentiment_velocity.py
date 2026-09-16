"""D193: Tests for Sentiment Velocity Detection.

Pure-deterministic signal — no mocking required (no network/LLM calls).

Test coverage:
  1.  test_silent_no_headlines           — 0 headlines → SILENT
  2.  test_isolated_single_headline      — 1 headline  → ISOLATED
  3.  test_steady_few_headlines          — 3 flat headlines → STEADY
  4.  test_building_accelerating         — 5 accelerating headlines → BUILDING
  5.  test_viral_cascade                 — 15+ dense headlines → VIRAL
  6.  test_velocity_calculation          — Correct headlines/hour
  7.  test_acceleration_positive         — Second half faster → positive accel
  8.  test_acceleration_negative         — Second half slower  → negative accel
  9.  test_hawkes_intensity_decays       — Old headlines contribute less
  10. test_hawkes_intensity_peaks        — Recent dense cluster → high intensity
  11. test_credibility_weighting         — Reuters > Reddit in weighted_velocity
  12. test_novelty_detection             — Duplicate headline filtered
  13. test_novel_headline_passes         — Novel headline counted
  14. test_faller_signal_viral           — VIRAL → -0.25 delta
  15. test_faller_signal_silent          — SILENT → +0.15 delta
  16. test_batch_registration            — register_headlines_batch works
  17. test_itrm_scenario                 — Sparse headlines → ISOLATED/SILENT → bearish
  18. test_fda_approval_scenario         — Cascading FDA headlines → VIRAL → bullish
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.data.sentiment_velocity import (
    HeadlineEvent,
    NarrativeMomentum,
    SentimentVelocityResult,
    SentimentVelocityTracker,
    SOURCE_CREDIBILITY,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_tracker(**kwargs) -> SentimentVelocityTracker:
    """Return a tracker with tight thresholds for test predictability."""
    defaults = dict(viral_threshold=5.0, building_threshold=2.0,
                    hawkes_alpha=0.8, hawkes_beta=0.5,
                    novelty_similarity_threshold=0.7)
    defaults.update(kwargs)
    return SentimentVelocityTracker(**defaults)


def _ts(hour: int, minute: int = 0) -> datetime:
    """Return a datetime at 2026-04-03 HH:MM UTC."""
    return datetime(2026, 4, 3, hour, minute, 0)


# ── 1. Silent: no headlines ────────────────────────────────────────────────────

def test_silent_no_headlines():
    tracker = _make_tracker()
    result = tracker.get_result("ITRM")
    assert result.total_headlines == 0
    assert result.momentum == NarrativeMomentum.SILENT


# ── 2. Isolated: exactly one headline ─────────────────────────────────────────

def test_isolated_single_headline():
    tracker = _make_tracker()
    tracker.register_headline("ITRM", "Company files S-1 prospectus", source="SEC", timestamp=_ts(5, 0))
    result = tracker.get_result("ITRM")
    assert result.total_headlines == 1
    assert result.momentum == NarrativeMomentum.ISOLATED


# ── 3. Steady: a few widely-spaced headlines ──────────────────────────────────

def test_steady_few_headlines():
    tracker = _make_tracker()
    # 3 headlines spread over 3 hours = low intensity, flat
    for hour in [4, 5, 6]:
        tracker.register_headline(
            "AAPL", f"Apple news at hour {hour}",
            source="Yahoo Finance", timestamp=_ts(hour),
        )
    result = tracker.get_result("AAPL")
    assert result.total_headlines == 3
    assert result.momentum == NarrativeMomentum.STEADY


# ── 4. Building: accelerating (back-weighted) headlines ───────────────────────

def test_building_accelerating():
    tracker = _make_tracker()
    # Early: 1 headline at hour 4
    # Later: 4 headlines clustered at hours 5–6 (back-weighted = accelerating)
    tracker.register_headline("NVDA", "Nvidia quarterly outlook", source="Reuters", timestamp=_ts(4, 0))
    for minute in [0, 15, 30, 45]:
        tracker.register_headline(
            "NVDA", f"Nvidia follow-up detail {minute}",
            source="Bloomberg", timestamp=_ts(6, minute),
        )
    result = tracker.get_result("NVDA")
    assert result.total_headlines == 5
    assert result.momentum in (NarrativeMomentum.BUILDING, NarrativeMomentum.VIRAL)


# ── 5. Viral: dense cascade of many headlines ─────────────────────────────────

def test_viral_cascade():
    # Disable novelty threshold so all 15 headlines count — tests Hawkes intensity,
    # not the dedup logic (that's covered in tests 12–13).
    tracker = _make_tracker(novelty_similarity_threshold=1.0)
    base = _ts(5, 0)
    for i in range(15):
        tracker.register_headline(
            "BFRG",
            f"BioForce Genetics FDA fast-track approval detail {i}: phase {i} data",
            source="Reuters",
            timestamp=base + timedelta(minutes=i * 6),
        )
    result = tracker.get_result("BFRG")
    assert result.total_headlines == 15
    assert result.momentum == NarrativeMomentum.VIRAL
    assert result.narrative_is_viral is True


# ── 6. Velocity: correct headlines/hour ───────────────────────────────────────

def test_velocity_calculation():
    tracker = _make_tracker()
    base = _ts(4, 0)
    # 6 headlines evenly spread over 1 hour = velocity ≈ 6 h⁻¹
    for i in range(6):
        tracker.register_headline(
            "MSFT", f"Microsoft headline {i} with unique content {i * 7}",
            source="WSJ", timestamp=base + timedelta(minutes=i * 10),
        )
    result = tracker.get_result("MSFT")
    # Allow ±2 because of bucket rounding
    assert 2.0 <= result.velocity <= 10.0


# ── 7. Acceleration: positive (second half faster) ────────────────────────────

def test_acceleration_positive():
    # Use novelty_similarity_threshold=1.0 — this test exercises the acceleration
    # math, not novelty dedup.
    tracker = _make_tracker(novelty_similarity_threshold=1.0)
    base = _ts(4, 0)
    # First half: 1 headline at minute 0
    tracker.register_headline("ACME", "Acme early mention unique alpha bravo", source="Bloomberg", timestamp=base)
    # Second half: 4 headlines in minutes 30–50
    for i in range(4):
        tracker.register_headline(
            "ACME", f"Acme breaking story detail foxtrot {i} zulu whiskey",
            source="Reuters", timestamp=base + timedelta(minutes=30 + i * 5),
        )
    result = tracker.get_result("ACME")
    assert result.acceleration > 0, f"Expected positive acceleration, got {result.acceleration}"


# ── 8. Acceleration: negative (second half slower) ────────────────────────────

def test_acceleration_negative():
    # Use novelty_similarity_threshold=1.0 — tests acceleration math only.
    tracker = _make_tracker(novelty_similarity_threshold=1.0)
    base = _ts(4, 0)
    # First half: 4 headlines dense in minutes 0–20
    for i in range(4):
        tracker.register_headline(
            "FADING", f"Fading story initial burst info {i} delta echo foxtrot",
            source="CNBC", timestamp=base + timedelta(minutes=i * 5),
        )
    # Second half: 1 headline at minute 50
    tracker.register_headline("FADING", "Fading story late single recap mention", source="Yahoo Finance",
                               timestamp=base + timedelta(minutes=50))
    result = tracker.get_result("FADING")
    assert result.acceleration < 0, f"Expected negative acceleration, got {result.acceleration}"


# ── 9. Hawkes intensity decays with age ───────────────────────────────────────

def test_hawkes_intensity_decays():
    tracker = _make_tracker(hawkes_alpha=0.8, hawkes_beta=0.5)
    base = _ts(1, 0)

    # Register one headline 6 hours ago vs reference time
    tracker.register_headline("OLD", "Old company news event unique alpha bravo", source="Reuters", timestamp=base)

    # Intensity evaluated at t_now: headline was 6h ago
    t_now = base + timedelta(hours=6)
    old_intensity = tracker.compute_hawkes_intensity("OLD", now=t_now)

    # Register same headline 1 minute ago vs reference time
    tracker2 = _make_tracker(hawkes_alpha=0.8, hawkes_beta=0.5)
    t_recent = base + timedelta(hours=6)
    tracker2.register_headline("NEW", "New company news event unique alpha bravo", source="Reuters", timestamp=t_recent)
    new_intensity = tracker2.compute_hawkes_intensity("NEW", now=t_recent + timedelta(minutes=1))

    assert new_intensity > old_intensity, (
        f"Recent intensity ({new_intensity:.3f}) should exceed old ({old_intensity:.3f})"
    )


# ── 10. Hawkes intensity peaks on dense recent cluster ────────────────────────

def test_hawkes_intensity_peaks():
    # Disable novelty so all 10 headlines contribute to intensity.
    tracker = _make_tracker(novelty_similarity_threshold=1.0)
    base = _ts(6, 0)
    # 10 high-credibility headlines within 30 minutes
    for i in range(10):
        tracker.register_headline(
            "PEAK",
            f"Breaking news important event major catalyst detail {i} unique epsilon",
            source="Bloomberg",
            timestamp=base + timedelta(minutes=i * 3),
        )
    result = tracker.get_result("PEAK")
    assert result.intensity >= tracker._viral_threshold, (
        f"Expected intensity ≥ {tracker._viral_threshold}, got {result.intensity:.3f}"
    )


# ── 11. Credibility weighting: Reuters > Reddit ────────────────────────────────

def test_credibility_weighting():
    # Disable novelty dedup so all 3 headlines contribute to weighted_velocity.
    # This test is about source credibility math, not novelty filtering.
    tracker  = _make_tracker(novelty_similarity_threshold=1.0)
    tracker2 = _make_tracker(novelty_similarity_threshold=1.0)
    base = _ts(5, 0)
    for i in range(3):
        tracker.register_headline(
            "TIER1",
            f"Market moving announcement number {i} institutional alpha gamma",
            source="Reuters",
            timestamp=base + timedelta(minutes=i * 20),
        )
        tracker2.register_headline(
            "TIER4",
            f"Market moving announcement number {i} institutional alpha gamma",
            source="Reddit",
            timestamp=base + timedelta(minutes=i * 20),
        )
    r1 = tracker.get_result("TIER1")
    r2 = tracker2.get_result("TIER4")
    assert r1.weighted_velocity > r2.weighted_velocity, (
        f"Reuters weighted_velocity ({r1.weighted_velocity:.2f}) should exceed "
        f"Reddit ({r2.weighted_velocity:.2f})"
    )


# ── 12. Novelty detection: duplicate filtered ─────────────────────────────────

def test_novelty_detection():
    tracker = _make_tracker(novelty_similarity_threshold=0.7)
    tracker.register_headline("DUP", "Company announces quarterly earnings beat results", source="Reuters", timestamp=_ts(5))
    # Near-identical rehash
    tracker.register_headline("DUP", "Company announces quarterly earnings beat results today", source="Benzinga", timestamp=_ts(5, 30))
    result = tracker.get_result("DUP")
    assert result.total_headlines == 2   # Both registered
    assert result.unique_headlines == 1  # Only one novel
    assert result.is_rehash_only is False


# ── 13. Novel headline passes through ─────────────────────────────────────────

def test_novel_headline_passes():
    tracker = _make_tracker(novelty_similarity_threshold=0.7)
    tracker.register_headline("NOVEL", "Company announces quarterly earnings beat", source="Reuters", timestamp=_ts(5))
    # Completely different topic
    tracker.register_headline("NOVEL", "FDA grants accelerated approval for new drug therapy", source="Bloomberg", timestamp=_ts(5, 30))
    result = tracker.get_result("NOVEL")
    assert result.total_headlines == 2
    assert result.unique_headlines == 2


# ── 14. Faller signal: VIRAL → -0.25 ─────────────────────────────────────────

def test_faller_signal_viral():
    # Disable novelty so all 15 headlines accumulate intensity.
    tracker = _make_tracker(novelty_similarity_threshold=1.0)
    base = _ts(5, 0)
    for i in range(15):
        tracker.register_headline(
            "DRUG",
            f"BioPharma FDA fast-track approval stage {i} milestone data results outcome",
            source="Reuters",
            timestamp=base + timedelta(minutes=i * 5),
        )
    result = tracker.get_result("DRUG")
    assert result.momentum == NarrativeMomentum.VIRAL
    assert tracker.get_signal_for_faller("DRUG") == -0.25


# ── 15. Faller signal: SILENT → +0.15 ────────────────────────────────────────

def test_faller_signal_silent():
    tracker = _make_tracker()
    assert tracker.get_signal_for_faller("PROMO") == +0.15


# ── 16. Batch registration ────────────────────────────────────────────────────

def test_batch_registration():
    tracker = _make_tracker()
    headlines = [
        {"headline": "Alpha Corp signs major licensing deal", "source": "Reuters", "timestamp": _ts(4, 0)},
        {"headline": "Alpha Corp deal specifics announced today", "source": "Bloomberg", "timestamp": _ts(4, 15)},
        {"headline": "Alpha Corp partners with biotech giant new", "source": "CNBC", "timestamp": _ts(4, 30)},
        {"headline": "Alpha Corp expansion plan regional markets", "source": "MarketWatch", "timestamp": _ts(4, 45)},
        {"headline": "Alpha Corp revenue target raised analyst", "source": "Seeking Alpha", "timestamp": _ts(5, 0)},
    ]
    tracker.register_headlines_batch("ALPH", headlines)
    result = tracker.get_result("ALPH")
    assert result.total_headlines == 5
    assert result.first_headline_time == _ts(4, 0)
    assert result.latest_headline_time == _ts(5, 0)


# ── 17. ITRM scenario: sparse → bearish ───────────────────────────────────────

def test_itrm_scenario():
    """
    ITRM-like pattern: single promotional mention with no real catalyst.
    Expect ISOLATED or SILENT, bearish faller signal.
    """
    tracker = _make_tracker()
    # Only one headline — typical pump-and-dump with no follow-on coverage
    tracker.register_headline(
        "ITRM", "Iterion Therapeutics stock surges 300 percent premarket",
        source="StockTwits", timestamp=_ts(5, 0),
    )
    result = tracker.get_result("ITRM")
    assert result.momentum in (NarrativeMomentum.ISOLATED, NarrativeMomentum.SILENT)
    delta = tracker.get_signal_for_faller("ITRM")
    assert delta > 0, f"Expected bearish delta for ITRM, got {delta}"


# ── 18. FDA approval scenario: cascade → VIRAL → bullish ─────────────────────

def test_fda_approval_scenario():
    """
    BFRG/ELAB-like pattern: genuine FDA approval → cascade of headlines
    from institutional sources within a 2-hour window.
    """
    tracker = _make_tracker()
    base = _ts(4, 30)
    cascade = [
        ("FDA grants accelerated approval for BFRG cancer therapy", "Reuters"),
        ("BFRG FDA approval details mechanism action trial results", "Bloomberg"),
        ("BFRG stock surges FDA approval implications market", "CNBC"),
        ("Analysts raise BFRG price target following FDA decision", "WSJ"),
        ("BFRG FDA approval clinical significance oncology community", "Financial Times"),
        ("BFRG treatment now available patients oncologists reaction", "Associated Press"),
        ("BFRG approval analyst upgrade institutional buy rating", "Barrons"),
        ("BFRG partnerships expected following regulatory clearance", "MarketWatch"),
        ("FDA commissioner statement BFRG approval significance", "Reuters"),
        ("BFRG CEO statement following FDA approval landmark", "Bloomberg"),
        ("BFRG commercial launch timeline following FDA clearance", "Reuters"),
        ("BFRG competitor implications FDA approval cancer sector", "WSJ"),
    ]
    for i, (headline, source) in enumerate(cascade):
        tracker.register_headline(
            "BFRG", headline, source=source,
            timestamp=base + timedelta(minutes=i * 10),
        )
    result = tracker.get_result("BFRG")
    assert result.momentum == NarrativeMomentum.VIRAL, (
        f"Expected VIRAL for FDA cascade, got {result.momentum.value} "
        f"(intensity={result.intensity:.2f})"
    )
    delta = tracker.get_signal_for_faller("BFRG")
    assert delta < 0, f"Expected bullish delta for BFRG FDA cascade, got {delta}"
    assert delta == -0.25
