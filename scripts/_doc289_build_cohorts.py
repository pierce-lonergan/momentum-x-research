"""DOC 289 STAGE 0 — build the COHORT FIELD dataset + integrity report.

THE REFRAME (never done here): every prior experiment asked "will ticker X continue?" (per-name, dead).
This asks whether the edge is RELATIONAL — determined by a name's POSITION within its morning cohort
(the set of low-floats that gapped together and compete for one finite pool of retail attention), not by
its absolute features. Stage 0 builds, per session, the pre-decision cross-section (absolute + within-
cohort RELATIVE features) and the leakage-safe intraday outcome from the AUTHORITATIVE minute-bar
warehouse (the feature-file current_price is stale 57% of the time — proven — so outcomes come from bars).

Leakage discipline: decision time = each name's FIRST morning snapshot (its pre-decision state); absolute
features are read at that snapshot only; the outcome uses ONLY bars at/after that timestamp; relative
features are functions of the cohort's absolute features at decision time. No future info enters features.

Outputs:
  data/research/doc289/cohorts.jsonl   — one row per (session, ticker): absolute + relative features + outcome
  data/research/doc289/stage0_report.json — coverage, cohort sizes, winner defn, leakage/tz sanity, regime split
"""
from __future__ import annotations
import json, math, os, sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT / "scripts"))

FEATURES = _ROOT / "data" / "features"
OUTDIR = _ROOT / "data" / "research" / "doc289"
OUTDIR.mkdir(parents=True, exist_ok=True)

MORNING_MAX_HOUR = 10        # decision snapshot must be <= 10:30 ET (a coherent morning cohort)
MORNING_MAX_MIN_AT_10 = 30
FWD_WIN_MIN = 60.0          # continuation window: peak run over the first 60 min after decision
MIN_COHORT = 5             # a session needs >= this many usable names to be a cohort


def _f(x, d=None):
    try:
        if x is None:
            return d
        return float(x)
    except Exception:
        return d


def decision_snapshot(snaps: list[dict]) -> dict | None:
    """The name's earliest morning snapshot (pre-decision state)."""
    m = [r for r in snaps if r.get("timestamp") and (
        (r.get("hour_et") == 9) or (r.get("hour_et") == MORNING_MAX_HOUR and (r.get("minute_et") or 0) <= MORNING_MAX_MIN_AT_10))]
    if not m:
        return None
    m.sort(key=lambda r: r["timestamp"])
    return m[0]


def continuation(con, ticker, ts0):
    """Leakage-safe intraday outcome from the warehouse: peak forward return over the first FWD_WIN_MIN
    and return-to-close, measured from the first bar at/after ts0. Returns None if no bars."""
    import _doc280_posture_backtest as B
    lb = B._load_local_bars(con, ticker, ts0)
    if not lb or not lb["bars"] or not lb["close_px"]:
        return None
    bars = lb["bars"]
    # entry reference = close of the first bar at/after decision (off >= 0); fall back to first bar
    entry = None
    for (off, lo, op, hi, cl, vol) in bars:
        if off >= 0:
            entry = op if op > 0 else cl
            break
    if entry is None:
        entry = bars[0][2] if bars[0][2] > 0 else bars[0][4]
    if not entry or entry <= 0:
        return None
    win = [b for b in bars if 0 <= b[0] <= FWD_WIN_MIN]
    if not win:
        win = [b for b in bars if b[0] >= 0][:1]
    peak_hi = max((b[3] for b in win), default=entry)
    trough_lo = min((b[1] for b in win), default=entry)
    return {
        "entry_px": entry,
        "peak_run": peak_hi / entry - 1.0,          # max upside over the window (the "did it run")
        "max_draw": trough_lo / entry - 1.0,
        "ret_close": lb["close_px"] / entry - 1.0,
        "n_bars": len(bars),
    }


def zscores(vals):
    xs = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(xs) < 2:
        return [0.0 for _ in vals]
    mu = sum(xs) / len(xs)
    sd = (sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 or 1.0
    return [((v - mu) / sd if v is not None else 0.0) for v in vals]


def ranks(vals):
    """fractional rank in [0,1] within the cohort (1 = largest); None -> 0.5."""
    idx = [(i, v) for i, v in enumerate(vals) if v is not None]
    out = [0.5] * len(vals)
    if not idx:
        return out
    idx.sort(key=lambda t: t[1])
    n = len(idx)
    for r, (i, _v) in enumerate(idx):
        out[i] = (r + 0.5) / n
    return out


def build():
    import duckdb
    import fill_model_backtest as F
    con = duckdb.connect(); con.execute("SET threads=4")

    files = sorted(FEATURES.glob("features_2026-*.jsonl"))
    all_rows = []
    report = {"sessions_total": len(files), "sessions_used": 0, "sessions_dropped": [],
              "names_seen": 0, "names_with_outcome": 0, "cohort_sizes": [], "fwd_win_min": FWD_WIN_MIN}
    sanity = {"live_snap_checked": 0, "live_snap_agree": 0}  # warehouse-outcome vs live-snapshot cross-check

    for fp in files:
        date = fp.stem.replace("features_", "")
        rows = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        byt = defaultdict(list)
        for r in rows:
            byt[r["ticker"]].append(r)

        members = []
        for tk, snaps in byt.items():
            r0 = decision_snapshot(snaps)
            if not r0:
                continue
            report["names_seen"] += 1
            try:
                ts0 = F._parse_ts(r0["timestamp"])
            except Exception:
                continue
            out = continuation(con, tk, ts0)
            if out is None:
                continue
            report["names_with_outcome"] += 1
            fl = _f(r0.get("float_shares"))
            pmv = _f(r0.get("premarket_volume"))
            members.append({
                "date": date, "ticker": tk, "ts0": r0["timestamp"],
                # absolute (pre-decision) features
                "gap_pct": _f(r0.get("gap_pct"), 0.0),
                "rvol": _f(r0.get("rvol"), 0.0),
                "float_shares": fl,
                "market_cap": _f(r0.get("market_cap")),
                "premarket_volume": pmv,
                "price": _f(r0.get("current_price")) or out["entry_px"],
                "has_news": 1.0 if r0.get("has_news_catalyst") else 0.0,
                "mfcs": _f(r0.get("mfcs")),
                "float_rotation": (pmv / fl) if (pmv and fl and fl > 0) else None,
                # outcome
                "peak_run": out["peak_run"], "max_draw": out["max_draw"], "ret_close": out["ret_close"],
                "n_bars": out["n_bars"],
            })
            # cross-check: for names with a LIVE later snapshot, does warehouse peak_run agree in sign/scale?
            live_px = [(_f(r.get("current_price")), r["timestamp"]) for r in snaps
                       if _f(r.get("current_price")) and r.get("timestamp") and r["timestamp"] > r0["timestamp"]]
            if live_px:
                base = out["entry_px"]
                snap_hi = max(p for p, _t in live_px)
                snap_run = snap_hi / base - 1.0 if base else 0.0
                sanity["live_snap_checked"] += 1
                if (snap_run > 0.02) == (out["peak_run"] > 0.02) or abs(snap_run - out["peak_run"]) < 0.03:
                    sanity["live_snap_agree"] += 1

        if len(members) < MIN_COHORT:
            report["sessions_dropped"].append({"date": date, "n": len(members)})
            continue
        report["sessions_used"] += 1
        report["cohort_sizes"].append(len(members))

        # within-cohort RELATIVE features (functions of the cohort's absolute features at decision time)
        n = len(members)
        for feat in ["gap_pct", "rvol", "float_shares", "premarket_volume", "price", "mfcs", "float_rotation"]:
            vals = [m.get(feat) for m in members]
            zs = zscores(vals)
            rk = ranks(vals)
            for m, z, r in zip(members, zs, rk):
                m[f"z_{feat}"] = z
                m[f"rank_{feat}"] = r
        # cohort-geometry relatives: standardized distance to the cohort centroid + cohort dispersion
        geom_feats = ["z_gap_pct", "z_rvol", "z_float_shares", "z_premarket_volume", "z_price", "z_float_rotation"]
        for m in members:
            m["centroid_dist"] = math.sqrt(sum(m[g] ** 2 for g in geom_feats))  # how "outlier" in the cohort
        # cohort context (same for all names; lets the model condition on cohort temperature)
        disp = {}
        for feat in ["gap_pct", "rvol", "float_rotation"]:
            xs = [m[feat] for m in members if m.get(feat) is not None]
            disp[f"cohort_sd_{feat}"] = (sum((x - sum(xs)/len(xs))**2 for x in xs)/len(xs))**0.5 if len(xs) > 1 else 0.0
        # outcome ranks within cohort + the WINNER label (argmax peak_run)
        pr = [m["peak_run"] for m in members]
        pr_rank = ranks(pr)
        win_idx = max(range(n), key=lambda i: pr[i])
        for i, m in enumerate(members):
            m["cohort_size"] = n
            m.update(disp)
            m["peak_run_rank"] = pr_rank[i]           # continuation rank in cohort (0..1)
            m["is_winner"] = 1 if i == win_idx else 0  # relational target: did THIS name win its cohort
            m["is_top3"] = 1 if pr_rank[i] >= (1.0 - 3.0 / n) else 0
        all_rows.extend(members)

    con.close()

    (OUTDIR / "cohorts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in all_rows) + "\n", encoding="utf-8")

    sizes = report["cohort_sizes"]
    report["names_rows_emitted"] = len(all_rows)
    report["outcome_coverage_pct"] = round(100 * report["names_with_outcome"] / max(report["names_seen"], 1), 1)
    report["cohort_size_median"] = sorted(sizes)[len(sizes)//2] if sizes else 0
    report["cohort_size_min"] = min(sizes) if sizes else 0
    report["cohort_size_max"] = max(sizes) if sizes else 0
    report["warehouse_snapshot_agree_pct"] = round(
        100 * sanity["live_snap_agree"] / max(sanity["live_snap_checked"], 1), 1)
    report["warehouse_snapshot_checked"] = sanity["live_snap_checked"]
    # regime split (chronological halves) for cross-regime testing in Stage A
    dates = sorted({r["date"] for r in all_rows})
    half = len(dates) // 2
    report["regime_split_date"] = dates[half] if dates else None
    report["regime_early_sessions"] = half
    report["regime_late_sessions"] = len(dates) - half
    (OUTDIR / "stage0_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps({k: report[k] for k in [
        "sessions_total", "sessions_used", "names_seen", "names_with_outcome", "outcome_coverage_pct",
        "names_rows_emitted", "cohort_size_median", "cohort_size_min", "cohort_size_max",
        "warehouse_snapshot_agree_pct", "warehouse_snapshot_checked", "regime_split_date"]}, indent=2))


if __name__ == "__main__":
    build()
