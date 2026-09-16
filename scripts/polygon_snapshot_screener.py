"""Polygon snapshot screener — premarket gap-up candidate generator.

Replaces / supplements Alpaca's stale 09:25 ET screener (per doc 92 §6.1).

Per Compass §A.4 pattern:
  04:30 ET → snapshot/gainers (top 20 immediate)
            + snapshot/all_tickers (~10k, filter pct_change > +5%)
            ↓ dedupe via /v3/reference/tickers (filter type ∈ {CS, ADRC})
            ↓ /v3/reference/splits (last 90d) → flag reverse-split fraud risk
            ↓ /v3/reference/news (last 24h) → catalyst quality
  09:25 ET → re-snapshot final candidates → confirmed gap list

USAGE:
    python scripts/polygon_snapshot_screener.py --check
    python scripts/polygon_snapshot_screener.py --gainers-only
    python scripts/polygon_snapshot_screener.py --full
    python scripts/polygon_snapshot_screener.py --price-min 1.50 --price-max 20

ENVIRONMENT:
    POLYGON_API_KEY     REST API key (separate from S3 keys)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "mx-arena"))

from data_providers.polygon import PolygonClient, PolygonEndpoints  # noqa: E402

OUT_DIR = REPO / "data" / "polygon_snapshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("snapshot")


def parse_pct(snap: dict) -> float:
    """Polygon snapshot returns todaysChangePerc."""
    return float(snap.get("todaysChangePerc") or 0.0)


def parse_price(snap: dict) -> float:
    """Latest trade price from snapshot."""
    last_trade = snap.get("lastTrade") or {}
    p = last_trade.get("p")
    if p is not None:
        return float(p)
    # Fallback to today's close
    day = snap.get("day") or {}
    return float(day.get("c") or 0.0)


def parse_volume(snap: dict) -> int:
    day = snap.get("day") or {}
    return int(day.get("v") or 0)


async def screen(args, ep: PolygonEndpoints) -> list[dict]:
    """Build the candidate list per Compass §A.4 pattern."""
    candidates: list[dict] = []

    # Step 1: gainers snapshot (top 20 immediate)
    log.info("Pulling /v2/snapshot/...gainers ...")
    gainers = await ep.gainers()
    log.info("  got %d gainers", len(gainers))
    for g in gainers:
        candidates.append({"source": "gainers", **g})

    # Step 2: all-tickers snapshot, filtered (~10k tickers)
    if args.full:
        log.info("Pulling /v2/snapshot/...tickers (full universe, ~10k) ...")
        all_t = await ep.all_tickers_snapshot()
        log.info("  got %d total snapshots", len(all_t))
        for t in all_t:
            pct = parse_pct(t)
            price = parse_price(t)
            vol = parse_volume(t)
            if pct < args.pct_min: continue
            if not (args.price_min <= price <= args.price_max): continue
            if vol < args.min_volume: continue
            candidates.append({"source": "all_tickers", **t})

    # Dedupe by ticker
    by_ticker: dict[str, dict] = {}
    for c in candidates:
        sym = c.get("ticker") or c.get("T")
        if not sym: continue
        if sym not in by_ticker or parse_pct(c) > parse_pct(by_ticker[sym]):
            by_ticker[sym] = c
    deduped = list(by_ticker.values())
    log.info("After dedupe: %d unique candidates", len(deduped))

    # Step 3: enrich with reference (type filter)
    if args.enrich_types:
        log.info("Filtering to CS + ADRC via ticker_details ...")
        kept = []
        for c in deduped:
            sym = c.get("ticker") or c.get("T")
            try:
                ref = await ep.ticker_details(sym)
                ttype = (ref and ref.type) or "?"
                if ttype in ("CS", "ADRC"):
                    c["ticker_type"] = ttype
                    if ref and getattr(ref, "market_cap", None):
                        c["market_cap"] = ref.market_cap
                    kept.append(c)
            except Exception as e:
                log.debug("  %s: %s", sym, e)
        log.info("  kept %d / %d (CS+ADRC only)", len(kept), len(deduped))
        deduped = kept

    # Step 4: reverse-split fraud filter (last 90d)
    if args.flag_reverse_splits:
        log.info("Pulling recent splits to flag reverse-split risk ...")
        ninety_days_ago = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
        for c in deduped:
            sym = c.get("ticker") or c.get("T")
            try:
                splits = await ep.splits(ticker=sym, execution_date_gte=ninety_days_ago)
                for s in splits:
                    sf = float(s.get("split_from", 1) or 1)
                    st = float(s.get("split_to", 1) or 1)
                    if sf > st:  # reverse split (e.g., 10:1 → 1)
                        c["reverse_split_90d"] = True
                        c["reverse_split_ratio"] = f"{int(sf)}:{int(st)}"
                        c["reverse_split_date"] = s.get("execution_date")
                        log.warning("  %s: REVERSE SPLIT %s on %s (fraud-risk gate)",
                                    sym, c["reverse_split_ratio"], c["reverse_split_date"])
                        break
            except Exception as e:
                log.debug("  %s splits lookup: %s", sym, e)

    # Sort by pct change desc
    deduped.sort(key=parse_pct, reverse=True)
    return deduped


async def main_async(args):
    if args.check:
        try:
            client = PolygonClient.from_env()
        except RuntimeError as e:
            log.error(str(e))
            return 1
        async with client:
            try:
                ep = PolygonEndpoints(client)
                status = await ep.market_status_now()
                log.info("OK — Polygon API reachable. Market status: %s",
                         status.get("market"))
                return 0
            except Exception as e:
                log.error("API call failed: %s", e)
                return 1

    try:
        client = PolygonClient.from_env()
    except RuntimeError as e:
        log.error(str(e))
        return 1
    async with client:
        ep = PolygonEndpoints(client)
        candidates = await screen(args, ep)
        log.info("\n=== TOP CANDIDATES (sorted by todaysChangePerc) ===")
        log.info("%-6s  %-7s  %7s  %12s  %s",
                 "sym", "price", "%chg", "vol", "flags")
        for c in candidates[:50]:
            sym = c.get("ticker") or c.get("T")
            flags = []
            if c.get("reverse_split_90d"):
                flags.append(f"REV-SPLIT({c.get('reverse_split_ratio')})")
            if c.get("ticker_type"):
                flags.append(f"type={c['ticker_type']}")
            log.info("%-6s  $%6.2f  %+6.2f%%  %12d  %s",
                     sym, parse_price(c), parse_pct(c), parse_volume(c),
                     " ".join(flags))
        # Persist
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUT_DIR / f"snapshot_{ts}.json"
        out_path.write_text(json.dumps(candidates, indent=2, default=str))
        log.info("\nWrote %s (%d candidates)", out_path, len(candidates))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--gainers-only", dest="full", action="store_false",
                         default=True, help="Only pull /gainers (skip full snapshot)")
    parser.add_argument("--full", dest="full", action="store_true",
                         help="Pull /gainers + /all_tickers (~10k snapshots)")
    parser.add_argument("--pct-min", type=float, default=5.0,
                         help="Min pct change for all-tickers filter")
    parser.add_argument("--price-min", type=float, default=1.50)
    parser.add_argument("--price-max", type=float, default=20.0)
    parser.add_argument("--min-volume", type=int, default=50_000)
    parser.add_argument("--enrich-types", action="store_true", default=True,
                         help="Filter to CS + ADRC via ticker_details (slower)")
    parser.add_argument("--no-enrich-types", dest="enrich_types", action="store_false")
    parser.add_argument("--flag-reverse-splits", action="store_true", default=True)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
