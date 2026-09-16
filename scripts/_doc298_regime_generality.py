"""DOC 298 / trial T00031 — does the gapper universe's negative expectancy hold outside the bull regime?

Prereg frozen and hashed BEFORE execution (sha256 4174451d..., registered as trial T00031 in
scripts/trial_registry.py). Runs exactly what was frozen: no variant selection, no sub-slicing beyond
the pre-declared regime split.

Doc 297's load-bearing verdict — the bot's own long universe loses 2.041%/ticket — was computed
entirely inside 2024-01..2026-07, one bull regime. This asks the same question of 2016-2023, which
contains the 2018 Q4 selloff, the 2020 COVID crash and the 2022 bear.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "polygon_warehouse" / "daily_2016"
OUT = _ROOT / "data" / "research" / "doc298"

GAP_MIN = 0.10
PX_LO, PX_HI = 0.50, 5.00
ADV_MIN = 1_000_000
SEED = 298


def load() -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in sorted(STORE.glob("daily_*.parquet"))]
    df = pd.concat(frames, ignore_index=True)
    df = df[(df.o > 0) & (df.c > 0) & (df.v > 0)].copy()
    df.sort_values(["ticker", "d"], inplace=True)
    g = df.groupby("ticker", sort=False)
    df["prev_close"] = g.c.shift(1)
    df["dollar_vol"] = df.c * df.v
    df["adv20"] = g.dollar_vol.transform(lambda s: s.rolling(20, min_periods=10).mean()).groupby(
        df.ticker).shift(1)
    df["gap"] = df.o / df.prev_close - 1.0
    df["oc_ret"] = df.c / df.o - 1.0
    return df


def regime_labels(df: pd.DataFrame) -> dict[str, str]:
    """BEAR if SPY closed >10% below its trailing 252-session max. Defined from SPY only."""
    spy = df[df.ticker == "SPY"][["d", "c"]].drop_duplicates("d").sort_values("d")
    if spy.empty:
        raise SystemExit("SPY missing from the warehouse — regime labels cannot be built")
    peak = spy.c.rolling(252, min_periods=60).max()
    dd = spy.c / peak - 1.0
    return {d: ("BEAR" if x < -0.10 else "BULL") for d, x in zip(spy.d, dd.fillna(0.0))}


def day_blocked_ci(vals: np.ndarray, days: np.ndarray, n_boot: int = 5000, seed: int = SEED):
    rng = np.random.default_rng(seed)
    by = {}
    for v, d in zip(vals, days):
        by.setdefault(d, []).append(v)
    keys = list(by)
    arrs = [np.asarray(by[k]) for k in keys]
    if len(keys) < 3:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(arrs), len(arrs))
        means.append(np.concatenate([arrs[i] for i in pick]).mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def report(sub: pd.DataFrame, label: str) -> dict:
    if len(sub) < 30:
        return {"label": label, "n": int(len(sub)), "REFUSED": "n<30"}
    v = sub.oc_ret.values
    lo, hi = day_blocked_ci(v, sub.d.values)
    sess = sub.groupby("d").oc_ret.mean()
    return {
        "label": label, "n_ticker_days": int(len(sub)), "n_sessions": int(sub.d.nunique()),
        "mean_pct": round(100 * float(v.mean()), 4),
        "median_pct": round(100 * float(np.median(v)), 4),
        "ci95_day_blocked_pct": [round(100 * lo, 4), round(100 * hi, 4)],
        "pct_sessions_negative": round(100 * float((sess < 0).mean()), 2),
        "excludes_zero": bool(hi < 0 or lo > 0),
        "sign": "negative" if v.mean() < 0 else "positive",
    }


def main() -> int:
    df = load()
    reg = regime_labels(df)
    uni = df[(df.gap >= GAP_MIN) & (df.prev_close.between(PX_LO, PX_HI)) & (df.adv20 > ADV_MIN)].copy()
    uni["year"] = uni.d.str[:4]
    uni["regime"] = uni.d.map(reg)
    uni = uni[uni.regime.notna()]

    res = {"trial_id": "T00031", "prereg_sha256": "4174451d9d39c8f9afda5da6e396d4e9b040564aea599ae6b3d0618c49be4fab",
           "universe": {"gap_min": GAP_MIN, "prev_close": [PX_LO, PX_HI], "adv_min": ADV_MIN},
           "window": [str(uni.d.min()), str(uni.d.max())],
           "POOLED": report(uni, "2016-2023 pooled"),
           "BY_REGIME": {r: report(uni[uni.regime == r], r) for r in ("BULL", "BEAR")},
           "BY_YEAR": {y: report(uni[uni.year == y], y) for y in sorted(uni.year.unique())}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "T00031_regime_generality.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("window", "POOLED", "BY_REGIME")}, indent=2))
    print("\nBY YEAR:")
    for y, r in res["BY_YEAR"].items():
        if "REFUSED" in r:
            print(f"  {y}: {r['REFUSED']} (n={r['n']})")
        else:
            print(f"  {y}: mean {r['mean_pct']:+7.3f}%  CI [{r['ci95_day_blocked_pct'][0]:+7.3f},"
                  f" {r['ci95_day_blocked_pct'][1]:+7.3f}]  n={r['n_ticker_days']:5d}"
                  f"  excl0={r['excludes_zero']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
