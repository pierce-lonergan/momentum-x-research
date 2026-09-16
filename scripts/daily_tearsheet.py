#!/usr/bin/env python
"""D216: QuantStats Daily Tear Sheet

Generates an HTML report with professional risk/return analytics from
Alpaca trade history. Run after each trading session.

Usage:
    python scripts/daily_tearsheet.py                    # Full report
    python scripts/daily_tearsheet.py --days 7           # Last 7 days
    python scripts/daily_tearsheet.py --output report.html  # Custom output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    parser = argparse.ArgumentParser(description="D216: QuantStats Daily Tear Sheet")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--output", default="data/reports/tearsheet.html", help="Output HTML path")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    import requests

    h = {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }

    print("=" * 60)
    print("  D216: QUANTSTATS DAILY TEAR SHEET")
    print("=" * 60)

    # Get portfolio history from Alpaca
    start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    r = requests.get(
        "https://paper-api.alpaca.markets/v2/account/portfolio/history",
        headers=h,
        params={
            "period": f"{args.days}D",
            "timeframe": "1D",
            "intraday_reporting": "market_hours",
            "pnl_reset": "per_day",
        },
        timeout=15,
    )

    if r.status_code != 200:
        print(f"  Alpaca portfolio history error: {r.status_code}")
        print(f"  Response: {r.text[:200]}")
        return

    data = r.json()
    timestamps = data.get("timestamp", [])
    equity = data.get("equity", [])
    profit_loss = data.get("profit_loss", [])
    profit_loss_pct = data.get("profit_loss_pct", [])

    if not timestamps or not equity:
        print("  No portfolio history available.")
        print("  Need at least 2 trading days of data.")
        return

    print(f"\n  Data points: {len(timestamps)}")
    print(f"  Period: {datetime.fromtimestamp(timestamps[0]).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(timestamps[-1]).strftime('%Y-%m-%d')}")
    print(f"  Starting equity: ${equity[0]:,.2f}")
    print(f"  Current equity: ${equity[-1]:,.2f}")
    print(f"  Total P&L: ${equity[-1] - equity[0]:+,.2f}")

    # Build returns series
    import pandas as pd
    import numpy as np

    dates = pd.to_datetime([datetime.fromtimestamp(t) for t in timestamps])
    equity_series = pd.Series(equity, index=dates)
    returns = equity_series.pct_change().dropna()

    if len(returns) < 2:
        print("\n  Need at least 2 data points for analysis.")
        return

    # Generate QuantStats report
    try:
        import quantstats as qs

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Full HTML report
        qs.reports.html(
            returns,
            benchmark=None,
            output=str(output_path),
            title="MOMENTUM-X Paper Trading",
        )
        print(f"\n  HTML report saved: {output_path}")

        # Print key stats to terminal
        print(f"\n  KEY METRICS:")
        print(f"    Sharpe Ratio:  {qs.stats.sharpe(returns):.3f}")
        print(f"    Sortino Ratio: {qs.stats.sortino(returns):.3f}")
        print(f"    Max Drawdown:  {qs.stats.max_drawdown(returns):.2%}")
        print(f"    Win Rate:      {qs.stats.win_rate(returns):.2%}")
        print(f"    Avg Win:       {qs.stats.avg_win(returns):.4%}")
        print(f"    Avg Loss:      {qs.stats.avg_loss(returns):.4%}")
        print(f"    Profit Factor: {qs.stats.profit_factor(returns):.3f}")
        try:
            calmar = qs.stats.calmar(returns)
            print(f"    Calmar Ratio:  {float(calmar):.3f}" if not hasattr(calmar, '__len__') else f"    Calmar Ratio:  N/A (insufficient data)")
        except Exception:
            pass
        try:
            var = qs.stats.value_at_risk(returns)
            print(f"    Daily VaR:     {float(var):.4%}")
        except Exception:
            pass

    except Exception as e:
        print(f"\n  QuantStats error: {e}")
        print("  Falling back to basic stats...")

        # Basic stats without QuantStats
        total_return = (equity[-1] / equity[0]) - 1 if equity[0] > 0 else 0
        max_dd = 0
        peak = equity[0]
        for e in equity:
            peak = max(peak, e)
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)

        wins = sum(1 for r in returns if r > 0)
        losses = sum(1 for r in returns if r <= 0)
        avg_win = np.mean([r for r in returns if r > 0]) if wins > 0 else 0
        avg_loss = np.mean([r for r in returns if r <= 0]) if losses > 0 else 0

        print(f"    Total Return:  {total_return:.2%}")
        print(f"    Max Drawdown:  {max_dd:.2%}")
        print(f"    Win Rate:      {wins/(wins+losses):.2%}")
        print(f"    Avg Win:       {avg_win:.4%}")
        print(f"    Avg Loss:      {avg_loss:.4%}")

    # Also get trade-level stats from backfill
    backfill_path = _DATA / "backfill" / "trade_features_backfill.json"
    if backfill_path.exists():
        trades = json.loads(backfill_path.read_text())
        recovered = [t for t in trades if t.get("recovered")]
        if recovered:
            pnls = [t["pnl_pct"] for t in recovered]
            print(f"\n  TRADE-LEVEL STATS ({len(recovered)} trades):")
            print(f"    Avg P&L:     {np.mean(pnls)*100:+.2f}%")
            print(f"    Median P&L:  {np.median(pnls)*100:+.2f}%")
            print(f"    Best trade:  {max(pnls)*100:+.1f}%")
            print(f"    Worst trade: {min(pnls)*100:+.1f}%")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
