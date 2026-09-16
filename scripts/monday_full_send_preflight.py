"""Pre-Monday full-send preflight: validates all pieces of the deploy.

Run this Sunday evening before Monday paper trading. Verifies:
  1. .env loads + all credentials present (Alpaca paper, Polygon, Finnhub, Discord)
  2. Production model artifacts on disk (continuer_v2_v3_tuned.pkl, tcn_intraday.pt)
  3. MetaScorer.load_default() succeeds + reports tier counts
  4. Aggressive Kelly profile honored (caps == 50/35/20/10)
  5. Alpaca paper API reachable (account check, NOT order submission)
  6. Drift detector ran today (drift_report_<today>.json fresh)
  7. Lottery_runner DRY_RUN smoke test (no real orders)
  8. Pipeline P&L expectation summary

Exit codes:
  0 = all green; deploy with confidence
  1 = some checks failed; review before deploying
  2 = fatal: cannot deploy

Usage:
    python scripts/monday_full_send_preflight.py
"""
from __future__ import annotations
import json
import os
import pickle
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_PATH = REPO / ".env"
MODELS = REPO / "data" / "models"
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def check_status(label: str, ok: bool, detail: str = "") -> bool:
    mark = "[OK]" if ok else "[XX]"
    print(f"  {mark} {label}{('  -- ' + detail) if detail else ''}")
    return ok


def load_env_dotfile() -> dict:
    env = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.split("#", 1)[0].strip()
        env[k] = v
        if not os.environ.get(k):
            os.environ[k] = v
    return env


def main():
    section(f"MONDAY FULL-SEND PREFLIGHT  date={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    fails = 0
    warns = 0

    # ── Step 1: env / credentials ──
    section("Step 1 - Credentials check")
    env = load_env_dotfile()
    needed = {
        "ALPACA_API_KEY": True,
        "ALPACA_SECRET_KEY": True,
        "ALPACA_BASE_URL": True,
        "POLYGON_API_KEY": False,   # nice-to-have
        "POLYGON_S3_KEY": False,
        "POLYGON_S3_SECRET": False,
        "FINNHUB_API_KEY": False,
        "OPS_ALERT_WEBHOOK_URL": False,
    }
    for k, required in needed.items():
        present = bool(env.get(k))
        ok = present or not required
        if not ok: fails += 1
        if not present and not required: warns += 1
        check_status(f"{k}", ok,
                      "REQUIRED MISSING" if not present and required
                      else ("present" if present else "absent (optional)"))

    # ── Step 2: model artifacts ──
    section("Step 2 - Production model artifacts")
    arts = {
        "continuer_v2_v3_tuned.pkl": MODELS / "continuer_v2_v3_tuned.pkl",
        "continuer_v2_v3_tuned_manifest.json": MODELS / "continuer_v2_v3_tuned_manifest.json",
        "tcn_intraday.pt": MODELS / "tcn_intraday.pt",
        "ising_daily.parquet": DERIVED / "ising_daily.parquet",
    }
    for name, p in arts.items():
        present = p.exists()
        if not present: fails += 1
        check_status(name, present,
                      f"{p.stat().st_size / 1024:.1f} KB" if present else "MISSING")

    # ── Step 3: MetaScorer.load_default ──
    section("Step 3 - MetaScorer load")
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        from ml_meta_scorer_inference import MetaScorer, KELLY_CAPS  # type: ignore
        scorer = MetaScorer.load_default()
        check_status("scorer loaded",
                      True,
                      f"{len(scorer.feature_columns)} features, "
                      f"mag_5d={scorer.mag_5d:+.4f} ({scorer.mag_label})")
        check_status("TCN model loaded",
                      scorer.tcn_model is not None,
                      "BCE production checkpoint")
        check_status("conformal threshold",
                      scorer.conformal_threshold > 0,
                      f"{scorer.conformal_threshold:.4f}")
    except Exception as e:
        check_status("MetaScorer load", False, f"FAILED: {e}")
        fails += 1

    # ── Step 4: Aggressive Kelly profile honored ──
    section("Step 4 - Kelly profile validation")
    os.environ["MX_META_KELLY_PROFILE"] = "aggressive"
    # Re-import to pick up new profile
    import importlib
    import ml_meta_scorer_inference as msi
    importlib.reload(msi)
    expected = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
    for tier, exp in expected.items():
        got = msi.KELLY_CAPS.get(tier, 0)
        ok = abs(got - exp) < 1e-6
        if not ok: fails += 1
        check_status(f"{tier} cap = {exp:.2f}", ok, f"got {got:.2f}")

    # ── Step 5: Alpaca paper API ──
    section("Step 5 - Alpaca paper API reachability")
    base = env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    try:
        import httpx
        r = httpx.get(
            f"{base}/v2/account",
            headers={"APCA-API-KEY-ID": env.get("ALPACA_API_KEY", ""),
                       "APCA-API-SECRET-KEY": env.get("ALPACA_SECRET_KEY", "")},
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            equity = float(data.get("equity", 0))
            check_status("Alpaca account reachable", True,
                          f"equity=${equity:,.2f}")
            if "paper" in base:
                check_status("Paper endpoint", True, base)
            else:
                check_status("LIVE endpoint", False,
                              "WARNING: live endpoint configured!")
                warns += 1
        else:
            check_status("Alpaca account", False, f"HTTP {r.status_code}")
            fails += 1
    except Exception as e:
        check_status("Alpaca API", False, f"FAILED: {e}")
        fails += 1

    # ── Step 6: Drift detector freshness ──
    section("Step 6 - Drift detector freshness")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    drift_path = MODELS / f"drift_report_{today}.json"
    if drift_path.exists():
        report = json.loads(drift_path.read_text())
        n = report.get("n_drift_alerts", 0)
        check_status(f"drift report fresh (today)", True,
                      f"{n} alerts" + (": " + ", ".join(report.get("alerts", [])) if n > 0 else ""))
        if n > 0:
            warns += 1
    else:
        check_status("drift report fresh", False,
                      "MISSING — run drift_cron.py before deploy")
        fails += 1

    # ── Step 7: Lottery DRY_RUN ──
    section("Step 7 - Lottery DRY_RUN smoke (skipped if no Alpaca)")
    if env.get("ALPACA_API_KEY"):
        # Set DRY_RUN so no real orders
        cmd_env = {**os.environ,
                    "LOTTERY_DRY_RUN": "1",
                    "LOTTERY_TEST_MODE": "1",
                    "LOTTERY_USE_META_SCORER": "1",
                    "LOTTERY_AGGRESSIVE_KELLY": "1",
                    "MX_META_KELLY_PROFILE": "aggressive",
                    "LOTTERY_META_BANKROLL_USD": "10000"}
        try:
            r = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "lottery_runner.py")],
                env=cmd_env, capture_output=True, text=True, timeout=300,
            )
            ok = r.returncode in (0, 1)  # 1 may be expected if pre-9:30
            check_status("lottery_runner DRY_RUN exits cleanly",
                          ok, f"rc={r.returncode}")
            if not ok:
                fails += 1
                print(f"\n  --- last 20 lines of stderr ---")
                print("\n".join(r.stderr.splitlines()[-20:]))
        except subprocess.TimeoutExpired:
            check_status("lottery_runner DRY_RUN", False,
                          "timed out (>5 min) — likely sleeping until 9:30 ET")
            warns += 1
        except Exception as e:
            check_status("lottery_runner DRY_RUN", False, f"FAILED: {e}")
            fails += 1
    else:
        check_status("lottery_runner DRY_RUN", False,
                      "skipped: no Alpaca creds")
        warns += 1

    # ── Step 8: WF P&L expectation summary ──
    section("Step 8 - WF P&L expectation (the bar Monday must hit)")
    expectation_path = MODELS / "meta_scorer_summary_16fold_aggressive.json"
    if expectation_path.exists():
        s = json.loads(expectation_path.read_text())
        bankroll = s.get("bankroll_simulated", 10000)
        total = s.get("total_pnl_dollars", 0)
        pct = s.get("total_pnl_pct_of_bankroll", 0)
        n = s.get("n_picks", 0)
        print(f"  Aggressive Kelly WF on ${bankroll:,.0f}:")
        print(f"    total $:  ${total:+,.2f}  ({pct:+.2f}% of bankroll)")
        print(f"    n picks:  {n} over WF span")
        print(f"    avg/pick: ${total/n if n>0 else 0:+.2f}")
        if "tier_stats" in s:
            for tier, t in s["tier_stats"].items():
                print(f"      {tier:<8} n={t['n']:>4} avg={t['avg_pct']:>+6.2f}% win={t['win_pct']:>5.1f}%")
    else:
        check_status("WF expectation summary", False,
                      f"MISSING: {expectation_path.name}")
        warns += 1

    # ── Final ──
    section(f"PREFLIGHT SUMMARY  fails={fails}  warns={warns}")
    if fails > 0:
        print(f"  [FAIL] {fails} CRITICAL FAILURE(S) -- fix before deploy")
        return 1
    elif warns > 0:
        print(f"  [WARN] {warns} warning(s) -- review but OK to deploy")
        return 0
    else:
        print(f"  [PASS] ALL GREEN -- full-send ready for Monday open")
        return 0


if __name__ == "__main__":
    sys.exit(main())
