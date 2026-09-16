"""Pull the Alpaca account activities tape for the full project window.

Critical correction script (2026-04-29). Earlier analyses assumed
trade_results.jsonl was the source-of-truth for realized P&L. It is
not — it covers only 5 sessions (4/22-4/28). Broker actual is +$40K
over Dec 2025 → today; trade_results shows -$6,612 over 4/22-4/28.
The +$47K unattributed gain happened in a period for which we have
ZERO trade-level records.

This script pulls the Alpaca /v2/account/activities tape (FILL
events) for the full window, reconstructs per-trade entry/exit
pairs, and writes a parquet that becomes the new ground truth.

Output:
    data/broker_truth/activities.parquet — raw FILL events
    data/broker_truth/closed_trades.parquet — reconstructed entry+exit pairs
        with realized P&L per trade

Usage:
    python scripts/pull_broker_truth.py --since 2025-12-01

Discipline: paper account, READ-ONLY queries. No live trading impact.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("pull_broker_truth")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "broker_truth"


@dataclass
class FillEvent:
    """One FILL or PARTIAL_FILL activity from Alpaca."""
    activity_id: str
    transaction_time: str
    symbol: str
    side: str            # buy | sell | sell_short | buy_to_cover
    qty: int
    price: float
    order_id: str
    order_status: str    # filled | partially_filled
    type: str            # fill | partial_fill


@dataclass
class ClosedTrade:
    """An entry/exit pair reconstructed from broker activities.

    Pairing rule: FIFO per symbol. The first BUY is paired with the
    first SELL of equal-or-greater qty for that symbol; partial sells
    create multiple closed-trade rows.
    """
    symbol: str
    entry_ts: str
    entry_price: float
    entry_qty: int
    exit_ts: str
    exit_price: float
    exit_qty: int
    realized_pnl: float
    hold_seconds: int
    is_carry: bool       # entry and exit on different session dates
    session_date: str    # session of EXIT


def _load_creds() -> tuple[str, str, str]:
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if not api_key or not secret:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("ALPACA_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("ALPACA_SECRET_KEY="):
                    secret = line.split("=", 1)[1].strip()
                elif line.startswith("ALPACA_BASE_URL="):
                    base = line.split("=", 1)[1].strip()
    if not api_key or not secret:
        raise SystemExit("ALPACA credentials missing in env or .env")
    return api_key, secret, base


async def fetch_activities(*, since: str, until: str) -> list[FillEvent]:
    """Pull all FILL + PARTIAL_FILL activities for the window.

    Alpaca's /v2/account/activities supports activity_types=FILL.
    Pagination via `page_token`. Returns chronological list."""
    import httpx

    api_key, secret, base = _load_creds()
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret,
    }

    url = f"{base}/v2/account/activities/FILL"
    params = {
        "after": f"{since}T00:00:00Z",
        "until": f"{until}T23:59:59Z",
        "page_size": 100,
        "direction": "asc",
    }

    fills: list[FillEvent] = []
    page = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            page += 1
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for item in data:
                fills.append(FillEvent(
                    activity_id=str(item.get("id", "")),
                    transaction_time=str(item.get("transaction_time", "")),
                    symbol=str(item.get("symbol", "")),
                    side=str(item.get("side", "")),
                    qty=int(float(item.get("qty", 0))),
                    price=float(item.get("price", 0) or 0),
                    order_id=str(item.get("order_id", "")),
                    order_status=str(item.get("order_status", "")),
                    type=str(item.get("type", "")),
                ))
            logger.info("  page %d: %d activities (cumulative: %d)", page, len(data), len(fills))
            if len(data) < 100:
                break
            # Paginate via page_token (last activity_id)
            last_id = data[-1].get("id")
            if not last_id:
                break
            params["page_token"] = last_id
            if page > 200:  # safety cap
                logger.warning("hit pagination safety cap at 200 pages")
                break

    return fills


def reconstruct_closed_trades(fills: list[FillEvent]) -> list[ClosedTrade]:
    """FIFO pair entries with exits per symbol."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    open_lots: dict[str, list[FillEvent]] = defaultdict(list)
    closed: list[ClosedTrade] = []

    for fill in fills:
        sym = fill.symbol
        if fill.side == "buy":
            open_lots[sym].append(fill)
        elif fill.side in ("sell", "sell_short"):
            # Close against open BUY lots FIFO
            remaining = fill.qty
            while remaining > 0 and open_lots[sym]:
                entry = open_lots[sym][0]
                pair_qty = min(remaining, entry.qty)
                pnl = (fill.price - entry.price) * pair_qty
                try:
                    e_ts = _dt.fromisoformat(entry.transaction_time.replace("Z", "+00:00"))
                    x_ts = _dt.fromisoformat(fill.transaction_time.replace("Z", "+00:00"))
                    hold_seconds = int((x_ts - e_ts).total_seconds())
                    is_carry = e_ts.astimezone(ZoneInfo("America/New_York")).date() != \
                              x_ts.astimezone(ZoneInfo("America/New_York")).date()
                    session_date = x_ts.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                except Exception:
                    hold_seconds = 0
                    is_carry = False
                    session_date = ""
                closed.append(ClosedTrade(
                    symbol=sym,
                    entry_ts=entry.transaction_time,
                    entry_price=entry.price,
                    entry_qty=pair_qty,
                    exit_ts=fill.transaction_time,
                    exit_price=fill.price,
                    exit_qty=pair_qty,
                    realized_pnl=round(pnl, 4),
                    hold_seconds=hold_seconds,
                    is_carry=is_carry,
                    session_date=session_date,
                ))
                remaining -= pair_qty
                if pair_qty >= entry.qty:
                    open_lots[sym].pop(0)
                else:
                    # Partial close — reduce the open lot's qty
                    entry.qty -= pair_qty
            if remaining > 0:
                logger.warning("  short or unpaired sell: %s remaining qty %d at %s",
                               sym, remaining, fill.transaction_time)
        # Skip sell_short / buy_to_cover for now (focus on long-only baseline)

    return closed


async def amain(args) -> int:
    fills = await fetch_activities(since=args.since, until=args.until)
    logger.info("fetched %d FILL activities for [%s, %s]", len(fills), args.since, args.until)

    closed = reconstruct_closed_trades(fills)
    total_pnl = sum(t.realized_pnl for t in closed)
    logger.info("reconstructed %d closed trades, Σ realized P&L = $%+,.2f",
                len(closed), total_pnl)

    # Open lots remaining (positions still held)
    n_open = sum(1 for f in fills if f.side == "buy") - len(closed)
    logger.info("open lots remaining (positions still held): ~%d", n_open)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import pandas as pd
        if fills:
            df_fills = pd.DataFrame([asdict(f) for f in fills])
            df_fills.to_parquet(args.output_dir / "activities.parquet", index=False)
            logger.info("wrote %s (%d rows)", args.output_dir / "activities.parquet", len(df_fills))
        if closed:
            df_closed = pd.DataFrame([asdict(t) for t in closed])
            df_closed.to_parquet(args.output_dir / "closed_trades.parquet", index=False)
            logger.info("wrote %s (%d rows)", args.output_dir / "closed_trades.parquet", len(df_closed))
    except ImportError:
        logger.error("pandas required to write parquet")
        return 1

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", default="2025-12-01")
    p.add_argument("--until", default=datetime.now(timezone.utc).date().isoformat())
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
