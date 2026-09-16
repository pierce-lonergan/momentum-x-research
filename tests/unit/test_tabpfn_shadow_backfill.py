"""Doc 163 Gate 3 -- tabpfn_shadow_backfill_returns.

Manufactures a 'stale unlabeled' shadow parquet (3 rows: 2 ancient with
NaN realized_ret_t5, 1 recent with NaN that should NOT be touched) and
a tiny aftermath_strat parquet, runs the backfill, and asserts:

  - The 2 ancient rows pick up their ret_t5 from aftermath_strat.
  - The 1 recent row stays NaN (within the days_buffer window).
  - A 4th row whose ticker is missing from aftermath_strat stays NaN.
  - Re-running the script is a no-op (idempotency).

This is the binary pass test for Gate 3 of doc 163.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture()
def fake_aftermath(tmp_path: Path) -> Path:
    """Tiny aftermath_strat parquet covering 3 known (ticker, d0)."""
    af = pd.DataFrame({
        "ticker": ["AAOX", "IONL", "DEAD"],
        "d0": pd.to_datetime(["2026-04-25", "2026-04-25", "2026-04-25"]),
        "ret_t5": [0.1234, -0.0567, 0.9999],
        "open": [5.0, 6.0, 7.0],
        "volume": [200_000, 200_000, 200_000],
        "ca_flag": ["clean", "clean", "clean"],
    })
    out = tmp_path / "aftermath_strat.parquet"
    af.to_parquet(out, compression="zstd")
    return out


@pytest.fixture()
def fake_shadow_dir(tmp_path: Path) -> Path:
    """Manufactured shadow parquet with mixed staleness + a missing-ticker
    row that should remain NaN even after backfill."""
    sd = tmp_path / "tabpfn_shadow"
    sd.mkdir()
    # File 1: stale d0 (ancient enough that ret_t5 should exist)
    stale = pd.DataFrame({
        "ticker": ["AAOX", "IONL", "WYHG"],
        "d0": pd.to_datetime(["2026-04-25", "2026-04-25", "2026-04-25"]),
        "tabpfn_pred": np.array([0.05, -0.02, 0.01], dtype="float32"),
        "n_train_rows_used": [2500, 2500, 2500],
        "tabpfn_quintile_per_day": [4, 0, 2],
        "realized_ret_t5": [np.nan, np.nan, np.nan],
        "scoring_mode": ["live", "live", "live"],
        "shadow_run_at_utc": pd.to_datetime(
            ["2026-04-25T12:00:00Z"] * 3, utc=True),
    })
    stale.to_parquet(sd / "2026-04-25.parquet", compression="zstd")

    # File 2: recent d0 (today-ish): backfill must skip it
    recent = pd.DataFrame({
        "ticker": ["AAOX"],
        "d0": pd.to_datetime(["2026-05-11"]),
        "tabpfn_pred": np.array([0.03], dtype="float32"),
        "n_train_rows_used": [2500],
        "tabpfn_quintile_per_day": [4],
        "realized_ret_t5": [np.nan],
        "scoring_mode": ["live"],
        "shadow_run_at_utc": pd.to_datetime(["2026-05-11T12:00:00Z"], utc=True),
    })
    recent.to_parquet(sd / "2026-05-11.parquet", compression="zstd")
    return sd


def _run_backfill(shadow_dir: Path, aftermath_path: Path,
                  today: pd.Timestamp, days_buffer: int = 7,
                  dry_run: bool = False) -> tuple[int, int]:
    """Helper that calls backfill_file on every parquet in shadow_dir,
    pointing at the manufactured aftermath. Returns (total_eligible,
    total_filled)."""
    from tabpfn_shadow_backfill_returns import backfill_file
    total_elig = total_filled = 0
    for fp in sorted(shadow_dir.glob("*.parquet")):
        _, n_elig, n_filled = backfill_file(
            fp,
            today=today,
            days_buffer=days_buffer,
            dry_run=dry_run,
            aftermath_path=aftermath_path,
        )
        total_elig += n_elig
        total_filled += n_filled
    return total_elig, total_filled


# Today is 2026-05-12: ancient stale file (2026-04-25) is 17 days old (>7d
# buffer), the recent file (2026-05-11) is 1 day old (within buffer).


def test_stale_rows_get_filled_from_aftermath(fake_shadow_dir, fake_aftermath):
    """Gate 3 binary pass: stale NaN rows pick up ret_t5 correctly."""
    today = pd.Timestamp("2026-05-12")
    n_elig, n_filled = _run_backfill(fake_shadow_dir, fake_aftermath, today)
    # 3 stale rows + 0 recent (skipped by buffer); 2 of 3 stale have a
    # match in aftermath ('AAOX', 'IONL'); 'WYHG' is missing → stays NaN.
    assert n_elig == 3, f"expected 3 eligible rows; got {n_elig}"
    assert n_filled == 2, f"expected 2 fills; got {n_filled}"

    # Verify written values
    out = pd.read_parquet(fake_shadow_dir / "2026-04-25.parquet")
    out_by_t = out.set_index("ticker")["realized_ret_t5"]
    assert out_by_t["AAOX"] == pytest.approx(0.1234)
    assert out_by_t["IONL"] == pytest.approx(-0.0567)
    # WYHG had no match in aftermath -> remains NaN
    assert pd.isna(out_by_t["WYHG"])


def test_recent_rows_left_alone(fake_shadow_dir, fake_aftermath):
    """The 2026-05-11 file must be untouched (within 7d buffer of today)."""
    today = pd.Timestamp("2026-05-12")
    _run_backfill(fake_shadow_dir, fake_aftermath, today)
    recent = pd.read_parquet(fake_shadow_dir / "2026-05-11.parquet")
    assert recent["realized_ret_t5"].isna().all(), (
        "recent (within-buffer) file must NOT be backfilled — its label "
        "isn't really available yet"
    )


def test_idempotent_second_run_is_noop(fake_shadow_dir, fake_aftermath):
    """Re-running after a successful backfill yields zero new fills."""
    today = pd.Timestamp("2026-05-12")
    _run_backfill(fake_shadow_dir, fake_aftermath, today)
    n_elig2, n_filled2 = _run_backfill(fake_shadow_dir, fake_aftermath, today)
    # After first run: AAOX/IONL filled, WYHG still NaN. Second run still
    # sees WYHG as eligible (NaN + stale) but it remains unfillable.
    assert n_elig2 == 1, f"expected 1 still-NaN eligible; got {n_elig2}"
    assert n_filled2 == 0, "second pass must not fill anything new"


def test_dry_run_does_not_modify(fake_shadow_dir, fake_aftermath):
    """--dry-run never writes; values stay NaN."""
    today = pd.Timestamp("2026-05-12")
    _run_backfill(fake_shadow_dir, fake_aftermath, today, dry_run=True)
    out = pd.read_parquet(fake_shadow_dir / "2026-04-25.parquet")
    assert out["realized_ret_t5"].isna().all(), (
        "dry-run must not modify on disk"
    )


def test_realized_ret_t5_dtype_is_float64(fake_shadow_dir, fake_aftermath):
    """Backfilled column must remain float64 (matches live writer schema)."""
    today = pd.Timestamp("2026-05-12")
    _run_backfill(fake_shadow_dir, fake_aftermath, today)
    out = pd.read_parquet(fake_shadow_dir / "2026-04-25.parquet")
    assert out["realized_ret_t5"].dtype == np.float64
