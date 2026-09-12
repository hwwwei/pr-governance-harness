from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Any

from .providers import AgentProvider, MockProvider


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    split: str
    language: str
    category: str
    severity: str
    diff: str
    expected: tuple[str, ...]


def build_cases() -> list[BenchmarkCase]:
    fixture = Path(__file__).parent.parent / "benchmark" / "cases.json"
    raw_cases = json.loads(fixture.read_text(encoding="utf-8"))
    return [
        BenchmarkCase(
            case_id=item["case_id"],
            split=item["split"],
            language=item["language"],
            category=item["category"],
            severity=item["severity"],
            diff=item["diff"],
            expected=tuple(item["expected"]),
        )
        for item in raw_cases
    ]


def _evaluate_cases(cases: list[BenchmarkCase], provider: AgentProvider, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    started_at = time.perf_counter()
    tp = fp = fn = high_tp = high_total = clean_total = clean_correct = 0
    for case in cases:
        predicted: set[str] = set()
        context = {"evolution": {"candidate": candidate or {}}}
        for role in ("security", "reliability"):
            predicted.update(item.get("type") for item in provider.analyze(role, case.diff, context).get("findings", []))
        expected = set(case.expected)
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
        if case.severity in {"high", "critical"}:
            high_total += 1
            high_tp += bool(predicted & expected)
        if case.category == "clean":
            clean_total += 1
            clean_correct += not predicted
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    elapsed = time.perf_counter() - started_at
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "high_risk_recall": round(high_tp / high_total, 4) if high_total else 1.0,
        "clean_pr_specificity": round(clean_correct / clean_total, 4) if clean_total else 1.0,
        "average_seconds": round(elapsed / len(cases), 6) if cases else 0.0,
        "model_calls": getattr(provider, "calls", 0),
    }


def evaluate(provider: AgentProvider | None = None, split: str | None = None, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    cases = build_cases()
    selected = [case for case in cases if split is None or case.split == split]
    active_provider = provider or MockProvider()
    metrics = _evaluate_cases(selected, active_provider, candidate)
    result: dict[str, Any] = {
        "cases": len(selected),
        "validation_cases": sum(case.split == "validation" for case in selected),
        "holdout_cases": sum(case.split == "holdout" for case in selected),
        "split": split or "all",
        "metrics": metrics,
    }
    if split is None:
        result["validation"] = evaluate(MockProvider(), split="validation", candidate=candidate)["metrics"]
        result["holdout"] = evaluate(MockProvider(), split="holdout", candidate=candidate)["metrics"]
    return result
