"""Polygon backfill orchestrator (Block B).

Resumable, parquet-output, per-corpus subcommands.

⚠️ 2026-05-12: this script writes to `data/polygon_backfill/`,
NOT `data/polygon_warehouse/`. The high_movers / aftermath catalog
pipeline reads from `data/polygon_warehouse/`, populated by:
  - scripts/polygon_flatfile_pull.py  (S3 flat-file CSVs)
  - scripts/polygon_parquet_warehouse.py  (CSV→parquet conversion)
For DAILY data refresh see scripts/daily_data_ingest.ps1.
This script's outputs are for backfill-style validation/research only.

Usage (--help-validated, do NOT trust older docstring forms):
    python scripts/polygon_backfill_all.py --corpus tick_validation
    python scripts/polygon_backfill_all.py --corpus etf_baseline --years 10
    python scripts/polygon_backfill_all.py --corpus equity_universe --years 1
    python scripts/polygon_backfill_all.py --corpus fundamentals
    python scripts/polygon_backfill_all.py --corpus all   # runs all four

Valid args: --corpus {etf_baseline,equity_universe,tick_validation,
            fundamentals,all}, --years YEARS, --max MAX.
The --tickers-from / --tickers args shown in older versions of this
docstring DO NOT EXIST. Verify with `--help` first.

Resumability: per-corpus progress tracked in
`data/polygon_backfill/_progress_{corpus}.parquet`. Re-running the
same corpus skips already-pulled (ticker, date, endpoint) tuples.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))

# Load .env minimally so subprocesses inherit POLYGON_API_KEY.
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import pandas as pd  # noqa: E402

from data_providers.polygon import (  # noqa: E402
    PolygonClient,
    PolygonEndpoints,
    Bar,
    Quote,
    Trade,
    FinancialReport,
    TickerRef,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("polygon_backfill")

OUT_ROOT = REPO_ROOT / "data" / "polygon_backfill"
OUT_ROOT.mkdir(parents=True, exist_ok=True)

PROGRESS_DIR = OUT_ROOT / "_progress"
PROGRESS_DIR.mkdir(parents=True, exist_ok=True)


# ── Progress tracking ─────────────────────────────────────────────


def _progress_path(corpus: str) -> Path:
    return PROGRESS_DIR / f"{corpus}.parquet"


def _load_progress(corpus: str) -> set[tuple]:
    p = _progress_path(corpus)
    if not p.exists():
        return set()
    df = pd.read_parquet(p)
    return set(zip(*[df[c] for c in df.columns]))


def _save_progress(corpus: str, completed: list[dict]) -> None:
    if not completed:
        return
    df = pd.DataFrame(completed)
    p = _progress_path(corpus)
    if p.exists():
        existing = pd.read_parquet(p)
        df = pd.concat([existing, df]).drop_duplicates()
    df.to_parquet(p, index=False)


# ── B.1: ETF baseline (10y of SPY/QQQ/IWM + top ETFs) ──────────────

ETF_BASELINE_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "VEA", "VWO",
    "AGG", "BND", "TLT", "GLD", "SLV", "USO", "UNG",
    "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLB", "XLC", "XLRE",
    "ARKK", "ARKQ", "ARKG", "ARKF", "ARKW",
    "SOXL", "SOXS", "TQQQ", "SQQQ", "TZA", "TNA",
    "VXX", "UVXY", "SVXY",
]


async def backfill_etf_baseline(ep: PolygonEndpoints, years: int = 10) -> int:
    corpus = "etf_baseline"
    out_dir = OUT_ROOT / "etf_minute_bars"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = _load_progress(corpus)

    end = date.today()
    start = end - timedelta(days=365 * years)
    new_completed = []
    pulled = 0

    for ticker in ETF_BASELINE_TICKERS:
        key = (ticker, str(start), str(end))
        if key in done:
            continue
        out_path = out_dir / f"{ticker}.parquet"
        try:
            t0 = time.perf_counter()
            bars = await ep.aggregates(
                ticker, 1, "minute", str(start), str(end), limit=50_000,
            )
            dt = time.perf_counter() - t0
            if not bars:
                logger.info("etf_baseline %s: 0 bars (no data)", ticker)
                new_completed.append({"ticker": ticker, "from": str(start), "to": str(end)})
                continue
            df = pd.DataFrame([
                {
                    "ts_ms": b.timestamp_ms,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "vwap": b.vwap,
                    "n_trades": b.trade_count,
                }
                for b in bars
            ])
            df.to_parquet(out_path, index=False)
            pulled += 1
            new_completed.append({"ticker": ticker, "from": str(start), "to": str(end)})
            logger.info(
                "etf_baseline %s: %d bars in %.1fs -> %s",
                ticker, len(bars), dt, out_path.name,
            )
        except Exception as e:
            logger.error("etf_baseline %s FAILED: %s", ticker, e)

    _save_progress(corpus, new_completed)
    logger.info("etf_baseline complete: %d new tickers pulled", pulled)
    return pulled


# ── B.2: equity universe (1y, deduped from project history) ──────


def _collect_project_tickers() -> set[str]:
    """Gather every ticker that has ever appeared in the project."""
    tickers: set[str] = set()
    # bar_recordings/{date}/{ticker}.json
    rec_root = REPO_ROOT / "data" / "bar_recordings"
    if rec_root.exists():
        for date_dir in rec_root.iterdir():
            if not date_dir.is_dir():
                continue
            for f in date_dir.glob("*.json"):
                tickers.add(f.stem.upper())
    # data/replay/prod_qty_truth.parquet
    pqt = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
    if pqt.exists():
        try:
            df = pd.read_parquet(pqt)
            tickers.update(df["ticker"].astype(str).str.upper())
        except Exception:
            pass
    # data/trade_results.jsonl (if it exists)
    tr = REPO_ROOT / "data" / "trade_results.jsonl"
    if tr.exists():
        try:
            for line in tr.read_text(encoding="utf-8", errors="ignore").splitlines():
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("ticker"):
                    tickers.add(str(obj["ticker"]).upper())
        except Exception:
            pass
    # decision_row instrumentation
    inst_root = REPO_ROOT / "data" / "instrumentation" / "decision_row"
    if inst_root.exists():
        for f in inst_root.rglob("*.parquet"):
            try:
                df = pd.read_parquet(f, columns=["ticker"])
                tickers.update(df["ticker"].astype(str).str.upper())
            except Exception:
                pass
    return {t for t in tickers if t and t.isalpha()}


async def backfill_equity_universe(
    ep: PolygonEndpoints, years: int = 1, max_tickers: Optional[int] = None,
) -> int:
    corpus = "equity_universe"
    out_dir = OUT_ROOT / "equity_minute_bars"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = _load_progress(corpus)

    universe = sorted(_collect_project_tickers())
    if max_tickers:
        universe = universe[:max_tickers]
    end = date.today()
    start = end - timedelta(days=365 * years)
    logger.info("equity_universe: %d tickers, %s -> %s", len(universe), start, end)

    new_completed = []
    pulled = 0

    for ticker in universe:
        key = (ticker, str(start), str(end))
        if key in done:
            continue
        try:
            t0 = time.perf_counter()
            bars = await ep.aggregates(
                ticker, 1, "minute", str(start), str(end), limit=50_000,
            )
            dt = time.perf_counter() - t0
            if not bars:
                new_completed.append({"ticker": ticker, "from": str(start), "to": str(end)})
                continue
            df = pd.DataFrame([
                {
                    "ts_ms": b.timestamp_ms,
                    "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                    "volume": b.volume, "vwap": b.vwap, "n_trades": b.trade_count,
                }
                for b in bars
            ])
            out_path = out_dir / f"{ticker}.parquet"
            df.to_parquet(out_path, index=False)
            pulled += 1
            new_completed.append({"ticker": ticker, "from": str(start), "to": str(end)})
            if pulled % 10 == 0:
                logger.info(
                    "equity_universe progress: %d / %d (last %s: %d bars in %.1fs)",
                    pulled, len(universe), ticker, len(bars), dt,
                )
        except Exception as e:
            logger.warning("equity_universe %s FAILED: %s", ticker, e)

    _save_progress(corpus, new_completed)
    logger.info("equity_universe complete: %d new tickers pulled", pulled)
    return pulled


# ── B.3: tick validation (NBBO + trades for 9-trade truth set) ────


async def backfill_tick_validation(ep: PolygonEndpoints) -> int:
    """Pull /v3/quotes + /v3/trades for every (ticker, date) in
    data/replay/prod_qty_truth.parquet, full session window."""
    corpus = "tick_validation"
    out_dir = OUT_ROOT / "tick_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = _load_progress(corpus)

    pqt = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
    if not pqt.exists():
        logger.error("tick_validation: %s does not exist; skipping", pqt)
        return 0
    truth = pd.read_parquet(pqt)

    new_completed = []
    pulled = 0
    for _, row in truth.iterrows():
        ticker = str(row["ticker"]).upper()
        sess_date = str(row["session_date"])  # YYYY-MM-DD
        # Window: 09:00 ET → 16:30 ET (covers pre-market + regular session
        # + 30min cushion). Convert to nanoseconds.
        # ET is UTC-5 in winter / UTC-4 in summer; for April use UTC-4.
        # 09:00 ET → 13:00 UTC; 16:30 ET → 20:30 UTC.
        d = datetime.strptime(sess_date, "%Y-%m-%d")
        from_dt = datetime(d.year, d.month, d.day, 13, 0, 0, tzinfo=timezone.utc)
        to_dt = datetime(d.year, d.month, d.day, 20, 30, 0, tzinfo=timezone.utc)
        from_ns = int(from_dt.timestamp() * 1e9)
        to_ns = int(to_dt.timestamp() * 1e9)

        for kind in ("quotes", "trades"):
            key = (ticker, sess_date, kind)
            if key in done:
                continue
            try:
                t0 = time.perf_counter()
                if kind == "quotes":
                    rows = await ep.quotes(
                        ticker, timestamp_gte_ns=from_ns, timestamp_lte_ns=to_ns,
                        limit=50_000,
                    )
                    df = pd.DataFrame([
                        {
                            "sip_ts_ns": q.sip_timestamp_ns,
                            "participant_ts_ns": q.participant_timestamp_ns,
                            "bid_price": q.bid_price, "bid_size": q.bid_size,
                            "ask_price": q.ask_price, "ask_size": q.ask_size,
                            "bid_exchange": q.bid_exchange, "ask_exchange": q.ask_exchange,
                            "midpoint": q.midpoint,
                        }
                        for q in rows
                    ])
                else:
                    rows = await ep.trades(
                        ticker, timestamp_gte_ns=from_ns, timestamp_lte_ns=to_ns,
                        limit=50_000,
                    )
                    df = pd.DataFrame([
                        {
                            "sip_ts_ns": t.sip_timestamp_ns,
                            "participant_ts_ns": t.participant_timestamp_ns,
                            "price": t.price, "size": t.size,
                            "exchange": t.exchange, "trade_id": t.trade_id,
                        }
                        for t in rows
                    ])
                dt = time.perf_counter() - t0
                date_dir = out_dir / sess_date
                date_dir.mkdir(parents=True, exist_ok=True)
                out_path = date_dir / f"{ticker}_{kind}.parquet"
                df.to_parquet(out_path, index=False)
                pulled += 1
                new_completed.append({"ticker": ticker, "session_date": sess_date, "kind": kind})
                logger.info(
                    "tick_validation %s %s %s: %d rows in %.1fs",
                    sess_date, ticker, kind, len(rows), dt,
                )
            except Exception as e:
                logger.error("tick_validation %s %s %s FAILED: %s", sess_date, ticker, kind, e)

    _save_progress(corpus, new_completed)
    logger.info("tick_validation complete: %d new (ticker,date,kind) pulls", pulled)
    return pulled


# ── B.4: fundamentals for the EP universe ─────────────────────────


async def backfill_fundamentals(ep: PolygonEndpoints, max_tickers: Optional[int] = None) -> int:
    corpus = "fundamentals"
    out_dir = OUT_ROOT / "fundamentals"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = _load_progress(corpus)

    universe = sorted(_collect_project_tickers())
    if max_tickers:
        universe = universe[:max_tickers]
    new_completed = []
    pulled = 0

    for ticker in universe:
        key = (ticker,)
        if key in done:
            continue
        try:
            # Two pulls per ticker: financials + ticker_details (market cap)
            reports = await ep.financials(ticker, timeframe="quarterly", limit=8)
            details = await ep.ticker_details(ticker)
            payload = {
                "ticker": ticker,
                "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
                "details": None if details is None else {
                    "name": details.name, "market": details.market,
                    "primary_exchange": details.primary_exchange, "type": details.type,
                    "active": details.active, "cik": details.cik,
                    "market_cap": details.market_cap,
                    "weighted_shares_outstanding": details.weighted_shares_outstanding,
                },
                "financials": [
                    {
                        "fiscal_period": r.fiscal_period, "fiscal_year": r.fiscal_year,
                        "start_date": r.start_date, "end_date": r.end_date,
                        "timeframe": r.timeframe,
                        "revenues": r.revenues, "net_income_loss": r.net_income_loss,
                        "operating_income_loss": r.operating_income_loss,
                        "diluted_eps": r.diluted_earnings_per_share,
                    }
                    for r in reports
                ],
            }
            out_path = out_dir / f"{ticker}.json"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            pulled += 1
            new_completed.append({"ticker": ticker})
            if pulled % 25 == 0:
                logger.info("fundamentals progress: %d / %d", pulled, len(universe))
        except Exception as e:
            logger.warning("fundamentals %s FAILED: %s", ticker, e)

    _save_progress(corpus, new_completed)
    logger.info("fundamentals complete: %d new tickers pulled", pulled)
    return pulled


# ── Main ─────────────────────────────────────────────────────────


async def main() -> None:
    p = argparse.ArgumentParser(description="Polygon backfill orchestrator")
    p.add_argument(
        "--corpus", required=True,
        choices=["etf_baseline", "equity_universe", "tick_validation", "fundamentals", "all"],
    )
    p.add_argument("--years", type=int, default=10, help="History window in years")
    p.add_argument("--max", type=int, default=None, help="Max tickers (for testing)")
    args = p.parse_args()

    async with PolygonClient.from_env() as c:
        ep = PolygonEndpoints(c)
        if args.corpus == "tick_validation":
            await backfill_tick_validation(ep)
        elif args.corpus == "fundamentals":
            await backfill_fundamentals(ep, max_tickers=args.max)
        elif args.corpus == "etf_baseline":
            await backfill_etf_baseline(ep, years=args.years)
        elif args.corpus == "equity_universe":
            await backfill_equity_universe(ep, years=args.years, max_tickers=args.max)
        elif args.corpus == "all":
            # Highest-priority pulls first.
            await backfill_tick_validation(ep)
            await backfill_fundamentals(ep, max_tickers=args.max)
            await backfill_etf_baseline(ep, years=args.years)
            await backfill_equity_universe(ep, years=args.years, max_tickers=args.max)


if __name__ == "__main__":
    asyncio.run(main())
