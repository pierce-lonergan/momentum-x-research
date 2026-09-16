"""doc 268 - VERDICT LIFECYCLE LEDGER (VLL). DORMANT-C: pure logging, NEVER raises, no behavior change.

Purpose: no BUY/SHORT verdict may die silently again. On 6/8, 14 verdicts (7 = the day's rockets) died with no
order_id and no journal stamp; forensics named the killers (D200-E4 catalyst gate main.py:~3453, D204 news gate
main.py:~6676/6975, D56 carried-dupes). This module gives every drop point a one-line structured emit:

    from src.ops.verdict_ledger import vll_emit
    vll_emit("BLOCKED_CATALYST_GATE", ticker, reason="catalyst_type=NONE", mfcs=0.289)

Events append to data/ops/verdict_trace_<ET-date>.jsonl. Terminal stages (one per verdict-attempt):
SUBMITTED / BLOCKED_<GATE> / ERROR. The nightly grader joins journal BUY rows vs trace terminals + broker
fills; any journal-BUY ticker with no terminal = ORPHAN -> flagged (the 6/8 class becomes loud).

Failure philosophy: this function must NEVER affect the trading hot path. Any internal error is swallowed
(single debug line, best-effort). Writes are line-buffered appends; one file per ET session date.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DIR = str(_PROJECT_ROOT / "data" / "ops")


def _et_date() -> str:
    # EDT/EST nuance is irrelevant at this granularity (sessions never straddle the 4-5h band edge at midnight ET
    # in a way that changes the trading date for events emitted during/around market hours).
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def vll_emit(stage: str, ticker: str, reason: str = "", **context) -> None:
    """Append one structured lifecycle event. Never raises; never blocks meaningfully."""
    try:
        os.makedirs(_DIR, exist_ok=True)
        evt = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stage": str(stage)[:40],
            "ticker": str(ticker)[:12],
            "reason": str(reason)[:160],
        }
        for k, v in context.items():
            try:
                json.dumps(v)
                evt[str(k)[:24]] = v
            except (TypeError, ValueError):
                evt[str(k)[:24]] = str(v)[:80]
        path = os.path.join(_DIR, f"verdict_trace_{_et_date()}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt) + "\n")
    except Exception as e:  # noqa: BLE001 - by design: observability must never hurt the hot path
        try:
            logger.debug("vll_emit swallowed: %s", str(e)[:80])
        except Exception:
            pass
