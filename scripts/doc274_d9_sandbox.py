"""doc 274 D9 — sealed instance-blinded sandbox builder for the agent-referee fleets.

Per the frozen prereg: 4 run-IDs (R1..R4) sealed-assigned to {decoy, decoy, mutant,
specimen} by seeded RNG; the mapping file's sha256 goes into the state file BEFORE
fleets launch; contents revealed only after grading. Each run-ID directory contains:
  engine_result.json   cell stats STRIPPED of defense outputs and provenance
  spine.parquet        the cell's spine (neutral name)
  day_aggs_export.parquet  the warehouse export FOR THIS RUN (masked for overlay-mount
                           mutants, unmasked otherwise -- same filename either way)
  engine_v2.py         sanitized engine (scrub-term comments stripped, _clean -> _v2)
  method_checklist.md  anecdote-free doctrine
  README.md            identical framing for every fleet
Grep self-check for scrub terms runs over every text artifact; failure -> D9 auto-demotes.

Usage: python scripts/doc274_d9_sandbox.py --build --mutant CELLNAME [--mask A,B]
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil
import numpy as np
import pandas as pd

ROOT = "data/research/doc274_d9"
SCRUB = ["zombie", "stale", "contaminat", "clean", "03-23", "mutation", "gauntlet",
         "274", "273", "specimen", "pathology", "inject", "leak", "mix"]
CHECKLIST = """# Hostile-audit method checklist (standing doctrine)

You are auditing a quantitative research result for defects. The artifact set may or
may not contain one. Audit ALL of the following, mechanically, against the provided
data; report any defect as MECHANISM + LOCUS + the recomputation that proves it.

1. UNIVERSE MEMBERSHIP: recompute the stated inclusion gate (gap >= 8% open vs prior
   close; open $0.50-20; trailing-20d ADV >= $1M) for every (ticker, session_date)
   directly from day_aggs_export.parquet, INCLUDING the freshness of the prior close
   (how many calendar days old?) and the integrity of any trailing window (are the
   sessions inside the window actually present in the export?).
2. LOOK-AHEAD / FEATURE TIMING: features must use prints strictly before the decision
   time t. Spot-recompute features for sampled rows from the raw trades partitions
   (data/polygon_warehouse/trades_v1_parquet, ET derived from raw sip_timestamp) with
   the tape hard-truncated at t and compare.
3. LABEL INTEGRITY: entry = VWAP of prints [t+5s, t+300s); exit = VWAP [15:50,16:00)
   (fallback last print >= 15:00). Recompute for sampled rows. Check no feature column
   is a transform of the label's own measurement interval (correlate candidate columns
   against exit/last-prefix-price style statistics).
4. CONCENTRATION: attribute the result's per-row log-loss reduction by session date.
   If a small number of days carries most of the positive mass, name them and inspect
   those days' cohorts (base rate, activity, gate inputs) for anomalies.
5. STABILITY: split-half (calendar) re-estimation; a pooled positive absent in both
   halves is suspect.
6. ECONOMICS COHERENCE: base rates, unconditional net means, and validity (entryable)
   fractions should be internally consistent across time-of-day and with each other.
7. SAMPLE COMPOSITION: month-by-month distribution shifts in size/price/activity
   covariates; sudden cohort changes; duplicated or near-duplicated rows.

Verdict format: DEFECT(mechanism, locus, evidence) or NO-DEFECT-FOUND, plus a
confidence grade and the exact recomputations performed.
"""
README = """# Audit run {RUN}

This directory contains one research-result artifact set from an intraday equity
study (executable labels on a small-cap gap universe; decision-time grid; the engine
computes out-of-fold log-loss reduction vs base rate for two model families with a
within-day permutation null). Your fleet's job: hostile-audit it for defects per
method_checklist.md. The set may or may not contain a defect; "no defect found" is an
acceptable verdict and false alarms carry a cost. The raw trades warehouse is at
data/polygon_warehouse/trades_v1_parquet (read-only). The daily-aggregates export for
THIS run is day_aggs_export.parquet (use it, not any other daily source). engine_v2.py
is the engine that produced engine_result.json; spine.parquet is the row-level data.
"""


def _sanitize_engine(dst: str) -> None:
    src = open("scripts/knowability_frontier_doc273.py", encoding="utf-8").read()
    # cut everything from the post-retest amendments onward: the fleets need only the
    # CORE engine (spine build, families, dll, null, plants) — the amendments section
    # and verdict() carry the kill narrative and the *_clean machinery (tells)
    cut = src.find("# ───────────────────── post-retest amendments")
    if cut > 0:
        core, rest = src[:cut], src[cut:]
        nullsec = rest.find("# ───────────────────────────── permutation null")
        src = core + (rest[nullsec:rest.find("# ───────────────────────────── planted-signal")]
                      if nullsec > 0 else "")
    src = re.sub(r'"""doc 273.*?"""', '"""Engine v2 — executable-label decision-grid study."""',
                 src, count=1, flags=re.S)
    lines = []
    for ln in src.splitlines():
        low = ln.lower()
        if ln.strip().startswith("#") and any(s in low for s in SCRUB):
            continue
        lines.append(ln)
    src = "\n".join(lines)
    src = re.sub(r'""".*?"""', '"""(doc removed)"""', src, flags=re.S)
    src = (src.replace("_clean", "_v2").replace("doc273", "study").replace("doc 273", "study")
              .replace("CLEAN", "BASE").replace("clean", "base"))
    open(dst, "w", encoding="utf-8").write(src)


def _warehouse_export(dst: str, mask: tuple[str, str] | None) -> None:
    import duckdb
    where = (f"WHERE ts_et::DATE NOT BETWEEN DATE '{mask[0]}' AND DATE '{mask[1]}'"
             if mask else "")
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_parquet('data/polygon_warehouse/day_aggs/**/*.parquet') "
        f"{where}) TO '{dst}' (FORMAT PARQUET)")


def _strip_result(cell_json: str, dst: str) -> None:
    rec = json.load(open(cell_json))
    keep = {k: rec[k] for k in ("S_star", "S_star_at", "I", "base", "uncond", "valid_n",
                                "null_p", "null_thr95") if k in rec}
    json.dump(keep, open(dst, "w"), indent=1, default=float)


def build(mutant_cell: str, mutant_mask: tuple[str, str] | None):
    rng = np.random.default_rng(274_001)
    conditions = ["decoy", "decoy", "mutant", "specimen"]
    rng.shuffle(conditions)
    assignment = {f"R{i+1}": c for i, c in enumerate(conditions)}
    os.makedirs(ROOT, exist_ok=True)
    apath = os.path.join(ROOT, "ASSIGNMENT.json")
    json.dump(assignment, open(apath, "w"))
    sha = hashlib.sha256(open(apath, "rb").read()).hexdigest()
    print(f"ASSIGNMENT sealed: sha256={sha}")

    spec_map = dict(
        decoy=dict(spine="data/research/doc273_spine_clean.parquet",
                   cell="data/research/doc274_cells/cell_CLEAN_A.json", mask=None),
        mutant=dict(spine=None,  # built below per the frozen-rule mutant (P3_MIX03)
                    cell=f"data/research/doc274_cells/cell_{mutant_cell}.json",
                    mask=mutant_mask),
        specimen=dict(spine="data/research/doc273_spine.parquet",
                      cell="data/research/doc274_cells/cell_SPECIMEN.json", mask=None),
    )
    import sys as _sys
    _sys.path.insert(0, "scripts")
    import doc274_cells_registry as reg

    for run, cond in assignment.items():
        d = os.path.join(ROOT, run)
        os.makedirs(d, exist_ok=True)
        info = spec_map[cond]
        if cond == "mutant":
            if mutant_cell == "P3_MIX03":
                spine, _feats = reg.inject_p3_exitmix(0.3)
                # the column NAME is a tell — exported under an innocuous name; the
                # spec-divergence (39 cols vs the engine's 38) is itself a legitimate
                # detection route for the fleet
                spine = spine.rename(columns={"leak_mix": "factor_resid"})
            else:
                spine, _m, _p = reg.inject_p1_hole(
                    reg.ADV_HOLE_MASK if mutant_cell == "P1_ADVHOLE" else reg.HOLE_REPLAY_MASK,
                    reg.ADV_HOLE_QUALIFY if mutant_cell == "P1_ADVHOLE"
                    else (reg.HOLE_REPLAY_MASK[1], "2026-01-15"))
            spine.to_parquet(os.path.join(d, "spine.parquet"), index=False)
        else:
            df = pd.read_parquet(info["spine"])
            df = df[df.t_lbl.isin(reg.mg.GRID5)].reset_index(drop=True)
            df.to_parquet(os.path.join(d, "spine.parquet"), index=False)
        _strip_result(info["cell"], os.path.join(d, "engine_result.json"))
        _warehouse_export(os.path.join(d, "day_aggs_export.parquet"), info["mask"])
        _sanitize_engine(os.path.join(d, "engine_v2.py"))
        open(os.path.join(d, "method_checklist.md"), "w", encoding="utf-8").write(CHECKLIST)
        open(os.path.join(d, "README.md"), "w", encoding="utf-8").write(README.format(RUN=run))
        print(f"  built {run} ({cond})")

    # grep self-check over text artifacts (auto-demote trigger if it fails).
    # WORD-BOUNDED terms: the first audit pass flagged vocabulary collisions
    # ("permutation" contains "mutation"; stale_at_t is a registered feature name whose
    # renaming would CREATE spec divergence) — both documented; tells are matched as
    # standalone words/known tell-strings instead.
    tell_patterns = [r"\bzombie", r"\bcontaminat", r"\bgauntlet\b", r"\bspecimen\b",
                     r"\binject", r"03-23", r"\bclean\b", r"_clean\b", r"doc.?27[34]",
                     r"\bleak", r"\bdecoy", r"\bmutant"]
    bad = []
    for run in assignment:
        for fn in ("engine_v2.py", "method_checklist.md", "README.md", "engine_result.json"):
            p = os.path.join(ROOT, run, fn)
            txt = open(p, encoding="utf-8").read().lower()
            for pat in tell_patterns:
                if re.search(pat, txt):
                    bad.append((run, fn, pat))
    if bad:
        print("GREP SELF-CHECK FAILED:", bad[:10])
        print("D9 AUTO-DEMOTES to descriptive-only per the prereg trigger.")
    else:
        print("GREP SELF-CHECK PASS — sandbox is instance-blind.")
    return assignment, sha, (not bad)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--mutant", type=str, default="P1_ADVHOLE")
    a = ap.parse_args()
    if a.build:
        import importlib.util as ilu
        sp = ilu.spec_from_file_location("reg", "scripts/doc274_cells_registry.py")
        reg = ilu.module_from_spec(sp); sp.loader.exec_module(reg)
        mask = reg.ADV_HOLE_MASK if a.mutant == "P1_ADVHOLE" else reg.HOLE_REPLAY_MASK
        build(a.mutant, mask)
