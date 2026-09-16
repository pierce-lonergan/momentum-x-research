"""D308 (2026-05-19) — Stop-decision shadow log.

Captures both the orchestrator's wide stop (ATR + gap-floor + D122 widening)
AND the D142 Phase 1 tightened stop AT EVERY OTO SUBMISSION, regardless of
whether the order ultimately fills.

PURPOSE — T1 of the "stop-widening" validation plan (doc 169):
  Production behavior is UNCHANGED. The bot still submits the tight Phase 1
  stop. This module just emits a structured event so the offline replayer
  (``scripts/stop_widening_replay.py``) can compute what would have happened
  with the wider ATR stop. After ~3 trading days of clean shadow data we
  can decide whether to flip to the wide-stop arm in T2 A/B testing.

WHY THIS MATTERS:
  Diagnostic on 2026-05-19 found 7/7 round-trips in the last week were
  stopped out at exactly -1.5% within 0-5 minutes of entry. The
  orchestrator had computed wide stops ($3.43 for CISS = -26.6%), but
  alpaca_executor.py:282-302 silently overrides with ``entry × 1.5%``
  before submission. Every winner gets shaken out by noise. NXXT only
  ran (+44%) because the bot crashed and the stop wasn't actively managed.

OUTPUT FORMAT:
  data/shadow_stops/stop_decisions_YYYY-MM-DD.jsonl
  One line per OTO submission attempt. Each line is a self-describing
  JSON dict with: timestamp, symbol, entry, stops (atr/phase1/submitted),
  strategy chosen, qty, qty_halved (what T2 would size), gap_pct, atr.

DEFENSIVE GUARANTEES:
  - Never raises. A logging failure must NEVER block an OTO submission.
  - Synchronous append-only writes (line-buffered) so no events lost on
    crash mid-write.
  - Self-contained: no dependency on TradeJournal, ExecutionRecorder,
    or any module that imports it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.fast_json import dumps as _fast_dumps

logger = logging.getLogger(__name__)


class StopDecisionLog:
    """Append-only shadow log of stop-distance decisions per OTO submission.

    Used by ``AlpacaExecutor.submit_entry`` to record the full set of
    candidate stops (wide ATR, tight Phase 1, what was submitted) so an
    offline replayer can compute hypothetical P&L under wider stops.
    """

    def __init__(self, output_dir: str = "data/shadow_stops") -> None:
        self._dir = Path(output_dir)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception as _e:  # noqa: silent-handler -- log init is best-effort
            logger.warning("D308: stop_decisions dir create failed (%s); "
                            "shadow log disabled", _e)
            self._dir = None
        self._path: Path | None = None
        self._count = 0
        if self._dir is not None:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._path = self._dir / f"stop_decisions_{today}.jsonl"
            logger.info("D308: StopDecisionLog initialized -> %s", self._path)

    def log_stop_decision(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        atr_stop: float,
        phase1_stop: float | None,
        phase1_pct: float | None,
        submitted_stop: float,
        submitted_strategy: str,
        qty: int,
        # Optional context
        gap_pct: float | None = None,
        atr_14d: float | None = None,
        kelly_tier: int | None = None,
        mfcs: float | None = None,
        d106_class: str | None = None,
        target_prices: list[float] | None = None,
        verdict_id: str | None = None,
    ) -> None:
        """Emit one stop_decision event. Never raises.

        Required positional fields are the minimum needed for offline
        replay: symbol, entry, both candidate stops, what was submitted,
        and qty for dollar-P&L computation.

        Optional fields are captured for downstream slice-and-dice (does
        D106 PROMOTIONAL_EARLY consistently underperform? do high-gap
        names need different stop multipliers?) but are NOT required.
        """
        if self._path is None:
            return  # init failed; degrade silently
        try:
            entry_f = float(entry_price)
            atr_stop_f = float(atr_stop)
            submitted_stop_f = float(submitted_stop)
            qty_i = int(qty)
            # Compute the two distance pcts (signed, negative for long
            # stops below entry). Defensive: zero entry -> 0%.
            if entry_f > 0:
                atr_dist_pct = round((atr_stop_f / entry_f - 1.0) * 100, 4)
                sub_dist_pct = round((submitted_stop_f / entry_f - 1.0) * 100, 4)
                phase1_dist_pct = (
                    round((phase1_stop / entry_f - 1.0) * 100, 4)
                    if phase1_stop is not None else None
                )
            else:
                atr_dist_pct = sub_dist_pct = 0.0
                phase1_dist_pct = None
            # Halved qty -- what T2 wide-stop arm would submit. Floor to
            # at least 1 share if qty > 0 so the replayer doesn't drop
            # the event entirely on small positions.
            qty_halved = max(1, qty_i // 2) if qty_i > 0 else 0
            event = {
                "event": "stop_decision",
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "side": side,
                "entry": round(entry_f, 4),
                "atr_stop": round(atr_stop_f, 4),
                "atr_dist_pct": atr_dist_pct,
                "phase1_stop": (round(float(phase1_stop), 4)
                                 if phase1_stop is not None else None),
                "phase1_pct_config": phase1_pct,
                "phase1_dist_pct": phase1_dist_pct,
                "submitted_stop": round(submitted_stop_f, 4),
                "submitted_dist_pct": sub_dist_pct,
                "submitted_strategy": submitted_strategy,
                "qty": qty_i,
                "qty_halved_hypothetical": qty_halved,
                "gap_pct": (round(float(gap_pct), 4)
                            if gap_pct is not None else None),
                "atr_14d": (round(float(atr_14d), 6)
                            if atr_14d is not None else None),
                "kelly_tier": kelly_tier,
                "mfcs": (round(float(mfcs), 4) if mfcs is not None else None),
                "d106_class": d106_class,
                "target_prices": (
                    [round(float(t), 4) for t in target_prices]
                    if target_prices else None
                ),
                "verdict_id": verdict_id,
            }
            line = _fast_dumps(event)
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
            self._count += 1
        except Exception as _e:  # noqa: silent-handler -- shadow log MUST NOT block trading
            logger.warning(
                "D308: stop_decision write failed for %s (%s); "
                "production unaffected",
                symbol, _e,
            )

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def count(self) -> int:
        return self._count
