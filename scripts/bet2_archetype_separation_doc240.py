"""doc 240 — BET#2 Gate 3 (the decisive cheap read): do catalyst ARCHETYPES separate on forward returns?

If archetypes don't separate (KS, Bonferroni) from the base gapper distribution and from each other, the
"playbook" is the base strategy mislabeled -> BET#2 dead, no tagger/playbook work needed. Run on the 2026
eval corpus (journals carry real news_headlines; exit_labels.parquet carries the 30-min forward return).
A reproducible RULE-BASED tagger stands in for the (Together-403-blocked) production LLM tagger for this
preliminary in-sample read. NO live change.
"""
from __future__ import annotations
import json, glob, re, numpy as np, pandas as pd, duckdb
from scipy.stats import ks_2samp

# --- archetype taxonomy (doc-231 research + MEMORY catalyst patterns), priority-ordered ---
RULES = [
    ("REVERSE_SPLIT", [r"reverse (stock )?split", r"\bsplit\b.*ratio"]),
    ("OFFERING_DILUTION", [r"offering", r"registered direct", r"\batm\b", r"424b", r"\bs-1\b",
                            r"private placement", r"pricing of", r"prices \$?\d", r"warrant", r"raises \$"]),
    ("FDA_APPROVAL", [r"fda approv", r"\bapproval\b", r"\bclears?\b", r"510\(k\)", r"pdufa",
                       r"marketing authorization", r"\bnda\b", r"\bbla\b", r"breakthrough therapy"]),
    ("CLINICAL_DATA", [r"phase [123]", r"\btrial\b", r"topline", r"clinical", r"study (results|met)",
                        r"endpoint", r"\bdata\b.*(positive|met)", r"interim results"]),
    ("MA_DEAL", [r"acqui", r"\bmerger\b", r"to acquire", r"partnership", r"collaborat",
                  r"\bagreement\b", r"\bcontract\b", r"\bawarded?\b", r"\bdeal\b", r"\bjoint venture\b",
                  r"\bloi\b", r"letter of intent"]),
    ("EARNINGS", [r"earnings", r"\bbeats?\b", r"\bq[1-4]\b", r"revenue", r"quarterly", r"\beps\b",
                   r"guidance", r"fiscal"]),
    ("GENERAL_PROMO", [r"soar", r"surg", r"rally", r"rallie", r"trending", r"why .* (stock|shares)",
                        r"skyrocket", r"\bjumps?\b", r"\bspikes?\b", r"moving", r"\brocket"]),
]

def tag(headlines):
    txt = " || ".join(headlines).lower() if headlines else ""
    if not txt.strip():
        return "NO_CATALYST"
    for name, pats in RULES:
        if any(re.search(p, txt) for p in pats):
            return name
    return "OTHER_NEWS"

def main():
    # 1. per-ticker-day headlines from 2026 journals
    hl = {}
    for fn in glob.glob('data/journals/journal_2026-*.jsonl'):
        try:
            for l in open(fn, encoding='utf-8', errors='ignore'):
                if not l.strip(): continue
                try: r = json.loads(l)
                except: continue
                tk, sd = r.get('ticker'), r.get('session_date')
                idd = r.get('input_data', {}) or {}
                h = idd.get('news_headlines') or []
                if tk and sd and (tk, sd) not in hl:
                    hl[(tk, sd)] = h
        except: pass
    tags = {k: tag(v) for k, v in hl.items()}

    # 2. 30-min forward return per ticker-day from exit_labels.parquet (entry idx3 -> +6)
    df = duckdb.connect().execute(
        "SELECT ticker,session_date,minute_idx,ret_session FROM read_parquet('data/research/exit_labels.parquet')").df()
    ret30 = {}
    for (tk, d), g in df.groupby(["ticker", "session_date"]):
        g = g.sort_values("minute_idx"); rel = 1 + g.ret_session.to_numpy()
        if len(rel) > 9 and rel[3] > 0:
            ret30[(tk, d)] = rel[9] / rel[3] - 1.0

    # 3. join
    rows = [(tags[k], ret30[k]) for k in tags if k in ret30]
    dd = pd.DataFrame(rows, columns=["archetype", "r30"])
    print(f"joined ticker-days (headlines x returns): {len(dd)}")
    print("\n=== Gate-2 (preliminary, 2026-only) sample-size + return by archetype ===")
    print(f"  {'archetype':18}{'N':>5}{'median r30':>11}{'mean r30':>10}{'P(up)':>7}")
    base = dd.r30.values
    big = []
    for a, g in dd.groupby("archetype"):
        print(f"  {a:18}{len(g):>5}{g.r30.median()*100:>+10.2f}%{g.r30.mean()*100:>+9.2f}%{(g.r30>0).mean()*100:>6.0f}%")
        if len(g) >= 30: big.append((a, g.r30.values))

    print("\n=== Gate-3 SEPARATION: KS each archetype (N>=30) vs BASE (all), Bonferroni ===")
    m = len(big); alpha = 0.05 / max(m, 1)
    any_sep = False
    for a, vals in big:
        ks, p = ks_2samp(vals, base)
        sig = p < alpha
        any_sep = any_sep or sig
        print(f"  {a:18} vs base: KS={ks:.3f} p={p:.4f} {'*SEPARATES*' if sig else 'no (= base mislabeled)'}")
    print(f"\n  Bonferroni alpha={alpha:.4f} (m={m} archetypes with N>=30)")
    print(f"  ANY archetype separates from base? {'YES' if any_sep else 'NO -> BET#2 archetypes are the base mislabeled -> DEAD'}")
    # pairwise between the two largest
    if len(big) >= 2:
        print("\n=== pairwise KS between archetypes (do they differ from EACH OTHER?) ===")
        for i in range(len(big)):
            for j in range(i+1, len(big)):
                ks, p = ks_2samp(big[i][1], big[j][1])
                if p < 0.05/ (m*(m-1)/2):
                    print(f"  {big[i][0]} vs {big[j][0]}: KS={ks:.3f} p={p:.4f} *DIFFER*")
    print("\n  NOTE: 2026-only, in-sample, rule-based tagger (Together LLM 403-blocked). Cross-regime + LLM-kappa")
    print("        gates are the binding test IF separation shows here. If no separation -> BET#2 closes now.")

if __name__ == "__main__":
    main()
