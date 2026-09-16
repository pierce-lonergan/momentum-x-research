"""
Track B reconciliation daemon (2026-04-24, items 14-20).

Per `25_bug_hunting_playbook.md` §3 + Track A handoff in
`28_track_a_summary.md` §"Hand-off to Track B".

Wraps `eod_recon.run_eod_invariants()` (the one-shot EOD version) into
a long-running 30-second asyncio loop with three escalation tiers:

  D230 RECON_WARN          (soft, Tier 2): equity drift, no-OID stop drift
  D231 RECON_HARD_BLOCK    (hard, Tier 1): qty mismatch, OID-confirmed stop drift
                                           → blocks new entries (state.trading_blocked = True)
  D232 RECON_LETHAL        (lethal):       sustained Tier-1 violation > 60s
                                           OR equity divergence > 5%
                                           → flat all + halt (caller-invoked)

Shadow mode: logs all decisions tagged "SHADOW" but never sets
state.trading_blocked or state.lethal_triggered. First week of
production deployment SHOULD run shadow_mode=True.

Independent observability: writes a JSON status file every tick so
external dashboards / Discord webhooks can read state without the
trading loop being involved (per playbook §3.5 — "if your monitoring
shares state with the system being monitored, an outage in the
monitored system silently disables monitoring").

Architecture (this version):
    daemon = ReconDaemon(client=alpaca, position_manager=pm, ...)
    state  = daemon.state    # shared with trading loop for blocking
    asyncio.create_task(daemon.run_forever())

    # In trading loop, before submitting a new order:
    if state.trading_blocked:
        skip_entry()
    if state.lethal_triggered:
        await flat_all_and_halt()

A future enhancement (per playbook §3.5) extracts this into a
separate process. For now, in-process daemon with a status_file
provides ~80% of the value with much less coordination cost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.monitoring.eod_recon import run_eod_invariants

logger = logging.getLogger(__name__)


# ── Default thresholds ────────────────────────────────────────────
# Conservative starting values per next-actions item 19. Shadow-mode
# data determines final calibration. Err on the side of UNDER-triggering
# in first arm-live deployment.

DEFAULT_TICK_INTERVAL_S: float = 30.0
DEFAULT_LETHAL_QTY_DRIFT_S: float = 60.0     # qty mismatch sustained >60s
DEFAULT_LETHAL_EQUITY_DRIFT_PCT: float = 0.05  # 5%


@dataclass
class ReconState:
    """Shared state between the daemon and the trading loop.

    The trading loop reads `trading_blocked` before any new order
    submission and `lethal_triggered` to decide whether to invoke
    flatten-and-halt. The daemon owns writes; the trading loop only
    reads (one-way edge).
    """

    trading_blocked: bool = False
    lethal_triggered: bool = False
    last_tick_utc: str = ""
    consecutive_violation_ticks: int = 0
    first_violation_time: float | None = None     # epoch seconds
    last_qty_drift_count: int = 0
    last_stop_drift_count: int = 0
    last_equity_drift_usd: float = 0.0
    last_tier: str = "ok"

    def reset_violation_streak(self) -> None:
        self.consecutive_violation_ticks = 0
        self.first_violation_time = None


class ReconDaemon:
    """30-second-cadence reconciliation daemon.

    Three escalation tiers per item 16 of the next-actions list.
    Shadow mode for first week per item 17. Lethal threshold defaults
    per item 19.
    """

    def __init__(
        self,
        *,
        client: Any,
        position_manager: Any,
        shadow_mode: bool = True,
        state: ReconState | None = None,
        tick_interval_s: float = DEFAULT_TICK_INTERVAL_S,
        lethal_qty_drift_seconds: float = DEFAULT_LETHAL_QTY_DRIFT_S,
        lethal_equity_drift_pct: float = DEFAULT_LETHAL_EQUITY_DRIFT_PCT,
        status_file: str | None = None,
    ) -> None:
        self._client = client
        self._pm = position_manager
        self._shadow_mode = shadow_mode
        self.state = state or ReconState()
        self._tick_interval_s = tick_interval_s
        self._lethal_qty_drift_s = lethal_qty_drift_seconds
        self._lethal_equity_drift_pct = lethal_equity_drift_pct
        self._status_file = status_file
        self._stop_event = asyncio.Event()
        # doc 272 A2: session-level emitted-set so 700 ticks of the same
        # violation = ONE incident per (kind, ticker) per daemon lifetime.
        # On 2026-06-09 [SHADOW] D232 RECON_LETHAL logged ~700x while a
        # position sat naked 5.8h and the incident bus stayed EMPTY.
        self._incidents_emitted: set[tuple[str, str]] = set()

    # ── Public lifecycle ──────────────────────────────────────────

    async def run_forever(self) -> None:
        """Loop forever, ticking every `tick_interval_s` seconds.
        Caller cancels the asyncio task to stop."""
        logger.info(
            "Track B recon daemon starting (shadow_mode=%s, tick=%.1fs, "
            "lethal_qty_drift>%.0fs, lethal_equity_drift>%.1f%%)",
            self._shadow_mode, self._tick_interval_s,
            self._lethal_qty_drift_s, self._lethal_equity_drift_pct * 100,
        )
        while not self._stop_event.is_set():
            try:
                await self.run_one_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover — defensive
                logger.error(
                    "Track B daemon: tick raised unexpectedly (%s) — "
                    "continuing loop", e, exc_info=True,
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._tick_interval_s,
                )
            except asyncio.TimeoutError:  # noqa: silent-handler — TimeoutError IS the expected tick signal
                pass

    def stop(self) -> None:
        self._stop_event.set()

    # ── doc 272 A2: incident-bus emit (never raises, once per session) ──

    def _emit_recon_incident(
        self, kind: str, *, ticker: str | None, context: dict,
        suggested: list[str] | None = None,
    ) -> None:
        """Emit one CRITICAL incident per (kind, ticker) per daemon lifetime.
        NEVER raises into the tick. Set-marking only happens when the bus
        confirms the write, so a transient write failure retries next tick."""
        key = (kind, (ticker or "").upper())
        if key in self._incidents_emitted:
            return
        try:
            from src.ops.incident_bus import emit_incident
            if emit_incident(kind, "CRITICAL", ticker=ticker,
                             context=context, suggested=suggested):
                self._incidents_emitted.add(key)
        except Exception as e:  # noqa: BLE001 — telemetry must never break recon
            logger.warning(
                "Track B: incident emit failed for %s %s: %s", kind, ticker, e,
            )

    # ── One tick of the loop ──────────────────────────────────────

    async def run_one_tick(self) -> None:
        """Single iteration: poll broker → run invariants → escalate
        per tier rules → optionally take action (or log SHADOW)."""
        # Run the invariants from eod_recon (the one-shot version)
        recon = await run_eod_invariants(
            client=self._client, position_manager=self._pm,
        )

        self.state.last_tick_utc = datetime.now(timezone.utc).isoformat()
        self.state.last_qty_drift_count = recon["qty_drift_count"]
        self.state.last_stop_drift_count = recon["stop_drift_count"]

        # Compute equity drift in absolute dollars and pct
        broker_equity = recon.get("broker_equity")
        internal_estimate = recon.get("internal_equity_estimate", 0.0) or 0.0
        equity_drift_usd = 0.0
        equity_drift_pct = 0.0
        if broker_equity is not None and internal_estimate > 0:
            equity_drift_usd = float(broker_equity) - internal_estimate
            equity_drift_pct = abs(equity_drift_usd) / internal_estimate
        self.state.last_equity_drift_usd = equity_drift_usd

        # ── Tier classification ──────────────────────────────────
        # Tier-1 conditions: any qty drift OR a STOP drift where the
        # broker-confirmed stop price disagrees with internal (kind="stop"
        # in the details). The "no stop_order_id" case (kind="stop_no_oid")
        # is Tier-2 — we cannot prove violation without an OID to compare
        # against. eod_recon.run_eod_invariants increments stop_drift_count
        # for both kinds, so we discriminate here from the details list.
        details = recon.get("details", []) or []
        tier1_stop_drifts = sum(
            1 for d in details if d.get("kind") in ("stop", "stop_oid_missing")
        )
        tier2_stop_drifts = sum(
            1 for d in details if d.get("kind") == "stop_no_oid"
        )
        tier1_conditions = (
            recon["qty_drift_count"] > 0
            or tier1_stop_drifts > 0
        )
        # Tier-2 conditions: equity drift outside the soft tolerance
        # ($1) OR a "no broker stop" warning (the D56 case — internal
        # has a stop_loss but no broker order to confirm against).
        tier2_conditions = (
            (not recon["equity_within_tolerance"])
            or tier2_stop_drifts > 0
        )

        # ── doc 272 A2: page-able incidents for qty drift / ghosts ────
        # Fires in BOTH shadow and armed mode (shadow flagged in context).
        # Session-level emitted-set => one incident per (kind, ticker)
        # even though this tick repeats every 30s all day.
        for d in details:
            if d.get("kind") in ("qty", "qty_ghost"):
                _t = d.get("ticker") or "?"
                self._emit_recon_incident(
                    "QTY_GHOST",
                    ticker=str(_t),
                    context={
                        "drift_kind": d.get("kind"),
                        "internal_qty": d.get("internal"),
                        "broker_qty": d.get("broker"),
                        "delta": d.get("delta"),
                        "shadow_mode": self._shadow_mode,
                    },
                    suggested=[
                        "broker and tracker disagree on this position "
                        "(ghost/phantom) — verify at broker NOW",
                        "if broker holds it untracked: it has NO protective "
                        "stop management — flatten or hedge manually",
                    ],
                )

        # Lethal conditions:
        #   (a) equity drift exceeds lethal_equity_drift_pct (immediate)
        #   (b) Tier-1 violation sustained beyond lethal_qty_drift_s
        lethal_now = False
        lethal_reason = ""
        if equity_drift_pct >= self._lethal_equity_drift_pct:
            lethal_now = True
            lethal_reason = (
                f"EQUITY drift {equity_drift_pct * 100:.2f}% exceeds "
                f"lethal threshold {self._lethal_equity_drift_pct * 100:.1f}%"
            )

        # Track sustained Tier-1 violation
        now = time.monotonic()
        if tier1_conditions:
            self.state.consecutive_violation_ticks += 1
            if self.state.first_violation_time is None:
                self.state.first_violation_time = now
            elapsed = now - self.state.first_violation_time
            if elapsed >= self._lethal_qty_drift_s:
                lethal_now = True
                lethal_reason = (
                    f"QTY/STOP drift sustained {elapsed:.1f}s exceeds "
                    f"lethal threshold {self._lethal_qty_drift_s:.0f}s"
                )
        else:
            self.state.reset_violation_streak()

        # ── Tier action (or SHADOW log) ──────────────────────────
        prefix = "[SHADOW] " if self._shadow_mode else ""

        if lethal_now:
            self.state.last_tier = "lethal"
            logger.error(
                "%sD232 RECON_LETHAL: %s. %s",
                prefix, lethal_reason,
                "WOULD flat all + halt" if self._shadow_mode
                else "FLATTING ALL + HALTING NEW ENTRIES",
            )
            # doc 272 A2: the 2026-06-09 incident — this exact line logged
            # ~700x over 5.8h with zero pages. Emit ONE CRITICAL incident
            # (per session) the pager can deliver.
            _lethal_tickers = sorted({
                str(d.get("ticker")) for d in details if d.get("ticker")
            })
            self._emit_recon_incident(
                "RECON_LETHAL_SHADOW" if self._shadow_mode else "RECON_LETHAL",
                ticker=(_lethal_tickers[0] if len(_lethal_tickers) == 1 else None),
                context={
                    "reason": lethal_reason,
                    "shadow_mode": self._shadow_mode,
                    "action": ("WOULD flat all + halt (shadow)"
                               if self._shadow_mode
                               else "FLATTING ALL + HALTING NEW ENTRIES"),
                    "qty_drift_count": recon["qty_drift_count"],
                    "stop_drift_count": recon["stop_drift_count"],
                    "equity_drift_usd": round(equity_drift_usd, 2),
                    "tickers": _lethal_tickers[:8],
                },
                suggested=[
                    "reconcile broker vs tracker positions immediately",
                    "shadow mode does NOT act — a human must flatten/hedge",
                ],
            )
            if not self._shadow_mode:
                self.state.lethal_triggered = True
                self.state.trading_blocked = True
        elif tier1_conditions:
            self.state.last_tier = "hard"
            logger.warning(
                "%sD231 RECON_HARD_BLOCK: qty_drift=%d stop_drift=%d. %s",
                prefix, recon["qty_drift_count"], recon["stop_drift_count"],
                "WOULD block new entries" if self._shadow_mode
                else "BLOCKING NEW ENTRIES",
            )
            if not self._shadow_mode:
                self.state.trading_blocked = True
        elif tier2_conditions:
            self.state.last_tier = "soft"
            logger.warning(
                "%sD230 RECON_WARN: equity_drift=$%+.2f (within lethal threshold)",
                prefix, equity_drift_usd,
            )
            # Soft tier doesn't block; trading_blocked stays as-is
            # (cleared if recovered from a previous Tier-1 state)
        else:
            self.state.last_tier = "ok"
            # Clean tick — clear blocking if previously set, EXCEPT
            # lethal (lethal is sticky; only operator intervention clears)
            if not self.state.lethal_triggered and self.state.trading_blocked:
                logger.info(
                    "Track B: previous block cleared — broker-internal "
                    "agreement restored; trading_blocked=False",
                )
                self.state.trading_blocked = False

        # ── Status file (independent observability) ───────────────
        if self._status_file:
            try:
                self._write_status_file(recon, equity_drift_usd, equity_drift_pct)
            except Exception as e:  # pragma: no cover
                logger.debug("Track B status_file write failed: %s", e)

    # ── Status file ────────────────────────────────────────────────

    def _write_status_file(
        self, recon: dict[str, Any],
        equity_drift_usd: float, equity_drift_pct: float,
    ) -> None:
        """Atomic JSON write so external readers never see a half-
        written file. Use os.replace for atomicity."""
        payload = {
            "tick_time_utc": self.state.last_tick_utc,
            "tier": self.state.last_tier,
            "trading_blocked": self.state.trading_blocked,
            "lethal_triggered": self.state.lethal_triggered,
            "shadow_mode": self._shadow_mode,
            "qty_drift_count": self.state.last_qty_drift_count,
            "stop_drift_count": self.state.last_stop_drift_count,
            "equity_drift_usd": round(equity_drift_usd, 2),
            "equity_drift_pct": round(equity_drift_pct, 6),
            "consecutive_violation_ticks": self.state.consecutive_violation_ticks,
            "broker_reachable": recon.get("broker_reachable", False),
            "broker_equity": recon.get("broker_equity"),
            "details": recon.get("details", []),
        }
        tmp = self._status_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, self._status_file)
