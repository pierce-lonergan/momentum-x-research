"""CLI: replay a production session log against an alternate rule set.

Tier 4 #15 operator entry point. The "rigorous answer" tool.

Usage:
  # Validate Bug AK against today's actual session log
  python scripts/replay_session.py logs/momentum_2026-04-27.log

  # Compare v1 (pre-Bug AK) vs v2 (post-Bug AK) explicitly
  python scripts/replay_session.py logs/momentum_2026-04-27.log \\
      --rule-version v2_bug_ak

  # Dump the parsed decision corpus to JSONL for further analysis
  python scripts/replay_session.py logs/momentum_2026-04-27.log \\
      --output-corpus data/decision_replay/2026-04-27.jsonl
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow the script to find src/ even when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.decision_replay.log_parser import parse_log_file, parse_log_to_jsonl
from src.analysis.decision_replay.counterfactual import replay_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", help="Path to logs/momentum_<date>.log")
    parser.add_argument(
        "--rule-version", default="v2_bug_ak",
        choices=["v1_pre_bug_ak", "v2_bug_ak"],
        help="Which D124 rule variant to replay against",
    )
    parser.add_argument(
        "--output-corpus", default=None,
        help="If set, also dump parsed DecisionRow JSONL here",
    )
    parser.add_argument(
        "--session-date", default=None,
        help="ISO date string; inferred from log filename if not set",
    )
    args = parser.parse_args()

    log_path = Path(args.log_path)
    if not log_path.exists():
        print(f"ERROR: log file not found: {log_path}", file=sys.stderr)
        return 1

    # Optionally dump the corpus for downstream tools
    if args.output_corpus:
        n = parse_log_to_jsonl(
            log_path, args.output_corpus, session_date=args.session_date,
        )
        print(f"Parsed {n} decisions to {args.output_corpus}")

    # Build the corpus iterator (single-pass, low memory)
    corpus = list(parse_log_file(log_path, session_date=args.session_date))
    if not corpus:
        print(
            "WARNING: no decisions parsed from the log. "
            "(Are you sure this is a momentum_<date>.log file with VERDICT lines?)",
            file=sys.stderr,
        )
        return 2

    print(f"Parsed {len(corpus)} decisions from {log_path.name}")
    print(f"Replaying against D124 rule_version={args.rule_version!r}...")
    print()

    report = replay_corpus(corpus, rule_kind="d124", rule_version=args.rule_version)
    print(report.summary_text())

    return 0


if __name__ == "__main__":
    sys.exit(main())
