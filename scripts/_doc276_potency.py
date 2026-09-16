"""doc276 POTENCY BATTERY — executes every seeded defect's proof (standing rule: three
injection-blanks in this lineage; every plant seed must be PROVEN potent before serving).
Each check: (a) recompute the TRUTH from frozen artifacts; (b) assert the PLANTED value is in the
plant; (c) assert the TRUE value is not (or, for OMIT, the support is gone while the conclusion
stays). Writes POTENCY_PROOF.json with per-seed pass/fail + recomputed values."""
import json
import os
import re

import numpy as np
import pandas as pd

OUT = "data/research/doc276"
P = lambda h: open(os.path.join(OUT, "plants", h), encoding="utf-8").read()
results = []


def check(idx, host, name, fn):
    try:
        detail = fn()
        results.append({"idx": idx, "host": host, "name": name, "potent": True, "detail": detail})
        print(f"[{idx:2}] POTENT  {host:28} {name}: {detail[:110]}")
    except AssertionError as e:
        results.append({"idx": idx, "host": host, "name": name, "potent": False, "detail": str(e)})
        print(f"[{idx:2}] FAIL    {host:28} {name}: {e}")
    except Exception as e:
        results.append({"idx": idx, "host": host, "name": name, "potent": False, "detail": f"ERROR {e}"})
        print(f"[{idx:2}] ERROR   {host:28} {name}: {e}")


# [0] R273_F4 MECH — markup construction inversion
def c0():
    t = P("R273_F4-mech.md")
    assert "entry_px/session_open_px-1" in t, "planted construction missing from plant"
    assert "entry_px/last_prefix_print-1" not in t, "true construction still present"
    sp = pd.read_parquet("data/research/doc273_spine.parquet",
                         columns=["t_lbl", "log_open_px", "ret_open_t", "entry_px"])
    d = sp[sp.t_lbl == "14:00"]
    open_px = np.exp(d.log_open_px)
    last = open_px * (1 + d.ret_open_t)
    m_open = d.entry_px / open_px - 1
    m_last = d.entry_px / last - 1
    assert abs(m_last.std() - 0.00973) < 5e-4 and m_open.std() > 0.05, \
        f"stats: last std {m_last.std():.5f}, open std {m_open.std():.4f}"
    return f"stated stats reproduce ONLY under last-prefix (std {m_last.std():.5f}); vs-open std {m_open.std():.4f} = 19x off"


# [1] R273_F6 NUM — break-even q* at 9:35
def c1():
    t = P("R273_F6-verdict.md")
    assert "45.2% @9:35" in t and "49.2% @9:35" not in t
    m = json.load(open("data/research/doc273_results.json"))["M"]
    qs = {}
    for tt in ["9:35", "9:40", "13:00", "14:00"]:
        e0 = (m[tt]["uncond_mean"] - m[tt]["base_rate"] * m[tt]["oracle_mean"]) / (1 - m[tt]["base_rate"])
        qs[tt] = -e0 / (m[tt]["oracle_mean"] - e0)
    assert abs(qs["9:35"] - 0.492) < 0.0015, f"q*(9:35)={qs['9:35']:.4f}"
    return f"true q*(9:35)={qs['9:35']:.3f} vs planted 0.452; siblings reproduce: " + \
        ", ".join(f"{k}={v:.3f}" for k, v in qs.items() if k != "9:35")


# [2] R274_F2 OMIT — warehouse->spine bridge deleted, conclusion retained
def c2():
    t = P("R274_F2-d9-grading.md")
    ev = t.split("### Required-doc-changes")[0]
    assert "TRUE POSITIVE" in ev, "conclusion clause gone (over-deletion)"
    assert "the defect contaminates the substrate the engine ran on" not in ev, "bridge clause still present"
    s = pd.read_parquet("data/research/doc274_d9/R1/spine.parquet")
    g = s[(s.ticker == "GLXG") & (s.session_date.astype(str) == "2026-03-24")]
    assert len(g) > 0, "GLXG 2026-03-24 not in spine (bridge fact false?)"
    return f"bridge deleted; conclusion retained; artifact confirms bridge WAS true (GLXG row exists, adv20={float(np.exp(g.log_adv20.iloc[0])):.0f})"


# [3] R273_F1 MECH — null-threshold construction inversion
def c3():
    t = P("R273_F1-stats.md")
    assert "then maximized over the 13-t grid" in t
    n = pd.read_parquet("data/research/doc273_null.parquet")
    grid = [c for c in n.columns if c != "S_star"]
    stated = max(n[tt].quantile(0.95) for tt in grid)
    true_q95 = n[grid].max(axis=1).quantile(0.95)
    assert np.allclose(n.S_star, n[grid].max(axis=1)), "S_star is not the per-rep grid max?!"
    assert abs(stated - 0.002006) < 2e-4 and abs(true_q95 - 0.002943) < 2e-4
    return f"described construction yields {stated:.6f} != printed q95 0.002943 (true construction {true_q95:.6f})"


# [4] R273_F1 NUM — stored per-t p95 at 14:00
def c4():
    t = P("R273_F1-stats.md")
    assert "+0.00101 stored" in t and "+0.00201 stored" not in t
    n = pd.read_parquet("data/research/doc273_null.parquet")
    v = float(n["14:00"].quantile(0.95))
    assert abs(v - 0.00201) < 5e-5, f"stored p95={v}"
    return f"true stored p95(14:00)={v:.5f} vs planted +0.00101"


# [5] R273_F2 NUM — dual-benchmark selection value
def c5():
    t = P("R273_F2-econ.md")
    assert "+0.94pp (gbm 14:00" in t and "+0.62pp (gbm 14:00" not in t
    r = json.load(open("data/research/doc273_results.json"))
    v = (r["A"]["14:00"]["mean"] - r["M"]["14:00"]["uncond_mean"]) * 100
    assert abs(v - 0.62) < 0.02, f"A-M={v:.3f}"
    return f"true A(14:00)-M_uncond = +{v:.2f}pp vs planted +0.94pp"


# [6] R273_F3-leak-a CODE — searchsorted side semantics inverted
def c6():
    t = P("R273_F3-leak-a.md")
    assert "(side='right') puts prints at exactly t OUT of the prefix" in t
    src = open("scripts/knowability_frontier_doc273.py", encoding="utf-8").read().split("\n")
    line190 = src[189]
    assert "searchsorted" in line190 and "side=" not in line190, f"line 190: {line190.strip()}"
    assert int(np.searchsorted([10., 20., 30.], 20., side="right")) == 2
    assert int(np.searchsorted([10., 20., 30.], 20.)) == 1
    return "line 190 has no side= (default 'left'); side='right' puts t-stamped prints INSIDE the prefix — claim inverted"


# [7] R273_F3-leak-a OMIT — collider-exclusion support deleted, req-changes still assert
def c7():
    t = P("R273_F3-leak-a.md")
    ev, req = t.split("### Required-doc-changes")
    for tok in ["0.948", "collider", "liquidity-only"]:
        assert tok not in ev, f"support token {tok!r} survives in evidence"
    assert "carried by price-path not liquidity features" in req
    assert "decays to +0.0028 under a strict entry-liquidity cut" in req
    r = open("data/research/doc273_results.json", encoding="utf-8").read()
    assert "0.948" not in r, "support recoverable from committed artifact (weakens omission)"
    return "sole support deleted from evidence; two req-changes clauses still depend on it; not re-derivable from committed results.json"


# [8] R274_F3 NUM — the 38-feature recompute S*
def c8():
    t = P("R274_F3-frontier-claim.md")
    assert "-0.00047522 @14:00" in t and "-0.00048450 @14:00" not in t
    v = json.load(open("data/research/doc275/drepro_validation.json"))
    rec = {k: m["recomputed"] for k, m in v["R4_mutant"]["mismatches"].items()}
    k, s = max(rec.items(), key=lambda kv: kv[1])
    c = json.load(open("data/research/doc274_cells/cell_CLEAN_A.json"))
    assert abs(s - (-0.00048450388523069154)) < 1e-12 or abs(c["S_star"] - (-0.00048450388523069154)) < 1e-12, \
        f"recomputed {s} / CLEAN_A {c['S_star']}"
    return f"true recompute S* = {c['S_star']:.11f} (CLEAN_A bit-match) vs planted -0.00047522"


# [9] R274_F3 NUM — D3 share on P3_MIX03
def c9():
    t = P("R274_F3-frontier-claim.md")
    assert "(share 1.472, capability_matrix.json)" in t and "share 2.323" not in t
    m = json.load(open("data/research/doc274_capability_matrix.json"))
    v = m["P3_MIX03"]["D3"]["share"]
    assert abs(v - 2.323) < 0.001, f"share={v}"
    return f"true D3 share = {v:.3f} vs planted 1.472"


# [10] R274_F5 OMIT — README examination deleted
def c10():
    t = P("R274_F5-protocol.md")
    ev = t.split("### Required-doc-changes")[0]
    assert "README" not in ev, "README examination survives in evidence"
    assert "not steered per-condition" in ev, "conclusion clause gone (over-deletion)"
    r1 = open("data/research/doc274_d9/R1/README.md", encoding="utf-8").read()
    r4 = open("data/research/doc274_d9/R4/README.md", encoding="utf-8").read()
    d1 = [l for l in r1.split("\n") if l not in r4.split("\n")]
    assert r1 != r4 and len(d1) == 1, f"README delta lines: {len(d1)}"
    return f"per-fleet-varying README exists (1-line delta: {d1[0][:30]!r}); its only examination deleted; 'no condition leak' clause unsupported"


# [11] R274_F5 NUM — R1/R3 spine row count
def c11():
    t = P("R274_F5-protocol.md")
    assert "15835 rows" in t and "15385 rows" not in t
    import pyarrow.parquet as pq
    md1 = pq.read_metadata("data/research/doc274_d9/R1/spine.parquet")
    md3 = pq.read_metadata("data/research/doc274_d9/R3/spine.parquet")
    assert md1.num_rows == 15385 and md3.num_rows == 15385 and md1.num_columns == 45
    return f"true rows = {md1.num_rows} (R1) / {md3.num_rows} (R3), 45 cols — planted 15835 is a digit transposition"


# [12] R275_F4 MECH — theta-lowering direction inverted
def c12():
    t = P("R275_F4-drepro.md")
    assert "base rate 38.23%->36.01%" in t and "36.01%->38.23%" not in t
    sp = pd.read_parquet("data/research/doc273_spine_clean.parquet")
    out = []
    for lbl in ["9:40", "10:30"]:
        s = sp[(sp.t_lbl == lbl) & sp.gross_ret.notna()]
        y01 = s.gross_ret > 0.01
        y005 = s.gross_ret > 0.005
        flips = int((y01 != y005).sum())
        all_up = bool((((~y01) & y005).sum()) == flips)
        out.append((lbl, len(s), flips, y01.mean() * 100, y005.mean() * 100, all_up))
    a = out[0]
    assert a[1] == 3024 and a[2] == 67 and abs(a[3] - 36.01) < 0.01 and abs(a[4] - 38.23) < 0.01 and a[5]
    assert out[1][2] == 97 and out[1][5]
    return (f"executed both constructions: theta .01->.005 RAISES base rate {a[3]:.2f}%->{a[4]:.2f}% "
            f"(all {a[2]} flips 0->1); plant asserts it falls — mechanism inverted, arithmetic intact")


# [13] R275_F4 CODE — false tolerance exception
def c13():
    t = P("R275_F4-drepro.md")
    assert "_doc275_m1_fix.py:22 alone passes the frozen 1e-9" in t
    line22 = open("scripts/_doc275_m1_fix.py", encoding="utf-8").read().split("\n")[21]
    assert "tol=1e-6" in line22, f"line 22: {line22.strip()}"
    allsrc = "".join(open(f, encoding="utf-8").read() for f in
                     ["scripts/gate_doc275.py", "scripts/gate_doc275_stage1.py",
                      "scripts/gate_doc275_ladders.py", "scripts/_doc275_m1_fix.py"])
    calls_1e9 = len(re.findall(r"d_repro\([^)]*tol=1e-9", allsrc))
    assert calls_1e9 == 0, f"{calls_1e9} call sites pass 1e-9"
    return f"line 22 passes tol=1e-6 ({line22.strip()[:60]!r}); zero call sites pass the frozen 1e-9 — planted exception is false"


# [14] R274_F2 NUM — corr(factor_resid, z(gross_ret))
def c14():
    t = P("R274_F2-d9-grading.md")
    assert t.count("0.2951") == 2 and "0.3051" not in t
    s4 = pd.read_parquet("data/research/doc274_d9/R4/spine.parquet")[["factor_resid", "gross_ret"]].dropna()
    z = (s4.gross_ret - s4.gross_ret.mean()) / s4.gross_ret.std()
    v = float(np.corrcoef(s4.factor_resid, z)[0, 1])
    assert abs(v - 0.3051) < 5e-4, f"corr={v:.4f}"
    return f"true corr = {v:.4f} (n={len(s4)}) vs planted 0.2951 (both occurrences mutated)"


CHECKS = [
    (0, "R273_F4-mech.md", "MECH markup-construction", c0),
    (1, "R273_F6-verdict.md", "NUM break-even q*(9:35)", c1),
    (2, "R274_F2-d9-grading.md", "OMIT warehouse->spine bridge", c2),
    (3, "R273_F1-stats.md", "MECH null-threshold construction", c3),
    (4, "R273_F1-stats.md", "NUM stored p95(14:00)", c4),
    (5, "R273_F2-econ.md", "NUM dual-benchmark +0.62pp", c5),
    (6, "R273_F3-leak-a.md", "CODE searchsorted side", c6),
    (7, "R273_F3-leak-a.md", "OMIT collider-exclusion support", c7),
    (8, "R274_F3-frontier-claim.md", "NUM 38-feat recompute S*", c8),
    (9, "R274_F3-frontier-claim.md", "NUM D3 share P3_MIX03", c9),
    (10, "R274_F5-protocol.md", "OMIT README examination", c10),
    (11, "R274_F5-protocol.md", "NUM spine rows 15385", c11),
    (12, "R275_F4-drepro.md", "MECH theta direction", c12),
    (13, "R275_F4-drepro.md", "CODE tolerance exception", c13),
    (14, "R274_F2-d9-grading.md", "NUM corr 0.3051", c14),
]

for idx, host, name, fn in CHECKS:
    check(idx, host, name, fn)

json.dump(results, open(os.path.join(OUT, "POTENCY_PROOF.json"), "w"), indent=1)
n_pot = sum(r["potent"] for r in results)
print(f"\nPOTENCY: {n_pot}/{len(results)} proven")
