"""DOC 296 — purge the look-ahead rows the skeptic fleet found (MAJOR, attack 1 issue 2).

A contract's strike was selected using spot at its monthly anchor date; rows for days BEFORE that
anchor embed future-spot moneyness in iv(t) (and would let SEVP pick a straddle whose ATM-ness knows
the earnings move). The collector now refuses to emit such rows (_doc293_iv_collector.py); this
script applies the same rule retroactively to both stores. Backups: *.bak-doc296.

Rules (conservative min-anchor: a row survives if ANY legally-selected contract could have written it):
  option_eod.jsonl : drop row if date < earliest anchor that selected row's contract
  iv_eod.jsonl     : source=polygon_bs_inverted only; exp = date + tenor_days calendar days;
                     drop row if date < earliest anchor that selected (ticker, exp)
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import importlib.util
_spec = importlib.util.spec_from_file_location("col", str(_ROOT / "scripts" / "_doc293_iv_collector.py"))
COL = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(COL)

IV = _ROOT / "data" / "iv_store" / "iv_eod.jsonl"
PRICES = _ROOT / "data" / "iv_store" / "option_eod.jsonl"


def main():
    st = COL._state()
    amap = COL.contract_anchors(st)                       # contract -> min anchor
    emap = {}                                             # (ticker, exp) -> min anchor
    for ck, pair in st["done_contracts"].items():
        if ck == "_pulled" or not pair or ":" not in ck:
            continue
        name, a = ck.split(":", 1)
        k = (name, pair["exp"])
        emap[k] = min(a, emap[k]) if k in emap else a

    out = {}
    # ── prices ──
    rows = [json.loads(l) for l in PRICES.read_text(encoding="utf-8").splitlines() if l.strip()]
    bak = PRICES.with_suffix(".jsonl.bak-doc296")
    if not bak.exists():
        bak.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    keep, drop, unmapped = [], 0, 0
    for r in rows:
        a = amap.get(r["contract"])
        if a is None:
            unmapped += 1; keep.append(r)
        elif r["date"] < a:
            drop += 1
        else:
            keep.append(r)
    PRICES.write_text("\n".join(json.dumps(r) for r in keep) + ("\n" if keep else ""), encoding="utf-8")
    out["prices"] = {"before": len(rows), "dropped": drop, "kept": len(keep), "unmapped_kept": unmapped}

    # ── iv ──
    rows = [json.loads(l) for l in IV.read_text(encoding="utf-8").splitlines() if l.strip()]
    bak = IV.with_suffix(".jsonl.bak-doc296")
    if not bak.exists():
        bak.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    keep, drop, unmapped = [], 0, 0
    for r in rows:
        if r.get("source") != "polygon_bs_inverted":
            keep.append(r); continue
        # collector stores tenor_days = int(T*365) which floats DOWN one day for some DTEs, so the
        # true expiry is date+tenor or date+tenor+1. Conservative: drop if ANY candidate implies
        # the row predates its contract's selection anchor.
        anchors = []
        for off in (0, 1):
            exp = (datetime.fromisoformat(r["date"])
                   + timedelta(days=int(r["tenor_days"]) + off)).strftime("%Y-%m-%d")
            a = emap.get((r["ticker"], exp))
            if a is not None:
                anchors.append(a)
        if not anchors:
            unmapped += 1; keep.append(r)
        elif any(r["date"] < a for a in anchors):
            drop += 1
        else:
            keep.append(r)
    IV.write_text("\n".join(json.dumps(r) for r in keep) + ("\n" if keep else ""), encoding="utf-8")
    out["iv"] = {"before": len(rows), "dropped": drop, "kept": len(keep), "unmapped_kept": unmapped}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
