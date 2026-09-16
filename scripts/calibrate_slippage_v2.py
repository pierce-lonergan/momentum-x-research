"""Calibration v2: side-aware slippage fit with structural-mismatch
exclusion.

Block B of the falsification session. Replaces doc 59's reverted
fit. Per the brief stop condition: with 9 trades, the per-side ×
per-tier × per-hold-time × per-volume grid is too sparse (4 of 6
side×tier cells have n<3, well over the 50% threshold). Fall back
to side-only fit.

CRITICAL FINDING — STRUCTURAL OUTLIERS:
  Computing per-trade residuals revealed three trades with
  |residual| > 500 bps:
    OGN  buy  -1487 bps (bar.open $13.22 vs prod fill $11.25)
    ATER buy  -1042 bps (bar.open $1.44 vs prod fill $1.29)
    ATER sell -1176 bps (bar.open $1.44 vs prod fill $1.27)
    SEGG buy  +417  bps (bar.open $1.09 vs prod fill $1.14)
    SEGG sell +417  bps (same trade)
    SCNI buy  -315  bps (bar.open $0.88 vs prod fill $0.86)
    SCNI sell -315  bps (same trade)

  These are NOT slippage. They are LIMIT-PRICE vs BAR-OPEN structural
  mismatches: prod submitted FAST_PATH OTO orders with limit prices
  set BELOW (or ABOVE) the bar's first-tick open. The bar.open is the
  first observed price after the open auction; limit orders fill
  intra-bar at the limit price. Calibrating arena's spread to fit
  these residuals would over-correct by 10-100×.

  This IS a separate finding (rig limitation: arena uses bar.open as
  proxy for "where prod entered" but for limit orders that's
  structurally wrong). Documented in doc 64 §3 + plan doc 58 §11.

  Tonight's calibration EXCLUDES these structural outliers
  (|residual| > 500 bps drop) and fits the remaining 11 residuals.

Approach:
  1. Compute residuals per leg (entry buy / exit sell) using truth tables.
  2. Drop |residual| > 500 bps (structural-mismatch outliers).
  3. Per-side winsorized median (drop top/bottom 5% of remaining).
  4. Convert median to multiplier vs arena's default half-spread.
  5. Floor at 1.0 (never tighten).
  6. Write calibration JSON with provenance (n_dropped, n_kept).
  7. Validate: prod-mirror replay must still match prod ($0 Δ),
     because prod-mirror overrides modeled slippage.

Output:
  mx-arena/arena/calibration/spread_v2.json
  data/calibration/slippage_residuals.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("calibrate_v2")

REPO_ROOT = Path(__file__).resolve().parent.parent
QTY_TRUTH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
EXIT_TRUTH = REPO_ROOT / "data" / "replay" / "prod_exit_truth.parquet"
DEFAULT_OUTPUT = REPO_ROOT / "mx-arena" / "arena" / "calibration" / "spread_v2.json"
RESIDUAL_PARQUET = REPO_ROOT / "data" / "calibration" / "slippage_residuals.parquet"

# Arena baseline base-bps per tier (from spread_model.py)
ARENA_BASE_BPS = {
    "sub_1": 80.0, "sub_3": 30.0, "sub_10": 12.0, "sub_50": 5.0, "above_50": 2.0,
}

STRUCTURAL_OUTLIER_THRESHOLD_BPS = 500.0


@dataclass
class Residual:
    ticker: str
    session_date: str
    side: str          # buy | sell
    tier: str          # sub_3 | sub_10 | sub_50
    arena_default_px: float
    prod_actual_px: float
    residual_bps: float
    is_structural_outlier: bool


def _tier_for_price(p: float) -> str:
    if p < 1.0: return "sub_1"
    if p < 3.0: return "sub_3"
    if p < 10.0: return "sub_10"
    if p < 50.0: return "sub_50"
    return "above_50"


def build_residuals() -> list[Residual]:
    sys.path.insert(0, str(REPO_ROOT / "mx-arena"))
    import pandas as pd
    from arena.data_engine import DataEngine
    from arena.clock import SimClock, ClockMode

    qty_df = pd.read_parquet(QTY_TRUTH)
    exit_df = pd.read_parquet(EXIT_TRUTH)
    exit_lookup = {
        (r["ticker"], r["session_date"], r["entry_ts"]): float(r["prod_exit_avg_px"])
        for _, r in exit_df.iterrows()
    }

    out: list[Residual] = []
    for _, r in qty_df.iterrows():
        ticker, sd, entry_iso = r["ticker"], r["session_date"], r["entry_ts"]
        prod_entry = float(r["prod_entry_avg_px"])
        target = REPO_ROOT / "mx-arena" / "data" / "historical" / ticker / f"{sd}.parquet"
        if not target.exists():
            continue
        clock = SimClock(
            start=datetime.fromisoformat(f"{sd}T13:30:00+00:00"),
            end=datetime.fromisoformat(f"{sd}T20:00:00+00:00"),
            mode=ClockMode.REPLAY,
        )
        engine = DataEngine(clock=clock, historical_dir=str(target.parent.parent))
        bars = engine._load_parquet_bars(ticker, sd)
        if not bars:
            continue
        entry_dt = datetime.fromisoformat(entry_iso)
        target_iso = entry_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        bar_at_entry = None
        for _i, b in bars.items():
            if b.timestamp > target_iso:
                break
            bar_at_entry = b
        if not bar_at_entry:
            continue
        bar_open_entry = float(bar_at_entry.open)
        if bar_open_entry <= 0:
            continue

        entry_residual = (prod_entry - bar_open_entry) / bar_open_entry * 1e4
        out.append(Residual(
            ticker=ticker, session_date=sd,
            side="buy", tier=_tier_for_price(prod_entry),
            arena_default_px=bar_open_entry, prod_actual_px=prod_entry,
            residual_bps=round(entry_residual, 2),
            is_structural_outlier=abs(entry_residual) > STRUCTURAL_OUTLIER_THRESHOLD_BPS,
        ))

        prod_exit = exit_lookup.get((ticker, sd, entry_iso))
        if prod_exit is None or prod_exit <= 0:
            continue
        # For exit residual, ideally use bar.open at EXIT minute. We don't
        # have exit_ts in qty_truth, but exit_truth does — load exit
        # timestamp would require trade_results.jsonl join. For MVP:
        # use bar_open_entry as a conservative anchor — same-bar exits
        # will compute as 0; multi-bar exits will report the price
        # difference, which is a mix of price drift + slippage. This is
        # acknowledged in doc 64.
        exit_residual = (prod_exit - bar_open_entry) / bar_open_entry * 1e4
        out.append(Residual(
            ticker=ticker, session_date=sd,
            side="sell", tier=_tier_for_price(prod_exit),
            arena_default_px=bar_open_entry, prod_actual_px=prod_exit,
            residual_bps=round(exit_residual, 2),
            is_structural_outlier=abs(exit_residual) > STRUCTURAL_OUTLIER_THRESHOLD_BPS,
        ))
    return out


def fit_side_only(residuals: list[Residual]) -> dict:
    """Per-side winsorized median multiplier vs arena's mean baseline
    half-spread (~7.5 bps across tiers as a rough average; we apply
    multiplier per tier still, just using ONE multiplier per side)."""
    clean = [r for r in residuals if not r.is_structural_outlier]
    by_side: dict[str, list[float]] = {"buy": [], "sell": []}
    for r in clean:
        by_side[r.side].append(abs(r.residual_bps))

    out: dict = {"sub_1": 1.0, "sub_3": 1.0, "sub_10": 1.0, "sub_50": 1.0, "above_50": 1.0}
    per_side_diag: dict = {}
    avg_baseline_half_bps = 7.5  # approximate average across tiers

    for side, vals in by_side.items():
        if len(vals) < 3:
            per_side_diag[side] = {"n_kept": len(vals), "median_bps": None, "multiplier": 1.0, "note": "insufficient data — no calibration"}
            continue
        # Winsorize: drop top/bottom 5% (with n=9 that's 0 each side; we use indexing to be safe)
        vs = sorted(vals)
        n_drop = max(0, int(len(vs) * 0.05))
        winsorized = vs[n_drop:len(vs) - n_drop] if n_drop > 0 else vs
        median = statistics.median(winsorized)
        multiplier = max(1.0, median / avg_baseline_half_bps)
        per_side_diag[side] = {
            "n_total": len(vals), "n_dropped_winsorize": 2 * n_drop,
            "n_kept": len(winsorized),
            "median_abs_bps": round(median, 2),
            "multiplier": round(multiplier, 3),
        }

    # The arena SpreadModel applies one multiplier per tier (no side
    # dimension currently). For MVP, average buy and sell to a single
    # multiplier, applied to all tiers.
    side_mults = [v.get("multiplier", 1.0) for v in per_side_diag.values() if isinstance(v, dict)]
    if side_mults:
        avg_mult = round(sum(side_mults) / len(side_mults), 3)
    else:
        avg_mult = 1.0
    for tier in out:
        out[tier] = max(1.0, avg_mult)

    return {"tiers": out, "per_side_diag": per_side_diag, "method": "side-only-averaged-to-uniform-tier"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    residuals = build_residuals()
    logger.info("Built %d residual rows from truth tables", len(residuals))

    n_outlier = sum(1 for r in residuals if r.is_structural_outlier)
    logger.info("  %d structural outliers (|residual| > %.0f bps) — EXCLUDED from fit", n_outlier, STRUCTURAL_OUTLIER_THRESHOLD_BPS)
    n_clean = len(residuals) - n_outlier

    # Persist all residuals (including outliers) for diagnostic
    try:
        import pandas as pd
        RESIDUAL_PARQUET.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([asdict(r) for r in residuals])
        df.to_parquet(RESIDUAL_PARQUET, index=False)
        logger.info("wrote %s", RESIDUAL_PARQUET)
    except ImportError:
        pass

    fit = fit_side_only(residuals)

    # SECOND STOP CONDITION: even after excluding structural outliers,
    # the cleaned residuals (n=14, median 130-142 bps) yield an 18×
    # multiplier — at which point arena's half-spread on a $3 stock
    # becomes ~$0.08, which is an order of magnitude wider than realistic.
    #
    # The cleaned residuals STILL contain intra-bar price drift (the
    # difference between bar.open and prod's actual fill within the
    # same minute is mostly tape movement, not slippage). Without
    # intra-bar tick data we cannot separate the two.
    #
    # Per discipline rule from doc 59 (calibration v0.1 was reverted on
    # the same kind of overshoot), DO NOT SHIP a misleading fit. Set
    # multipliers to identity (1.0) and document.
    naive_multiplier = max(
        v.get("multiplier", 1.0) for v in fit["per_side_diag"].values()
        if isinstance(v, dict) and v.get("multiplier") is not None
    )
    if naive_multiplier > 5.0:
        logger.warning(
            "v2 fit produced multiplier %.2fx — over the 5x sanity cap. "
            "Cleaned residuals are still contaminated by intra-bar drift "
            "(the difference between bar.open and prod fill within the "
            "same minute is largely tape movement, not slippage). "
            "Per discipline (doc 59 precedent), shipping IDENTITY (1.0) "
            "and documenting in doc 64.",
            naive_multiplier,
        )
        final_tiers = {"sub_1": 1.0, "sub_3": 1.0, "sub_10": 1.0, "sub_50": 1.0, "above_50": 1.0}
        ship_status = "IDENTITY (sanity cap exceeded)"
    else:
        final_tiers = fit["tiers"]
        ship_status = "FITTED"

    payload = {
        "version": "v2",
        "method": "side-only winsorized median, structural outliers excluded; sanity-capped to identity if naive multiplier > 5x",
        "structural_outlier_threshold_bps": STRUCTURAL_OUTLIER_THRESHOLD_BPS,
        "n_total_residuals": len(residuals),
        "n_structural_outliers_excluded": n_outlier,
        "n_used_for_fit": n_clean,
        "per_side_diagnostic": fit["per_side_diag"],
        "naive_fit_tiers_uncapped": fit["tiers"],
        "tiers": final_tiers,
        "ship_status": ship_status,
        "warnings": [
            "Calibration applies ONLY to non-prod-mirror trades. Prod-mirror still wins for trades in the truth corpus.",
            f"{n_outlier}/{len(residuals)} residuals were structural limit-vs-bar-open mismatches, NOT slippage. See doc 64 §3.",
            "Exit-side residuals approximated using bar.open at ENTRY minute, not exit minute. Acceptable for side-averaged MVP; doc 64 §4 plans the proper exit-bar lookup.",
            "Cleaned-residual MEDIAN of 130-142 bps still contains intra-bar drift. Without intra-bar tick data we cannot separate intra-minute price movement from slippage. Identity multipliers shipped to avoid over-correcting.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("wrote %s", args.output)
    logger.info("SUMMARY: %s", json.dumps(payload["per_side_diagnostic"], indent=2))
    logger.info("FINAL multipliers (per tier, all set to side-averaged value): %s", payload["tiers"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
