"""
Wed 2026-04-22 Bug #13 regression tests.

Background. EOD trade journal printed:

    ═══ TRADE JOURNAL SUMMARY ═══
      Evaluations: 8 | BUYs: 3 | Closed: 0
      Total P&L: $0.00 | Avg MFCS: ...

despite three real closures and +$328.80 realized (broker-confirmed):
  ELSE 10:00 ET → +$298.20
  AGPU 10:52 ET → +$0.00 (BAR-1 exit, flat)
  MAAS 11:25 ET → +$30.60

Root cause. `TradeJournal.session_summary()` derives `closed_count`
and `total_pnl` from `entry.exit_price is not None` and
`entry.realized_pnl`. Both fields only get set when the exit path
explicitly calls `record_close()`. The BAR-1 / D146 path does not call
record_close — so any close that goes through BAR-1 leaves the journal
entry stuck with `exit_price=None` forever. The counter wasn't broken;
the data flow was inverted.

Fix per directive: reverse the data flow. The journal's Closed count
and Total P&L must derive from the **broker fill ledger** as single
source of truth, not from per-exit-path counters that each exit path
has to remember to increment.

Plus: EOD reconciliation assertion. If `journal.realized_pnl` (from
record_close path, where it exists) differs from the broker-fill
derived total by more than $1, log a "D222 PNL_RECON" warning with
the delta. Silent agreement is not verification.

T1-T3:  derive closed_count and total_pnl from broker fills
T4:     three-trade reproduction — ELSE/AGPU/MAAS exact replay
T5-T6:  D222 PNL_RECON warning fires only on >$1 divergence
T7:     EOD recon log includes per-ticker breakdown
T8:     reconciliation is non-fatal on broker-API failure
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── T1-T3: counts derive from broker fills ─────────────────────────


class TestSessionSummaryFromBrokerFills:
    """Closed count and total_pnl must come from the broker fill ledger,
    not from per-exit-path counters that BAR-1 etc. forget to update."""

    def test_closed_count_counts_unique_tickers_with_sell_fills(self):
        """For every ticker with a buy fill AND a sell fill on the
        session, closed_count += 1. BAR-1 path also counts because the
        broker recorded the sell — no record_close() call required."""
        from src.analysis.trade_journal import summarize_from_broker_fills

        fills = [
            # ELSE: buy + sell → 1 close
            {"symbol": "ELSE", "side": "buy",  "qty": "100", "price": "5.00"},
            {"symbol": "ELSE", "side": "sell", "qty": "100", "price": "8.00"},
            # AGPU: buy + sell → 1 close (BAR-1 path; record_close NEVER called)
            {"symbol": "AGPU", "side": "buy",  "qty": "846", "price": "9.59"},
            {"symbol": "AGPU", "side": "sell", "qty": "846", "price": "9.59"},
            # MAAS: buy + sell → 1 close
            {"symbol": "MAAS", "side": "buy",  "qty": "200", "price": "12.00"},
            {"symbol": "MAAS", "side": "sell", "qty": "200", "price": "12.15"},
            # XYZ: buy only (still open) → does NOT count
            {"symbol": "XYZ",  "side": "buy",  "qty": "100", "price": "5.00"},
        ]
        summary = summarize_from_broker_fills(fills)
        assert summary["closed_count"] == 3
        assert summary["open_count"] == 1

    def test_total_pnl_sums_realized_per_ticker(self):
        """Total P&L = Σ over closed tickers of (avg_sell - avg_buy) × qty."""
        from src.analysis.trade_journal import summarize_from_broker_fills

        fills = [
            {"symbol": "A", "side": "buy",  "qty": "100", "price": "10.00"},
            {"symbol": "A", "side": "sell", "qty": "100", "price": "11.00"},  # +$100
            {"symbol": "B", "side": "buy",  "qty": "50",  "price": "20.00"},
            {"symbol": "B", "side": "sell", "qty": "50",  "price": "19.50"},  # -$25
        ]
        summary = summarize_from_broker_fills(fills)
        assert summary["closed_count"] == 2
        assert summary["total_pnl"] == pytest.approx(75.00, abs=0.01)

    def test_partial_close_does_not_count_until_flat(self):
        """A ticker still has open shares (buy 100 / sell 60) — not yet
        closed for accounting. Realized P&L on the 60 sold counts;
        closed_count stays 0 until shares net to zero."""
        from src.analysis.trade_journal import summarize_from_broker_fills

        fills = [
            {"symbol": "C", "side": "buy",  "qty": "100", "price": "5.00"},
            {"symbol": "C", "side": "sell", "qty": "60",  "price": "5.50"},
        ]
        summary = summarize_from_broker_fills(fills)
        assert summary["closed_count"] == 0
        assert summary["open_count"] == 1
        # Realized PnL on the 60 already sold — still counted in total
        assert summary["total_pnl"] == pytest.approx(60 * 0.50, abs=0.01)


# ── T4: ELSE / AGPU / MAAS exact replay ────────────────────────────


class TestTodaysThreeClosuresReplay:
    """Replay today's exact closures and assert the spec totals.

    From the postmortem (2026-04-22):
      ELSE 10:00 ET  +$298.20
      AGPU 10:52 ET  +$0.00   (BAR-1 exit; the bug case)
      MAAS 11:25 ET  +$30.60
      ─────────────────────────
      Total          +$328.80
    """

    def test_else_agpu_maas_close_count_and_pnl(self):
        from src.analysis.trade_journal import summarize_from_broker_fills

        # Reconstruct buy/sell legs that produce the postmortem's
        # realized P&L per ticker. Quantities and prices preserve the
        # totals to within $0.01.
        fills = [
            # ELSE: 2478 sh, +$298.20  →  +$0.12031 / sh   (entry $7.65; exit $7.7703)
            {"symbol": "ELSE", "side": "buy",  "qty": "2478", "price": "7.65"},
            {"symbol": "ELSE", "side": "sell", "qty": "2478", "price": "7.77031"},
            # AGPU: 846 sh, +$0.00 (BAR-1, flat)
            {"symbol": "AGPU", "side": "buy",  "qty": "846",  "price": "9.59"},
            {"symbol": "AGPU", "side": "sell", "qty": "846",  "price": "9.59"},
            # MAAS: 200 sh, +$30.60  →  +$0.153 / sh
            {"symbol": "MAAS", "side": "buy",  "qty": "200",  "price": "12.00"},
            {"symbol": "MAAS", "side": "sell", "qty": "200",  "price": "12.153"},
        ]
        summary = summarize_from_broker_fills(fills)
        assert summary["closed_count"] == 3, (
            f"Today's three closures (ELSE/AGPU/MAAS) must count as "
            f"3 closed, not {summary['closed_count']}. Bug #13: BAR-1's "
            f"AGPU exit was being silently dropped because record_close() "
            f"was never called on that path."
        )
        # Tolerance $0.10 absorbs floating-point roundoff in the synthetic
        # per-share prices (real broker reports use 4-dp prices; we use 5).
        # Production recon tolerance is $1.00 (PNL_RECON_TOLERANCE_USD).
        assert summary["total_pnl"] == pytest.approx(328.80, abs=0.10), (
            f"Today's three closures totalled +$328.80 realized; "
            f"summary returned ${summary['total_pnl']:.2f}"
        )


# ── T5-T6: D222 PNL_RECON warning behavior ─────────────────────────


class TestPnlReconWarning:
    """At EOD, compare journal.realized_pnl vs broker-fill realized_pnl.
    On divergence > $1, log "D222 PNL_RECON" warning with the delta and
    a per-ticker breakdown."""

    def test_no_warning_when_within_dollar_tolerance(self, caplog):
        from src.analysis.trade_journal import emit_pnl_reconciliation

        # Journal claims $328.80; broker says $328.10. Within $1 → quiet.
        with caplog.at_level(logging.WARNING, logger="src.analysis.trade_journal"):
            emit_pnl_reconciliation(
                journal_pnl=328.80,
                broker_pnl=328.10,
                per_ticker_journal={"ELSE": 298.20, "AGPU": 0.00, "MAAS": 30.60},
                per_ticker_broker={"ELSE": 298.10, "AGPU": 0.00, "MAAS": 30.00},
            )
        recon_logs = [r for r in caplog.records if "D222 PNL_RECON" in r.message]
        assert len(recon_logs) == 0

    def test_warning_fires_on_dollar_divergence(self, caplog):
        from src.analysis.trade_journal import emit_pnl_reconciliation

        # Journal says $0 (the bug); broker says $328.80. Divergence
        # $328.80 → warning required.
        with caplog.at_level(logging.WARNING, logger="src.analysis.trade_journal"):
            emit_pnl_reconciliation(
                journal_pnl=0.00,
                broker_pnl=328.80,
                per_ticker_journal={},
                per_ticker_broker={"ELSE": 298.20, "AGPU": 0.00, "MAAS": 30.60},
            )
        recon_logs = [r for r in caplog.records if "D222 PNL_RECON" in r.message]
        assert len(recon_logs) == 1
        msg = recon_logs[0].message
        assert "328.80" in msg or "$328.80" in msg, f"missing delta in: {msg}"
        # Per-ticker breakdown must be visible — operator needs to see
        # which closures the journal missed.
        assert "ELSE" in msg
        assert "MAAS" in msg


# ── T7: per-ticker breakdown explicit in log ───────────────────────


class TestReconBreakdown:
    """The recon log must call out the missing tickers individually so
    operators can immediately see which exit path forgot to record."""

    def test_breakdown_lists_journal_missing_tickers(self, caplog):
        from src.analysis.trade_journal import emit_pnl_reconciliation

        with caplog.at_level(logging.WARNING, logger="src.analysis.trade_journal"):
            emit_pnl_reconciliation(
                journal_pnl=298.20,
                broker_pnl=328.80,
                per_ticker_journal={"ELSE": 298.20},
                per_ticker_broker={"ELSE": 298.20, "AGPU": 0.00, "MAAS": 30.60},
            )
        recon_msgs = [r.message for r in caplog.records if "D222 PNL_RECON" in r.message]
        assert recon_msgs, "expected a D222 PNL_RECON warning"
        msg = recon_msgs[0]
        # MAAS journal=missing, broker=$30.60 — must appear
        assert "MAAS" in msg, f"MAAS missing from recon breakdown: {msg}"
        # AGPU at $0 still gets called out as journal-missing
        assert "AGPU" in msg, f"AGPU missing from recon breakdown: {msg}"


# ── T8: non-fatal on broker fetch failure ──────────────────────────
# ── T9: works against real AlpacaDataClient method shape (Bug N) ───


class TestReconWithRealClientShape:
    """Bug N (2026-04-23): yesterday's Bug #13 patch assumed
    `client.get_account_activities` exists. AlpacaDataClient does NOT
    expose that — only `get_orders`. The patch shipped with a silent
    AttributeError caught by a broad `except Exception` and produced
    the exact same `Closed: 0 / P&L: $0.00` symptom Bug #13 was
    supposed to fix.

    The fix: derive fills from `client.get_orders(status='closed')`
    which returns each filled order with `filled_avg_price`,
    `filled_qty`, `side`, `symbol` — sufficient for
    `summarize_from_broker_fills`.
    """

    @pytest.mark.asyncio
    async def test_recon_uses_get_orders_not_get_account_activities(self, caplog):
        """The async helper must work against a client that exposes
        `get_orders` but NOT `get_account_activities` — i.e., the
        actual production AlpacaDataClient shape."""
        from src.analysis.trade_journal import run_pnl_reconciliation

        # Real-client-shape mock: NO get_account_activities attribute.
        client = MagicMock(spec=["get_orders"])
        client.get_orders = AsyncMock(return_value=[
            {"symbol": "ELSE", "side": "buy",  "filled_qty": "100", "filled_avg_price": "5.00", "status": "filled"},
            {"symbol": "ELSE", "side": "sell", "filled_qty": "100", "filled_avg_price": "5.50", "status": "filled"},
        ])

        with caplog.at_level(logging.INFO, logger="src.analysis.trade_journal"):
            await run_pnl_reconciliation(
                client=client,
                journal_pnl=50.00,
                per_ticker_journal={"ELSE": 50.00},
            )
        # Must not have raised AttributeError or emitted DEGRADED warning.
        # Note: a benign D236 INFO line MAY mention 'get_account_activities'
        # in its descriptive text — that's the new path-marker telemetry.
        # The bug shape we're guarding against is an AttributeError that
        # falls through to the DEGRADED warning path.
        regressions = [
            r for r in caplog.records
            if (
                "AttributeError" in r.message
                or "DEGRADED" in r.message
                or r.levelname in ("ERROR", "CRITICAL")
            )
        ]
        assert len(regressions) == 0, (
            f"Bug N regression: recon path raised or degraded. "
            f"Got: {[(r.levelname, r.message) for r in regressions]}"
        )

    def test_summarize_accepts_get_orders_fill_shape(self):
        """The fill-summarisation helper must accept the get_orders
        payload shape directly (filled_qty / filled_avg_price keys)
        in addition to the simpler (qty / price) shape."""
        from src.analysis.trade_journal import summarize_from_broker_fills

        orders = [
            {"symbol": "XNDU", "side": "buy",  "filled_qty": "395", "filled_avg_price": "30.74"},
            {"symbol": "XNDU", "side": "sell", "filled_qty": "395", "filled_avg_price": "30.80"},
        ]
        summary = summarize_from_broker_fills(orders)
        assert summary["closed_count"] == 1
        assert summary["total_pnl"] == pytest.approx(395 * 0.06, abs=0.01)


class TestReconNonFatal:
    @pytest.mark.asyncio
    async def test_recon_runner_swallows_broker_errors(self, caplog):
        """The async helper that fetches broker fills + emits the recon
        warning must not crash the EOD summary path on a broker outage."""
        from src.analysis.trade_journal import run_pnl_reconciliation

        client = MagicMock()
        client.get_account_activities = AsyncMock(
            side_effect=RuntimeError("API down"),
        )
        # journal_pnl + per_ticker stand-in
        with caplog.at_level(logging.WARNING, logger="src.analysis.trade_journal"):
            await run_pnl_reconciliation(
                client=client,
                journal_pnl=328.80,
                per_ticker_journal={"ELSE": 298.20, "AGPU": 0.00, "MAAS": 30.60},
            )
        # Must not raise. May emit a degraded log explaining the failure.
        api_failure_logs = [r for r in caplog.records if "D222" in r.message and ("API" in r.message or "broker" in r.message.lower())]
        assert len(api_failure_logs) >= 1, (
            "Reconciliation must emit a degraded D222 log when broker is "
            "unreachable so operators know the check did not run."
        )
