"""
D105: EMC Scanner Filter Enhancements — Tests

Tests for the tiered price floor, absolute volume RVOL override,
raised price ceiling, and enhanced rejection logging introduced in D105.

Background: On March 13, 2026, the system missed ALL top 6 movers
(BIAF +114%, AIFF +44%, ISPC +59%) because they were sub-$3 stocks
blocked by the price_min=$3.00 filter despite having massive volume
and tight spreads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import polars as pl
import pytest

from config.settings import ScannerThresholds
from src.scanners.premarket import scan_premarket_gappers


# ── Shared helpers ──────────────────────────────────────────────────


def _make_quotes_df(rows: list[dict]) -> pl.DataFrame:
    """Build test DataFrames with prev_volume for dollar_vol calculation."""
    schema = {
        "ticker": pl.Utf8,
        "current_price": pl.Float64,
        "previous_close": pl.Float64,
        "premarket_volume": pl.Int64,
        "avg_volume_at_time": pl.Float64,
        "float_shares": pl.Int64,
        "market_cap": pl.Float64,
        "has_news": pl.Boolean,
        "prev_volume": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def _stock(
    ticker: str = "TEST",
    price: float = 5.0,
    prev_close: float = 4.0,
    premarket_volume: int = 200_000,
    avg_volume_at_time: float = 50_000.0,
    prev_volume: int = 5_000_000,
    has_news: bool = True,
) -> dict:
    """Shorthand for creating a stock row with sensible defaults.

    Defaults produce: gap=25%, rvol=4.0x, dollar_vol=price*prev_volume.
    """
    return {
        "ticker": ticker,
        "current_price": price,
        "previous_close": prev_close,
        "premarket_volume": premarket_volume,
        "avg_volume_at_time": avg_volume_at_time,
        "float_shares": 5_000_000,
        "market_cap": 50_000_000.0,
        "has_news": has_news,
        "prev_volume": prev_volume,
    }


@pytest.fixture
def thresholds() -> ScannerThresholds:
    return ScannerThresholds()


# ── TestTieredPriceFloor ────────────────────────────────────────────


class TestTieredPriceFloor:
    """D105: Tiered price floor -- conditional sub-$3 admission."""

    def test_sub3_blocked_by_default(self, thresholds):
        """Sub-$3 stock with low dollar volume and low RVOL is blocked."""
        df = _make_quotes_df([
            _stock(
                ticker="LOWVOL",
                price=1.50,
                prev_close=1.20,
                premarket_volume=100_000,
                avg_volume_at_time=30_000.0,
                prev_volume=500_000,  # dollar_vol = 1.50 * 500K = $750K < $10M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 0

    def test_sub3_admitted_with_high_vol_and_rvol(self, thresholds):
        """BIAF scenario: sub-$3 stock with dollar_vol > $10M AND rvol > 5x passes.
        D205: Adjusted prev_close so gap is under 50% max (was 114%, now 40%)."""
        df = _make_quotes_df([
            _stock(
                ticker="BIAF",
                price=1.07,
                prev_close=0.76,  # D205: gap = 40% (was 0.50 → 114%, blocked by gap_max)
                premarket_volume=5_000_000,
                avg_volume_at_time=10_000.0,  # rvol = 5M / 10K = 500x
                prev_volume=350_000_000,  # dollar_vol = 1.07 * 350M = $374.5M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "BIAF"

    def test_sub3_blocked_if_dollar_vol_insufficient_and_low_rvol(self, thresholds):
        """D116: Sub-$3 stock with rvol > 5x but < 30x AND dollar_vol < $2M is blocked."""
        df = _make_quotes_df([
            _stock(
                ticker="THINPENNY",
                price=0.80,
                prev_close=0.60,
                premarket_volume=50_000,
                avg_volume_at_time=5_000.0,  # rvol = 50K / 5K = 10x (> 5x but < 30x)
                prev_volume=2_000_000,  # dollar_vol = 0.80 * 2M = $1.6M < $2M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 0

    def test_sub3_passes_with_extreme_rvol_override(self, thresholds):
        """D116: Sub-$3 stock with RVOL > 30x bypasses dollar volume check."""
        df = _make_quotes_df([
            _stock(
                ticker="THINPENNY",
                price=0.80,
                prev_close=0.60,
                premarket_volume=500_000,
                avg_volume_at_time=10_000.0,  # rvol = 500K / 10K = 50x (> 30x)
                prev_volume=5_000_000,  # dollar_vol = 0.80 * 5M = $4M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "THINPENNY"

    def test_sub3_blocked_if_rvol_insufficient(self, thresholds):
        """D160: Sub-$1.50 stock with dollar_vol < $10M and rvol <= 5x and < 30x is blocked.

        D160 lowered price_min from $3.00 to $1.50. A stock at $1.20 still requires
        the high-vol override (dolvol > $2M AND rvol > 5x) or extreme RVOL (>30x)
        to pass. With rvol=4.0x (<5x) and dolvol=$4.8M (<$10M mega_dolvol), it is blocked.
        """
        df = _make_quotes_df([
            _stock(
                ticker="SLOWPENNY",
                price=1.20,
                prev_close=0.95,
                premarket_volume=200_000,
                avg_volume_at_time=50_000.0,  # rvol = 200K / 50K = 4.0x (<= 5.0)
                prev_volume=4_000_000,  # dollar_vol = 1.20 * 4M = $4.8M (< $10M mega threshold)
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 0

    def test_below_absolute_floor_blocked_without_mega_dolvol(self, thresholds):
        """Stock below $0.50 is blocked when dollar_vol < $10M mega threshold.

        D160 introduced price_override_mega_dollar_vol ($10M) which bypasses ALL price
        floors (including the $0.50 absolute floor) for demonstrably liquid stocks.
        A sub-$0.50 stock with only $1.5M dolvol (< $10M) remains blocked.
        The ITRM pattern ($54.6M dolvol at $0.07) passes; thin sub-penny stocks don't.
        """
        df = _make_quotes_df([
            _stock(
                ticker="SUBPENNY",
                price=0.30,
                prev_close=0.15,
                premarket_volume=200_000,
                avg_volume_at_time=1_000.0,  # rvol = 200K / 1K = 200x (extreme, but...)
                prev_volume=5_000_000,  # dollar_vol = 0.30 * 5M = $1.5M (< $10M mega threshold)
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 0

    def test_above_3_passes_standard_path(self, thresholds):
        """Stock at $5.00 passes standard path regardless of dollar volume."""
        df = _make_quotes_df([
            _stock(
                ticker="NORMAL",
                price=5.00,
                prev_close=4.00,
                premarket_volume=200_000,
                avg_volume_at_time=50_000.0,  # rvol = 200K / 50K = 4.0x
                prev_volume=2_000_000,  # dollar_vol = 5.00 * 2M = $10M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "NORMAL"


# ── TestAbsoluteVolumeOverride ──────────────────────────────────────


class TestAbsoluteVolumeOverride:
    """D105: Absolute volume gate as RVOL alternative."""

    def test_high_absolute_vol_low_rvol_passes(self, thresholds):
        """MARA scenario: 600K premarket vol but rvol=1.5 passes via override."""
        df = _make_quotes_df([
            _stock(
                ticker="MARA",
                price=9.00,
                prev_close=8.00,
                premarket_volume=600_000,
                avg_volume_at_time=400_000.0,  # rvol = 600K / 400K = 1.5x
                prev_volume=25_000_000,  # dollar_vol = 9 * 25M = $225M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "MARA"

    def test_low_absolute_vol_low_rvol_blocked(self, thresholds):
        """Stock with 100K premarket vol and rvol=1.5 is blocked."""
        df = _make_quotes_df([
            _stock(
                ticker="THIN",
                price=8.00,
                prev_close=7.00,
                premarket_volume=100_000,
                avg_volume_at_time=66_666.0,  # rvol = 100K / 66.7K = 1.5x
                prev_volume=10_000_000,  # dollar_vol = 8 * 10M = $80M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 0

    def test_high_rvol_low_absolute_vol_passes(self, thresholds):
        """Stock with rvol=5.0 but only 200K premarket vol passes via standard."""
        df = _make_quotes_df([
            _stock(
                ticker="HIGHRVOL",
                price=6.00,
                prev_close=5.00,
                premarket_volume=200_000,
                avg_volume_at_time=40_000.0,  # rvol = 200K / 40K = 5.0x
                prev_volume=5_000_000,  # dollar_vol = 6 * 5M = $30M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "HIGHRVOL"

    def test_both_conditions_met_still_passes(self, thresholds):
        """Stock meeting both RVOL and absolute vol thresholds passes."""
        df = _make_quotes_df([
            _stock(
                ticker="BOTH",
                price=7.00,
                prev_close=6.00,
                premarket_volume=1_000_000,
                avg_volume_at_time=100_000.0,  # rvol = 1M / 100K = 10.0x
                prev_volume=10_000_000,  # dollar_vol = 7 * 10M = $70M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "BOTH"


# ── TestPriceCeilingRaise ───────────────────────────────────────────


class TestPriceCeilingRaise:
    """D105: Price ceiling raised from $20 to $50."""

    def test_stock_at_21_now_passes(self, thresholds):
        """BMNR scenario: $20.55 stock should now pass with $50 ceiling."""
        df = _make_quotes_df([
            _stock(
                ticker="BMNR",
                price=20.55,
                prev_close=18.00,
                premarket_volume=200_000,
                avg_volume_at_time=50_000.0,  # rvol = 4.0x
                prev_volume=30_000_000,  # dollar_vol = 20.55 * 30M = $616M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "BMNR"

    def test_stock_at_50_passes(self, thresholds):
        """Stock at exactly $50 passes the ceiling."""
        df = _make_quotes_df([
            _stock(
                ticker="FIFTY",
                price=50.00,
                prev_close=42.00,
                premarket_volume=300_000,
                avg_volume_at_time=50_000.0,  # rvol = 6.0x
                prev_volume=10_000_000,  # dollar_vol = 50 * 10M = $500M
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 1
        assert candidates[0].ticker == "FIFTY"

    def test_stock_above_50_blocked(self, thresholds):
        """Stock above $50 is still blocked."""
        df = _make_quotes_df([
            _stock(
                ticker="TOOHIGH",
                price=55.00,
                prev_close=45.00,
                premarket_volume=300_000,
                avg_volume_at_time=50_000.0,
                prev_volume=10_000_000,
            ),
        ])
        candidates = scan_premarket_gappers(df, thresholds)
        assert len(candidates) == 0


# ── TestEnhancedRejectionLogging ────────────────────────────────────


class TestEnhancedRejectionLogging:
    """D105: Enhanced rejection logging for top-10 most-active tickers."""

    def test_top_active_rejected_logs_at_info(self, thresholds, caplog):
        """Top-10 most-active ticker rejection is logged at INFO level."""
        # Create one stock that is rejected (gap too low) but has high volume
        df = _make_quotes_df([
            _stock(
                ticker="BIGVOL",
                price=5.00,
                prev_close=4.90,  # gap = 2% < 5% threshold
                premarket_volume=1_000_000,
                avg_volume_at_time=100_000.0,
                prev_volume=10_000_000,
            ),
        ])
        with caplog.at_level(logging.DEBUG, logger="src.scanners.premarket"):
            candidates = scan_premarket_gappers(df, thresholds)

        assert len(candidates) == 0
        # BIGVOL is top-1 by volume so should be INFO
        info_messages = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("BIGVOL" in r.message for r in info_messages), (
            f"Expected INFO-level rejection for BIGVOL, got: {[r.message for r in info_messages]}"
        )

    def test_non_top_active_rejected_logs_at_debug(self, thresholds, caplog):
        """Non-top-10 ticker rejection stays at DEBUG level."""
        # Create 12 stocks: 10 with high volume, 2 with low volume
        rows = []
        for i in range(10):
            rows.append(_stock(
                ticker=f"BIG{i}",
                price=5.00,
                prev_close=4.90,  # gap too low
                premarket_volume=1_000_000 - i * 10_000,
                avg_volume_at_time=100_000.0,
                prev_volume=10_000_000,
            ))
        # These two have very low volume — NOT top-10
        for i in range(2):
            rows.append(_stock(
                ticker=f"SMALL{i}",
                price=5.00,
                prev_close=4.90,  # gap too low
                premarket_volume=1_000,
                avg_volume_at_time=100_000.0,
                prev_volume=10_000_000,
            ))

        df = _make_quotes_df(rows)
        with caplog.at_level(logging.DEBUG, logger="src.scanners.premarket"):
            candidates = scan_premarket_gappers(df, thresholds)

        assert len(candidates) == 0
        # SMALL0 and SMALL1 should be at DEBUG, not INFO
        debug_messages = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("SMALL0" in r.message for r in debug_messages)

    def test_rejection_reason_includes_override_info(self, thresholds, caplog):
        """Price rejection reason includes override info with dolvol and rvol details.

        D160 updated the rejection message format from "no high-vol override" to
        "no override: dolvol=$X<$Y, rvol=Zx" which also surfaces the mega-dolvol threshold.
        """
        df = _make_quotes_df([
            _stock(
                ticker="CHEAPO",
                price=1.00,
                prev_close=0.80,
                premarket_volume=200_000,
                avg_volume_at_time=50_000.0,  # rvol = 4.0x
                prev_volume=3_000_000,  # dollar_vol = 1.00 * 3M = $3M (< $10M mega threshold)
            ),
        ])
        with caplog.at_level(logging.DEBUG, logger="src.scanners.premarket"):
            candidates = scan_premarket_gappers(df, thresholds)

        assert len(candidates) == 0
        all_messages = [r.message for r in caplog.records]
        reject_msgs = [m for m in all_messages if "CHEAPO" in m and "EMC REJECT" in m]
        assert len(reject_msgs) >= 1
        # D160: Updated message format includes "no override" with dolvol<mega_threshold and rvol details
        assert "no override" in reject_msgs[0]
        assert "dolvol" in reject_msgs[0]
        assert "rvol" in reject_msgs[0]


# ── TestConfigDefaults ──────────────────────────────────────────────


class TestConfigDefaults:
    """D105: Verify new config field defaults are sensible."""

    def test_price_max_raised_to_50(self):
        t = ScannerThresholds()
        assert t.price_max == 50.0

    def test_price_min_high_volume_default(self):
        t = ScannerThresholds()
        assert t.price_min_high_volume == 0.50

    def test_price_override_dollar_vol_default(self):
        t = ScannerThresholds()
        assert t.price_override_dollar_vol == 2_000_000  # D116: lowered from $10M

    def test_price_override_rvol_default(self):
        t = ScannerThresholds()
        assert t.price_override_rvol == 5.0

    def test_absolute_volume_override_default(self):
        t = ScannerThresholds()
        assert t.absolute_volume_override == 500_000

    def test_price_min_d160(self):
        """Standard price floor exists and stays below the $3 that blocked winners.

        D160 lowered it $3.00 → $1.50 after the Selection Arena showed the $3
        floor blocked JCSE ($1.72, +72%) and MKDW ($2.10, +32%); D219 raised it
        to $2.00 (sub-$2 = deep-penny spreads/slippage). The durable invariants:
        the floor sits in [hard floor, $3) and the high-volume override floor
        stays at or below it.
        """
        t = ScannerThresholds()
        assert isinstance(t.price_min, float)
        # Below $3 (JCSE/MKDW lesson), at or above the unconditional hard floor.
        assert 0.50 <= t.price_min < 3.0
        # The extreme-override floor must not exceed the standard floor.
        assert t.price_min_high_volume <= t.price_min
        assert t.price_min < t.price_max
