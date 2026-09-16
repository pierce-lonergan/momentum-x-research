"""Cross-strategy capital allocator with Ising regime weighting.

Updated session 109 to use the unified meta-scorer tier system:
  L:      Long lottery (chronic-fader-gate filter)              +1.43%/trade
  S:      Chronic-fader short (S3 variant)                      +5.55%/trade
  H:      H3 short morning ripper                               +1.6%/trade (disabled)
  BROAD:  v3-tuned P>=0.30 + (HI|MID)-mag                       +5.50% n=386 WF
  VETOED: v3-tuned P>=0.30 + MID-mag + TCN<0.30                 +10.71% n=67 WF
  HIGH:   v3-tuned P>=0.50 + (HI|MID)-mag                       +13.36% n=17 WF
  ELITE:  v3-tuned P>=0.60 + (HI|MID)-mag                       +60.58% n=7 WF (85.7% win)

Per session 109 unified meta-scorer + Kelly:
  $10k bankroll over 16-month WF: +$5,476 (+54.76%, ~41% APY)
  Source: scripts/ml_meta_scorer.py + data/polygon_warehouse/derived/meta_scores_walkforward.parquet

This allocator:
  1. Reads today's Ising regime indicators (mag + breadth from yesterday)
  2. Outputs recommended capital allocation per strategy (5 strategies)
  3. Computes per-strategy notional caps for the day
  4. Writes to data/strategy_state/allocation_{date}.json for runners

USAGE:
    python scripts/strategy_capital_allocator.py --equity 140000

  Output (example):
    {
      "date": "2026-05-04",
      "regime": {"mag": -0.03, "breadth": 13, "regime_label": "MID_MID"},
      "allocations": {
        "lottery_long":  {"weight": 0.40, "notional_per_pick": 250, "max_picks": 5},
        "fader_short":   {"weight": 0.40, "notional_per_pick": 250, "max_picks": 5},
        "h3_short":      {"weight": 0.20, "notional_per_pick": 500, "max_picks": 1}
      },
      "total_deployed_cap_usd": 4500.0
    }

  Then runners read this file:
    lottery_runner.py:  uses lottery_long.notional_per_pick + max_picks
    fader_short_runner.py: uses fader_short.notional_per_pick + max_picks
    (h3_runner pending PROMPT_10 §7.5 helper variant)
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = REPO / "data" / "strategy_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
ET = ZoneInfo("America/New_York")


def get_latest_ising() -> dict:
    """Load most recent Ising magnetization + breadth from the parquet."""
    p = REPO / "data" / "polygon_warehouse" / "derived" / "ising_daily.parquet"
    if not p.exists():
        return {"mag": 0, "mag_5d": 0, "breadth": 0, "available": False,
                "note": "ising_daily.parquet missing"}
    con = duckdb.connect()
    rows = con.sql(f"""
        WITH last5 AS (
            SELECT d, magnetization, n_huge_up
            FROM read_parquet('{p.as_posix()}')
            ORDER BY d DESC LIMIT 5
        )
        SELECT
          ANY_VALUE(d) FILTER (WHERE rn = 1) AS d,
          ANY_VALUE(magnetization) FILTER (WHERE rn = 1) AS mag,
          ANY_VALUE(n_huge_up) FILTER (WHERE rn = 1) AS breadth,
          AVG(magnetization) AS mag_5d
        FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY d DESC) AS rn FROM last5)
    """).fetchone()
    return {"d": str(rows[0]), "mag": float(rows[1]),
            "breadth": float(rows[2]), "mag_5d": float(rows[3]),
            "available": True}


def classify_regime(mag_5d: float, breadth: float) -> str:
    """Classify into Ising regime per doc 100 §2.

    Magnetization terciles: lo<-0.05, hi>+0.05
    Breadth terciles: lo<10, hi>15
    """
    mag_label = "MID"
    if mag_5d < -0.05: mag_label = "LO"
    elif mag_5d > 0.05: mag_label = "HI"
    breadth_label = "MID"
    if breadth < 10: breadth_label = "LO"
    elif breadth > 15: breadth_label = "HI"
    return f"{mag_label}_{breadth_label}"


def allocate(regime: str, equity: float) -> dict:
    """Compute per-strategy allocation given regime + equity.

    Per doc 100 + doc 106:
      - L  (lottery long):       always on, modest size (+1.43% per trade WF)
      - S  (fader short):        always on, larger size (+5.55% per trade S3)
      - H  (h3 short):           MID_MID full size; otherwise reduce or pause
      - MR (ML regime-gated):    ON only in HI/MID mag regimes (+10.00% n=212)
      - MC (ML high-conviction): ON only in HI/MID mag regimes (+37.48% n=10);
                                 max 1-2 picks/day (rare signal)

    Total deployment capped at ~7% of equity per day (was 5% pre-session-106
    — tighter regime gating means we can deploy more inside qualified buckets).
    """
    # Base caps as fraction of equity (SESSION 109 META-SCORER TIERS)
    L_base = 0.010    # 1.0% lottery long
    S_base = 0.010    # 1.0% fader short
    H_base = 0.0      # h3 disabled
    MR_base = 0.020   # ml_regime_gated (BROAD tier proxy)
    MC_base = 0.010   # ml_high_conviction (HIGH tier proxy)
    # Session 109: per-pick Kelly caps now driven by meta-scorer tier
    # (see scripts/ml_meta_scorer.py KELLY_CAPS dict).
    # ELITE  cap = 5%   ($500/$10k bankroll) - +60.58%/trade WF
    # HIGH   cap = 3%   ($300/$10k)          - +13.36%/trade WF
    # VETOED cap = 2%   ($200/$10k)          - +10.71%/trade WF
    # BROAD  cap = 1%   ($100/$10k)          - +5.50%/trade WF

    # ── Regime modulations (per doc 100 §2 + doc 106 §3) ──
    mag_label, breadth_label = (regime.split("_") + ["MID"])[:2]

    # H3: MID×MID is sweet-spot
    h3_mult = 1.0 if regime == "MID_MID" else (0.5 if regime in ("MID_LO", "MID_HI", "LO_MID", "HI_MID") else 0.25)

    # Lottery long: slightly underweight in extreme HI (overheated tape)
    l_mult = 1.0 if regime not in ("HI_HI",) else 0.5

    # Fader short: bigger when there's pump activity (MID/HI breadth) since more candidates
    s_mult = 1.0 if regime not in ("LO_LO", "HI_LO") else 0.7

    # ML regime-gated: HI-mag is best (+10.00%); MID-mag also strong (+9.30%);
    # LO-mag underperforms (+1-2%) so KILL switch.
    if mag_label == "HI":
        mr_mult = 1.0
    elif mag_label == "MID":
        mr_mult = 0.8
    else:  # LO
        mr_mult = 0.0   # gate OFF

    # ML high-conviction: even more aggressive HI-mag preference (+37.48%);
    # MID-mag still solid (+24.59%); LO is starvation territory (gate OFF).
    if mag_label == "HI":
        mc_mult = 1.0
    elif mag_label == "MID":
        mc_mult = 0.8
    else:  # LO
        mc_mult = 0.0

    L_cap  = equity * L_base  * l_mult
    S_cap  = equity * S_base  * s_mult
    H_cap  = equity * H_base  * h3_mult
    MR_cap = equity * MR_base * mr_mult
    MC_cap = equity * MC_base * mc_mult

    return {
        "lottery_long": {
            "weight": l_mult * L_base,
            "notional_per_pick": 250.0,
            "max_picks": int(L_cap / 250.0),
            "regime_modulation": l_mult,
        },
        "fader_short": {
            "weight": s_mult * S_base,
            "notional_per_pick": 250.0,
            "max_picks": int(S_cap / 250.0),
            "regime_modulation": s_mult,
        },
        "h3_short": {
            "weight": h3_mult * H_base,
            "notional_per_pick": 500.0,
            "max_picks": int(H_cap / 500.0),
            "regime_modulation": h3_mult,
            "active": h3_mult > 0 and H_base > 0,
            "note": "h3 disabled until PROMPT_10 §7.5 short-side helper variant ships",
        },
        "ml_regime_gated": {
            "weight": mr_mult * MR_base,
            "notional_per_pick": 350.0,   # ~1.4x lottery (validated +10% per trade)
            "max_picks": int(MR_cap / 350.0),
            "regime_modulation": mr_mult,
            "active": mr_mult > 0,
            "note": "v2 default model + Ising HI/MID mag gate (LO-mag = OFF)",
            "gate_env": {
                "LOTTERY_USE_ISING_GATE": "1",
                "LOTTERY_ISING_TERCILE": "HI" if mag_label == "HI" else "MID",
            },
        },
        "ml_high_conviction": {
            "weight": mc_mult * MC_base,
            "notional_per_pick": 700.0,   # 2x ml_regime_gated (rare/concentrated)
            "max_picks": max(1, int(MC_cap / 700.0)) if mc_mult > 0 else 0,
            "regime_modulation": mc_mult,
            "active": mc_mult > 0,
            "note": "v2-tuned model, P>=0.50 only; HI-mag delivers +37.48%/trade WF (n=10)",
            "model": "continuer_v2_tuned.pkl",
            "min_proba": 0.50,
        },
        "_total_deployed_cap_usd": L_cap + S_cap + H_cap + MR_cap + MC_cap,
        "_pct_of_equity": (L_cap + S_cap + H_cap + MR_cap + MC_cap) / equity * 100,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", type=float, default=140000.0,
                         help="Total account equity for sizing")
    parser.add_argument("--print-only", action="store_true",
                         help="Print allocation but don't write state file")
    args = parser.parse_args()

    today = datetime.now(ET).strftime("%Y-%m-%d")

    print("=" * 72)
    print(f"CAPITAL ALLOCATOR  date={today}  equity=${args.equity:,.2f}")
    print("=" * 72)

    ising = get_latest_ising()
    if ising.get("available"):
        regime = classify_regime(ising["mag_5d"], ising["breadth"])
        print(f"\nIsing regime indicators (latest = {ising['d']}):")
        print(f"  mag       = {ising['mag']:+.4f}")
        print(f"  mag_5d    = {ising['mag_5d']:+.4f}  (lo<-0.05, hi>+0.05)")
        print(f"  breadth   = {ising['breadth']:.0f}      (lo<10, hi>15)")
        print(f"  REGIME    = {regime}")
    else:
        regime = "MID_MID"
        print(f"\nWARNING: ising data unavailable ({ising.get('note')}); defaulting to MID_MID")

    alloc = allocate(regime, args.equity)
    print("\nAllocation:")
    for s_name, s_data in alloc.items():
        if s_name.startswith("_"): continue
        print(f"  {s_name}:")
        for k, v in s_data.items():
            if isinstance(v, float):
                print(f"    {k:25s} {v:>10.4f}")
            else:
                print(f"    {k:25s} {v}")
    print(f"\nTotal deployed cap: ${alloc['_total_deployed_cap_usd']:,.2f}  "
          f"({alloc['_pct_of_equity']:.2f}% of equity)")

    out = {
        "date": today,
        "equity": args.equity,
        "regime_indicators": ising,
        "regime_label": regime,
        "allocations": alloc,
    }
    if not args.print_only:
        path = STATE_DIR / f"allocation_{today}.json"
        path.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
