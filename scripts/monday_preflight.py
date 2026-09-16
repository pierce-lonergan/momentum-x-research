#!/usr/bin/env python
"""D204: Monday Pre-Flight Readiness Check.

Run this before market open to verify all systems are GO.
Checks VIX, day-of-week gate, API connectivity, and config.

Usage:
    python scripts/monday_preflight.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    print("=" * 70)
    print("  D204: PRE-SESSION READINESS CHECK")
    print("=" * 70)

    from config.settings import load_settings
    s = load_settings()

    issues = []
    warnings = []

    # ── Day of week ─────────────────────────────────────────────
    now = datetime.now()
    dow = now.weekday()
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    allowed = {int(d.strip()) for d in s.scoring.trading_days_allowed.split(",") if d.strip().isdigit()}

    print(f"\n  Today: {dow_names[dow]} (weekday={dow})")
    print(f"  Allowed days: {s.scoring.trading_days_allowed} = {[dow_names[d] for d in sorted(allowed)]}")
    if dow in allowed:
        print("  [OK] Day-of-week gate: PASS")
    else:
        issues.append(f"Day-of-week gate BLOCKS today ({dow_names[dow]})")
        print(f"  [FAIL] Day-of-week gate: {dow_names[dow]} is NOT in allowed days")

    # ── VIX check (try yfinance) ────────────────────────────────
    print()
    try:
        import yfinance as yf
        vix = yf.download("^VIX", period="5d", progress=False)
        if not vix.empty:
            latest_vix = float(vix["Close"].iloc[-1])
            vix_date = str(vix.index[-1].date())
            print(f"  VIX: {latest_vix:.1f} (as of {vix_date})")
            if latest_vix >= s.scoring.vix_panic_threshold:
                issues.append(f"VIX {latest_vix:.1f} >= panic threshold {s.scoring.vix_panic_threshold}")
                print(f"  [FAIL] VIX >= {s.scoring.vix_panic_threshold} — ALL entries blocked (D211 panic)")
            elif latest_vix >= s.scoring.vix_block_threshold:
                warnings.append(f"VIX {latest_vix:.1f} >= block threshold {s.scoring.vix_block_threshold}")
                print(f"  [WARN] VIX >= {s.scoring.vix_block_threshold} — MOMENTUM blocked, CATALYST at 50% size (D211)")
            elif latest_vix >= s.scoring.vix_reduce_threshold:
                warnings.append(f"VIX {latest_vix:.1f} >= reduce threshold {s.scoring.vix_reduce_threshold}")
                print(f"  [WARN] VIX >= {s.scoring.vix_reduce_threshold} — half-size positions")
            else:
                print(f"  [OK] VIX < {s.scoring.vix_reduce_threshold} — full-size positions")
        else:
            warnings.append("Could not fetch VIX data")
            print("  [WARN] VIX data unavailable")
    except Exception as e:
        warnings.append(f"VIX fetch error: {e}")
        print(f"  [WARN] VIX check failed: {e}")

    # ── Config audit ────────────────────────────────────────────
    print()
    print("  --- PRODUCTION CONFIG ---")
    checks = [
        ("Catalyst gate", s.universe.require_catalyst, True),
        ("Ensemble enabled", s.models.ensemble_enabled, True),
        ("Ensemble N calls", s.models.ensemble_n_calls, 3),
        ("Debate disabled", s.debate.max_debate_attempts, 0),
        ("News weight", s.scoring.catalyst_news, 0.55),
        ("Technical weight", s.scoring.technical, 0.05),
        ("MFCS threshold", s.scoring.mfcs_buy_threshold, 0.25),
        ("Temperature", s.models.default_temperature, 0.1),
        ("Risk lambda", s.scoring.risk_aversion_lambda, 0.25),
    ]

    for name, actual, expected in checks:
        match = actual == expected
        status = "OK" if match else "MISMATCH"
        print(f"  [{status:>8s}] {name:<20s} = {actual} (expected {expected})")
        if not match:
            warnings.append(f"{name}: got {actual}, expected {expected}")

    # ── Weight sum check ────────────────────────────────────────
    wsum = s.scoring.catalyst_news + s.scoring.technical + s.scoring.volume_rvol + s.scoring.float_structure
    print(f"\n  Weight sum: {wsum:.2f} (should be 1.00)")
    if abs(wsum - 1.0) > 0.01:
        issues.append(f"Weight sum = {wsum:.2f} (not 1.00)")

    # ── API connectivity ────────────────────────────────────────
    print()
    print("  --- API CONNECTIVITY ---")
    try:
        import os
        alpaca_key = os.environ.get("ALPACA_API_KEY", "")
        alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        if alpaca_key and alpaca_secret:
            print(f"  [OK] Alpaca API key: ...{alpaca_key[-4:]}")
        else:
            issues.append("Alpaca API keys not set")
            print("  [FAIL] Alpaca API keys missing")

        together_key = os.environ.get("TOGETHER_API_KEY", "")
        if together_key:
            print(f"  [OK] Together AI key: ...{together_key[-4:]}")
        else:
            issues.append("Together AI API key not set")
            print("  [FAIL] Together AI key missing")
    except Exception as e:
        warnings.append(f"API check error: {e}")

    # ── Summary ─────────────────────────────────────────────────
    print()
    print("=" * 70)
    if issues:
        print("  STATUS: BLOCKED")
        for issue in issues:
            print(f"    [X] {issue}")
    elif warnings:
        print("  STATUS: GO (with warnings)")
        for warn in warnings:
            print(f"    [!] {warn}")
    else:
        print("  STATUS: ALL SYSTEMS GO")

    print()
    print("  EXPECTED BEHAVIOR:")
    print("  1. Scan 50 most-active + 20 top-movers from Alpaca")
    print("  2. Catalyst gate blocks stocks without confirmed news catalyst")
    print("  3. News confidence gate requires news_agent BULL with conf>=0.30")
    print("  4. D170 observation: 5-min minimum before any entry")
    print("  5. Ensemble: each LLM agent called 3x, majority vote")
    print("  6. VIX checked every 5 min — blocks if >25, half-size if >18")
    print("  7. All trades logged with bid/ask spread for post-session analysis")
    print("=" * 70)

    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
