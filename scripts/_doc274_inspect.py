import json
for name in ("CLEAN_A", "SPECIMEN", "P3_MIX03", "P4_SURV", "P5_DRIFT"):
    r = json.load(open(f"data/research/doc274_cells/cell_{name}.json"))
    print(f"=== {name}: S*={r['S_star']:+.4f} p={r['null_p']:.3f} margin={r['margin']:+.4f} at {r['S_star_at']}")
    if "D3" in r:
        print("  D3 share", round(r["D3"]["share"], 3), "day", r["D3"].get("day"),
              "total", round(r["D3"].get("total", 0), 3), "fired", r["D3"]["fired"])
    if "D6" in r:
        print("  D6 recovery", round(r["D6"]["recovery_median"], 4), "thr",
              round(r["D6"]["null_thr"], 4), "fired", r["D6"]["fired"])
    if "D8" in r:
        print("  D8 fired", r["D8"]["fired"], r["D8"].get("per_t"))
    if "D10" in r:
        print("  D10 h1max", round(r["D10"]["h1_max"], 3), "h2max",
              round(r["D10"]["h2_max"], 3), "fired", r["D10"]["fired"])
    if "potency" in r:
        print("  potency max", round(r["potency"]["max"], 4))
    if "D2_truth" in r:
        print("  D2_truth", {k: v for k, v in r["D2_truth"].items()
                             if not isinstance(v, dict)})
    print("  base@14:00", round(r["base"]["14:00"], 3), "uncond@14:00",
          round(r["uncond"]["14:00"], 4))
