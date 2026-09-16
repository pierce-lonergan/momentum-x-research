"""Storage parity audit for the four data modules slated for v2 feature plumbing.

Walks the on-disk storage layer (if any) of:
  - src/data/short_interest.py
  - src/data/sentiment_velocity.py
  - src/data/order_flow.py
  - src/data/premarket_velocity.py

For each module, reports earliest date, latest date, row count, sample shape.
Classifies READY / PARTIAL (specify missing window) / LIVE-ONLY.

Designed to be re-run after persistence is added to any module — at that point
the module's section will flip from LIVE-ONLY to READY/PARTIAL with real data.

Usage:
    python scripts/audit_module_storage.py
    python scripts/audit_module_storage.py --window 2025-12-11 2026-04-14

Exit code:
    0 = at least one module READY for the given window (or no window specified)
    1 = all modules LIVE-ONLY or PARTIAL for the given window
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ModuleAudit:
    name: str
    candidate_paths: list[Path] = field(default_factory=list)
    found_paths: list[Path] = field(default_factory=list)
    earliest_date: str | None = None
    latest_date: str | None = None
    row_count: int = 0
    sample_keys: list[str] = field(default_factory=list)
    classification: str = "LIVE-ONLY"  # READY | PARTIAL | LIVE-ONLY
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"### {self.name}", ""]
        lines.append(f"**Classification:** {self.classification}")
        lines.append("")
        lines.append("Candidate storage paths checked:")
        for p in self.candidate_paths:
            present = "FOUND" if p in self.found_paths else "absent"
            lines.append(f"  - `{p.relative_to(_PROJECT_ROOT)}` -- {present}")
        lines.append("")
        if self.row_count > 0:
            lines.append(f"Earliest row: {self.earliest_date}")
            lines.append(f"Latest row:   {self.latest_date}")
            lines.append(f"Row count:    {self.row_count}")
            lines.append(f"Sample keys:  {self.sample_keys}")
        else:
            lines.append("No rows found in any candidate path.")
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            for n in self.notes:
                lines.append(f"  - {n}")
        return "\n".join(lines)


def _scan_jsonl_dir(audit: ModuleAudit, root: Path) -> None:
    """Scan a directory for *.jsonl files and accumulate stats into audit."""
    if not root.exists() or not root.is_dir():
        return
    audit.found_paths.append(root)
    files = sorted(root.glob("*.jsonl"))
    if not files:
        audit.notes.append(f"{root} exists but contains no .jsonl files")
        return

    dates: list[str] = []
    sample_keys_set: set[str] = set()
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:  # noqa: silent-handler
                    # Streaming reader of historical JSONL; one malformed
                    # line in a 5K-row file must not abort the audit.
                    continue
                audit.row_count += 1
                # Try common date fields
                for key in ("date", "session_date", "timestamp", "fetched_at", "as_of"):
                    if key in row and isinstance(row[key], str):
                        dates.append(row[key][:10])
                        break
                if not sample_keys_set:
                    sample_keys_set = set(row.keys())

    if dates:
        dates.sort()
        audit.earliest_date = dates[0]
        audit.latest_date = dates[-1]
    audit.sample_keys = sorted(sample_keys_set)


def audit_short_interest() -> ModuleAudit:
    a = ModuleAudit(name="short_interest")
    a.candidate_paths = [
        _PROJECT_ROOT / "data" / "short_interest",
        _PROJECT_ROOT / "data" / "shortinterest",
    ]
    for p in a.candidate_paths:
        _scan_jsonl_dir(a, p)
    if a.row_count == 0:
        a.notes.append(
            "Module is in-memory TTL cache only (src/data/short_interest.py:188 "
            "`self._cache: dict[str, _CacheEntry] = {}`). "
            "Tier cascade: Alpaca -> yfinance -> Finviz -> FINRA stub. "
            "No disk writes anywhere in the module."
        )
        a.notes.append(
            "Backfill path: Alpaca short-interest API supports historical fetches "
            "but with FINRA's 2-week lag; per-(ticker, date) reconstruction would "
            "need ~5,800 API calls."
        )
    return a


def audit_sentiment_velocity() -> ModuleAudit:
    a = ModuleAudit(name="sentiment_velocity")
    a.candidate_paths = [
        _PROJECT_ROOT / "data" / "sentiment_velocity",
        _PROJECT_ROOT / "data" / "sentiment",
        _PROJECT_ROOT / "data" / "headlines",
    ]
    for p in a.candidate_paths:
        _scan_jsonl_dir(a, p)
    if a.row_count == 0:
        a.notes.append(
            "Module is in-session memory only (src/data/sentiment_velocity.py:164 "
            "`self._trackers: dict[str, SentimentVelocityResult] = {}`). "
            "Hawkes intensity recomputed on demand. No disk persistence."
        )
        a.notes.append(
            "Backfill path: requires upstream historical headline archive "
            "(Benzinga, NewsAPI, or similar). Headlines themselves are not "
            "stored anywhere in the repo."
        )
    return a


def audit_order_flow() -> ModuleAudit:
    a = ModuleAudit(name="order_flow")
    a.candidate_paths = [
        _PROJECT_ROOT / "data" / "order_flow",
        _PROJECT_ROOT / "data" / "orderflow",
        _PROJECT_ROOT / "data" / "trades",
    ]
    for p in a.candidate_paths:
        _scan_jsonl_dir(a, p)
    if a.row_count == 0:
        a.notes.append(
            "Module is stateless analyzer (src/data/order_flow.py:222 "
            "`analyze_trades(ticker, trades, bid, ask)`). No instance state, no caching. "
            "Returns fresh OrderFlowResult per call."
        )
        a.notes.append(
            "Backfill path: requires Alpaca historical T&S replay through the analyzer "
            "for each (ticker, date). Estimated ~5,800 ticker-date pairs x thousands of "
            "ticks/day = significant API quota and storage."
        )
    return a


def audit_premarket_velocity() -> ModuleAudit:
    a = ModuleAudit(name="premarket_velocity")
    a.candidate_paths = [
        _PROJECT_ROOT / "data" / "premarket_velocity",
        _PROJECT_ROOT / "data" / "premarket",
    ]
    for p in a.candidate_paths:
        _scan_jsonl_dir(a, p)
    if a.row_count == 0:
        a.notes.append(
            "Module is in-session memory only (src/data/premarket_velocity.py:61 "
            "`self._snapshots: dict[str, list[VelocitySnapshot]] = {}`). "
            "`reset()` clears at session start."
        )
        a.notes.append(
            "Backfill path: TRACTABLE -- premarket bars already exist on disk at "
            "data/bar_recordings/<date>/<ticker>.json. Snapshots can be reconstructed "
            "by replaying these bars through the tracker. Estimated ~half-day of work."
        )
    return a


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Storage parity audit for v2 feature modules."
    )
    parser.add_argument(
        "--window",
        nargs=2,
        metavar=("START", "END"),
        help="Optional (start, end) date window to classify as READY/PARTIAL.",
    )
    args = parser.parse_args()

    window: tuple[date, date] | None = None
    if args.window:
        try:
            window = (
                datetime.strptime(args.window[0], "%Y-%m-%d").date(),
                datetime.strptime(args.window[1], "%Y-%m-%d").date(),
            )
        except ValueError as e:
            print(f"invalid --window date: {e}", file=sys.stderr)
            return 2

    audits = [
        audit_short_interest(),
        audit_sentiment_velocity(),
        audit_order_flow(),
        audit_premarket_velocity(),
    ]

    for a in audits:
        if a.row_count > 0 and window is not None:
            try:
                earliest = datetime.strptime(a.earliest_date, "%Y-%m-%d").date()
                latest = datetime.strptime(a.latest_date, "%Y-%m-%d").date()
                if earliest <= window[0] and latest >= window[1]:
                    a.classification = "READY"
                else:
                    a.classification = "PARTIAL"
                    a.notes.append(
                        f"Coverage [{earliest}..{latest}] does not span requested "
                        f"window [{window[0]}..{window[1]}]."
                    )
            except (ValueError, TypeError):
                a.classification = "PARTIAL"
                a.notes.append("Could not parse coverage dates for window check.")

    print("# Storage Parity Audit\n")
    if window:
        print(f"Window: {window[0]} - {window[1]}\n")
    else:
        print("No window specified; classifications report disk presence only.\n")
    for a in audits:
        print(a.render())
        print()

    has_ready = any(a.classification == "READY" for a in audits)
    return 0 if has_ready else 1


if __name__ == "__main__":
    sys.exit(main())
