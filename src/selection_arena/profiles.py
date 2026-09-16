"""
Selection Arena Filter Profiles

Pre-built profiles for scanner filter analysis. Each profile represents
a different filtering philosophy. The goal is to sweep these profiles
against historical data to find the optimal balance of capture vs precision.

Profile naming convention:
    wide_net     — cast wide, don't miss winners (low capture cost, high noise)
    current      — mirrors today's live ScannerThresholds production defaults
    tight        — higher quality threshold (fewer candidates, higher precision)
    ablation_*   — remove one filter entirely to measure its individual contribution

Adding custom profiles:
    from src.selection_arena.profiles import FilterProfile, PROFILES
    PROFILES["my_profile"] = FilterProfile(
        name="my_profile",
        price_floor=2.00,
        rvol_min=1.5,
    )
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.selection_arena.models import FilterProfile

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Pre-built profiles
# ═══════════════════════════════════════════════════════════════════

PROFILES: dict[str, FilterProfile] = {

    # ── Baseline: matches live ScannerThresholds production defaults (D160 updated) ──
    "current": FilterProfile(
        name="current",
        description="Live production scanner parameters (D160 updated). "
                    "Price floor $1.50 (was $3), RVOL 2.0x, gap 5%, dolvol $2M (was $5M). "
                    "Mega-dolvol override at $10M bypasses all price checks.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=2_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
        price_override_mega_dollar_vol=10_000_000,
        faller_score_threshold=0.65,
    ),

    # ── Pre-D160 baseline: original production settings before Mar 30 changes ──
    "pre_d160": FilterProfile(
        name="pre_d160",
        description="Pre-D160 scanner parameters (before Mar 30 Selection Arena adjustments). "
                    "Price floor $3, dolvol $5M — the configuration that missed ITRM, PMNT, SLND, GLND.",
        price_floor=3.00,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=5_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
        price_override_mega_dollar_vol=10_000_000,
        faller_score_threshold=None,  # No faller detection in pre-D160
    ),

    # ── Proposed March 30: full D160 proposal for arena validation ──
    "proposed_march30": FilterProfile(
        name="proposed_march30",
        description="D160 full proposal validated by Mar 30 Selection Arena run. "
                    "price_floor $1.50, dolvol $2M, mega-dolvol $10M bypass, "
                    "faller_score_threshold 0.65. "
                    "Expected: capture_rate 33%→78%, zero precision loss. "
                    "ITRM ($54.6M), PMNT, SLND, GLND, JCSE, MKDW all captured.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=2_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
        price_override_mega_dollar_vol=10_000_000,
        faller_score_threshold=0.65,
    ),

    # ── Wide net: catch everything, minimize false negatives ──
    "wide_net": FilterProfile(
        name="wide_net",
        description="Low thresholds — cast wide to catch every potential mover. "
                    "Price floor $1, RVOL 1.0x, gap 3%, dolvol $500K. "
                    "Maximizes capture rate at the cost of high noise.",
        price_floor=1.00,
        price_floor_high_vol=0.25,
        rvol_min=1.0,
        absolute_volume_min=100_000,
        gap_pct_min=0.03,
        dollar_volume_min=500_000,
        extreme_rvol_override=20.0,
        price_override_dollar_vol=500_000,
        price_override_rvol=3.0,
    ),

    # ── Moderate: between current and wide_net ──
    "moderate": FilterProfile(
        name="moderate",
        description="Relaxed thresholds — price floor $1.50, RVOL 1.5x, gap 4%, dolvol $2M. "
                    "Captures most winners with manageable noise increase.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=1.5,
        absolute_volume_min=250_000,
        gap_pct_min=0.04,
        dollar_volume_min=2_000_000,
        extreme_rvol_override=25.0,
        price_override_dollar_vol=1_000_000,
        price_override_rvol=4.0,
    ),

    # ── Tight: higher quality, fewer candidates ──
    "tight": FilterProfile(
        name="tight",
        description="Strict thresholds — price floor $5, RVOL 3.0x, gap 8%, dolvol $10M. "
                    "Highest precision, but misses many sub-$5 small-cap movers.",
        price_floor=5.00,
        price_floor_high_vol=1.00,
        rvol_min=3.0,
        absolute_volume_min=750_000,
        gap_pct_min=0.08,
        dollar_volume_min=10_000_000,
        extreme_rvol_override=40.0,
        price_override_dollar_vol=3_000_000,
        price_override_rvol=8.0,
    ),

    # ── Ablation: remove price floor (let sub-$3 stocks through) ──
    "ablation_price_floor": FilterProfile(
        name="ablation_price_floor",
        description="Ablation study: remove price floor entirely. "
                    "All other filters at production defaults. "
                    "Measures how much the price floor contributes to missed winners.",
        price_floor=0.01,           # Effectively disabled
        price_floor_high_vol=0.01,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=5_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
    ),

    # ── Ablation: remove RVOL filter ──
    "ablation_rvol": FilterProfile(
        name="ablation_rvol",
        description="Ablation study: remove RVOL minimum. "
                    "All other filters at production defaults. "
                    "Measures how much RVOL filter contributes to missed winners.",
        price_floor=3.00,
        price_floor_high_vol=0.50,
        rvol_min=0.0,               # Effectively disabled
        absolute_volume_min=0,      # Disable absolute volume too
        gap_pct_min=0.05,
        dollar_volume_min=5_000_000,
        extreme_rvol_override=1.0,  # Always bypass
        price_override_dollar_vol=2_000_000,
        price_override_rvol=0.0,
    ),

    # ── Ablation: remove dollar volume filter ──
    "ablation_dolvol": FilterProfile(
        name="ablation_dolvol",
        description="Ablation study: remove dollar volume minimum. "
                    "All other filters at production defaults. "
                    "Measures how much dolvol filter contributes to missed winners.",
        price_floor=3.00,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=0,        # Effectively disabled
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
    ),

    # ── Ablation: remove gap threshold ──
    "ablation_gap": FilterProfile(
        name="ablation_gap",
        description="Ablation study: remove gap % minimum. "
                    "All other filters at production defaults. "
                    "Measures how much gap filter contributes to missed winners.",
        price_floor=3.00,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.0,            # Effectively disabled
        dollar_volume_min=5_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
    ),

    # ── Target: lower price floor to $1.50 (the most common FN fix) ──
    "price_floor_150": FilterProfile(
        name="price_floor_150",
        description="Lower price floor to $1.50. All other params at production defaults. "
                    "Proposed improvement: captures sub-$3 small-cap movers "
                    "that the current $3 floor misses.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=5_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
    ),

    # ── Target: lower RVOL minimum to 1.5x ──
    "rvol_1_5x": FilterProfile(
        name="rvol_1_5x",
        description="Lower RVOL minimum to 1.5x. All other params at production defaults. "
                    "Proposed improvement: may capture low-float catalysts that "
                    "move big without extreme RVOL.",
        price_floor=3.00,
        price_floor_high_vol=0.50,
        rvol_min=1.5,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=5_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
    ),

    # ── Combined relaxation: lower price floor AND RVOL ──
    "relaxed_price_rvol": FilterProfile(
        name="relaxed_price_rvol",
        description="Lower price floor to $1.50 AND RVOL to 1.5x. "
                    "Tests whether combining both relaxations improves "
                    "combined score more than either alone.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=1.5,
        absolute_volume_min=250_000,
        gap_pct_min=0.05,
        dollar_volume_min=2_000_000,
        extreme_rvol_override=25.0,
        price_override_dollar_vol=1_000_000,
        price_override_rvol=4.0,
    ),

    # ── D161: Short-only strategy — short high-faller-score rejects ──
    # Used by ShortAnalyzer to backtest the D161 short path in isolation.
    # Not a scanner profile in the traditional sense — faller_score_threshold
    # acts as the short entry gate instead of rejection gate.
    "short_only": FilterProfile(
        name="short_only",
        description="D161: Short-only strategy — short stocks that score >0.65 on "
                    "the faller gate instead of rejecting them. Requires RVOL>=3x, "
                    "dolvol>=$500K, gap>=20%. No long trades. "
                    "Validates ARTL (-55%), EEIQ (+$19K), SST (filtered by dolvol) thesis.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=3.0,           # D161: Short requires higher RVOL than long
        absolute_volume_min=500_000,
        gap_pct_min=0.20,       # D161: Only short overextended gap-ups (>=20%)
        dollar_volume_min=500_000,  # D161: Lower than long — just enough to cover
        extreme_rvol_override=30.0,
        price_override_dollar_vol=500_000,
        price_override_rvol=3.0,
        price_override_mega_dollar_vol=10_000_000,
        faller_score_threshold=0.65,  # Short qualification threshold (not rejection)
    ),

    # ── D161: Combined long + short strategy ──
    # Standard long scanner + short high-faller rejects
    "combined_long_short": FilterProfile(
        name="combined_long_short",
        description="D161: Combined long + short strategy. Long scanner uses current D160 "
                    "parameters. High-faller rejects (score>0.65) that pass RVOL/dolvol/gap "
                    "checks are shorted instead of rejected. Best of both worlds.",
        price_floor=1.50,
        price_floor_high_vol=0.50,
        rvol_min=2.0,
        absolute_volume_min=500_000,
        gap_pct_min=0.05,
        dollar_volume_min=2_000_000,
        extreme_rvol_override=30.0,
        price_override_dollar_vol=2_000_000,
        price_override_rvol=5.0,
        price_override_mega_dollar_vol=10_000_000,
        faller_score_threshold=0.65,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# Profile loading utilities
# ═══════════════════════════════════════════════════════════════════

def get_profile(name: str) -> FilterProfile:
    """Get a profile by name. Raises KeyError if not found."""
    if name not in PROFILES:
        available = sorted(PROFILES.keys())
        raise KeyError(f"Profile '{name}' not found. Available: {available}")
    return PROFILES[name]


def get_all_profiles() -> list[FilterProfile]:
    """Return all registered profiles."""
    return list(PROFILES.values())


def load_profiles_from_file(path: Path) -> dict[str, FilterProfile]:
    """Load custom filter profiles from a JSON file.

    File format:
    {
      "profiles": [
        {
          "name": "my_profile",
          "description": "...",
          "price_floor": 2.00,
          "rvol_min": 1.5,
          ...
        }
      ]
    }
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    loaded = {}
    for p_data in data.get("profiles", []):
        try:
            profile = FilterProfile(**p_data)
            loaded[profile.name] = profile
        except Exception as e:
            logger.warning("Skipping invalid profile %s: %s", p_data.get("name", "?"), e)

    logger.info("Loaded %d custom profiles from %s", len(loaded), path)
    return loaded


def register_profile(profile: FilterProfile) -> None:
    """Register a profile into the global PROFILES dict."""
    PROFILES[profile.name] = profile


def profile_diff(a: FilterProfile, b: FilterProfile) -> dict[str, tuple]:
    """Return fields that differ between two profiles.

    Returns: {field_name: (a_value, b_value)}
    """
    diffs = {}
    for field_name in a.__dataclass_fields__:
        if field_name in ("name", "description"):
            continue
        va = getattr(a, field_name)
        vb = getattr(b, field_name)
        if va != vb:
            diffs[field_name] = (va, vb)
    return diffs


def profiles_summary_table(profile_names: list[str] | None = None) -> str:
    """Return a formatted table of profile parameters for comparison."""
    names = profile_names or sorted(PROFILES.keys())
    profiles = [PROFILES[n] for n in names if n in PROFILES]

    if not profiles:
        return "No profiles found."

    headers = ["Profile", "price_floor", "rvol_min", "gap_pct_min", "dolvol_min", "require_news"]
    rows = []
    for p in profiles:
        rows.append([
            p.name,
            f"${p.price_floor:.2f}",
            f"{p.rvol_min:.1f}x",
            f"{p.gap_pct_min:.0%}",
            f"${p.dollar_volume_min / 1e6:.1f}M",
            str(p.require_news),
        ])

    col_widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    separator = "  ".join("-" * w for w in col_widths)

    lines = [fmt.format(*headers), separator]
    for row in rows:
        lines.append(fmt.format(*row))
    return "\n".join(lines)
