"""D200-E3: Regime-Conditional Strategy Analysis.

Partitions historical scenarios by market regime (VIX level, gap density,
day-of-week, market trend) and measures win rate per regime. Identifies
regimes with negative expected value where the system should NOT trade.

Data sources:
- data/scenarios/gap_scenarios.json (196 labeled scenarios with dates + outcomes)
- VIX data from yfinance (cached to data/regime/vix_history.csv)
"""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class RegimeBucket:
    """Win rate and P&L stats for a specific regime condition."""

    label: str
    total: int = 0
    wins: int = 0
    total_return_pct: float = 0.0
    scenarios: list[dict] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.0

    @property
    def avg_return_pct(self) -> float:
        return self.total_return_pct / self.total if self.total > 0 else 0.0

    @property
    def positive_ev(self) -> bool:
        return self.avg_return_pct > 0 and self.total >= 5


@dataclass
class RegimeReport:
    """Complete regime analysis output."""

    by_vix: dict[str, RegimeBucket] = field(default_factory=dict)
    by_day_of_week: dict[str, RegimeBucket] = field(default_factory=dict)
    by_gap_density: dict[str, RegimeBucket] = field(default_factory=dict)
    by_gap_size: dict[str, RegimeBucket] = field(default_factory=dict)
    negative_ev_regimes: list[str] = field(default_factory=list)
    total_scenarios: int = 0
    scenarios_with_vix: int = 0


class RegimeAnalyzer:
    """Partitions scenarios by regime and computes conditional win rates."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or (_PROJECT_ROOT / "data")
        self._scenarios: list[dict] = []
        self._vix_data: dict[str, float] = {}  # date → VIX close

    def load_scenarios(self) -> int:
        """Load labeled gap scenarios."""
        sc_path = self._data_dir / "scenarios" / "gap_scenarios.json"
        if not sc_path.exists():
            logger.warning("No scenario file at %s", sc_path)
            return 0
        with open(sc_path, encoding="utf-8") as f:
            data = json.load(f)
        self._scenarios = data if isinstance(data, list) else data.get("scenarios", [])
        logger.info("D200-E3: Loaded %d scenarios", len(self._scenarios))
        return len(self._scenarios)

    def load_vix(self) -> int:
        """Load VIX history. Try cache first, then yfinance."""
        cache_path = self._data_dir / "regime" / "vix_history.csv"
        if cache_path.exists():
            return self._load_vix_csv(cache_path)

        # Try fetching from yfinance
        try:
            return self._fetch_vix_yfinance(cache_path)
        except Exception as e:
            logger.warning("D200-E3: VIX fetch failed: %s — regime analysis will skip VIX", e)
            return 0

    def analyze(self) -> RegimeReport:
        """Run regime analysis across all partition dimensions."""
        report = RegimeReport(total_scenarios=len(self._scenarios))

        # ── VIX-based partitioning ──────────────────────────────────
        vix_buckets = {
            "VIX < 15": RegimeBucket("VIX < 15"),
            "VIX 15-20": RegimeBucket("VIX 15-20"),
            "VIX 20-30": RegimeBucket("VIX 20-30"),
            "VIX > 30": RegimeBucket("VIX > 30"),
        }
        for s in self._scenarios:
            vix = self._vix_data.get(s["date"])
            if vix is not None:
                report.scenarios_with_vix += 1
                if vix < 15:
                    bucket = vix_buckets["VIX < 15"]
                elif vix < 20:
                    bucket = vix_buckets["VIX 15-20"]
                elif vix < 30:
                    bucket = vix_buckets["VIX 20-30"]
                else:
                    bucket = vix_buckets["VIX > 30"]
                self._add_to_bucket(bucket, s)
        report.by_vix = vix_buckets

        # ── Day-of-week partitioning ────────────────────────────────
        dow_buckets: dict[str, RegimeBucket] = {}
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        for name in dow_names:
            dow_buckets[name] = RegimeBucket(name)
        for s in self._scenarios:
            try:
                dt = datetime.strptime(s["date"], "%Y-%m-%d")
                dow = dow_names[dt.weekday()] if dt.weekday() < 5 else None
                if dow:
                    self._add_to_bucket(dow_buckets[dow], s)
            except (ValueError, IndexError):
                continue
        report.by_day_of_week = dow_buckets

        # ── Gap density partitioning ────────────────────────────────
        # Count how many scenarios share each date
        date_counts: dict[str, int] = defaultdict(int)
        for s in self._scenarios:
            date_counts[s["date"]] += 1

        density_buckets = {
            "Low (1-2 gaps)": RegimeBucket("Low (1-2 gaps)"),
            "Medium (3-5 gaps)": RegimeBucket("Medium (3-5 gaps)"),
            "High (6+ gaps)": RegimeBucket("High (6+ gaps)"),
        }
        for s in self._scenarios:
            count = date_counts.get(s["date"], 1)
            if count <= 2:
                self._add_to_bucket(density_buckets["Low (1-2 gaps)"], s)
            elif count <= 5:
                self._add_to_bucket(density_buckets["Medium (3-5 gaps)"], s)
            else:
                self._add_to_bucket(density_buckets["High (6+ gaps)"], s)
        report.by_gap_density = density_buckets

        # ── Gap size partitioning ───────────────────────────────────
        gap_buckets = {
            "Gap 5-20%": RegimeBucket("Gap 5-20%"),
            "Gap 20-50%": RegimeBucket("Gap 20-50%"),
            "Gap 50-100%": RegimeBucket("Gap 50-100%"),
            "Gap > 100%": RegimeBucket("Gap > 100%"),
        }
        for s in self._scenarios:
            gap = abs(s.get("gap_pct", 0))
            if gap < 0.20:
                self._add_to_bucket(gap_buckets["Gap 5-20%"], s)
            elif gap < 0.50:
                self._add_to_bucket(gap_buckets["Gap 20-50%"], s)
            elif gap < 1.00:
                self._add_to_bucket(gap_buckets["Gap 50-100%"], s)
            else:
                self._add_to_bucket(gap_buckets["Gap > 100%"], s)
        report.by_gap_size = gap_buckets

        # ── Identify negative EV regimes ────────────────────────────
        all_buckets = {
            **report.by_vix,
            **report.by_day_of_week,
            **report.by_gap_density,
            **report.by_gap_size,
        }
        for label, bucket in all_buckets.items():
            if bucket.total >= 5 and not bucket.positive_ev:
                report.negative_ev_regimes.append(
                    f"{label}: win_rate={bucket.win_rate:.0%} avg_ret={bucket.avg_return_pct:.1%} n={bucket.total}"
                )

        return report

    def format_report(self, report: RegimeReport) -> str:
        """Format as human-readable text."""
        lines = [
            "=" * 70,
            "D200-E3 REGIME-CONDITIONAL ANALYSIS",
            "=" * 70,
            f"Total scenarios: {report.total_scenarios}",
            f"Scenarios with VIX data: {report.scenarios_with_vix}",
            "",
        ]

        for section_name, buckets in [
            ("VIX LEVEL", report.by_vix),
            ("DAY OF WEEK", report.by_day_of_week),
            ("GAP DENSITY", report.by_gap_density),
            ("GAP SIZE", report.by_gap_size),
        ]:
            lines.append(f"─── {section_name} ───")
            for label, b in buckets.items():
                ev_tag = " ✓" if b.positive_ev else " ✗" if b.total >= 5 else ""
                bar = "#" * int(b.win_rate * 20) if b.total > 0 else ""
                lines.append(
                    f"  {label:20s} | n={b.total:3d} | "
                    f"win={b.win_rate:.0%} | avg_ret={b.avg_return_pct:+.1%}{ev_tag}  {bar}"
                )
            lines.append("")

        if report.negative_ev_regimes:
            lines.append("─── NEGATIVE EV REGIMES (sit these out) ───")
            for regime in report.negative_ev_regimes:
                lines.append(f"  ✗ {regime}")
        else:
            lines.append("No negative EV regimes found with n≥5.")

        lines.append("=" * 70)
        return "\n".join(lines)

    # ── Internal ────────────────────────────────────────────────────

    @staticmethod
    def _add_to_bucket(bucket: RegimeBucket, scenario: dict) -> None:
        bucket.total += 1
        if scenario.get("outcome") == "WIN":
            bucket.wins += 1
        bucket.total_return_pct += scenario.get("intraday_return", 0.0)
        bucket.scenarios.append(scenario)

    def _load_vix_csv(self, path: Path) -> int:
        """Load VIX from CSV cache."""
        loaded = 0
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = row.get("Date", row.get("date", ""))[:10]
                close_str = row.get("Close", row.get("close", ""))
                try:
                    self._vix_data[date_str] = float(close_str)
                    loaded += 1
                except (ValueError, TypeError):
                    continue
        logger.info("D200-E3: Loaded %d VIX data points from cache", loaded)
        return loaded

    def _fetch_vix_yfinance(self, cache_path: Path) -> int:
        """Fetch VIX from yfinance and cache to CSV."""
        import yfinance as yf

        vix = yf.download("^VIX", period="3y", progress=False)
        if vix.empty:
            return 0

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for idx, row in vix.iterrows():
            date_str = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
            close_val = float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"])
            self._vix_data[date_str] = close_val
            rows.append({"Date": date_str, "Close": f"{close_val:.2f}"})

        with open(cache_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Date", "Close"])
            writer.writeheader()
            writer.writerows(rows)

        logger.info("D200-E3: Fetched %d VIX data points, cached to %s", len(rows), cache_path)
        return len(rows)
