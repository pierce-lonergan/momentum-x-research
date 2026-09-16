"""D313 (doc 172) — 90-day historical unhedge-window audit.

Walks the last N days of broker activities + orders, infers per-position
lifetimes (BUY fill → next SELL fill), then for each lifetime asks:
during this window, was a protective sell-stop actually active at the
broker? Output: histogram of unhedge durations + JSON ledger.

Tells us:
  - How often the dead callback bit us before doc 171 shipped L2
  - How to size the L2 tolerance (60s = conservative? aggressive?)
  - Whether 0.5x sizing is conservative enough for T2's wide arm
  - Which tickers had the longest unhedge windows (NXXT-class outliers)
"""
from __future__ import annotations
import os, json, urllib.request, sys, statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path


def main(window_days: int = 90, gap_threshold_sec: int = 30) -> int:
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    secrets_file = os.path.expanduser('~/momentum-x-secrets.env')
    for line in open(secrets_file, encoding='utf-8'):
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            os.environ[k.strip()] = v.strip()
    key = os.environ.get('ALPACA_API_KEY')
    sec = os.environ.get('ALPACA_SECRET_KEY')
    hdr = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': sec}

    after = (datetime.now(timezone.utc) - timedelta(days=window_days)
             ).isoformat().replace('+00:00', 'Z')
    print(f'=== Pulling {window_days} days of broker data from {after[:10]} ===')

    # Fills -- activities endpoint has different pagination semantics.
    # Use `page_token` cursor to walk all pages until empty.
    acts: list[dict] = []
    page_token = None
    while True:
        url = (f'https://paper-api.alpaca.markets/v2/account/activities'
               f'?activity_types=FILL&after={after}&direction=asc')
        if page_token:
            url += f'&page_token={page_token}'
        page = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=hdr), timeout=30).read())
        if not page:
            break
        acts.extend(page)
        if len(page) < 100:  # default page size = 100
            break
        page_token = page[-1].get('id')
        if not page_token or len(acts) > 5000:
            break  # safety bound
    print(f'  fills: {len(acts)}')

    # All orders (paginated isn't supported here; 500 limit)
    url = (f'https://paper-api.alpaca.markets/v2/orders?status=all'
           f'&after={after}&limit=500&direction=asc')
    orders = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=hdr), timeout=30).read())
    print(f'  orders: {len(orders)}')

    # Group fills + stops by ticker
    ticker_fills = defaultdict(list)
    for a in acts:
        ticker_fills[a['symbol']].append({
            'side': a['side'], 'qty': float(a['qty']),
            'price': float(a['price']),
            'ts': datetime.fromisoformat(a['transaction_time'].replace('Z', '+00:00')),
        })

    ticker_stops = defaultdict(list)
    for o in orders:
        if (o.get('type') or '').lower() not in (
                'stop', 'stop_limit', 'trailing_stop'):
            continue
        sym = (o.get('symbol') or '').upper()
        side = (o.get('side') or '').lower()
        submitted = datetime.fromisoformat(o['submitted_at'].replace('Z', '+00:00'))
        terminated = (o.get('filled_at') or o.get('canceled_at')
                      or o.get('expired_at') or o.get('replaced_at'))
        end = (datetime.fromisoformat(terminated.replace('Z', '+00:00'))
               if terminated else datetime.now(timezone.utc))
        ticker_stops[sym].append({
            'side': side, 'submitted': submitted, 'end': end,
            'status': o.get('status'),
        })

    # Walk each round trip + check stop coverage
    windows = []
    for ticker, fills in ticker_fills.items():
        sorted_fills = sorted(fills, key=lambda x: x['ts'])
        buys = [f for f in sorted_fills if f['side'] == 'buy']
        sells = [f for f in sorted_fills if f['side'].startswith('sell')]
        for buy in buys:
            next_sells = [s for s in sells if s['ts'] > buy['ts']]
            if not next_sells:
                continue
            sell = next_sells[0]
            stops = [s for s in ticker_stops[ticker] if s['side'] == 'sell']
            # Sample every 30s through the position lifetime
            t = buy['ts']
            gap_start = None
            while t < sell['ts']:
                covered = any(s['submitted'] <= t <= s['end'] for s in stops)
                if not covered and gap_start is None:
                    gap_start = t
                elif covered and gap_start is not None:
                    gap_sec = (t - gap_start).total_seconds()
                    if gap_sec >= gap_threshold_sec:
                        windows.append({
                            'ticker': ticker, 'start': gap_start, 'end': t,
                            'duration_sec': gap_sec, 'qty': buy['qty'],
                            'entry_price': buy['price'],
                        })
                    gap_start = None
                t += timedelta(seconds=30)
            if gap_start is not None:
                gap_sec = (sell['ts'] - gap_start).total_seconds()
                if gap_sec >= gap_threshold_sec:
                    windows.append({
                        'ticker': ticker, 'start': gap_start,
                        'end': sell['ts'], 'duration_sec': gap_sec,
                        'qty': buy['qty'], 'entry_price': buy['price'],
                    })

    print()
    print(f'=== {len(windows)} unhedge windows >={gap_threshold_sec}s ===\n')
    if not windows:
        print('  (no windows found)')
        return 0

    durs = [w['duration_sec'] for w in windows]
    print('  duration distribution:')
    print(f'    min:     {min(durs):>10.0f}s  ({min(durs)/60:.1f}min)')
    if len(durs) >= 4:
        q = statistics.quantiles(durs, n=4)
        print(f'    p25:     {q[0]:>10.0f}s  ({q[0]/60:.1f}min)')
        print(f'    median:  {statistics.median(durs):>10.0f}s  ({statistics.median(durs)/60:.1f}min)')
        print(f'    p75:     {q[2]:>10.0f}s  ({q[2]/60:.1f}min)')
    print(f'    max:     {max(durs):>10.0f}s  ({max(durs)/3600:.1f}h)')
    print()

    buckets = {'30s-60s': 0, '60s-5min': 0, '5min-1h': 0, '1h-24h': 0, '>24h': 0}
    for d in durs:
        if d < 60:    buckets['30s-60s'] += 1
        elif d < 300: buckets['60s-5min'] += 1
        elif d < 3600: buckets['5min-1h'] += 1
        elif d < 86400: buckets['1h-24h'] += 1
        else:           buckets['>24h'] += 1
    print('  bucket histogram:')
    for k, v in buckets.items():
        print(f'    {k:<10} {v:>5}  {"#" * min(50, v)}')
    print()

    print('  top 15 longest unhedge windows:')
    for w in sorted(windows, key=lambda x: -x['duration_sec'])[:15]:
        h = w['duration_sec'] / 3600
        start_local = w['start'].astimezone()
        print(f'    {w["ticker"]:<7} {start_local.strftime("%m-%d %H:%M"):<14} '
              f'dur={h:>6.2f}h  qty={int(w["qty"]):>6} '
              f'entry=${w["entry_price"]:.4f}')

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    out = _PROJECT_ROOT / 'data' / 'shadow_stops' / 'historical_unhedge_audit_90d.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'audit_date': datetime.now(timezone.utc).isoformat(),
            'window_days': window_days,
            'gap_threshold_sec': gap_threshold_sec,
            'n_fills': len(acts),
            'n_orders': len(orders),
            'n_unhedge_windows': len(windows),
            'duration_stats': {
                'min_sec': min(durs), 'max_sec': max(durs),
                'median_sec': statistics.median(durs),
                'mean_sec': statistics.mean(durs),
            },
            'bucket_histogram': buckets,
            'unhedge_windows': [{
                'ticker': w['ticker'],
                'start': w['start'].isoformat(),
                'end': w['end'].isoformat(),
                'duration_sec': w['duration_sec'],
                'qty': w['qty'], 'entry_price': w['entry_price'],
            } for w in sorted(windows, key=lambda x: -x['duration_sec'])],
        }, f, indent=2, default=str)
    print(f'\nWrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
