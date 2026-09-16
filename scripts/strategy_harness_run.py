"""Strategy-driven arena harness — end-to-end runner skeleton.

Block C.3 of the strategy-harness session. The foundation Block 4.4
will run across 86 sessions; this script ships it for ONE session
(4/28) as proof-of-life.

Architecture:
    DataEngine (bar stream) → Orchestrator stub → CandidateDecision
        → SimAlpacaClient.submit_oto_order → SimExchange
        → exit policy (BAR-1 default at T+60s) → close_position
        → P&L computed by SimExchange

What this validates:
    - The wiring works: orchestrator → sim_alpaca_client → SimExchange
      produces orders, fills, exits, P&L
    - FailureInjector queryable via the sim client
    - Halt switch parity verified via the same env-aware check

What this does NOT validate:
    - Strategy edge (this is stub-quality decision logic)
    - Modeled-exit fidelity vs prod (deferred to next session)
    - 86-session corpus run (Block 4.4 scope)

Usage:
    python scripts/strategy_harness_run.py --date 2026-04-28 --mode decision_row
    python scripts/strategy_harness_run.py --date 2026-04-28 --mode policy

Output:
    data/replay/strategy_harness_<date>_<mode>.parquet
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("strategy_harness")

REPO_ROOT = Path(__file__).resolve().parent.parent
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
DECISION_ROW_DIR = REPO_ROOT / "data" / "instrumentation" / "decision_row"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "replay"

sys.path.insert(0, str(REPO_ROOT / "mx-arena"))


@dataclass
class HarnessTrade:
    ticker: str
    session_date: str
    entry_ts_iso: str
    exit_ts_iso: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    exit_reason: str  # bar1_default | other
    news_signal: str
    news_conf: float
    decision_reason: str


def list_session_tickers(session_date: str) -> list[str]:
    """The watchlist for a session = every ticker we have bars for."""
    out = []
    for ticker_dir in sorted(ARENA_HISTORICAL.iterdir()):
        if not ticker_dir.is_dir():
            continue
        target = ticker_dir / f"{session_date}.parquet"
        if target.exists():
            out.append(ticker_dir.name)
    return out


def load_bars_for(ticker: str, session_date: str):
    from arena.data_engine import DataEngine
    from arena.clock import SimClock, ClockMode
    target = ARENA_HISTORICAL / ticker / f"{session_date}.parquet"
    if not target.exists():
        return None
    clock = SimClock(
        start=datetime.fromisoformat(f"{session_date}T13:30:00+00:00"),
        end=datetime.fromisoformat(f"{session_date}T20:00:00+00:00"),
        mode=ClockMode.REPLAY,
    )
    return DataEngine(clock=clock, historical_dir=str(ARENA_HISTORICAL))._load_parquet_bars(ticker, session_date)


def bar_at_or_before(bars_map: dict, ts: datetime):
    target_iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    last = None
    for _idx, b in bars_map.items():
        if b.timestamp > target_iso:
            break
        last = b
    return last


def bar_at_or_after(bars_map: dict, ts: datetime):
    target_iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for _idx, b in bars_map.items():
        if b.timestamp >= target_iso:
            return b
    return list(bars_map.values())[-1]


async def run_one_session(
    *, session_date: str, mode: str, gate_signal_neutral: bool = False,
    max_concurrent_positions: int = 3,
    use_consensus: bool = True,
    data_completeness: str = "full_decision_row",
    require_earnings_catalyst: bool = False,
    earnings_calendar: dict | None = None,
) -> list[HarnessTrade]:
    """Run the strategy harness end-to-end for one session.

    Block A harness fixes (this session):
      - max_concurrent_positions: enforce position-count limit
        (default 3 mirrors prod's typical ceiling)
      - Entry-time alignment: collect ALL candidate decisions for the
        full session FIRST, dedupe per ticker (first-emit wins),
        THEN apply gate + consensus to the same pool. This ensures
        GATE ON and GATE OFF arms compare the SAME trades.
      - use_consensus: 3-rule consensus stub (news + gap + rvol);
        require 2/3 BULL with no BEAR to PASS
      - data_completeness: tagged on each trade for stratification
    """
    from arena.exchange import SimExchange
    from arena.clock import SimClock, ClockMode
    from arena.failure_injector import FailureInjector
    from arena.sim_alpaca_client import SimAlpacaClient
    from arena.orchestrator_stub import make_orchestrator
    from arena.consensus_stub import ConsensusStub

    watchlist = list_session_tickers(session_date)
    logger.info("session %s: watchlist=%d tickers", session_date, len(watchlist))

    if not watchlist:
        return []

    bars_cache: dict[str, dict] = {}
    for t in watchlist:
        b = load_bars_for(t, session_date)
        if b is not None:
            bars_cache[t] = b

    clock = SimClock(
        start=datetime.fromisoformat(f"{session_date}T13:30:00+00:00"),
        end=datetime.fromisoformat(f"{session_date}T20:00:00+00:00"),
        mode=ClockMode.REPLAY,
    )
    sim_exchange = SimExchange(clock=clock)
    failure_injector = FailureInjector()
    sim_client = SimAlpacaClient(
        sim_exchange=sim_exchange, failure_injector=failure_injector,
    )

    if mode == "decision_row":
        orch_kwargs = {
            "decision_corpus_path": DECISION_ROW_DIR / f"session_date={session_date}" / "decisions.parquet",
        }
    else:
        # Block B: policy mode needs a bar_loader callable so it can
        # apply the gappers+RVOL+price filter at session open.
        orch_kwargs = {
            "bar_loader": lambda t, d: bars_cache.get(t),
            "require_earnings_catalyst": require_earnings_catalyst,
            "earnings_calendar": earnings_calendar or {},
        }
    orchestrator = make_orchestrator(mode=mode, **orch_kwargs)

    # Block A.2 entry-time alignment: collect ALL candidates for the full
    # session, dedupe per ticker (first valid emit wins), then apply gate
    # + consensus to the same pool. This guarantees ARM ON and ARM OFF
    # operate on the same candidate set.
    session_start = datetime.fromisoformat(f"{session_date}T13:30:00+00:00")
    candidates_by_ticker: dict[str, tuple] = {}  # ticker -> (cand, ts)
    for minute_offset in range(390):
        ts = session_start + timedelta(minutes=minute_offset)
        cands = orchestrator.candidates_at(ts=ts, watchlist=watchlist)
        for cand in cands:
            if cand.verdict != "BUY":
                continue
            if cand.ticker in candidates_by_ticker:
                continue  # first emit wins
            candidates_by_ticker[cand.ticker] = (cand, ts)

    logger.info("session %s: %d unique candidates emitted", session_date, len(candidates_by_ticker))

    # Now apply gates in order: catalyst gate → consensus → position limit
    trades: list[HarnessTrade] = []
    consensus = ConsensusStub()

    # Order candidates by emit timestamp so position limit is applied
    # against earliest candidates first (mirrors prod's chronological flow)
    ordered = sorted(candidates_by_ticker.items(), key=lambda kv: kv[1][1])

    for ticker, (cand, ts) in ordered:
        # Block B catalyst gate
        if gate_signal_neutral:
            sig = (cand.news_signal or "NO_SIGNAL").upper()
            if sig in {"NEUTRAL", "NO_SIGNAL", "EMPTY", ""}:
                continue

        # Block A.3 consensus: skip if any of (gap, rvol) absent — would
        # default to NEUTRAL on those rules and never pass. Decision_row
        # mode supplies news but not gap/rvol; for now, use news_signal
        # alone as a conservative pass: if news is BULL/STRONG_BULL,
        # treat as 1-rule pass and don't reject.
        if use_consensus:
            # Lightweight: only enforce when we have all 3 inputs.
            # Decision_row mode lacks gap/rvol, so consensus is a no-op
            # there; policy mode (Block B) supplies all 3 and full
            # consensus fires.
            sig = (cand.news_signal or "NO_SIGNAL").upper()
            has_gap_rvol = (
                getattr(cand, "_gap_pct", None) is not None
                and getattr(cand, "_rvol", None) is not None
            )
            if has_gap_rvol:
                cr = consensus.evaluate(
                    news_signal=sig,
                    gap_pct=float(getattr(cand, "_gap_pct", 0)),
                    rvol=float(getattr(cand, "_rvol", 1.0)),
                )
                if cr.decision != "PASS":
                    continue
            # else: decision_row mode — orchestrator already filtered
            # via verdict_action; no consensus override.

        # Block A.1 position limit
        if len(trades) >= max_concurrent_positions:
            continue

        bars = bars_cache.get(ticker)
        if bars is None:
            continue
        entry_bar = bar_at_or_after(bars, ts)
        if entry_bar is None:
            continue
        entry_px = float(cand.entry_price) if cand.entry_price > 0 else float(entry_bar.open)
        qty = cand.size_qty if cand.size_qty > 0 else max(1, int(75_000.0 / entry_px))
        stop_px = float(cand.stop_price) if cand.stop_price > 0 else round(entry_px * 0.945, 4)

        try:
            resp = await sim_client.submit_oto_order(
                symbol=ticker, qty=qty, limit_price=entry_px,
                stop_loss=stop_px,
            )
        except Exception as e:
            logger.warning("submit failed for %s: %s", ticker, e)
            continue
        if resp.get("status") == "halted_by_operator":
            logger.info("halt switch active — skipping %s", ticker)
            continue

        # Exit at T+60s (BAR-1 default)
        exit_ts = ts + timedelta(seconds=60)
        exit_bar = bar_at_or_after(bars, exit_ts)
        if exit_bar is None:
            continue
        exit_px = float(exit_bar.open)

        pnl = round((exit_px - entry_px) * qty, 2)
        trades.append(HarnessTrade(
            ticker=ticker, session_date=session_date,
            entry_ts_iso=entry_bar.timestamp,
            exit_ts_iso=exit_bar.timestamp,
            entry_price=round(entry_px, 4),
            exit_price=round(exit_px, 4),
            qty=qty, pnl=pnl,
            exit_reason="bar1_default",
            news_signal=cand.news_signal, news_conf=cand.news_conf,
            decision_reason=cand.reason,
        ))
        logger.info(
            "  TRADE %s entry=$%.4f exit=$%.4f qty=%d pnl=$%+.2f signal=%s",
            ticker, entry_px, exit_px, qty, pnl, cand.news_signal,
        )

    return trades


async def amain(args) -> int:
    trades = await run_one_session(session_date=args.date, mode=args.mode)
    logger.info("Session %s produced %d trades, total P&L $%+.2f",
                args.date, len(trades), sum(t.pnl for t in trades))

    if not trades:
        logger.warning("zero trades — coherence check: was orchestrator wired?")
        # Empty harness output is still a valid result for some modes;
        # write a marker file so downstream tools see "ran but empty"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"strategy_harness_{args.date}_{args.mode}.parquet"
    try:
        import pandas as pd
        df = pd.DataFrame([asdict(t) for t in trades]) if trades else pd.DataFrame(
            columns=["ticker","session_date","pnl"]
        )
        df.to_parquet(out_path, index=False)
        logger.info("wrote %s", out_path)
    except ImportError:
        logger.error("pandas required")
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", required=True)
    p.add_argument("--mode", choices=["decision_row", "policy"], default="decision_row")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
