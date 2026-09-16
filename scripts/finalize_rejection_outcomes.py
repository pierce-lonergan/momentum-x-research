#!/usr/bin/env python
"""doc 182: Post-close finalizer for the rejection-outcome shadow.

Reads the day's shadow file, finds the `kind == "rejection_outcome"` rows logged
by src/shadow/rejection_outcome_shadow.py at each faller (D160) / entry-delay
(D170) rejection, fetches each name's FORWARD price action after the decision,
and grades whether the gate was RIGHT (the name faded) or WRONG (it ran).

Run after close, e.g.:
    python scripts/finalize_rejection_outcomes.py            # today
    python scripts/finalize_rejection_outcomes.py 2026-05-29 # a specific date

Outputs:
  - data/shadow/rejection_outcomes_graded_<date>.jsonl  (one graded row per rejection)
  - a per-gate summary to stdout: count, %faded (block correct), %ran (block wrong),
    mean forward return — the data to calibrate the faller 0.60 threshold and the
    D170 10%-drawdown limit, and to decide whether it is safe to LOOSEN the gates.

NO lookahead: decision-time fields were frozen at log time; this batch only adds
the `outcome` sub-dict from bars strictly AFTER decision_ts.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import Settings  # noqa: E402
from src.data.alpaca_client import AlpacaDataClient  # noqa: E402

_SHADOW_DIR = _ROOT / "data" / "shadow"
# A "meaningful run" the gate would have missed (block WRONG); below this the
# block was at worst neutral. Tunable; matches the ~+10% scalp the bot targets.
_RUN_THRESHOLD = 0.10
# A "real fade" that validates the block (block CORRECT).
_FADE_THRESHOLD = -0.05


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _price_at_or_after(bars: list[dict], when: datetime) -> float | None:
    """First bar close at/after `when` (bars sorted ascending by time)."""
    for b in bars:
        t = b.get("t") or b.get("timestamp")
        if t is None:
            continue
        bt = _parse_ts(t) if isinstance(t, str) else t
        if bt >= when:
            c = b.get("c", b.get("close"))
            return float(c) if c is not None else None
    return None


async def _grade_one(client: AlpacaDataClient, row: dict) -> dict:
    ticker = row["ticker"]
    p0 = row.get("decision_price")
    out = {"filled": False, "return_15m": None, "return_60m": None,
           "return_eod": None, "mfe_pct": None, "mae_pct": None, "verdict": "no_data"}
    if not p0:
        return {**row, "outcome": out}
    try:
        d_ts = _parse_ts(row["decision_ts_utc"])
        # Session close = 20:00 UTC (16:00 ET) of the decision date.
        eod = d_ts.replace(hour=20, minute=0, second=0, microsecond=0)
        bars = await client.get_bars(
            ticker, timeframe="1Min",
            start=d_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=eod.strftime("%Y-%m-%dT%H:%M:%SZ"),
            limit=500,
        )
        if not bars:
            return {**row, "outcome": out}
        out["filled"] = True
        p15 = _price_at_or_after(bars, d_ts + timedelta(minutes=15))
        p60 = _price_at_or_after(bars, d_ts + timedelta(minutes=60))
        peod = None
        for b in reversed(bars):
            c = b.get("c", b.get("close"))
            if c is not None:
                peod = float(c); break
        highs = [float(b["h"]) for b in bars if b.get("h") is not None]
        lows = [float(b["l"]) for b in bars if b.get("l") is not None]
        if p15:
            out["return_15m"] = round((p15 - p0) / p0, 4)
        if p60:
            out["return_60m"] = round((p60 - p0) / p0, 4)
        if peod:
            out["return_eod"] = round((peod - p0) / p0, 4)
        if highs:
            out["mfe_pct"] = round((max(highs) - p0) / p0, 4)
        if lows:
            out["mae_pct"] = round((min(lows) - p0) / p0, 4)
        # Verdict: was blocking this name right?
        mfe = out["mfe_pct"] or 0.0
        reod = out["return_eod"] if out["return_eod"] is not None else 0.0
        if mfe >= _RUN_THRESHOLD and reod >= 0:
            out["verdict"] = "block_WRONG_ran"      # gate missed a real runner
        elif reod <= _FADE_THRESHOLD:
            out["verdict"] = "block_CORRECT_faded"  # gate avoided a fade
        else:
            out["verdict"] = "block_neutral"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    return {**row, "outcome": out}


async def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    shadow_file = _SHADOW_DIR / f"shadow_{date}.jsonl"
    if not shadow_file.exists():
        print(f"No shadow file for {date}: {shadow_file}")
        return 1
    rejections = []
    for line in shadow_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") == "rejection_outcome":
            rejections.append(r)
    if not rejections:
        print(f"No rejection_outcome rows in {shadow_file.name}")
        return 0

    settings = Settings()
    client = AlpacaDataClient(settings.alpaca)
    try:
        graded = []
        for r in rejections:
            graded.append(await _grade_one(client, r))
    finally:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close:
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    out_file = _SHADOW_DIR / f"rejection_outcomes_graded_{date}.jsonl"
    out_file.write_text("\n".join(json.dumps(g) for g in graded) + "\n", encoding="utf-8")

    # ── Per-gate summary ───────────────────────────────────────────────
    print(f"\n=== Rejection-outcome grades for {date} ({len(graded)} rejections) ===")
    by_gate: dict[str, list[dict]] = {}
    for g in graded:
        by_gate.setdefault(g.get("gate", "?"), []).append(g)
    for gate, rows in sorted(by_gate.items()):
        graded_rows = [r for r in rows if r["outcome"].get("filled")]
        n = len(graded_rows)
        if not n:
            print(f"  {gate}: {len(rows)} rejections, 0 with price data")
            continue
        wrong = sum(1 for r in graded_rows if r["outcome"]["verdict"] == "block_WRONG_ran")
        correct = sum(1 for r in graded_rows if r["outcome"]["verdict"] == "block_CORRECT_faded")
        neutral = n - wrong - correct
        eods = [r["outcome"]["return_eod"] for r in graded_rows if r["outcome"]["return_eod"] is not None]
        mean_eod = sum(eods) / len(eods) if eods else 0.0
        print(f"  {gate}: {n} graded | CORRECT(faded)={correct} ({100*correct/n:.0f}%) "
              f"| WRONG(ran)={wrong} ({100*wrong/n:.0f}%) | neutral={neutral} "
              f"| mean EOD return={mean_eod:+.1%}")
        for r in graded_rows:
            if r["outcome"]["verdict"] == "block_WRONG_ran":
                print(f"      MISSED RUNNER: {r['ticker']} mfe={r['outcome']['mfe_pct']:+.1%} "
                      f"eod={r['outcome']['return_eod']:+.1%} (score={r.get('score')}, mfcs={r.get('mfcs')})")
    print(f"\nGraded file: {out_file}")
    print("Interpretation: high CORRECT% = the gate is right (keep/tighten). "
          "Any WRONG(ran) = a runner the gate cost us (loosen / add a continuation override).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
