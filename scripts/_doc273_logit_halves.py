"""doc 273: split-half I(t) for the LOGIT family (the family that produced pooled S*).
The pre-registered split-half robustness check must use the S*-producing family."""
import importlib.util
import numpy as np

spec = importlib.util.spec_from_file_location("kf", "scripts/knowability_frontier_doc273.py")
kf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kf)

df = kf._load_spine()
fams = {"logit": kf._families()["logit"]}
for half, (a, b) in {"H1": ("2025-08-01", "2025-12-31"),
                     "H2": ("2026-01-01", "2026-04-30")}.items():
    sub = df[(df.session_date >= a) & (df.session_date <= b)]
    vals = {}
    for lbl in kf.GRID_LBL:
        s, X, y, fold = kf._per_t(sub, lbl)
        if len(s) < 200:
            vals[lbl] = float("nan")
            continue
        dll, _ = kf._dll_curve(X, y, None, fold, fams)
        vals[lbl] = dll["logit"]
    finite = {k: round(v, 4) for k, v in vals.items() if v == v}
    pos = {k: v for k, v in finite.items() if v > 0}
    print(f"{half} logit: max = {max(finite.values()):+.4f} | positive: {pos or 'none'}")
