import json
m = json.load(open("data/research/doc277/DIFFICULTY_SEALED.json"))
items = [{"id": i["item_id"], "cls": i["seed_class"],
          "path": i["served_path"].replace("\\", "/"),
          "recomp": i["recompute_identity_flag"]} for i in m["items"]]
print(json.dumps(items, separators=(",", ":")))
