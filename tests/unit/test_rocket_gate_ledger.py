"""doc 284 — rocket-gate Stage-2 forward ledger: the FROZEN gate predicate + retro flag.

Covers: UTC->ET hour conversion (EDT and EST), the strict rvol>100 threshold, missing/unparseable
fields -> excluded, both candidate-dict shapes ('ts' and raw-features 'timestamp'), the retro flag
boundary at FORWARD_START, and a tripwire on the frozen prereg constants (a re-tune fails HERE
before it can silently bend the gate — doc 284 §2/§4: any re-tune = automatic kill).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import _doc284_rocket_gate_ledger as L  # noqa: E402


class TestGatePredicate:
    def test_gated_edt_13utc_is_9et(self):
        # July (EDT): 13:45 UTC == 9:45 ET -> in the hour-9 window
        assert L.gate_predicate({"rvol": 150.0, "ts": "2026-07-02T13:45:00+00:00"}) is True

    def test_hour_10_et_excluded(self):
        # July (EDT): 14:05 UTC == 10:05 ET -> outside
        assert L.gate_predicate({"rvol": 150.0, "ts": "2026-07-02T14:05:00+00:00"}) is False

    def test_est_winter_14utc_is_9et(self):
        # January (EST): 14:30 UTC == 9:30 ET -> gated (the 13:xx rule is EDT-only; tz math must hold)
        assert L.gate_predicate({"rvol": 150.0, "ts": "2026-01-15T14:30:00+00:00"}) is True

    def test_est_winter_13utc_is_8et_excluded(self):
        # January (EST): 13:30 UTC == 8:30 ET -> NOT hour 9
        assert L.gate_predicate({"rvol": 150.0, "ts": "2026-01-15T13:30:00+00:00"}) is False

    def test_z_suffix_timestamp_parses(self):
        assert L.gate_predicate({"rvol": 543.8, "ts": "2026-07-02T13:30:33Z"}) is True

    def test_rvol_exactly_100_excluded(self):
        # STRICT >: the boundary itself does not gate
        assert L.gate_predicate({"rvol": 100.0, "ts": "2026-07-02T13:45:00+00:00"}) is False

    def test_rvol_just_above_100_included(self):
        assert L.gate_predicate({"rvol": 100.01, "ts": "2026-07-02T13:45:00+00:00"}) is True

    def test_rvol_below_excluded(self):
        assert L.gate_predicate({"rvol": 0.95, "ts": "2026-07-02T13:30:32+00:00"}) is False

    def test_missing_rvol_excluded(self):
        assert L.gate_predicate({"ts": "2026-07-02T13:45:00+00:00"}) is False

    def test_none_rvol_excluded(self):
        assert L.gate_predicate({"rvol": None, "ts": "2026-07-02T13:45:00+00:00"}) is False

    def test_unparseable_rvol_excluded(self):
        assert L.gate_predicate({"rvol": "n/a", "ts": "2026-07-02T13:45:00+00:00"}) is False

    def test_missing_timestamp_excluded(self):
        assert L.gate_predicate({"rvol": 150.0}) is False

    def test_unparseable_timestamp_excluded(self):
        assert L.gate_predicate({"rvol": 150.0, "ts": "not-a-time"}) is False

    def test_raw_features_row_timestamp_key(self):
        # gate_predicate accepts the raw features row shape too ('timestamp' instead of 'ts')
        assert L.gate_predicate({"rvol": 543.8, "timestamp": "2026-07-02T13:30:33.349215+00:00"}) is True

    def test_naive_timestamp_treated_as_utc(self):
        # features timestamps are UTC; a naive one must be read as UTC, not local
        assert L.gate_predicate({"rvol": 150.0, "ts": "2026-07-02T13:45:00"}) is True


class TestRetroFlag:
    def test_history_is_retro(self):
        assert L.is_retro("2026-04-14") is True
        assert L.is_retro("2026-07-02") is True

    def test_freeze_day_is_retro(self):
        # the prereg freezes on 2026-07-05; that day is still seeding
        assert L.is_retro("2026-07-05") is True

    def test_forward_window_not_retro(self):
        assert L.is_retro("2026-07-06") is False
        assert L.is_retro("2026-12-01") is False


class TestFrozenConstants:
    """Tripwire (doc 284 §2/§4): re-tuning ANY of these = automatic kill of the gate.
    If this test is failing, the prereg has been violated — do not 'fix' the test."""

    def test_prereg_constants_unchanged(self):
        assert L.FORWARD_START == "2026-07-06"
        assert L.RVOL_MIN == 100.0
        assert L.ENTRY_HOUR_ET == 9
        assert L.STOP_PCT == 0.15
        assert L.EXIT_COST == 0.005
        assert L.NOTIONAL == 8000.0
        assert L.BOOT_B == 10_000
        assert L.BOOT_SEED == 284
        assert L.N_MIN == 30
        assert L.N_KILL == 60
        assert L.MAX_FIRST_BAR_OFF_MIN == 390.0
        assert L.MAX_UNMEASURED_FRAC == 0.20
        assert L.SUBMIT_OFF_MIN == 45 / 60.0
        assert L.FILL_WINDOW_MIN == 2.0


def _row(date, delta, n_gated=10, n_unm=0, hole=False, retro=False):
    """Minimal ledger row for acceptance() tests."""
    return {"date": date, "n_gated": n_gated, "n_filled": 0 if hole else n_gated - n_unm,
            "n_unmeasured": n_unm, "delta_hold_vs_bar1_usd": delta,
            "d_stop15_vs_bar1_usd": 0.0, "no_bar_data": hole, "retro": retro}


class TestAcceptance:
    """The verdict function itself (doc 284 §4) — forward-only, day-blocked, coverage-honest."""

    def test_empty_ledger_pending(self):
        assert L.acceptance([])["status"] == "PENDING-COLLECTION"

    def test_retro_rows_are_not_evidence(self):
        # 40 wildly positive RETRO sessions must move nothing: n stays 0
        rows = [_row(f"2026-05-{i:02d}", 50_000.0, retro=True) for i in range(1, 29)]
        out = L.acceptance(rows)
        assert out["status"] == "PENDING-COLLECTION"
        assert out["n_gated_forward_sessions"] == 0

    def test_passes_at_n30_all_positive(self):
        rows = [_row(f"2026-08-{i:02d}", 500.0) for i in range(1, 31)]
        out = L.acceptance(rows)
        assert out["status"] == "PASSED"
        assert out["ci95_mean"][0] > 0

    def test_first_half_negative_blocks_pass(self):
        # both-chronological-halves rule: a back-loaded window is not a PASS
        rows = [_row(f"2026-08-{i:02d}", -10.0) for i in range(1, 16)]
        rows += [_row(f"2026-09-{i:02d}", 2000.0) for i in range(1, 16)]
        out = L.acceptance(rows)
        assert out["half1_total_usd"] < 0
        assert out["status"] == "FAILING-SO-FAR"

    def test_dead_at_kill_horizon(self):
        # 60 gated forward sessions alternating +/-: CI straddles 0 -> DEAD, no extension
        rows = [_row(f"2026-{8 + i // 28:02d}-{i % 28 + 1:02d}", 100.0 if i % 2 else -100.0)
                for i in range(60)]
        assert L.acceptance(rows)["status"] == "DEAD"

    def test_whole_session_holes_count_toward_coverage(self):
        # REGRESSION (validation 2026-07-05): a no_bar_data session is 100% unmeasured and must
        # weigh on the coverage fraction — 30 clean positives + 8 whole-session holes is 63%
        # unmeasured and CANNOT pass (previously reported frac 0.0 and PASSED).
        rows = [_row(f"2026-08-{i:02d}", 500.0) for i in range(1, 31)]
        rows += [_row(f"2026-09-{i:02d}", 0.0, n_gated=65, n_unm=65, hole=True) for i in range(1, 9)]
        out = L.acceptance(rows)
        assert out["unmeasured_ticket_frac"] == round(520 / 820, 4)  # reported frac is rounded
        assert out["status"] == "BLOCKED-COVERAGE"

    def test_partial_unmeasured_blocks_pass(self):
        # 30% of gated tickets unmeasured inside otherwise-clean sessions -> BLOCKED-COVERAGE
        rows = [_row(f"2026-08-{i:02d}", 500.0, n_gated=10, n_unm=3) for i in range(1, 31)]
        assert L.acceptance(rows)["status"] == "BLOCKED-COVERAGE"

    def test_unmeasured_at_exactly_20pct_does_not_block(self):
        # the clause is STRICTLY greater-than MAX_UNMEASURED_FRAC
        rows = [_row(f"2026-08-{i:02d}", 500.0, n_gated=10, n_unm=2) for i in range(1, 31)]
        out = L.acceptance(rows)
        assert abs(out["unmeasured_ticket_frac"] - 0.20) < 1e-9
        assert out["status"] == "PASSED"

    def test_hole_sessions_excluded_from_delta_sample(self):
        # a hole neither adds a session to n nor a $0 to the mean; it is reported as a hole
        rows = [_row(f"2026-08-{i:02d}", 500.0) for i in range(1, 31)]
        rows += [_row("2026-09-01", 0.0, n_gated=5, n_unm=5, hole=True)]
        out = L.acceptance(rows)
        assert out["n_gated_forward_sessions"] == 30
        assert out["coverage_holes"] == ["2026-09-01"]
        assert out["mean_delta_usd"] == 500.0
