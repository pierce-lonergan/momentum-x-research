"""Parity validation for D221 Phase F news_agent distillation.

Replays all historical news_agent records from data/journals/journal_*.jsonl
through the new compute_news_features() function and verifies:

  (1) Feature extraction handles every observed input combination without
      raising and produces a valid 12-dim dict.
  (2) Migrated callers' new feature-based logic produces identical decisions
      to the old verdict-based logic on every historical record:
        - orchestrator.py:1260 D91 degraded-mode _news_bull
        - faller_detection.py:704 catalyst quality BULL gate
        - faller_detection.py:723 bearish detection gate

Exit code:
  0  if parity holds across ALL records
  1  if any divergence is detected (the commit MUST be reverted)
  2  on unexpected errors during replay

Usage:
  python scripts/parity_news_features.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from src.agents.news_agent import compute_news_features

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JOURNALS_DIR = _PROJECT_ROOT / "data" / "journals"


def _extract_news_signals(journal_dir: Path) -> list[dict]:
    """Walk all journal files and collect every news_agent signal record."""
    out: list[dict] = []
    for journal in sorted(journal_dir.glob("journal_*.jsonl")):
        with open(journal, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:  # noqa: silent-handler
                    # Streaming reader of historical journals; skip malformed
                    # lines so the parity validation still runs on the rest.
                    continue
                for sig in row.get("agent_signals", []) or []:
                    if not isinstance(sig, dict):
                        continue
                    if sig.get("agent_id") == "news_agent":
                        out.append(sig)
    return out


def _extract_fields(sig: dict) -> dict:
    """Extract compute_news_features kwargs from a historical sig record.

    The historical records use the NewsSignal schema from BEFORE the
    distillation (no news_features field). They have:
      - signal (direct)
      - confidence (direct)
      - reasoning (direct)
      - flags (red_flags equivalent)
      - key_data.catalyst_type
      - key_data.catalyst_specificity
      - key_data.sentiment_score

    Some NewsSignal-level fields are also top-level:
      - catalyst_type (duplicated from key_data)
      - catalyst_specificity (duplicated)
      - sentiment_score (duplicated)
      - source_citations (top-level list)
    """
    key_data = sig.get("key_data") or {}
    return {
        "signal": sig.get("signal", "NEUTRAL"),
        "confidence": float(sig.get("confidence") or 0.0),
        "catalyst_type": (
            sig.get("catalyst_type")
            or key_data.get("catalyst_type")
            or "NONE"
        ),
        "specificity": (
            sig.get("catalyst_specificity")
            or key_data.get("catalyst_specificity")
            or "SPECULATIVE"
        ),
        "sentiment_score": float(
            sig.get("sentiment_score")
            if sig.get("sentiment_score") is not None
            else (key_data.get("sentiment_score") or 0.0)
        ),
        "reasoning": sig.get("reasoning", "") or "",
        "red_flags": sig.get("flags", []) or [],
        "source_citations": sig.get("source_citations", []) or [],
    }


def _decision_old_news_bull(signal: str) -> bool:
    """Old orchestrator.py:1260 logic: .signal in ("BULL", "STRONG_BULL",
    "BULLISH", "STRONG_BUY", "BUY")."""
    return signal in ("BULL", "STRONG_BULL", "BULLISH", "STRONG_BUY", "BUY")


def _decision_new_news_bull(features: dict) -> bool:
    """New logic: features["signal_direction_numeric"] >= 1.0."""
    return features.get("signal_direction_numeric", 0.0) >= 1.0


def _decision_old_catalyst_quality(signal: str) -> bool:
    """Old faller_detection.py:704 logic: .signal in ("BULL", "STRONG_BULL")."""
    return signal in ("BULL", "STRONG_BULL")


def _decision_new_catalyst_quality(features: dict) -> bool:
    """New logic: features["signal_direction_numeric"] >= 1.0."""
    return features.get("signal_direction_numeric", 0.0) >= 1.0


def _decision_old_bearish(signal: str) -> bool:
    """Old faller_detection.py:723: .signal in ("BEAR", "STRONG_BEAR")."""
    return signal in ("BEAR", "STRONG_BEAR")


def _decision_new_bearish(features: dict) -> bool:
    """New logic: features["signal_direction_numeric"] <= -1.0."""
    return features.get("signal_direction_numeric", 0.0) <= -1.0


def main() -> int:
    print("=" * 72)
    print("D221 Phase F — Parity validation: news_agent distillation")
    print("=" * 72)
    print(f"Journals directory: {_JOURNALS_DIR}")

    if not _JOURNALS_DIR.exists():
        print(f"ERROR: journals directory does not exist: {_JOURNALS_DIR}")
        return 2

    records = _extract_news_signals(_JOURNALS_DIR)
    print(f"Collected {len(records)} news_agent records")
    if not records:
        print("No records to validate; aborting.")
        return 2

    # ─ Validation 1: feature extraction completes on every record ─
    extraction_failures: list[dict] = []
    range_failures: list[dict] = []
    all_features: list[dict] = []

    for i, sig in enumerate(records):
        fields = _extract_fields(sig)
        try:
            features = compute_news_features(**fields)
        except Exception as e:
            extraction_failures.append({
                "record_index": i,
                "error": f"{type(e).__name__}: {e}",
                "fields": fields,
            })
            continue

        if len(features) != 12:
            range_failures.append({
                "record_index": i,
                "issue": f"expected 12 features, got {len(features)}",
            })
            continue

        # Range checks
        f = features
        if not (0.0 <= f["catalyst_quality_score"] <= 1.0):
            range_failures.append({"record_index": i, "issue": "catalyst_quality_score out of [0,1]"})
        if not (0.0 <= f["catalyst_specificity_score"] <= 1.0):
            range_failures.append({"record_index": i, "issue": "catalyst_specificity_score out of [0,1]"})
        if not (-1.0 <= f["sentiment_score"] <= 1.0):
            range_failures.append({"record_index": i, "issue": "sentiment_score out of [-1,1]"})
        if not (0.0 <= f["sentiment_intensity"] <= 1.0):
            range_failures.append({"record_index": i, "issue": "sentiment_intensity out of [0,1]"})
        if not (-2.0 <= f["signal_direction_numeric"] <= 2.0):
            range_failures.append({"record_index": i, "issue": "signal_direction_numeric out of [-2,2]"})
        if not (0.0 <= f["confidence"] <= 1.0):
            range_failures.append({"record_index": i, "issue": "confidence out of [0,1]"})
        all_features.append({"record": sig, "fields": fields, "features": features})

    print()
    print(f"  Extraction failures: {len(extraction_failures)}")
    print(f"  Range failures:      {len(range_failures)}")
    if extraction_failures:
        print("  First 3 extraction failures:")
        for ef in extraction_failures[:3]:
            print(f"    [{ef['record_index']}] {ef['error']}")
            print(f"        fields={ef['fields']}")
    if range_failures:
        print("  First 3 range failures:")
        for rf in range_failures[:3]:
            print(f"    [{rf['record_index']}] {rf['issue']}")

    # ─ Validation 2: behavioral parity on all 3 migrated callers ─
    news_bull_mismatches: list[dict] = []
    catalyst_quality_mismatches: list[dict] = []
    bearish_mismatches: list[dict] = []

    for entry in all_features:
        sig = entry["record"]
        features = entry["features"]
        signal = sig.get("signal", "NEUTRAL")

        old_nb = _decision_old_news_bull(signal)
        new_nb = _decision_new_news_bull(features)
        if old_nb != new_nb:
            news_bull_mismatches.append({
                "signal": signal,
                "old": old_nb,
                "new": new_nb,
                "features_sig_num": features["signal_direction_numeric"],
            })

        old_cq = _decision_old_catalyst_quality(signal)
        new_cq = _decision_new_catalyst_quality(features)
        if old_cq != new_cq:
            catalyst_quality_mismatches.append({
                "signal": signal,
                "old": old_cq,
                "new": new_cq,
                "features_sig_num": features["signal_direction_numeric"],
            })

        old_bear = _decision_old_bearish(signal)
        new_bear = _decision_new_bearish(features)
        if old_bear != new_bear:
            bearish_mismatches.append({
                "signal": signal,
                "old": old_bear,
                "new": new_bear,
                "features_sig_num": features["signal_direction_numeric"],
            })

    print()
    print("Behavioral parity on migrated callers:")
    print(f"  orchestrator.py:1260 (D91 _news_bull)       mismatches: {len(news_bull_mismatches)}")
    print(f"  faller_detection.py:704 (catalyst quality)   mismatches: {len(catalyst_quality_mismatches)}")
    print(f"  faller_detection.py:723 (bearish detection) mismatches: {len(bearish_mismatches)}")

    if news_bull_mismatches:
        print("\n  First news_bull mismatches:")
        for m in news_bull_mismatches[:5]:
            print(f"    signal={m['signal']}  old={m['old']}  new={m['new']}  "
                  f"sig_num={m['features_sig_num']}")
    if catalyst_quality_mismatches:
        print("\n  First catalyst_quality mismatches:")
        for m in catalyst_quality_mismatches[:5]:
            print(f"    signal={m['signal']}  old={m['old']}  new={m['new']}  "
                  f"sig_num={m['features_sig_num']}")
    if bearish_mismatches:
        print("\n  First bearish mismatches:")
        for m in bearish_mismatches[:5]:
            print(f"    signal={m['signal']}  old={m['old']}  new={m['new']}  "
                  f"sig_num={m['features_sig_num']}")

    # ─ Summary ─
    print()
    print("=" * 72)
    signal_dist = Counter(sig.get("signal", "NEUTRAL") for sig in records)
    print(f"Signal distribution observed in historical records:")
    for s, n in signal_dist.most_common():
        print(f"  {s:15s}: {n}")

    catalyst_dist = Counter(
        (sig.get("catalyst_type") or (sig.get("key_data") or {}).get("catalyst_type") or "NONE")
        for sig in records
    )
    print(f"\nCatalyst type distribution:")
    for c, n in catalyst_dist.most_common(8):
        print(f"  {c:20s}: {n}")

    total_issues = (
        len(extraction_failures) + len(range_failures)
        + len(news_bull_mismatches) + len(catalyst_quality_mismatches)
        + len(bearish_mismatches)
    )

    print()
    print("=" * 72)
    if total_issues == 0:
        print(f"PARITY OK -- {len(records)} historical records validated")
        print("  - 0 extraction failures")
        print("  - 0 range failures")
        print("  - 0 behavioral mismatches across 3 migrated callers")
        print("Commit may proceed.")
        return 0

    print(f"PARITY FAIL -- {total_issues} total issues across {len(records)} records")
    print("The commit MUST be reverted.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
