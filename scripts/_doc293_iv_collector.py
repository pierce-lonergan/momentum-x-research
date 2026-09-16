"""DOC 293 — the $0 IV COLLECTOR: builds the Stage-3 single-name IV panel from the EXISTING Polygon
entitlement (historical option-contract EOD aggregates @ 5 calls/min; the IV snapshot is paywalled, so IV
is computed here by Black-Scholes inversion from option closes).

Entitlement probe (data/research/doc293/entitlement_probe*.json): reference contracts 200, per-contract
daily aggs 200 with real bars, snapshot 403, rate limit ~5/min -> the full ~150-name x 2yr panel is a
multi-night drip (~16K calls). This collector is RESUMABLE (state file), polite (12.5s/call), bounded per
run (--budget calls), and writes computed IV rows into the doc-292 canonical store via _doc292_iv_ingest.

Method per (name, week): at each Monday (or first session) of each week, select the expiration nearest
30 calendar days ahead (21..45 window) and the strike nearest spot (from the local minute-bar warehouse —
no API calls for spot); pull that call AND put contract's FULL 2-year daily aggs (1 call each, covers every
later week that reuses the contract); invert Black-Scholes on each day's close (r=0.045 flat, q=0 — DISCLOSED
approximations; ATM 30d IV is insensitive to both at the ~1-vol-point level) and average call/put IV.
Rows: {date, ticker, tenor_days=actual DTE, iv, kind='atm', source='polygon_bs_inverted'}.

NO plan changes, no signups; the key already exists in .env. Never prints the key.
Usage: --names AAPL,MSFT --budget 300      (one bounded tranche)
       --status                            (panel coverage so far)
"""
from __future__ import annotations
import argparse, json, math, os, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STATE = _ROOT / "data" / "research" / "doc293" / "iv_collector_state.json"
CALL_SPACING = 12.5          # 5/min with margin
R_FLAT, Q_FLAT = 0.045, 0.0  # disclosed approximations

# Stage-3 target universe: the doc-291 151-name panel, ordered most-liquid-first for collection priority.
PRIORITY = ['AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','GS','IBM','AMD','NFLX','JPM','AVGO','COIN',
            'PLTR','MU','BA','INTC','CRM','ORCL','QCOM','XOM','V','MA','UNH','WMT','DIS','BAC','PYPL','SMCI']


def _key():
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    k = os.environ.get("POLYGON_API_KEY", "")
    if not k:
        raise SystemExit("no POLYGON_API_KEY")
    return k


_last_call = [0.0]


def get(url, key):
    wait = CALL_SPACING - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url + ("&" if "?" in url else "?") + "apiKey=" + key, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            # doc 294: transient network blips (WinError 10060 etc.) must not kill an unattended nightly
            # run — the doc-287 preflight lesson. Backoff and retry; state-file resumability catches the rest.
            time.sleep(20 * (attempt + 1))
            continue
    raise RuntimeError("retries exhausted (429s or network)")


# ── Black-Scholes inversion (bisection; robust, dependency-free) ─────────────

def _bs_price(S, K, T, r, q, sigma, is_call):
    if T <= 0 or sigma <= 0:
        intr = max(S - K, 0.0) if is_call else max(K - S, 0.0)
        return intr
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if is_call:
        return S * math.exp(-q * T) * N(d1) - K * math.exp(-r * T) * N(d2)
    return K * math.exp(-r * T) * N(-d2) - S * math.exp(-q * T) * N(-d1)


def implied_vol(price, S, K, T, r=R_FLAT, q=Q_FLAT, is_call=True):
    if price <= 0 or S <= 0 or T <= 0:
        return None
    intr = _bs_price(S, K, T, r, q, 1e-9, is_call)
    if price <= intr + 1e-6:
        return None
    lo, hi = 1e-3, 5.0
    if _bs_price(S, K, T, r, q, hi, is_call) < price:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _bs_price(S, K, T, r, q, mid, is_call) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ── spot prices from the local warehouse (0 API calls) ───────────────────────

def load_spots(names):
    import duckdb
    con = duckdb.connect(); con.execute("SET threads=4")
    tk = "','".join(names)
    df = con.execute(f"""
        SELECT ticker, strftime(ts_et,'%Y-%m-%d') d, last(close ORDER BY ts_et) c
        FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet', union_by_name=true)
        WHERE ticker IN ('{tk}') AND strftime(ts_et,'%H:%M') BETWEEN '09:30' AND '16:00'
        GROUP BY ticker, d""").df()
    con.close()
    spots = {}
    for r in df.itertuples():
        spots.setdefault(r.ticker, {})[r.d] = float(r.c)
    return spots


PRICES = _ROOT / "data" / "iv_store" / "option_eod.jsonl"


def _parse_occ(contract):
    """O:ROOTYYMMDD[C|P]KKKKKKKK -> (root, exp_iso, cp, K)"""
    body = contract.split(":", 1)[1]
    K = int(body[-8:]) / 1000.0
    cp = body[-9]
    exp = "20" + body[-15:-9]
    exp_iso = f"{exp[:4]}-{exp[4:6]}-{exp[6:8]}"
    root = body[:-15]
    return root, exp_iso, cp, K


def write_prices(rows):
    """idempotent upsert of option EOD price rows on (date, contract)."""
    existing = []
    if PRICES.exists():
        existing = [json.loads(l) for l in PRICES.read_text(encoding="utf-8").splitlines() if l.strip()]
    have = {(r["date"], r["contract"]): i for i, r in enumerate(existing)}
    for r in rows:
        k = (r["date"], r["contract"])
        if k in have:
            existing[have[k]] = r
        else:
            existing.append(r)
    PRICES.parent.mkdir(parents=True, exist_ok=True)
    PRICES.write_text("\n".join(json.dumps(r) for r in
                                sorted(existing, key=lambda r: (r["ticker"], r["date"], r["contract"])))
                      + ("\n" if existing else ""), encoding="utf-8")


def _price_rows_from_aggs(aggs, contract, name, spots, anchor=None):
    root, exp_iso, cp, K = _parse_occ(contract)
    out = []
    for bar in aggs.get("results") or []:
        d_ = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if anchor and d_ < anchor:
            # doc 296 skeptic (MAJOR): strike chosen with spot at `anchor`; pre-anchor rows would let
            # SEVP select a straddle whose moneyness embeds future spot. Same rule as the IV rows.
            continue
        S_ = spots.get(name, {}).get(d_)
        out.append({"date": d_, "ticker": name, "contract": contract, "cp": cp, "K": K,
                    "exp": exp_iso, "close": bar["c"], "S": S_})
    return out


def _state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"done_contracts": {}, "done_names": [], "calls_spent": 0}


def _save(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")


def collect(names, budget):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("ing", str(_ROOT / "scripts" / "_doc292_iv_ingest.py"))
    ING = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(ING)
    key = _key()
    st = _state()
    spots = load_spots(names)
    calls = 0
    for name in names:
        if name in st["done_names"]:
            continue
        days_all = sorted(spots.get(name, {}))
        if not days_all:
            continue
        # entitlement window: options history is ~2yr rolling (ladder-probed 2026-07-12: bars at 22mo,
        # 403/empty at ~30mo). Clamp anchors to 22 months back so no call is wasted past the edge.
        cutoff = "2024-09-15"
        days = [d for d in days_all if d >= cutoff]
        if not days:
            continue
        # MONTHLY anchors: each anchor's ~30d contract yields daily IV across its whole life, so monthly
        # cadence gives near-continuous coverage at 1/4 the call budget of weekly.
        anchors, seen = [], set()
        for d in days:
            mo = d[:7]
            if mo not in seen:
                seen.add(mo); anchors.append(d)
        contracts_needed = []          # (contract_ticker, is_call, K, expiry)
        for a in anchors:
            S = spots[name][a]
            if calls >= budget:
                break
            # find nearest-30d expiration + nearest strike via reference (1 call per anchor, cached by key)
            ck = f"{name}:{a}"
            if ck in st["done_contracts"]:
                pair = st["done_contracts"][ck]
            else:
                ref = get(f"https://api.polygon.io/v3/reference/options/contracts?underlying_ticker={name}"
                          f"&contract_type=call&expiration_date.gte={a}&as_of={a}&limit=1000&sort=expiration_date", key)
                calls += 1; st["calls_spent"] += 1
                res = ref.get("results") or []
                # pick expiry 21-45d out nearest 30
                def dte(e):
                    return (datetime.fromisoformat(e) - datetime.fromisoformat(a)).days
                exps = sorted({r_["expiration_date"] for r_ in res if 21 <= dte(r_["expiration_date"]) <= 45},
                              key=lambda e: abs(dte(e) - 30))
                if not exps:
                    st["done_contracts"][ck] = None; _save(st); continue
                exp = exps[0]
                strikes = sorted({r_["strike_price"] for r_ in res if r_["expiration_date"] == exp},
                                 key=lambda k_: abs(k_ - S))
                if not strikes:
                    st["done_contracts"][ck] = None; _save(st); continue
                K = strikes[0]
                root = name
                kfmt = f"{int(round(K * 1000)):08d}"
                efmt = datetime.fromisoformat(exp).strftime("%y%m%d")
                pair = {"call": f"O:{root}{efmt}C{kfmt}", "put": f"O:{root}{efmt}P{kfmt}",
                        "K": K, "exp": exp}
                st["done_contracts"][ck] = pair; _save(st)
            if not pair:
                continue
            for is_call in (True, False):
                ct = pair["call" if is_call else "put"]
                if ct in st["done_contracts"].get("_pulled", {}):
                    continue
                if calls >= budget:
                    break
                aggs = get(f"https://api.polygon.io/v2/aggs/ticker/{ct}/range/1/day/{days[0]}/{days[-1]}?limit=50000", key)
                calls += 1; st["calls_spent"] += 1
                write_prices(_price_rows_from_aggs(aggs, ct, name, spots, anchor=a))   # doc 294: SEVP needs prices
                rows = []
                for bar in aggs.get("results") or []:
                    d_ = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    if d_ < a:
                        # doc 296 skeptic (MAJOR): the contract's strike was selected using spot at the
                        # anchor date `a`; emitting IV for days BEFORE the anchor embeds future-spot
                        # moneyness info in iv(t). Emit only anchor-date-or-later rows.
                        continue
                    S_ = spots[name].get(d_)
                    if not S_:
                        continue
                    T = (datetime.fromisoformat(pair["exp"]) - datetime.fromisoformat(d_)).days / 365.0
                    iv = implied_vol(bar["c"], S_, pair["K"], T, is_call=is_call)
                    if iv and 0.03 <= iv <= 4.0:
                        rows.append({"date": d_, "ticker": name,
                                     "tenor_days": max(int(T * 365), 1), "iv": round(iv, 4),
                                     "kind": "atm", "source": "polygon_bs_inverted",
                                     "asof": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                if rows:
                    ING.upsert(rows)
                st["done_contracts"].setdefault("_pulled", {})[ct] = len(rows)
                _save(st)
        else:
            st["done_names"].append(name)
            _save(st)
            continue
        break   # budget hit inside the name loop
    return {"calls_this_run": calls, "calls_total": st["calls_spent"], "names_done": st["done_names"]}


def coverage():
    import importlib.util
    _spec = importlib.util.spec_from_file_location("ing", str(_ROOT / "scripts" / "_doc292_iv_ingest.py"))
    ING = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(ING)
    rows = ING._read_store()
    names = {}
    for r in rows:
        names.setdefault(r["ticker"], set()).add(r["date"])
    return {"names": len(names), "rows": len(rows),
            "per_name_days": {k: len(v) for k, v in sorted(names.items())}}


def backfill_prices(budget):
    """doc 294: re-pull aggs for contracts collected BEFORE price-storing landed; writes price rows only."""
    key = _key()
    st = _state()
    pulled = st["done_contracts"].get("_pulled", {})
    have = set()
    if PRICES.exists():
        have = {json.loads(l)["contract"] for l in PRICES.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [c for c in pulled if c not in have]
    names = sorted({_parse_occ(c)[0] for c in todo})
    spots = load_spots(names) if names else {}
    amap = contract_anchors(st)
    calls = 0
    for ct in todo:
        if calls >= budget:
            break
        root = _parse_occ(ct)[0]
        aggs = get(f"https://api.polygon.io/v2/aggs/ticker/{ct}/range/1/day/2024-09-01/2026-07-12?limit=50000", key)
        calls += 1; st["calls_spent"] += 1; _save(st)
        write_prices(_price_rows_from_aggs(aggs, ct, root, spots, anchor=amap.get(ct)))
    return {"contracts_backfilled": calls, "remaining": max(len(todo) - calls, 0)}


def contract_anchors(st=None):
    """contract ticker -> earliest anchor date whose selection produced it (doc 296 look-ahead rule)."""
    st = st or _state()
    amap = {}
    for ck, pair in st["done_contracts"].items():
        if ck == "_pulled" or not pair or ":" not in ck:
            continue
        a = ck.split(":", 1)[1]
        for side in ("call", "put"):
            c = pair.get(side)
            if c:
                amap[c] = min(a, amap[c]) if c in amap else a
    return amap


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--names", default=",".join(PRIORITY[:8]),
                    help="comma list, or 'auto' = the doc-295 greedy events-per-call priority list")
    ap.add_argument("--budget", type=int, default=300, help="max API calls this run (polite: ~1h per 280)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--backfill-prices", action="store_true",
                    help="doc 294: store option EOD prices for contracts pulled before price-storing existed")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(coverage(), indent=2))
        return 0
    if args.backfill_prices:
        print(json.dumps(backfill_prices(args.budget), indent=2))
        return 0
    if args.names.strip().lower() == "auto":
        # doc 295: greedy events-per-call priority (max SEVP-covered events first; Stage-3 rides along)
        pri = _ROOT / "data" / "research" / "doc294" / "collector_priority.json"
        names = json.loads(pri.read_text(encoding="utf-8")) if pri.exists() else PRIORITY
        res = collect(names, args.budget)
        print(json.dumps({**res, "coverage": coverage()}, indent=2))
        return 0
    res = collect([n.strip().upper() for n in args.names.split(",") if n.strip()], args.budget)
    print(json.dumps({**res, "coverage": coverage()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
