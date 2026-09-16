"""
Wed 2026-04-22 Bug F regression tests.

Background. Today's BAR-1 EXIT log lines were structurally identical
across three completely different trades:

    D146 BAR-1 EXIT: ELSE — selling 2478/2478 shares (100%) at T+60s. Arena: +$5.11 (+299%).
    D146 BAR-1 EXIT: AGPU — selling 846/846 shares (100%) at T+60s. Arena: +$5.11 (+299%).
    D146 BAR-1 EXIT: MAAS — selling 200/200 shares (100%) at T+60s. Arena: +$5.11 (+299%).

The "+$5.11 (+299%)" literal is the Arena backtest mean from the
research that motivated the BAR-1 pattern (D146 docstring confirms).
It was NOT a placeholder for per-trade realized P&L — it was an
expected-value annotation that got mislabeled "Arena: ..." with no
qualifier, making it read as if it were the live trade's P&L.

Decision (per directive): relabel to `Arena_expected` and add
`Arena_actual` alongside, emitted after close_with_attribution
completes so the realized per-trade P&L is on the line.

T1:    pre-exit log uses 'Arena_expected' qualifier, not bare 'Arena'
T2:    post-exit log includes 'Arena_actual=$X.XX' with realized P&L
T3:    three trades with distinct realized P&L produce three distinct
       Arena_actual values in the log (the directive's anti-regression)
T4:    Arena_expected literal is centralized as a constant, not magic
"""

from __future__ import annotations

import logging
import re

import pytest


# ── T1: pre-exit qualifier ─────────────────────────────────────────


class TestPreExitArenaExpected:
    """The pre-exit log line must use 'Arena_expected' (with qualifier)
    so operators don't misread the backtest mean as live P&L."""

    def test_format_helper_emits_arena_expected_qualifier(self):
        from src.execution.bar1_exit_logging import format_arena_expected_line

        msg = format_arena_expected_line(
            ticker="ELSE", qty=2478, total=2478, pct=1.0, age_s=60.0,
        )
        # Must contain the explicit "Arena_expected" qualifier
        assert "Arena_expected" in msg, (
            f"BAR-1 pre-exit log must qualify the backtest mean as "
            f"'Arena_expected', not bare 'Arena: $X.XX'. Got: {msg}"
        )
        # Backtest constants still present so operators see the bench
        assert "$5.11" in msg
        assert "299" in msg
        # Contains trade context
        assert "ELSE" in msg
        assert "2478" in msg


# ── T2: post-exit Arena_actual ─────────────────────────────────────


class TestPostExitArenaActual:

    def test_format_arena_actual_line_includes_realized_pnl(self):
        from src.execution.bar1_exit_logging import format_arena_actual_line

        msg = format_arena_actual_line(
            ticker="ELSE",
            realized_pnl=298.20,
            entry_price=7.65,
            exit_price=7.7703,
            qty=2478,
        )
        assert "Arena_actual" in msg
        # The realized P&L appears with the right sign + magnitude
        assert "+$298.20" in msg or "298.20" in msg
        assert "ELSE" in msg

    def test_arena_actual_handles_negative_pnl(self):
        from src.execution.bar1_exit_logging import format_arena_actual_line

        msg = format_arena_actual_line(
            ticker="XYZ",
            realized_pnl=-42.75,
            entry_price=10.00,
            exit_price=9.95,
            qty=855,
        )
        assert "Arena_actual" in msg
        assert "-$42.75" in msg or "-42.75" in msg

    def test_arena_actual_handles_zero_pnl(self):
        """AGPU's BAR-1 exit was flat. The line must still emit cleanly
        with $0.00 — no formatting glitch on zero."""
        from src.execution.bar1_exit_logging import format_arena_actual_line

        msg = format_arena_actual_line(
            ticker="AGPU",
            realized_pnl=0.00,
            entry_price=9.59,
            exit_price=9.59,
            qty=846,
        )
        assert "Arena_actual" in msg
        assert "$0.00" in msg


# ── T3: three trades, three distinct values (the anti-regression) ──


class TestThreeDistinctValues:
    """Today's bug: three trades, identical literals. After the fix:
    three trades, three distinct realized values in the Arena_actual
    log lines."""

    def test_three_trades_produce_three_distinct_arena_actual_values(self):
        from src.execution.bar1_exit_logging import format_arena_actual_line

        trades = [
            ("ELSE", 298.20, 7.65, 7.7703, 2478),
            ("AGPU",   0.00, 9.59, 9.59,    846),
            ("MAAS",  30.60, 12.00, 12.153,  200),
        ]
        emitted = [
            format_arena_actual_line(
                ticker=t, realized_pnl=p, entry_price=e,
                exit_price=x, qty=q,
            )
            for (t, p, e, x, q) in trades
        ]
        # Extract the dollar amount from Arena_actual=$X.XX in each line
        amounts = []
        for msg in emitted:
            m = re.search(r"Arena_actual=([+\-]?\$\d+\.\d{2})", msg)
            assert m is not None, (
                f"format_arena_actual_line must emit "
                f"'Arena_actual=$X.XX' (with explicit dollar sign and "
                f"two decimals) so the value is greppable. Got: {msg}"
            )
            amounts.append(m.group(1))
        # The Bug F regression: three trades must have THREE different
        # realized values, not the same hardcoded literal.
        assert len(set(amounts)) == 3, (
            f"Three trades with different realized P&L produced only "
            f"{len(set(amounts))} unique Arena_actual values: {amounts}. "
            f"Bug F regression: hardcoded literal returned across distinct trades."
        )


# ── T4: Arena_expected backtest constants centralized ──────────────


class TestArenaExpectedConstantCentralized:
    """The +$5.11 (+299%) numbers came from the Arena research that
    motivated D146. Centralize them as a named constant so any future
    update to the backtest mean propagates from one place, and the
    pre-exit log line can never silently drift from the underlying
    research again."""

    def test_module_exposes_named_constants(self):
        import src.execution.bar1_exit_logging as bel

        # Both numbers must be exposed as module-level constants with
        # discoverable names.
        assert hasattr(bel, "ARENA_EXPECTED_MEAN_USD"), (
            "Arena_expected mean must be a named constant, not a magic "
            "literal in a log f-string"
        )
        assert hasattr(bel, "ARENA_EXPECTED_UPLIFT_PCT")
        # Sanity-check the values match the research.
        assert bel.ARENA_EXPECTED_MEAN_USD == pytest.approx(5.11, abs=0.01)
        assert bel.ARENA_EXPECTED_UPLIFT_PCT == pytest.approx(299.0, abs=1.0)
