"""Compare MoMTrans ablation variants vs baseline + production v3.

Reads:
  data/models/momtrans_v4_predictions.parquet              (default config)
  data/models/momtrans_v4_tabular_only_predictions.parquet (variant 1)
  data/models/momtrans_v4_cls_only_predictions.parquet     (variant 2)
  data/models/momtrans_v4_bigger_predictions.parquet       (variant 3)
  data/polygon_warehouse/derived/ml_v2_walkforward_predictions_v3_tuned_16fold.parquet

Reports rank-discrimination (top-K avg ret_t5 + win rate) + Spearman
correlation across all 5 strategies. Helps pick which variant is the
strongest signal so the Optuna sweep can run on the right baseline.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def rank_discrimination(scores: pd.Series, y_reg: pd.Series,
                          k_pcts=(1, 2, 5, 10, 25)) -> dict:
    df = pd.DataFrame({"score": scores, "y_reg": y_reg}).sort_values(
        "score", ascending=False
    ).reset_index(drop=True)
    n = len(df)
    out = {}
    for k_pct in k_pcts:
        k = int(n * k_pct / 100)
        if k == 0:
            continue
        top = df.head(k)
        out[f"top_{k_pct}pct"] = {
            "n": k,
            "avg_ret_pct": float(top["y_reg"].mean() * 100),
            "win_pct": float((top["y_reg"] > 0).mean() * 100),
        }
    out["spearman"] = float(scores.corr(y_reg, method="spearman"))
    return out


def main() -> int:
    section("MoMTrans ablation comparison vs production v3")

    candidates = {
        "v3-tuned-16f (PROD)": (
            DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet",
            "prob_continuer",
        ),
        "MoMTrans default":   (MODELS / "momtrans_v4_predictions.parquet",
                                 "prob_binary"),
        "MoMTrans tabular-only": (MODELS / "momtrans_v4_tabular_only_predictions.parquet",
                                     "prob_binary"),
        "MoMTrans cls-only":  (MODELS / "momtrans_v4_cls_only_predictions.parquet",
                                 "prob_binary"),
        "MoMTrans bigger":    (MODELS / "momtrans_v4_bigger_predictions.parquet",
                                 "prob_binary"),
    }

    results = {}
    for name, (path, score_col) in candidates.items():
        if not path.exists():
            print(f"  SKIP {name}: {path.name} missing")
            continue
        df = pd.read_parquet(path)
        if score_col not in df.columns:
            print(f"  SKIP {name}: column '{score_col}' missing")
            continue
        df = df.dropna(subset=[score_col, "y_reg"])
        results[name] = rank_discrimination(df[score_col], df["y_reg"])

    section("Rank-discrimination — top-K avg ret_t5 (and win rate)")
    print(f"  {'strategy':<28} {'top1%':>15} {'top2%':>15} {'top5%':>15} "
          f"{'top10%':>15} {'spearman':>10}")
    print("  " + "-" * 100)
    for name, r in results.items():
        def fmt_top(k):
            d = r.get(f"top_{k}pct")
            if d is None:
                return "n/a"
            return f"{d['avg_ret_pct']:+5.2f}%/{d['win_pct']:.0f}%"
        print(f"  {name:<28} {fmt_top(1):>15} {fmt_top(2):>15} "
              f"{fmt_top(5):>15} {fmt_top(10):>15} {r['spearman']:>+10.4f}")

    # Find the best MoMTrans variant by top-1% avg
    momtrans_results = {k: v for k, v in results.items() if "MoMTrans" in k}
    best = None  # hoist so the prod-comparison block below can reference it safely
    if momtrans_results:
        best = max(momtrans_results.items(),
                   key=lambda kv: kv[1].get("top_1pct", {}).get("avg_ret_pct", -1e9))
        print(f"\n  WINNER variant: {best[0]} (top-1% = {best[1]['top_1pct']['avg_ret_pct']:+.2f}%)")
    prod = results.get("v3-tuned-16f (PROD)")
    if prod and best is not None:
        best_top1 = best[1]["top_1pct"]["avg_ret_pct"]
        prod_top1 = prod["top_1pct"]["avg_ret_pct"]
        gap = prod_top1 - best_top1
        gap_pct = gap / abs(prod_top1) * 100 if prod_top1 != 0 else 0
        print(f"  Gap vs v3 prod top-1%: {gap:+.2f}pp ({gap_pct:+.1f}% relative)")

    out = MODELS / "momtrans_v4_ablation_comparison.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
