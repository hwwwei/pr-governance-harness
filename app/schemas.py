from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _validate_budget(value: dict[str, int] | None) -> dict[str, int] | None:
    if value is None:
        return None
    allowed = {"max_model_calls", "max_retries", "max_seconds"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown budget keys: {sorted(unknown)}")
    if any(int(item) < 0 for item in value.values()):
        raise ValueError("budget values must be non-negative")
    return value


class RunCreate(BaseModel):
    repository: str = Field(min_length=1, max_length=255)
    diff: str = Field(min_length=1)
    pr_number: int | None = Field(default=None, ge=1)
    budget: dict[str, int] = Field(default_factory=lambda: {"max_model_calls": 12, "max_retries": 1, "max_seconds": 60})

    _budget = field_validator("budget")(_validate_budget)


class ResumeRequest(BaseModel):
    """Optional budget increases; consumed budget is never reset on resume."""

    budget: dict[str, int] | None = None

    _budget = field_validator("budget")(_validate_budget)


class RunSummary(BaseModel):
    id: str
    repository: str
    pr_number: int | None
    status: str
    risk_level: str
    created_at: datetime


class Finding(BaseModel):
    id: str
    kind: Literal["security", "reliability", "quality"]
    severity: Literal["low", "medium", "high", "critical"]
    type: str
    file: str
    line: int | None = None
    evidence: str
    explanation: str
    confidence: float = Field(ge=0, le=1)
    recommendation: str
    source_stage: str
    fingerprint: str


class PatchValidation(BaseModel):
    valid: bool = False
    parseable: bool
    scope_allowed: bool
    safe: bool
    tests_not_executed: bool = True
    files: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PatchCandidate(BaseModel):
    patch: str
    rationale: str
    validation: PatchValidation


class RunReport(BaseModel):
    run_id: str
    status: str
    risk_level: str
    findings: list[Finding] = Field(default_factory=list)
    patch: PatchCandidate | None = None
    trace_id: str
    budget: dict[str, Any]
    checkpoint: dict[str, Any]


class NodeExecutionView(BaseModel):
    node: str
    attempt: int
    status: str
    duration_ms: float
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class TraceEventView(BaseModel):
    event_type: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunDetail(RunSummary):
    report: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    nodes: list[NodeExecutionView] = Field(default_factory=list)
    trace: list[TraceEventView] = Field(default_factory=list)


class FeedbackCreate(BaseModel):
    label: Literal["true_positive", "false_positive", "false_negative", "failure"]
    fingerprint: str = Field(min_length=1)
    note: str | None = None


class BenchmarkResult(BaseModel):
    cases: int
    validation_cases: int
    holdout_cases: int
    split: str = "all"
    metrics: dict[str, float]
    validation: dict[str, Any] = Field(default_factory=dict)
    holdout: dict[str, Any] = Field(default_factory=dict)


class EvolutionActivate(BaseModel):
    version: str
