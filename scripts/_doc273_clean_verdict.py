"""doc 273: formal verdict numbers on the CLEAN universe (exact exceedances, per-t view)."""
import json
import pandas as pd
import numpy as np

res = json.load(open("data/research/doc273_results_clean.json"))
nd = pd.read_parquet("data/research/doc273_null_clean.parquet")
s_real = {lbl: float(np.nanmax(list(v.values()))) for lbl, v in res["I"].items()}
s_star = max(s_real.values())
exc = int((nd.S_star >= s_star).sum())
p = (1 + exc) / (len(nd) + 1)
print(f"clean real S* = {s_star:+.5f} at {max(s_real, key=s_real.get)}")
print(f"clean mirrored null: median {nd.S_star.median():+.5f}  p95 {nd.S_star.quantile(.95):+.5f} "
      f"p99 {nd.S_star.quantile(.99):+.5f}  max {nd.S_star.max():+.5f}")
print(f"exceedances {exc}/{len(nd)}  ->  formal p = {p:.4f}")
print("\nper-t real max-fam vs per-t null p95:")
for lbl in res["grid"]:
    col = nd[lbl].dropna()
    print(f"  {lbl:>6}: real {s_real[lbl]:+.5f} | null p95 {col.quantile(.95):+.5f} "
          f"| exceed {int((col >= s_real[lbl]).sum())}/{len(col)}")
