"""DOC 289 — the CONSERVATION / CONDENSATION probe (the design-panel physicist lens; orthogonal to the
Stage-A selection-channel test). Treats each morning cohort as a closed system competing for one finite
pool of retail ATTENTION (proxy = intraday dollar-volume) and asks three physics questions + one money question:

  Q1 CONSERVATION : is total intraday dollar-volume a predictable multiple of total PREMARKET dollar-volume?
                    (is the attention "pie" set pre-open, then merely redistributed?)
  Q2 CONDENSATION : within a cohort, is dollar-volume winner-take-all? order parameter = Herfindahl H and the
                    top-1 condensate share; is H bimodal across the 53 cohorts (a phase transition)?
  Q3 CONTROL PARAM: does a PRE-DECISION variable (cohort size, premarket-$vol Herfindahl, gap dispersion)
                    predict the intraday condensation (H / max share)? i.e. can you tell pre-open it will condense?
  Q4 SELECTION    : (the money question, NOT tested in Stage A) does the name with the max PREMARKET
                    dollar-volume share become the intraday attention condensate AND the price winner?
                    permutation-nulled within cohort. This is a DIFFERENT feature than Stage A used.
"""
from __future__ import annotations
import json, math, os, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT); sys.path.insert(0, str(_ROOT / "scripts"))
OUT = _ROOT / "data" / "research" / "doc289"
FEATURES = _ROOT / "data" / "features"
MORNING = lambda r: (r.get("hour_et") == 9) or (r.get("hour_et") == 10 and (r.get("minute_et") or 0) <= 30)


def herfindahl(shares):
    return float(sum(s * s for s in shares))


def main():
    import duckdb
    import fill_model_backtest as F
    import _doc280_posture_backtest as B
    con = duckdb.connect(); con.execute("SET threads=4")

    cohorts = []  # per session: list of dicts with pm_dvol, intraday_dvol, peak_run, ticker
    for fp in sorted(FEATURES.glob("features_2026-*.jsonl")):
        rows = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        byt = defaultdict(list)
        for r in rows:
            byt[r["ticker"]].append(r)
        members = []
        for tk, snaps in byt.items():
            m = [r for r in snaps if r.get("timestamp") and MORNING(r)]
            if not m:
                continue
            m.sort(key=lambda r: r["timestamp"])
            r0 = m[0]
            try:
                ts0 = F._parse_ts(r0["timestamp"])
            except Exception:
                continue
            lb = B._load_local_bars(con, tk, ts0)
            if not lb or not lb["bars"] or not lb["close_px"]:
                continue
            bars = lb["bars"]
            entry = next((b[2] for b in bars if b[0] >= 0 and b[2] > 0), bars[0][4])
            if not entry or entry <= 0:
                continue
            win = [b for b in bars if 0 <= b[0] <= 60.0]
            peak = (max((b[3] for b in win), default=entry) / entry) - 1.0
            # intraday dollar-volume (attention captured) = sum vol*close over the session bars
            idv = sum(b[5] * b[4] for b in bars if b[0] >= 0)
            pmv = r0.get("premarket_volume"); px = r0.get("current_price") or entry
            pmdv = (float(pmv) * float(px)) if pmv else None
            members.append({"ticker": tk, "peak_run": peak, "intraday_dvol": idv, "pm_dvol": pmdv})
        if len(members) >= 5:
            cohorts.append(members)
    con.close()

    # Q1 conservation: total intraday vs total premarket dollar-volume across cohorts (names with pm known)
    tot_id, tot_pm = [], []
    for c in cohorts:
        km = [m for m in c if m["pm_dvol"]]
        if len(km) >= 3:
            tot_id.append(sum(m["intraday_dvol"] for m in km))
            tot_pm.append(sum(m["pm_dvol"] for m in km))
    rho_cons, p_cons = spearmanr(np.log(np.array(tot_pm) + 1), np.log(np.array(tot_id) + 1)) if len(tot_id) > 5 else (float("nan"), float("nan"))
    ratio = np.array(tot_id) / (np.array(tot_pm) + 1e-9)
    cv_ratio = float(np.std(ratio) / (np.mean(ratio) + 1e-9)) if len(ratio) else float("nan")

    # Q2 condensation: Herfindahl + top-1 share of intraday dollar-volume within each cohort
    H, maxshare = [], []
    for c in cohorts:
        tot = sum(m["intraday_dvol"] for m in c) or 1.0
        sh = [m["intraday_dvol"] / tot for m in c]
        H.append(herfindahl(sh)); maxshare.append(max(sh))
    H = np.array(H); maxshare = np.array(maxshare)
    # bimodality: dip via comparing to uniform-share baseline 1/n
    unif = np.array([1.0 / len(c) for c in cohorts])
    excess_H = H - unif  # how much more concentrated than uniform

    # Q3 control parameter: does premarket-$vol Herfindahl predict intraday Herfindahl?
    pmH, idH = [], []
    for c in cohorts:
        km = [m for m in c if m["pm_dvol"]]
        if len(km) < 3:
            continue
        tp = sum(m["pm_dvol"] for m in km) or 1.0
        ti = sum(m["intraday_dvol"] for m in km) or 1.0
        pmH.append(herfindahl([m["pm_dvol"] / tp for m in km]))
        idH.append(herfindahl([m["intraday_dvol"] / ti for m in km]))
    rho_ctrl, p_ctrl = spearmanr(pmH, idH) if len(pmH) > 5 else (float("nan"), float("nan"))

    # Q4 SELECTION (money): does the max-premarket-$vol-share name win the cohort (price argmax)?
    rng = np.random.default_rng(289)
    hits = 0; tot_c = 0; rand_hits = []
    pm_wins_intraday = 0  # does premarket condensate become intraday condensate?
    for c in cohorts:
        km = [m for m in c if m["pm_dvol"]]
        if len(km) < 3:
            continue
        tot_c += 1
        pm_lead = max(km, key=lambda m: m["pm_dvol"])
        price_win = max(c, key=lambda m: m["peak_run"])
        id_lead = max(c, key=lambda m: m["intraday_dvol"])
        hits += int(pm_lead["ticker"] == price_win["ticker"])
        pm_wins_intraday += int(pm_lead["ticker"] == id_lead["ticker"])
    # permutation null for Q4: random in-cohort pick == price winner
    null = []
    for _ in range(2000):
        h = 0
        for c in cohorts:
            km = [m for m in c if m["pm_dvol"]]
            if len(km) < 3:
                continue
            price_win = max(c, key=lambda m: m["peak_run"])
            h += int(c[rng.integers(len(c))]["ticker"] == price_win["ticker"])
        null.append(h / tot_c)
    null = np.array(null)
    sel_acc = hits / tot_c if tot_c else float("nan")
    p_sel = float((null >= sel_acc).mean())

    res = {
        "n_cohorts": len(cohorts),
        "Q1_conservation": {"spearman_logPM_vs_logIntraday": round(float(rho_cons), 3), "p": float(p_cons),
                            "ratio_intraday_over_premarket_CV": round(cv_ratio, 3),
                            "note": "high correlation + low CV would mean the attention pie is set pre-open"},
        "Q2_condensation": {"herfindahl_mean": round(float(H.mean()), 3), "herfindahl_median": round(float(np.median(H)), 3),
                            "top1_share_mean": round(float(maxshare.mean()), 3), "top1_share_max": round(float(maxshare.max()), 3),
                            "excess_over_uniform_mean": round(float(excess_H.mean()), 3),
                            "note": "top1_share_mean >> 1/n indicates winner-take-all condensation of attention"},
        "Q3_control_param": {"spearman_premarketH_vs_intradayH": round(float(rho_ctrl), 3), "p": float(p_ctrl),
                             "note": "positive = you can tell pre-open whether attention will condense"},
        "Q4_selection_money": {"premarket_leader_is_price_winner_acc": round(sel_acc, 3),
                               "random_null_mean": round(float(null.mean()), 3),
                               "null_p95": round(float(np.percentile(null, 95)), 3), "p_value": p_sel,
                               "premarket_leader_becomes_intraday_leader_frac": round(pm_wins_intraday / tot_c, 3) if tot_c else None,
                               "verdict": ("SELECTION SIGNAL — premarket $vol leader predicts the price winner above chance"
                                           if p_sel < 0.05 else
                                           "no selection edge — premarket $vol leadership does not predict the price winner")},
    }
    (OUT / "condensation_result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
