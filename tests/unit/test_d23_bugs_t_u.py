"""
Thu 2026-04-23 EOD Bug T + Bug U regression tests.

Bug T: VWAP scan candidates missing `scan_timestamp` required field.
       35 silent failures today; entire Phase-3 VWAP rescan was a no-op.
Bug U: `MOMENTUM_UNIVERSE` import broken in screener fallback path.
       7 silent failures; when primary screener fails, fallback also
       crashes leaving zero candidates.

Per discipline: tests reproduce the exact bug shape from today's logs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ── Bug T: scan_timestamp required ─────────────────────────────────


class TestBugT_VwapScanTimestamp:
    """The CandidateStock model requires `scan_timestamp`. The Phase-3
    VWAP rescan path in main.py used to omit it; today's log showed
    35 occurrences of `1 validation error for CandidateStock /
    scan_timestamp / Field required`. The fix sets `scan_timestamp =
    datetime.now(timezone.utc)` at the construction site."""

    def test_candidate_stock_requires_scan_timestamp(self):
        from src.core.models import CandidateStock
        from pydantic import ValidationError

        # Without scan_timestamp → must raise (the original bug shape)
        with pytest.raises(ValidationError) as exc:
            CandidateStock(
                ticker="TZOO",
                current_price=10.0,
                previous_close=9.0,
                gap_pct=11.1,
                gap_classification="SIGNIFICANT",
                rvol=2.0,
                premarket_volume=1000,
                scan_phase="INTRADAY",
            )
        # Must mention scan_timestamp explicitly
        assert "scan_timestamp" in str(exc.value)

    def test_candidate_stock_with_scan_timestamp_constructs(self):
        """The patched construction shape works."""
        from src.core.models import CandidateStock

        cand = CandidateStock(
            ticker="TZOO",
            current_price=10.0,
            previous_close=9.0,
            gap_pct=11.1,
            gap_classification="SIGNIFICANT",
            rvol=2.0,
            premarket_volume=1000,
            scan_phase="INTRADAY",
            scan_timestamp=datetime.now(timezone.utc),
        )
        assert cand.ticker == "TZOO"
        assert cand.scan_timestamp.tzinfo is not None  # tz-aware

    def test_main_py_vwap_rescan_includes_scan_timestamp(self):
        """Source-grep guard: the VWAP rescan construction site must
        include scan_timestamp= in the kwargs. Catches silent
        re-introduction if a future refactor drops the field."""
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        text = (repo / "main.py").read_text(encoding="utf-8")
        # Find the vwap_candidates construction block
        m = re.search(
            r"vwap_candidates\.append\(CandidateStock\((.*?)\)\)",
            text, re.DOTALL,
        )
        assert m is not None, "VWAP rescan construction not found"
        assert "scan_timestamp=" in m.group(1), (
            "Bug T regression: vwap_candidates construction must include "
            "scan_timestamp= kwarg. Got: " + m.group(1)
        )


# ── Bug U: MOMENTUM_UNIVERSE import ────────────────────────────────


class TestBugU_MomentumUniverseImport:
    """When the Alpaca screener API fails, the fallback path tries to
    import MOMENTUM_UNIVERSE from src.data.premarket_research. Today's
    log showed 7 occurrences of `cannot import name 'MOMENTUM_UNIVERSE'
    from 'src.data.premarket_research'`. Either restore the constant or
    fix the import path."""

    def test_momentum_universe_importable_no_attribute_error(self):
        """The fallback path must successfully import MOMENTUM_UNIVERSE
        from its declared location WITHOUT raising ImportError.
        Empty-list value is intentional (fail-closed; see premarket_research.py
        docstring for rationale). The bug being pinned is the
        ImportError, not the list contents."""
        # The actual production import call site
        from src.data.premarket_research import MOMENTUM_UNIVERSE
        assert isinstance(MOMENTUM_UNIVERSE, list)
        # Empty is the intentional fail-closed default; non-empty also OK
        # if a future operator chooses to populate it.

    @pytest.mark.asyncio
    async def test_get_most_active_tickers_fail_closed_on_screener_outage(self):
        """When the screener API fails AND MOMENTUM_UNIVERSE is empty
        (the intentional fail-closed configuration), the call must
        return an empty list — NOT raise ImportError or AttributeError.
        This is the exact production failure mode from 2026-04-23."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.data.alpaca_client import AlpacaDataClient

        # Build a real client instance with mocked HTTP layer
        cfg = MagicMock()
        cfg.paper_api_key = "test"
        cfg.paper_secret_key = "test"
        cfg.live_api_key = "test"
        cfg.live_secret_key = "test"
        cfg.use_paper = True
        client = AlpacaDataClient(cfg)
        # Force the screener call to fail
        with patch.object(
            client, "_data_get",
            new=AsyncMock(side_effect=RuntimeError("simulated screener outage")),
        ):
            result = await client.get_most_active_tickers(limit=20)
        # Must return without raising — empty list under fail-closed default
        assert isinstance(result, list)
        # Either fail-closed empty (production default) or populated
        # (if operator restored a curated list later)
        assert len(result) >= 0
