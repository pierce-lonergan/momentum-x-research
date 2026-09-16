"""
Tests for D107 WS1: Per-Cycle Exit Signal History Logger.

Covers:
  - SignalHistoryLogger: file creation, entry format, daily rotation,
    close behavior, write failure resilience, directory creation.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.execution.exit_intelligence import ExitSignal, SignalHistoryLogger


# ── Helpers ──────────────────────────────────────────────────────


def _make_signal(ticker: str = "TEST", **overrides) -> ExitSignal:
    """Create an ExitSignal with optional overrides."""
    defaults = {
        "ticker": ticker,
        "volume_fade": 0.1,
        "vwap_deterioration": 0.2,
        "spread_widening": 0.05,
        "time_decay": 0.3,
        "distribution": 0.0,
        "resistance_proximity": 0.15,
        "failed_breakout": 0.0,
        "churning": 0.1,
        "obv_divergence": 0.0,
        "volume_climax": 0.0,
        "momentum_degradation": 0.05,
        "flow_toxicity": 0.0,
        "distribution_detector": 0.0,
        "composite_exit_urgency": 0.12,
        "recommendation": "HOLD",
        "reasoning": "test signal",
    }
    defaults.update(overrides)
    return ExitSignal(**defaults)


# ── Tests ────────────────────────────────────────────────────────


class TestSignalHistoryLogger:
    """Tests for SignalHistoryLogger."""

    def test_log_cycle_creates_file(self, tmp_path: Path) -> None:
        """JSONL file is created in the target directory."""
        logger = SignalHistoryLogger(signal_history_dir=str(tmp_path))
        signal = _make_signal()

        logger.log_cycle(
            ticker="AAPL",
            signal=signal,
            current_price=10.50,
            entry_price=10.00,
            pnl_pct=0.05,
        )
        logger.close()

        # Find the file
        files = list(tmp_path.glob("signal_log_*.jsonl"))
        assert len(files) == 1
        assert files[0].stat().st_size > 0

    def test_log_cycle_entry_format(self, tmp_path: Path) -> None:
        """JSON line contains all 13 signals + composite + recommendation + price + pnl_pct."""
        logger = SignalHistoryLogger(signal_history_dir=str(tmp_path))
        signal = _make_signal(
            volume_fade=0.42,
            vwap_deterioration=0.18,
            composite_exit_urgency=0.25,
            recommendation="TIGHTEN",
        )

        logger.log_cycle(
            ticker="TSLA",
            signal=signal,
            current_price=250.0,
            entry_price=245.0,
            pnl_pct=0.0204,
        )
        logger.close()

        files = list(tmp_path.glob("signal_log_*.jsonl"))
        line = files[0].read_text().strip()
        entry = json.loads(line)

        # Required metadata fields
        assert entry["ticker"] == "TSLA"
        assert entry["recommendation"] == "TIGHTEN"
        assert entry["price"] == 250.0
        assert entry["entry_price"] == 245.0
        assert entry["pnl_pct"] == 0.0204

        # Composite
        assert entry["composite"] == 0.25

        # All 13 individual signals present
        expected_signals = [
            "volume_fade", "vwap_deterioration", "spread_widening",
            "time_decay", "distribution", "resistance_proximity",
            "failed_breakout", "churning", "obv_divergence",
            "volume_climax", "momentum_degradation", "flow_toxicity",
            "distribution_detector",
        ]
        for sig_name in expected_signals:
            assert sig_name in entry, f"Missing signal: {sig_name}"

        # Verify specific signal values
        assert entry["volume_fade"] == 0.42
        assert entry["vwap_deterioration"] == 0.18

        # Timestamp present and parseable
        assert "ts" in entry
        datetime.fromisoformat(entry["ts"])  # Should not raise

    def test_daily_rotation(self, tmp_path: Path) -> None:
        """New file is created when date changes (mock datetime)."""
        logger = SignalHistoryLogger(signal_history_dir=str(tmp_path))
        signal = _make_signal()

        # Log on "day 1"
        with patch(
            "src.execution.exit_intelligence.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            logger.log_cycle(ticker="AAA", signal=signal, current_price=10, entry_price=10, pnl_pct=0)

        # Log on "day 2" — must produce a second file
        with patch(
            "src.execution.exit_intelligence.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            logger.log_cycle(ticker="BBB", signal=signal, current_price=11, entry_price=11, pnl_pct=0)

        logger.close()

        files = sorted(tmp_path.glob("signal_log_*.jsonl"))
        assert len(files) == 2
        assert "2026-03-10" in files[0].name
        assert "2026-03-11" in files[1].name

    def test_close_flushes(self, tmp_path: Path) -> None:
        """close() works cleanly and allows file to be read."""
        logger = SignalHistoryLogger(signal_history_dir=str(tmp_path))
        signal = _make_signal()

        logger.log_cycle(ticker="XYZ", signal=signal, current_price=5, entry_price=5, pnl_pct=0)
        logger.close()

        # File should be readable and have content
        files = list(tmp_path.glob("signal_log_*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text().strip()
        assert len(content) > 0
        entry = json.loads(content)
        assert entry["ticker"] == "XYZ"

        # Double close should not raise
        logger.close()

    def test_write_failure_non_fatal(self, tmp_path: Path) -> None:
        """Write to read-only directory does not raise exceptions."""
        # Use a path that can't be written to
        bad_dir = tmp_path / "readonly"
        bad_dir.mkdir()
        bad_file = bad_dir / "signal_log_2026-03-10.jsonl"
        bad_file.touch()

        # Make the file read-only (cross-platform approach)
        if os.name == "nt":
            # Windows: make file read-only
            import stat
            bad_file.chmod(stat.S_IREAD)
        else:
            bad_dir.chmod(0o444)

        logger = SignalHistoryLogger(signal_history_dir=str(bad_dir))
        signal = _make_signal()

        # Should not raise — fire-and-forget
        logger.log_cycle(ticker="FAIL", signal=signal, current_price=1, entry_price=1, pnl_pct=0)
        logger.close()

        # Cleanup permissions
        if os.name == "nt":
            import stat
            bad_file.chmod(stat.S_IWRITE | stat.S_IREAD)
        else:
            bad_dir.chmod(0o755)

    def test_mkdir_creates_parents(self, tmp_path: Path) -> None:
        """Nested directories are created automatically."""
        nested_dir = str(tmp_path / "deep" / "nested" / "path")
        logger = SignalHistoryLogger(signal_history_dir=nested_dir)
        signal = _make_signal()

        logger.log_cycle(ticker="NEST", signal=signal, current_price=20, entry_price=20, pnl_pct=0)
        logger.close()

        files = list(Path(nested_dir).glob("signal_log_*.jsonl"))
        assert len(files) == 1

    def test_multiple_positions_per_cycle(self, tmp_path: Path) -> None:
        """Multiple positions logged in the same cycle produce multiple JSONL lines."""
        logger = SignalHistoryLogger(signal_history_dir=str(tmp_path))

        for ticker, price in [("AAA", 10.0), ("BBB", 20.0), ("CCC", 30.0)]:
            signal = _make_signal(composite_exit_urgency=price / 100)
            logger.log_cycle(
                ticker=ticker,
                signal=signal,
                current_price=price,
                entry_price=price * 0.95,
                pnl_pct=0.0526,
            )

        logger.close()

        files = list(tmp_path.glob("signal_log_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 3

        tickers = [json.loads(line)["ticker"] for line in lines]
        assert tickers == ["AAA", "BBB", "CCC"]

    def test_d109_extension_fields(self, tmp_path: Path) -> None:
        """D109 Phase 4 extension fields (intraday_atr, mfe_pct, time_held_min) are written."""
        logger = SignalHistoryLogger(signal_history_dir=str(tmp_path))
        signal = _make_signal()

        logger.log_cycle(
            ticker="EXT",
            signal=signal,
            current_price=15.0,
            entry_price=14.0,
            pnl_pct=0.071,
            intraday_atr=1.25,
            mfe_pct=0.035,
            time_held_min=47.5,
        )
        logger.close()

        files = list(tmp_path.glob("signal_log_*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert entry["intraday_atr"] == 1.25
        assert entry["mfe_pct"] == 0.035
        assert entry["time_held_min"] == 47.5


class TestExitSignalToScoresDict:
    """D218: Regression guard for to_scores_dict() — prevents silent rename breaking logger."""

    def test_has_14_fields(self) -> None:
        signal = _make_signal()
        scores = signal.to_scores_dict()
        assert len(scores) == 14, f"Expected 14, got {len(scores)}: {list(scores.keys())}"

    def test_contains_composite(self) -> None:
        signal = _make_signal(composite_exit_urgency=0.42)
        assert signal.to_scores_dict()["composite"] == 0.42

    def test_all_13_signals_present(self) -> None:
        signal = _make_signal()
        scores = signal.to_scores_dict()
        expected = [
            "volume_fade", "vwap_deterioration", "spread_widening", "time_decay",
            "distribution", "resistance_proximity", "failed_breakout",
            "churning", "obv_divergence", "volume_climax",
            "momentum_degradation", "flow_toxicity", "distribution_detector",
        ]
        for name in expected:
            assert name in scores, f"Missing: {name}"

    def test_values_match_input(self) -> None:
        signal = _make_signal(volume_fade=0.777, churning=0.333)
        scores = signal.to_scores_dict()
        assert scores["volume_fade"] == 0.777
        assert scores["churning"] == 0.333

    def test_values_rounded_to_3dp(self) -> None:
        signal = _make_signal(volume_fade=0.123456789)
        assert signal.to_scores_dict()["volume_fade"] == 0.123
