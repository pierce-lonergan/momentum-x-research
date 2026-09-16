import json
for n in ("P1_ADVHOLE", "P1_HOLEREPLAY", "P2_ENTRYVIS"):
    r = json.load(open(f"data/research/doc274_cells/cell_{n}.json"))
    prov = r.get("provenance", {})
    print(f"=== {n}: S*={r['S_star']:+.4f} p={r['null_p']:.3f} margin={r['margin']:+.4f} at {r['S_star_at']}")
    print("   prov n_admitted_td:", prov.get("n_admitted_td"), "rows:", prov.get("n_admitted_rows"),
          "days:", (prov.get("admitted_days") or [])[:5])
    for d in ("D2_overlay", "D2_truth"):
        if d in r:
            print(f"   {d}:", {k: v for k, v in r[d].items() if not isinstance(v, dict)})
    if "D3" in r:
        print("   D3 share", round(r["D3"]["share"], 3), r["D3"].get("day"), "fired", r["D3"]["fired"])
    if "D6" in r:
        print("   D6 rec", round(r["D6"]["recovery_median"], 4), "thr",
              round(r["D6"]["null_thr"], 4), "fired", r["D6"]["fired"])
    if "D5" in r:
        print("   D5 H1", r["D5"].get("H1"), "H2", r["D5"].get("H2"), "fired", r["D5"]["fired"])
    if "D10" in r:
        print("   D10 fired", r["D10"]["fired"], "h2max", round(r["D10"]["h2_max"], 3))
    if "potency" in r:
        print("   potency max", round(r["potency"]["max"], 4), r.get("potency_note", ""))
