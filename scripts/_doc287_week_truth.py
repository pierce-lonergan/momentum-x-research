"""doc 287 (read-only): authoritative week-1 (2026-07-06..07-10) account truth straight from the broker.
Prints portfolio-history daily equity deltas + this week's realized fills. Keys never printed."""
from __future__ import annotations
import json, os, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _keys():
    k = s = None
    base = "https://paper-api.alpaca.markets"
    for f in [os.path.expanduser("~/momentum-x-secrets.env"), str(ROOT / ".env")]:
        if os.path.exists(f):
            for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("ALPACA_API_KEY="): k = k or line.split("=", 1)[1].strip().strip('"\'')
                if line.startswith("ALPACA_SECRET_KEY="): s = s or line.split("=", 1)[1].strip().strip('"\'')
                if line.startswith("ALPACA_BASE_URL="): base = line.split("=", 1)[1].strip().strip('"\'') or base
        if k and s: break
    return k, s, base.rstrip("/")


def _get(path, params=None):
    k, s, base = _keys()
    u = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(u, headers={"APCA-API-KEY-ID": k or "", "APCA-API-SECRET-KEY": s or "",
                                             "User-Agent": "mx-doc287"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    acct = _get("/v2/account")
    print(f"NOW: equity=${float(acct['equity']):,.2f} cash=${float(acct['cash']):,.2f} "
          f"status={acct['status']} positions_held={acct.get('position_market_value')}")
    # portfolio history, 1D resolution, last ~2 weeks
    ph = _get("/v2/account/portfolio/history", {"period": "2W", "timeframe": "1D", "extended_hours": "true"})
    ts, eq, pl, plpct = ph["timestamp"], ph["equity"], ph["profit_loss"], ph["profit_loss_pct"]
    import datetime as dt
    print("\nDATE (ET)     equity        Δ$         Δ%")
    for i in range(len(ts)):
        d = dt.datetime.utcfromtimestamp(ts[i]) - dt.timedelta(hours=4)  # ET label
        e = eq[i] if eq[i] is not None else 0
        p = pl[i] if pl[i] is not None else 0
        pc = (plpct[i] or 0) * 100
        print(f"{d:%Y-%m-%d %a}  ${e:>11,.2f}  {p:>+9,.2f}  {pc:>+6.3f}%")
    # this-week fills
    print("\n=== FILLS 2026-07-06..07-11 (activity=FILL) ===")
    acts = _get("/v2/account/activities/FILL", {"after": "2026-07-06", "until": "2026-07-12", "direction": "asc"})
    from collections import defaultdict
    bysym = defaultdict(lambda: {"buy_qty": 0.0, "buy_notional": 0.0, "sell_qty": 0.0, "sell_notional": 0.0, "days": set()})
    for a in acts:
        sym = a["symbol"]; qty = float(a["qty"]); price = float(a["price"])
        side = a["side"]; day = a["transaction_time"][:10]
        r = bysym[sym]; r["days"].add(day)
        if side.startswith("buy"): r["buy_qty"] += qty; r["buy_notional"] += qty * price
        else: r["sell_qty"] += qty; r["sell_notional"] += qty * price
    print(f"{'SYM':6} {'buyQ':>8} {'sellQ':>8} {'realized$ (sell-buy matched)':>28}  days")
    tot = 0.0
    for sym, r in sorted(bysym.items()):
        mq = min(r["buy_qty"], r["sell_qty"])
        avgb = r["buy_notional"] / r["buy_qty"] if r["buy_qty"] else 0
        avgs = r["sell_notional"] / r["sell_qty"] if r["sell_qty"] else 0
        realized = (avgs - avgb) * mq
        tot += realized
        carry = r["buy_qty"] - r["sell_qty"]
        flag = f"  CARRY {carry:+.0f}sh" if abs(carry) > 1e-6 else ""
        print(f"{sym:6} {r['buy_qty']:>8.0f} {r['sell_qty']:>8.0f} {realized:>28,.2f}  {sorted(r['days'])}{flag}")
    print(f"\nSUM matched-realized (approx, avg-price basis): ${tot:,.2f}")


if __name__ == "__main__":
    main()
