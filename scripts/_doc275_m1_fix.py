"""doc 275: corrected M1 config-lie mutant (the first run's theta mutation was defeated by
def-time default-arg binding -> invalid cell, adjudicated; injection now explicit)."""
import json, sys
sys.path.insert(0, "scripts")
import gate_doc275 as g

kf = g.kf
import pandas as pd

spine = pd.read_parquet(g.SPINE_273CLEAN)
spine = spine[spine.t_lbl.isin(g.GRID5)].reset_index(drop=True)
if "day" not in spine.columns:
    spine = spine.assign(day=spine.session_date)
fams = kf._families()

lied = {}
for lbl in g.GRID5[:2]:
    s, X, y, fold = kf._per_t(spine, lbl, theta=0.005)     # explicit: the lie takes effect
    dll, _ = kf._dll_curve(X, y, None, fold, fams)
    lied[lbl] = dll

res = g.d_repro(g.SPINE_273CLEAN, list(kf.FEATURES), lied, tol=1e-6)
print("M1_config_lie (corrected): fired =", res["fired"],
      "| mismatches =", len(res["mismatches"]), "| expected fired=True")
out = json.load(open("data/research/doc275/drepro_heldout.json"))
out["M1_config_lie"] = res
out["M1_note"] = ("first construction was a BLANK (theta default-arg binding defeated the "
                  "mutation; adjudicated, injection made explicit)")
json.dump(out, open("data/research/doc275/drepro_heldout.json", "w"), indent=1, default=float)
