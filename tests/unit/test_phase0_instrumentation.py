"""Phase 0 instrumentation tests — schemas + writer + integration.

Per `docs/research-log/21_phase0_instrumentation_mvp.md` §5-7. Three test
categories:

  1. Schema round-trip — each schema writes and reads back as identical
     Pydantic instance via the InstrumentationWriter.
  2. Validation discipline (D261) — invalid payloads log + count without
     crashing the trading hot path.
  3. Lifecycle integration — mock a complete order lifecycle (entry →
     partial → full → BAR-1 → exit), assert all four schemas write
     correctly, partition correctly, and round-trip.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from src.analysis.instrumentation import (
    BarContextRow,
    ChildFillRow,
    CohortRow,
    InstrumentationWriter,
    SCHEMA_VERSION,
    TradeContextRow,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def writer(tmp_path: Path) -> InstrumentationWriter:
    return InstrumentationWriter(
        base_dir=tmp_path / "instrumentation",
        session_date="2026-04-25",
        ring_size=10,
    )


@pytest.fixture
def sync_writer(tmp_path: Path) -> InstrumentationWriter:
    """Sync-on-emit variant (ring_size=1) — for tests that want every
    emit to flush immediately."""
    return InstrumentationWriter(
        base_dir=tmp_path / "instrumentation",
        session_date="2026-04-25",
        ring_size=1,
    )


def _now() -> datetime:
    return datetime(2026, 4, 25, 14, 30, 0, tzinfo=timezone.utc)


# ── 1. Schema round-trip tests ──────────────────────────────────


class TestSchemaRoundTrip:

    def test_trade_context_round_trip(self, sync_writer: InstrumentationWriter) -> None:
        row = TradeContextRow(
            order_id="oid-1", ticker="AAPL", side="buy",
            requested_qty=100, requested_px=150.50, submit_ts=_now(),
            submit_nbbo_bid=150.49, submit_nbbo_ask=150.51,
            terminal_ts=_now() + timedelta(seconds=30),
            terminal_nbbo_bid=150.48, terminal_nbbo_ask=150.50,
            terminal_status="filled", terminal_filled_qty=100,
        )
        sync_writer.emit_trade_context(row)
        # ring_size=1 → flushed on emit
        path = sync_writer._output_path("trade_context")
        assert path.exists()
        df = pd.read_parquet(path)
        assert len(df) == 1
        assert df.iloc[0]["order_id"] == "oid-1"
        assert df.iloc[0]["schema_version"] == SCHEMA_VERSION
        assert df.iloc[0]["terminal_filled_qty"] == 100

    def test_bar_context_round_trip(self, sync_writer: InstrumentationWriter) -> None:
        row = BarContextRow(
            position_id="pos-1", ticker="MAAS", entry_ts=_now(),
            entry_bar_open_ts=_now() - timedelta(seconds=15),
            entry_bar_open=2.10, entry_bar_high=2.18, entry_bar_low=2.08,
            entry_bar_close=2.15,
            entry_bar_volume=3000, our_q_shares=200,
            q_over_v_tau=200 / 3000,
            bar_data_quality="complete",
        )
        sync_writer.emit_bar_context(row)
        df = pd.read_parquet(sync_writer._output_path("bar_context"))
        assert len(df) == 1
        # The MAAS test case from the spec preamble: 200/3000 = 0.0667
        assert df.iloc[0]["q_over_v_tau"] == pytest.approx(0.0667, abs=0.0001)

    def test_child_fill_round_trip(self, sync_writer: InstrumentationWriter) -> None:
        row = ChildFillRow(
            parent_order_id="oid-1", child_fill_ts=_now(),
            qty=505, price=2.15,
            cumulative_filled_qty=505,
        )
        sync_writer.emit_child_fill(row)
        df = pd.read_parquet(sync_writer._output_path("child_fill_ticks"))
        assert len(df) == 1
        # MVP per spec: venue + nbbo NULL until WebSocket subscription
        assert pd.isna(df.iloc[0]["venue"])
        assert pd.isna(df.iloc[0]["nbbo_bid_at_fill"])

    def test_cohort_registry_round_trip(self, sync_writer: InstrumentationWriter) -> None:
        row = CohortRow(
            cohort_id="cohort-uuid-123", traded_ticker="MAAS",
            cohort_ticker="ELSE", match_dt=_now(),
            match_features={
                "catalyst_type": "earnings_beat",
                "market_cap_bucket": "micro",
                "gap_pct_bucket": "30-50",
                "hour_bucket": "09:30-10:00",
            },
            cohort_entry_ref_px=1.85,
            cohort_data_quality="partial",
        )
        sync_writer.emit_cohort_registry(row)
        df = pd.read_parquet(sync_writer._output_path("cohort_registry"))
        assert len(df) == 1
        assert df.iloc[0]["traded_ticker"] == "MAAS"
        # match_features stored as a JSON-compatible dict (Pydantic
        # model_dump(mode='json') makes it a dict that Parquet stores
        # as a JSON-like map)
        features = df.iloc[0]["match_features"]
        if isinstance(features, str):
            features = json.loads(features)
        assert features["catalyst_type"] == "earnings_beat"


# ── 2. Validation discipline (D261) ─────────────────────────────


class TestD261ValidationDiscipline:

    def test_emit_dict_returns_false_on_invalid_payload(
        self, writer: InstrumentationWriter, caplog,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.analysis.instrumentation.writer"):
            ok = writer.emit_trade_context_dict({
                "order_id": "",  # min_length=1 → fails
                "ticker": "AAPL",
            })
        assert ok is False
        assert writer._d261_failures["trade_context"] == 1
        # D261 marker must be in the log line
        assert any("D261" in r.message for r in caplog.records)
        # Buffer must be EMPTY — invalid row never enqueued
        assert writer._buffers["trade_context"] == []

    def test_emit_model_directly_raises_validation_error(self) -> None:
        """Constructing TradeContextRow with bad data raises Pydantic.
        Production code that uses emit_*() (not emit_*_dict) must catch."""
        with pytest.raises(ValidationError):
            TradeContextRow(
                order_id="", ticker="X", side="buy",
                requested_qty=1, requested_px=1.0, submit_ts=_now(),
                terminal_filled_qty=0,
            )

    def test_d261_counter_persists_across_failures(
        self, writer: InstrumentationWriter,
    ) -> None:
        for _ in range(3):
            writer.emit_bar_context_dict({"ticker": "X"})  # missing required fields
        assert writer._d261_failures["bar_context"] == 3

    def test_health_snapshot_exposes_d261_counters(
        self, writer: InstrumentationWriter,
    ) -> None:
        writer.emit_cohort_registry_dict({"cohort_id": ""})
        snap = writer.health_snapshot()
        assert snap["d261_failures"]["cohort_registry"] == 1
        assert snap["session_date"] == "2026-04-25"


# ── 3. Buffering + atomicity ────────────────────────────────────


class TestBufferingAndAtomicity:

    def test_ring_full_auto_flushes(self, tmp_path: Path) -> None:
        w = InstrumentationWriter(
            base_dir=tmp_path / "instr", session_date="2026-04-25",
            ring_size=3,
        )
        for i in range(3):
            w.emit_trade_context(TradeContextRow(
                order_id=f"oid-{i}", ticker="X", side="buy",
                requested_qty=1, requested_px=1.0, submit_ts=_now(),
                terminal_filled_qty=0,
            ))
        # Ring size 3 → flushed on the 3rd emit
        assert w._buffers["trade_context"] == []
        path = w._output_path("trade_context")
        assert path.exists()
        df = pd.read_parquet(path)
        assert len(df) == 3

    def test_flush_all_writes_every_schema(
        self, sync_writer: InstrumentationWriter,
    ) -> None:
        # Reset to ring_size=10 — emit one per schema, then explicit flush
        w = sync_writer
        w.ring_size = 10
        # Re-init buffers (already empty, but be explicit)
        w._buffers = {k: [] for k in w.SCHEMA_PATHS}
        w.emit_trade_context(TradeContextRow(
            order_id="oid-A", ticker="X", side="buy",
            requested_qty=1, requested_px=1.0, submit_ts=_now(),
            terminal_filled_qty=0,
        ))
        w.emit_bar_context(BarContextRow(
            position_id="pos-A", ticker="X", entry_ts=_now(),
            entry_bar_open_ts=_now(), entry_bar_open=1.0,
            entry_bar_high=1.1, entry_bar_low=0.9, entry_bar_close=1.0,
            entry_bar_volume=100, our_q_shares=10,
            q_over_v_tau=0.10, bar_data_quality="complete",
        ))
        # Flush (sync variant)
        counts = w.flush_all_sync(reason="test")
        assert counts["trade_context"] == 1
        assert counts["bar_context"] == 1
        # The other two schemas had empty buffers → 0 written
        assert counts["child_fill_ticks"] == 0
        assert counts["cohort_registry"] == 0

    def test_flush_to_existing_partition_appends(
        self, sync_writer: InstrumentationWriter,
    ) -> None:
        """Writing twice in a session should append, not overwrite."""
        w = sync_writer
        w.ring_size = 10
        w._buffers = {k: [] for k in w.SCHEMA_PATHS}
        # First batch
        w.emit_trade_context(TradeContextRow(
            order_id="A", ticker="X", side="buy",
            requested_qty=1, requested_px=1.0, submit_ts=_now(),
            terminal_filled_qty=0,
        ))
        w.flush_all_sync(reason="batch1")
        # Second batch
        w.emit_trade_context(TradeContextRow(
            order_id="B", ticker="X", side="buy",
            requested_qty=2, requested_px=2.0, submit_ts=_now(),
            terminal_filled_qty=0,
        ))
        w.flush_all_sync(reason="batch2")
        df = pd.read_parquet(w._output_path("trade_context"))
        assert len(df) == 2
        assert set(df["order_id"]) == {"A", "B"}

    def test_partition_path_uses_session_date(
        self, sync_writer: InstrumentationWriter,
    ) -> None:
        path = sync_writer._partition_dir("trade_context")
        assert path.name == "session_date=2026-04-25"

    @pytest.mark.asyncio
    async def test_async_flush_all_works(
        self, sync_writer: InstrumentationWriter,
    ) -> None:
        sync_writer.ring_size = 10
        sync_writer._buffers = {k: [] for k in sync_writer.SCHEMA_PATHS}
        sync_writer.emit_trade_context(TradeContextRow(
            order_id="A", ticker="X", side="buy",
            requested_qty=1, requested_px=1.0, submit_ts=_now(),
            terminal_filled_qty=0,
        ))
        counts = await sync_writer.flush_all(reason="test")
        assert counts["trade_context"] == 1


# ── 4. Lifecycle integration test ───────────────────────────────


class TestFullOrderLifecycleIntegration:
    """Mock a complete order lifecycle: entry submit → partial fill →
    full fill → BAR-1 entry recorded → cohort match recorded → terminal.
    Asserts every schema writes correctly + round-trips back."""

    def test_full_lifecycle_writes_all_four_schemas(
        self, tmp_path: Path,
    ) -> None:
        w = InstrumentationWriter(
            base_dir=tmp_path / "instr", session_date="2026-04-25",
            ring_size=10,
        )
        order_id = "lifecycle-oid"
        position_id = order_id  # spec: position_id = entry order_id
        cohort_id = "cohort-uuid-A"
        t0 = _now()

        # Step 1 — submit (alpaca_executor.submit_oto_order)
        w.emit_trade_context(TradeContextRow(
            order_id=order_id, ticker="MAAS", side="buy",
            requested_qty=200, requested_px=2.10, submit_ts=t0,
            submit_nbbo_bid=2.09, submit_nbbo_ask=2.11,
            terminal_status="pending", terminal_filled_qty=0,
        ))

        # Step 2 — partial fill 1 (child fill from REST get_orders.legs)
        w.emit_child_fill(ChildFillRow(
            parent_order_id=order_id,
            child_fill_ts=t0 + timedelta(seconds=2),
            qty=100, price=2.10, cumulative_filled_qty=100,
        ))

        # Step 3 — partial fill 2 (terminal)
        w.emit_child_fill(ChildFillRow(
            parent_order_id=order_id,
            child_fill_ts=t0 + timedelta(seconds=4),
            qty=100, price=2.11, cumulative_filled_qty=200,
        ))

        # Step 4 — terminal trade context update (bridge._poll_for_terminal_fill)
        # MVP: emit a fresh TradeContextRow with terminal columns; spec
        # acknowledges the row update happens — implementation detail
        # is to write the final state. Append-on-flush handles dedup-or-not
        # at read time.
        w.emit_trade_context(TradeContextRow(
            order_id=order_id, ticker="MAAS", side="buy",
            requested_qty=200, requested_px=2.10, submit_ts=t0,
            submit_nbbo_bid=2.09, submit_nbbo_ask=2.11,
            first_fill_ts=t0 + timedelta(seconds=2),
            first_fill_nbbo_bid=2.09, first_fill_nbbo_ask=2.11,
            terminal_ts=t0 + timedelta(seconds=4),
            terminal_nbbo_bid=2.10, terminal_nbbo_ask=2.12,
            terminal_status="filled", terminal_filled_qty=200,
        ))

        # Step 5 — bar_context emitted from bridge.execute_verdict
        w.emit_bar_context(BarContextRow(
            position_id=position_id, ticker="MAAS", entry_ts=t0 + timedelta(seconds=4),
            entry_bar_open_ts=t0,
            entry_bar_open=2.10, entry_bar_high=2.18, entry_bar_low=2.08,
            entry_bar_close=2.15,
            entry_bar_volume=3000, our_q_shares=200,
            q_over_v_tau=200 / 3000, bar_data_quality="complete",
        ))

        # Step 6 — cohort match recorded
        w.emit_cohort_registry(CohortRow(
            cohort_id=cohort_id, traded_ticker="MAAS", cohort_ticker="ELSE",
            match_dt=t0 + timedelta(seconds=4),
            match_features={
                "catalyst_type": "earnings_beat",
                "market_cap_bucket": "micro",
            },
            cohort_entry_ref_px=1.85, cohort_data_quality="partial",
        ))

        # Flush everything
        counts = w.flush_all_sync(reason="lifecycle-test")
        # Assert on the four schemas this lifecycle exercises rather than
        # freezing the writer's full schema set — later docs add schemas
        # (e.g. decision_row) that this test deliberately emits nothing for.
        expected = {
            "trade_context": 2,
            "bar_context": 1,
            "child_fill_ticks": 2,
            "cohort_registry": 1,
        }
        for schema, n in expected.items():
            assert counts[schema] == n, f"{schema}: expected {n}, got {counts[schema]}"
        for schema, n in counts.items():
            if schema not in expected:
                assert n == 0, f"unexercised schema {schema} flushed {n} rows"

        # Round-trip every partition
        df_tc = pd.read_parquet(w._output_path("trade_context"))
        df_bc = pd.read_parquet(w._output_path("bar_context"))
        df_cf = pd.read_parquet(w._output_path("child_fill_ticks"))
        df_co = pd.read_parquet(w._output_path("cohort_registry"))

        assert len(df_tc) == 2
        # Final state row has terminal_status="filled"
        terminal_row = df_tc[df_tc["terminal_status"] == "filled"].iloc[0]
        assert terminal_row["terminal_filled_qty"] == 200
        assert terminal_row["first_fill_ts"] is not None

        assert len(df_bc) == 1
        assert df_bc.iloc[0]["our_q_shares"] == 200
        assert df_bc.iloc[0]["entry_bar_volume"] == 3000

        assert len(df_cf) == 2
        # Cumulative filled tracks correctly
        assert sorted(df_cf["cumulative_filled_qty"].tolist()) == [100, 200]
        # Sum of child fill qty = parent terminal_filled_qty
        assert df_cf["qty"].sum() == terminal_row["terminal_filled_qty"]

        assert len(df_co) == 1
        assert df_co.iloc[0]["cohort_ticker"] == "ELSE"

        # Health snapshot — zero D261 failures on a clean lifecycle
        snap = w.health_snapshot()
        assert all(v == 0 for v in snap["d261_failures"].values())
