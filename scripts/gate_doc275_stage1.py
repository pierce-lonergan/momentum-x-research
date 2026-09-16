"""doc 275 Stage-1 runner — the six arms under the frozen §3 recipes (DORMANT-C, read-only).

Implements the panel-frozen spec on top of gate_doc275 cores:
  * studentized maxT family statistic over members x {money, dll} at each member's t;
    fire iff real >= 10th-largest of B=200 null maxes (Phipson-Smyth p).
  * Tier-1: fixed random 5-sparse scores; money = winsorized top-decile mean net
    (winsorization ONCE on the unpermuted spine — invariant under donor-bundle perms);
    dll = per-fold 1-D logistic calibration on the score, REFIT per permutation.
  * Tier-2: exact-refit 5-feature logistic per permutation (the frozen-score
    approximation is dead; one bias-exhibit cell only).
  * truth-generator: outer within-day donor scrambles compose with the inner shared
    permutations (both from the mirrored donor-bundle construction, disjoint seeds).
  * ladders A (273-style label plants, driver in-subset, dll channel) and B (exit-mix
    leaks, money channel + D-REPRO pairing), >=25 reps/rung, dual-mode thresholds.
  * contamination arms A/B/C/D with per-arm q95 and the frozen specimen-canary.
Artifacts -> data/research/doc275/. Seeds: root 275_777 (disjoint from burned pilot
ranges and from the committed pool seed 275_100 used in the prereg smoke).
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_doc275 as g

kf = g.kf
OUT = g.OUTDIR
GRID5 = g.GRID5
B = 200
POOL_T1, POOL_T2 = 1000, 300
ALPHA_RANK = 10          # fire iff real >= 10th-largest of B=200 null maxes (q95, Phipson-Smyth)
N_SWEEP = [10, 30, 100, 300, 1000]
ROOT_SEED = 275_777


# ─────────────────────────── member pools (committed, seeded) ───────────────────────────

def _seed(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{ROOT_SEED}|{tag}".encode()).digest()[:4], "big")


def tier1_pool(M=POOL_T1):
    rng = np.random.default_rng(_seed("tier1"))
    out = []
    for m in range(M):
        t = GRID5[int(rng.integers(len(GRID5)))]
        idx = rng.choice(38, size=5, replace=False)
        w = np.zeros(38)
        w[idx] = rng.choice([-1.0, 1.0], 5) * rng.uniform(0.5, 1.5, 5)
        out.append(dict(id=f"T1_{m}", t=t, w=w))
    return out


def tier2_pool(M=POOL_T2):
    rng = np.random.default_rng(_seed("tier2"))
    return [dict(id=f"T2_{m}", t=GRID5[int(rng.integers(len(GRID5)))],
                 feats=sorted(rng.choice(38, size=5, replace=False).tolist()))
            for m in range(M)]


SPECIMEN_CANARY = dict(id="CANARY", t="14:00", w=None)   # zombie fingerprint, frozen:
CANARY_SPEC = {"intensity": -1.0, "rv_1m": -1.0, "t_since_print_log": +1.0,
               "max_gap_log": +1.0, "pm_logvol": -1.0}


# ─────────────────────────── statistics under shared permutations ───────────────────────────

def _prep_arm(spine):
    packs = g.build_packs(spine)
    donors = g.shared_permutations(spine, packs, B=B, seed=_seed("innerperm"))
    from sklearn.preprocessing import QuantileTransformer
    Xq, wnet, yv = {}, {}, {}
    for lbl in GRID5:
        X = np.nan_to_num(packs[lbl]["X"], nan=0.0)
        qt = QuantileTransformer(output_distribution="normal",
                                 n_quantiles=min(1000, len(X)), random_state=0)
        Xq[lbl] = qt.fit_transform(X)
        wnet[lbl] = kf._wins(packs[lbl]["net"])
        yv[lbl] = packs[lbl]["y"]
    return dict(packs=packs, donors=donors, Xq=Xq, wnet=wnet, y=yv)


def _dll_1d(score, y, fold):
    """Per-fold 1-D logistic calibration OOF dll (the frozen Tier-1 dll recipe)."""
    from sklearn.linear_model import LogisticRegression
    eps = 1e-4
    oof = np.full(len(y), np.nan)
    base = np.empty(len(y))
    s2 = score.reshape(-1, 1)
    for f in range(kf.NFOLD):
        tr, te = fold != f, fold == f
        if te.sum() == 0 or len(np.unique(y[tr])) < 2:
            continue
        m = LogisticRegression(C=1.0, max_iter=200)
        m.fit(s2[tr], y[tr])
        oof[te] = m.predict_proba(s2[te])[:, 1]
        base[te] = y[tr].mean()
    ok = ~np.isnan(oof)
    if ok.sum() < 100:
        return np.nan
    p = np.clip(oof[ok], eps, 1 - eps)
    b = np.clip(base[ok], eps, 1 - eps)
    yy = y[ok]
    ll_m = -(yy * np.log(p) + (1 - yy) * np.log(1 - p)).mean()
    ll_b = -(yy * np.log(b) + (1 - yy) * np.log(1 - b)).mean()
    return float(ll_b - ll_m)


def _dll_kfeat(X5, y, fold):
    """Tier-2 exact-refit 5-feature logistic OOF dll (the registered family recipe)."""
    from sklearn.linear_model import LogisticRegression
    eps = 1e-4
    oof = np.full(len(y), np.nan)
    base = np.empty(len(y))
    for f in range(kf.NFOLD):
        tr, te = fold != f, fold == f
        if te.sum() == 0 or len(np.unique(y[tr])) < 2:
            continue
        m = LogisticRegression(C=1.0, max_iter=300)
        m.fit(X5[tr], y[tr])
        oof[te] = m.predict_proba(X5[te])[:, 1]
        base[te] = y[tr].mean()
    ok = ~np.isnan(oof)
    if ok.sum() < 100:
        return np.nan
    p = np.clip(oof[ok], eps, 1 - eps)
    b = np.clip(base[ok], eps, 1 - eps)
    yy = y[ok]
    return float((-(yy * np.log(b) + (1 - yy) * np.log(1 - b)).mean())
                 - (-(yy * np.log(p) + (1 - yy) * np.log(1 - p)).mean()))


def _perm_labels(arm, lbl, b, outer=None):
    """Donor-permuted (y, net) at permutation b (inner), optionally composed with an
    outer scramble donor array (the truth-generator). -1 donors -> row dropped."""
    p = arm["packs"][lbl]
    donors = arm["donors"][lbl][b]
    if outer is not None:
        o = outer[lbl]
        donors = np.where((donors >= 0) & (o[np.clip(donors, 0, None)] >= 0),
                          o[np.clip(donors, 0, None)], -1)
    keep = donors >= 0
    y = np.where(keep, p["y"][np.clip(donors, 0, None)], -1)
    net = np.where(keep, arm["wnet"][lbl][np.clip(donors, 0, None)], np.nan)
    return y, net, keep


def member_stats(arm, t1, t2, outer=None, n_jobs=8):
    """Real + Bx null statistics for every member under the frozen recipes.
    Returns dict id -> {t, money_real, money_null(B,), dll_real, dll_null(B,)}."""
    from joblib import Parallel, delayed
    res = {}
    # Tier-1: vectorized money; calibrated dll via joblib over (member, perm)
    for m in t1:
        lbl = m["t"]
        score = arm["Xq"][lbl] @ m["w"] if m["w"] is not None else None
        if score is None:                     # canary
            w = np.zeros(38)
            for f_name, v in CANARY_SPEC.items():
                w[kf.FEATURES.index(f_name)] = v
            score = arm["Xq"][lbl] @ w
        thr = np.quantile(score, 0.9)
        top = np.where(score >= thr)[0]
        p = arm["packs"][lbl]
        y0, net0 = p["y"], arm["wnet"][lbl]
        if outer is not None:
            o = outer[lbl]
            ok0 = o >= 0
            y0 = np.where(ok0, p["y"][np.clip(o, 0, None)], -1)
            net0 = np.where(ok0, arm["wnet"][lbl][np.clip(o, 0, None)], np.nan)
        money_real = float(np.nanmean(net0[top]))
        D = arm["donors"][lbl][:, top]
        if outer is not None:
            o = outer[lbl]
            D = np.where((D >= 0) & (o[np.clip(D, 0, None)] >= 0),
                         o[np.clip(D, 0, None)], -1)
        vals = np.where(D >= 0, arm["wnet"][lbl][np.clip(D, 0, None)], np.nan)
        money_null = np.nanmean(vals, axis=1)
        res[m["id"]] = dict(t=lbl, money_real=money_real, money_null=money_null,
                            score=score)
    # Tier-1 calibrated dll (real + null) — parallel over members
    def t1_dll(m):
        lbl = m["t"]
        score = res[m["id"]]["score"]
        p = arm["packs"][lbl]
        y0 = p["y"]
        if outer is not None:
            o = outer[lbl]
            keep = o >= 0
            dll_r = _dll_1d(score[keep], p["y"][np.clip(o, 0, None)][keep], p["fold"][keep])
        else:
            dll_r = _dll_1d(score, y0, p["fold"])
        nulls = np.empty(B)
        for b in range(B):
            yb, _nb, keep = _perm_labels(arm, lbl, b, outer)
            nulls[b] = _dll_1d(score[keep], yb[keep], p["fold"][keep])
        return m["id"], dll_r, nulls
    for mid, dr, dn in Parallel(n_jobs=n_jobs)(delayed(t1_dll)(m) for m in t1):
        res[mid]["dll_real"] = dr
        res[mid]["dll_null"] = dn
    # Tier-2 exact refit
    def t2_all(m):
        lbl = m["t"]
        p = arm["packs"][lbl]
        X5 = arm["Xq"][lbl][:, m["feats"]]
        if outer is not None:
            o = outer[lbl]
            keep = o >= 0
            dll_r = _dll_kfeat(X5[keep], p["y"][np.clip(o, 0, None)][keep], p["fold"][keep])
        else:
            dll_r = _dll_kfeat(X5, p["y"], p["fold"])
        nulls = np.empty(B)
        for b in range(B):
            yb, _nb, keep = _perm_labels(arm, lbl, b, outer)
            nulls[b] = _dll_kfeat(X5[keep], yb[keep], p["fold"][keep])
        return m["id"], lbl, dll_r, nulls
    if t2:
        for mid, lbl, dr, dn in Parallel(n_jobs=n_jobs)(delayed(t2_all)(m) for m in t2):
            res[mid] = dict(t=lbl, money_real=np.nan, money_null=np.full(B, np.nan),
                            dll_real=dr, dll_null=dn)
    for v in res.values():
        v.pop("score", None)
    return res


# ─────────────────────────── the studentized family gate ───────────────────────────

def family_gate(stats: dict, member_ids: list[str], channels=("money", "dll")) -> dict:
    """Studentized maxT over members x channels. fire iff real >= ALPHA_RANK-th largest
    of the B null maxes. Returns thresholds, fire flags, naive counts, per-channel."""
    zs_real, zs_null = [], []
    naive = {c: 0 for c in channels}
    eligible = {c: 0 for c in channels}
    for mid in member_ids:
        s = stats[mid]
        for c in channels:
            r, nb = s.get(f"{c}_real"), s.get(f"{c}_null")
            if r is None or nb is None or not np.isfinite(r):
                continue
            mu, sd = np.nanmean(nb), np.nanstd(nb)
            if not np.isfinite(sd) or sd < 1e-12:
                continue
            eligible[c] += 1
            zs_real.append((r - mu) / sd)
            zs_null.append((nb - mu) / sd)
            if r >= np.nanquantile(nb, 0.95):
                naive[c] += 1
    if not zs_real:
        return dict(fired=False, note="no eligible members")
    ZR = np.array(zs_real)
    ZN = np.vstack(zs_null)                     # (K, B)
    fam_null = np.nanmax(ZN, axis=0)            # per permutation, max over member-channels
    kth = np.sort(fam_null)[-ALPHA_RANK]        # 10th largest of B=200 = q95 (frozen)
    real_max = float(np.nanmax(ZR))
    p = float((1 + (fam_null >= real_max).sum()) / (len(fam_null) + 1))
    return dict(K=len(ZR), real_max=real_max, threshold=float(kth), fired=bool(real_max >= kth),
                p=p, naive=naive, eligible=eligible,
                n_gated_discoveries=int((ZR >= kth).sum()))


def gate_curve(stats, pool_ids, sweep=N_SWEEP):
    ns = sorted({n for n in sweep if n <= len(pool_ids)} | {len(pool_ids)})
    return {str(n): family_gate(stats, pool_ids[:n]) for n in ns}


# ─────────────────────────── arms ───────────────────────────

def outer_scramble(arm, spine, seed):
    """One truth-generator realization: an outer donor-bundle scramble (mirrored
    construction, disjoint seed), returned as donor arrays per t."""
    return {lbl: g.shared_permutations(spine, arm["packs"], B=1, seed=seed)[lbl][0]
            for lbl in GRID5}


def run_residue(arm_name, spine, t1n=400, t2n=100, tag="residue"):
    arm = _prep_arm(spine)
    t1 = tier1_pool()[:t1n] + [dict(id="CANARY", t="14:00", w=None)]
    t2 = tier2_pool()[:t2n]
    stats = member_stats(arm, t1, t2)
    ids = [m["id"] for m in t1 if m["id"] != "CANARY"] + [m["id"] for m in t2]
    out = dict(arm=arm_name,
               curve=gate_curve(stats, ids),
               canary=dict(money_real=stats["CANARY"]["money_real"],
                           dll_real=stats["CANARY"]["dll_real"],
                           money_q95=float(np.nanquantile(stats["CANARY"]["money_null"], .95)),
                           dll_q95=float(np.nanquantile(stats["CANARY"]["dll_null"], .95))))
    json.dump(out, open(os.path.join(OUT, f"{tag}_{arm_name}.json"), "w"),
              indent=1, default=float)
    top = out["curve"][str(max(int(k) for k in out["curve"]))]
    print(f"[{tag}:{arm_name}] K={top.get('K')} naive={top.get('naive')} "
          f"gated={top.get('n_gated_discoveries')} fired={top.get('fired')} p={top.get('p')}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", type=str, default="")          # residue arm on a substrate
    ap.add_argument("--truth", type=int, default=0)          # T truth-generator realizations
    ap.add_argument("--t1", type=int, default=400)
    ap.add_argument("--t2", type=int, default=100)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sub = {}
    if a.arm or a.truth:
        if a.arm in ("A", "asis"):
            spine = pd.read_parquet(g.SPINE_ASIS)
            spine = spine[spine.t_lbl.isin(GRID5)].reset_index(drop=True)
            name = "A_asis"
        elif a.arm in ("B", "clean"):
            spine = pd.read_parquet(g.SPINE_273CLEAN)
            spine = spine[spine.t_lbl.isin(GRID5)].reset_index(drop=True)
            name = "B_clean"
        elif a.arm in ("D", "placebo"):
            base = pd.read_parquet(g.SPINE_273CLEAN)
            base = base[base.t_lbl.isin(GRID5)].reset_index(drop=True)
            filt = g.immune_filtered_spine()
            n_del = base.session_date.nunique() - filt.session_date.nunique()
            rng = np.random.default_rng(_seed("placebo"))
            drop = set(rng.choice(sorted(base.session_date.unique()), size=n_del, replace=False))
            spine = base[~base.session_date.isin(drop)].reset_index(drop=True)
            name = "D_placebo"
        else:
            spine = g.immune_filtered_spine()
            name = "C_filtered"
        # substrate fingerprint (R13)
        td = sorted(set(zip(spine.ticker, spine.session_date)))
        fp = hashlib.sha256(json.dumps(td).encode()).hexdigest()
        print(f"substrate {name}: {len(td)} ticker-days sha256={fp[:16]}...")
        if a.truth:
            arm = _prep_arm(spine)
            t1 = tier1_pool()[:a.t1]
            fires = []
            for r in range(a.truth):
                o = outer_scramble(arm, spine, seed=_seed(f"outer{r}"))
                stats = member_stats(arm, t1, [], outer=o)
                fam = family_gate(stats, [m["id"] for m in t1])
                fires.append(bool(fam["fired"]))
                print(f"  truth-realization {r}: fired={fam['fired']} p={fam['p']:.3f}")
            json.dump(dict(T=a.truth, fires=fires, fwer=float(np.mean(fires))),
                      open(os.path.join(OUT, f"truth_{name}.json"), "w"), indent=1)
            print(f"[truth:{name}] FWER = {np.mean(fires):.3f} over T={a.truth}")
        else:
            run_residue(name, spine, t1n=a.t1, t2n=a.t2)
