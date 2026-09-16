"""DOC 298 — TRUE NBBO COST: replace the Roll proxy with the quoted spread itself.

WHY
---
Every cost number this program has ever used came from the Roll estimator on 1-minute closes. The
doc-298 cull measured that estimator against ground truth and found it: 0.56x truth on thin names,
3.5x truth on SPY, and undefined (<=0, i.e. a failed estimate) on 29.5% of 463,183 liquid ticker-days.
Worse, one generator's cost script coded a FAILED estimate as 0.0 bps and carried the zeros into a
median, producing a "monotone cost ladder" that was a map of estimator failure rather than of cost.

And the 7.2 bps liquid cost line in docs/TARGET.md -- the line that gates roughly thirty hypotheses --
has no artifact behind it at all. doc 297's own `_atk7_roll.json` holds 0.42-3.57 bps and no 7.2.

Alpaca serves historical equity NBBO (verified 2026-07-29: full bid/ask/sizes at nanosecond stamps,
back past 2024-03, multi-symbol). So the proxy is unnecessary. This measures the thing directly.

METHOD
------
Stratified sampling, because a mean over all ticker-days is dominated by whatever is most numerous:
  - sample sessions from a stated date range (regular trading days only)
  - sample intraday instants at fixed ET clock times, avoiding 09:30-09:35 and 15:55-16:00 where
    spreads are unrepresentative of when a patient strategy would actually trade
  - at each (ticker, session, instant) take the prevailing NBBO and record the RELATIVE QUOTED SPREAD
    (ask-bid)/mid in bps, plus displayed depth at the touch in dollars

Reported per ticker and per liquidity tier with a session-clustered bootstrap CI, because quotes on the
same session are not independent (doc-297 law 4).

WHAT THIS IS AND IS NOT
-----------------------
IS: the cost of crossing the spread, which for a $190K account trading liquid names in small size is
the dominant term -- displayed depth at the touch is reported so that assumption is checkable, not
assumed.
IS NOT: market impact for size beyond the touch, adverse selection, or the cost of patience. A strategy
that must trade more than the displayed depth pays more than this number, and this file does not
pretend otherwise.

READ-ONLY: GET endpoints only. No order path. Never prints credentials.

Usage:
    python scripts/true_nbbo_cost.py --universe liquid --sessions 10
    python scripts/true_nbbo_cost.py --tickers SPY,QQQ,AAPL,F --sessions 5 --out my_probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = _ROOT / "data" / "research" / "doc298"
DATA_HOST = "https://data.alpaca.markets"

# ET clock instants to sample. Deliberately excludes the first and last five minutes.
SAMPLE_TIMES_ET = ["09:45", "10:30", "11:30", "13:00", "14:30", "15:30", "15:50"]

TIERS = {
    "index_etf":  ["SPY", "QQQ", "IWM", "DIA"],
    "mega_cap":   ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"],
    "large_cap":  ["JPM", "XOM", "WMT", "UNH", "BAC", "DIS"],
    "mid_liquid": ["F", "PLUG", "SOFI", "RIVN", "AAL", "SNAP"],
    "low_priced": ["NIO", "MARA", "RIOT", "LCID", "CHPT", "GRAB"],
}


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
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 * (attempt + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(3 * (attempt + 1)); continue
    raise RuntimeError("retries exhausted")


def _sessions(n: int, end: datetime | None = None) -> list[str]:
    """The last n weekdays ending at `end` (holidays are tolerated: a holiday simply yields no quotes)."""
    end = end or datetime.now(timezone.utc)
    out, d = [], end.date()
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
    return sorted(out)


def probe(tickers: list[str], sessions: list[str], key: str, secret: str) -> list[dict]:
    rows: list[dict] = []
    for sess in sessions:
        for hhmm in SAMPLE_TIMES_ET:
            # ET -> UTC (EDT = UTC-4; the sampled range is inside DST for these dates)
            h, m = (int(x) for x in hhmm.split(":"))
            start = f"{sess}T{h + 4:02d}:{m:02d}:00Z"
            end = f"{sess}T{h + 4:02d}:{m:02d}:02Z"
            syms = ",".join(tickers)
            url = (f"{DATA_HOST}/v2/stocks/quotes?symbols={urllib.parse.quote(syms)}"
                   f"&start={start}&end={end}&limit=1000")
            try:
                d = _get(url, key, secret)
            except Exception:
                continue
            quotes = d.get("quotes") or {}
            for tk, qs in quotes.items():
                if not qs:
                    continue
                q = qs[0]           # first prevailing quote in the 2-second window
                b, a = q.get("bp"), q.get("ap")
                bs, asz = q.get("bs") or 0, q.get("as") or 0
                if not b or not a or a <= b:
                    continue
                mid = 0.5 * (a + b)
                rows.append({
                    "session": sess, "et_time": hhmm, "ticker": tk,
                    "bid": b, "ask": a, "mid": mid,
                    "rel_spread_bps": 1e4 * (a - b) / mid,
                    "depth_touch_usd": mid * min(bs, asz) * 100,   # sizes are round lots
                })
    return rows


def summarize(rows: list[dict], tier_of: dict[str, str]) -> dict:
    def boot_ci(vals_by_session: dict[str, list[float]], n_boot: int = 2000) -> tuple[float, float]:
        """Session-clustered bootstrap: resample SESSIONS, not observations (doc-297 law 4)."""
        import random
        rnd = random.Random(298)
        keys = list(vals_by_session)
        if len(keys) < 2:
            return (float("nan"), float("nan"))
        means = []
        for _ in range(n_boot):
            pick = [rnd.choice(keys) for _ in keys]
            pool = [v for k in pick for v in vals_by_session[k]]
            if pool:
                means.append(st.median(pool))
        means.sort()
        return (means[int(0.025 * len(means))], means[int(0.975 * len(means))])

    per_ticker: dict[str, dict] = {}
    by_tk: dict[str, dict[str, list[float]]] = {}
    depth_tk: dict[str, list[float]] = {}
    for r in rows:
        by_tk.setdefault(r["ticker"], {}).setdefault(r["session"], []).append(r["rel_spread_bps"])
        depth_tk.setdefault(r["ticker"], []).append(r["depth_touch_usd"])
    for tk, bysess in by_tk.items():
        allv = [v for vs in bysess.values() for v in vs]
        lo, hi = boot_ci(bysess)
        per_ticker[tk] = {
            "tier": tier_of.get(tk, "?"), "n": len(allv),
            "median_rel_spread_bps": round(st.median(allv), 3),
            "ci95_session_clustered": [round(lo, 3), round(hi, 3)],
            "p75_bps": round(sorted(allv)[int(0.75 * len(allv))], 3),
            "median_depth_at_touch_usd": round(st.median(depth_tk[tk]), 0),
        }
    per_tier: dict[str, dict] = {}
    for tier in set(tier_of.values()):
        tks = [t for t in per_ticker if tier_of.get(t) == tier]
        if not tks:
            continue
        meds = [per_ticker[t]["median_rel_spread_bps"] for t in tks]
        depths = [per_ticker[t]["median_depth_at_touch_usd"] for t in tks]
        per_tier[tier] = {
            "tickers": len(tks),
            "median_rel_spread_bps": round(st.median(meds), 3),
            "round_trip_bps": round(st.median(meds), 3),   # one full spread = cross in + cross out
            "median_depth_at_touch_usd": round(st.median(depths), 0),
        }
    by_time: dict[str, float] = {}
    tmp: dict[str, list[float]] = {}
    for r in rows:
        tmp.setdefault(r["et_time"], []).append(r["rel_spread_bps"])
    for t, v in sorted(tmp.items()):
        by_time[t] = round(st.median(v), 3)
    return {"per_ticker": per_ticker, "per_tier": per_tier, "median_bps_by_et_time": by_time,
            "n_observations": len(rows),
            "note": ("round_trip_bps = one full quoted spread (cross to enter, cross to exit). "
                     "Excludes impact beyond the displayed touch, adverse selection, and fees. "
                     "Check median_depth_at_touch_usd against intended order size before using.")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default="")
    ap.add_argument("--universe", default="all", choices=["all", "liquid", "thin"])
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--out", default="true_nbbo_cost.json")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        tier_of = {t: "custom" for t in tickers}
    else:
        chosen = TIERS
        if args.universe == "liquid":
            chosen = {k: v for k, v in TIERS.items() if k in ("index_etf", "mega_cap", "large_cap")}
        elif args.universe == "thin":
            chosen = {k: v for k, v in TIERS.items() if k in ("mid_liquid", "low_priced")}
        tickers = [t for v in chosen.values() for t in v]
        tier_of = {t: k for k, v in chosen.items() for t in v}

    key, secret = _creds()
    sessions = _sessions(args.sessions)
    rows = probe(tickers, sessions, key, secret)
    res = summarize(rows, tier_of)
    res["sessions_sampled"] = sessions
    res["et_times_sampled"] = SAMPLE_TIMES_ET
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / args.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("per_tier", "median_bps_by_et_time", "n_observations")}, indent=2))
    print(f"\nfull result -> data/research/doc298/{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
