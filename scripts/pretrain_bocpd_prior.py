"""Pre-train the BOCPD prior from the existing trade-results corpus.

Per `docs/research-log/25_bug_hunting_playbook.md` §3.1 + items 21-23 of the
2026-04-25 next-actions list. Reads `data/trade_results.jsonl`, filters
infrastructure_contaminated trades (LIDR Bug Z fake-positive et al.),
fits μ_edge / σ_edge, and persists to
`data/priors/s1_bocpd_prior.parquet` for session-startup load.

Usage:
    python scripts/pretrain_bocpd_prior.py [--dry-run]

The contamination filter mirrors today's understanding:
  - LIDR (Bug Z fake-positive close) — explicit infrastructure_contaminated
  - Future tags will be set in the trade_results.jsonl row at write time
    (this script just respects whatever is already in the file)

Reproducible: re-running on the same corpus produces an identical prior.
The output Parquet's `generated_at` field is the only difference between
runs; all numeric fields are deterministic.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.bocpd import BOCPDPrior  # noqa: E402

# Hard-coded contamination list per `30_bug_w_lidr_evidence.md` +
# `32_bug_aa_simple_broker_fill_discipline.md`. Trade rows in
# `data/trade_results.jsonl` matching these (ticker, session_date) are
# filtered regardless of the row's own infrastructure_contaminated flag
# (because the rows pre-date the flag's introduction).
KNOWN_CONTAMINATED: set[tuple[str, str]] = {
    ("LIDR", "2026-04-24"),  # Bug Z fake-positive close ($421.12 → actual -$1,210.72)
    # doc 177 (2026-05-28): the D76 EOD close booked unrealized ghost MTM as
    # realized P&L when client.close_position 403'd (qty reserved by a
    # protective stop) and execution fell through to close_with_attribution.
    # Same fake-positive class as LIDR/Bug Z, now recurring on overnight ghosts.
    # Root cause fixed in main.py D76 (route through cancel-blocking-stops
    # wrapper + only book on confirmed broker close). These two rows are the
    # known phantoms in the corpus; also tagged infrastructure_contaminated in
    # trade_results.jsonl. Broker truth: 5/27 net -$759, 5/28 realized -$269.56.
    ("LFS", "2026-05-27"),   # phantom +$1,403.36 (basic-path ghost MTM; broker day was -$759)
    ("APPS", "2026-05-28"),  # phantom +$1,425.90 (overnight ghost carryover; broker close 403'd)
    # AUUD, TZOO, CPIX referenced in the user's plan are not in the current
    # trade_results.jsonl corpus — adding here as placeholders for when they
    # appear in older / restored journal data.
}


def _is_contaminated(row: dict) -> bool:
    if row.get("infrastructure_contaminated"):
        return True
    key = (row.get("ticker", ""), row.get("session_date", ""))
    return key in KNOWN_CONTAMINATED


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the prior values without writing the Parquet file.",
    )
    parser.add_argument(
        "--corpus", default="data/trade_results.jsonl",
        help="Path to the trade-outcome corpus (JSONL).",
    )
    parser.add_argument(
        "--output", default="data/priors/s1_bocpd_prior.parquet",
        help="Output path for the persisted prior.",
    )
    args = parser.parse_args()

    corpus_path = REPO_ROOT / args.corpus
    output_path = REPO_ROOT / args.output

    if not corpus_path.exists():
        print(f"[bocpd-pretrain] corpus not found: {corpus_path}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[bocpd-pretrain] skipping bad row: {e}", file=sys.stderr)
                continue
            # Tag row with contamination flag from KNOWN_CONTAMINATED if not already set
            if _is_contaminated(row):
                row["infrastructure_contaminated"] = True
            rows.append(row)

    print(f"[bocpd-pretrain] loaded {len(rows)} rows from {corpus_path}")

    prior = BOCPDPrior.from_corpus(rows)
    print(f"[bocpd-pretrain] fit prior:")
    print(f"  mu_edge      = {prior.mu_edge:.4f}")
    print(f"  sigma_edge   = {prior.sigma_edge:.4f}")
    print(f"  hazard_rate  = {prior.hazard_rate:.6f} (expected run length: {1/prior.hazard_rate:.0f})")
    print(f"  n_trades     = {prior.n_trades}")
    print(f"  filtered     = {prior.filtered_count}")
    print(f"  corpus_dates = {prior.corpus_dates}")
    print(f"  generated_at = {prior.generated_at}")

    if args.dry_run:
        print("[bocpd-pretrain] dry-run — not writing.")
        return 0

    prior.to_parquet(output_path)
    print(f"[bocpd-pretrain] wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
