"""Probabilistic adverse-selection sampler for arena's modeled exits.

Built from `data/calibration/adverse_selection_curves.parquet` (Block D
of doc 81). For each (price_tier, T_minutes) cell, returns an
empirical sample drawn from the cohort's adverse-drift distribution
at that holding duration.

This is NOT a parametric fit. The cohort is small (8 fills, see doc 81
§2 for per-fill heterogeneity) so a parametric model would overstate
confidence. The sampler returns one of the actually-observed cohort
values for the requested cell, with sane fallbacks when n<3.

Usage:
    from adverse_selection_sampler import AdverseSelectionSampler
    s = AdverseSelectionSampler.from_default()
    drift_bps = s.sample(price_tier="sub_3", T_minutes=15)
    # Returns one of: -39, +176, +223, +304, +393  (the 5 sub_3 fills at T+15)

Determinism:
    Pass `rng=numpy.random.default_rng(seed)` for reproducibility.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CURVES = REPO_ROOT / "data" / "calibration" / "adverse_selection_curves.parquet"

# Per-fill heterogeneity is so high that a per-tier identity at small T
# values can mislead. Below this n threshold we fall back to the
# all-tier pool, then to identity (0 bps) if even that's empty.
MIN_N_FOR_TIER_SAMPLE = 3


@dataclass
class AdverseSelectionSampler:
    """Empirical-distribution sampler keyed on (price_tier, T_minutes).

    `_pool` is a dict keyed on (tier, T) → list of observed adverse_drift_bps
    values. `_global_pool` is keyed on T alone for the n<MIN fallback.
    """
    _pool: dict[tuple[str, int], list[float]] = field(default_factory=dict)
    _global_pool: dict[int, list[float]] = field(default_factory=dict)
    _max_T: int = 60

    @classmethod
    def from_curves_parquet(cls, path: Path | str = DEFAULT_CURVES) -> "AdverseSelectionSampler":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"adverse-selection curves missing: {path}. "
                f"Run scripts/compute_adverse_selection.py first."
            )
        df = pd.read_parquet(path)
        df = df[df["has_quote"]].copy()
        if df.empty:
            raise ValueError(f"no usable rows in {path}")
        return cls.from_dataframe(df)

    @classmethod
    def from_default(cls) -> "AdverseSelectionSampler":
        return cls.from_curves_parquet(DEFAULT_CURVES)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "AdverseSelectionSampler":
        """Build from an already-loaded curves DataFrame.

        df must have columns: price_tier, T_minutes, adverse_drift_bps,
        has_quote (optional, will be filtered if present).
        """
        if "has_quote" in df.columns:
            df = df[df["has_quote"]].copy()
        required = {"price_tier", "T_minutes", "adverse_drift_bps"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"curves DataFrame missing columns: {missing}")
        df = df.dropna(subset=["adverse_drift_bps"])

        pool: dict[tuple[str, int], list[float]] = {}
        global_pool: dict[int, list[float]] = {}
        for (tier, T), g in df.groupby(["price_tier", "T_minutes"]):
            pool[(str(tier), int(T))] = g["adverse_drift_bps"].astype(float).tolist()
        for T, g in df.groupby("T_minutes"):
            global_pool[int(T)] = g["adverse_drift_bps"].astype(float).tolist()

        # Handle empty-after-NaN-drop case: default _max_T to 60
        # so sample(T=anything) clamps cleanly and falls through to identity.
        max_T_val = df["T_minutes"].max()
        if pd.isna(max_T_val):
            max_T = 60
        else:
            max_T = int(max_T_val)

        return cls(
            _pool=pool,
            _global_pool=global_pool,
            _max_T=max_T,
        )

    # ── Sampling API ───────────────────────────────────────────────

    def sample(
        self,
        price_tier: str,
        T_minutes: int,
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        """Return one drawn adverse_drift_bps for the given cell.

        Fallback order:
        1. (tier, T) cell with n >= MIN_N_FOR_TIER_SAMPLE → uniform draw
        2. (tier, T) cell with n < MIN_N → uniform draw from global pool at T
        3. global pool at T empty → 0.0 (identity, no adverse modeled)
        4. T > _max_T → use _max_T
        """
        if rng is None:
            rng = np.random.default_rng()
        T = min(int(T_minutes), self._max_T)
        cell = self._pool.get((price_tier, T), [])
        if len(cell) >= MIN_N_FOR_TIER_SAMPLE:
            return float(rng.choice(cell))
        # Fallback 2: global pool at T
        global_at_T = self._global_pool.get(T, [])
        if global_at_T:
            return float(rng.choice(global_at_T))
        # Fallback 3: identity
        return 0.0

    def cell_n(self, price_tier: str, T_minutes: int) -> int:
        """How many empirical samples back this cell."""
        return len(self._pool.get((price_tier, int(T_minutes)), []))

    def cell_quantiles(
        self, price_tier: str, T_minutes: int, qs: tuple[float, ...] = (0.25, 0.5, 0.75),
    ) -> dict[float, float]:
        """Return quantile values for the cell. Empty cell → all NaN."""
        cell = self._pool.get((price_tier, int(T_minutes)), [])
        if not cell:
            return {q: float("nan") for q in qs}
        arr = np.asarray(cell, dtype=float)
        return {q: float(np.quantile(arr, q)) for q in qs}

    def sample_path(
        self,
        price_tier: str,
        T_max: int = 60,
        rng: Optional[np.random.Generator] = None,
    ) -> list[float]:
        """Return a list of `T_max` sampled adverse-drift values, one per
        minute T+1..T+T_max. Each minute drawn INDEPENDENTLY (no
        autocorrelation modeling) — appropriate for the small-cohort
        regime where any autocorrelation estimate would overfit.
        """
        return [self.sample(price_tier, T, rng=rng) for T in range(1, T_max + 1)]

    def summary(self) -> str:
        """Human-readable summary of cell coverage."""
        lines = ["AdverseSelectionSampler cell coverage (n per cell):"]
        tiers = sorted({t for t, _ in self._pool})
        Ts = sorted({T for _, T in self._pool})
        if not tiers or not Ts:
            return "(empty)"
        # Header
        header = "  T   |" + "|".join(f" {t:^9} " for t in tiers)
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        # Rows
        for T in [1, 5, 15, 30, 60]:
            if T > self._max_T:
                continue
            row = f"  T+{T:<2}|"
            for t in tiers:
                n = self.cell_n(t, T)
                marker = "*" if n < MIN_N_FOR_TIER_SAMPLE else " "
                row += f"  n={n}{marker:1}     |"
            lines.append(row)
        lines.append(f"  (cells marked * fall back to global pool at that T)")
        return "\n".join(lines)
