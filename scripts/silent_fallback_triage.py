"""
Triage the silent-fallback findings:
  - Drop the 3 known-fixed instances (pandas_ta, D158 SEC dilution, PreMarketCache)
  - Classify hot/warm/cold by file path heuristics + caller search
  - Score severity HIGH/MED/LOW
  - Check tests/ for positive-case coverage by function name
"""
from __future__ import annotations
import json, re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "scripts" / "_silent_fallback_findings.json"

# Path-prefix heuristics for hot/warm/cold classification.
# HOT = called per-evaluation in the live trading loop.
# WARM = startup, EOD, sessionly, scheduler-tick periodic.
# COLD = scripts/manual jobs, retry handlers, infrequent utilities.
HOT_PREFIXES = (
    "src/scanners/",
    "src/composite/",
    "src/execution/",
    "src/core/",
    "src/data/feeds/",
    "src/data/intraday",
    "src/data/realtime",
    "src/data/quote",
    "src/data/fundamentals",      # SEC dilution lives here — known-bad pattern
    "src/data/cache",             # PreMarketCache pattern
    "src/data/pre_market",
    "src/data/tickers",
    "src/data/short_avail",
    "src/agents/",
    "src/composite/",
)
WARM_PREFIXES = (
    "src/scheduling/",
    "src/monitoring/",
    "src/analysis/",
    "src/utils/cache",
    "src/data/",       # catch-all for data ingest startup
    "src/shadow/",
    "main.py",
    "config/",
)
COLD_PREFIXES = (
    "scripts/",
    "src/backfill_agent/",
    "src/experiments/",
    "src/arena/",          # research harness
    "src/production_arena/",  # CLI-driven scenario replay, not live
    "src/llm_arena/",      # research harness
    "src/model_arena/",    # research harness
    "src/selection_arena/", # research harness
)

# 3 known-fixed exemplars to drop from the table
KNOWN_FIXED_PATTERNS = [
    # (rel-path-substring, function-name-substring)
    ("indicators", "pandas_ta"),     # pandas_ta dormancy fix
    ("indicators", "_pandas_ta"),
    ("d158", "dilution"),
    ("dilution", "classify"),
    ("pre_market_cache", ""),
    ("PreMarketCache", ""),
]

def classify_path(rel: str) -> str:
    rel_l = rel.lower()
    for p in HOT_PREFIXES:
        if rel_l.startswith(p): return "HOT"
    for p in WARM_PREFIXES:
        if rel_l.startswith(p): return "WARM"
    for p in COLD_PREFIXES:
        if rel_l.startswith(p): return "COLD"
    return "WARM"

def is_known_fixed(f) -> bool:
    rel_l = f["rel"].lower()
    func_l = f["func"].lower()
    # pandas_ta: try import pandas_ta - look at returns/log mentions of pandas_ta
    log_l = f.get("log", "").lower()
    if "pandas_ta" in log_l or "pandas_ta" in rel_l:
        return True
    # PreMarketCache.tickers
    if "premarketcache" in func_l.replace("_", "") or "pre_market_cache" in rel_l:
        return True
    # D158 SEC dilution classifier — file likely under data/fundamentals or sec/dilution
    if "dilution" in rel_l and ("classify" in func_l or "classifier" in func_l):
        return True
    if "sec" in rel_l and "dilution" in func_l:
        return True
    return False

NARROW_PARSER_EXCS = {
    "ValueError", "TypeError", "(ValueError, TypeError)",
    "(TypeError, ValueError)", "json.JSONDecodeError",
    "(json.JSONDecodeError, OSError)", "(OSError, json.JSONDecodeError)",
    "OSError", "FileNotFoundError", "KeyError", "AttributeError",
    "statistics.StatisticsError", "sqlite3.IntegrityError",
    "ImportError", "ModuleNotFoundError",
    "(ValueError, KeyError)", "(KeyError, ValueError)",
}

def severity(f, hot, has_test) -> str:
    klass = f["class"]
    caught = f["caught"]
    narrow_exc = caught not in ("Exception", "BaseException", "bare except", "")
    # narrow exception handlers: drop one level (less risky usually)
    base = "LOW"
    if hot == "HOT" and klass in ("S1", "S4"):
        base = "HIGH"
    elif (hot == "WARM" and klass in ("S1", "S4")) or (hot == "HOT" and klass in ("S2", "S3")):
        base = "MEDIUM"
    elif hot == "WARM" and klass in ("S2", "S3"):
        base = "LOW"
    else:
        base = "LOW"
    if has_test: base = downgrade(base)
    # Narrow, well-typed exceptions on the listed parser exceptions are
    # appropriate-by-design (they document the contract). Drop one extra level.
    if caught in NARROW_PARSER_EXCS:
        base = downgrade(base)
    elif narrow_exc and klass != "S4":
        base = downgrade(base)
    return base

def downgrade(s):
    return {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}[s]

def collect_test_function_names() -> set[str]:
    names = set()
    tdir = ROOT / "tests"
    if not tdir.exists(): return names
    import sys as _sys
    for p in tdir.rglob("*.py"):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as _e:
            print(f"  [skip] {p}: {_e}", file=_sys.stderr)
            continue
        # crude: look for any token mentions of the function name
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]{3,})\b", txt):
            names.add(m.group(1))
    return names

def main():
    data = json.loads(DATA.read_text())
    findings = data["findings"]
    test_tokens = collect_test_function_names()

    triaged = []
    for f in findings:
        if is_known_fixed(f):
            continue
        rel = f["rel"]
        hot = classify_path(rel)
        # function-name only (strip ClassName.)
        fname = f["func"].split(".")[-1]
        has_test = fname in test_tokens and fname not in {"__init__", "main", "run", "execute", "process"}
        sev = severity(f, hot, has_test)
        f["hot"] = hot
        f["has_test"] = has_test
        f["severity"] = sev
        triaged.append(f)

    # Sort: severity (H>M>L), then hot, then file
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    triaged.sort(key=lambda x: (sev_order[x["severity"]], x["rel"], x["try_line"]))

    by_sev = defaultdict(int)
    by_class = defaultdict(int)
    for f in triaged:
        by_sev[f["severity"]] += 1
        by_class[f["class"]] += 1

    out = ROOT / "scripts" / "_silent_fallback_triaged.json"
    out.write_text(json.dumps({
        "total": len(triaged),
        "by_sev": dict(by_sev),
        "by_class": dict(by_class),
        "findings": triaged,
    }, indent=2))
    print(f"Triaged {len(triaged)} findings")
    print("By severity:", dict(by_sev))
    print("By class:", dict(by_class))
    # print top 20 HIGH
    high = [f for f in triaged if f["severity"] == "HIGH"]
    print(f"\nHIGH count: {len(high)}")
    for f in high[:30]:
        print(f"  {f['rel']}:{f['try_line']} {f['func']} [{f['class']}] catches={f['caught']} -> {f['returns']!r}")

if __name__ == "__main__":
    main()
