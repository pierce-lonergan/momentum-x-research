"""Quick connectivity + screener smoke test for the lottery runner."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from lottery_runner import AlpacaClient, build_watchlist, load_recent_tickers, FRESHNESS_LOOKBACK_DAYS


async def main():
    client = AlpacaClient()
    try:
        a = await client.get_account()
        print(f"ACCOUNT OK: {a.get('account_number')} status={a.get('status')} "
              f"equity=${float(a.get('equity', 0)):.2f} cash=${float(a.get('cash', 0)):.2f}")
        clock = await client.get_clock()
        print(f"CLOCK: is_open={clock.get('is_open')} timestamp={clock.get('timestamp')} "
              f"next_open={clock.get('next_open')} next_close={clock.get('next_close')}")
        m = await client.get_movers(limit=15)
        gainers = m.get("gainers", [])
        print(f"\nSCREENER MOVERS: {len(gainers)} gainers returned")
        for g in gainers[:10]:
            print(f"  {g.get('symbol'):6s} ${float(g.get('price', 0)):>8.2f}  "
                  f"+{float(g.get('percent_change', 0)):>5.1f}%")

        recent = load_recent_tickers(FRESHNESS_LOOKBACK_DAYS)
        print(f"\nFRESHNESS CORPUS: {len(recent)} tickers in bar_recordings (last {FRESHNESS_LOOKBACK_DAYS}d)")

        print("\n=== build_watchlist() output ===")
        picks = await build_watchlist(client)
        for p in picks:
            print(f"  PICK {p.ticker:6s} ${p.price:>7.2f} +{p.pct_change:>5.1f}% fresh={p.is_first_appearance}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
