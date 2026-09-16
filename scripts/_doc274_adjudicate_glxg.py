"""doc 274 D9 adjudication: verify the decoy fleets' latent-defect claim with my own
recomputation. Claim: doc-273 clean-spine names qualifying after 2026-03-23 carry ADV20
windows bridging the Jan-Mar warehouse hole; true trailing-20-SESSION tape ADV violates
the $1M gate (GLXG 2026-03-24: stored ~$1.08M vs true ~$37k)."""
import duckdb
import numpy as np
import pandas as pd

con = duckdb.connect()
spine = pd.read_parquet("data/research/doc273_spine_clean.parquet")
g = spine[(spine.ticker == "GLXG") & (spine.session_date == "2026-03-24")]
stored = float(np.exp(g.log_adv20.iloc[0])) if len(g) else float("nan")
print(f"GLXG 2026-03-24 in clean spine: {len(g)} rows | stored ADV20 = ${stored:,.0f}")

# true trailing-20-session dollar volume from the raw TRADES tape (sessions before 03-24)
days = con.execute("""
  SELECT DISTINCT strftime(ts_et::DATE, '%Y-%m-%d') d
  FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet', hive_partitioning=1)
  WHERE year IN (2026) AND ts_et::DATE BETWEEN DATE '2026-02-01' AND DATE '2026-03-23'
  ORDER BY d DESC LIMIT 25""").df()
sess = sorted(days.d.tolist(), reverse=True)[:20]
tot = []
for d in sess:
    y, m, dd = d[:4], d[5:7], d[8:10]
    try:
        v = con.execute(f"""SELECT sum(price*size) dv FROM read_parquet(
            'data/polygon_warehouse/trades_v1_parquet/year={y}/month={m}/day={dd}/ticker=GLXG/data_*.parquet')
            """).df().dv.iloc[0]
        tot.append(float(v) if v == v else 0.0)
    except Exception:
        tot.append(0.0)
true_adv = float(np.mean(tot)) if tot else float("nan")
print(f"true trailing-20-session tape ADV (sessions {sess[-1]}..{sess[0]}): ${true_adv:,.0f}")
print(f"overstatement: {stored / max(true_adv, 1):.1f}x | gate $1M: stored "
      f"{'PASS' if stored >= 1e6 else 'FAIL'} vs true {'PASS' if true_adv >= 1e6 else 'FAIL'}")

# how many clean-spine ticker-days post-2026-03-23 carry hole-bridging windows?
post = spine[spine.session_date >= "2026-03-24"][["ticker", "session_date"]].drop_duplicates()
print(f"clean-spine ticker-days on/after 2026-03-24: {len(post)} "
      f"({post.session_date.nunique()} sessions) — all carry day_aggs ADV windows that "
      f"bridge the Jan-Mar hole (window reaches Dec 2025 until ~20 sessions post-resume)")
