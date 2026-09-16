"""Parse production session logs into DecisionRow objects.

Tier 4 #15: enables BACKWARD-LOOKING decision replay against ALREADY-
SHIPPED sessions (today's, yesterday's, etc.) without needing the
orchestrator hook to be wired first. Closes the chicken-and-egg
problem: the hook needs validation, validation needs captured data,
captured data needs the hook.

The parser extracts DecisionRow fields by pattern-matching on the
canonical orchestrator log lines. It's tolerant — fields not present
in the log default to safe values (e.g., empty agent_signals list,
zero confidence). The output is a JSONL stream so it can be replayed,
filtered, or aggregated like any other DecisionRow corpus.

Limitations (acknowledged):
  - Only D124 + MFCS-threshold + verdict are reliably extractable from
    today's log format. Agent signal breakdowns require parsing the
    "D101 TECH X: signal=..." style lines and joining by ticker+cycle.
  - VERDICT_SUMMARY lines give us per-cycle aggregates but not per-
    candidate intermediate state. This is good-enough for the Bug AK
    validation use case (D124 rejection patterns) but the orchestrator
    hook is still needed for full coverage.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# ── Log-line patterns ─────────────────────────────────────────────────


# 09:31:00 | src.core.orchestrator | INFO  | LIDR D124 CONSENSUS ALIGNMENT (tier=A:numeric): 0 bullish (conf=0.00) vs 1 bearish (conf=0.49, max=0.49) — REJECT
# (Old format pre-Bug AK — no tier, no max field)
# 09:31:00 | src.core.orchestrator | INFO  | LIDR D124 CONSENSUS ALIGNMENT: 0 bullish (conf=0.00) vs 1 bearish (conf=0.49) — REJECT (bearish dominant)
D124_REJECT_RE = re.compile(
    r"(\d\d:\d\d:\d\d).*?(\w+) D124 CONSENSUS ALIGNMENT(?: \(tier=([^)]+)\))?:"
    r" (\d+) bullish \(conf=([\d.]+)\) vs (\d+) bearish "
    r"\(conf=([\d.]+)(?:, max=([\d.]+))?\)"
)

# 09:31:01 | momentum_x | INFO  |   >>> OGN: BUY (MFCS=0.555, conf=0.55) MFCS=0.555 | Debate=NO | Risk=PASS
# 09:31:01 | momentum_x | INFO  |       LIDR: HOLD (MFCS=0.226, conf=0.23) MFCS=0.226 | Debate=NO | Risk=PASS
VERDICT_LINE_RE = re.compile(
    r"(\d\d:\d\d:\d\d).*?(?:>>> )?(\w+):"
    r" (BUY|STRONG_BUY|HOLD|NO_TRADE) \(MFCS=([\d.]+), conf=([\d.]+)\)"
)

# 09:31:01 | momentum_x | INFO  | ═══ PHASE 2 VERDICT SUMMARY ═══ 9 evaluated -> 1 BUY, 5 HOLD, 3 NO_TRADE | Best MFCS: 0.555 | Threshold: 0.25
SUMMARY_RE = re.compile(
    r"(\d\d:\d\d:\d\d).*?PHASE 2 VERDICT SUMMARY.*? (\d+) evaluated -> "
    r"(\d+) BUY, (\d+) HOLD, (\d+) NO_TRADE"
)


# ── Public API ────────────────────────────────────────────────────────


def parse_log_file(
    log_path: str | Path, *, session_date: str | None = None,
) -> Iterator[dict]:
    """Stream DecisionRow-shaped dicts from a production session log.

    Yields one dict per VERDICT line found in the log. Joins with the
    nearest preceding D124 line (if any) for the same ticker.

    Args:
        log_path: Path to logs/momentum_<date>.log.
        session_date: ISO date string (YYYY-MM-DD); inferred from
                      filename or set to today UTC if not provided.

    Yields:
        dict ready to be passed to DecisionRow(**d) (subject to
        Pydantic validation; missing required fields will raise).
    """
    log_path = Path(log_path)
    if session_date is None:
        # Infer from filename: momentum_YYYY-MM-DD.log
        m = re.search(r"(\d{4}-\d{2}-\d{2})", log_path.stem)
        session_date = m.group(1) if m else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Maintain a per-ticker buffer of the most recent D124 state seen.
    # When a VERDICT line arrives, we attach the D124 buffer to it.
    # When a D124 REJECT line arrives WITHOUT a downstream VERDICT
    # line, we emit it as its own NO_TRADE decision (D124 reject is
    # a terminal verdict — production never reaches the verdict-summary
    # line for that ticker in that cycle).
    pending_d124: dict[str, dict] = {}
    cycle_number = 0
    last_summary_time: str | None = None
    # Tickers whose D124 reject we've already emitted in this cycle
    # (so the verdict-summary line doesn't double-count if it appears)
    emitted_d124_this_cycle: set[str] = set()

    with log_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            # Track cycle boundaries via VERDICT SUMMARY lines
            sm = SUMMARY_RE.search(line)
            if sm:
                t = sm.group(1)
                if t != last_summary_time:
                    cycle_number += 1
                    last_summary_time = t
                    pending_d124.clear()  # new cycle, reset
                    emitted_d124_this_cycle.clear()
                continue

            # Capture D124 facts AND emit them as NO_TRADE decisions.
            # The D124 reject IS the verdict — there's no downstream
            # VERDICT line for this ticker in this cycle.
            dm = D124_REJECT_RE.search(line)
            if dm:
                t, ticker, tier, bull_n, bull_c, bear_n, bear_c, max_c = dm.groups()
                d124_state = {
                    "time": t,
                    "tier": tier or "v1-pre-AK",
                    "d124_bullish_count": int(bull_n),
                    "d124_bearish_count": int(bear_n),
                    "d124_bullish_conf": float(bull_c),
                    "d124_bearish_conf": float(bear_c),
                    "d124_max_bearish_conf": float(max_c) if max_c else float(bear_c),
                    "d124_was_rejected": True,
                    "d124_rejection_tier": tier or "v1-pre-AK",
                }
                pending_d124[ticker] = d124_state
                emitted_d124_this_cycle.add(ticker)
                # Emit a NO_TRADE row for this D124 rejection
                yield {
                    "decision_id": str(uuid.uuid4()),
                    "timestamp": _parse_log_timestamp(t, session_date),
                    "session_date": session_date,
                    "cycle_number": cycle_number,
                    "ticker": ticker,
                    "candidate_gap_pct": 0.0, "candidate_rvol": 0.0,
                    "candidate_current_price": 0.0,
                    "candidate_float_shares": None, "candidate_market_cap": None,
                    "candidate_gap_classification": "",
                    "candidate_has_news_catalyst": False,
                    "agent_signals": [],
                    "n_agents_total": 0, "n_agents_returned": 0, "n_agents_failed": 0,
                    "mfcs_score": 0.0,
                    "mfcs_components": {},
                    **{k: v for k, v in d124_state.items()
                       if k.startswith("d124_")},
                    "mfcs_threshold_static": 0.25,
                    "mfcs_threshold_effective": 0.25,
                    "mfcs_threshold_passed": False,
                    "verdict_action": "NO_TRADE",
                    "verdict_reason": f"D124 consensus alignment (tier={d124_state['tier']})",
                    "verdict_confidence": 0.0,
                    "led_to_trade": False, "trade_oid": None, "trade_realized_pnl": None,
                }
                continue

            # Capture VERDICT facts and emit a row
            vm = VERDICT_LINE_RE.search(line)
            if vm:
                t, ticker, action, mfcs_str, conf_str = vm.groups()
                # Skip if this ticker already had a D124 reject emitted
                # in this cycle (the verdict-summary line repeats them)
                if ticker in emitted_d124_this_cycle:
                    continue
                mfcs = float(mfcs_str)
                conf = float(conf_str)
                d124_state = pending_d124.get(ticker, {
                    "d124_bullish_count": 0,
                    "d124_bearish_count": 0,
                    "d124_bullish_conf": 0.0,
                    "d124_bearish_conf": 0.0,
                    "d124_max_bearish_conf": 0.0,
                    "d124_was_rejected": False,
                    "d124_rejection_tier": None,
                })
                # Build a minimal DecisionRow-shaped dict. Fields not
                # extractable from the log default to safe values.
                # The schema's `extra="forbid"` means we need to fill
                # ALL required fields.
                row = {
                    "decision_id": str(uuid.uuid4()),
                    "timestamp": _parse_log_timestamp(t, session_date),
                    "session_date": session_date,
                    "cycle_number": cycle_number,
                    "ticker": ticker,
                    # Candidate snapshot — not in verdict log; placeholder
                    "candidate_gap_pct": 0.0,
                    "candidate_rvol": 0.0,
                    "candidate_current_price": 0.0,
                    "candidate_float_shares": None,
                    "candidate_market_cap": None,
                    "candidate_gap_classification": "",
                    "candidate_has_news_catalyst": False,
                    # Agent signals — not in verdict log; empty
                    "agent_signals": [],
                    "n_agents_total": 0,
                    "n_agents_returned": 0,
                    "n_agents_failed": 0,
                    # MFCS
                    "mfcs_score": mfcs,
                    "mfcs_components": {},
                    # D124
                    "d124_bullish_count": d124_state["d124_bullish_count"],
                    "d124_bearish_count": d124_state["d124_bearish_count"],
                    "d124_bullish_conf": d124_state["d124_bullish_conf"],
                    "d124_bearish_conf": d124_state["d124_bearish_conf"],
                    "d124_max_bearish_conf": d124_state["d124_max_bearish_conf"],
                    "d124_was_rejected": d124_state["d124_was_rejected"],
                    "d124_rejection_tier": d124_state["d124_rejection_tier"],
                    # Threshold (extracted from summary line; default if not seen)
                    "mfcs_threshold_static": 0.25,
                    "mfcs_threshold_effective": 0.25,
                    "mfcs_threshold_passed": mfcs >= 0.25,
                    # Verdict
                    "verdict_action": action,
                    "verdict_reason": "",
                    "verdict_confidence": conf,
                    # Outcome — not yet known
                    "led_to_trade": False,
                    "trade_oid": None,
                    "trade_realized_pnl": None,
                }
                yield row


def _parse_log_timestamp(time_str: str, session_date: str) -> str:
    """Combine HH:MM:SS log time + session_date into ISO datetime str."""
    return f"{session_date}T{time_str}+00:00"


def parse_log_to_jsonl(
    log_path: str | Path, output_path: str | Path,
    *, session_date: str | None = None,
) -> int:
    """Parse a session log + write DecisionRow dicts to JSONL.

    Returns the number of rows written.
    """
    log_path = Path(log_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with output_path.open("w", encoding="utf-8") as fh:
        for row in parse_log_file(log_path, session_date=session_date):
            fh.write(json.dumps(row, default=str) + "\n")
            n += 1
    return n
