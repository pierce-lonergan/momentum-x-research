"""Tests for scripts/convert_bars_to_arena_parquet.py — round-trip
identity for OHLCV+timestamp, schema match against arena's loader,
and graceful handling of malformed inputs.

Discipline (per docs/research-log/58_arena_prod_parity_plan.md §3): the
recording → parquet → arena pipeline must preserve OHLCV bit-for-bit
within float precision. A round-trip mismatch silently corrupts every
arena replay; pin the invariants here."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip module if pandas isn't installed (CI debt — gap register AZ)
pd = pytest.importorskip("pandas")

import sys
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import convert_bars_to_arena_parquet as conv  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def lidr_recording() -> dict:
    """A representative LIDR recording — first 3 bars from today's tape
    (data/bar_recordings/2026-04-28/LIDR.json), values pinned for
    regression detection."""
    return {
        "ticker": "LIDR",
        "date": "2026-04-28",
        "bars": [
            {
                "timestamp": "2026-04-28T13:30:00Z",
                "open": 2.2, "high": 2.24, "low": 2.2,
                "close": 2.225, "volume": 49140, "vwap": 2.213199,
            },
            {
                "timestamp": "2026-04-28T13:31:00Z",
                "open": 2.23, "high": 2.25, "low": 2.23,
                "close": 2.2474, "volume": 29629, "vwap": 2.246821,
            },
            {
                "timestamp": "2026-04-28T13:32:00Z",
                "open": 2.24, "high": 2.26, "low": 2.238,
                "close": 2.2595, "volume": 8836, "vwap": 2.249829,
            },
        ],
    }


@pytest.fixture
def temp_io(tmp_path) -> tuple[Path, Path]:
    """Yield (input_dir, output_dir) under a temp tree."""
    input_dir = tmp_path / "bar_recordings"
    output_dir = tmp_path / "arena_historical"
    input_dir.mkdir()
    output_dir.mkdir()
    return input_dir, output_dir


def _write_recording(input_dir: Path, recording: dict) -> Path:
    day_dir = input_dir / recording["date"]
    day_dir.mkdir(exist_ok=True)
    path = day_dir / f"{recording['ticker']}.json"
    path.write_text(json.dumps(recording), encoding="utf-8")
    return path


# ── Round-trip identity ─────────────────────────────────────────────


def test_round_trip_preserves_ohlcv_bit_for_bit(lidr_recording, temp_io):
    """The CRITICAL invariant: recording → parquet → load returns
    identical OHLCV + timestamp for every bar. Any drift here silently
    corrupts every replay downstream."""
    input_dir, out_dir = temp_io
    _write_recording(input_dir, lidr_recording)

    converted, skipped = conv.convert_one_date(
        date="2026-04-28", input_dir=input_dir, out_dir=out_dir,
        dry_run=False,
    )
    assert converted == 1
    assert skipped == 0

    target = out_dir / "LIDR" / "2026-04-28.parquet"
    assert target.exists()

    df = pd.read_parquet(target)
    assert len(df) == 3
    # Verify each bar matches the source
    for i, src_bar in enumerate(lidr_recording["bars"]):
        row = df.iloc[i]
        assert str(row["t"]) == src_bar["timestamp"], f"bar {i} timestamp drift"
        assert float(row["o"]) == src_bar["open"], f"bar {i} open drift"
        assert float(row["h"]) == src_bar["high"], f"bar {i} high drift"
        assert float(row["l"]) == src_bar["low"], f"bar {i} low drift"
        assert float(row["c"]) == src_bar["close"], f"bar {i} close drift"
        assert int(row["v"]) == src_bar["volume"], f"bar {i} volume drift"
        assert float(row["vw"]) == src_bar["vwap"], f"bar {i} vwap drift"


def test_arena_data_engine_loads_converted_parquet(lidr_recording, temp_io):
    """End-to-end: after conversion, arena's _load_parquet_bars (the
    method that actually feeds simulations) reads our output without
    error and produces Bar objects with matching values. This is the
    test that pins the ENTIRE conversion contract — if arena's parser
    changes, it fails here loudly."""
    sys.path.insert(0, str(REPO_ROOT / "mx-arena"))
    from datetime import datetime, timezone
    from arena.data_engine import DataEngine, Bar  # type: ignore
    from arena.clock import SimClock, ClockMode  # type: ignore

    input_dir, out_dir = temp_io
    _write_recording(input_dir, lidr_recording)
    conv.convert_one_date(
        date="2026-04-28", input_dir=input_dir, out_dir=out_dir,
        dry_run=False,
    )

    clock = SimClock(
        start=datetime(2026, 4, 28, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc),
        mode=ClockMode.REPLAY,
    )
    engine = DataEngine(clock=clock, historical_dir=str(out_dir))
    bars = engine._load_parquet_bars("LIDR", "2026-04-28")
    assert bars is not None, (
        "arena's _load_parquet_bars returned None — converted parquet "
        "is not at the path arena searches. Fix the converter's output "
        "layout."
    )
    assert len(bars) == 3
    # Spot-check first bar
    first: Bar = bars[0]
    assert first.timestamp == "2026-04-28T13:30:00Z"
    assert first.open == 2.2
    assert first.close == 2.225
    assert first.volume == 49140


# ── Sorting invariant ───────────────────────────────────────────────


def test_output_is_sorted_chronologically(temp_io):
    """If the recording has out-of-order bars (network reorder, late
    bars), the output parquet must be chronologically sorted. Arena
    re-sorts on load, but doing it once at conversion time is cheaper
    and more debuggable."""
    input_dir, out_dir = temp_io
    out_of_order = {
        "ticker": "TEST", "date": "2026-04-28",
        "bars": [
            {"timestamp": "2026-04-28T13:32:00Z", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 100, "vwap": 3},
            {"timestamp": "2026-04-28T13:30:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100, "vwap": 1},
            {"timestamp": "2026-04-28T13:31:00Z", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 100, "vwap": 2},
        ],
    }
    _write_recording(input_dir, out_of_order)
    conv.convert_one_date(date="2026-04-28", input_dir=input_dir, out_dir=out_dir, dry_run=False)
    df = pd.read_parquet(out_dir / "TEST" / "2026-04-28.parquet")
    timestamps = list(df["t"])
    assert timestamps == sorted(timestamps), (
        f"Output not sorted chronologically: {timestamps}"
    )


# ── Malformed inputs ────────────────────────────────────────────────


def test_malformed_json_is_skipped_not_crashed(temp_io, caplog):
    """A corrupt JSON file in the input dir must NOT crash the batch.
    The converter logs a warning and proceeds. Operator can grep the
    log for 'skip' to find data-quality issues."""
    input_dir, out_dir = temp_io
    day_dir = input_dir / "2026-04-28"
    day_dir.mkdir()
    (day_dir / "BROKEN.json").write_text("{not valid json", encoding="utf-8")
    converted, skipped = conv.convert_one_date(
        date="2026-04-28", input_dir=input_dir, out_dir=out_dir,
        dry_run=False,
    )
    assert converted == 0
    assert skipped == 1
    assert any("skip" in rec.message.lower() for rec in caplog.records)


def test_missing_required_field_drops_only_that_bar(temp_io):
    """One malformed bar in a file shouldn't drop the whole file —
    only the bad bar. The rest convert and the warning log surfaces
    the count."""
    input_dir, out_dir = temp_io
    mixed = {
        "ticker": "TEST", "date": "2026-04-28",
        "bars": [
            {"timestamp": "2026-04-28T13:30:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100, "vwap": 1},
            {"timestamp": "2026-04-28T13:31:00Z", "high": 2, "low": 2, "close": 2, "volume": 100, "vwap": 2},  # missing open
            {"timestamp": "2026-04-28T13:32:00Z", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 100, "vwap": 3},
        ],
    }
    _write_recording(input_dir, mixed)
    conv.convert_one_date(date="2026-04-28", input_dir=input_dir, out_dir=out_dir, dry_run=False)
    df = pd.read_parquet(out_dir / "TEST" / "2026-04-28.parquet")
    # 2 of 3 bars survived
    assert len(df) == 2
    assert list(df["t"]) == [
        "2026-04-28T13:30:00Z",
        "2026-04-28T13:32:00Z",
    ]


def test_filename_date_mismatch_is_skipped(temp_io, caplog):
    """If the JSON file's `date` field doesn't match the directory it
    sits in (e.g., someone moved files manually), treat as suspect and
    skip with a warning. Don't write inconsistent data into arena."""
    input_dir, out_dir = temp_io
    day_dir = input_dir / "2026-04-28"
    day_dir.mkdir()
    (day_dir / "WRONG.json").write_text(
        json.dumps({"ticker": "WRONG", "date": "2026-04-27", "bars": []}),
        encoding="utf-8",
    )
    converted, skipped = conv.convert_one_date(
        date="2026-04-28", input_dir=input_dir, out_dir=out_dir,
        dry_run=False,
    )
    assert converted == 0
    assert skipped == 1


# ── Idempotency ─────────────────────────────────────────────────────


def test_re_running_overwrites_cleanly(lidr_recording, temp_io):
    """Re-running the converter must produce identical output. No
    accidental append, no half-write."""
    input_dir, out_dir = temp_io
    _write_recording(input_dir, lidr_recording)
    for _ in range(2):
        conv.convert_one_date(date="2026-04-28", input_dir=input_dir, out_dir=out_dir, dry_run=False)
    df = pd.read_parquet(out_dir / "LIDR" / "2026-04-28.parquet")
    assert len(df) == 3  # Not 6 (would indicate append)


# ── Dry-run safety ──────────────────────────────────────────────────


def test_dry_run_does_not_write(lidr_recording, temp_io):
    """--dry-run must NOT write any parquet files. Used for safe
    operator inspection of large historical conversions."""
    input_dir, out_dir = temp_io
    _write_recording(input_dir, lidr_recording)
    converted, _ = conv.convert_one_date(
        date="2026-04-28", input_dir=input_dir, out_dir=out_dir,
        dry_run=True,
    )
    assert converted == 1
    # No parquet was actually written
    assert not (out_dir / "LIDR").exists()
