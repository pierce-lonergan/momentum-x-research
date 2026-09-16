import glob, json, sys

for f in sorted(glob.glob("data/research/doc275/residue_*.json")):
    r = json.load(open(f))
    print(f"=== {r['arm']} ===")
    for n, c in r["curve"].items():
        if "K" not in c:
            print(f"  N={n}: {c}")
            continue
        print(f"  N={n:>4}: K={c['K']} real_max={c['real_max']:.2f} thr={c['threshold']:.2f} "
              f"fired={c['fired']} p={c['p']:.3f} naive={c['naive']} "
              f"eligible={c['eligible']} gated={c['n_gated_discoveries']}")
    cn = r.get("canary", {})
    print("  canary:", {k: (round(v, 5) if isinstance(v, float) else v) for k, v in cn.items()})

for f in sorted(glob.glob("data/research/doc275/truth_*.json")):
    r = json.load(open(f))
    print(f"=== truth {f}: T={r['T']} FWER={r['fwer']:.3f} ===")
