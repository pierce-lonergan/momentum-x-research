"""DOC 298 — THE REGIME EXTENSION: pull the warehouse back to 2016.

WHY THIS IS THE HIGHEST-VALUE BUILD ON THE BOARD
------------------------------------------------
The doc-298 adversary measured two levers against the multiplicity bar:

    culling the ENTIRE 206-hypothesis slate ....... 1.22x relief
    extending the sample 2.5yr -> 10.5yr .......... 2.05x relief   (and it is free)

Confirmed locally with scripts/trial_registry.py: at this program's ~60 gated hypotheses the
null-expected best Sharpe falls from **1.483 to 0.724**. That is the difference between a bar nothing
this program could plausibly produce and a bar that an audited Carver-grade 0.80 would clear.

The second reason matters more than the arithmetic. Every "cross-regime" verdict this program owns was
computed inside 2024-01 -> 2026-07, which is ONE BULL REGIME. That includes the -2.041%/ticket gapper
measurement, the doc-251 fade, and the doc-297 overnight-ETF kill. The reachable window contains three
genuine bear regimes the corpus has never seen:

    2018 Q4 selloff   SPY -16.6%
    2020 COVID crash  SPY -24.4%
    2022 bear         SPY -13.2%

Verified 2026-07-29: Alpaca serves daily bars, minute bars AND NBBO quotes back to 2016 on the existing
entitlement. The program has been data-starved by choice, not by permission.

WHAT THIS PULLS
---------------
Daily OHLCV bars, split/dividend adjusted, for the requested universe, 2016-01-01 -> today, written to
data/polygon_warehouse/daily_2016/ as one parquet per year. Daily first because it is cheap, it is
enough for every cross-sectional and regime hypothesis on the slate, and it settles whether a longer
sample changes any standing verdict before anyone spends disk on minutes.

Resumable (a year already written is skipped unless --force), bounded, and read-only against the broker.

Usage:
    python scripts/extend_warehouse_2016.py --status
    python scripts/extend_warehouse_2016.py --universe liquid --start 2016
    python scripts/extend_warehouse_2016.py --universe all --start 2016
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "polygon_warehouse" / "daily_2016"
DATA_HOST = "https://data.alpaca.markets"
TRADING_HOST = "https://paper-api.alpaca.markets"
BATCH = 200          # symbols per request


def _creds() -> tuple[str, str]:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    k = os.environ.get("ALPACA_API_KEY", "")
    s = os.environ.get("ALPACA_SECRET_KEY", "")
    if not k or not s:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY not present")
    return k, s


def _get(url: str, key: str, secret: str) -> dict:
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 * (attempt + 1)); continue
            if e.code >= 500:
                time.sleep(2 * (attempt + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(3 * (attempt + 1)); continue
    raise RuntimeError("retries exhausted")


def universe(kind: str, key: str, secret: str) -> list[str]:
    """Tradable US equities. 'liquid' keeps common stock + ETFs that are marginable and shortable-ish."""
    assets = _get(f"{TRADING_HOST}/v2/assets?status=active&asset_class=us_equity", key, secret)
    if kind == "all":
        syms = [a["symbol"] for a in assets if a.get("tradable")]
    else:
        syms = [a["symbol"] for a in assets
                if a.get("tradable") and a.get("marginable")
                and a.get("exchange") in ("NYSE", "NASDAQ", "ARCA", "BATS", "AMEX")]
    # exclude symbols the bars endpoint cannot express
    return sorted({s for s in syms if s and "/" not in s and len(s) <= 6})


def pull_year(syms: list[str], year: int, key: str, secret: str) -> tuple[int, int]:
    import pandas as pd
    start = f"{year}-01-01T00:00:00Z"
    end = f"{min(year, date.today().year)}-12-31T23:59:59Z"
    rows, calls = [], 0
    for i in range(0, len(syms), BATCH):
        chunk = syms[i:i + BATCH]
        page = None
        while True:
            url = (f"{DATA_HOST}/v2/stocks/bars?symbols={urllib.parse.quote(','.join(chunk))}"
                   f"&timeframe=1Day&start={start}&end={end}&limit=10000&adjustment=all")
            if page:
                url += f"&page_token={page}"
            d = _get(url, key, secret)
            calls += 1
            for sym, bars in (d.get("bars") or {}).items():
                for b in bars:
                    rows.append((sym, b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"],
                                 b.get("n"), b.get("vw")))
            page = d.get("next_page_token")
            if not page:
                break
    if not rows:
        return 0, calls
    df = pd.DataFrame(rows, columns=["ticker", "d", "o", "h", "l", "c", "v", "n", "vw"])
    STORE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(STORE / f"daily_{year}.parquet", index=False)
    return len(df), calls


def status() -> dict:
    if not STORE.exists():
        return {"years": 0, "note": "not built yet"}
    import pandas as pd
    out, tot = {}, 0
    for f in sorted(STORE.glob("daily_*.parquet")):
        df = pd.read_parquet(f, columns=["ticker", "d"])
        out[f.stem[-4:]] = {"rows": len(df), "tickers": int(df.ticker.nunique()),
                            "sessions": int(df.d.nunique())}
        tot += len(df)
    return {"years": len(out), "rows_total": tot, "per_year": out,
            "path": str(STORE.relative_to(_ROOT))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--universe", default="liquid", choices=["liquid", "all"])
    ap.add_argument("--start", type=int, default=2016)
    ap.add_argument("--end", type=int, default=date.today().year)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(status(), indent=2))
        return 0

    key, secret = _creds()
    syms = universe(args.universe, key, secret)
    print(json.dumps({"universe": args.universe, "symbols": len(syms)}))
    total_rows = total_calls = 0
    for yr in range(args.start, args.end + 1):
        dest = STORE / f"daily_{yr}.parquet"
        if dest.exists() and not args.force:
            # doc 298: "file exists" is NOT "year is complete". A partial write — an interrupted run,
            # or a small-universe diagnostic that happened to land here — would otherwise be skipped
            # forever and silently poison every downstream regime test. Verify coverage, not presence.
            try:
                import pandas as pd
                have = int(pd.read_parquet(dest, columns=["ticker"]).ticker.nunique())
            except Exception:
                have = 0
            if have >= 0.5 * len(syms):
                print(json.dumps({"year": yr, "skipped": "complete", "tickers": have}), flush=True)
                continue
            print(json.dumps({"year": yr, "REBUILDING": "partial file",
                              "tickers_found": have, "expected_min": int(0.5 * len(syms))}), flush=True)
        t0 = time.time()
        n, calls = pull_year(syms, yr, key, secret)
        total_rows += n; total_calls += calls
        print(json.dumps({"year": yr, "rows": n, "calls": calls,
                          "seconds": round(time.time() - t0, 1)}), flush=True)
    print(json.dumps({"done": True, "rows": total_rows, "calls": total_calls,
                      "asof": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
