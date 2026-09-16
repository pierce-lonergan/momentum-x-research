"""Bayesian (eta_perm, gamma) EOD runner — wires fit_eta_gamma + the four
trigger gates into the EOD pipeline.

Per items 12-15 of the 2026-04-25 next-actions list + the Bayesian
estimator ship (commit `0cee9d4`, tag `v2.2-bayesian-estimator-armed`).
This module bridges the gap between "estimator exists" and "estimator
answers the capacity question against captured Phase 0 data."

Pipeline:
  1. Read child_fill_ticks Parquet for the session  (one row per fill)
  2. Read bar_context Parquet for the session       (one row per entry; q_over_v_tau)
  3. Join: each fill links to its position via parent_order_id; each
     position has q/v from bar_context. Realized slippage is per-fill:
       slip = (fill_price - reference_price) / reference_price
     For now reference_price = entry_bar_open (stable; v2: VWAP-of-bar)
  4. fit_eta_gamma() on the (q/v, slip) tuples
  5. Run all four gates D256-D259
  6. Persist a daily report at data/reports/bayesian_eta_gamma_<date>.json

Hook site: `main.py` EOD section (after BOCPD refit check, before
EOD failsafes). Non-fatal — a Bayesian-runner failure NEVER blocks
session close.

Gracefully no-ops when:
  - Phase 0 partitions don't exist yet (first session, no captured data)
  - Sample size below MIN_OBSERVATIONS (default 30)
  - PyMC import fails (estimator unavailable)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


MIN_OBSERVATIONS_FOR_FIT: int = 30
"""Minimum number of (q/v, slip) tuples before the estimator runs.
Below this, the runner emits an INFO log and skips. Tunable; matches
the v2.2 spec §13.2 calibration sample-size floor."""


# ── Data loading ────────────────────────────────────────────────


def _load_phase0_partition(
    schema: str, base_dir: Path, session_date: str,
) -> "Any":
    """Load one Phase 0 schema's Parquet for the given session_date.
    Returns DataFrame or None if missing/unreadable."""
    path = base_dir / schema / f"session_date={session_date}" / {
        "trade_context": "orders.parquet",
        "bar_context": "entries.parquet",
        "child_fill_ticks": "fills.parquet",
        "cohort_registry": "cohorts.parquet",
    }[schema]
    if not path.exists():
        return None
    try:
        import pandas as pd
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("Bayesian EOD: failed to read %s: %s", path, e)
        return None


def build_q_slippage_tuples(
    df_fills: "Any", df_bars: "Any",
) -> list[tuple[float, float]]:
    """Join fills to bar_context to produce (q/v_tau, realized_slippage) tuples.

    Realized slippage definition (v0.1):
      slip = (fill_price - entry_bar_open) / entry_bar_open

    Each row is one child-fill. Skips rows where:
      - parent_order_id missing
      - corresponding bar_context not found
      - q_over_v_tau or entry_bar_open <= 0
      - slippage value would be NaN/inf
    """
    if df_fills is None or df_bars is None:
        return []
    import math
    tuples: list[tuple[float, float]] = []
    # Build a lookup: position_id (= parent_order_id) -> (q_over_v_tau, entry_bar_open)
    bar_lookup: dict[str, tuple[float, float]] = {}
    for _, b_row in df_bars.iterrows():
        pid = b_row.get("position_id")
        if not pid:
            continue
        try:
            qv = float(b_row.get("q_over_v_tau") or 0)
            open_px = float(b_row.get("entry_bar_open") or 0)
            if qv > 0 and open_px > 0:
                bar_lookup[str(pid)] = (qv, open_px)
        except (TypeError, ValueError) as _e:
            logger.debug("Bayesian EOD: skipping bar row, bad parse: %s", _e)
            continue

    for _, f_row in df_fills.iterrows():
        parent = f_row.get("parent_order_id")
        if not parent or str(parent) not in bar_lookup:
            continue
        try:
            fill_px = float(f_row.get("price") or 0)
            qv, open_px = bar_lookup[str(parent)]
            if fill_px <= 0:
                continue
            slip = (fill_px - open_px) / open_px
            if not math.isfinite(slip):
                continue
            tuples.append((qv, slip))
        except (TypeError, ValueError, ZeroDivisionError) as _e:
            logger.debug("Bayesian EOD: skipping fill row, bad parse: %s", _e)
            continue

    return tuples


# ── Runner ──────────────────────────────────────────────────────


def run_eod_bayesian_fit(
    *,
    base_dir: Path | str = "data/instrumentation",
    session_date: str | None = None,
    report_dir: Path | str = "data/reports",
    min_observations: int = MIN_OBSERVATIONS_FOR_FIT,
    chains: int = 2,
    tune: int = 200,
    draws: int = 200,
) -> dict[str, Any]:
    """Read Phase 0 fills + bars, fit (eta_perm, gamma), run 4 gates,
    write daily report.

    Returns dict with:
      n_observations, posterior, gate_results, report_path, error
    """
    if session_date is None:
        session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = Path(base_dir)
    report_dir_p = Path(report_dir)

    df_fills = _load_phase0_partition("child_fill_ticks", base, session_date)
    df_bars = _load_phase0_partition("bar_context", base, session_date)

    if df_fills is None or df_bars is None:
        logger.info(
            "Bayesian EOD: Phase 0 partitions missing for %s — "
            "skipping (first session may not have captured yet)",
            session_date,
        )
        return {"error": "no_phase0_data", "n_observations": 0}

    tuples = build_q_slippage_tuples(df_fills, df_bars)
    n = len(tuples)
    logger.info(
        "Bayesian EOD: built %d (q/v, slippage) tuples from %d fills + %d bar rows",
        n, len(df_fills), len(df_bars),
    )

    if n < min_observations:
        logger.info(
            "Bayesian EOD: %d observations below floor %d — skipping fit "
            "(accumulate more sessions before calibration)",
            n, min_observations,
        )
        return {"error": "below_min_observations", "n_observations": n}

    try:
        from src.analysis.slippage_calibration import (
            eod_slippage_gates,
            fit_eta_gamma,
        )
    except ImportError as e:
        logger.error("Bayesian EOD: failed to import estimator: %s", e)
        return {"error": str(e), "n_observations": n}

    qs = [t[0] for t in tuples]
    slips = [t[1] for t in tuples]

    try:
        posterior = fit_eta_gamma(
            qs, slips,
            chains=chains, tune=tune, draws=draws,
            progressbar=False,
        )
    except Exception as e:
        logger.warning("Bayesian EOD: fit_eta_gamma raised: %s", e)
        return {"error": str(e), "n_observations": n}

    # Gates: D256, D258, D259 (D257 needs first/second halves — skipped MVP)
    # Compute residuals for D259: residual = slip_observed - slip_predicted
    residuals: list[float] = []
    try:
        for q_v, slip in tuples:
            pred = posterior.eta_mean * (q_v ** posterior.gamma_mean)
            residuals.append(slip - pred)
    except Exception as e:
        logger.debug("Bayesian EOD: residual computation failed: %s", e)
        residuals = []

    # Halt rate — placeholder until halt detection wires through Phase 0.
    # For MVP: halt rate is the proportion of rows where slip > 3 * sigma_edge
    # (extreme fills are halt-correlated). Estimator's eta_mean × gamma gives
    # us a noise scale; use Tukey's 3-sigma flag.
    n_halted = sum(1 for r in residuals if abs(r) > 3 * (posterior.eta_mean or 0.01))
    n_total = max(n, 1)

    gates = eod_slippage_gates(
        posterior,
        n_total_trades=n_total,
        n_halted_trades=n_halted,
        residuals=residuals,
    )

    # Persist report
    report_dir_p.mkdir(parents=True, exist_ok=True)
    report_path = report_dir_p / f"bayesian_eta_gamma_{session_date}.json"
    report = {
        "session_date": session_date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_observations": n,
        "posterior": {
            "eta_mean": posterior.eta_mean, "eta_std": posterior.eta_std,
            "eta_q05": posterior.eta_q05, "eta_q95": posterior.eta_q95,
            "gamma_mean": posterior.gamma_mean, "gamma_std": posterior.gamma_std,
            "gamma_q05": posterior.gamma_q05, "gamma_q95": posterior.gamma_q95,
            "n_chains": posterior.n_chains, "n_draws": posterior.n_draws,
        },
        "gates": [
            {
                "code": g.gate_code, "name": g.gate_name,
                "triggered": g.triggered, "measured_value": g.measured_value,
                "threshold": g.threshold, "detail": g.detail,
            }
            for g in gates
        ],
    }

    tmp_path = report_path.with_suffix(report_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)
        os.replace(tmp_path, report_path)
    except Exception as e:
        logger.error("Bayesian EOD: report write failed: %s", e)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # noqa: silent-handler — best-effort cleanup
                pass

    logger.info(
        "Bayesian EOD: posterior eta=%.4f sigma=%.4f gamma=%.4f sigma=%.4f n=%d → %s",
        posterior.eta_mean, posterior.eta_std,
        posterior.gamma_mean, posterior.gamma_std,
        n, report_path,
    )
    return {
        "error": None,
        "n_observations": n,
        "posterior": {
            "eta_mean": posterior.eta_mean,
            "gamma_mean": posterior.gamma_mean,
        },
        "gate_results": [
            {"code": g.gate_code, "triggered": g.triggered}
            for g in gates
        ],
        "report_path": str(report_path),
    }
