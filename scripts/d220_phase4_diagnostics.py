"""Phase 4 critical diagnostics — verify the inverted strategy isn't a measurement artifact.

Five diagnostics per the user's pushback before Phase 5 wires anything live:
  1. Recompute PnL with first-2-minute VWAP entry (vs labeled 9:31 open print)
  2. ORB-decision-time check — does inverted require post-9:35 lookahead?
  3. Composite null result — is the strategy actually the labeling?
  4. Recompute MAE from realistic entry, see if 5:1 ratio survives
  5. 20% true holdout — strategy never touches these rows
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.composite.features import extract_features
from src.composite.score import composite_score
from src.production_arena.pipeline_runner import run_scenario
from src.production_arena.scenarios import load_scenarios

random.seed(42)


def first_n_minute_vwap(scenario, n_minutes: int = 2):
    """Realistic entry: VWAP of the first N minutes after the 9:31 open."""
    bars = scenario.minute_bars
    if not bars or len(bars) < n_minutes + 5:
        return None
    open_idx = None
    for i, b in enumerate(bars):
        ts = b.timestamp
        if "T13:30" in ts or "T14:30" in ts:
            open_idx = i
            break
    if open_idx is None:
        open_idx = 0
    entry_idx = open_idx + 1
    if entry_idx + n_minutes > len(bars):
        return None
    window = bars[entry_idx : entry_idx + n_minutes]
    total_vol = sum(b.volume for b in window)
    if total_vol == 0:
        return None
    weighted = 0.0
    for b in window:
        bar_vwap = b.vwap if b.vwap and b.vwap > 0 else (b.high + b.low + b.close) / 3.0
        weighted += bar_vwap * b.volume
    return weighted / total_vol


def close_return_from(scenario, entry_price):
    bars = scenario.minute_bars
    if not bars or entry_price <= 0:
        return None
    end_close = bars[-1].close
    return (end_close - entry_price) / entry_price


def mfe_mae_from(scenario, entry_price, n_minutes_post_open_skip=2):
    bars = scenario.minute_bars
    if not bars or entry_price <= 0:
        return (0.0, 0.0)
    open_idx = None
    for i, b in enumerate(bars):
        ts = b.timestamp
        if "T13:30" in ts or "T14:30" in ts:
            open_idx = i
            break
    if open_idx is None:
        open_idx = 0
    start = open_idx + 1 + n_minutes_post_open_skip
    post = bars[start:]
    if not post:
        return (0.0, 0.0)
    highs = [b.high for b in post if b.high > 0]
    lows = [b.low for b in post if b.low > 0]
    if not highs or not lows:
        return (0.0, 0.0)
    mfe = (max(highs) - entry_price) / entry_price
    mae = (min(lows) - entry_price) / entry_price
    return (mfe, mae)


def main():
    coll = load_scenarios(require_label=True)
    scenarios = [s for s in coll if s.labeled_outcome.close_return is not None]
    print(f"Loaded {len(scenarios)} labeled scenarios")

    verdicts = {(s.session_date, s.ticker): run_scenario(s) for s in scenarios}

    def is_pre_open_reject(s):
        v = verdicts[(s.session_date, s.ticker)]
        return (v.decision == "NO_TRADE"
                and v.gate_rejected != "entry_delay.py:orb_confirmation")

    inverted_set = [s for s in scenarios if is_pre_open_reject(s)]
    print(f"Inverted set (NT-excl-ORB): {len(inverted_set)}")
    print(f"Labeled close_return mean: "
          f"{statistics.mean(s.labeled_outcome.close_return for s in inverted_set)*100:+.2f}%")

    # ── DIAGNOSTIC 1 ──
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 1 — first-2-minute VWAP entry")
    print("=" * 70)
    n_uninvestable = 0
    realistic_returns = []
    labeled_returns = []
    slippage_pcts = []
    for s in inverted_set:
        re_ = first_n_minute_vwap(s, 2)
        le = s.labeled_outcome.entry_price
        if re_ is None or le is None or le <= 0:
            n_uninvestable += 1
            continue
        rr = close_return_from(s, re_)
        lr = s.labeled_outcome.close_return
        if rr is None or lr is None:
            n_uninvestable += 1
            continue
        realistic_returns.append(rr)
        labeled_returns.append(lr)
        slippage_pcts.append((re_ - le) / le * 100)
    n = len(realistic_returns)
    print(f"  inverted set: {len(inverted_set)} | uninvestable: {n_uninvestable} | investable: {n}")
    if n > 0:
        print(f"  LABELED entry close return:    mean={statistics.mean(labeled_returns)*100:+.2f}%  median={statistics.median(labeled_returns)*100:+.2f}%")
        print(f"  REALISTIC entry close return:  mean={statistics.mean(realistic_returns)*100:+.2f}%  median={statistics.median(realistic_returns)*100:+.2f}%")
        print(f"  Slippage realistic vs labeled: mean={statistics.mean(slippage_pcts):+.2f}%  median={statistics.median(slippage_pcts):+.2f}%")
        wr_l = sum(1 for r in labeled_returns if r > 0) / n * 100
        wr_r = sum(1 for r in realistic_returns if r > 0) / n * 100
        print(f"  WR labeled: {wr_l:.1f}%   WR realistic: {wr_r:.1f}%")

    # ── DIAGNOSTIC 2 ──
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 2 — ORB-decision-time check")
    print("=" * 70)
    n_all_nt = sum(1 for s in scenarios if verdicts[(s.session_date, s.ticker)].decision == "NO_TRADE")
    n_nt_orb_held = sum(1 for s in scenarios
                        if verdicts[(s.session_date, s.ticker)].decision == "NO_TRADE"
                        and verdicts[(s.session_date, s.ticker)].gate_rejected == "entry_delay.py:orb_confirmation")
    print(f"  TOTAL NT (decision-time pool, no ORB info): {n_all_nt}")
    print(f"  Of these, would later be ORB-held:           {n_nt_orb_held}")
    print(f"  Inverted set (excl ORB, post-hoc filter):    {len(inverted_set)}")

    all_nt = [s for s in scenarios if verdicts[(s.session_date, s.ticker)].decision == "NO_TRADE"]
    all_nt_returns = [s.labeled_outcome.close_return for s in all_nt
                      if s.labeled_outcome.close_return is not None]
    wr = sum(1 for r in all_nt_returns if r > 0) / len(all_nt_returns) * 100
    print(f"\n  Trade ALL NT (the only realistic 9:31 universe), labeled entry:")
    print(f"    n={len(all_nt_returns)} WR={wr:.1f}% avg={statistics.mean(all_nt_returns)*100:+.2f}%")

    # And realistic entry on ALL NT
    all_nt_realistic = []
    for s in all_nt:
        re_ = first_n_minute_vwap(s, 2)
        if re_ is None: continue
        rr = close_return_from(s, re_)
        if rr is not None:
            all_nt_realistic.append(rr)
    if all_nt_realistic:
        wr = sum(1 for r in all_nt_realistic if r > 0) / len(all_nt_realistic) * 100
        print(f"\n  Trade ALL NT (realistic first-2-min VWAP entry):")
        print(f"    n={len(all_nt_realistic)} WR={wr:.1f}% avg={statistics.mean(all_nt_realistic)*100:+.2f}%")

    # ── DIAGNOSTIC 3 ──
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 3 — Composite filter contribution in inverted mode")
    print("=" * 70)
    inv_with_cs = []
    for s in inverted_set:
        cs = composite_score(s.premarket_features, arena_buy_verdict=False)
        inv_with_cs.append((s, cs))
    cs_values = sorted(cs for _, cs in inv_with_cs)
    print(f"  composite distribution: min={cs_values[0]:.3f} p25={cs_values[len(cs_values)//4]:.3f} p50={cs_values[len(cs_values)//2]:.3f} max={cs_values[-1]:.3f}")
    for thresh in [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70]:
        above = [(s, cs) for s, cs in inv_with_cs if cs >= thresh]
        if not above: continue
        rets_l = [s.labeled_outcome.close_return for s, _ in above
                  if s.labeled_outcome.close_return is not None]
        if not rets_l: continue
        wr_l = sum(1 for r in rets_l if r > 0) / len(rets_l) * 100
        avg_l = statistics.mean(rets_l) * 100
        # Realistic
        rets_r = []
        for s, _ in above:
            re_ = first_n_minute_vwap(s, 2)
            if re_ is None: continue
            rr = close_return_from(s, re_)
            if rr is not None: rets_r.append(rr)
        wr_r = sum(1 for r in rets_r if r > 0) / max(1, len(rets_r)) * 100
        avg_r = statistics.mean(rets_r) * 100 if rets_r else 0.0
        print(f"  >={thresh:.2f}: n_lab={len(rets_l)} WR_lab={wr_l:.1f}% avg_lab={avg_l:+.2f}%  |  n_real={len(rets_r)} WR_real={wr_r:.1f}% avg_real={avg_r:+.2f}%")

    # ── DIAGNOSTIC 4 ──
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 4 — MAE from realistic entry")
    print("=" * 70)
    real_mfes = []
    real_maes = []
    for s in inverted_set:
        re_ = first_n_minute_vwap(s, 2)
        if re_ is None: continue
        mfe, mae = mfe_mae_from(s, re_, 2)
        real_mfes.append(mfe)
        real_maes.append(mae)
    if real_mfes:
        med_mfe = statistics.median(real_mfes) * 100
        med_mae = statistics.median(real_maes) * 100
        print(f"  on {len(real_mfes)} investable inverted candidates:")
        print(f"    median MFE (realistic): {med_mfe:+.2f}%   |   median MAE (realistic): {med_mae:+.2f}%")
        print(f"    mean   MFE: {statistics.mean(real_mfes)*100:+.2f}%   |   mean MAE: {statistics.mean(real_maes)*100:+.2f}%")
        ratio = abs(med_mfe / med_mae) if med_mae != 0 else float('inf')
        print(f"    median MFE/MAE ratio: {ratio:.2f}:1")
        print(f"  ORIGINAL (labeled) reported: +22.80% / -4.41% = 5.17:1")

    # ── DIAGNOSTIC 5 ──
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 5 — 20% true holdout")
    print("=" * 70)
    all_indices = list(range(len(scenarios)))
    random.shuffle(all_indices)
    split = int(len(all_indices) * 0.8)
    train_idx = set(all_indices[:split])
    holdout_idx = set(all_indices[split:])
    print(f"  train: {len(train_idx)} | holdout: {len(holdout_idx)}")

    holdout_inv = [scenarios[i] for i in holdout_idx if is_pre_open_reject(scenarios[i])]
    print(f"  holdout NT-excl-ORB rows: {len(holdout_inv)}")
    if holdout_inv:
        labeled_rets = [s.labeled_outcome.close_return for s in holdout_inv
                        if s.labeled_outcome.close_return is not None]
        wr = sum(1 for r in labeled_rets if r > 0) / len(labeled_rets) * 100
        print(f"  HOLDOUT inverted (labeled entry, all NT-excl-ORB):")
        print(f"    n={len(labeled_rets)} WR={wr:.1f}% avg={statistics.mean(labeled_rets)*100:+.2f}%")
        # Realistic entry
        realistic_rets = []
        for s in holdout_inv:
            re_ = first_n_minute_vwap(s, 2)
            if re_ is None: continue
            rr = close_return_from(s, re_)
            if rr is not None: realistic_rets.append(rr)
        if realistic_rets:
            wr = sum(1 for r in realistic_rets if r > 0) / len(realistic_rets) * 100
            print(f"  HOLDOUT inverted (REALISTIC first-2-min VWAP entry):")
            print(f"    n={len(realistic_rets)} WR={wr:.1f}% avg={statistics.mean(realistic_rets)*100:+.2f}%")

    # Cross-cohort: if inverted strategy works, BUY arena-rejected on HOLDOUT
    # ALSO measured against ALL NT (no ORB filter, the realistic universe)
    holdout_all_nt = [scenarios[i] for i in holdout_idx
                      if verdicts[(scenarios[i].session_date, scenarios[i].ticker)].decision == "NO_TRADE"]
    if holdout_all_nt:
        rets_l = [s.labeled_outcome.close_return for s in holdout_all_nt
                  if s.labeled_outcome.close_return is not None]
        wr = sum(1 for r in rets_l if r > 0) / len(rets_l) * 100
        rets_r = []
        for s in holdout_all_nt:
            re_ = first_n_minute_vwap(s, 2)
            if re_ is None: continue
            rr = close_return_from(s, re_)
            if rr is not None: rets_r.append(rr)
        print(f"\n  HOLDOUT trade ALL NT (no ORB filter, the realistic universe):")
        print(f"    labeled:    n={len(rets_l)} WR={wr:.1f}% avg={statistics.mean(rets_l)*100:+.2f}%")
        if rets_r:
            wr = sum(1 for r in rets_r if r > 0) / len(rets_r) * 100
            print(f"    realistic:  n={len(rets_r)} WR={wr:.1f}% avg={statistics.mean(rets_r)*100:+.2f}%")


if __name__ == "__main__":
    main()
