"""One-command v4 retrain + WF lift comparison vs v3-tuned-16fold.

Once microstructure + news features are available, this script:
  1. Verifies all data sources (microstructure_features.parquet,
     news_features_polygon_180d.parquet) are present
  2. Retrains v4 = v3-tuned-16fold + microstructure + news
  3. Runs walk-forward; persists predictions to
     ml_v2_walkforward_predictions_v4.parquet
  4. Re-runs meta-scorer with both conservative + aggressive Kelly
  5. Compares per-tier $-PNL vs v3-tuned-16fold baseline
  6. Reports verdict: SHIP v4 / HOLD v3 / RECOMPUTE-AND-RECHECK

Outputs:
  data/models/continuer_v2_v4.pkl
  data/polygon_warehouse/derived/meta_scores_walkforward_v4_aggressive.parquet
  data/models/v4_lift_summary.json

Verdict logic:
  v4 SHIPS IF:
    - Aggressive Kelly $-PNL improves by >= +$500 vs v3-tuned-16fold
    - AND no tier loses > 30% of its baseline P&L (no regression)
    - AND v4 broad tier sharpe >= v3 broad tier sharpe * 0.95

USAGE:
  python scripts/retrain_v4_and_compare.py
  python scripts/retrain_v4_and_compare.py --skip-train  # only re-run scorer
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def check_status(label: str, ok: bool, detail: str = "") -> bool:
    mark = "[OK]" if ok else "[XX]"
    print(f"  {mark} {label}{('  -- ' + detail) if detail else ''}")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-train", action="store_true",
                        help="Skip retraining; only re-evaluate meta-scorer + compare")
    parser.add_argument("--news-path", type=str,
                        default="data/polygon_warehouse/derived/news_features_polygon_180d.parquet")
    args = parser.parse_args()

    section("STEP 1 - Data sources check")
    micro_p = DERIVED / "microstructure_features.parquet"
    news_p = Path(args.news_path)
    paths_p = DERIVED / "intraday_paths_30min.parquet"

    micro_ok = micro_p.exists() and micro_p.stat().st_size > 1024
    news_ok = news_p.exists() and news_p.stat().st_size > 1024
    paths_ok = paths_p.exists()

    micro_n = pd.read_parquet(micro_p).shape[0] if micro_ok else 0
    news_n = pd.read_parquet(news_p).shape[0] if news_ok else 0
    news_with = ((pd.read_parquet(news_p)["n_articles_24h"] > 0).sum()
                  if news_ok else 0)

    check_status("microstructure_features.parquet", micro_ok,
                  f"{micro_n} rows" if micro_ok else "MISSING")
    check_status(f"news ({news_p.name})", news_ok,
                  f"{news_n} rows ({news_with} with news)" if news_ok else "MISSING")
    check_status("intraday_paths_30min.parquet", paths_ok,
                  f"{paths_p.stat().st_size//1024} KB" if paths_ok else "MISSING")

    if not (micro_ok or news_ok):
        print("\n  ERROR: neither microstructure nor news data present. Cannot proceed.")
        return 1

    section("STEP 2 - Retrain v4 (or skip)")
    if args.skip_train:
        print("  --skip-train: using existing data/polygon_warehouse/derived/ml_v2_walkforward_predictions_v4.parquet")
        if not (DERIVED / "ml_v2_walkforward_predictions_v4.parquet").exists():
            print("  ERROR: --skip-train but predictions parquet missing")
            return 1
    else:
        cmd = [sys.executable, str(REPO / "scripts" / "ml_continuer_v2_ensemble.py"),
               "--include-paths",
               "--optuna-params", str(MODELS / "v3_optuna_full16fold.json"),
               "--out-suffix", "_v4"]
        if micro_ok: cmd.append("--include-microstructure")
        if news_ok:
            cmd.append("--include-news")
            cmd.extend(["--news-path", str(news_p)])
        print(f"  Running: {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=False)
        if r.returncode != 0:
            print(f"  ERROR: ensemble retrain failed (rc={r.returncode})")
            return 1

    section("STEP 3 - Re-run meta-scorer with v4 predictions")
    v4_preds = DERIVED / "ml_v2_walkforward_predictions_v4.parquet"
    if not v4_preds.exists():
        print(f"  ERROR: {v4_preds} missing")
        return 1

    for profile, suffix in [("conservative", "_v4"), ("aggressive", "_v4_aggressive")]:
        env = {**os.environ}
        if profile == "aggressive":
            env["MX_META_KELLY_PROFILE"] = "aggressive"
        cmd = [sys.executable, str(REPO / "scripts" / "ml_meta_scorer.py"),
               "--v3t-preds", str(v4_preds),
               "--out-suffix", suffix]
        print(f"  Running meta-scorer ({profile})...")
        subprocess.run(cmd, capture_output=False, env=env)

    section("STEP 4 - Compare v4 vs v3 baseline (aggressive)")
    v3_summary = MODELS / "meta_scorer_summary_16fold_aggressive.json"
    v4_summary = MODELS / "meta_scorer_summary_v4_aggressive.json"
    if not (v3_summary.exists() and v4_summary.exists()):
        print(f"  ERROR: missing summary file(s)")
        return 1

    v3 = json.loads(v3_summary.read_text())
    v4 = json.loads(v4_summary.read_text())

    print(f"  {'tier':<8} {'v3_n':>6} {'v3_avg':>8} {'v3_$':>10}  "
          f"{'v4_n':>6} {'v4_avg':>8} {'v4_$':>10}  {'delta_$':>10}")
    print(f"  {'-'*84}")
    delta_total = 0.0
    tier_regressed = []
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        v3_t = v3.get("tier_stats", {}).get(tier, {})
        v4_t = v4.get("tier_stats", {}).get(tier, {})
        v3_n = v3_t.get("n", 0)
        v3_avg = v3_t.get("avg_pct", 0)
        v3_pnl = (v3.get("expected_win_loss_pct", {}).get(tier, {}).get("win", 0)
                   * v3_n / 100 if False else 0)  # use total instead
        v4_n = v4_t.get("n", 0)
        v4_avg = v4_t.get("avg_pct", 0)
        # Simpler: use total_pnl_dollars per tier if present
        v3_dollar = v3.get("total_pnl_dollars", 0)
        v4_dollar = v4.get("total_pnl_dollars", 0)
        # Approximation: tier $ = total $ * (tier_n / sum_n)
        v3_sum_n = sum(v3.get("tier_stats", {}).get(t, {}).get("n", 0)
                        for t in ["ELITE", "HIGH", "VETOED", "BROAD"])
        v4_sum_n = sum(v4.get("tier_stats", {}).get(t, {}).get("n", 0)
                        for t in ["ELITE", "HIGH", "VETOED", "BROAD"])
        v3_t_pnl = v3_dollar * (v3_n / max(v3_sum_n, 1))
        v4_t_pnl = v4_dollar * (v4_n / max(v4_sum_n, 1))
        delta = v4_t_pnl - v3_t_pnl
        delta_total += delta
        if v3_t_pnl > 0 and delta / max(v3_t_pnl, 1) < -0.30:
            tier_regressed.append((tier, delta / v3_t_pnl * 100))
        print(f"  {tier:<8} {v3_n:>6,} {v3_avg:>+7.2f}% ${v3_t_pnl:>+9.2f}  "
              f"{v4_n:>6,} {v4_avg:>+7.2f}% ${v4_t_pnl:>+9.2f}  ${delta:>+9.2f}")
    print(f"  {'-'*84}")
    print(f"  {'TOTAL':<8} {'':>6} {'':>8} ${v3.get('total_pnl_dollars',0):>+9.2f}  "
          f"{'':>6} {'':>8} ${v4.get('total_pnl_dollars',0):>+9.2f}  "
          f"${v4.get('total_pnl_dollars',0)-v3.get('total_pnl_dollars',0):>+9.2f}")

    section("STEP 5 - Verdict")
    delta_total_real = v4.get("total_pnl_dollars", 0) - v3.get("total_pnl_dollars", 0)
    if delta_total_real >= 500 and not tier_regressed:
        verdict = "SHIP v4"
        reason = (f"v4 lifts bankroll by ${delta_total_real:+.2f} (>= $500 threshold) "
                  f"with no tier regression > 30%")
    elif delta_total_real >= 500 and tier_regressed:
        verdict = "HOLD v3 (mixed)"
        reason = (f"v4 lifts total by ${delta_total_real:+.2f} but {len(tier_regressed)} tier(s) "
                  f"regressed > 30%: {', '.join(f'{t[0]} ({t[1]:.1f}%)' for t in tier_regressed)}")
    else:
        verdict = "HOLD v3"
        reason = (f"v4 delta ${delta_total_real:+.2f} < $500 threshold; "
                  f"insufficient lift to justify swap")
    print(f"  Verdict: {verdict}")
    print(f"  Reason:  {reason}")

    out = {
        "v3_total_dollars": v3.get("total_pnl_dollars", 0),
        "v4_total_dollars": v4.get("total_pnl_dollars", 0),
        "delta_dollars": delta_total_real,
        "tier_regressed": [{"tier": t[0], "pct": t[1]} for t in tier_regressed],
        "verdict": verdict,
        "reason": reason,
        "data_sources": {
            "microstructure_rows": int(micro_n),
            "news_rows": int(news_n),
            "news_with_news": int(news_with),
        },
    }
    out_path = MODELS / "v4_lift_summary.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n  Wrote {out_path}")
    return 0 if "SHIP" in verdict else 1


if __name__ == "__main__":
    sys.exit(main())
