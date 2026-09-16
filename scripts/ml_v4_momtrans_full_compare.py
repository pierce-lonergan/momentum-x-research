"""All-up MoMTrans comparison — every variant ever trained vs production.

Reads predictions from every momtrans_v4*_predictions.parquet under
data/models, plus production v3-tuned-16f baseline. Prints a unified
ranking table sorted by Spearman ρ.

Includes the BEST sweep trial (loaded from
momtrans_tabular_sweep_summary.json's best_params + matching predictions
file) and the tier-specialist UNION cascade ($-PNL evaluated under the
same Aggressive-Kelly framework as D281).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

KELLY_CAPS = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}
BANKROLL = 10_000.0


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly(p, w, ew, el, cap):
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    return float(min(f_star * float(np.exp(-2 * w)), cap))


def rank_stats(scores: pd.Series, y_reg: pd.Series) -> dict:
    df = pd.DataFrame({"s": scores, "y": y_reg}).dropna().sort_values(
        "s", ascending=False
    ).reset_index(drop=True)
    n = len(df)
    out = {"n_total": n, "spearman": float(df["s"].corr(df["y"], method="spearman"))}
    for k_pct in (1, 2, 5, 10):
        k = max(int(n * k_pct / 100), 1)
        top = df.head(k)
        out[f"top{k_pct}pct_n"] = k
        out[f"top{k_pct}pct_avg"] = float(top["y"].mean() * 100)
        out[f"top{k_pct}pct_win"] = float((top["y"] > 0).mean() * 100)
    return out


def main():
    section("All-up MoMTrans v4 comparison vs production")

    # 1. Production baseline
    prod = pd.read_parquet(DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet")
    prod_stats = rank_stats(prod["prob_continuer"], prod["y_reg"])

    # 2. All MoMTrans variants
    variants = []
    for p in sorted(MODELS.glob("momtrans_v4*_predictions.parquet")):
        # Skip per-tier specialist files (handled separately)
        if "_tier_" in p.name and re.search(r"_tier_(ELITE|HIGH|VETOED|BROAD)_", p.name):
            continue
        df = pd.read_parquet(p)
        if df.empty or "prob_binary" not in df.columns:
            continue
        label = (
            p.stem.replace("momtrans_v4_", "")
                  .replace("_predictions", "")
                  .replace("momtrans_v4", "default")
        ) or "default"
        s = rank_stats(df["prob_binary"], df["y_reg"])
        variants.append((label, s))

    # 3. Best sweep trial
    sweep_path = MODELS / "momtrans_tabular_sweep_summary.json"
    sweep_best = None
    if sweep_path.exists():
        sweep = json.loads(sweep_path.read_text())
        sweep_best_n = max(
            (h for h in sweep["history"] if h["state"] == "COMPLETE"),
            key=lambda h: h.get("value") or -1,
            default=None,
        )
        if sweep_best_n is not None:
            best_pred_path = MODELS / f"momtrans_v4_sweep_{sweep_best_n['number']}_predictions.parquet"
            if best_pred_path.exists():
                df = pd.read_parquet(best_pred_path)
                sweep_best = ("sweep_best (#{} {})".format(
                    sweep_best_n["number"],
                    {k: round(v, 4) if isinstance(v, float) else v for k, v in sweep_best_n["params"].items()}
                ), rank_stats(df["prob_binary"], df["y_reg"]))

    # 4. Tier specialists — cascade $-PNL
    tier_paths = {}
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        p = MODELS / f"momtrans_v4_tier_{tier}_predictions.parquet"
        if p.exists():
            tier_paths[tier] = p

    # PRINT TABLE
    print()
    print(f"  {'variant':<45} {'spearman':>10} {'top1%_avg':>12} {'top1%_win':>10} "
          f"{'top5%_avg':>12} {'n_total':>8}")
    print("  " + "-" * 100)

    rows = [("v3-tuned-16f (PROD)", prod_stats)]
    rows.extend(variants)
    if sweep_best:
        rows.append(sweep_best)
    rows.sort(key=lambda r: r[1].get("spearman") or -1, reverse=True)
    for label, s in rows:
        print(f"  {label:<45} {s['spearman']:>+9.4f}  "
              f"{s['top1pct_avg']:>+10.2f}%   {s['top1pct_win']:>7.1f}%  "
              f"{s['top5pct_avg']:>+10.2f}%   {s['n_total']:>8,}")

    # Tier-specialist UNION cascade $-PNL (only if all 4 specialists trained)
    # Hoist tier-cascade vars to function scope so the JSON-summary block
    # below doesn't trigger pyright "possibly unbound" warnings when
    # tier_paths has fewer than 4 specialists (no cascade computed).
    prod_pnl: float = 0.0
    mt_pnl: float = 0.0
    prod_per_tier: dict = {}
    mt_per_tier: dict = {}

    if len(tier_paths) == 4:
        section("MoMTrans tier-specialist UNION cascade vs production $-PNL")
        # Join all 4 specialist predictions on (d0, ticker, fold)
        base = pd.read_parquet(tier_paths["BROAD"])[["d0", "ticker", "fold",
                                                          "y_reg", "prob_binary"]].rename(
            columns={"prob_binary": "prob_specialist_BROAD"}
        )
        base["d0"] = pd.to_datetime(base["d0"])
        for tier in ("VETOED", "HIGH", "ELITE"):
            df_t = pd.read_parquet(tier_paths[tier])[["d0", "ticker", "fold", "prob_binary"]]
            df_t["d0"] = pd.to_datetime(df_t["d0"])
            base = base.merge(
                df_t.rename(columns={"prob_binary": f"prob_specialist_{tier}"}),
                on=["d0", "ticker", "fold"], how="inner",
            )

        # Conformal width proxy: 0.5 (constant, since MoMTrans doesn't produce one)
        base["conformal_width"] = 0.5

        # Join Ising mag_label
        ising_path = DERIVED / "ising_daily.parquet"
        con = duckdb.connect()
        con.sql(f"""CREATE TABLE i AS SELECT *,
            AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
            AS mag_5d FROM read_parquet('{ising_path.as_posix()}')""")
        mag_df = con.sql("""SELECT d AS d0, mag_5d,
            CASE WHEN mag_5d < -0.05 THEN 'LO' WHEN mag_5d > 0.05 THEN 'HI' ELSE 'MID' END
            AS mag_label FROM i""").df()
        mag_df["d0"] = pd.to_datetime(mag_df["d0"])
        base = base.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])

        # Production baseline $-PNL
        prod_with_mag = prod.copy()
        prod_with_mag["d0"] = pd.to_datetime(prod_with_mag["d0"])
        prod_with_mag = prod_with_mag.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
        yreg = prod_with_mag["y_reg"].clip(-0.5, 1.0).values
        prob = prod_with_mag["prob_continuer"].values
        width = prod_with_mag["conformal_width"].values
        mag = prod_with_mag["mag_label"].values
        is_hi_mid = np.isin(mag, ["HI", "MID"])
        is_mid = mag == "MID"
        for tier_name, mask in [
            ("ELITE", (prob >= 0.60) & is_hi_mid),
            ("HIGH", (prob >= 0.50) & (prob < 0.60) & is_hi_mid),
            ("VETOED", (prob >= 0.30) & (prob < 0.50) & is_mid),
            ("BROAD", (prob >= 0.30) & (prob < 0.50) & is_hi_mid),
        ]:
            idx = np.where(mask)[0]
            if not len(idx): continue
            ew, el = TIER_EW_EL_PCT[tier_name]; cap = KELLY_CAPS[tier_name]
            pnl = sum(BANKROLL * kelly(prob[i], width[i], ew, el, cap) * yreg[i] for i in idx)
            prod_pnl += pnl
            prod_per_tier[tier_name] = (len(idx), float(pnl))

        # MoMTrans tier UNION cascade $-PNL
        # Use the per-specialist threshold tuning from D281 grid-search:
        # ELITE 0.40, HIGH 0.40, VETOED 0.40, BROAD 0.50
        thresholds = {"ELITE": 0.40, "HIGH": 0.40, "VETOED": 0.40, "BROAD": 0.50}
        yreg_t = base["y_reg"].clip(-0.5, 1.0).values
        width_t = base["conformal_width"].values
        mag_t = base["mag_label"].values
        is_hi_mid_t = np.isin(mag_t, ["HI", "MID"])
        is_mid_t = mag_t == "MID"
        sp = {tier: base[f"prob_specialist_{tier}"].values
              for tier in ("ELITE", "HIGH", "VETOED", "BROAD")}

        elite_t = (sp["ELITE"] >= thresholds["ELITE"]) & is_hi_mid_t
        high_t = (sp["HIGH"] >= thresholds["HIGH"]) & is_hi_mid_t & ~elite_t
        vetoed_t = (sp["VETOED"] >= thresholds["VETOED"]) & is_mid_t & ~elite_t & ~high_t
        broad_t = (sp["BROAD"] >= thresholds["BROAD"]) & is_hi_mid_t & ~elite_t & ~high_t & ~vetoed_t

        for tier_name, mask, prob_arr in [
            ("ELITE", elite_t, sp["ELITE"]),
            ("HIGH", high_t, sp["HIGH"]),
            ("VETOED", vetoed_t, sp["VETOED"]),
            ("BROAD", broad_t, sp["BROAD"]),
        ]:
            idx = np.where(mask)[0]
            if not len(idx):
                mt_per_tier[tier_name] = (0, 0.0)
                continue
            ew, el = TIER_EW_EL_PCT[tier_name]; cap = KELLY_CAPS[tier_name]
            # Floor proba at 0.31 if specialist asserts membership but binary <0.30
            prob_eff = np.maximum(prob_arr, 0.31)
            pnl = sum(BANKROLL * kelly(prob_eff[i], width_t[i], ew, el, cap) * yreg_t[i] for i in idx)
            mt_pnl += pnl
            mt_per_tier[tier_name] = (int(len(idx)), float(pnl))

        print(f"\n  {'tier':<8} {'prod_n':>6} {'prod_$':>10} {'mt_n':>6} {'mt_$':>10} {'delta_$':>10}")
        print("  " + "-" * 60)
        for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
            pn, pp = prod_per_tier.get(tier, (0, 0.0))
            mn, mp = mt_per_tier[tier]
            print(f"  {tier:<8} {pn:>6,} ${pp:>+9,.2f} {mn:>6,} ${mp:>+9,.2f} ${mp - pp:>+8,.2f}")
        print(f"  {'TOTAL':<8}        ${prod_pnl:>+9,.2f}        ${mt_pnl:>+9,.2f} ${mt_pnl - prod_pnl:>+8,.2f}")

    # Persist summary
    summary = {
        "production_baseline": {"label": "v3-tuned-16f (PROD)", "stats": prod_stats},
        "all_variants": [{"label": l, "stats": s} for l, s in rows[1:]],
    }
    if len(tier_paths) == 4:
        summary["tier_cascade_pnl"] = {
            "production": float(prod_pnl),
            "momtrans_tier_cascade": float(mt_pnl),
            "delta": float(mt_pnl - prod_pnl),
            "per_tier_prod": prod_per_tier,
            "per_tier_momtrans": mt_per_tier,
        }
    out = MODELS / "momtrans_v4_full_compare.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
