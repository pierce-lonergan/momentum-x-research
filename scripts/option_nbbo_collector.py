"""DOC 298 — THE FORWARD OPTION-NBBO COLLECTOR: the one dataset that can only be built by starting.

WHY THIS EXISTS, AND WHY TODAY
------------------------------
Doc 297 verified, by direct probe, that this plan does NOT serve historical option NBBO quotes
(404 on every documented endpoint shape). Only *latest* quotes are served. Daily option BARS go back
to 2024-01-18, but bars carry no bid/ask — so every option cost estimate the program has ever made
was a guess.

That has two consequences:

  1. Doc 295's Stage-4 spread-aware simulation is blocked by a data gap that cannot be backfilled at
     any price on this plan.
  2. The gap closes only by collecting FORWARD. Every session that passes without a snapshot is a
     session permanently missing from the future dataset.

So this collector's value is a pure function of when it starts. It is $0 (Alpaca's data key serves
full chains with OPRA NBBO and free greeks at 10,000 calls/min — measured 120 calls in 8.47s, no
429s), and it is forward-only by construction, which means **look-ahead contamination of the doc-296
kind is impossible here**: a snapshot taken at time t contains only what was quoted at time t.

WHAT IT RECORDS
---------------
One row per (session, ticker, contract): bid, ask, sizes, last, IV and greeks as the venue published
them, plus the underlying price and the exact snapshot timestamp. Nothing is derived, nothing is
inverted, nothing is modelled — the entire point is to bank the raw observable that cannot be
recovered later. Derivation happens downstream, where it can be re-done when someone finds a bug.

DISCIPLINE
----------
- READ-ONLY against the broker: GET endpoints only. This file contains no order path of any kind.
- Never prints credentials.
- Append-only JSONL partitioned by session date; re-running a session is idempotent per contract.
- Bounded per run (--max-calls) and resumable, so an interrupted run costs nothing.

Usage:
    python scripts/option_nbbo_collector.py --status
    python scripts/option_nbbo_collector.py --collect                 # default universe
    python scripts/option_nbbo_collector.py --collect --tickers SPY,QQQ,AAPL --dte-max 60
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "option_nbbo"
DATA_HOST = "https://data.alpaca.markets"

# Default universe: the liquid end, where doc 297 says cost leaves room for an edge to exist at all.
# Index ETFs first (deepest, tightest chains), then large caps with dense option markets.
DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
]


def _creds() -> tuple[str, str]:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    k = os.environ.get("ALPACA_API_KEY", "")
    s = os.environ.get("ALPACA_SECRET_KEY", "")
    if not k or not s:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY not present in environment")
    return k, s


def _get(url: str, key: str, secret: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "accept": "application/json",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            # doc 287's preflight lesson: transient blips must not kill an unattended run.
            time.sleep(5 * (attempt + 1))
            continue
    raise RuntimeError("retries exhausted")


def _session_path(session: str) -> Path:
    return STORE / f"option_nbbo_{session}.jsonl"


def _existing_contracts(session: str) -> set[str]:
    p = _session_path(session)
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.add(json.loads(line)["contract"])
            except Exception:
                continue
    return out


def _underlying_price(ticker: str, key: str, secret: str) -> float | None:
    try:
        d = _get(f"{DATA_HOST}/v2/stocks/{ticker}/trades/latest", key, secret)
        return float(((d.get("trade") or {}).get("p")) or 0) or None
    except Exception:
        return None


def collect(tickers: list[str], dte_max: int, max_calls: int) -> dict:
    key, secret = _creds()
    now = datetime.now(timezone.utc)
    session = now.astimezone(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")  # ET session date
    STORE.mkdir(parents=True, exist_ok=True)
    path = _session_path(session)
    have = _existing_contracts(session)

    exp_lte = (now + timedelta(days=dte_max)).strftime("%Y-%m-%d")
    calls = 0
    written = 0
    per_ticker: dict[str, int] = {}

    with path.open("a", encoding="utf-8") as fh:
        for tk in tickers:
            if calls >= max_calls:
                break
            spot = _underlying_price(tk, key, secret)
            calls += 1
            page = None
            n_tk = 0
            while calls < max_calls:
                url = (f"{DATA_HOST}/v1beta1/options/snapshots/{tk}"
                       f"?feed=opra&limit=1000&expiration_date_lte={exp_lte}")
                if page:
                    url += f"&page_token={page}"
                try:
                    d = _get(url, key, secret)
                except Exception as e:
                    per_ticker[tk] = per_ticker.get(tk, 0)
                    print(json.dumps({"ticker": tk, "error": type(e).__name__}))
                    break
                calls += 1
                snaps = d.get("snapshots") or {}
                stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for contract, s in snaps.items():
                    if contract in have:
                        continue
                    q = s.get("latestQuote") or {}
                    if not q:
                        continue
                    g = s.get("greeks") or {}
                    row = {
                        "session": session,
                        "snapshot_utc": stamp,
                        "ticker": tk,
                        "contract": contract,
                        "underlying_price": spot,
                        "quote_ts": q.get("t"),
                        "bid": q.get("bp"), "ask": q.get("ap"),
                        "bid_size": q.get("bs"), "ask_size": q.get("as"),
                        "iv": s.get("impliedVolatility"),
                        "delta": g.get("delta"), "gamma": g.get("gamma"),
                        "theta": g.get("theta"), "vega": g.get("vega"), "rho": g.get("rho"),
                        "source": "alpaca_opra_snapshot",
                    }
                    fh.write(json.dumps(row) + "\n")
                    have.add(contract)
                    written += 1
                    n_tk += 1
                page = d.get("next_page_token")
                if not page:
                    break
            per_ticker[tk] = n_tk
    return {"session": session, "calls": calls, "rows_written": written,
            "per_ticker": per_ticker, "path": str(path.relative_to(_ROOT))}


def status() -> dict:
    if not STORE.exists():
        return {"sessions": 0, "note": "no collection yet — the dataset starts the day this first runs"}
    files = sorted(STORE.glob("option_nbbo_*.jsonl"))
    per = {}
    total = 0
    for f in files:
        n = sum(1 for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
        per[f.stem.replace("option_nbbo_", "")] = n
        total += n
    spreads = {}
    if files:
        # descriptive only: median relative spread on the most recent session, for sanity
        rows = [json.loads(l) for l in files[-1].read_text(encoding="utf-8").splitlines() if l.strip()]
        by_tk: dict[str, list[float]] = {}
        for r in rows:
            b, a = r.get("bid"), r.get("ask")
            if b and a and a > 0 and b > 0:
                mid = 0.5 * (a + b)
                if mid > 0:
                    by_tk.setdefault(r["ticker"], []).append((a - b) / mid)
        for tk, v in by_tk.items():
            v.sort()
            spreads[tk] = round(100 * v[len(v) // 2], 2)
    return {"sessions": len(files), "rows_total": total, "rows_per_session": per,
            "median_rel_spread_pct_latest_session": spreads}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tickers", default=",".join(DEFAULT_UNIVERSE))
    ap.add_argument("--dte-max", type=int, default=45,
                    help="only contracts expiring within N days (keeps the snapshot focused and cheap)")
    ap.add_argument("--max-calls", type=int, default=400)
    args = ap.parse_args()

    if args.status or not args.collect:
        print(json.dumps(status(), indent=2))
        return 0
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    print(json.dumps(collect(tickers, args.dte_max, args.max_calls), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
