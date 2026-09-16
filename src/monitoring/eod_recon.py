"""
End-of-day reconciliation invariants — Track A item 3.

Per `25_bug_hunting_playbook.md` §3.2 + Track A item 3 in the
2026-04-23 next-actions list:

    "Add the three reconciliation invariants as standalone assertions
    right now — even before the daemon. assert internal_qty ==
    broker_filled_qty, assert internal_stop_price == broker_stop_price,
    assert abs(internal_equity - broker_equity) < 1.00 — wired into
    the existing EOD summary hook. This is a 30-minute change that
    catches D-class and R-class recurrence before the full daemon ships."

This is the cheap stopgap that catches Bug D, Bug R, Bug N-class
recurrence at the EOD horizon, while the full Tier-1 reconciliation
daemon (Track B Week 2) is being built. The daemon will eventually
run these same invariants every 30 seconds; this module runs them
once at session close.

D-codes used (per `26_d_code_registry.md`):
    D230 RECON_WARN       — soft (Tier-2): equity drift, stop drift on
                            position with no broker stop_order_id
    D231 RECON_HARD_BLOCK — hard (Tier-1): qty mismatch, stop mismatch
                            on position with broker-confirmed stop
    (D232 LETHAL is daemon-only; not appropriate for batch EOD checks
     because the session is already closing.)

Architecture:
    run_eod_invariants(client, position_manager) → dict[str, Any]

Returns a structured result dict with per-invariant pass/fail counts
plus the broker-reachable flag for downstream observability. Never
raises — all failures degrade gracefully.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Tolerance constants ────────────────────────────────────────────
# Equity drift is Tier-2 because intraday unrealized P&L can produce
# legitimate drift before EOD mark-to-market. Threshold $1.00 is the
# same value as `PNL_RECON_TOLERANCE_USD` in trade_journal.py.
EQUITY_DRIFT_TOLERANCE_USD: float = 1.00

# Stop-price drift threshold: 1 cent. Anything bigger is real
# divergence (the Bug R case was $32.44 vs $25.72 = $6.72 drift).
STOP_DRIFT_TOLERANCE_USD: float = 0.01


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Coerce broker numeric fields (often strings) to float."""
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


async def run_eod_invariants(
    *,
    client: Any,
    position_manager: Any,
    equity_tolerance_usd: float = EQUITY_DRIFT_TOLERANCE_USD,
    stop_tolerance_usd: float = STOP_DRIFT_TOLERANCE_USD,
) -> dict[str, Any]:
    """
    Run the three EOD reconciliation invariants and return a structured
    result. Always non-fatal.

    Invariants:
      1. QTY (Tier-1): for every (broker_position, internal_position) pair,
         broker_qty == internal_qty. Mismatch → D231.
      2. STOP (Tier-1 if stop_order_id present, else Tier-2): for every
         internal position with a broker-confirmed stop_order_id,
         internal.stop_loss == broker.stop_order.stop_price within
         stop_tolerance. Mismatch → D231 (or D230 for unconfirmed).
      3. EQUITY (Tier-2): |broker_equity - (starting + realized)| ≤
         equity_tolerance. Mismatch → D230. (Note: this is a coarse check
         because internal does not mark-to-market unrealized; production
         drift is acceptable up to the unrealized P&L magnitude.)

    Returns:
        {
          "broker_reachable": bool,
          "qty_drift_count": int,
          "stop_drift_count": int,
          "equity_within_tolerance": bool,
          "broker_equity": float | None,
          "internal_equity_estimate": float,
          "details": [...],          # per-violation breakdown
        }
    """
    result: dict[str, Any] = {
        "broker_reachable": True,
        "qty_drift_count": 0,
        "stop_drift_count": 0,
        "equity_within_tolerance": True,
        "broker_equity": None,
        "internal_equity_estimate": 0.0,
        "details": [],
    }

    # ── Fetch broker truth ─────────────────────────────────────────
    try:
        broker_positions = await client.get_positions()
    except Exception as e:
        logger.warning(
            "EOD RECON DEGRADED: get_positions raised (%s) — "
            "qty + stop invariants skipped this session",
            e,
        )
        result["broker_reachable"] = False
        broker_positions = []

    try:
        broker_orders = await client.get_orders(status="all", limit=500)
    except Exception as e:
        logger.warning(
            "EOD RECON DEGRADED: get_orders raised (%s) — stop invariant "
            "may be incomplete this session",
            e,
        )
        broker_orders = []

    try:
        broker_account = await client.get_account()
        result["broker_equity"] = _safe_float(broker_account.get("equity"))
    except Exception as e:
        logger.warning(
            "EOD RECON DEGRADED: get_account raised (%s) — equity "
            "invariant skipped this session",
            e,
        )
        result["broker_reachable"] = False

    # ── Build broker-side lookups ─────────────────────────────────
    broker_qty_by_sym: dict[str, int] = {}
    for bp in broker_positions or []:
        sym = (bp.get("symbol") or "").upper()
        if sym:
            broker_qty_by_sym[sym] = _safe_int(bp.get("qty"))

    broker_stop_by_oid: dict[str, float] = {}
    for bo in broker_orders or []:
        if (bo.get("type") or "").lower() == "stop" and (bo.get("status") or "").lower() in ("new", "accepted", "pending_new", "held"):
            oid = bo.get("id") or ""
            sp = _safe_float(bo.get("stop_price"))
            if oid and sp > 0:
                broker_stop_by_oid[oid] = sp

    # ── Internal-side lookups ─────────────────────────────────────
    internal_positions = list(getattr(position_manager, "open_positions", []) or [])

    # ── Invariant 1: QTY (Tier-1) ─────────────────────────────────
    for pos in internal_positions:
        ticker = getattr(pos, "ticker", "?")
        internal_qty = int(getattr(pos, "qty", 0) or 0)
        broker_qty = broker_qty_by_sym.get(ticker)
        if broker_qty is None:
            # Position in tracker but not at broker → Tier-1 violation
            logger.warning(
                "D231 RECON_HARD_BLOCK QTY %s: internal_qty=%d broker_qty=MISSING "
                "(position in tracker but not in broker get_positions). "
                "Bug D / Bug N regression possible.",
                ticker, internal_qty,
            )
            result["qty_drift_count"] += 1
            result["details"].append({
                "kind": "qty",
                "ticker": ticker,
                "internal": internal_qty,
                "broker": None,
                "delta": -internal_qty,
            })
        elif broker_qty != internal_qty:
            logger.warning(
                "D231 RECON_HARD_BLOCK QTY %s: internal_qty=%d broker_qty=%d "
                "delta=%+d. Bug D regression — internal tracker diverged "
                "from broker truth.",
                ticker, internal_qty, broker_qty, broker_qty - internal_qty,
            )
            result["qty_drift_count"] += 1
            result["details"].append({
                "kind": "qty",
                "ticker": ticker,
                "internal": internal_qty,
                "broker": broker_qty,
                "delta": broker_qty - internal_qty,
            })

    # Detect broker-side positions not in tracker (ghost positions)
    internal_syms = {getattr(p, "ticker", None) for p in internal_positions}
    for sym, b_qty in broker_qty_by_sym.items():
        if sym not in internal_syms and b_qty != 0:
            logger.warning(
                "D231 RECON_HARD_BLOCK QTY %s: internal_qty=MISSING broker_qty=%d "
                "(GHOST position at broker — D86 cleanup may be needed)",
                sym, b_qty,
            )
            result["qty_drift_count"] += 1
            result["details"].append({
                "kind": "qty_ghost",
                "ticker": sym,
                "internal": None,
                "broker": b_qty,
                "delta": b_qty,
            })

    # ── Invariant 2: STOP price (Tier-1 if stop_order_id confirmed)
    for pos in internal_positions:
        ticker = getattr(pos, "ticker", "?")
        internal_stop = float(getattr(pos, "stop_loss", 0) or 0)
        stop_oid = getattr(pos, "stop_order_id", "") or ""
        if not stop_oid:
            # No broker-confirmed stop. Per D56 sync labeling integrity,
            # this is a D230 (Tier-2 soft) — cannot reconcile without an
            # OID, but the absence itself is worth flagging.
            if internal_stop > 0:
                logger.warning(
                    "D230 RECON_WARN STOP %s: internal_stop=$%.2f but no "
                    "stop_order_id (cannot reconcile against broker; D56 "
                    "no-broker-stop case)",
                    ticker, internal_stop,
                )
                result["stop_drift_count"] += 1
                result["details"].append({
                    "kind": "stop_no_oid",
                    "ticker": ticker,
                    "internal": internal_stop,
                    "broker": None,
                })
            continue
        broker_stop = broker_stop_by_oid.get(stop_oid)
        if broker_stop is None:
            logger.warning(
                "D231 RECON_HARD_BLOCK STOP %s: internal claims stop_order_id=%s "
                "but no matching active stop found at broker. Stop may have "
                "been canceled, filled, or expired silently.",
                ticker, stop_oid[:8],
            )
            result["stop_drift_count"] += 1
            result["details"].append({
                "kind": "stop_oid_missing",
                "ticker": ticker,
                "stop_order_id": stop_oid,
            })
        elif abs(broker_stop - internal_stop) > stop_tolerance_usd:
            logger.warning(
                "D231 RECON_HARD_BLOCK STOP %s: internal_stop=$%.2f "
                "broker_stop=$%.2f delta=$%+.2f. Bug R regression — "
                "Phase-0/1 stop tightening may not have propagated to "
                "ManagedPosition.stop_loss.",
                ticker, internal_stop, broker_stop, broker_stop - internal_stop,
            )
            result["stop_drift_count"] += 1
            result["details"].append({
                "kind": "stop",
                "ticker": ticker,
                "internal": internal_stop,
                "broker": broker_stop,
                "delta": broker_stop - internal_stop,
            })

    # ── Invariant 3: EQUITY (Tier-2 soft) ─────────────────────────
    # Bug AN fix (2026-04-27, Tier 3 #11): the previous code did
    # `getattr(position_manager, "starting_equity", 0)` but
    # PositionManager only had `_starting_equity` (private convention)
    # — no public `starting_equity` attribute existed. The typo
    # always returned the default 0, making internal_equity_estimate
    # always $0, making `delta = broker_equity - 0 = broker_equity`
    # — always greater than the $1 tolerance. D230 RECON_WARN EQUITY
    # fired every 30s of every session for an unknown duration.
    #
    # The fix has two halves:
    #   1. Bug AN added a public `starting_equity` property to
    #      PositionManager (see position_manager.py).
    #   2. This caller now correctly accesses it.
    if result["broker_equity"] is not None:
        # M1 (doc 179): broker_equity includes UNREALIZED MTM on open positions, but
        # the internal estimate was realized-only — so a fixed $1 tolerance fired
        # every 30s whenever a position was open (1,308 false D230 WARN + 1,290 EOD
        # RECON SUMMARY lines on 5/28, burying real signals). Two-part fix:
        #   (1) add unrealized P&L to the estimate (mark-to-market parity), and
        #   (2) compare against a BAND that absorbs residual unrealized/timing
        #       swings (max of the configured floor and 0.5% of equity). A real
        #       position-tracking drift (a phantom/missing position) is 5-15% of
        #       equity — far above 0.5% — so it is still caught.
        _unreal = float(getattr(position_manager, "_unrealized_pnl", 0) or 0)
        internal_equity_estimate = (
            float(getattr(position_manager, "starting_equity", 0) or 0)
            + float(getattr(position_manager, "_daily_realized_pnl", 0) or 0)
            + _unreal
        )
        result["internal_equity_estimate"] = internal_equity_estimate
        delta = result["broker_equity"] - internal_equity_estimate
        _equity_band = max(equity_tolerance_usd, 0.005 * result["broker_equity"])
        if abs(delta) > _equity_band:
            logger.warning(
                "D230 RECON_WARN EQUITY: broker_equity=$%.2f internal_estimate=$%.2f "
                "(incl unrealized $%+.2f) delta=$%+.2f (band $%.2f = max($%.2f, 0.5%%)). "
                "Exceeds the unrealized-swing band — likely a real position-tracking "
                "drift requiring investigation.",
                result["broker_equity"], internal_equity_estimate, _unreal, delta,
                _equity_band, equity_tolerance_usd,
            )
            result["equity_within_tolerance"] = False
        else:
            logger.info(
                "EOD RECON EQUITY OK: broker_equity=$%.2f internal_estimate=$%.2f "
                "delta=$%+.2f (within band $%.2f)",
                result["broker_equity"], internal_equity_estimate, delta,
                _equity_band,
            )

    # ── Summary ───────────────────────────────────────────────────
    summary_level = (
        logging.ERROR if result["qty_drift_count"] > 0
        else logging.WARNING if result["stop_drift_count"] > 0 or not result["equity_within_tolerance"]
        else logging.INFO
    )
    logger.log(
        summary_level,
        "EOD RECON SUMMARY: broker_reachable=%s qty_drift=%d stop_drift=%d "
        "equity_within_tolerance=%s. Tier-1 violations require investigation "
        "before next session.",
        result["broker_reachable"],
        result["qty_drift_count"],
        result["stop_drift_count"],
        result["equity_within_tolerance"],
    )

    # Bug AH fix (2026-04-27): function used to fall off the end here,
    # implicitly returning None. The docstring (lines 93-102) and the
    # type annotation (`-> dict[str, Any]`) both promised a dict. Every
    # caller — Track B daemon (recon_daemon.py:162-167), the EOD wire
    # in main.py:7013, and 8 unit tests in test_d230_eod_recon_invariants
    # — assumed a dict and crashed with `'NoneType' object is not
    # subscriptable` at the first attribute access. In production this
    # killed the Track B continuous reconciliation daemon on every
    # 30-second tick from 09:31:01 ET to 16:00:41 ET on Monday
    # 2026-04-27 — Track B was effectively offline for the entire
    # trading session. See docs/research-log/47_bug_ah_track_b_typeerror.md.
    return result


# ── D262 BOCPD_REFIT_RECOMMENDED — re-fit scheduling surface ──────


def run_eod_bocpd_refit_check(
    corpus_path: Any | None = None,
    prior_path: Any | None = None,
    *,
    new_trades_threshold: int = 10,
) -> dict[str, Any]:
    """Compare the persisted BOCPD prior against a fresh re-fit on the
    current corpus. Logs a WARNING with the literal `D262` marker if
    drift OR sample-size growth recommends a refit; INFO baseline-stable
    otherwise. Non-fatal — never raises.

    Per `docs/research-log/26_d_code_registry.md`:
      D262 BOCPD_REFIT_RECOMMENDED — operator-facing signal that the
      pre-trained prior is meaningfully out of sync with the corpus.
      Action: `python scripts/pretrain_bocpd_prior.py` to refresh.

    Args:
      corpus_path:           Path to data/trade_results.jsonl (default).
      prior_path:            Path to data/priors/s1_bocpd_prior.parquet (default).
      new_trades_threshold:  Recommend refit when n_trades grows by ≥ this.

    Returns dict with `fires_d262` flag + diff summary suitable for the
    EOD report.
    """
    import json
    from pathlib import Path as _Path

    if corpus_path is None:
        corpus_path = _Path("data/trade_results.jsonl")
    if prior_path is None:
        prior_path = _Path("data/priors/s1_bocpd_prior.parquet")

    try:
        from src.analysis.bocpd import bocpd_refit_diff
    except ImportError as e:
        logger.error("D262 refit check failed to import bocpd: %s", e)
        return {"fires_d262": False, "error": str(e), "diff": None}

    corpus_p = _Path(corpus_path)
    if not corpus_p.exists():
        logger.info(
            "D262 refit check: corpus not found at %s — skipping (no trades yet)",
            corpus_p,
        )
        return {"fires_d262": False, "error": "no_corpus", "diff": None}

    rows: list[dict] = []
    try:
        with open(corpus_p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as _je:
                    logger.debug("D262 refit: skipping bad row: %s", _je)
    except Exception as e:
        logger.error("D262 refit check: corpus read failed: %s", e)
        return {"fires_d262": False, "error": str(e), "diff": None}

    diff = bocpd_refit_diff(
        rows, prior_path=prior_path,
        new_trades_threshold=new_trades_threshold,
    )

    summary = {
        "old_n_trades": diff.old_prior.n_trades if diff.old_prior else None,
        "new_n_trades": diff.new_prior.n_trades,
        "delta_n_trades": diff.delta_n_trades,
        "delta_mu": diff.delta_mu,
        "delta_sigma": diff.delta_sigma,
        "old_mu_edge": diff.old_prior.mu_edge if diff.old_prior else None,
        "new_mu_edge": diff.new_prior.mu_edge,
        "old_sigma_edge": diff.old_prior.sigma_edge if diff.old_prior else None,
        "new_sigma_edge": diff.new_prior.sigma_edge,
        "reason": diff.reason,
    }

    if diff.recommend_refit:
        logger.warning(
            "D262 BOCPD_REFIT_RECOMMENDED: %s. "
            "Old prior: mu=%s sigma=%s n=%s. "
            "New fit:   mu=%.4f sigma=%.4f n=%d. "
            "Action: `python scripts/pretrain_bocpd_prior.py`.",
            diff.reason,
            f"{diff.old_prior.mu_edge:.4f}" if diff.old_prior else "n/a",
            f"{diff.old_prior.sigma_edge:.4f}" if diff.old_prior else "n/a",
            diff.old_prior.n_trades if diff.old_prior else 0,
            diff.new_prior.mu_edge,
            diff.new_prior.sigma_edge,
            diff.new_prior.n_trades,
        )
    else:
        logger.info(
            "EOD BOCPD refit check: stable (%s). Prior n=%d; new corpus n=%d.",
            diff.reason,
            diff.old_prior.n_trades if diff.old_prior else 0,
            diff.new_prior.n_trades,
        )

    return {
        "fires_d262": diff.recommend_refit,
        "error": None,
        "diff": summary,
    }


# ── D261 PHASE0_SCHEMA_VALIDATION_FAILED — health surface ─────────


def run_eod_phase0_health(writer: Any | None) -> dict[str, Any]:
    """Surface Phase 0 instrumentation health in the EOD report.

    Returns the writer's `health_snapshot()` plus a derived
    `total_d261_failures` count. Logs a WARNING with the literal D261
    marker if any schema accumulated validation failures during the
    session (so the EOD review surfaces them).

    Per `docs/research-log/26_d_code_registry.md`:
      D261 PHASE0_SCHEMA_VALIDATION_FAILED — emit_*() failed Pydantic
      validation; the row was dropped + counter bumped.

    Returns an empty dict if `writer` is None (Phase 0 disabled).
    """
    if writer is None:
        return {}
    try:
        snap = writer.health_snapshot()
    except Exception as e:
        logger.warning("Phase 0 health_snapshot raised: %s", e)
        return {"error": str(e)}

    total = sum(snap.get("d261_failures", {}).values())
    out = {
        "session_date": snap.get("session_date"),
        "ring_size": snap.get("ring_size"),
        "buffer_lengths": snap.get("buffer_lengths", {}),
        "d261_failures": snap.get("d261_failures", {}),
        "total_d261_failures": total,
    }
    if total > 0:
        per_schema = ", ".join(
            f"{k}={v}" for k, v in snap.get("d261_failures", {}).items() if v > 0
        )
        logger.warning(
            "D261 PHASE0_SCHEMA_VALIDATION_FAILED EOD: %d total validation "
            "failures during session (%s). Rows were dropped; investigate "
            "upstream payload construction.",
            total, per_schema,
        )
    else:
        logger.info(
            "Phase 0 EOD health: 0 validation failures across schemas. "
            "Buffer lengths at flush: %s",
            snap.get("buffer_lengths", {}),
        )
    return out


# ── D260 SIGNING_DISAGREEMENT (QMP migration) ──────────────────────


def run_eod_signing_audit(
    trade_corpus: list[dict],
    *,
    threshold: float = 0.10,
) -> dict[str, Any]:
    """Run QMP vs midpoint-baseline signing comparison on a session's
    trade corpus. Fires D260 SIGNING_DISAGREEMENT when the disagreement
    rate exceeds threshold (default 10%).

    Args:
      trade_corpus: list of trade dicts each with keys
        trade_price, bid, ask, prior_tick_price (optional), symbol (optional)
      threshold:    disagreement-rate threshold above which D260 fires

    Returns:
      Dict with summary metrics + fires_d260 flag, suitable for the EOD
      recon report. Never raises — degrades gracefully on malformed input.

    Per `docs/research-log/26_d_code_registry.md`:
      D260 SIGNING_DISAGREEMENT — flags trade for manual review when
      QMP and BJZZ-style baseline disagree on direction beyond threshold.
    """
    try:
        from src.analysis.qmp_signing import signing_disagreement
    except ImportError as e:
        logger.error("D260 audit failed to import qmp_signing: %s", e)
        return {"fires_d260": False, "error": str(e), "summary": None}

    try:
        summary, fires = signing_disagreement(trade_corpus, threshold=threshold)
    except (KeyError, TypeError, ValueError) as e:
        logger.error(
            "D260 audit malformed corpus (skipping): %s. "
            "Each trade dict must have trade_price, bid, ask.", e,
        )
        return {"fires_d260": False, "error": str(e), "summary": None}

    if fires:
        logger.warning(
            "D260 SIGNING_DISAGREEMENT: %d/%d trades signed by both rules disagreed "
            "on direction (rate=%.2f%% > threshold=%.2f%%). QMP rescued %d at-mid "
            "trades the baseline left UNKNOWN. Trades flagged for manual review.",
            summary.disagreed,
            max(summary.qmp_signed, summary.baseline_signed, 1),
            summary.disagreement_rate * 100,
            threshold * 100,
            summary.qmp_rescued,
        )
    elif summary.qmp_rescued > 0:
        logger.info(
            "EOD SIGNING AUDIT: QMP rescued %d/%d at-midpoint trades the baseline "
            "left UNKNOWN (disagreement rate %.2f%% within threshold %.2f%%). "
            "Net QMP capacity-relevant signal coverage: +%d trades.",
            summary.qmp_rescued, summary.total_trades,
            summary.disagreement_rate * 100, threshold * 100,
            summary.qmp_rescued,
        )
    else:
        logger.info(
            "EOD SIGNING AUDIT: %d trades, %d both-signed, 0 disagreements, "
            "0 QMP rescues. Baseline parity.",
            summary.total_trades, summary.qmp_signed,
        )

    return {
        "fires_d260": fires,
        "error": None,
        "summary": {
            "total_trades": summary.total_trades,
            "qmp_signed": summary.qmp_signed,
            "baseline_signed": summary.baseline_signed,
            "disagreed": summary.disagreed,
            "qmp_rescued": summary.qmp_rescued,
            "baseline_rescued": summary.baseline_rescued,
            "disagreement_rate": summary.disagreement_rate,
            "rescue_rate": summary.rescue_rate,
        },
    }
    return result
