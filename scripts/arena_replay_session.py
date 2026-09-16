"""Replay production trades through arena's fill model + spread model.

Block 1 (B1.1) of the arena↔prod parity program — see
docs/research-log/58_arena_prod_parity_plan.md.

NOTE: a different tool `scripts/replay_session.py` exists (Tier 4 #15
rule-set replay against the prod log). This script is the arena
shadow-replay; the names are kept distinct.

SCOPE NOTE — what this script tests:
  This is a SHADOW REPLAY. We do NOT drive arena's full SimExchange
  with a strategy bot. We feed arena's FillModel + SpreadModel the
  exact entry+exit timestamps and order shapes that production
  recorded, and compare arena's fill prices against production's
  fills. This isolates one variable: arena's pricing fidelity.

  Block 4.4 (full historical bar corpus) will run the actual strategy
  through arena. This script is the simpler, faster check that
  gates that work.

SCOPE NOTE — bar coverage gap:
  Production trades on a wider universe than what data/bar_recordings/
  captures. For 2026-04-22 → 2026-04-28, only ~7 of 12 deduped trades
  have matching bar files. The other ~5 (AGPU 4/22, MAAS 4/22, SCNI
  4/24, ONMD 4/24, SEGG 4/28) cannot be replayed and are surfaced in
  the diff report as a separate "missing_bars" category. This itself
  is a Block 2 finding (gap register entry pending).

SCOPE NOTE — LIDR cross-session carry:
  Excluded per session scope (Block 4 stretch).

Usage:
    python scripts/arena_replay_session.py --since 2026-04-22 --until 2026-04-28

    # Single session
    python scripts/arena_replay_session.py --date 2026-04-28

Output:
    data/replay/arena_session_<since>_<until>.parquet
    Columns: ticker, session_date, prod_entry_ts, prod_entry_px,
             arena_entry_ts, arena_entry_px, prod_exit_ts, prod_exit_px,
             arena_exit_ts, arena_exit_px, qty, side, prod_pnl,
             arena_pnl, fill_delta_entry_bps, fill_delta_exit_bps,
             pnl_delta_usd, replay_status
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any

logger = logging.getLogger("arena_replay")

REPO_ROOT = Path(__file__).resolve().parent.parent
TRADE_RESULTS = REPO_ROOT / "data" / "trade_results.jsonl"
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "replay"

# Make arena importable
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))


@dataclass
class TradeRecord:
    """A deduped production trade ready for replay."""
    ticker: str
    session_date: str
    entry_time: datetime
    exit_time: datetime
    pnl: float
    is_carry: bool = False  # cross-session — Block 4

    @property
    def is_intraday(self) -> bool:
        return self.entry_time.date() == self.exit_time.date()


@dataclass
class ReplayRow:
    """One trade's worth of replay output."""
    ticker: str
    session_date: str
    prod_entry_ts: str = ""
    prod_entry_px: float = 0.0
    arena_entry_ts: str = ""
    arena_entry_px: float = 0.0
    prod_exit_ts: str = ""
    prod_exit_px: float = 0.0
    arena_exit_ts: str = ""
    arena_exit_px: float = 0.0
    qty: int = 0
    side: str = "buy"
    prod_pnl: float = 0.0
    arena_pnl: float = 0.0
    fill_delta_entry_bps: float = 0.0
    fill_delta_exit_bps: float = 0.0
    pnl_delta_usd: float = 0.0
    replay_status: str = ""  # ok | missing_bars | bar_gap | carry_skipped | unsupported | no_entry_fill | no_exit_fill
    exit_source: str = ""    # bar_anchored | prod_mirror_<path> — Block A.2


# ── Trade dedup (mirrors evaluation/2026-04-28-edge-assessment.md §1.1) ──


def load_trades(*, since: str, until: str) -> list[TradeRecord]:
    """Load + dedupe trades from data/trade_results.jsonl in [since, until]."""
    trades: list[TradeRecord] = []
    with TRADE_RESULTS.open("r", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            sd = j.get("session_date", "")
            if not (since <= sd <= until):
                continue
            entry = datetime.fromisoformat(j["entry_time"])
            exit_ = datetime.fromisoformat(j["exit_time"])
            ticker = j["ticker"]
            pnl = float(j["pnl"])
            # Cross-session = entry_date < session_date.
            is_carry = entry.date() < exit_.date()
            trades.append(TradeRecord(
                ticker=ticker, session_date=sd,
                entry_time=entry, exit_time=exit_,
                pnl=pnl, is_carry=is_carry,
            ))
    # Dedupe LIDR session-end mark snapshots (multiple rows for same
    # underlying carry). Key = (ticker, entry_time). For carries keep
    # the snapshot with EARLIEST exit_time (closest to realized exit).
    # For non-carries the key is naturally unique.
    seen: dict[tuple[str, str], TradeRecord] = {}
    for t in trades:
        key = (t.ticker, t.entry_time.isoformat())
        if t.is_carry:
            existing = seen.get(key)
            if existing is None or t.exit_time < existing.exit_time:
                seen[key] = t
        else:
            seen[key] = t
    return sorted(seen.values(), key=lambda r: r.entry_time)


# ── Arena machinery wiring ──────────────────────────────────────────


def _load_bars_for(ticker: str, session_date: str):
    """Load arena Bar objects for one ticker+date via the converted parquet."""
    from arena.data_engine import DataEngine  # type: ignore
    from arena.clock import SimClock, ClockMode  # type: ignore

    target = ARENA_HISTORICAL / ticker / f"{session_date}.parquet"
    if not target.exists():
        return None
    clock = SimClock(
        start=datetime.fromisoformat(f"{session_date}T13:30:00+00:00"),
        end=datetime.fromisoformat(f"{session_date}T20:00:00+00:00"),
        mode=ClockMode.REPLAY,
    )
    engine = DataEngine(clock=clock, historical_dir=str(ARENA_HISTORICAL))
    return engine._load_parquet_bars(ticker, session_date)


_QTY_TRUTH_CACHE: dict | None = None
_EXIT_TRUTH_CACHE: dict | None = None


def _exit_truth_lookup(*, ticker: str, session_date: str, entry_ts: datetime):
    """Block A.2: prod-mirror exit-truth lookup.

    Returns {prod_exit_avg_px, exit_path, source} or None.

    Loads data/replay/prod_exit_truth.parquet on first call (built by
    scripts/extract_exit_truth.py from D76 CLOSED + bridge attribution
    log lines + computed-from-pnl fallback). Tolerates missing file
    (returns None for everything → callers fall back to bar-anchored
    exit). See docs/research-log/62_h2_exit_semantics.md."""
    global _EXIT_TRUTH_CACHE
    if _EXIT_TRUTH_CACHE is None:
        truth_path = REPO_ROOT / "data" / "replay" / "prod_exit_truth.parquet"
        if not truth_path.exists():
            _EXIT_TRUTH_CACHE = {}
        else:
            try:
                import pandas as pd
                df = pd.read_parquet(truth_path)
                _EXIT_TRUTH_CACHE = {
                    (r["ticker"], r["session_date"], r["entry_ts"]): {
                        "prod_exit_avg_px": float(r["prod_exit_avg_px"]),
                        "exit_path": r["exit_path"],
                        "source": r["source"],
                    }
                    for _, r in df.iterrows()
                }
            except Exception:
                _EXIT_TRUTH_CACHE = {}
    return _EXIT_TRUTH_CACHE.get((ticker, session_date, entry_ts.isoformat()))


def _qty_truth_lookup(*, ticker: str, session_date: str, entry_ts: datetime):
    """Return {prod_qty, prod_entry_avg_px, source} or None.

    Loads the truth parquet on first call and caches; subsequent
    lookups are O(1) per (ticker, date, entry_ts) key. Tolerates a
    missing truth file (returns None for everything → callers fall
    back to tier-1 default)."""
    global _QTY_TRUTH_CACHE
    if _QTY_TRUTH_CACHE is None:
        truth_path = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
        if not truth_path.exists():
            _QTY_TRUTH_CACHE = {}
        else:
            try:
                import pandas as pd
                df = pd.read_parquet(truth_path)
                _QTY_TRUTH_CACHE = {
                    (r["ticker"], r["session_date"], r["entry_ts"]): {
                        "prod_qty": int(r["prod_qty"]),
                        "prod_entry_avg_px": float(r["prod_entry_avg_px"]),
                        "source": r["source"],
                    }
                    for _, r in df.iterrows()
                }
            except Exception:
                _QTY_TRUTH_CACHE = {}
    return _QTY_TRUTH_CACHE.get((ticker, session_date, entry_ts.isoformat()))


def _bar_at_or_before(bars_map: dict, ts: datetime):
    """Return the bar at-or-immediately-before timestamp ts (UTC)."""
    target_iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    last = None
    for _idx, bar in bars_map.items():
        if bar.timestamp > target_iso:
            break
        last = bar
    return last


def _arena_fill(
    *, side: str, qty: int, bar, ts: datetime, fill_model, spread_model,
    rng: Random, order_type: str = "market", limit_price: float | None = None,
):
    """Run one fill attempt against arena's FillModel + SpreadModel."""
    from arena.exchange import OrderState  # type: ignore

    mid = (bar.open + bar.close) / 2.0
    half_spread = spread_model.get_spread(price=mid, volume=bar.volume, timestamp=ts)
    bid = round(mid - half_spread, 4)
    ask = round(mid + half_spread, 4)

    order = OrderState(
        id=str(uuid.uuid4()),
        client_order_id=str(uuid.uuid4()),
        symbol="UNUSED", side=side, type=order_type, time_in_force="day",
        qty=qty, limit_price=limit_price, status="new",
    )
    fill = fill_model.try_fill(order=order, bar=bar, bid=bid, ask=ask, rng=rng)
    return fill, bid, ask


# ── Per-trade replay ───────────────────────────────────────────────


def replay_trade(trade: TradeRecord) -> ReplayRow:
    from arena.fill_model import AlpacaFillModel  # type: ignore
    from arena.spread_model import SpreadModel  # type: ignore

    row = ReplayRow(ticker=trade.ticker, session_date=trade.session_date)
    row.prod_entry_ts = trade.entry_time.isoformat()
    row.prod_exit_ts = trade.exit_time.isoformat()
    row.prod_pnl = trade.pnl

    if trade.is_carry:
        row.replay_status = "carry_skipped"
        return row

    # Pre-market carry-snapshot detection: production records LIDR
    # session-end mark snapshots with synthetic 04:30 ET (08:30 UTC)
    # "entry" times — these are NOT new trades, they're snapshots of a
    # prior-session carry. Use a generous cutoff (<13:00 UTC = <08:00 ET)
    # so legitimate pre-market entries close to the open (e.g. 09:29 ET)
    # still get a fair shot at bar lookup (will fail there as bar_gap
    # if no bar exists — accurate signal).
    et_cutoff = datetime.fromisoformat("2000-01-01T13:00:00+00:00").time()
    if trade.entry_time.astimezone(timezone.utc).time() < et_cutoff:
        row.replay_status = "carry_skipped"
        return row

    # Bar coverage check
    entry_date = trade.entry_time.date().isoformat()
    bars = _load_bars_for(trade.ticker, entry_date)
    if bars is None:
        row.replay_status = "missing_bars"
        return row

    entry_bar = _bar_at_or_before(bars, trade.entry_time)
    if entry_bar is None:
        row.replay_status = "bar_gap"
        return row

    # Re-derive prod's entry price from bar (trade_results.jsonl
    # doesn't carry entry_price). Production typically filled near
    # the bar open of the entry minute.
    row.prod_entry_px = float(entry_bar.open)

    exit_date = trade.exit_time.date().isoformat()
    if exit_date != entry_date:
        row.replay_status = "unsupported"
        return row
    exit_bar = _bar_at_or_before(bars, trade.exit_time)
    if exit_bar is None:
        row.replay_status = "bar_gap"
        return row
    row.prod_exit_px = float(exit_bar.open)

    # Quantity resolution — Block B.1 fix per docs/replay_diffs/
    # 2026-04-28_error_decomposition.md:
    #
    # Source priority:
    #   1. data/replay/prod_qty_truth.parquet — authoritative qty +
    #      entry_px from trade_context parquet OR D215/D217 log fills.
    #      This eliminates the 5.7-16.7× multiplier errors that
    #      drowned out everything else in the previous diff.
    #   2. Tier-1 sizing default ONLY when no truth source exists
    #      (pre-Bug-AO sessions with no D215/D217 line in the log).
    #
    # Old back-out from prod_pnl was abandoned (broke OGN/SBLX with
    # qty=1 collapse).
    truth = _qty_truth_lookup(
        ticker=trade.ticker, session_date=entry_date, entry_ts=trade.entry_time,
    )
    if truth is not None:
        row.qty = int(truth["prod_qty"])
        # Override prod_entry_px with the authoritative source — bar.open
        # at minute is an approximation, the D215/D217/trade_context
        # value is the actual broker fill price.
        row.prod_entry_px = float(truth["prod_entry_avg_px"])
    else:
        equity_for_sizing = 150_000.0
        tier1_pct = 0.50
        row.qty = max(1, int(equity_for_sizing * tier1_pct / max(row.prod_entry_px, 0.01)))
    row.side = "buy"

    # Arena fills (Block 2.1: load spread calibration if available)
    calibration_payload: dict = {}
    cal_path = REPO_ROOT / "mx-arena" / "arena" / "calibration" / "spread_v0.1.json"
    if cal_path.exists():
        try:
            calibration_payload = json.loads(cal_path.read_text(encoding="utf-8"))
        except Exception as _e:  # noqa: BLE001
            logger.debug("calibration load failed (%s) — using defaults", _e)
    base_fill_model = AlpacaFillModel()
    # Block A.2: limit-aware fill for entries. When prod's actual fill
    # price is in the truth table (which it is for all 9 OK trades),
    # the prod-mirror snap below overrides whatever this computes — so
    # the limit-aware path runs in the "limit_price=prod_entry_px"
    # case and arena enters at the limit. For trades NOT in the truth
    # corpus (Block C 86-session OOS), the limit-aware path will fill
    # at the strategy's actual limit if it falls within the bar range,
    # else mark failed-to-fill.
    from arena.limit_aware_fill import LimitAwareFillModel  # type: ignore
    fill_model = LimitAwareFillModel(base_market_fill_model=base_fill_model)
    spread_model = SpreadModel(calibration=calibration_payload.get("tiers"))
    rng = Random(hash((trade.ticker, trade.entry_time.isoformat())) & 0xFFFFFFFF)

    # For prod-corpus replay, "limit price" for the modeled fill is
    # prod's actual entry price (which matches what production
    # submitted). The fill model now correctly checks the bar's
    # high/low range before filling at that price.
    entry_fill, _, _ = _arena_fill(
        side="buy", qty=row.qty, bar=entry_bar, ts=trade.entry_time,
        fill_model=fill_model, spread_model=spread_model, rng=rng,
        order_type="limit",
        limit_price=row.prod_entry_px if row.prod_entry_px > 0 else None,
    )
    if entry_fill is None:
        # Block A.2: limit was outside bar range. Fall back to bar-anchored
        # market fill for replay continuity (prod-mirror snap will override
        # if exit_truth available). Tag the source for diagnostics.
        entry_fill, _, _ = _arena_fill(
            side="buy", qty=row.qty, bar=entry_bar, ts=trade.entry_time,
            fill_model=base_fill_model, spread_model=spread_model, rng=rng,
            order_type="market",
        )
        if entry_fill is None:
            row.replay_status = "no_entry_fill"
            return row
    row.arena_entry_ts = entry_bar.timestamp
    row.arena_entry_px = float(entry_fill.price)

    # Block A.2: prod-mirror exit truth. If we have an authoritative
    # exit_avg_px from prod logs (D76 CLOSED, bridge attribution, or
    # computed_from_pnl fallback), use it as arena's exit price AND
    # snap arena's entry price to prod's actual entry too. Both legs
    # must come from the same basis or the synthetic exit (computed
    # as prod_entry + pnl/qty) misaligns and produces phantom Δ.
    #
    # This is "prod-mirror" by construction: arena reads what prod
    # realized end-to-end. Validates the replay rig wiring; does NOT
    # model alternative exit policies. Forward-looking sweeps (Block E
    # BAR-1 timing) must use modeled exits because they ask "what if
    # exit logic were different" — for which there's no prod fill to
    # mirror. Falls back to bar-anchored entry+exit when no truth.
    exit_truth = _exit_truth_lookup(
        ticker=trade.ticker, session_date=trade.session_date,
        entry_ts=trade.entry_time,
    )
    if exit_truth is not None:
        # Snap arena and prod columns to the same truth basis. Without
        # this, fill_delta_exit_bps would compare arena's mirrored
        # value against the stale bar-open estimate of `prod_exit_px`
        # (which is just our pre-truth approximation, not the actual
        # prod fill). Updating both gives a clean diagnostic.
        row.arena_entry_px = row.prod_entry_px
        row.prod_exit_px = float(exit_truth["prod_exit_avg_px"])
        row.arena_exit_px = float(exit_truth["prod_exit_avg_px"])
        row.arena_exit_ts = exit_bar.timestamp
        row.exit_source = f"prod_mirror_{exit_truth['exit_path']}"
    else:
        exit_fill, _, _ = _arena_fill(
            side="sell", qty=row.qty, bar=exit_bar, ts=trade.exit_time,
            fill_model=fill_model, spread_model=spread_model, rng=rng,
            order_type="market",
        )
        if exit_fill is None:
            row.replay_status = "no_exit_fill"
            return row
        row.arena_exit_ts = exit_bar.timestamp
        row.arena_exit_px = float(exit_fill.price)
        row.exit_source = "bar_anchored"

    row.arena_pnl = (row.arena_exit_px - row.arena_entry_px) * row.qty
    if row.prod_entry_px > 0:
        row.fill_delta_entry_bps = (
            (row.arena_entry_px - row.prod_entry_px) / row.prod_entry_px * 1e4
        )
    if row.prod_exit_px > 0:
        row.fill_delta_exit_bps = (
            (row.arena_exit_px - row.prod_exit_px) / row.prod_exit_px * 1e4
        )
    row.pnl_delta_usd = row.arena_pnl - row.prod_pnl
    row.replay_status = "ok"
    return row


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", default="2026-04-22")
    p.add_argument("--until", default="2026-04-28")
    p.add_argument("--date", help="Shorthand: --since=DATE --until=DATE")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    if args.date:
        since = until = args.date
    else:
        since, until = args.since, args.until

    trades = load_trades(since=since, until=until)
    logger.info("Loaded %d deduped trades for [%s, %s]", len(trades), since, until)

    rows = [replay_trade(t) for t in trades]

    status_counts: dict[str, int] = {}
    for r in rows:
        status_counts[r.replay_status] = status_counts.get(r.replay_status, 0) + 1
    logger.info("Status summary: %s", status_counts)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"arena_session_{since}_{until}.parquet"
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required; pip install pandas pyarrow")
        return 1
    df = pd.DataFrame([r.__dict__ for r in rows])
    df.to_parquet(out_path, index=False)
    logger.info("wrote %s (n=%d rows)", out_path, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
