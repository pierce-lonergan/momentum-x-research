"""D313 (2026-05-24, doc 171) — Hedge integrity invariant watcher.

PURPOSE -- Layer 2 of the stop-widening rollout (Pierce's safety net):
  Independent watcher that polls broker positions + open orders every
  ~20s and enforces the invariant:

      For every position with qty != 0,
      there MUST be a protective order at the broker
      (sell-stop for longs, buy-stop for shorts).

  On violation: emit a structured D313 alert, page operator via Discord,
  and (after a tolerance window) auto-submit an emergency protective
  STOP at a defensive distance from current price.

WHY THIS MATTERS:
  D310 (TrailingStopManager cancel-without-resubmit) silently unhedged
  NXXT for 66 hours on 2026-05-18 to 5/20. The +44% gain was pure luck.
  This watcher would have caught it within 30-60 seconds and either
  re-armed the stop OR alerted the operator. It catches D310 + D312
  + the *unknown* future variants of the same failure shape.

CATCHES (not exhaustive):
  - D310 cancel-without-resubmit (TrailingStopManager)
  - D312 silent-BUY-no-OTO submission (executor swallows)
  - L1 standalone-stop submission failure (network blip after fill)
  - StopResubmitter ratchet that cancels old + fails to submit new
  - Manual broker-side order cancellation (operator mistake)
  - Alpaca rejecting stop submission for sub-$1 names (filed: D294 was
    one corner; broader pattern likely exists)

GATING:
  Tolerance is configurable so brief gaps during cancel/resubmit churn
  don't fire spurious alarms. Default 60s. The watcher is ALWAYS-ON
  (not gated by MOMENTUM_T2_ENABLED) -- it's a global safety net for
  ALL arms including the unchanged tight-stop production path.

EMERGENCY STOP DISTANCE:
  When no last-known-stop is available (D310 scenario), the emergency
  stop is placed at min(entry * 0.85, last_price * 0.92) -- the wider
  of "15% from entry" or "8% from current price". This bounds the
  downside without being so tight that the stop triggers immediately
  on a noisy re-arm.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _PositionState:
    """Per-ticker tracking of "first observed unhedged" timestamp.

    Reset to None when a protective order is observed; set to the
    current timestamp when the position is first seen unhedged.
    """
    first_unhedged_utc: datetime | None = None
    emergency_stop_submitted: bool = False
    last_seen_qty: int = 0


@dataclass
class HedgeViolation:
    """Reported when a position has been unhedged longer than tolerance."""
    ticker: str
    qty: int
    side: str  # "long" or "short"
    avg_entry: float
    last_price: float
    unhedged_seconds: float
    emergency_stop_price: float
    will_submit_emergency_stop: bool


class HedgeIntegrityWatcher:
    """D313 Layer 2 safety net. Run as an async background task alongside
    the main bot loop.

    Usage (in main.py):
        watcher = HedgeIntegrityWatcher(client=client, alert_webhook=...)
        asyncio.create_task(watcher.run_forever())
    """

    def __init__(
        self,
        client: Any,
        *,
        position_manager: Any | None = None,
        # D313.v4 (2026-05-27): when set, the watcher calls
        # position_manager.attach_external_stop() after a successful
        # emergency-stop submission so the internal tracker reflects
        # reality. Without this, D230 RECON_WARN STOP fires every 30s
        # forever (~1700/day) because the position's stop_order_id
        # stays empty even though the broker has the stop. See
        # _recovery_log.md (2026-05-27 06:15 ET) for live observation.
        poll_interval_sec: float = 20.0,
        tolerance_sec: float = 60.0,
        emergency_stop_pct_from_entry: float = 0.15,
        emergency_stop_pct_from_last: float = 0.08,
        alert_webhook_url: str | None = None,
        auto_submit_emergency: bool = True,
        skip_below_price: float = 1.0,
        # D313.v2 (2026-05-24, doc 173): bands for "stop within reasonable
        # range" correctness check. A stop placed at $0.01 on a $100 stock
        # technically exists but provides no protection -- treat as
        # incorrect (= unhedged) so the emergency-stop path fires.
        # D313.v3 (2026-05-24, doc 174): widened from 0.30 -> 0.65 per
        # empirical historical analysis. ATR stops on high-vol microcaps
        # (the T2 wide-arm target population) routinely sit 30-65% below
        # entry. p95 of 14 historical wide-arm shadow events = 60.91%
        # (AUUD); max = 60.91%. A 0.30 band would false-positive 21% of
        # legitimate wide-arm stops, causing the safety net to REPLACE
        # the correct ATR stop with an emergency -8% stop -- wrecking
        # the wide-arm thesis on its first volatile microcap trade.
        # 0.65 gives 4pp margin above the worst observed case while
        # still catching absurd stops (e.g. $0.01 on $100 = 99% off).
        stop_correctness_band_min_pct: float = 0.65,  # not less than -65% from entry
        stop_correctness_band_max_pct: float = 0.001,  # not closer than -0.1% (=10bps)
    ) -> None:
        self._client = client
        self._poll_interval = float(poll_interval_sec)
        self._tolerance = float(tolerance_sec)
        self._emerg_entry_pct = float(emergency_stop_pct_from_entry)
        self._emerg_last_pct = float(emergency_stop_pct_from_last)
        self._alert_webhook = alert_webhook_url
        # D313.v4 (2026-05-27): position_manager handle for writeback
        # after successful emergency stop submission.
        self._position_manager = position_manager
        # When False, the watcher only ALERTS and does not submit
        # emergency stops itself. Useful for dry-run or when operator
        # wants manual control.
        self._auto_submit = bool(auto_submit_emergency)
        # Sub-$1 names have unreliable Alpaca stop-trigger behavior per
        # user's plan. Watcher skips emergency stop submission below
        # this price; alert still fires so operator can intervene.
        self._skip_below_price = float(skip_below_price)
        self._stop_band_min_pct = float(stop_correctness_band_min_pct)
        self._stop_band_max_pct = float(stop_correctness_band_max_pct)
        # Per-ticker state across polls
        self._state: dict[str, _PositionState] = {}
        # Counters surfaced via .stats() for dashboards
        self._n_checks = 0
        self._n_violations = 0
        self._n_emergency_stops = 0
        # D313.v2: track wrong-params (existence ✓ but correctness ✗)
        # separately so EOD can surface them
        self._n_incorrect_stops = 0
        self._stopped = asyncio.Event()
        # D313.v2 watchdog heartbeat: last successful check_once tick.
        # main.py's L2 liveness watchdog asserts this is < 90s stale.
        # If the watcher silently crashed or hung, the watchdog pages.
        self._last_heartbeat_utc: datetime | None = None

    def stop(self) -> None:
        """Signal the watcher to exit at the next poll boundary."""
        self._stopped.set()

    def stats(self) -> dict[str, Any]:
        return {
            "checks": self._n_checks,
            "violations": self._n_violations,
            "emergency_stops": self._n_emergency_stops,
            "incorrect_stops": self._n_incorrect_stops,
            "tracked_positions": len(self._state),
            "last_heartbeat_utc": (
                self._last_heartbeat_utc.isoformat()
                if self._last_heartbeat_utc else None
            ),
        }

    def heartbeat_age_sec(self, now: datetime | None = None) -> float | None:
        """D313.v2 watchdog: seconds since last successful check_once tick.

        Returns None if no tick has completed yet (watcher just started)
        OR if the watcher's heartbeat was never set. Returns a float
        otherwise. main.py's L2 liveness watchdog asserts < 90s
        before paging the operator.
        """
        if self._last_heartbeat_utc is None:
            return None
        if now is None:
            now = datetime.now(timezone.utc)
        return (now - self._last_heartbeat_utc).total_seconds()

    async def run_forever(self) -> None:
        """Main loop. Polls every ``poll_interval_sec`` until ``.stop()``."""
        logger.info(
            "D313 HEDGE_WATCHER starting: interval=%.0fs tolerance=%.0fs "
            "auto_submit=%s skip_below=$%.2f",
            self._poll_interval, self._tolerance,
            self._auto_submit, self._skip_below_price,
        )
        while not self._stopped.is_set():
            try:
                await self.check_once()
            except Exception as e:
                logger.warning(
                    "D313 HEDGE_WATCHER tick raised (%s); will retry next interval",
                    e,
                )
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self._poll_interval,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("D313 HEDGE_WATCHER stopped")

    async def check_once(self) -> list[HedgeViolation]:
        """One poll: fetch broker positions + open orders, evaluate
        invariant, alert + (optionally) submit emergency stops.

        Returns the list of violations detected this tick (empty if
        all positions are properly hedged).
        """
        self._n_checks += 1
        positions = await self._client.get_positions()
        # Get open orders so we know which positions have a protective stop
        orders = await self._client.get_orders(status="open", limit=200)

        # D313.v2 (2026-05-24, doc 173): index ALL protective orders per
        # (symbol, side) -- and KEEP the full order so we can validate
        # qty, TIF, and stop price band. Existence alone is insufficient
        # (Pierce: "What if the stop submitted with wrong qty/side/TIF?").
        hedged_orders: dict[tuple[str, str], list[dict]] = {}
        for o in orders or []:
            sym = (o.get("symbol") or "").upper()
            otype = (o.get("type") or "").lower()
            side = (o.get("side") or "").lower()
            if not sym or otype not in ("stop", "stop_limit", "trailing_stop"):
                continue
            hedged_orders.setdefault((sym, side), []).append(o)
        # Backwards-compat: existence-only set used by older callers/tests
        hedged: set[tuple[str, str]] = set(hedged_orders.keys())

        violations: list[HedgeViolation] = []
        now = datetime.now(timezone.utc)
        seen_syms: set[str] = set()
        for p in positions or []:
            sym = (p.get("symbol") or "").upper()
            if not sym:
                continue
            seen_syms.add(sym)
            qty = int(float(p.get("qty") or 0))
            if qty == 0:
                continue
            position_side = "long" if qty > 0 else "short"
            required_stop_side = "sell" if qty > 0 else "buy"
            avg_entry = float(p.get("avg_entry_price") or 0.0)
            last_price = float(p.get("current_price") or avg_entry or 0.0)
            state = self._state.setdefault(sym, _PositionState())
            state.last_seen_qty = qty
            # D313.v2: existence + CORRECTNESS check. A stop with wrong
            # params (under-qty cover / wrong side / DAY TIF / absurd
            # stop price) is functionally unhedged. We treat it the
            # same as "no stop" so emergency intervention can correct it.
            matched_orders = hedged_orders.get((sym, required_stop_side), [])
            correctness_issue = None
            if matched_orders:
                correctness_issue = self._check_stop_correctness(
                    sym=sym, position_qty=abs(qty), position_side=position_side,
                    avg_entry=avg_entry, last_price=last_price,
                    orders=matched_orders,
                )
            is_hedged = bool(matched_orders) and correctness_issue is None

            if is_hedged:
                # Reset state (re-armed or never breached)
                state.first_unhedged_utc = None
                state.emergency_stop_submitted = False
                continue
            if matched_orders and correctness_issue is not None:
                # Stop EXISTS but is wrong -- count separately for visibility
                self._n_incorrect_stops += 1
                logger.warning(
                    "D313.v2 STOP_INCORRECT %s: stop exists but %s -- "
                    "treating as unhedged",
                    sym, correctness_issue,
                )

            # Unhedged. Record first-seen timestamp if new.
            if state.first_unhedged_utc is None:
                state.first_unhedged_utc = now
                logger.info(
                    "D313 HEDGE_WATCHER %s: %s position qty=%d unhedged "
                    "(no %s stop at broker); starting tolerance clock",
                    sym, position_side, qty, required_stop_side,
                )
                continue

            unhedged_sec = (now - state.first_unhedged_utc).total_seconds()
            if unhedged_sec < self._tolerance:
                continue  # within tolerance, no action yet

            # VIOLATION
            self._n_violations += 1
            emergency_stop_price = self._compute_emergency_stop(
                position_side, avg_entry, last_price,
            )
            will_submit = (
                self._auto_submit
                and not state.emergency_stop_submitted
                and last_price >= self._skip_below_price
            )
            v = HedgeViolation(
                ticker=sym, qty=qty, side=position_side,
                avg_entry=avg_entry, last_price=last_price,
                unhedged_seconds=unhedged_sec,
                emergency_stop_price=emergency_stop_price,
                will_submit_emergency_stop=will_submit,
            )
            violations.append(v)
            await self._handle_violation(v, state)

        # Clean up state for positions that no longer exist (closed)
        for sym in list(self._state.keys()):
            if sym not in seen_syms:
                del self._state[sym]

        # D313.v2 (2026-05-24): heartbeat -- update ONLY after a
        # successful tick completes. main.py's watchdog reads this and
        # pages the operator if no heartbeat in >90s (watcher hung or
        # crashed silently).
        self._last_heartbeat_utc = now
        return violations

    def _check_stop_correctness(
        self, *, sym: str, position_qty: int, position_side: str,
        avg_entry: float, last_price: float, orders: list[dict],
    ) -> str | None:
        """D313.v2 (2026-05-24, doc 173): correctness check.

        For the set of protective orders matching (symbol, required_side),
        verify at least one satisfies:
          - covered_qty >= position_qty (sums across multiple orders)
          - tif in (gtc, gtd) -- DAY stops expire at 16:00 ET
          - stop_price within reasonable band:
              long:  avg_entry * (1 - max_pct) <= stop <= last * (1 - min_pct)
              short: last * (1 + min_pct) <= stop <= avg_entry * (1 + max_pct)

        Returns None if at least one order satisfies all checks.
        Returns a short string describing the issue otherwise.
        """
        total_covered_qty = 0
        any_valid_tif = False
        any_in_band = False
        for o in orders:
            try:
                o_qty = int(float(o.get("qty") or 0))
            except (TypeError, ValueError):
                o_qty = 0
            tif = (o.get("time_in_force") or "").lower()
            try:
                stop_price = float(o.get("stop_price") or 0.0)
            except (TypeError, ValueError):
                stop_price = 0.0
            total_covered_qty += o_qty
            if tif in ("gtc", "gtd"):
                any_valid_tif = True
            # Band check
            if position_side == "long":
                # Stop should be BELOW current, but not absurdly far
                price_anchor = max(avg_entry, last_price) or 1.0
                min_allowed = avg_entry * (1.0 - self._stop_band_min_pct)
                max_allowed = price_anchor * (1.0 - self._stop_band_max_pct)
                if min_allowed <= stop_price <= max_allowed:
                    any_in_band = True
            else:
                price_anchor = min(avg_entry, last_price) or 1.0
                min_allowed = price_anchor * (1.0 + self._stop_band_max_pct)
                max_allowed = avg_entry * (1.0 + self._stop_band_min_pct)
                if min_allowed <= stop_price <= max_allowed:
                    any_in_band = True
        if total_covered_qty < position_qty:
            return (f"covered_qty={total_covered_qty} < position_qty={position_qty}")
        if not any_valid_tif:
            return "no stop has TIF in (gtc, gtd) -- DAY stops expire at close"
        if not any_in_band:
            return (f"no stop price in reasonable band -- "
                    f"check stops are not absurdly tight or wide")
        return None

    def _compute_emergency_stop(
        self, side: str, avg_entry: float, last_price: float,
    ) -> float:
        """Defensive stop at the WIDER of (entry × 0.85) or (last × 0.92)
        for longs. Inverse for shorts."""
        if side == "long":
            entry_based = avg_entry * (1 - self._emerg_entry_pct)
            last_based = last_price * (1 - self._emerg_last_pct)
            return round(max(0.01, min(entry_based, last_based)), 4)
        else:
            entry_based = avg_entry * (1 + self._emerg_entry_pct)
            last_based = last_price * (1 + self._emerg_last_pct)
            return round(max(entry_based, last_based), 4)

    def _force_stop_oid_writeback(
        self, ticker: str, stop_oid: str, stop_price: float,
    ) -> None:
        """doc 286 (doc-285 gap #11): last-resort direct write-back of a
        broker-CONFIRMED emergency-stop oid onto the tracked position.

        Called only after attach_external_stop refused or raised, i.e.
        the canonical path could not record a stop we KNOW exists (we
        just submitted it and hold the oid from the broker's response).
        Mirrors attach_external_stop's mutation (stop_order_id +
        stop_loss together — recording the oid alone would make
        eod_recon flag a stop-price drift against the broker forever).
        Guarded end-to-end: the position may be gone from the tracker
        (get_position → None) or the manager may not expose the lookup —
        in both cases this is a silent no-op, NEVER a raise into the
        watcher tick."""
        if not stop_oid or stop_oid in ("?", "None"):
            return
        pos = None
        try:
            _getp = getattr(self._position_manager, "get_position", None)
            pos = _getp(ticker) if callable(_getp) else None
        except Exception as _ge:  # noqa: BLE001 — lookup must never break the net
            logger.debug(
                "D313 doc286 %s: get_position raised during force "
                "write-back: %s", ticker, _ge,
            )
        if pos is None:
            logger.info(
                "D313 doc286 %s: position not in tracker — skipping stop-oid "
                "force write-back (broker stop %s IS in place)",
                ticker, stop_oid[:8],
            )
            return
        try:
            pos.stop_order_id = stop_oid
            pos.stop_loss = float(stop_price)
            logger.warning(
                "D313 doc286 STOP_OID_FORCE_WRITEBACK %s: oid=%s stop=$%.4f "
                "written directly onto the tracked position (attach path "
                "refused/raised; the broker stop is confirmed-real so the "
                "tracker must reflect it — recon/D98/resubmitter now see it)",
                ticker, stop_oid[:8], stop_price,
            )
        except Exception as _we:  # noqa: BLE001 — write must never break the net
            logger.warning(
                "D313 doc286 %s: force write-back failed (%s) — broker stop "
                "%s IS in place but internal tracker drift remains",
                ticker, _we, stop_oid[:8],
            )

    async def _handle_violation(
        self, v: HedgeViolation, state: _PositionState,
    ) -> None:
        """Log + alert + (optionally) submit emergency stop."""
        logger.error(
            "D313 HEDGE_VIOLATION %s: %s qty=%d unhedged for %.0fs "
            "(threshold %.0fs); avg_entry=$%.4f last=$%.4f "
            "emergency_stop=$%.4f will_submit=%s",
            v.ticker, v.side, v.qty, v.unhedged_seconds, self._tolerance,
            v.avg_entry, v.last_price, v.emergency_stop_price,
            v.will_submit_emergency_stop,
        )
        # doc 272 A2: durable CRITICAL incident for the pager (the Discord
        # webhook below can be unset/down; the incident bus is the record).
        # Bus dedup_key bounds a persistent violation to one row / 5 min.
        # NEVER raises into the watcher tick.
        try:
            from src.ops.incident_bus import emit_incident
            emit_incident(
                "HEDGE_VIOLATION", "CRITICAL", ticker=v.ticker,
                context={
                    "side": v.side, "qty": v.qty,
                    "unhedged_seconds": round(v.unhedged_seconds, 1),
                    "avg_entry": v.avg_entry, "last_price": v.last_price,
                    "emergency_stop_price": v.emergency_stop_price,
                    "will_submit_emergency_stop": v.will_submit_emergency_stop,
                },
                suggested=[
                    "position has NO working protective order at the broker",
                    ("watcher will auto-submit an emergency stop"
                     if v.will_submit_emergency_stop else
                     "auto-submit NOT firing (sub-$1 or already attempted) — "
                     "intervene manually NOW"),
                ],
                dedup_key=f"hedge_violation_{v.ticker}",
            )
        except Exception as _ie:  # noqa: BLE001 — telemetry never breaks the net
            logger.warning("D313 incident emit failed (%s)", _ie)
        # Discord alert (best-effort)
        if self._alert_webhook:
            try:
                from src.monitoring.alerts import alert_critical
                msg = (
                    f"⚠️ D313 HEDGE VIOLATION: **{v.ticker}** {v.side} "
                    f"qty={v.qty} unhedged {v.unhedged_seconds:.0f}s\n"
                    f"avg_entry=${v.avg_entry:.4f} last=${v.last_price:.4f} "
                    f"emergency_stop=${v.emergency_stop_price:.4f}\n"
                    f"auto_submit={'YES' if v.will_submit_emergency_stop else 'NO (manual intervention required)'}"
                )
                await alert_critical(msg, webhook_url=self._alert_webhook)
            except Exception as _ae:
                logger.warning("D313 alert post failed (%s)", _ae)
        # Submit emergency stop
        if v.will_submit_emergency_stop:
            try:
                stop_side = "sell" if v.side == "long" else "buy"
                resp = await self._client.submit_stop_order(
                    symbol=v.ticker, qty=abs(v.qty), side=stop_side,
                    stop_price=v.emergency_stop_price, time_in_force="gtc",
                )
                state.emergency_stop_submitted = True
                self._n_emergency_stops += 1
                _emerg_oid = resp.get("id", "?")
                logger.error(
                    "D313 EMERGENCY_STOP_SUBMITTED %s: oid=%s stop=$%.4f "
                    "qty=%d -- position now hedged. Investigate why the "
                    "original protective order disappeared.",
                    v.ticker, _emerg_oid, v.emergency_stop_price,
                    abs(v.qty),
                )
                # D313.v4 (2026-05-27): write the emergency stop oid back
                # to position_manager so the internal tracker matches
                # reality. Without this, D230 RECON_WARN STOP fires every
                # 30s for the rest of the session (~1700/day spam).
                # Discovered live during T2 Day-2 morning carryover of
                # LFS ghost position 2026-05-27.
                if (
                    self._position_manager is not None
                    and _emerg_oid not in ("?", "", None)
                ):
                    try:
                        attached = self._position_manager.attach_external_stop(
                            ticker=v.ticker,
                            stop_order_id=str(_emerg_oid),
                            stop_price=float(v.emergency_stop_price),
                            qty=int(abs(v.qty)),
                        )
                        if attached:
                            logger.info(
                                "D313.v4 STOP_OID_WRITEBACK %s: oid=%s "
                                "stop=$%.4f -- internal tracker updated, "
                                "D230 spam should stop.",
                                v.ticker, str(_emerg_oid)[:8],
                                v.emergency_stop_price,
                            )
                        else:
                            logger.warning(
                                "D313.v4 STOP_OID_WRITEBACK %s: "
                                "attach_external_stop returned False; see "
                                "D273 STOP_ATTACH FAILED above for reason",
                                v.ticker,
                            )
                            # doc 286 (doc-285 gap #11): the broker stop is
                            # REAL — we just submitted it and hold its oid
                            # from the submit response. On 7/6 RIVN the
                            # attach was refused on a stale-qty check and
                            # the position kept "internal_stop but no
                            # stop_order_id" all session: D230 spam, and
                            # D98/the resubmitter blind to the stop (a
                            # blind resubmitter can submit a DUPLICATE
                            # stop → double-sell). Book the confirmed oid
                            # directly onto the tracked position.
                            self._force_stop_oid_writeback(
                                v.ticker, str(_emerg_oid),
                                float(v.emergency_stop_price),
                            )
                    except Exception as _we:
                        logger.warning(
                            "D313.v4 STOP_OID_WRITEBACK %s: exception "
                            "(non-fatal, broker stop IS in place but "
                            "internal tracker drift remains): %s",
                            v.ticker, _we,
                        )
                        # doc 286: same rationale as the refused-attach
                        # branch — the stop is broker-confirmed; record it.
                        self._force_stop_oid_writeback(
                            v.ticker, str(_emerg_oid),
                            float(v.emergency_stop_price),
                        )
            except Exception as _se:
                logger.error(
                    "D313 EMERGENCY_STOP_FAILED %s: %s -- position remains "
                    "UNHEDGED. OPERATOR INTERVENTION REQUIRED.",
                    v.ticker, _se,
                )
                # doc 272 A2: the self-heal FAILED — this is the highest-
                # urgency naked-risk state. Durable CRITICAL for the pager.
                try:
                    from src.ops.incident_bus import emit_incident
                    emit_incident(
                        "EMERGENCY_STOP_FAILED", "CRITICAL", ticker=v.ticker,
                        context={
                            "error": str(_se)[:200], "side": v.side,
                            "qty": v.qty,
                            "intended_stop": v.emergency_stop_price,
                            "unhedged_seconds": round(v.unhedged_seconds, 1),
                        },
                        suggested=[
                            "auto-hedge failed — submit a protective stop "
                            "or flatten this position manually NOW",
                        ],
                        dedup_key=f"emerg_stop_fail_{v.ticker}",
                    )
                except Exception as _ie2:  # telemetry never breaks the net
                    logger.debug(
                        "D313 EMERGENCY_STOP_FAILED incident emit failed: %s",
                        _ie2,
                    )
        elif v.last_price < self._skip_below_price:
            logger.error(
                "D313 HEDGE_VIOLATION %s: price $%.4f < skip_below "
                "$%.2f -- NOT submitting emergency stop (Alpaca stop "
                "trigger unreliable on sub-$1 names per D294 analysis); "
                "operator must intervene",
                v.ticker, v.last_price, self._skip_below_price,
            )
