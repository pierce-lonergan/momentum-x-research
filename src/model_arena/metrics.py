"""Metrics + ranking for cross-model arena results.

Stdlib only. Outputs a list[dict] suitable for tabulation; callers can convert
to pandas / CSV as needed.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Iterable

from src.model_arena.runner import ModelResponse


def compute_metrics(responses: Iterable[ModelResponse]) -> list[dict]:
    """Per-model aggregate metrics.

    Returns one dict per model_key with:
      - n: total samples
      - n_ok: non-error responses
      - error_rate: errors / n
      - n_correct: correct predictions (excluding errors)
      - accuracy: n_correct / n_ok
      - macro_f1: macro-F1 across classes (skips classes with no truth examples)
      - p50_latency_ms, p95_latency_ms
      - total_cost_usd, avg_cost_per_call_usd
      - cost_per_correct_usd: total_cost / n_correct (inf if 0 correct)
    """
    by_model: dict[str, list[ModelResponse]] = defaultdict(list)
    for r in responses:
        by_model[r.model_key].append(r)

    out: list[dict] = []
    for model_key, rs in by_model.items():
        n = len(rs)
        n_ok = sum(1 for r in rs if r.error is None)
        n_err = n - n_ok
        n_correct = sum(1 for r in rs if r.correct)
        latencies = [r.latency_ms for r in rs if r.error is None]
        costs = [r.cost_usd for r in rs]
        total_cost = sum(costs)

        out.append({
            "model_key": model_key,
            "n": n,
            "n_ok": n_ok,
            "error_rate": n_err / n if n else 0.0,
            "n_correct": n_correct,
            "accuracy": n_correct / n_ok if n_ok else 0.0,
            "macro_f1": _macro_f1(rs),
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "total_cost_usd": total_cost,
            "avg_cost_per_call_usd": total_cost / n if n else 0.0,
            "cost_per_correct_usd": total_cost / n_correct if n_correct else float("inf"),
        })
    return out


def rank_by_cost_per_correct(metrics: list[dict]) -> list[dict]:
    """Sort by cost_per_correct ascending (cheapest accurate model first).

    Models with 0 correct are pushed to the end.
    """
    return sorted(metrics, key=lambda m: (m["cost_per_correct_usd"], -m["accuracy"]))


# ── helpers ─────────────────────────────────────────────────────────────


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = int(len(sorted_vals) * p / 100.0)
    k = max(0, min(len(sorted_vals) - 1, k))
    return sorted_vals[k]


def _macro_f1(responses: list[ModelResponse]) -> float:
    """Macro-averaged F1 across classes present in `truth`.

    For each class C: precision = TP/(TP+FP), recall = TP/(TP+FN),
    F1 = 2*P*R/(P+R). Macro = mean across classes.
    """
    classes = sorted({r.truth for r in responses})
    if not classes:
        return 0.0
    f1s = []
    for c in classes:
        tp = sum(1 for r in responses if r.truth == c and r.prediction == c)
        fp = sum(1 for r in responses if r.truth != c and r.prediction == c)
        fn = sum(1 for r in responses if r.truth == c and r.prediction != c)
        if tp == 0 and fp == 0 and fn == 0:
            continue
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        f1s.append(f1)
    return statistics.mean(f1s) if f1s else 0.0


def confusion_matrix(responses: list[ModelResponse]) -> dict:
    """{(truth, prediction): count}. Useful for failure-mode analysis."""
    cm: Counter = Counter()
    for r in responses:
        cm[(r.truth, r.prediction or "ERROR")] += 1
    return dict(cm)
