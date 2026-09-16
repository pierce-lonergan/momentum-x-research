#!/usr/bin/env python
"""D215 P1: Slippage TCA (Transaction Cost Analysis)

Records signal price, NBBO at submit, fill price, and time-to-fill
for every trade. Run for 10+ trading days to measure actual slippage
before deciding whether TWAP is worth building.

Usage:
    python scripts/slippage_tca.py                # Analyze existing journal data
    python scripts/slippage_tca.py --live         # Start live recording (future)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    print("=" * 70)
    print("  D215: SLIPPAGE TCA (Transaction Cost Analysis)")
    print("=" * 70)

    # Load journal entries with fills
    journal_dir = _DATA / "journals"
    fills = []

    for jf in sorted(journal_dir.glob("journal_*.jsonl")):
        for line in jf.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                fp = d.get("fill_price")
                sp = d.get("entry_price") or d.get("signal_price")
                if fp and sp and isinstance(fp, (int, float)) and isinstance(sp, (int, float)):
                    if fp > 0 and sp > 0:
                        slippage_bps = (fp - sp) / sp * 10000
                        fills.append({
                            "ticker": d.get("ticker", "?"),
                            "date": d.get("timestamp", "?")[:10],
                            "signal_price": sp,
                            "fill_price": fp,
                            "slippage_bps": slippage_bps,
                            "qty": d.get("fill_qty", d.get("shares", "?")),
                        })
            except Exception:
                continue

    if not fills:
        print("\n  No filled trades found in journal data.")
        print("  The TCA module will begin collecting data on the next trading session.")
        print("  Run again after 10+ trading days / 30+ fills.")
        print("=" * 70)
        return

    slippages = [f["slippage_bps"] for f in fills]

    print(f"\n  FILLS ANALYZED: {len(fills)}")
    print(f"\n  SLIPPAGE DISTRIBUTION (bps):")
    print(f"    Mean:   {np.mean(slippages):+.1f} bps")
    print(f"    Median: {np.median(slippages):+.1f} bps")
    print(f"    Std:    {np.std(slippages):.1f} bps")
    print(f"    Min:    {min(slippages):+.1f} bps")
    print(f"    Max:    {max(slippages):+.1f} bps")
    print(f"    P25:    {np.percentile(slippages, 25):+.1f} bps")
    print(f"    P75:    {np.percentile(slippages, 75):+.1f} bps")

    avg_slip = abs(np.mean(slippages))
    print(f"\n  DECISION GATE:")
    if avg_slip < 5:
        print(f"    Avg slippage {avg_slip:.1f} bps < 5 bps -> SKIP TWAP (not worth building)")
    elif avg_slip < 15:
        print(f"    Avg slippage {avg_slip:.1f} bps (5-15 bps) -> BUILD simple limit router")
    else:
        print(f"    Avg slippage {avg_slip:.1f} bps > 15 bps -> BUILD adaptive router + evaluate IBKR")

    print(f"\n  TRADE DETAILS:")
    print(f"    {'Ticker':6s} {'Date':10s} {'Signal':>8s} {'Fill':>8s} {'Slip(bps)':>10s}")
    for f in fills[:20]:
        print(f"    {f['ticker']:6s} {f['date']:10s} ${f['signal_price']:>7.2f} ${f['fill_price']:>7.2f} {f['slippage_bps']:>+9.1f}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
