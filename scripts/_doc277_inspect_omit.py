import json
rows = json.load(open("data/research/doc277/catch_matrix.json", encoding="utf-8"))
print("=== per-seed catches by tier x frame (all 16 seeds) ===")
seeds = sorted(set(r["seed"] for r in rows))
tiers = ["opus", "sonnet", "haiku", "opus_rep"]
for cls in ["OMIT", "CODE", "MECH", "NUM"]:
    print(f"\n--- {cls} ---")
    for s in seeds:
        srows = [r for r in rows if r["seed"] == s]
        if not srows or srows[0]["class"] != cls:
            continue
        cell = {}
        for r in srows:
            cell[(r["tier"], r["frame"])] = r["catch"]
        f1 = " ".join(f"{t[:4]}:{cell.get((t,'F1'),'-')}" for t in tiers)
        f2 = " ".join(f"{t[:4]}:{cell.get((t,'F2'),'-')}" for t in ["opus","sonnet","haiku"])
        print(f"  {s[:40]:40} diff={srows[0]['difficulty']}  F1[{f1}]  F2[{f2}]")
