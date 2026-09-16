"""D219 regression: frozen Pydantic enrichment via model_copy.

The bug (April 16): main.py:1431 had `_ec.float_shares = _cached.get(...)`
on a `CandidateStock` declared `frozen=True`. Pydantic raised
``ValidationError: Instance is frozen`` on every candidate, leaving the
session at 0 evaluations after 4 scans. The fix uses `model_copy(update=...)`
and replaces the watchlist entry by index.

These tests pin the canonical fix in place. Combined with the static-analysis
test in `tests/static_analysis/test_frozen_mutations.py`, they form defense
in depth: even if the AST visitor is bypassed, the behavioral test catches
a regression that breaks the enrichment pattern.

Reference: src/core/models.py:116 (CandidateStock declaration),
            main.py:1424-1471 (D219 enrichment block, post-hotfix af50d34).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.core.models import CandidateStock


def _make_candidate(**overrides) -> CandidateStock:
    """Build a minimal valid CandidateStock for tests."""
    base: dict = dict(
        ticker="TEST",
        company_name="Test Corp",
        current_price=5.0,
        previous_close=4.5,
        gap_pct=0.10,
        gap_classification="MAJOR",  # Literal — matches src/core/models.py
        rvol=2.0,
        premarket_volume=500_000,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )
    base.update(overrides)
    return CandidateStock(**base)


class TestD219FrozenEnrichment:
    """Three test cases pinning the model_copy-based enrichment pattern."""

    def test_direct_attribute_assignment_is_blocked(self) -> None:
        """Setting attrs on frozen CandidateStock must raise.

        This is the regression test: if someone removes ``frozen=True`` from
        CandidateStock, this test fails LOUDLY — preventing a silent return
        to the buggy pattern from main.py:1431 (pre-hotfix).
        """
        c = _make_candidate(float_shares=None, market_cap=None)
        with pytest.raises(ValidationError, match="frozen"):
            c.float_shares = 10_000_000  # type: ignore[misc]

    def test_model_copy_replaces_frozen_field(self) -> None:
        """The canonical D219 fix: model_copy(update=...).

        Original instance is unchanged; copy has the new value; other fields
        are preserved.
        """
        c = _make_candidate(float_shares=None, market_cap=None)
        enriched = c.model_copy(
            update={"float_shares": 8_000_000, "market_cap": 50_000_000.0}
        )
        # Original untouched
        assert c.float_shares is None
        assert c.market_cap is None
        # Copy has new values
        assert enriched.float_shares == 8_000_000
        assert enriched.market_cap == 50_000_000.0
        # Other fields preserved
        assert enriched.ticker == c.ticker
        assert enriched.gap_pct == c.gap_pct
        assert enriched.previous_close == c.previous_close
        # Copy is also frozen
        with pytest.raises(ValidationError, match="frozen"):
            enriched.float_shares = 999  # type: ignore[misc]

    def test_watchlist_in_place_replacement_pattern(self) -> None:
        """Mirror the main.py:1430-1466 enrichment loop.

        This is the EXACT pattern in production after the hotfix.  If a
        future refactor breaks model_copy support, this test catches it.
        """
        watchlist = [_make_candidate(ticker=t, current_price=p) for t, p in
                     (("AAA", 5.0), ("BBB", 10.0), ("CCC", 15.0))]

        # Simulate the enrichment loop from main.py:1428
        enrichments = {
            "AAA": {"float_shares": 1_000_000, "market_cap": 5_000_000.0},
            "BBB": {"float_shares": 2_500_000, "market_cap": 25_000_000.0},
            "CCC": {"float_shares": None, "market_cap": 150_000_000.0},  # partial
        }
        for idx, ec in enumerate(watchlist):
            enrich = enrichments[ec.ticker]
            update = {k: v for k, v in enrich.items() if v is not None}
            if update:
                watchlist[idx] = ec.model_copy(update=update)

        # All entries enriched correctly
        assert watchlist[0].float_shares == 1_000_000
        assert watchlist[0].market_cap == 5_000_000.0
        assert watchlist[1].float_shares == 2_500_000
        assert watchlist[1].market_cap == 25_000_000.0
        # Partial: float_shares stays None, market_cap updated
        assert watchlist[2].float_shares is None
        assert watchlist[2].market_cap == 150_000_000.0
        # Order preserved
        assert [c.ticker for c in watchlist] == ["AAA", "BBB", "CCC"]
        # Other fields preserved per-row
        assert watchlist[0].current_price == 5.0
        assert watchlist[1].current_price == 10.0
        assert watchlist[2].current_price == 15.0

    def test_model_copy_is_idempotent_for_cache_hits(self) -> None:
        """The enrichment loop checks a session-scoped cache. If a candidate
        appears in successive pre-market scans, the same enrichment is reapplied.
        Verify model_copy is safe to call repeatedly with the same update."""
        c = _make_candidate(float_shares=None, market_cap=None)
        update = {"float_shares": 7_500_000, "market_cap": 60_000_000.0}
        c1 = c.model_copy(update=update)
        c2 = c1.model_copy(update=update)
        c3 = c2.model_copy(update=update)
        for x in (c1, c2, c3):
            assert x.float_shares == 7_500_000
            assert x.market_cap == 60_000_000.0
            assert x.ticker == "TEST"

    def test_empty_update_is_safe(self) -> None:
        """If Finnhub returns no data, the enrichment loop skips model_copy.
        But if it ever passes an empty dict, model_copy must still produce a
        valid (and equal) instance."""
        c = _make_candidate(float_shares=5_000_000, market_cap=25_000_000.0)
        c2 = c.model_copy(update={})
        assert c2.float_shares == c.float_shares
        assert c2.market_cap == c.market_cap
        assert c2.ticker == c.ticker
        # Different instance
        assert c2 is not c
